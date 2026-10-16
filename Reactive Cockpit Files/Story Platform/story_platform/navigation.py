from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .engine import parse_time, utc_now
from .model import StoryGraph
from .predicates import evaluate, geofence_distance_m
from .storage import append_jsonl, atomic_write_json


class NavigationRuntime:
    """Generic coordinate guidance and hold-to-acquire runtime.

    Stories activate a navigator with an ordinary variable predicate. The
    runtime supplies distance-scaled pings, acquisition cancellation and a
    normalized ``navigation.target_acquired`` signal; story progression stays
    entirely in the graph.
    """

    def __init__(
        self, graph: StoryGraph, state_path: Path, event_log: Path, outbox: Path,
        printer: Callable[[str], None] = print,
    ) -> None:
        self.graph = graph
        self.definitions = list(graph.data.get("navigators", []))
        self.state_path = state_path
        self.event_log = event_log
        self.outbox = outbox
        self.print = printer
        self.running = False
        self._signals: list[dict[str, Any]] = []
        self.state = self._load_or_create()

    def _fresh(self) -> dict[str, Any]:
        return {
            "schema": "platform.navigation-state.v1", "graph_id": self.graph.graph_id,
            "graph_version": self.graph.version, "navigator": None, "enabled": False,
            "target": None, "phase": "inactive", "distance_m": None,
            "next_ping_at": None, "updated_at": utc_now(),
        }

    def _load_or_create(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._fresh()
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return self._fresh()
        if saved.get("graph_id") != self.graph.graph_id or saved.get("graph_version") != self.graph.version:
            return self._fresh()
        fresh = self._fresh(); fresh.update(saved); return fresh

    def save(self) -> None:
        self.state["updated_at"] = utc_now()
        atomic_write_json(self.state_path, self.state)

    def _event(self, event_type: str, **fields: Any) -> None:
        append_jsonl(self.event_log, {"at": utc_now(), "type": event_type, "graph_id": self.graph.graph_id, **fields})

    def _emit(self, navigator: str, channel: str, payload: dict[str, Any]) -> None:
        append_jsonl(self.outbox, {
            "at": utc_now(), "graph_id": self.graph.graph_id,
            "node": f"navigation:{navigator}", "channel": channel, "payload": payload,
        })

    def drain_signals(self) -> list[dict[str, Any]]:
        result, self._signals = self._signals, []
        return result

    @staticmethod
    def _due(value: Any) -> bool:
        return not value or datetime.now(timezone.utc) >= parse_time(str(value))

    @staticmethod
    def _deadline(seconds: float) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _named_match(location: dict[str, Any], world: dict[str, Any]) -> bool:
        actual = world.get("location") if isinstance(world.get("location"), dict) else {}
        for key in ("system", "body"):
            if location.get(key) and str(actual.get(key, "")).casefold() != str(location[key]).casefold():
                return False
        return True

    @staticmethod
    def _context(signal: dict[str, Any], world: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
        return {"signal": signal, "world": world, "variables": variables, "node_elapsed_seconds": 0.0}

    def _active_definition(self, context: dict[str, Any]) -> dict[str, Any] | None:
        return next((item for item in self.definitions if evaluate(item.get("active_when", {"always": False}), context)), None)

    def _target(self, definition: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
        for target in definition.get("targets", []):
            complete_when = target.get("complete_when", {"always": False})
            if not evaluate(complete_when, context):
                return target
        return None

    def _reset_playback(self, definition_id: str) -> None:
        self._emit(definition_id, "audio", {"action": "stop_layer", "layer": f"navigation.{definition_id}.acquisition"})
        self._emit(definition_id, "audio", {"action": "stop_layer", "layer": f"navigation.{definition_id}.ping"})

    def dispatch(self, signal: dict[str, Any], world: dict[str, Any], variables: dict[str, Any]) -> bool:
        if not self.running or not self.definitions:
            return False
        changed = False
        context = self._context(signal, world, variables)
        definition = self._active_definition(context)
        if definition is None:
            if self.state.get("phase") != "inactive":
                old = str(self.state.get("navigator") or "navigator")
                self._reset_playback(old)
                self.state.update({"navigator": None, "target": None, "phase": "inactive", "distance_m": None, "enabled": False})
                changed = True
            if changed: self.save()
            return changed
        nav_id = str(definition["id"])
        if self.state.get("navigator") != nav_id:
            self.state.update({"navigator": nav_id, "target": None, "phase": "ready", "distance_m": None, "enabled": bool(definition.get("enabled_on_activate", False)), "next_ping_at": None})
            self._event("navigation.activated", navigator=nav_id)
            changed = True
        if signal.get("name") == definition.get("toggle_signal", "control.scanner"):
            self.state["enabled"] = not bool(self.state.get("enabled"))
            self.state["phase"] = "seeking" if self.state["enabled"] else "ready"
            self.state["next_ping_at"] = None
            if not self.state["enabled"]:
                self._reset_playback(nav_id)
            self._emit(nav_id, "visual", {"cue": "scanner", "enabled": self.state["enabled"], "navigator": nav_id})
            self._event("navigation.toggled", navigator=nav_id, enabled=self.state["enabled"])
            changed = True
        target = self._target(definition, context)
        target_id = str(target.get("id")) if target else None
        if target_id != self.state.get("target"):
            self.state.update({"target": target_id, "phase": "seeking" if self.state.get("enabled") else "ready", "distance_m": None, "next_ping_at": None})
            changed = True
        if not target or not self.state.get("enabled"):
            if changed: self.save()
            return changed
        location = target.get("location", {})
        if not self._named_match(location, world):
            self.state["distance_m"] = None
            if changed: self.save()
            return changed
        distance = geofence_distance_m(location, world.get("location", {}))
        self.state["distance_m"] = None if distance is None else round(distance, 2)
        if distance is None:
            if changed: self.save()
            return changed
        trigger = float(target.get("trigger_radius_m", definition.get("trigger_radius_m", 150.0)))
        hold = float(target.get("hold_radius_m", definition.get("hold_radius_m", trigger * 1.5)))
        completion = f"navigation.{nav_id}.{target_id}.audio_finished"
        phase = str(self.state.get("phase"))
        if phase == "acquiring" and distance > hold:
            self._reset_playback(nav_id)
            self.state.update({"phase": "seeking", "next_ping_at": None})
            self._event("navigation.acquisition_cancelled", navigator=nav_id, target=target_id, distance_m=round(distance, 2))
            changed = True
        elif phase == "acquiring" and signal.get("name") == completion:
            self.state["phase"] = "acquired"
            self._signals.append({
                "name": "navigation.target_acquired", "source": "navigation", "at": utc_now(),
                "data": {"navigator": nav_id, "target": target_id, "distance_m": round(distance, 2)},
            })
            self._event("navigation.target_acquired", navigator=nav_id, target=target_id, distance_m=round(distance, 2))
            changed = True
        elif phase in {"seeking", "ready"} and distance <= trigger:
            asset = str(target.get("acquisition_asset", definition.get("acquisition_asset", "")))
            if asset:
                self._emit(nav_id, "audio", {
                    "action": "play_layer", "layer": f"navigation.{nav_id}.acquisition",
                    "asset": asset, "volume": float(definition.get("acquisition_volume", 1.0)),
                    "completion_signal": completion,
                })
                self.state["phase"] = "acquiring"
                self._event("navigation.acquisition_started", navigator=nav_id, target=target_id, distance_m=round(distance, 2))
                changed = True
        elif phase in {"seeking", "ready"}:
            outer = float(target.get("outer_range_m", definition.get("outer_range_m", 1_000_000.0)))
            if distance <= outer and self._due(self.state.get("next_ping_at")):
                minimum = float(definition.get("minimum_ping_volume", 0.15))
                maximum = float(definition.get("maximum_ping_volume", 1.0))
                full = float(definition.get("full_volume_radius_m", 500.0))
                ratio = 1.0 if distance <= full else max(0.0, 1.0 - (distance - full) / max(1.0, outer - full))
                volume = minimum + (maximum - minimum) * math.pow(ratio, float(definition.get("volume_exponent", 1.25)))
                self._emit(nav_id, "audio", {
                    "action": "play_layer", "layer": f"navigation.{nav_id}.ping",
                    "asset": str(definition["ping_asset"]), "volume": round(volume, 4),
                })
                maximum_gap = float(definition.get("ping_gap_seconds", 5.0))
                minimum_gap = float(definition.get("minimum_ping_gap_seconds", maximum_gap))
                distance_ratio = max(0.0, min(1.0, distance / max(1.0, outer)))
                gap = minimum_gap + (maximum_gap - minimum_gap) * math.sqrt(distance_ratio)
                self.state["next_ping_at"] = self._deadline(gap)
                self.state["phase"] = "seeking"
                self._event("navigation.ping", navigator=nav_id, target=target_id, distance_m=round(distance, 2), volume=round(volume, 4))
                changed = True
        self.save()
        return changed

    def start_session(self, world: dict[str, Any], variables: dict[str, Any]) -> None:
        self.running = True
        self.dispatch({"name": "system.start", "source": "system", "at": utc_now(), "data": {}}, world, variables)

    def stop_session(self) -> None:
        self.running = False; self.save()

    def reset(self) -> None:
        if self.state.get("navigator"):
            self._reset_playback(str(self.state["navigator"]))
        self.state = self._fresh(); self.save()
