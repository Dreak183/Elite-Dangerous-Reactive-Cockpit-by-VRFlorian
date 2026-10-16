from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import APP_DIR, load_config, resolve_path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, PermissionError, OSError, ValueError, json.JSONDecodeError):
        return {}


def format_duration(value: Any) -> str:
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _cockpit_from_logs(install_dir: Path) -> dict[str, str]:
    try:
        paths = sorted(
            (install_dir / "logs").glob("ReactiveCockpit_*.log"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
    except OSError:
        return {"state": "WAITING", "theme": "Reactive Cockpit"}
    for path in paths[:3]:
        try:
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines[-5000:]):
            if "STATE ->" not in line:
                continue
            parts = line.split("STATE ->", 1)[1].split(" | ")
            if len(parts) >= 2:
                return {"state": parts[0].strip(), "theme": parts[1].strip()}
    return {"state": "WAITING", "theme": "Reactive Cockpit"}


def _scan_location(scan_state: dict[str, Any], set_id: str, prefix: str) -> dict[str, int | bool]:
    scan_set = scan_state.get("sets", {}).get(set_id, {})
    targets = scan_set.get("targets", {}) if isinstance(scan_set, dict) else {}
    selected = [value for key, value in targets.items() if str(key).startswith(prefix)]
    complete = sum(1 for value in selected if isinstance(value, dict) and value.get("phase") == "completed")
    total = len(selected)
    return {"complete": complete, "total": total, "remaining": max(0, total - complete), "done": bool(total and complete == total)}


def _scanner_state(environment_state: dict[str, Any]) -> str:
    controls = environment_state.get("control_states", {})
    if not isinstance(controls, dict) or "control.scanner" not in controls:
        return "OFF"
    return "ON" if bool(controls.get("control.scanner")) else "OFF"


def _with_graph_objectives(profile: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    """Let objectives edited in the visual graph override compatibility clues."""
    result = dict(profile)
    clues = dict(profile.get("node_clues", {})) if isinstance(profile.get("node_clues"), dict) else {}
    nodes = graph.get("nodes", {}) if isinstance(graph.get("nodes"), dict) else {}
    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        for effect in node.get("on_enter", []):
            if isinstance(effect, dict) and effect.get("type") == "next_step":
                text = str(effect.get("text", "")).strip()
                if text:
                    clues[str(node_id)] = text
                break
    result["node_clues"] = clues
    return result


def _survey_rows(profile: dict[str, Any], world: dict[str, Any], scan_state: dict[str, Any]) -> list[dict[str, Any]]:
    survey = profile.get("survey", {})
    if not isinstance(survey, dict):
        return []
    set_id = str(survey.get("scan_set", ""))
    time_data = world.get("time", {}) if isinstance(world.get("time"), dict) else {}
    gates = time_data.get("gates", {}) if isinstance(time_data.get("gates"), dict) else {}
    rows: list[dict[str, Any]] = []
    for location in survey.get("locations", []):
        if not isinstance(location, dict):
            continue
        gate = gates.get(str(location.get("gate", "")), {})
        gate = gate if isinstance(gate, dict) else {}
        opened = bool(gate.get("open"))
        remaining = gate.get("seconds_until_close") if opened else gate.get("seconds_until_open")
        scan = _scan_location(scan_state, set_id, str(location.get("target_prefix", "")))
        rows.append({
            "id": str(location.get("id", "")),
            "label": str(location.get("label", "LOCATION")),
            "window": "OPEN" if opened else "CLOSED",
            "change_label": "closes in" if opened else "opens in",
            "change_in": format_duration(remaining),
            **scan,
        })
    return rows


def _survey_visible(profile: dict[str, Any], graph_state: dict[str, Any]) -> bool:
    survey = profile.get("survey", {})
    if not isinstance(survey, dict):
        return False
    visits = graph_state.get("visits", {})
    visits = visits if isinstance(visits, dict) else {}
    after = str(survey.get("show_after_node", ""))
    hidden = str(survey.get("hide_after_node", ""))
    return bool(after and visits.get(after)) and not bool(hidden and visits.get(hidden))


def _dynamic_survey_clue(
    profile: dict[str, Any], graph_state: dict[str, Any], world: dict[str, Any], rows: list[dict[str, Any]], clue: str
) -> str:
    survey = profile.get("survey", {})
    if not isinstance(survey, dict):
        return clue
    active = {str(item) for item in graph_state.get("active_nodes", [])}
    if not active.intersection(str(item) for item in survey.get("choice_nodes", [])):
        return clue
    by_id = {str(row.get("id")): row for row in rows}
    mountain = by_id.get("mountain", {})
    canyon = by_id.get("canyon", {})
    mountain_remaining = int(mountain.get("remaining", 0))
    canyon_remaining = int(canyon.get("remaining", 0))
    if mountain_remaining and not canyon_remaining:
        noun = "scan" if mountain_remaining == 1 else "scans"
        return f"Complete the {mountain_remaining} remaining survey {noun} on Mount Zoor."
    if canyon_remaining and not mountain_remaining:
        noun = "scan" if canyon_remaining == 1 else "scans"
        return f"Complete the {canyon_remaining} remaining survey {noun} in Ayasai Canyon."
    windows = survey.get("window_clues", {})
    windows = windows if isinstance(windows, dict) else {}
    if mountain_remaining and mountain.get("window") == "OPEN":
        sites = world.get("time", {}).get("sites", {}) if isinstance(world.get("time"), dict) else {}
        site = sites.get("mount_zoor", {}) if isinstance(sites, dict) else {}
        try:
            early = float(site.get("local_hour", 24)) < 4.8
        except (TypeError, ValueError):
            early = False
        return str(windows.get("mountain_early" if early else "mountain_late", clue))
    if canyon_remaining and canyon.get("window") == "OPEN":
        return str(windows.get("canyon_open", clue))
    if mountain_remaining or canyon_remaining:
        return str(windows.get("both_closed", clue))
    return clue


def build_view(
    *,
    title: str,
    profile: dict[str, Any],
    graph_state: dict[str, Any],
    player_status: dict[str, Any],
    world: dict[str, Any],
    environment_state: dict[str, Any],
    scan_state: dict[str, Any],
    cockpit: dict[str, str],
) -> dict[str, Any]:
    active_nodes = [str(item) for item in graph_state.get("active_nodes", [])]
    clues = profile.get("node_clues", {}) if isinstance(profile.get("node_clues"), dict) else {}
    clue = next((str(clues[node]) for node in active_nodes if node in clues), "")
    if not clue and player_status.get("graph_id") == graph_state.get("graph_id"):
        clue = str(player_status.get("objective", "")).strip()
    if not clue:
        clue = str(profile.get("default_clue", "Follow the current mission audio instructions."))
    rows = _survey_rows(profile, world, scan_state) if _survey_visible(profile, graph_state) else []
    clue = _dynamic_survey_clue(profile, graph_state, world, rows, clue)
    return {
        "title": str(profile.get("title") or title),
        "clue": clue,
        "cockpit_state": str(cockpit.get("state", "WAITING")),
        "cockpit_theme": str(cockpit.get("theme", "Reactive Cockpit")),
        "scanner": _scanner_state(environment_state),
        "show_scanner": bool(rows),
        "survey": rows,
    }


class PlayerReaderData:
    def __init__(self, app_dir: Path = APP_DIR) -> None:
        self.app_dir = app_dir.resolve()
        self.runtime = self.app_dir / "runtime"

    def _selection(self) -> tuple[Path, dict[str, Any]]:
        manager = _read_json(self.runtime / "graph_manager_state.json")
        active = manager.get("active_graph", {}) if isinstance(manager.get("active_graph"), dict) else {}
        raw = str(active.get("path", "")).strip()
        if not raw:
            raw = str(load_config().get("active_graph", "stories/yggdrasil/graph.json"))
        path = resolve_path(raw, relative_to=self.app_dir)
        return path, _read_json(path)

    def read(self) -> dict[str, Any]:
        graph_path, graph = self._selection()
        graph_id = str(graph.get("id", ""))
        profile = _with_graph_objectives(_read_json(graph_path.with_name("player_reader.json")), graph)
        state = _read_json(self.runtime / f"state_{graph_id}.json")
        if not state:
            state = {"graph_id": graph_id, "active_nodes": [str(graph.get("start", "start"))], "visits": {}}
        rc = load_config().get("reactive_cockpit", {})
        install = resolve_path(str(rc.get("install_dir", "..")), relative_to=self.app_dir)
        view = build_view(
            title=str(graph.get("title", graph_id or "Living World")),
            profile=profile,
            graph_state=state,
            player_status=_read_json(self.runtime / "player_status.json"),
            world=_read_json(self.runtime / "world_snapshot.json"),
            environment_state=_read_json(self.runtime / f"environment_state_{graph_id}.json"),
            scan_state=_read_json(self.runtime / f"scan_state_{graph_id}.json"),
            cockpit=_cockpit_from_logs(install),
        )
        wind = _read_json(self.runtime / "wind_status.json")
        try:
            fresh = 0 <= time.time() - float(wind.get("updated_at", 0)) < 3
        except (ValueError, TypeError):
            fresh = False
        if fresh and wind.get("graph_id") == graph_id and wind.get("clue"):
            view["clue"] = str(wind["clue"])
        return view


__all__ = ["PlayerReaderData", "build_view", "format_duration"]
