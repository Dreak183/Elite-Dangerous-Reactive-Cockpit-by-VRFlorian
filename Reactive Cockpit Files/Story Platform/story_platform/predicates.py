from __future__ import annotations

import math
from typing import Any


MISSING = object()


def geofence_distance_m(fence: dict[str, Any], location: dict[str, Any]) -> float | None:
    """Return great-circle surface distance, or None while location is unknown."""
    try:
        latitude = math.radians(float(location["latitude"]))
        longitude = math.radians(float(location["longitude"]))
        target_latitude = math.radians(float(fence["latitude"]))
        target_longitude = math.radians(float(fence["longitude"]))
        body_radius = float(fence.get("body_radius_m") or location["body_radius_m"])
    except (KeyError, TypeError, ValueError):
        return None
    delta_latitude = target_latitude - latitude
    delta_longitude = target_longitude - longitude
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude) * math.cos(target_latitude) * math.sin(delta_longitude / 2) ** 2
    )
    return body_radius * 2 * math.atan2(math.sqrt(haversine), math.sqrt(max(0.0, 1 - haversine)))


def get_path(root: Any, path: str, default: Any = MISSING) -> Any:
    current = root
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "exists":
        return (actual is not MISSING) is bool(expected)
    if actual is MISSING:
        return False
    if op == "eq":
        return actual == expected
    if op == "ieq":
        return isinstance(actual, str) and actual.casefold() == str(expected).casefold()
    if op == "ne":
        return actual != expected
    if op == "gt":
        return actual > expected
    if op == "gte":
        return actual >= expected
    if op == "lt":
        return actual < expected
    if op == "lte":
        return actual <= expected
    if op == "in":
        return actual in expected
    if op == "contains":
        return expected in actual
    if op == "icontains":
        return isinstance(actual, str) and str(expected).casefold() in actual.casefold()
    if op == "startswith":
        return isinstance(actual, str) and actual.startswith(str(expected))
    if op == "endswith":
        return isinstance(actual, str) and actual.endswith(str(expected))
    raise ValueError(f"Unsupported predicate operator: {op}")


def evaluate(predicate: dict[str, Any], context: dict[str, Any]) -> bool:
    if "always" in predicate:
        return bool(predicate["always"])
    if "all" in predicate:
        return all(evaluate(item, context) for item in predicate["all"])
    if "any" in predicate:
        return any(evaluate(item, context) for item in predicate["any"])
    if "not" in predicate:
        return not evaluate(predicate["not"], context)
    if "signal" in predicate:
        return context.get("signal", {}).get("name") == predicate["signal"]
    if "elapsed_seconds" in predicate:
        return float(context.get("node_elapsed_seconds", 0.0)) >= float(predicate["elapsed_seconds"])
    if "geofence" in predicate:
        fence = predicate["geofence"]
        location = context.get("world", {}).get("location", {})
        distance = geofence_distance_m(fence, location)
        if distance is None:
            return False
        if distance > float(fence["radius_m"]):
            return False
        altitude = location.get("altitude")
        # Elite supplies altitude in many ship/SRV snapshots but can omit it
        # entirely while the commander is on foot.  Authored altitude bounds
        # therefore refine a surface geofence when telemetry is available;
        # they must not make an otherwise valid horizontal location impossible.
        if altitude is not None:
            if fence.get("min_altitude_m") is not None and float(altitude) < float(fence["min_altitude_m"]):
                return False
            if fence.get("max_altitude_m") is not None and float(altitude) > float(fence["max_altitude_m"]):
                return False
        return True
    if "path" in predicate:
        return _compare(get_path(context, predicate["path"]), predicate.get("op", "eq"), predicate.get("value"))
    if "variable" in predicate:
        actual = context.get("variables", {}).get(predicate["variable"], MISSING)
        return _compare(actual, predicate.get("op", "eq"), predicate.get("value"))
    raise ValueError(f"Unknown predicate form: {predicate}")
