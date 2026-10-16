from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .engine import parse_time, utc_now
from .model import StoryGraph
from .predicates import evaluate
from .storage import append_jsonl, atomic_write_json


class TimeGateError(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _phase(local_hour: float) -> str:
    # Author-facing daylight vocabulary. The clock is deliberately neutral:
    # individual stories decide which hours are safe through named gates.
    daylight = (
        "Sunrise", "Early dawn", "Dawn", "Early day", "Mid day",
        "Late mid day", "Late day", "Early dusk", "Dusk", "Sunset",
    )
    if 0.0 <= local_hour < 12.0:
        return daylight[min(9, int(local_hour / 1.2))]
    return "Night"


def _inside_window(hour: float, start: float, finish: float) -> bool:
    if start == finish:
        return True
    if start < finish:
        return start <= hour < finish
    return hour >= start or hour < finish


def _hours_until(hour: float, target: float) -> float:
    return (target - hour) % 24.0


class TimeGateRuntime:
    """Body clocks, named surface sites, gates and durable boundary events.

    Clock facts are recomputed into ``world.time``. Only event delivery state is
    persisted, so restarting can never freeze or overwrite celestial time.
    """

    def __init__(
        self,
        graph: StoryGraph,
        state_path: Path,
        event_log: Path,
        outbox: Path,
        printer: Callable[[str], None] = print,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.graph = graph
        self.state_path = state_path
        self.event_log = event_log
        self.outbox = outbox
        self.print = printer
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._signals: list[dict[str, Any]] = []
        self.clocks = {str(item["id"]): item for item in graph.data.get("solar_clocks", [])}
        self.sites = {str(item["id"]): item for item in graph.data.get("solar_sites", [])}
        self.gates = {str(item["id"]): item for item in graph.data.get("time_gates", [])}
        self.events = {str(item["id"]): item for item in graph.data.get("time_events", [])}
        self.state = self._load_or_create()

    def _now(self) -> datetime:
        return _utc(self._now_provider())

    def _fresh_state(self) -> dict[str, Any]:
        return {
            "schema": "platform.time-gate-state.v1",
            "graph_id": self.graph.graph_id,
            "graph_version": self.graph.version,
            "gates": {},
            "events": {},
            "updated_at": utc_now(),
        }

    def _load_or_create(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._fresh_state()
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TimeGateError(f"Cannot load time-gate state: {exc}") from exc
        if value.get("graph_id") != self.graph.graph_id or value.get("graph_version") != self.graph.version:
            raise TimeGateError("Time-gate state belongs to another graph version; reset explicitly")
        fresh = self._fresh_state()
        if isinstance(value.get("gates"), dict):
            fresh["gates"] = copy.deepcopy(value["gates"])
        if isinstance(value.get("events"), dict):
            fresh["events"] = copy.deepcopy(value["events"])
        fresh["updated_at"] = value.get("updated_at", fresh["updated_at"])
        return fresh

    def save(self) -> None:
        self.state["updated_at"] = self._now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        atomic_write_json(self.state_path, self.state)

    def reset(self) -> None:
        self.state = self._fresh_state()
        self.save()

    def _event(self, kind: str, **fields: Any) -> None:
        append_jsonl(self.event_log, {
            "at": self._now().isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "type": kind, "graph_id": self.graph.graph_id, **fields,
        })

    def _emit(self, event_id: str, channel: str, payload: dict[str, Any]) -> None:
        append_jsonl(self.outbox, {
            "at": self._now().isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "graph_id": self.graph.graph_id, "node": f"time:{event_id}",
            "channel": channel, "payload": copy.deepcopy(payload),
        })

    def _site_fact(self, site: dict[str, Any], now: datetime) -> dict[str, Any]:
        clock = self.clocks[str(site["clock"])]
        anchor = site.get("anchor") if isinstance(site.get("anchor"), dict) else clock["anchor"]
        day_seconds = float(clock["solar_day_seconds"])
        elapsed_seconds = (now - parse_time(str(anchor["at_utc"]))).total_seconds()
        longitude_hours = float(clock.get("direction", 1)) * (
            float(site["longitude"]) - float(anchor["longitude"])
        ) / 15.0
        unwrapped = float(anchor["local_hour"]) + elapsed_seconds * 24.0 / day_seconds + longitude_hours
        local_hour = unwrapped % 24.0
        local_seconds = int(round(local_hour * 3600.0)) % 86400
        hours, remainder = divmod(local_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return {
            "title": str(site.get("title", site["id"])),
            "clock": str(site["clock"]),
            "local_hour": round(local_hour, 6),
            "local_time": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
            "phase": _phase(local_hour),
            "cycle_index": math.floor(unwrapped / 24.0),
            "solar_day_earth_seconds": day_seconds,
        }

    def refresh_world(self, world: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        current = _utc(now) if now is not None else self._now()
        time_root: dict[str, Any] = {
            "at_utc": current.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "sites": {}, "gates": {},
        }
        for site_id, site in self.sites.items():
            time_root["sites"][site_id] = self._site_fact(site, current)
        for gate_id, gate in self.gates.items():
            site_fact = time_root["sites"][str(gate["site"])]
            hour = float(site_fact["local_hour"])
            start = float(gate["open_local_hour"])
            finish = float(gate["close_local_hour"])
            is_open = _inside_window(hour, start, finish)
            day_seconds = float(site_fact["solar_day_earth_seconds"])
            cycle = int(site_fact["cycle_index"])
            if start > finish and hour < finish:
                cycle -= 1
            time_root["gates"][gate_id] = {
                "title": str(gate.get("title", gate_id)),
                "site": str(gate["site"]),
                "open": is_open,
                "state": "open" if is_open else "closed",
                "occurrence": cycle,
                "seconds_until_open": round(_hours_until(hour, start) * day_seconds / 24.0, 3),
                "seconds_until_close": round(_hours_until(hour, finish) * day_seconds / 24.0, 3),
            }
        world["time"] = time_root
        return time_root

    def _delivery_key(self, event: dict[str, Any], occurrence: int) -> str:
        repeat = str(event.get("repeat", "once_per_cycle"))
        if repeat in {"once_per_story", "once_per_mission"}:
            return "forever"
        return str(occurrence)

    def _schedule(self, event: dict[str, Any], gate_fact: dict[str, Any], catch_up: bool) -> None:
        event_id = str(event["id"])
        state = self.state.setdefault("events", {}).setdefault(event_id, {"delivered": []})
        key = self._delivery_key(event, int(gate_fact["occurrence"]))
        if key in state.setdefault("delivered", []) or state.get("pending_key") == key:
            return
        due = self._now() + timedelta(seconds=float(event.get("delay_seconds", 0.0)))
        state.update({
            "pending_key": key,
            "pending_occurrence": int(gate_fact["occurrence"]),
            "due_at": due.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "catch_up": bool(catch_up),
        })
        self._event("time_event.scheduled", event=event_id, gate=event["gate"], occurrence=key, catch_up=catch_up)

    def _deliver_due(self, world: dict[str, Any]) -> bool:
        changed = False
        now = self._now()
        for event_id, event in self.events.items():
            state = self.state.setdefault("events", {}).setdefault(event_id, {"delivered": []})
            due_at = state.get("due_at")
            if not due_at or now < parse_time(str(due_at)):
                continue
            gate_fact = world.get("time", {}).get("gates", {}).get(str(event["gate"]), {})
            default_state = "open" if event.get("boundary", "open") == "open" else "closed"
            required_state = str(event.get("valid_while", default_state))
            if gate_fact.get("state") != required_state:
                self._event("time_event.expired", event=event_id, occurrence=state.get("pending_key"))
                state.pop("pending_key", None)
                state.pop("pending_occurrence", None)
                state.pop("due_at", None)
                state.pop("catch_up", None)
                changed = True
                continue
            for effect in event.get("effects", []):
                if effect.get("type") == "emit":
                    self._emit(event_id, str(effect["channel"]), effect.get("payload", {}))
                elif effect.get("type") == "log":
                    self.print(f"[TIME] {effect.get('message', '')}")
            key = str(state.pop("pending_key"))
            state.setdefault("delivered", []).append(key)
            state.pop("pending_occurrence", None)
            state.pop("due_at", None)
            catch_up = bool(state.pop("catch_up", False))
            self._event("time_event.delivered", event=event_id, gate=event["gate"], occurrence=key, catch_up=catch_up)
            changed = True
        return changed

    def process(self, signal: dict[str, Any], world: dict[str, Any], variables: dict[str, Any]) -> bool:
        facts = self.refresh_world(world)
        changed = False
        context = {
            "signal": signal, "world": world, "variables": variables,
            "state": self.state, "node_elapsed_seconds": 0.0,
        }
        for gate_id, gate_fact in facts["gates"].items():
            gate_state = self.state.setdefault("gates", {}).setdefault(gate_id, {})
            previous = gate_state.get("open")
            current = bool(gate_fact["open"])
            boundary: str | None = None
            catch_up = False
            if previous is None:
                # Startup catch-up is intentionally limited to an event whose
                # boundary is still valid now. Stale closed windows never speak.
                if current:
                    boundary, catch_up = "open", True
            elif bool(previous) != current:
                boundary = "open" if current else "close"
                startup = str(signal.get("name", "")) == "system.start"
                if startup and boundary == "close":
                    # The whole open period passed while offline. Announcing its
                    # stale close would break the scene; simply adopt current state.
                    boundary = None
                else:
                    catch_up = startup and boundary == "open"
                    signal_name = "timegate.opened" if boundary == "open" else "timegate.closed"
                    self._signals.append({
                        "name": signal_name, "source": "time",
                        "at": facts["at_utc"], "data": {"gate": gate_id, **copy.deepcopy(gate_fact)},
                    })
                    self._event(signal_name, gate=gate_id, occurrence=gate_fact["occurrence"])
            gate_state.update({"open": current, "occurrence": gate_fact["occurrence"], "observed_at": facts["at_utc"]})
            if boundary:
                for event in self.events.values():
                    if event.get("gate") != gate_id or event.get("boundary", "open") != boundary:
                        continue
                    if not evaluate(event.get("enabled_when", {"always": True}), context):
                        continue
                    if catch_up and not bool(event.get("catch_up", True)):
                        continue
                    self._schedule(event, gate_fact, catch_up)
                    changed = True
        changed = self._deliver_due(world) or changed
        self.save()
        return changed

    def drain_signals(self) -> list[dict[str, Any]]:
        result, self._signals = self._signals, []
        return result

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.state)
