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


class EnvironmentError(ValueError):
    pass


def zone_depth_percent(zone: dict[str, Any], world: dict[str, Any]) -> float | None:
    """Return 0 at the authored outer radius and 100 at the core."""
    location_rule = zone.get("location") if isinstance(zone.get("location"), dict) else {}
    if "latitude" not in location_rule or "longitude" not in location_rule:
        return None
    distance = geofence_distance_m(location_rule, world.get("location", {}))
    if distance is None:
        return None
    outer = float(location_rule["entry_radius_m"])
    core = float(location_rule.get("core_radius_m", 0.0))
    if distance <= core:
        return 100.0
    if distance >= outer:
        return 0.0
    return round(100.0 * (outer - distance) / (outer - core), 3)


def layer_level(layer: dict[str, Any], depth_percent: float | None) -> float:
    maximum = float(layer.get("volume", layer.get("intensity", 1.0)))
    response = layer.get("depth_response") if isinstance(layer.get("depth_response"), dict) else {}
    mode = str(response.get("curve", "constant"))
    if depth_percent is None or mode == "constant":
        return max(0.0, min(1.0, maximum))
    minimum = float(response.get("minimum", 0.0))
    start = float(response.get("start_percent", 0.0))
    if bool(response.get("silent_before_start", False)) and depth_percent < start:
        return 0.0
    if depth_percent <= start:
        return max(0.0, min(1.0, minimum))
    ratio = min(1.0, (depth_percent - start) / max(0.001, 100.0 - start))
    if mode == "exponential":
        ratio **= float(response.get("exponent", 2.0))
    value = minimum + (maximum - minimum) * ratio
    return max(0.0, min(1.0, value))


def scanner_pulse_gap_seconds(layer: dict[str, Any], depth_percent: float | None) -> float:
    """Return the pause after a scanner pulse: longest at 0%, shortest at 100%."""
    pulse = layer.get("scanner_pulse") if isinstance(layer.get("scanner_pulse"), dict) else {}
    outer = max(0.0, float(pulse.get("outer_gap_seconds", 5.0)))
    core = max(0.0, min(outer, float(pulse.get("core_gap_seconds", 0.0))))
    exponent = max(0.001, float(pulse.get("exponent", 1.0)))
    depth = 100.0 if depth_percent is None else max(0.0, min(100.0, float(depth_percent)))
    distance_ratio = 1.0 - (depth / 100.0)
    return core + (outer - core) * (distance_ratio ** exponent)


def scanner_pulse_next_momentum_gap_seconds(
    layer: dict[str, Any], measured_gap_seconds: float, current_gap_seconds: float,
    direction: str,
) -> float:
    """Advance one bounded cadence step in the last measured travel direction.

    The authored distance curve remains truth. Motion merely gives successive
    pulses a small proportional impulse until Elite publishes another real
    coordinate. Because both the step and limit are percentages of the measured
    pause, the absolute effect naturally becomes smaller as the pulse cadence
    becomes faster near the target.
    """
    pulse = layer.get("scanner_pulse") if isinstance(layer.get("scanner_pulse"), dict) else {}
    outer = max(0.0, float(pulse.get("outer_gap_seconds", 5.0)))
    core = max(0.0, min(outer, float(pulse.get("core_gap_seconds", 0.0))))
    step_ratio = max(0.0, min(0.25, float(pulse.get("momentum_step_percent", 4.0)) / 100.0))
    limit_ratio = max(0.0, min(0.25, float(pulse.get("momentum_limit_percent", 10.0)) / 100.0))
    measured = max(core, min(outer, float(measured_gap_seconds)))
    current = max(core, min(outer, float(current_gap_seconds)))
    step = measured * min(step_ratio, limit_ratio)
    if direction == "approaching":
        floor = max(core, measured * (1.0 - limit_ratio))
        return round(max(floor, current - step), 6)
    if direction == "retreating":
        ceiling = min(outer, measured * (1.0 + limit_ratio))
        return round(min(ceiling, current + step), 6)
    return round(current, 6)


