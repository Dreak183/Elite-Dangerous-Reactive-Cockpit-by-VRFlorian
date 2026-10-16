from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class GraphError(ValueError):
    pass


NODE_KINDS = {"start", "wait", "beat", "choice", "checkpoint", "fork", "join", "end"}
EFFECT_TYPES = {"log", "set", "increment", "next_step", "emit"}
PREDICATE_OPERATORS = {"eq", "ieq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "icontains", "startswith", "endswith", "exists"}


@dataclass(frozen=True)
class StoryGraph:
    path: Path
    data: dict[str, Any]

    @property
    def graph_id(self) -> str:
        return self.data["id"]

    @property
    def version(self) -> str:
        return self.data["version"]

    @property
    def start(self) -> str:
        return self.data["start"]

    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        return self.data["nodes"]

    @property
    def edges(self) -> list[dict[str, Any]]:
        return self.data["edges"]

    @property
    def defaults(self) -> dict[str, Any]:
        return dict(self.data.get("variables", {}))

    @property
    def time_gates(self) -> list[dict[str, Any]]:
        return list(self.data.get("time_gates", []))

    @property
    def scan_sets(self) -> list[dict[str, Any]]:
        return list(self.data.get("scan_sets", []))

    def outgoing(self, node_id: str) -> list[dict[str, Any]]:
        found = [e for e in self.edges if e["from"] == node_id]
        return sorted(found, key=lambda e: (-int(e.get("priority", 0)), e["id"]))


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _validate_predicate(predicate: Any, label: str, errors: list[str]) -> None:
    if not isinstance(predicate, dict):
        errors.append(f"{label} must be an object")
        return
    forms = [key for key in ("always", "all", "any", "not", "signal", "elapsed_seconds", "geofence", "path", "variable") if key in predicate]
    if len(forms) != 1:
        errors.append(f"{label} must contain exactly one predicate form")
        return
    form = forms[0]
    value = predicate[form]
    if form == "always":
        _require(isinstance(value, bool), f"{label}.always must be true or false", errors)
    elif form in {"all", "any"}:
        _require(isinstance(value, list) and bool(value), f"{label}.{form} must be a non-empty array", errors)
        if isinstance(value, list):
            for index, child in enumerate(value):
                _validate_predicate(child, f"{label}.{form}[{index}]", errors)
    elif form == "not":
        _validate_predicate(value, f"{label}.not", errors)
    elif form == "signal":
        _require(isinstance(value, str) and bool(value.strip()), f"{label}.signal must be a non-empty string", errors)
    elif form == "elapsed_seconds":
        _require(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0, f"{label}.elapsed_seconds must be zero or greater", errors)
    elif form == "geofence":
        _require(isinstance(value, dict), f"{label}.geofence must be an object", errors)
        if isinstance(value, dict):
            for key in ("latitude", "longitude", "radius_m"):
                number = value.get(key)
                _require(isinstance(number, (int, float)) and not isinstance(number, bool), f"{label}.geofence.{key} must be a number", errors)
            if isinstance(value.get("latitude"), (int, float)):
                _require(-90 <= value["latitude"] <= 90, f"{label}.geofence.latitude must be between -90 and 90", errors)
            if isinstance(value.get("longitude"), (int, float)):
                _require(-180 <= value["longitude"] <= 180, f"{label}.geofence.longitude must be between -180 and 180", errors)
            if isinstance(value.get("radius_m"), (int, float)):
                _require(value["radius_m"] > 0, f"{label}.geofence.radius_m must be greater than zero", errors)
            for key in ("body_radius_m",):
                if key in value:
                    _require(isinstance(value[key], (int, float)) and not isinstance(value[key], bool) and value[key] > 0, f"{label}.geofence.{key} must be greater than zero", errors)
                    if isinstance(value[key], (int, float)):
                        _require(value[key] >= 100_000, f"{label}.geofence.{key} looks like kilometres; store planet radius in metres", errors)
            for key in ("min_altitude_m", "max_altitude_m"):
                if key in value:
                    _require(isinstance(value[key], (int, float)) and not isinstance(value[key], bool), f"{label}.geofence.{key} must be a number", errors)
            if isinstance(value.get("min_altitude_m"), (int, float)) and isinstance(value.get("max_altitude_m"), (int, float)):
                _require(value["min_altitude_m"] <= value["max_altitude_m"], f"{label}.geofence altitude range is reversed", errors)
    else:
        _require(isinstance(value, str) and bool(value.strip()), f"{label}.{form} must be a non-empty string", errors)
        operator = predicate.get("op", "eq")
        _require(operator in PREDICATE_OPERATORS, f"{label}.op has unsupported operator {operator!r}", errors)
        if operator == "exists":
            _require(isinstance(predicate.get("value"), bool), f"{label}.value must be true or false for exists", errors)


def _validate_effect(effect: dict[str, Any], label: str, errors: list[str]) -> None:
    kind = effect.get("type")
    _require(kind in EFFECT_TYPES, f"{label} has unsupported effect", errors)
    if kind == "log":
        _require(isinstance(effect.get("message", ""), str), f"{label}.message must be a string", errors)
    elif kind in {"set", "increment"}:
        _require(isinstance(effect.get("variable"), str) and bool(effect.get("variable", "").strip()), f"{label}.variable must be a non-empty string", errors)
        if kind == "increment":
            amount = effect.get("amount", 1)
            _require(isinstance(amount, (int, float)) and not isinstance(amount, bool), f"{label}.amount must be a number", errors)
    elif kind == "next_step":
        _require(isinstance(effect.get("text", ""), str), f"{label}.text must be a string", errors)
        if "choices" in effect:
            choices = effect["choices"]
            _require(isinstance(choices, list) and all(isinstance(choice, str) for choice in choices), f"{label}.choices must be an array of strings", errors)
    elif kind == "emit":
        _require(isinstance(effect.get("channel"), str) and bool(effect.get("channel", "").strip()), f"{label}.channel must be a non-empty string", errors)
        payload = effect.get("payload", {})
        _require(isinstance(payload, dict), f"{label}.payload must be an object", errors)
        if effect.get("channel") == "platform" and isinstance(payload, dict):
            _require(payload.get("action") == "switch_graph", f"{label}.payload.action must be switch_graph on the platform channel", errors)
            _require(isinstance(payload.get("graph_id"), str) and bool(payload.get("graph_id", "").strip()), f"{label}.payload.graph_id must be a non-empty string", errors)
            if "version" in payload:
                _require(isinstance(payload.get("version"), str) and bool(payload.get("version", "").strip()), f"{label}.payload.version must be a non-empty string", errors)


def _validate_environment_layer(layer: Any, label: str, errors: list[str]) -> None:
    if not isinstance(layer, dict):
        errors.append(f"{label} must be an object")
        return
    _require(isinstance(layer.get("id"), str) and bool(layer.get("id", "").strip()), f"{label}.id must be a non-empty string", errors)
    kind = layer.get("type")
    _require(kind in {"audio", "visual", "wind"}, f"{label}.type must be audio, visual or wind", errors)
    if kind == "wind":
        from .wind import validate_wind
        try:
            validate_wind(layer)
        except (ValueError, TypeError, OverflowError) as exc:
            errors.append(f"{label}: {exc}")
    if kind == "audio":
        _require(isinstance(layer.get("asset"), str) and bool(layer.get("asset", "").strip()), f"{label}.asset must be a non-empty string", errors)
        volume = layer.get("volume", 1.0)
        _require(isinstance(volume, (int, float)) and not isinstance(volume, bool) and 0 <= volume <= 1, f"{label}.volume must be between 0 and 1", errors)
        _require(isinstance(layer.get("loop", True), bool), f"{label}.loop must be true or false", errors)
        transition = layer.get("volume_transition_seconds", 2.0)
        _require(isinstance(transition, (int, float)) and not isinstance(transition, bool) and transition >= 0, f"{label}.volume_transition_seconds must be zero or greater", errors)
        playback_mode = layer.get("playback_mode", "continuous")
        _require(playback_mode in {"continuous", "scanner_pulse"}, f"{label}.playback_mode is unsupported", errors)
        scanner_pulse = layer.get("scanner_pulse")
        if playback_mode == "scanner_pulse":
            _require(isinstance(scanner_pulse, dict), f"{label}.scanner_pulse must be an object", errors)
            if isinstance(scanner_pulse, dict):
                outer_gap = scanner_pulse.get("outer_gap_seconds", 5.0)
                core_gap = scanner_pulse.get("core_gap_seconds", 0.0)
                exponent = scanner_pulse.get("exponent", 1.0)
                momentum_step = scanner_pulse.get("momentum_step_percent", 4.0)
                momentum_limit = scanner_pulse.get("momentum_limit_percent", 10.0)
                _require(isinstance(outer_gap, (int, float)) and not isinstance(outer_gap, bool) and outer_gap >= 0, f"{label}.scanner_pulse.outer_gap_seconds must be zero or greater", errors)
                _require(isinstance(core_gap, (int, float)) and not isinstance(core_gap, bool) and core_gap >= 0, f"{label}.scanner_pulse.core_gap_seconds must be zero or greater", errors)
                if isinstance(outer_gap, (int, float)) and isinstance(core_gap, (int, float)):
                    _require(core_gap <= outer_gap, f"{label}.scanner_pulse.core_gap_seconds cannot exceed the outer gap", errors)
                _require(isinstance(exponent, (int, float)) and not isinstance(exponent, bool) and exponent > 0, f"{label}.scanner_pulse.exponent must be greater than zero", errors)
                _require(isinstance(momentum_step, (int, float)) and not isinstance(momentum_step, bool) and 0 <= momentum_step <= 25, f"{label}.scanner_pulse.momentum_step_percent must be between 0 and 25", errors)
                _require(isinstance(momentum_limit, (int, float)) and not isinstance(momentum_limit, bool) and 0 <= momentum_limit <= 25, f"{label}.scanner_pulse.momentum_limit_percent must be between 0 and 25", errors)
                if isinstance(momentum_step, (int, float)) and isinstance(momentum_limit, (int, float)):
                    _require(momentum_step <= momentum_limit, f"{label}.scanner_pulse.momentum_step_percent cannot exceed its momentum limit", errors)
    elif kind == "visual":
        _require(isinstance(layer.get("cue"), str) and bool(layer.get("cue", "").strip()), f"{label}.cue must be a non-empty string", errors)
        intensity = layer.get("intensity", 1.0)
        _require(isinstance(intensity, (int, float)) and not isinstance(intensity, bool) and 0 <= intensity <= 1, f"{label}.intensity must be between 0 and 1", errors)
    for key in ("start_delay_seconds", "stop_delay_seconds"):
        value = layer.get(key, 0)
        _require(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0, f"{label}.{key} must be zero or greater", errors)
    response = layer.get("depth_response")
    if response is not None:
        _require(isinstance(response, dict), f"{label}.depth_response must be an object", errors)
        if isinstance(response, dict):
            _require(response.get("curve", "constant") in {"constant", "linear", "exponential"}, f"{label}.depth_response.curve is unsupported", errors)
            start = response.get("start_percent", 0)
            _require(isinstance(start, (int, float)) and not isinstance(start, bool) and 0 <= start < 100, f"{label}.depth_response.start_percent must be at least 0 and below 100", errors)
            minimum = response.get("minimum", 0)
            _require(isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and 0 <= minimum <= 1, f"{label}.depth_response.minimum must be between 0 and 1", errors)
            exponent = response.get("exponent", 2)
            _require(isinstance(exponent, (int, float)) and not isinstance(exponent, bool) and exponent > 0, f"{label}.depth_response.exponent must be greater than zero", errors)
            silent_before_start = response.get("silent_before_start", False)
            _require(isinstance(silent_before_start, bool), f"{label}.depth_response.silent_before_start must be true or false", errors)


def _validate_layer_adjustment(adjustment: Any, label: str, errors: list[str]) -> None:
    if not isinstance(adjustment, dict):
        errors.append(f"{label} must be an object")
        return
    target = adjustment.get("target_layer")
    _require(
        isinstance(target, str) and target.startswith("environment.") and len(target.split(".")) >= 3,
        f"{label}.target_layer must name an environment.<zone>.<layer>", errors,
    )
    if adjustment.get("action") == "pause_wind":
        return
    _require(adjustment.get("action") in (None, "set_volume"), f"{label}.action is unsupported", errors)
    volume = adjustment.get("volume")
    _require(
        isinstance(volume, (int, float)) and not isinstance(volume, bool) and 0 <= volume <= 1,
        f"{label}.volume must be between 0 and 1", errors,
    )
    transition = adjustment.get("transition_seconds", 2.0)
    _require(
        isinstance(transition, (int, float)) and not isinstance(transition, bool) and transition >= 0,
        f"{label}.transition_seconds must be zero or greater", errors,
    )
    priority = adjustment.get("priority", 0)
    _require(isinstance(priority, int) and not isinstance(priority, bool), f"{label}.priority must be an integer", errors)


def _validate_environment(zone: Any, label: str, errors: list[str]) -> None:
    if not isinstance(zone, dict):
        errors.append(f"{label} must be an object")
        return
    _require(isinstance(zone.get("id"), str) and bool(zone.get("id", "").strip()), f"{label}.id must be a non-empty string", errors)
    _require(isinstance(zone.get("title"), str) and bool(zone.get("title", "").strip()), f"{label}.title must be a non-empty string", errors)
    _require(isinstance(zone.get("enabled", True), bool), f"{label}.enabled must be true or false", errors)
    active_vehicles = zone.get("active_vehicles")
    if active_vehicles is not None:
        _require(isinstance(active_vehicles, list) and bool(active_vehicles), f"{label}.active_vehicles must be a non-empty array", errors)
        if isinstance(active_vehicles, list):
            allowed = {"on_foot", "srv", "ship", "fighter"}
            _require(all(isinstance(value, str) and value in allowed for value in active_vehicles), f"{label}.active_vehicles contains an unsupported mode", errors)
    if any(isinstance(layer, dict) and layer.get("type") == "wind" for layer in (zone.get("layers") if isinstance(zone.get("layers"), list) else [])):
        location = zone.get("location", {})
        _require(isinstance(location, dict) and bool(location.get("body")), f"{label}: wind requires a planetary body in Location", errors)
    _require(zone.get("rearm", "after_exit") in {"after_exit", "immediate", "never"}, f"{label}.rearm is unsupported", errors)
    if "zone_type" in zone:
        _require(zone.get("zone_type") in {"surface", "event", "detection"}, f"{label}.zone_type is unsupported", errors)
    if "requires_control" in zone:
        _require(isinstance(zone.get("requires_control"), str) and zone.get("requires_control", "").startswith("control."), f"{label}.requires_control must be a control.* signal", errors)
    if "time_gate" in zone:
        _require(isinstance(zone.get("time_gate"), str) and bool(zone.get("time_gate", "").strip()), f"{label}.time_gate must be a non-empty string", errors)
        _require(zone.get("time_gate_state", "open") in {"open", "closed"}, f"{label}.time_gate_state must be open or closed", errors)
    exit_hold = zone.get("exit_hold_seconds", 0)
    _require(
        isinstance(exit_hold, (int, float)) and not isinstance(exit_hold, bool) and exit_hold >= 0,
        f"{label}.exit_hold_seconds must be zero or greater", errors,
    )
    for key in ("enabled_when", "entry_when", "exit_when"):
        if key in zone:
            _validate_predicate(zone[key], f"{label}.{key}", errors)
    location = zone.get("location")
    if location is not None:
        _require(isinstance(location, dict), f"{label}.location must be an object", errors)
        if isinstance(location, dict):
            for key in ("system", "body", "station", "settlement"):
                if key in location:
                    _require(isinstance(location[key], str) and bool(location[key].strip()), f"{label}.location.{key} must be a non-empty string", errors)
            has_latitude = "latitude" in location
            has_longitude = "longitude" in location
            _require(has_latitude == has_longitude, f"{label}.location requires both latitude and longitude", errors)
            if has_latitude and has_longitude:
                latitude, longitude = location.get("latitude"), location.get("longitude")
                _require(isinstance(latitude, (int, float)) and not isinstance(latitude, bool) and -90 <= latitude <= 90, f"{label}.location.latitude must be between -90 and 90", errors)
                _require(isinstance(longitude, (int, float)) and not isinstance(longitude, bool) and -180 <= longitude <= 180, f"{label}.location.longitude must be between -180 and 180", errors)
                entry_radius = location.get("entry_radius_m")
                exit_radius = location.get("exit_radius_m", entry_radius)
                core_radius = location.get("core_radius_m", 0)
                _require(isinstance(entry_radius, (int, float)) and not isinstance(entry_radius, bool) and entry_radius > 0, f"{label}.location.entry_radius_m must be greater than zero", errors)
                if isinstance(entry_radius, (int, float)):
                    _require(isinstance(exit_radius, (int, float)) and not isinstance(exit_radius, bool) and exit_radius >= entry_radius, f"{label}.location.exit_radius_m must be at least the entry radius", errors)
                    _require(isinstance(core_radius, (int, float)) and not isinstance(core_radius, bool) and 0 <= core_radius < entry_radius, f"{label}.location.core_radius_m must be at least zero and below the entry radius", errors)
                if "body_radius_m" in location:
                    value = location["body_radius_m"]
                    _require(isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0, f"{label}.location.body_radius_m must be greater than zero", errors)
                    if isinstance(value, (int, float)):
                        _require(value >= 100_000, f"{label}.location.body_radius_m looks like kilometres; store planet radius in metres", errors)
            for key in ("min_altitude_m", "max_altitude_m"):
                if key in location:
                    _require(isinstance(location[key], (int, float)) and not isinstance(location[key], bool), f"{label}.location.{key} must be a number", errors)
            if isinstance(location.get("min_altitude_m"), (int, float)) and isinstance(location.get("max_altitude_m"), (int, float)):
                _require(location["min_altitude_m"] <= location["max_altitude_m"], f"{label}.location altitude range is reversed", errors)
    layers = zone.get("layers", [])
    adjustments = zone.get("layer_adjustments", [])
    _require(isinstance(layers, list), f"{label}.layers must be an array", errors)
    _require(isinstance(adjustments, list), f"{label}.layer_adjustments must be an array", errors)
    _require(bool(layers) or bool(adjustments), f"{label} requires at least one layer or layer adjustment", errors)
    if isinstance(layers, list):
        layer_ids: set[str] = set()
        for index, layer in enumerate(layers):
            _validate_environment_layer(layer, f"{label}.layers[{index}]", errors)
            if isinstance(layer, dict) and isinstance(layer.get("id"), str):
                _require(layer["id"] not in layer_ids, f"{label} has duplicate layer id {layer['id']}", errors)
                layer_ids.add(layer["id"])
    if isinstance(adjustments, list):
        for index, adjustment in enumerate(adjustments):
            _validate_layer_adjustment(adjustment, f"{label}.layer_adjustments[{index}]", errors)


def validate_graph(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["graph root must be an object"]
    _require(data.get("schema") == "platform.story-graph.v1", "schema must be platform.story-graph.v1", errors)
    for key in ("id", "version", "title", "start"):
        _require(isinstance(data.get(key), str) and bool(data.get(key).strip()), f"{key} must be a non-empty string", errors)
    nodes = data.get("nodes")
    edges = data.get("edges")
    _require(isinstance(nodes, dict) and bool(nodes), "nodes must be a non-empty object", errors)
    _require(isinstance(edges, list), "edges must be an array", errors)
    if not isinstance(nodes, dict) or not isinstance(edges, list):
        return errors
    start = data.get("start")
    _require(start in nodes, f"start node {start!r} does not exist", errors)
    if "recovery_when" in data:
        _validate_predicate(data["recovery_when"], "recovery_when", errors)
    edge_ids: set[str] = set()
    for node_id, node in nodes.items():
        _require(isinstance(node_id, str) and bool(node_id), "node ids must be non-empty strings", errors)
        _require(isinstance(node, dict), f"node {node_id} must be an object", errors)
        if not isinstance(node, dict):
            continue
        kind = node.get("kind")
        _require(kind in NODE_KINDS, f"node {node_id} has unsupported kind {kind!r}", errors)
        effects = node.get("on_enter", [])
        _require(isinstance(effects, list), f"node {node_id}.on_enter must be an array", errors)
        if isinstance(effects, list):
            for index, effect in enumerate(effects):
                _require(isinstance(effect, dict), f"node {node_id}.on_enter[{index}] must be an object", errors)
                if isinstance(effect, dict):
                    _validate_effect(effect, f"node {node_id}.on_enter[{index}]", errors)
        if kind == "join":
            join_from = node.get("join_from")
            join_count = node.get("join_count")
            valid_join = (
                isinstance(join_from, list) and len(join_from) >= 2 and all(isinstance(item, str) for item in join_from)
            ) or (isinstance(join_count, int) and join_count >= 2)
            _require(valid_join, f"join node {node_id} requires join_from (2+ node ids) or join_count >= 2", errors)
    for index, edge in enumerate(edges):
        label = f"edge[{index}]"
        _require(isinstance(edge, dict), f"{label} must be an object", errors)
        if not isinstance(edge, dict):
            continue
        edge_id = edge.get("id")
        _require(isinstance(edge_id, str) and bool(edge_id), f"{label}.id must be a non-empty string", errors)
        if isinstance(edge_id, str):
            _require(edge_id not in edge_ids, f"duplicate edge id {edge_id}", errors)
            edge_ids.add(edge_id)
        source = edge.get("from")
        target = edge.get("to")
        _require(source in nodes, f"edge {edge_id} source {source!r} does not exist", errors)
        targets = target if isinstance(target, list) else [target]
        _require(bool(targets), f"edge {edge_id} has no target", errors)
        for item in targets:
            _require(item in nodes, f"edge {edge_id} target {item!r} does not exist", errors)
        _validate_predicate(edge.get("when", {"always": True}), f"edge {edge_id}.when", errors)
    if isinstance(start, str) and start in nodes:
        _require(nodes[start].get("kind") == "start", "start must point to a node with kind=start", errors)
    start_nodes = [node_id for node_id, node in nodes.items() if isinstance(node, dict) and node.get("kind") == "start"]
    _require(len(start_nodes) == 1, f"graph must contain exactly one start node; found {len(start_nodes)}", errors)
    for edge in edges:
        if isinstance(edge, dict) and edge.get("from") in nodes and isinstance(nodes[edge["from"]], dict):
            _require(nodes[edge["from"]].get("kind") != "end", f"end node {edge['from']} cannot have outgoing edge {edge.get('id')}", errors)
    for node_id, node in nodes.items():
        if isinstance(node, dict) and isinstance(node.get("join_from"), list):
            for source in node["join_from"]:
                _require(source in nodes, f"join node {node_id} references missing source {source!r}", errors)
    environments = data.get("environments", [])
    _require(isinstance(environments, list), "environments must be an array", errors)
    if isinstance(environments, list):
        environment_ids: set[str] = set()
        environment_layer_ids: set[str] = set()
        wind_layer_ids: set[str] = set()
        for index, zone in enumerate(environments):
            _validate_environment(zone, f"environment[{index}]", errors)
            if isinstance(zone, dict) and isinstance(zone.get("id"), str):
                _require(zone["id"] not in environment_ids, f"duplicate environment id {zone['id']}", errors)
                environment_ids.add(zone["id"])
                for layer in zone.get("layers", []):
                    if isinstance(layer, dict) and layer.get("type") == "audio" and isinstance(layer.get("id"), str):
                        environment_layer_ids.add(f"environment.{zone['id']}.{layer['id']}")
                    if isinstance(layer, dict) and layer.get("type") == "wind" and isinstance(layer.get("id"), str):
                        wind_layer_ids.add(f"environment.{zone['id']}.{layer['id']}")
        for zone_index, zone in enumerate(environments):
            if not isinstance(zone, dict):
                continue
            for adjustment_index, adjustment in enumerate(zone.get("layer_adjustments", [])):
                if isinstance(adjustment, dict):
                    _require(
                        adjustment.get("target_layer") in (wind_layer_ids if adjustment.get("action") == "pause_wind" else environment_layer_ids),
                        f"environment[{zone_index}].layer_adjustments[{adjustment_index}].target_layer references an unknown layer or the wrong layer type",
                        errors,
                    )

    clocks = data.get("solar_clocks", [])
    sites = data.get("solar_sites", [])
    gates = data.get("time_gates", [])
    events = data.get("time_events", [])
    for name, value in (("solar_clocks", clocks), ("solar_sites", sites), ("time_gates", gates), ("time_events", events)):
        _require(isinstance(value, list), f"{name} must be an array", errors)
    clock_ids: set[str] = set()
    if isinstance(clocks, list):
        for index, clock in enumerate(clocks):
            label = f"solar_clock[{index}]"
            _require(isinstance(clock, dict), f"{label} must be an object", errors)
            if not isinstance(clock, dict):
                continue
            clock_id = clock.get("id")
            _require(isinstance(clock_id, str) and bool(clock_id.strip()), f"{label}.id must be a non-empty string", errors)
            if isinstance(clock_id, str):
                _require(clock_id not in clock_ids, f"duplicate solar clock id {clock_id}", errors)
                clock_ids.add(clock_id)
            _require(isinstance(clock.get("solar_day_seconds"), (int, float)) and not isinstance(clock.get("solar_day_seconds"), bool) and clock.get("solar_day_seconds", 0) > 0, f"{label}.solar_day_seconds must be greater than zero", errors)
            _require(clock.get("direction", 1) in {-1, 1}, f"{label}.direction must be -1 or 1", errors)
            anchor = clock.get("anchor")
            _require(isinstance(anchor, dict), f"{label}.anchor must be an object", errors)
            if isinstance(anchor, dict):
                _require(isinstance(anchor.get("at_utc"), str) and bool(anchor.get("at_utc", "").strip()), f"{label}.anchor.at_utc must be an ISO timestamp", errors)
                _require(isinstance(anchor.get("longitude"), (int, float)) and -180 <= anchor.get("longitude", 999) <= 180, f"{label}.anchor.longitude must be between -180 and 180", errors)
                _require(isinstance(anchor.get("local_hour"), (int, float)) and 0 <= anchor.get("local_hour", -1) < 24, f"{label}.anchor.local_hour must be between 0 and 24", errors)
    site_ids: set[str] = set()
    if isinstance(sites, list):
        for index, site in enumerate(sites):
            label = f"solar_site[{index}]"
            _require(isinstance(site, dict), f"{label} must be an object", errors)
            if not isinstance(site, dict):
                continue
            site_id = site.get("id")
            _require(isinstance(site_id, str) and bool(site_id.strip()), f"{label}.id must be a non-empty string", errors)
            if isinstance(site_id, str):
                _require(site_id not in site_ids, f"duplicate solar site id {site_id}", errors)
                site_ids.add(site_id)
            _require(site.get("clock") in clock_ids, f"{label}.clock references an unknown solar clock", errors)
            _require(isinstance(site.get("latitude"), (int, float)) and -90 <= site.get("latitude", 999) <= 90, f"{label}.latitude must be between -90 and 90", errors)
            _require(isinstance(site.get("longitude"), (int, float)) and -180 <= site.get("longitude", 999) <= 180, f"{label}.longitude must be between -180 and 180", errors)
            if "anchor" in site:
                anchor = site.get("anchor")
                _require(isinstance(anchor, dict), f"{label}.anchor must be an object", errors)
                if isinstance(anchor, dict):
                    _require(isinstance(anchor.get("at_utc"), str) and bool(anchor.get("at_utc", "").strip()), f"{label}.anchor.at_utc must be an ISO timestamp", errors)
                    _require(isinstance(anchor.get("longitude"), (int, float)) and -180 <= anchor.get("longitude", 999) <= 180, f"{label}.anchor.longitude must be between -180 and 180", errors)
                    _require(isinstance(anchor.get("local_hour"), (int, float)) and 0 <= anchor.get("local_hour", -1) < 24, f"{label}.anchor.local_hour must be between 0 and 24", errors)
    gate_ids: set[str] = set()
    if isinstance(gates, list):
        for index, gate in enumerate(gates):
            label = f"time_gate[{index}]"
            _require(isinstance(gate, dict), f"{label} must be an object", errors)
            if not isinstance(gate, dict):
                continue
            gate_id = gate.get("id")
            _require(isinstance(gate_id, str) and bool(gate_id.strip()), f"{label}.id must be a non-empty string", errors)
            if isinstance(gate_id, str):
                _require(gate_id not in gate_ids, f"duplicate time gate id {gate_id}", errors)
                gate_ids.add(gate_id)
            _require(gate.get("site") in site_ids, f"{label}.site references an unknown solar site", errors)
            for key in ("open_local_hour", "close_local_hour"):
                value = gate.get(key)
                _require(isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 24, f"{label}.{key} must be between 0 and 24", errors)
    if isinstance(events, list):
        event_ids: set[str] = set()
        for index, event in enumerate(events):
            label = f"time_event[{index}]"
            _require(isinstance(event, dict), f"{label} must be an object", errors)
            if not isinstance(event, dict):
                continue
            event_id = event.get("id")
            _require(isinstance(event_id, str) and bool(event_id.strip()), f"{label}.id must be a non-empty string", errors)
            if isinstance(event_id, str):
                _require(event_id not in event_ids, f"duplicate time event id {event_id}", errors)
                event_ids.add(event_id)
            _require(event.get("gate") in gate_ids, f"{label}.gate references an unknown time gate", errors)
            _require(event.get("boundary", "open") in {"open", "close"}, f"{label}.boundary must be open or close", errors)
            _require(event.get("repeat", "once_per_cycle") in {"once_per_story", "once_per_mission", "once_per_cycle", "every_crossing"}, f"{label}.repeat is unsupported", errors)
            _require(isinstance(event.get("delay_seconds", 0), (int, float)) and not isinstance(event.get("delay_seconds", 0), bool) and event.get("delay_seconds", 0) >= 0, f"{label}.delay_seconds must be zero or greater", errors)
            effects = event.get("effects", [])
            _require(isinstance(effects, list) and bool(effects), f"{label}.effects must be a non-empty array", errors)
            if isinstance(effects, list):
                for effect_index, effect in enumerate(effects):
                    _require(isinstance(effect, dict), f"{label}.effects[{effect_index}] must be an object", errors)
                    if isinstance(effect, dict):
                        _validate_effect(effect, f"{label}.effects[{effect_index}]", errors)
                        _require(effect.get("type") in {"emit", "log"}, f"{label}.effects[{effect_index}] must be emit or log", errors)

    if isinstance(environments, list):
        for index, zone in enumerate(environments):
            if isinstance(zone, dict) and zone.get("time_gate"):
                _require(zone.get("time_gate") in gate_ids, f"environment[{index}].time_gate references an unknown time gate", errors)

    scan_sets = data.get("scan_sets", [])
    _require(isinstance(scan_sets, list), "scan_sets must be an array", errors)
    if isinstance(scan_sets, list):
        scan_set_ids: set[str] = set()
        for set_index, scan_set in enumerate(scan_sets):
            label = f"scan_set[{set_index}]"
            _require(isinstance(scan_set, dict), f"{label} must be an object", errors)
            if not isinstance(scan_set, dict):
                continue
            set_id = scan_set.get("id")
            _require(isinstance(set_id, str) and bool(set_id.strip()), f"{label}.id must be a non-empty string", errors)
            if isinstance(set_id, str):
                _require(set_id not in scan_set_ids, f"duplicate scan set id {set_id}", errors)
                scan_set_ids.add(set_id)
            if "enabled_when" in scan_set:
                _validate_predicate(scan_set["enabled_when"], f"{label}.enabled_when", errors)
            _require(isinstance(scan_set.get("requires_control", "control.scanner"), str) and scan_set.get("requires_control", "").startswith("control."), f"{label}.requires_control must be a control.* signal", errors)
            _require(isinstance(scan_set.get("hold_seconds", 8), (int, float)) and scan_set.get("hold_seconds", 0) > 0, f"{label}.hold_seconds must be greater than zero", errors)
            for key in ("acquisition_asset", "completion_asset"):
                _require(isinstance(scan_set.get(key), str) and bool(scan_set.get(key, "").strip()), f"{label}.{key} must be a non-empty string", errors)
            for key in ("acquisition_volume", "completion_volume"):
                if key in scan_set:
                    value = scan_set[key]
                    _require(isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1, f"{label}.{key} must be between 0 and 1", errors)
            targets = scan_set.get("targets")
            _require(isinstance(targets, list) and bool(targets), f"{label}.targets must be a non-empty array", errors)
            target_ids: set[str] = set()
            if isinstance(targets, list):
                for target_index, target in enumerate(targets):
                    target_label = f"{label}.targets[{target_index}]"
                    _require(isinstance(target, dict), f"{target_label} must be an object", errors)
                    if not isinstance(target, dict):
                        continue
                    target_id = target.get("id")
                    _require(isinstance(target_id, str) and bool(target_id.strip()), f"{target_label}.id must be a non-empty string", errors)
                    if isinstance(target_id, str):
                        _require(target_id not in target_ids, f"{label} has duplicate target id {target_id}", errors)
                        target_ids.add(target_id)
                    _require(target.get("detection_zone") in environment_ids, f"{target_label}.detection_zone references an unknown environment", errors)
                    location = target.get("location")
                    _require(isinstance(location, dict), f"{target_label}.location must be an object", errors)
                    if isinstance(location, dict):
                        for key in ("latitude", "longitude", "core_radius_m", "body_radius_m"):
                            _require(isinstance(location.get(key), (int, float)) and not isinstance(location.get(key), bool), f"{target_label}.location.{key} must be a number", errors)
                        if isinstance(location.get("body_radius_m"), (int, float)):
                            _require(location["body_radius_m"] >= 100_000, f"{target_label}.location.body_radius_m looks like kilometres; store metres", errors)
    navigators = data.get("navigators", [])
    _require(isinstance(navigators, list), "navigators must be an array", errors)
    if isinstance(navigators, list):
        navigator_ids: set[str] = set()
        for index, navigator in enumerate(navigators):
            label = f"navigator[{index}]"
            _require(isinstance(navigator, dict), f"{label} must be an object", errors)
            if not isinstance(navigator, dict):
                continue
            navigator_id = navigator.get("id")
            _require(isinstance(navigator_id, str) and bool(navigator_id.strip()), f"{label}.id must be a non-empty string", errors)
            if isinstance(navigator_id, str):
                _require(navigator_id not in navigator_ids, f"duplicate navigator id {navigator_id}", errors)
                navigator_ids.add(navigator_id)
            _validate_predicate(navigator.get("active_when", {"always": False}), f"{label}.active_when", errors)
            _require(isinstance(navigator.get("ping_asset"), str) and bool(navigator.get("ping_asset", "").strip()), f"{label}.ping_asset must be a non-empty string", errors)
            targets = navigator.get("targets")
            _require(isinstance(targets, list) and bool(targets), f"{label}.targets must be a non-empty array", errors)
            if isinstance(targets, list):
                for target_index, target in enumerate(targets):
                    target_label = f"{label}.targets[{target_index}]"
                    _require(isinstance(target, dict), f"{target_label} must be an object", errors)
                    if not isinstance(target, dict):
                        continue
                    _require(isinstance(target.get("id"), str) and bool(target.get("id", "").strip()), f"{target_label}.id must be a non-empty string", errors)
                    _validate_predicate(target.get("complete_when", {"always": False}), f"{target_label}.complete_when", errors)
                    location = target.get("location")
                    _require(isinstance(location, dict), f"{target_label}.location must be an object", errors)
                    if isinstance(location, dict):
                        for key in ("latitude", "longitude", "body_radius_m"):
                            _require(isinstance(location.get(key), (int, float)) and not isinstance(location.get(key), bool), f"{target_label}.location.{key} must be a number", errors)
    return errors


def load_graph(path: Path) -> StoryGraph:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GraphError(f"Cannot read graph {path}: {exc}") from exc
    errors = validate_graph(data)
    if errors:
        raise GraphError("Invalid graph:\n- " + "\n- ".join(errors))
    return StoryGraph(path=path.resolve(), data=data)
