from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .config import DEFAULT_CONTROL_BINDINGS


@dataclass(frozen=True)
class SignalDefinition:
    name: str
    category: str
    description: str
    data: dict[str, Any]


_JOURNAL_SIGNALS = (
    ("Navigation", "Location", "Current system, body or station location"),
    ("Navigation", "FSDJump", "Arrived in another star system"),
    ("Navigation", "CarrierJump", "Fleet Carrier completed a jump"),
    ("Navigation", "StartJump", "Hyperspace or supercruise jump started"),
    ("Navigation", "SupercruiseEntry", "Entered supercruise"),
    ("Navigation", "SupercruiseExit", "Left supercruise"),
    ("Navigation", "SupercruiseDestinationDrop", "Dropped at the selected navigation destination"),
    ("Navigation", "ApproachBody", "Approached a landable body"),
    ("Navigation", "LeaveBody", "Departed a body's vicinity"),
    ("Navigation", "ApproachSettlement", "Approached a surface settlement"),
    ("Navigation", "Docked", "Docking completed"),
    ("Navigation", "Undocked", "Departed a dock"),
    ("Navigation", "Touchdown", "Ship touched down on a surface"),
    ("Navigation", "Liftoff", "Ship lifted off from a surface"),
    ("Vehicle", "Disembark", "Commander left ship or SRV"),
    ("Vehicle", "Embark", "Commander entered ship or SRV"),
    ("Vehicle", "LaunchSRV", "SRV deployed from ship"),
    ("Vehicle", "DockSRV", "SRV boarded the ship"),
    ("Vehicle", "VehicleSwitch", "Changed active vehicle"),
    ("Vehicle", "LaunchFighter", "Ship-launched fighter deployed"),
    ("Vehicle", "DockFighter", "Returned from ship-launched fighter"),
    ("Scans", "DataScanned", "Data-link scanner completed a scan"),
    ("Scans", "Scan", "Astronomical body scan recorded"),
    ("Scans", "ScanOrganic", "Exobiology sample scanned"),
    ("Scans", "SAAScanComplete", "Detailed surface scan completed"),
    ("Scans", "FSSDiscoveryScan", "Discovery scanner honk completed"),
    ("Inventory", "CollectItems", "On-foot item collected"),
    ("Inventory", "CollectCargo", "Cargo collected or scooped"),
    ("Inventory", "MaterialCollected", "Ship/SRV material collected"),
    ("Inventory", "BackpackChange", "Backpack contents changed"),
    ("Inventory", "ShipLocker", "Ship locker contents reported"),
    ("Inventory", "BuyMicroResources", "On-foot material purchased"),
    ("Inventory", "UpgradeWeapon", "On-foot weapon upgraded"),
    ("Inventory", "UpgradeSuit", "Suit upgraded"),
    ("Combat", "UnderAttack", "Ship came under attack"),
    ("Combat", "HullDamage", "Hull damage was recorded"),
    ("Combat", "ShieldState", "Shield state changed"),
    ("Combat", "Died", "Commander died"),
    ("Combat", "Bounty", "Bounty claim awarded"),
    ("Combat", "CommitCrime", "Crime was committed"),
    ("Combat", "Interdicted", "Interdiction completed"),
    ("Combat", "EscapeInterdiction", "Interdiction escaped"),
    ("Missions", "MissionAccepted", "Mission accepted"),
    ("Missions", "MissionCompleted", "Mission completed"),
    ("Missions", "MissionFailed", "Mission failed"),
    ("Mining", "ProspectedAsteroid", "Prospector result received"),
    ("Mining", "AsteroidCracked", "Seismic-charge asteroid cracked"),
    ("Social", "ReceiveText", "Text transmission received"),
    ("Social", "SendText", "Text transmission sent"),
)


