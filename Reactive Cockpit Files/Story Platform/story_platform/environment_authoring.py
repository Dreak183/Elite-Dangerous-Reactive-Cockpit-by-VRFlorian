from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .authoring import predicate_summary, simulator_preset_for
from .config import default_journal_dir, load_config, resolve_path
from .telemetry import WorldModel, seed_world_from_latest_journal


_NAMED_LOCATION_CACHE: dict[str, Any] = {}


def default_planetary_glide_audio_layer() -> dict[str, Any]:
    return {
        "id": "glide_arrival",
        "type": "audio",
        "asset": "",
        "volume": 1.0,
        "loop": False,
        "volume_transition_seconds": 0.0,
    }


def default_environment(template: str = "surface") -> dict[str, Any]:
    zone: dict[str, Any] = {
        "id": "new_environment", "title": "New environment", "enabled": True,
        "entry_when": {"always": True}, "rearm": "after_exit", "exit_hold_seconds": 2.0, "layers": [],
    }
    if template == "surface":
        zone["zone_type"] = "surface"
        zone["location"] = {
            "latitude": 0.0, "longitude": 0.0, "entry_radius_m": 1000.0,
            "exit_radius_m": 1100.0, "core_radius_m": 0.0,
        }
        zone["title"] = "New surface environment"
    elif template == "event":
        zone["zone_type"] = "event"
        zone["title"] = "New event-bounded environment"
        zone["entry_when"] = {"signal": "journal.SupercruiseDestinationDrop"}
        zone["exit_when"] = {"any": [
            {"signal": "journal.SupercruiseEntry"}, {"signal": "journal.FSDJump"},
            {"signal": "journal.CarrierJump"},
        ]}
    elif template == "planetary_glide":
        zone.update({
            "id": "planetary_glide_arrival",
            "title": "New planetary glide sound",
            "zone_type": "event",
            "entry_when": {"signal": "status.glide.entered"},
            "exit_when": {
                "path": "world.flags2.glide_mode", "op": "eq", "value": False,
            },
            "rearm": "immediate",
            "exit_hold_seconds": 0.0,
            "active_vehicles": ["ship"],
        })
    elif template == "detection":
        zone.update({
            "zone_type": "detection", "title": "New detection environment",
            "requires_control": "control.scanner", "rearm": "immediate",
            "location": {
                "latitude": 0.0, "longitude": 0.0, "entry_radius_m": 50.0,
                "exit_radius_m": 60.0, "core_radius_m": 5.0,
            },
        })
    return zone


def environment_summary(zone: dict[str, Any]) -> str:
    location = zone.get("location") if isinstance(zone.get("location"), dict) else {}
    if zone.get("zone_type") == "detection":
        scope = f"detection zone · {location.get('entry_radius_m', '?')} m range / {location.get('core_radius_m', '?')} m core"
    elif "latitude" in location and "longitude" in location:
        scope = f"surface zone · {location.get('entry_radius_m', '?')} m entry / {location.get('exit_radius_m', '?')} m exit"
    elif location:
        names = [str(location[key]) for key in ("system", "body", "station", "settlement") if location.get(key)]
        scope = "named place · " + (" / ".join(names) if names else "location")
    else:
        scope = "event/story bounded"
    layers = zone.get("layers") if isinstance(zone.get("layers"), list) else []
    adjustments = zone.get("layer_adjustments") if isinstance(zone.get("layer_adjustments"), list) else []
    active_vehicles = zone.get("active_vehicles")
    vehicle_scope = f" · {', '.join(str(value).replace('_', ' ') for value in active_vehicles)} only" if isinstance(active_vehicles, list) else ""
    adjustment_scope = f" · {len(adjustments)} mix adjustment(s)" if adjustments else ""
    return f"{scope}{vehicle_scope} · {len(layers)} layer(s){adjustment_scope} · starts {predicate_summary(zone.get('entry_when', {'always': True}))}"