class EnvironmentRuntime:
    """Durable reactive environments running beside, not inside, story flow."""

    def __init__(
        self, graph: StoryGraph, state_path: Path, event_log: Path, outbox: Path,
        printer: Callable[[str], None] = print,
        outputs_enabled: bool = True,
    ) -> None:
        self.graph = graph
        self.zones = list(graph.data.get("environments", []))
        self.state_path = state_path
        self.event_log = event_log
        self.outbox = outbox
        self.print = printer
        self.outputs_enabled = bool(outputs_enabled)
        self.running = False
        self._signals: list[dict[str, Any]] = []
        self._audio_layers: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for zone in self.zones:
            for layer in zone.get("layers", []):
                if isinstance(layer, dict) and layer.get("type") == "audio":
                    self._audio_layers[self._layer_key(str(zone["id"]), str(layer["id"]))] = (zone, layer)
        self.state = self._load_or_create()
        # Logical control state is durable; readiness is deliberately transient.
        # A scanner restored as ON after a process restart is immediately ready,
        # while a fresh OFF->ON transition can be held until its startup sample
        # has genuinely finished in PresentationAdapter.
        scanner_on = bool(self.state.get("control_states", {}).get("control.scanner"))
        self.control_ready_states: dict[str, bool] = {"control.scanner": scanner_on}

    def _fresh_state(self) -> dict[str, Any]:
        required_controls = {
            str(zone["requires_control"]): False
            for zone in self.zones
            if isinstance(zone.get("requires_control"), str) and zone.get("requires_control", "").strip()
        }
        for scan_set in self.graph.data.get("scan_sets", []):
            control = scan_set.get("requires_control")
            if isinstance(control, str) and control.strip():
                required_controls.setdefault(control, False)
        # The scanner is a platform-wide suit control, even in graphs that have
        # not authored a detection zone yet.
        required_controls.setdefault("control.scanner", False)
        return {
            "schema": "platform.environment-state.v1",
            "graph_id": self.graph.graph_id,
            "graph_version": self.graph.version,
            "zones": {
                str(zone["id"]): {
                    "phase": "inactive", "depth_percent": None, "distance_m": None,
                    "entered_at": None, "exited_at": None, "exit_pending_at": None, "layers": {},
                    "activated_count": 0, "scan_suppressed_until_exit": False,
                    "scan_acquisition_active": False,
                    "vehicle_suspended_at": None,
                    "scanner_sample_key": None, "scanner_sample_distance_m": None,
                    "scanner_motion_direction": "steady", "scanner_motion_delta_m": 0.0,
                }
                for zone in self.zones
            },
            "control_states": required_controls,
            "manual_layer_volumes": {},
            "updated_at": utc_now(),
        }

    def _load_or_create(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._fresh_state()
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Cannot load environment state: {exc}") from exc
        if value.get("graph_id") != self.graph.graph_id or value.get("graph_version") != self.graph.version:
            raise RuntimeError("Environment state belongs to another graph version; reset explicitly")
        fresh = self._fresh_state()
        for zone_id, zone_state in fresh["zones"].items():
            saved = value.get("zones", {}).get(zone_id)
            if isinstance(saved, dict):
                zone_state.update(saved)
                # Acquisition is transient. ScanRuntime safely rolls an
                # interrupted acquisition back to pending during recovery.
                zone_state["scan_acquisition_active"] = False
                if "activated_count" not in saved and saved.get("entered_at"):
                    zone_state["activated_count"] = 1
        saved_controls = value.get("control_states")
        if isinstance(saved_controls, dict):
            for name in fresh["control_states"]:
                if isinstance(saved_controls.get(name), bool):
                    fresh["control_states"][name] = saved_controls[name]
        saved_volumes = value.get("manual_layer_volumes")
        if isinstance(saved_volumes, dict):
            fresh["manual_layer_volumes"] = {
                key: copy.deepcopy(item)
                for key, item in saved_volumes.items()
                if key in self._audio_layers and isinstance(item, dict)
            }
        fresh["updated_at"] = value.get("updated_at", fresh["updated_at"])
        return fresh

    def save(self) -> None:
        self.state["updated_at"] = utc_now()
        atomic_write_json(self.state_path, self.state)

    def _event(self, event_type: str, **fields: Any) -> None:
        append_jsonl(self.event_log, {
            "at": utc_now(), "type": event_type, "graph_id": self.graph.graph_id, **fields,
        })

    def _lifecycle_signal(self, event_type: str, zone: dict[str, Any], **fields: Any) -> None:
        """Expose zone lifecycle to ordinary graph edges without recursion."""
        self._signals.append({
            "name": event_type,
            "source": "environment",
            "at": utc_now(),
            "data": {"zone": str(zone["id"]), "title": str(zone.get("title", zone["id"])), **fields},
        })

    def drain_signals(self) -> list[dict[str, Any]]:
        result, self._signals = self._signals, []
        return result

    def _emit(self, zone_id: str, channel: str, payload: dict[str, Any]) -> bool:
        if not self.outputs_enabled:
            return False
        append_jsonl(self.outbox, {
            "at": utc_now(), "graph_id": self.graph.graph_id,
            "node": f"environment:{zone_id}", "channel": channel, "payload": payload,
        })
        return True

    def set_outputs_enabled(self, enabled: bool) -> None:
        """Arm presentation only after genuinely live Elite telemetry arrives."""
        self.outputs_enabled = bool(enabled)

    def effective_control_states(self) -> dict[str, Any]:
        """Return controls after transient startup handshakes are applied."""
        result = copy.deepcopy(self.state.get("control_states", {}))
        if "control.scanner" in result:
            result["control.scanner"] = bool(result["control.scanner"]) and bool(
                self.control_ready_states.get("control.scanner", True)
            )
        return result

    def observe_emit(self, command: dict[str, Any]) -> None:
        """Persist ordinary story volume commands as part of environment state.

        Existing graphs therefore recover their latest named-layer mix after a
        relog or process crash, even when the story node itself is not entered
        again. First-class active-zone adjustments still take precedence.
        """
        if command.get("channel") != "audio":
            return
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        action = payload.get("action")
        if action not in {"set_layer_volume", "clear_layer_volume_override"}:
            return
        layer_key = str(payload.get("layer", ""))
        if layer_key not in self._audio_layers:
            return
        if action == "clear_layer_volume_override":
            try:
                transition = max(0.0, float(payload.get("transition_seconds", 2.0)))
            except (TypeError, ValueError):
                return
            removed = self.state.setdefault("manual_layer_volumes", {}).pop(layer_key, None)
            zone, layer = self._audio_layers[layer_key]
            layer_state = self.state.get("zones", {}).get(str(zone["id"]), {}).get("layers", {}).get(str(layer["id"]))
            if isinstance(layer_state, dict) and layer_state.get("playing"):
                raw_base = layer_state.get("base_level")
                base = float(raw_base) if isinstance(raw_base, (int, float)) else layer_level(layer, None)
                effective = self._effective_audio_level(layer_key, base)
                effective_transition = max(transition, float(effective["transition_seconds"]))
                layer_state.update({
                    "level": effective["volume"],
                    "effective_source": effective["source"],
                    "effective_transition_seconds": effective_transition,
                })
                if layer_state.get("output_active"):
                    self._emit(str(zone["id"]), "audio", {
                        "action": "set_layer_volume", "layer": layer_key,
                        "volume": effective["volume"],
                        "transition_seconds": effective_transition,
                        "zone": zone["id"], "reason": "manual override cleared",
                    })
            self._event(
                "environment.layer_override_cleared", layer=layer_key,
                previous_volume=removed.get("volume") if isinstance(removed, dict) else None,
                node=str(command.get("node", "")),
            )
            self.save()
            return
        try:
            volume = max(0.0, min(1.0, float(payload.get("volume", 1.0))))
            transition = max(0.0, float(payload.get("transition_seconds", 2.0)))
        except (TypeError, ValueError):
            return
        self.state.setdefault("manual_layer_volumes", {})[layer_key] = {
            "volume": round(volume, 4), "transition_seconds": transition,
            "node": str(command.get("node", "")), "updated_at": utc_now(),
        }
        zone, layer = self._audio_layers[layer_key]
        layer_state = self.state.get("zones", {}).get(str(zone["id"]), {}).get("layers", {}).get(str(layer["id"]))
        if isinstance(layer_state, dict) and layer_state.get("playing"):
            raw_base = layer_state.get("base_level")
            base = float(raw_base) if isinstance(raw_base, (int, float)) else layer_level(layer, None)
            effective = self._effective_audio_level(layer_key, base)
            layer_state.update({
                "level": effective["volume"],
                "effective_source": effective["source"],
                "effective_transition_seconds": effective["transition_seconds"],
            })
        self.save()

    @staticmethod
    def _deadline(seconds: float) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=float(seconds))).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")

    @staticmethod
    def _is_due(value: Any) -> bool:
        return bool(value) and datetime.now(timezone.utc) >= parse_time(str(value))

    def _context(
        self, signal: dict[str, Any], world: dict[str, Any], variables: dict[str, Any],
        zone_state: dict[str, Any],
    ) -> dict[str, Any]:
        elapsed = 0.0
        if zone_state.get("entered_at"):
            elapsed = max(0.0, (datetime.now(timezone.utc) - parse_time(str(zone_state["entered_at"]))).total_seconds())
        return {
            "signal": signal, "world": world, "variables": variables,
            "environment": self.state, "node_elapsed_seconds": elapsed,
        }

    @staticmethod
    def _named_location(zone: dict[str, Any], world: dict[str, Any]) -> tuple[bool, bool]:
        rule = zone.get("location") if isinstance(zone.get("location"), dict) else {}
        actual = world.get("location") if isinstance(world.get("location"), dict) else {}
        has_named = False
        all_match = True
        known_mismatch = False
        for key in ("system", "body", "station", "settlement"):
            expected = rule.get(key)
            if not expected:
                continue
            has_named = True
            if key not in actual:
                all_match = False
            elif str(actual.get(key)).casefold() != str(expected).casefold():
                all_match = False
                known_mismatch = True
        return (all_match if has_named else True), known_mismatch

    @staticmethod
    def _altitude_inside(rule: dict[str, Any], world: dict[str, Any]) -> tuple[bool, bool]:
        location = world.get("location") if isinstance(world.get("location"), dict) else {}
        if "min_altitude_m" not in rule and "max_altitude_m" not in rule:
            return True, False
        altitude = location.get("altitude")
        if not isinstance(altitude, (int, float)):
            # Status.json may omit altitude while on foot. In that case the
            # named location and horizontal geofence remain authoritative.
            if world.get("vehicle") == "on_foot":
                return True, False
            flags = world.get("flags") if isinstance(world.get("flags"), dict) else {}
            if (
                "has_lat_long" in flags
                and not bool(flags["has_lat_long"])
            ):
                # A known loss of planetary coordinates in a ship means the
                # commander is outside an altitude-bounded surface zone. This
                # is distinct from genuinely unknown altitude (notably on foot).
                return False, True
            # Journal transitions can clear altitude before the next Status
            # reading, while the previous surface-position flag remains set.
            # Unknown height proves neither entry nor exit: keep active layers
            # stable, but wait for a known height before starting or resuming.
            return False, False
        outside = (
            (rule.get("min_altitude_m") is not None and altitude < float(rule["min_altitude_m"]))
            or (rule.get("max_altitude_m") is not None and altitude > float(rule["max_altitude_m"]))
        )
        return not outside, outside

    def _location_state(self, zone: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
        rule = zone.get("location") if isinstance(zone.get("location"), dict) else {}
        if not rule:
            return {"entry": True, "exit": False, "outside_for_rearm": False, "depth": None, "distance": None}
        named_match, named_mismatch = self._named_location(zone, world)
        altitude_inside, altitude_outside = self._altitude_inside(rule, world)
        distance: float | None = None
        geo_entry = True
        geo_exit = False
        has_geo = "latitude" in rule and "longitude" in rule
        if has_geo:
            distance = geofence_distance_m(rule, world.get("location", {}))
            geo_entry = distance is not None and distance <= float(rule["entry_radius_m"])
            geo_exit = distance is not None and distance > float(rule.get("exit_radius_m", rule["entry_radius_m"]))
        return {
            "entry": named_match and geo_entry and altitude_inside,
            "exit": named_mismatch or geo_exit or altitude_outside,
            "outside_for_rearm": named_mismatch or geo_exit or altitude_outside,
            "depth": zone_depth_percent(zone, world) if has_geo else (100.0 if named_match else None),
            "distance": None if distance is None else round(distance, 3),
        }

    @staticmethod
    def _layer_key(zone_id: str, layer_id: str) -> str:
        return f"environment.{zone_id}.{layer_id}"

    @staticmethod
    def _is_scanner_pulse(layer: dict[str, Any]) -> bool:
        return layer.get("type") == "audio" and layer.get("playback_mode") == "scanner_pulse"

    @classmethod
    def _is_one_shot_audio(cls, layer: dict[str, Any]) -> bool:
        return (
            layer.get("type") == "audio"
            and not bool(layer.get("loop", True))
            and not cls._is_scanner_pulse(layer)
        )

    @staticmethod
    def _pulse_completion_signal(zone: dict[str, Any], layer: dict[str, Any]) -> str:
        return f"environment.{zone['id']}.{layer['id']}.pulse_finished"

    @staticmethod
    def _one_shot_completion_signal(zone: dict[str, Any], layer: dict[str, Any]) -> str:
        return f"environment.{zone['id']}.{layer['id']}.finished"

    @staticmethod
    def _location_sample_key(world: dict[str, Any]) -> str | None:
        location = world.get("location") if isinstance(world.get("location"), dict) else {}
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            return None
        updated_at = str(world.get("location_updated_at") or "")
        return f"{updated_at}|{float(latitude):.10f}|{float(longitude):.10f}"

    def _capture_scanner_motion(
        self, zone_state: dict[str, Any], location_state: dict[str, Any], world: dict[str, Any],
    ) -> bool:
        """Capture direction only when Elite has supplied a genuinely new position."""
        sample_key = self._location_sample_key(world)
        distance = location_state.get("distance")
        if sample_key is None or not isinstance(distance, (int, float)):
            return False
        if sample_key == zone_state.get("scanner_sample_key"):
            return False
        previous = zone_state.get("scanner_sample_distance_m")
        delta = float(distance) - float(previous) if isinstance(previous, (int, float)) else 0.0
        if delta < -0.5:
            direction = "approaching"
        elif delta > 0.5:
            direction = "retreating"
        else:
            direction = "steady"
        zone_state.update({
            "scanner_sample_key": sample_key,
            "scanner_sample_distance_m": round(float(distance), 3),
            "scanner_motion_direction": direction,
            "scanner_motion_delta_m": round(delta, 3),
        })
        return True

    @staticmethod
    def _scanner_gap_state(
        layer: dict[str, Any], zone_state: dict[str, Any], layer_state: dict[str, Any],
        *, advance_momentum: bool,
    ) -> float:
        """Return the measured cadence plus a small, bounded directional impulse."""
        measured = scanner_pulse_gap_seconds(layer, zone_state.get("depth_percent"))
        sample_key = zone_state.get("scanner_sample_key")
        if layer_state.get("pulse_sample_key") != sample_key:
            previous = layer_state.get("pulse_gap_seconds")
            current = measured if not isinstance(previous, (int, float)) else (
                float(previous) + (measured - float(previous)) * 0.35
            )
            pulse = layer.get("scanner_pulse") if isinstance(layer.get("scanner_pulse"), dict) else {}
            outer = max(0.0, float(pulse.get("outer_gap_seconds", 5.0)))
            core = max(0.0, min(outer, float(pulse.get("core_gap_seconds", 0.0))))
            limit_ratio = max(
                0.0, min(0.25, float(pulse.get("momentum_limit_percent", 10.0)) / 100.0),
            )
            current = max(
                core, measured * (1.0 - limit_ratio),
                min(outer, measured * (1.0 + limit_ratio), current),
            )
            layer_state.update({
                "pulse_sample_key": sample_key,
                "pulse_measured_gap_seconds": round(measured, 6),
                "pulse_gap_seconds": round(current, 6),
                "pulse_motion_direction": str(zone_state.get("scanner_motion_direction", "steady")),
                "pulse_momentum_steps": 0,
            })
        current = float(layer_state.get("pulse_gap_seconds", measured))
        if advance_momentum:
            direction = str(layer_state.get("pulse_motion_direction", "steady"))
            current = scanner_pulse_next_momentum_gap_seconds(layer, measured, current, direction)
            layer_state["pulse_gap_seconds"] = current
            if direction in {"approaching", "retreating"}:
                layer_state["pulse_momentum_steps"] = int(layer_state.get("pulse_momentum_steps", 0)) + 1
        return current

    @staticmethod
    def _vehicle_allowed(zone: dict[str, Any], world: dict[str, Any]) -> bool:
        allowed = zone.get("active_vehicles")
        return not isinstance(allowed, list) or str(world.get("vehicle", "unknown")) in allowed

    def _active_adjustments(self, target_layer: str) -> list[tuple[int, float, float, str]]:
        result: list[tuple[int, float, float, str]] = []
        for zone in self.zones:
            zone_id = str(zone["id"])
            if self.state.get("zones", {}).get(zone_id, {}).get("phase") != "active":
                continue
            for index, adjustment in enumerate(zone.get("layer_adjustments", [])):
                if not isinstance(adjustment, dict) or str(adjustment.get("target_layer", "")) != target_layer:
                    continue
                result.append((
                    int(adjustment.get("priority", 0)),
                    max(0.0, min(1.0, float(adjustment.get("volume", 1.0)))),
                    max(0.0, float(adjustment.get("transition_seconds", 2.0))),
                    f"adjustment:{zone_id}:{index}",
                ))
        return result

    def _effective_audio_level(self, layer_key: str, base_level: float) -> dict[str, Any]:
        adjustments = self._active_adjustments(layer_key)
        if adjustments:
            # Explicit priority wins. At equal priority, the quieter rule wins;
            # overlapping ducking zones can never unexpectedly make music louder.
            priority, volume, transition, source = sorted(
                adjustments, key=lambda item: (-item[0], item[1], item[3]),
            )[0]
            return {
                "volume": round(volume, 4), "transition_seconds": transition,
                "source": source, "priority": priority,
            }
        manual = self.state.get("manual_layer_volumes", {}).get(layer_key)
        if isinstance(manual, dict):
            return {
                "volume": round(max(0.0, min(1.0, float(manual.get("volume", base_level)))), 4),
                "transition_seconds": max(0.0, float(manual.get("transition_seconds", 2.0))),
                "source": "story_override", "priority": None,
            }
        _zone, layer = self._audio_layers[layer_key]
        return {
            "volume": round(max(0.0, min(1.0, base_level)), 4),
            "transition_seconds": max(0.0, float(layer.get("volume_transition_seconds", 2.0))),
            "source": "authored", "priority": None,
        }

    def _emit_audio_play(
        self, zone: dict[str, Any], layer: dict[str, Any], zone_state: dict[str, Any],
        layer_state: dict[str, Any], level: float,
    ) -> bool:
        scanner_pulse = self._is_scanner_pulse(layer)
        scanner_core_loop = scanner_pulse and float(zone_state.get("depth_percent") or 0.0) >= 100.0
        payload = {
            "action": "play_layer", "layer": self._layer_key(str(zone["id"]), str(layer["id"])),
            "asset": layer["asset"], "volume": round(level, 4),
            "loop": scanner_core_loop if scanner_pulse else bool(layer.get("loop", True)),
            "zone": zone["id"], "depth_percent": zone_state.get("depth_percent"),
        }
        if not scanner_pulse and bool(layer.get("loop", True)):
            payload["transition_seconds"] = max(0.0, float(layer.get("volume_transition_seconds", 2.0)))
        if scanner_pulse and not scanner_core_loop:
            payload["completion_signal"] = self._pulse_completion_signal(zone, layer)
        elif self._is_one_shot_audio(layer):
            payload["completion_signal"] = self._one_shot_completion_signal(zone, layer)
        emitted = self._emit(str(zone["id"]), "audio", payload)
        layer_state["scanner_core_loop"] = scanner_core_loop
        layer_state["output_active"] = emitted
        return emitted

    def _start_layer(self, zone: dict[str, Any], layer: dict[str, Any], zone_state: dict[str, Any]) -> None:
        layer_id = str(layer["id"])
        layer_state = zone_state["layers"].setdefault(layer_id, {})
        if layer["type"] == "wind":
            layer_state.update(playing=True, start_at=None, stop_at=None, level=1.0)
            self._event("environment.layer_started", zone=zone["id"], layer=layer_id, level=1.0)
            return
        base_level = layer_level(layer, zone_state.get("depth_percent"))
        key = self._layer_key(str(zone["id"]), layer_id)
        if layer["type"] == "audio":
            effective = self._effective_audio_level(key, base_level)
            level = float(effective["volume"])
            emitted = self._emit_audio_play(zone, layer, zone_state, layer_state, level)
        else:
            scanner_core_loop = False
            level = base_level
            self._emit(str(zone["id"]), "visual", {
                "action": "set_environment_layer", "layer": key, "cue": layer["cue"],
                "intensity": round(level, 4), "zone": zone["id"],
                "depth_percent": zone_state.get("depth_percent"),
            })
        layer_state.update({
            "playing": True, "start_at": None, "stop_at": None,
            "pulse_finished_at": None, "level": round(level, 4),
            "base_level": round(base_level, 4),
        })
        if layer["type"] == "audio":
            layer_state.update({
                "effective_source": effective["source"],
                "effective_transition_seconds": effective["transition_seconds"],
            })
            if emitted:
                layer_state["played_in_activation"] = True
        event_type = "environment.layer_pulse_started" if self._is_scanner_pulse(layer) else "environment.layer_started"
        self._event(event_type, zone=zone["id"], layer=layer_id, level=round(level, 4))

    def _stop_layer(self, zone: dict[str, Any], layer: dict[str, Any], zone_state: dict[str, Any]) -> None:
        layer_id = str(layer["id"])
        layer_state = zone_state["layers"].setdefault(layer_id, {})
        key = self._layer_key(str(zone["id"]), layer_id)
        if layer["type"] == "wind":
            layer_state.update(playing=False, start_at=None, stop_at=None, level=0.0)
            self._event("environment.layer_stopped", zone=zone["id"], layer=layer_id)
            return
        if layer["type"] == "audio":
            if layer_state.get("output_active"):
                payload = {"action": "stop_layer", "layer": key, "zone": zone["id"]}
                if not self._is_scanner_pulse(layer) and bool(layer.get("loop", True)):
                    payload["transition_seconds"] = max(0.0, float(layer.get("volume_transition_seconds", 2.0)))
                self._emit(str(zone["id"]), "audio", payload)
        else:
            self._emit(str(zone["id"]), "visual", {"action": "stop_environment_layer", "layer": key, "zone": zone["id"]})
        layer_state.update({
            "playing": False, "start_at": None, "stop_at": None,
            "pulse_finished_at": None, "scanner_core_loop": False, "level": 0.0,
            "base_level": 0.0, "output_active": False,
            "pulse_sample_key": None, "pulse_measured_gap_seconds": None,
            "pulse_gap_seconds": None, "pulse_motion_direction": "steady",
            "pulse_momentum_steps": 0,
        })
        self._event("environment.layer_stopped", zone=zone["id"], layer=layer_id)

    def _activate(self, zone: dict[str, Any], zone_state: dict[str, Any], location_state: dict[str, Any]) -> None:
        zone_state.update({
            "phase": "active", "entered_at": utc_now(), "exited_at": None, "exit_pending_at": None,
            "depth_percent": location_state["depth"], "distance_m": location_state["distance"], "layers": {},
            "activated_count": int(zone_state.get("activated_count", 0)) + 1,
            "scan_acquisition_active": False,
            "vehicle_suspended_at": None,
            "scanner_sample_key": None, "scanner_sample_distance_m": None,
            "scanner_motion_direction": "steady", "scanner_motion_delta_m": 0.0,
        })
        for layer in zone.get("layers", []):
            zone_state["layers"][str(layer["id"])] = {
                "playing": False,
                "start_at": self._deadline(float(layer.get("start_delay_seconds", 0.0))),
                "stop_at": None, "pulse_finished_at": None,
                "scanner_core_loop": False, "level": None, "base_level": None,
                "output_active": False,
                "played_in_activation": False,
                "pulse_sample_key": None, "pulse_measured_gap_seconds": None,
                "pulse_gap_seconds": None, "pulse_motion_direction": "steady",
                "pulse_momentum_steps": 0,
            }
        self._event("environment.entered", zone=zone["id"], depth_percent=location_state["depth"])
        self._lifecycle_signal("environment.entered", zone, depth_percent=location_state["depth"])
        self.print(f"[ENVIRONMENT] Entered {zone.get('title', zone['id'])}")

    def _deactivate(self, zone: dict[str, Any], zone_state: dict[str, Any], reason: str) -> None:
        zone_state.update({
            "phase": "suppressed", "exited_at": utc_now(), "depth_percent": 0.0,
            "scan_acquisition_active": False, "vehicle_suspended_at": None,
        })
        for layer in zone.get("layers", []):
            layer_state = zone_state["layers"].setdefault(str(layer["id"]), {})
            layer_state["start_at"] = None
            if layer_state.get("playing"):
                delay = float(layer.get("stop_delay_seconds", 0.0))
                if delay > 0:
                    layer_state["stop_at"] = self._deadline(delay)
                else:
                    self._stop_layer(zone, layer, zone_state)
        self._event("environment.exited", zone=zone["id"], reason=reason)
        self._lifecycle_signal("environment.exited", zone, reason=reason)
        self.print(f"[ENVIRONMENT] Left {zone.get('title', zone['id'])}: {reason}")

    def _suspend_for_vehicle(self, zone: dict[str, Any], zone_state: dict[str, Any]) -> None:
        """Pause an activation without turning a vehicle swap into a new visit."""
        zone_state.update({
            "phase": "vehicle_suppressed", "vehicle_suspended_at": utc_now(),
            "exit_pending_at": None,
        })
        for layer in zone.get("layers", []):
            layer_state = zone_state.get("layers", {}).get(str(layer["id"]), {})
            if layer_state.get("playing"):
                self._stop_layer(zone, layer, zone_state)
        self._event("environment.vehicle_suspended", zone=zone["id"])

    def _resume_from_vehicle(
        self, zone: dict[str, Any], zone_state: dict[str, Any], location_state: dict[str, Any],
    ) -> None:
        zone_state.update({
            "phase": "active", "vehicle_suspended_at": None,
            "depth_percent": location_state["depth"], "distance_m": location_state["distance"],
            "exit_pending_at": None,
        })
        for layer in zone.get("layers", []):
            layer_state = zone_state.get("layers", {}).setdefault(str(layer["id"]), {})
            if self._is_one_shot_audio(layer) and layer_state.get("played_in_activation"):
                continue
            layer_state["start_at"] = self._deadline(0.0)
        self._event("environment.vehicle_resumed", zone=zone["id"])

    def _process_layers(
        self, zone: dict[str, Any], zone_state: dict[str, Any], signal: dict[str, Any],
    ) -> bool:
        changed = False
        if zone_state.get("scan_suppressed_until_exit"):
            return False
        for layer in zone.get("layers", []):
            layer_state = zone_state["layers"].setdefault(str(layer["id"]), {})
            if (
                self._is_one_shot_audio(layer)
                and layer_state.get("playing")
                and signal.get("name") == self._one_shot_completion_signal(zone, layer)
            ):
                layer_state.update({
                    "playing": False, "start_at": None, "stop_at": None,
                    "output_active": False,
                })
                self._event("environment.layer_finished", zone=zone["id"], layer=layer["id"])
                changed = True
                continue
            if zone_state.get("scan_acquisition_active") and self._is_scanner_pulse(layer):
                continue
            if layer_state.get("stop_at") and self._is_due(layer_state["stop_at"]):
                self._stop_layer(zone, layer, zone_state)
                changed = True
                continue
            if zone_state.get("phase") != "active":
                continue
            if self._is_scanner_pulse(layer):
                sample_changed = layer_state.get("pulse_sample_key") != zone_state.get("scanner_sample_key")
                self._scanner_gap_state(layer, zone_state, layer_state, advance_momentum=False)
                changed = sample_changed or changed
            if (
                self._is_scanner_pulse(layer)
                and layer_state.get("playing")
                and layer_state.get("scanner_core_loop")
                and float(zone_state.get("depth_percent") or 0.0) < 100.0
            ):
                # Leaving the core changes the uninterrupted scanner loop back
                # into distance-spaced one-shot pulses.
                self._stop_layer(zone, layer, zone_state)
                layer_state["start_at"] = self._deadline(0.0)
                changed = True
            if (
                self._is_scanner_pulse(layer)
                and layer_state.get("playing")
                and signal.get("name") == self._pulse_completion_signal(zone, layer)
            ):
                finished_at = utc_now()
                gap = self._scanner_gap_state(
                    layer, zone_state, layer_state, advance_momentum=True,
                )
                layer_state.update({
                    "playing": False, "pulse_finished_at": finished_at,
                    "start_at": (
                        parse_time(finished_at) + timedelta(seconds=gap)
                    ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                })
                self._event(
                    "environment.layer_pulse_finished", zone=zone["id"],
                    layer=layer["id"], next_gap_seconds=round(gap, 3),
                    measured_gap_seconds=round(float(layer_state.get("pulse_measured_gap_seconds", gap)), 3),
                    motion_direction=str(layer_state.get("pulse_motion_direction", "steady")),
                    momentum_steps=int(layer_state.get("pulse_momentum_steps", 0)),
                )
                changed = True
            if self._is_scanner_pulse(layer) and not layer_state.get("playing") and layer_state.get("pulse_finished_at"):
                # A new coordinate re-anchors the cadence. Between coordinates,
                # each completed ping carries a small bounded directional impulse.
                gap = self._scanner_gap_state(
                    layer, zone_state, layer_state, advance_momentum=False,
                )
                due = parse_time(str(layer_state["pulse_finished_at"])) + timedelta(seconds=gap)
                due_text = due.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                if layer_state.get("start_at") != due_text:
                    layer_state["start_at"] = due_text
                    changed = True
            if not layer_state.get("playing") and self._is_due(layer_state.get("start_at")):
                self._start_layer(zone, layer, zone_state)
                changed = True
                continue
            if layer_state.get("playing") and layer["type"] == "wind":
                continue
            if layer_state.get("playing"):
                base_target = round(layer_level(layer, zone_state.get("depth_percent")), 4)
                if layer["type"] == "audio":
                    key = self._layer_key(str(zone["id"]), str(layer["id"]))
                    effective = self._effective_audio_level(key, base_target)
                    target = float(effective["volume"])
                else:
                    effective = None
                    target = base_target
                previous = layer_state.get("level")
                layer_state["base_level"] = base_target
                if previous is None or abs(float(previous) - target) >= 0.01:
                    key = self._layer_key(str(zone["id"]), str(layer["id"]))
                    if layer["type"] == "audio":
                        previous_transition = float(layer_state.get("effective_transition_seconds", 0.0) or 0.0)
                        transition = (float(effective["transition_seconds"])
                                      if str(effective["source"]).startswith("adjustment:")
                                      else max(float(effective["transition_seconds"]), previous_transition))
                        if layer_state.get("output_active"):
                            self._emit(str(zone["id"]), "audio", {
                                "action": "set_layer_volume", "layer": key, "volume": target,
                                "transition_seconds": transition,
                                "zone": zone["id"], "depth_percent": zone_state.get("depth_percent"),
                            })
                        layer_state["effective_source"] = effective["source"]
                        layer_state["effective_transition_seconds"] = effective["transition_seconds"]
                    else:
                        self._emit(str(zone["id"]), "visual", {
                            "action": "set_environment_layer", "layer": key, "cue": layer["cue"],
                            "intensity": target, "zone": zone["id"],
                            "depth_percent": zone_state.get("depth_percent"),
                        })
                    layer_state["level"] = target
                    changed = True
                if layer["type"] == "audio" and self.outputs_enabled and not layer_state.get("output_active"):
                    emitted = self._emit_audio_play(zone, layer, zone_state, layer_state, target)
                    if emitted:
                        layer_state["played_in_activation"] = True
                    changed = True
        return changed

    def dispatch(self, signal: dict[str, Any], world: dict[str, Any], variables: dict[str, Any]) -> bool:
        if not self.running:
            raise EnvironmentError("Start the environment runtime before dispatching")
        changed = False
        signal_name = str(signal.get("name", ""))
        control_states = self.state.setdefault("control_states", {})
        if signal_name == "presentation.scanner_startup_complete":
            scanner_on = bool(control_states.get("control.scanner"))
            if self.control_ready_states.get("control.scanner") != scanner_on:
                self.control_ready_states["control.scanner"] = scanner_on
                self._event(
                    "environment.control_ready", control="control.scanner",
                    enabled=scanner_on,
                )
                changed = True
        if signal_name in control_states:
            control_states[signal_name] = not bool(control_states.get(signal_name))
            if signal_name == "control.scanner":
                enabled = bool(control_states[signal_name])
                data = signal.get("data") if isinstance(signal.get("data"), dict) else {}
                self.control_ready_states[signal_name] = enabled and not bool(
                    data.get("control_feedback_pending")
                )
            self._event(
                "environment.control_toggled", control=signal_name,
                enabled=bool(control_states[signal_name]),
            )
            label = signal_name.removeprefix("control.").replace("_", " ").title()
            self.print(f"[CONTROL] {label} {'ON' if control_states[signal_name] else 'OFF'}")
            changed = True
        suppress_zone = ""
        if signal_name == "scan.detection_suppress" and isinstance(signal.get("data"), dict):
            suppress_zone = str(signal["data"].get("detection_zone", ""))
        pause_zone = ""
        if signal_name == "scan.target_started" and isinstance(signal.get("data"), dict):
            pause_zone = str(signal["data"].get("detection_zone", ""))
        resume_zone = ""
        if signal_name == "scan.target_cancelled" and isinstance(signal.get("data"), dict):
            resume_zone = str(signal["data"].get("detection_zone", ""))
        for zone in self.zones:
            zone_id = str(zone["id"])
            zone_state = self.state["zones"][zone_id]
            context = self._context(signal, world, variables, zone_state)
            location_state = self._location_state(zone, world)
            phase = zone_state.get("phase", "inactive")
            if pause_zone == zone_id and phase == "active":
                zone_state["scan_acquisition_active"] = True
                for layer in zone.get("layers", []):
                    if not self._is_scanner_pulse(layer):
                        continue
                    layer_state = zone_state.get("layers", {}).get(str(layer["id"]), {})
                    if layer_state.get("playing"):
                        self._stop_layer(zone, layer, zone_state)
                self._event("environment.scan_acquisition_paused", zone=zone_id)
                changed = True
            if resume_zone == zone_id:
                zone_state["scan_acquisition_active"] = False
                if phase == "active" and not zone_state.get("scan_suppressed_until_exit"):
                    for layer in zone.get("layers", []):
                        if self._is_scanner_pulse(layer):
                            layer_state = zone_state.get("layers", {}).setdefault(str(layer["id"]), {})
                            layer_state["start_at"] = self._deadline(0.0)
                    self._event("environment.scan_acquisition_resumed", zone=zone_id)
                changed = True
            if suppress_zone == zone_id and phase == "active":
                zone_state["scan_acquisition_active"] = False
                zone_state["scan_suppressed_until_exit"] = True
                for layer in zone.get("layers", []):
                    layer_state = zone_state.get("layers", {}).get(str(layer["id"]), {})
                    if layer_state.get("playing"):
                        self._stop_layer(zone, layer, zone_state)
                self._event("environment.scan_suppressed", zone=zone_id)
                changed = True
            required_control = str(zone.get("requires_control", "")).strip()
            effective_controls = self.effective_control_states()
            control_allowed = not required_control or bool(effective_controls.get(required_control))
            enabled = bool(zone.get("enabled", True)) and evaluate(zone.get("enabled_when", {"always": True}), context)
            gate_id = str(zone.get("time_gate", "")).strip()
            if gate_id:
                gate_state = world.get("time", {}).get("gates", {}).get(gate_id, {}).get("state")
                enabled = enabled and gate_state == str(zone.get("time_gate_state", "open"))
            vehicle_allowed = self._vehicle_allowed(zone, world)
            if phase == "inactive":
                never_spent = str(zone.get("rearm", "after_exit")) == "never" and int(zone_state.get("activated_count", 0)) > 0
                if not never_spent and control_allowed and enabled and vehicle_allowed and location_state["entry"] and evaluate(zone.get("entry_when", {"always": True}), context):
                    self._activate(zone, zone_state, location_state)
                    self._capture_scanner_motion(zone_state, location_state, world)
                    changed = True
            elif phase == "active":
                zone_state["depth_percent"] = location_state["depth"]
                zone_state["distance_m"] = location_state["distance"]
                changed = self._capture_scanner_motion(zone_state, location_state, world) or changed
                explicit_exit = evaluate(zone["exit_when"], context) if isinstance(zone.get("exit_when"), dict) else False
                location_exit = bool(location_state["exit"])
                if location_exit and not zone_state.get("exit_pending_at"):
                    zone_state["exit_pending_at"] = self._deadline(float(zone.get("exit_hold_seconds", 0.0)))
                    changed = True
                elif not location_exit and zone_state.get("exit_pending_at"):
                    zone_state["exit_pending_at"] = None
                    changed = True
                confirmed_location_exit = location_exit and self._is_due(zone_state.get("exit_pending_at"))
                if not vehicle_allowed:
                    self._suspend_for_vehicle(zone, zone_state)
                    changed = True
                elif not control_allowed or not enabled or explicit_exit or confirmed_location_exit:
                    reason = "control toggled off" if not control_allowed else ("disabled" if not enabled else ("exit condition" if explicit_exit else "location exit"))
                    self._deactivate(zone, zone_state, reason)
                    if confirmed_location_exit:
                        zone_state["scan_suppressed_until_exit"] = False
                    if required_control and str(zone.get("rearm", "after_exit")) == "immediate":
                        zone_state["phase"] = "inactive"
                        self._event("environment.rearmed", zone=zone_id)
                        self._lifecycle_signal("environment.rearmed", zone)
                    changed = True
            elif phase == "vehicle_suppressed":
                if location_state["outside_for_rearm"]:
                    zone_state.update({
                        "phase": (
                            "suppressed" if str(zone.get("rearm", "after_exit")) == "never"
                            else "inactive"
                        ),
                        "exited_at": utc_now(), "depth_percent": 0.0,
                        "scan_suppressed_until_exit": False,
                        "scan_acquisition_active": False, "vehicle_suspended_at": None,
                    })
                    self._event("environment.exited", zone=zone_id, reason="location exit while vehicle-suspended")
                    self._lifecycle_signal("environment.exited", zone, reason="location exit while vehicle-suspended")
                    changed = True
                elif vehicle_allowed:
                    if not control_allowed or not enabled:
                        reason = "control toggled off" if not control_allowed else "disabled"
                        self._deactivate(zone, zone_state, reason)
                        changed = True
                    elif location_state["entry"]:
                        self._resume_from_vehicle(zone, zone_state, location_state)
                        self._capture_scanner_motion(zone_state, location_state, world)
                        changed = True
                    # Missing position/height must not discard this activation.
                    # Resume when a valid reading arrives; a confirmed location
                    # exit is handled by the branch above.
            else:
                rearm = str(zone.get("rearm", "after_exit"))
                entry_false = not evaluate(zone.get("entry_when", {"always": True}), context)
                has_location = bool(zone.get("location"))
                ready = rearm == "immediate" or (
                    rearm == "after_exit" and (location_state["outside_for_rearm"] if has_location else entry_false)
                )
                if signal.get("source") == "environment":
                    ready = False
                if ready:
                    zone_state["phase"] = "inactive"
                    changed = True
                    self._event("environment.rearmed", zone=zone_id)
                    self._lifecycle_signal("environment.rearmed", zone)
        # Resolve every zone lifecycle first. Audio layers are processed only
        # after the complete set of active modifier zones is known, preventing
        # a full-volume frame before a simultaneous ducking rule is applied.
        for zone in self.zones:
            zone_state = self.state["zones"][str(zone["id"])]
            changed = self._process_layers(zone, zone_state, signal) or changed
        self.save()
        return changed

    def start_session(self, world: dict[str, Any] | None = None, variables: dict[str, Any] | None = None) -> None:
        self.running = True
        self.control_ready_states["control.scanner"] = bool(
            self.state.get("control_states", {}).get("control.scanner")
        )
        current_world = world or {}
        for zone in self.zones:
            zone_state = self.state["zones"][str(zone["id"])]
            if zone_state.get("phase") != "active":
                continue
            required_control = str(zone.get("requires_control", "")).strip()
            if required_control and not bool(self.state.get("control_states", {}).get(required_control)):
                zone_state["phase"] = "inactive"
                for layer_state in zone_state.get("layers", {}).values():
                    layer_state.update({
                        "playing": False, "start_at": None, "stop_at": None,
                        "pulse_finished_at": None, "scanner_core_loop": False, "level": 0.0,
                        "base_level": 0.0, "output_active": False,
                        "pulse_sample_key": None, "pulse_measured_gap_seconds": None,
                        "pulse_gap_seconds": None, "pulse_motion_direction": "steady",
                        "pulse_momentum_steps": 0,
                    })
                continue
            if not self._vehicle_allowed(zone, current_world):
                zone_state["phase"] = "vehicle_suppressed"
                zone_state["vehicle_suspended_at"] = utc_now()
                for layer in zone.get("layers", []):
                    layer_state = zone_state.get("layers", {}).setdefault(str(layer["id"]), {})
                    if self._is_one_shot_audio(layer) and layer_state.get("output_active"):
                        layer_state["played_in_activation"] = True
                    layer_state.update({
                        "playing": False, "start_at": None, "stop_at": None,
                        "level": 0.0, "base_level": 0.0, "output_active": False,
                    })
                continue
            for layer in zone.get("layers", []):
                layer_state = zone_state.get("layers", {}).get(str(layer["id"]), {})
                if not layer_state.get("playing"):
                    continue
                if self._is_one_shot_audio(layer) and layer_state.get(
                    "played_in_activation", True,
                ):
                    layer_state.update({"playing": False, "output_active": False})
                    continue
                self._start_layer(zone, layer, zone_state)
        self.dispatch(
            {"name": "system.start", "source": "system", "at": utc_now(), "data": {}},
            current_world, variables or {},
        )

    def stop_session(self) -> None:
        self.running = False
        self.save()

    def reset(self) -> None:
        for zone in self.zones:
            state = self.state.get("zones", {}).get(str(zone["id"]), {})
            for layer in zone.get("layers", []):
                if state.get("layers", {}).get(str(layer["id"]), {}).get("playing"):
                    self._stop_layer(zone, layer, state)
        self.state = self._fresh_state()
        self.control_ready_states = {"control.scanner": False}
        self.save()

    def advance_time(self, seconds: float) -> None:
        """Advance durable environment timers without waiting in an editor dry run."""
        if seconds < 0:
            raise EnvironmentError("Time advance must be zero or greater")
        amount = timedelta(seconds=float(seconds))
        for zone_state in self.state.get("zones", {}).values():
            for key in ("entered_at", "exit_pending_at"):
                if zone_state.get(key):
                    zone_state[key] = (parse_time(str(zone_state[key])) - amount).isoformat(
                        timespec="milliseconds"
                    ).replace("+00:00", "Z")
            for layer_state in zone_state.get("layers", {}).values():
                for key in ("start_at", "stop_at", "pulse_finished_at"):
                    if layer_state.get(key):
                        layer_state[key] = (parse_time(str(layer_state[key])) - amount).isoformat(
                            timespec="milliseconds"
                        ).replace("+00:00", "Z")
        self.save()

    @property
    def active_zones(self) -> list[str]:
        return [zone_id for zone_id, value in self.state.get("zones", {}).items() if value.get("phase") == "active"]

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.state)