def signal_catalog(control_bindings: dict[str, Any] | None = None) -> list[SignalDefinition]:
    result = [
        SignalDefinition(f"journal.{name}", category, description, {"event": name})
        for category, name, description in _JOURNAL_SIGNALS
    ]
    result.extend([
        SignalDefinition("status.snapshot", "Status", "Fresh Status.json snapshot", {"status": {}, "changed": {}}),
        SignalDefinition("status.changed", "Status", "Any Status.json field changed", {"status": {}, "changed": {}}),
        SignalDefinition("status.Flags.changed", "Status", "Primary ship/status flags changed", {"path": "Flags"}),
        SignalDefinition("status.Flags2.changed", "Status", "Odyssey/on-foot flags changed", {"path": "Flags2"}),
        SignalDefinition("status.glide.entered", "Navigation", "Ship entered orbital glide", {"active": True}),
        SignalDefinition("status.glide.exited", "Navigation", "Ship left orbital glide", {"active": False}),
        SignalDefinition("status.GuiFocus.changed", "Status", "Focused game interface changed", {"path": "GuiFocus"}),
        SignalDefinition("status.SelectedWeapon.changed", "Status", "Selected on-foot weapon changed", {"path": "SelectedWeapon"}),
        SignalDefinition("status.LegalState.changed", "Status", "Legal state changed", {"path": "LegalState"}),
        SignalDefinition("status.Health.changed", "Status", "On-foot health changed", {"path": "Health"}),
        SignalDefinition("status.Oxygen.changed", "Status", "On-foot oxygen changed", {"path": "Oxygen"}),
        SignalDefinition("system.tick", "System", "One-second runtime evaluation tick", {}),
    ])
    controls = {**DEFAULT_CONTROL_BINDINGS, **dict(control_bindings or {})}
    for action, key in controls.items():
        result.append(SignalDefinition(
            f"control.{action}", "Controls", f"Configured {key} control", {"action": action, "key": key}
        ))
    return sorted(result, key=lambda item: (item.category.casefold(), item.name.casefold()))


def build_location_predicate(fields: dict[str, Any]) -> dict[str, Any]:
    predicates: list[dict[str, Any]] = []
    for key in ("system", "body", "station", "settlement"):
        value = str(fields.get(key, "")).strip()
        if value:
            predicates.append({"path": f"world.location.{key}", "op": "ieq", "value": value})

    latitude = fields.get("latitude")
    longitude = fields.get("longitude")
    if latitude not in (None, "") or longitude not in (None, ""):
        if latitude in (None, "") or longitude in (None, ""):
            raise ValueError("Both latitude and longitude are required for a surface geofence")
        latitude = float(latitude)
        longitude = float(longitude)
        if not -90 <= latitude <= 90:
            raise ValueError("Latitude must be between -90 and 90 degrees")
        if not -180 <= longitude <= 180:
            raise ValueError("Longitude must be between -180 and 180 degrees")
        radius_m = float(fields.get("radius_m") or 0)
        if radius_m <= 0:
            raise ValueError("Geofence radius must be greater than zero metres")
        body_radius_km = fields.get("body_radius_km")
        geofence: dict[str, Any] = {
            "latitude": latitude, "longitude": longitude, "radius_m": radius_m,
        }
        if body_radius_km not in (None, ""):
            body_radius_m = float(body_radius_km) * 1000.0
            if body_radius_m <= 0:
                raise ValueError("Body radius must be greater than zero kilometres")
            geofence["body_radius_m"] = body_radius_m
        min_altitude = fields.get("min_altitude_m")
        max_altitude = fields.get("max_altitude_m")
        if min_altitude not in (None, ""):
            geofence["min_altitude_m"] = float(min_altitude)
        if max_altitude not in (None, ""):
            geofence["max_altitude_m"] = float(max_altitude)
        if "min_altitude_m" in geofence and "max_altitude_m" in geofence:
            if geofence["min_altitude_m"] > geofence["max_altitude_m"]:
                raise ValueError("Minimum altitude cannot be greater than maximum altitude")
        predicates.append({"geofence": geofence})

    if not predicates:
        raise ValueError("Enter at least one Elite location field")
    return predicates[0] if len(predicates) == 1 else {"all": predicates}


def location_fields_from_predicate(predicate: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}

    def visit(item: Any) -> None:
        if not isinstance(item, dict):
            return
        path = str(item.get("path", ""))
        if path.startswith("world.location.") and item.get("op", "eq") in {"eq", "ieq"}:
            key = path.removeprefix("world.location.")
            if key in {"system", "body", "station", "settlement"}:
                fields[key] = item.get("value", "")
        if isinstance(item.get("geofence"), dict):
            fence = item["geofence"]
            for key in ("latitude", "longitude", "radius_m", "min_altitude_m", "max_altitude_m"):
                if key in fence:
                    fields[key] = fence[key]
            if "body_radius_m" in fence:
                fields["body_radius_km"] = float(fence["body_radius_m"]) / 1000.0
        for key in ("all", "any"):
            if isinstance(item.get(key), list):
                for child in item[key]:
                    visit(child)

    visit(predicate)
    return fields


def _set_nested(root: dict[str, Any], path: str, value: Any) -> None:
    current = root
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = copy.deepcopy(value)