def environment_entry_preset(zone: dict[str, Any], depth_percent: float = 100.0) -> dict[str, Any]:
    preset = simulator_preset_for(zone.get("entry_when", {"always": True}))
    active_vehicles = zone.get("active_vehicles")
    if isinstance(active_vehicles, list) and active_vehicles:
        preset["world"]["vehicle"] = str(active_vehicles[0])
    location_rule = zone.get("location") if isinstance(zone.get("location"), dict) else {}
    if location_rule:
        location = preset["world"].setdefault("location", {})
        for key in ("system", "body", "station", "settlement"):
            if location_rule.get(key):
                location[key] = location_rule[key]
        for key in ("latitude", "longitude", "body_radius_m"):
            if key in location_rule:
                location[key] = location_rule[key]
        # At the exact authored coordinates any positive radius yields distance zero.
        # This fallback is simulator-only; live play still requires telemetry or an authored radius.
        if "latitude" in location_rule and "body_radius_m" not in location:
            location["body_radius_m"] = 1.0
        if "latitude" in location_rule:
            depth = max(0.0, min(100.0, float(depth_percent)))
            outer = float(location_rule["entry_radius_m"])
            core = float(location_rule.get("core_radius_m", 0.0))
            distance = outer - (depth / 100.0) * (outer - core)
            radius = float(location.get("body_radius_m") or 1.0)
            location["latitude"] = max(
                -90.0,
                min(90.0, float(location_rule["latitude"]) + math.degrees(distance / radius)),
            )
            preset["notes"].append(f"Surface test position: {depth:g}% zone depth")
        if location_rule.get("min_altitude_m") is not None:
            location["altitude"] = location_rule["min_altitude_m"]
        elif location_rule.get("max_altitude_m") is not None:
            location["altitude"] = location_rule["max_altitude_m"]
    return preset


def _read_world_snapshot(snapshot_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("No live Elite location has been captured yet. Start the platform while Elite is running.") from exc
    except (json.JSONDecodeError, PermissionError) as exc:
        raise ValueError(f"Cannot read the live Elite location: {exc}") from exc
    location = value.get("location") if isinstance(value, dict) else None
    if not isinstance(location, dict) or not location:
        raise ValueError("The live telemetry snapshot does not contain a location yet.")
    result = copy.deepcopy(location)
    coordinate_time = value.get("location_updated_at")
    if isinstance(coordinate_time, str):
        result["captured_at"] = coordinate_time
    elif isinstance(value.get("at"), str):
        result["captured_at"] = value["at"]
    if isinstance(value.get("at"), str):
        result["snapshot_at"] = value["at"]
    return result


def _named_location_from_journal(journal_dir: Path, body: str) -> dict[str, Any]:
    cache_key = str(journal_dir.resolve())
    cached = _NAMED_LOCATION_CACHE.get(cache_key)
    if isinstance(cached, dict) and str(cached.get("body", "")).casefold() == body.casefold():
        return copy.deepcopy(cached)
    world = WorldModel()
    seed_world_from_latest_journal(journal_dir, world)
    location = world.data.get("location") if isinstance(world.data.get("location"), dict) else {}
    result = {
        key: location[key]
        for key in ("system", "body", "station", "settlement", "body_radius_m")
        if location.get(key) not in (None, "")
    }
    if body:
        result["body"] = body
    _NAMED_LOCATION_CACHE[cache_key] = copy.deepcopy(result)
    return result


def read_elite_status_location(journal_dir: Path | None = None) -> dict[str, Any]:
    """Read Elite's precise live Status.json without requiring the platform runtime."""
    if journal_dir is None:
        config = load_config()
        configured = str(config.get("journal_dir", "")).strip()
        journal_dir = resolve_path(configured) if configured else default_journal_dir()
    status_path = journal_dir / "Status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"Elite's live Status.json was not found in:\n{journal_dir}") from exc
    except (json.JSONDecodeError, PermissionError, OSError) as exc:
        raise ValueError(f"Cannot read Elite's live Status.json: {exc}") from exc
    if not isinstance(status, dict):
        raise ValueError("Elite's live Status.json is not a JSON object.")
    latitude, longitude = status.get("Latitude"), status.get("Longitude")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        raise ValueError("Elite is not currently publishing surface latitude and longitude.")
    body = str(status.get("BodyName", ""))
    result = _named_location_from_journal(journal_dir, body)
    result.update({"latitude": latitude, "longitude": longitude})
    for source, target in (("BodyName", "body"), ("Altitude", "altitude"), ("Heading", "heading"), ("PlanetRadius", "body_radius_m")):
        if status.get(source) not in (None, ""):
            result[target] = status[source]
    captured_at = status.get("timestamp")
    if not isinstance(captured_at, str) or not captured_at:
        captured_at = datetime.fromtimestamp(status_path.stat().st_mtime, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
    result["captured_at"] = captured_at
    result["snapshot_at"] = captured_at
    result["source_path"] = str(status_path)
    return result


def read_live_location(path: Path | None = None) -> dict[str, Any]:
    if path is not None:
        return _read_world_snapshot(path)
    return read_elite_status_location()
