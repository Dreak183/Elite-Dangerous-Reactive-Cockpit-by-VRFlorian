from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .engine import parse_time, utc_now
from .model import StoryGraph
from .predicates import evaluate, geofence_distance_m
from .storage import append_jsonl, atomic_write_json


class ScanError(ValueError):
    pass


class ScanRuntime:
    """Durable, order-independent story scans layered over detection zones."""

    def __init__(
        self, graph: StoryGraph, state_path: Path, event_log: Path, outbox: Path,
        printer: Callable[[str], None] = print,
    ) -> None:
        self.graph = graph
        self.scan_sets = list(graph.data.get("scan_sets", []))
        self.state_path = state_path
        self.event_log = event_log
        self.outbox = outbox
        self.print = printer
        self._signals: list[dict[str, Any]] = []
        self._recovery_stops: list[tuple[str, str]] = []
        self._detection_pulse_layers: dict[str, list[str]] = {}
        for zone in graph.data.get("environments", []):
            if not isinstance(zone, dict):
                continue
            zone_id = str(zone.get("id", ""))
            pulse_layers = [
                f"environment.{zone_id}.{layer['id']}"
                for layer in zone.get("layers", [])
                if isinstance(layer, dict)
                and layer.get("type") == "audio"
                and layer.get("playback_mode") == "scanner_pulse"
                and layer.get("id")
            ]
            if zone_id and pulse_layers:
                self._detection_pulse_layers[zone_id] = pulse_layers
        self.state = self._load_or_create()

    def _fresh_state(self) -> dict[str, Any]:
        return {
            "schema": "platform.scan-state.v1",
            "graph_id": self.graph.graph_id,
            "graph_version": self.graph.version,
            "sets": {
                str(scan_set["id"]): {
                    "completed": False,
                    "completed_at": None,
                    "targets": {
                        str(target["id"]): {
                            "phase": "pending", "started_at": None,
                            "completed_at": None, "distance_m": None,
                        }
                        for target in scan_set.get("targets", [])
                    },
                }
                for scan_set in self.scan_sets
            },
            "updated_at": utc_now(),
        }

    def _load_or_create(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._fresh_state()
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ScanError(f"Cannot load scan state: {exc}") from exc
        if saved.get("graph_id") != self.graph.graph_id or saved.get("graph_version") != self.graph.version:
            raise ScanError("Scan state belongs to another graph version; reset explicitly")
        fresh = self._fresh_state()
        for set_id, set_state in fresh["sets"].items():
            old_set = saved.get("sets", {}).get(set_id)
            if not isinstance(old_set, dict):
                continue
            for key in ("completed", "completed_at"):
                if key in old_set:
                    set_state[key] = copy.deepcopy(old_set[key])
            for target_id, target_state in set_state["targets"].items():
                old_target = old_set.get("targets", {}).get(target_id)
                if isinstance(old_target, dict):
                    target_state.update(copy.deepcopy(old_target))
                    # An interrupted scan is safe to retry; a completed one is durable.
                    if not target_state.get("completed_at"):
                        if target_state.get("phase") == "acquiring":
                            self._recovery_stops.append((set_id, target_id))
                        target_state.update({"phase": "pending", "started_at": None})
        return fresh

    def save(self) -> None:
        self.state["updated_at"] = utc_now()
        atomic_write_json(self.state_path, self.state)

    def reset(self) -> None:
        self.state = self._fresh_state()
        self.save()

    def _event(self, kind: str, **fields: Any) -> None:
        append_jsonl(self.event_log, {"at": utc_now(), "type": kind, "graph_id": self.graph.graph_id, **fields})

    def _emit(self, scan_set: str, target: str, payload: dict[str, Any]) -> None:
        append_jsonl(self.outbox, {
            "at": utc_now(), "graph_id": self.graph.graph_id,
            "node": f"scan:{scan_set}.{target}", "channel": "audio",
            "payload": copy.deepcopy(payload),
        })

    def _signal(self, name: str, scan_set: dict[str, Any], target: dict[str, Any] | None = None) -> None:
        data = {"scan_set": str(scan_set["id"]), "title": str(scan_set.get("title", scan_set["id"]))}
        if target is not None:
            data.update({
                "target": str(target["id"]), "target_title": str(target.get("title", target["id"])),
                "detection_zone": str(target["detection_zone"]),
            })
        self._signals.append({"name": name, "source": "scan", "at": utc_now(), "data": data})

    @staticmethod
    def _context(signal: dict[str, Any], world: dict[str, Any], variables: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        return {"signal": signal, "world": world, "variables": variables, "scan": state, "node_elapsed_seconds": 0.0}

    @staticmethod
    def _named_location_matches(rule: dict[str, Any], world: dict[str, Any]) -> bool:
        actual = world.get("location") if isinstance(world.get("location"), dict) else {}
        for key in ("system", "body"):
            if rule.get(key) and str(rule[key]).casefold() != str(actual.get(key, "")).casefold():
                return False
        return True

    def _start(self, scan_set: dict[str, Any], target: dict[str, Any], target_state: dict[str, Any]) -> None:
        # Silence the directional detector before the acquisition sound enters
        # the audio outbox.  The matching scan.target_started signal then
        # synchronizes the environment runtime's durable layer state.
        for detection_layer in self._detection_pulse_layers.get(str(target["detection_zone"]), []):
            self._emit(str(scan_set["id"]), str(target["id"]), {
                "action": "stop_layer", "layer": detection_layer,
            })
        layer = f"scan.{scan_set['id']}.{target['id']}.acquisition"
        self._emit(str(scan_set["id"]), str(target["id"]), {
            "action": "play_layer", "layer": layer,
            "asset": scan_set["acquisition_asset"],
            "volume": float(scan_set.get("acquisition_volume", 1.0)), "loop": True,
        })
        target_state.update({"phase": "acquiring", "started_at": utc_now()})
        self._event("scan.target_started", scan_set=scan_set["id"], target=target["id"])
        self._signal("scan.target_started", scan_set, target)
        self.print(f"[SCAN] Analysing {target.get('title', target['id'])}")

    def _cancel(self, scan_set: dict[str, Any], target: dict[str, Any], target_state: dict[str, Any], reason: str) -> None:
        self._emit(str(scan_set["id"]), str(target["id"]), {
            "action": "stop_layer", "layer": f"scan.{scan_set['id']}.{target['id']}.acquisition",
        })
        target_state.update({"phase": "pending", "started_at": None})
        self._event("scan.target_cancelled", scan_set=scan_set["id"], target=target["id"], reason=reason)
        self._signal("scan.target_cancelled", scan_set, target)

    def _complete(self, scan_set: dict[str, Any], target: dict[str, Any], target_state: dict[str, Any]) -> None:
        self._emit(str(scan_set["id"]), str(target["id"]), {
            "action": "stop_layer", "layer": f"scan.{scan_set['id']}.{target['id']}.acquisition",
        })
        self._emit(str(scan_set["id"]), str(target["id"]), {
            "action": "play_layer", "layer": f"scan.{scan_set['id']}.{target['id']}.complete",
            "asset": scan_set["completion_asset"],
            "volume": float(scan_set.get("completion_volume", 1.0)), "loop": False,
        })
        completed_at = utc_now()
        target_state.update({"phase": "completed", "completed_at": completed_at, "started_at": None})
        self._event("scan.target_completed", scan_set=scan_set["id"], target=target["id"])
        self._signal("scan.target_completed", scan_set, target)
        self._signal("scan.detection_suppress", scan_set, target)
        self.print(f"[SCAN] Complete: {target.get('title', target['id'])}")

    def dispatch(
        self, signal: dict[str, Any], world: dict[str, Any], variables: dict[str, Any],
        control_states: dict[str, Any],
    ) -> bool:
        changed = False
        if self._recovery_stops:
            for set_id, target_id in self._recovery_stops:
                self._emit(set_id, target_id, {
                    "action": "stop_layer", "layer": f"scan.{set_id}.{target_id}.acquisition",
                })
                self._event("scan.interrupted_recovered", scan_set=set_id, target=target_id)
            self._recovery_stops = []
            changed = True
        vehicle = str(world.get("vehicle", "unknown"))
        now = datetime.now(timezone.utc)
        for scan_set in self.scan_sets:
            set_id = str(scan_set["id"])
            set_state = self.state["sets"][set_id]
            context = self._context(signal, world, variables, self.state)
            armed = evaluate(scan_set.get("enabled_when", {"always": True}), context)
            required_control = str(scan_set.get("requires_control", "control.scanner"))
            scanner_on = bool(control_states.get(required_control))
            allowed_vehicle = vehicle in scan_set.get("active_vehicles", ["on_foot"])
            for target in scan_set.get("targets", []):
                target_id = str(target["id"])
                target_state = set_state["targets"][target_id]
                if target_state.get("completed_at"):
                    variable = f"scan.{set_id}.{target_id}.complete"
                    if variables.get(variable) is not True:
                        variables[variable] = True
                        changed = True
                    continue
                rule = target["location"]
                distance = geofence_distance_m(rule, world.get("location", {})) if self._named_location_matches(rule, world) else None
                rounded = None if distance is None else round(distance, 3)
                if target_state.get("distance_m") != rounded:
                    target_state["distance_m"] = rounded
                    changed = True
                inside = distance is not None and distance <= float(rule["core_radius_m"])
                acquiring = target_state.get("phase") == "acquiring"
                if armed and scanner_on and allowed_vehicle and inside and not acquiring:
                    self._start(scan_set, target, target_state)
                    changed = True
                    acquiring = True
                if acquiring and not (armed and scanner_on and allowed_vehicle and inside):
                    self._cancel(scan_set, target, target_state, "left scan conditions")
                    changed = True
                    continue
                if acquiring and target_state.get("started_at"):
                    elapsed = (now - parse_time(str(target_state["started_at"]))).total_seconds()
                    if elapsed >= float(scan_set.get("hold_seconds", 8.0)):
                        self._complete(scan_set, target, target_state)
                        changed = True
            if not set_state.get("completed") and all(
                bool(value.get("completed_at")) for value in set_state["targets"].values()
            ):
                set_state.update({"completed": True, "completed_at": utc_now()})
                self._event("scan.set_completed", scan_set=set_id)
                self._signal("scan.set_completed", scan_set)
                changed = True
            if set_state.get("completed"):
                variable = f"scan.{set_id}.complete"
                if variables.get(variable) is not True:
                    variables[variable] = True
                    changed = True
        if changed:
            self.save()
        return changed

    def advance_time(self, seconds: float) -> None:
        if seconds < 0:
            raise ScanError("Time advance must be zero or greater")
        for set_state in self.state.get("sets", {}).values():
            for target_state in set_state.get("targets", {}).values():
                if target_state.get("started_at"):
                    shifted = parse_time(str(target_state["started_at"]))
                    shifted = shifted.replace(tzinfo=timezone.utc) if shifted.tzinfo is None else shifted
                    target_state["started_at"] = (shifted - timedelta(seconds=float(seconds))).isoformat(
                        timespec="milliseconds"
                    ).replace("+00:00", "Z")
        self.save()

    def drain_signals(self) -> list[dict[str, Any]]:
        result, self._signals = self._signals, []
        return result

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.state)