def simulator_preset_for(predicate: dict[str, Any]) -> dict[str, Any]:
    preset: dict[str, Any] = {
        "signal_name": "simulator.trigger_test", "signal_data": {}, "world": {},
        "advance_seconds": 0.0, "notes": [],
    }

    def visit(item: Any) -> None:
        if not isinstance(item, dict):
            return
        if "signal" in item:
            preset["signal_name"] = str(item["signal"])
        elif "elapsed_seconds" in item:
            preset["advance_seconds"] = max(float(preset["advance_seconds"]), float(item["elapsed_seconds"]))
        elif "path" in item and str(item["path"]).startswith("world.") and item.get("op", "eq") in {"eq", "ieq"}:
            _set_nested(preset["world"], str(item["path"])[6:], item.get("value"))
        elif "path" in item and str(item["path"]).startswith("signal.data.") and item.get("op", "eq") in {"eq", "ieq"}:
            _set_nested(preset["signal_data"], str(item["path"])[12:], item.get("value"))
        elif "geofence" in item and isinstance(item["geofence"], dict):
            fence = item["geofence"]
            location = preset["world"].setdefault("location", {})
            location["latitude"] = fence.get("latitude")
            location["longitude"] = fence.get("longitude")
            if fence.get("body_radius_m") is not None:
                location["body_radius_m"] = fence["body_radius_m"]
            if fence.get("min_altitude_m") is not None:
                location["altitude"] = fence["min_altitude_m"]
            elif fence.get("max_altitude_m") is not None:
                location["altitude"] = fence["max_altitude_m"]
        elif "all" in item:
            for child in item["all"]:
                visit(child)
        elif "any" in item and item["any"]:
            visit(item["any"][0])
            preset["notes"].append("Preset uses the first branch of an ANY condition")
        elif "variable" in item:
            preset["notes"].append(f"Set variable {item['variable']} manually if this condition needs it")
        elif "not" in item:
            preset["notes"].append("NOT conditions may need manual simulator adjustment")

    visit(predicate)
    return preset


def predicate_summary(predicate: Any) -> str:
    if not isinstance(predicate, dict):
        return "Invalid condition"
    if predicate == {"always": True}:
        return "Immediately"
    if "signal" in predicate:
        name = str(predicate["signal"])
        return f"When {name.removeprefix('journal.').removeprefix('control.')} occurs"
    if "elapsed_seconds" in predicate:
        return f"After {predicate['elapsed_seconds']} seconds"
    if "variable" in predicate:
        return f"When variable {predicate['variable']} {predicate.get('op', 'eq')} {predicate.get('value')!r}"
    if "path" in predicate:
        path = str(predicate["path"]).removeprefix("world.location.").removeprefix("world.")
        return f"When {path} {predicate.get('op', 'eq')} {predicate.get('value')!r}"
    if "geofence" in predicate and isinstance(predicate["geofence"], dict):
        fence = predicate["geofence"]
        return (
            f"Within {fence.get('radius_m')} m of "
            f"{float(fence.get('latitude', 0)):.6f}, {float(fence.get('longitude', 0)):.6f}"
        )
    if "all" in predicate:
        return "All: " + "; ".join(predicate_summary(item) for item in predicate["all"])
    if "any" in predicate:
        return "Any: " + "; ".join(predicate_summary(item) for item in predicate["any"])
    if "not" in predicate:
        return "Not: " + predicate_summary(predicate["not"])
    return "Custom condition"


def effect_summary(effect: Any) -> str:
    if not isinstance(effect, dict):
        return "Invalid effect"
    kind = effect.get("type")
    if kind == "next_step":
        return f"Objective: {effect.get('text', '')}"
    if kind == "log":
        return f"Log: {effect.get('message', '')}"
    if kind == "set":
        return f"Set {effect.get('variable')} = {effect.get('value')!r}"
    if kind == "increment":
        return f"Increase {effect.get('variable')} by {effect.get('amount', 1)}"
    if kind == "emit":
        channel = effect.get("channel")
        payload = effect.get("payload") if isinstance(effect.get("payload"), dict) else {}
        if channel == "audio":
            if payload.get("action") == "set_layer_volume":
                return f"Audio layer {payload.get('layer', 'dialogue')}: volume {payload.get('volume', 1.0):g}"
            if payload.get("action") == "clear_layer_volume_override":
                return f"Return audio layer to authored volume: {payload.get('layer', 'dialogue')}"
            if payload.get("action") == "stop_layer":
                return f"Stop audio layer: {payload.get('layer', 'dialogue')}"
            if payload.get("asset"):
                layer = payload.get("layer", "dialogue")
                return f"Audio [{layer}]: {payload['asset']} · volume {payload.get('volume', 1.0):g}" + (" · loop" if payload.get("loop") else "")
            if payload.get("system_sound"):
                return f"System sound: {payload['system_sound']}"
            if payload.get("stop"):
                return "Stop audio"
        if channel == "visual":
            return f"Visual cue: {payload.get('cue', 'pulse')}"
        if channel == "clipboard":
            return f"Clipboard: {payload.get('text', '')}"
        return f"Emit {channel}: {payload}"
    return f"Unknown effect: {kind}"
