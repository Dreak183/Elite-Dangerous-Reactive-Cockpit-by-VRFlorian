from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage import atomic_write_json


APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "platform_config.json"

DEFAULT_CONTROL_BINDINGS: dict[str, str] = {
    "answer": "CTRL+C",
    "accept": "CTRL+A",
    "decline": "CTRL+N",
    "paste": "CTRL+V",
    "scanner": "CTRL+S",
}


DEFAULT_CONTROL_FEEDBACK: dict[str, dict[str, Any]] = {
    "scanner": {
        "on_asset": "scanner/Scanner activated.mp3",
        "off_asset": "scanner/Scanner deactivated.mp3",
        "volume": 1.0,
        "layer": "system.control.scanner.feedback",
    },
}


DEFAULT_CONFIG: dict[str, Any] = {
    "schema": "platform.config.v1",
    "active_graph": "stories/yggdrasil/graph.json",
    "graph_manager": {
        "default_graph_id": "yggdrasil",
        "maximum_switches_per_minute": 8,
    },
    "journal_dir": "",
    "poll_seconds": 0.20,
    "status_max_initial_age_seconds": 60.0,
    # A clean/player build must never inherit an authoring machine's absolute
    # story-media path.  The marker file shipped beside the player build is
    # also accepted by ``story_media_dir`` so an update can repair older
    # installations without overwriting their other machine-specific config.
    "player_build": False,
    "reactive_cockpit": {
        "enabled": True,
        "install_dir": "",
        "core_script": "reactive_cockpit.py",
        "theme_cache_dir": "",
        "bundled_theme_cache_dir": "",
        "edhm_theme_library_dir": "",
    },
    "external_story_media_dir": "",
    "controls": {
        "enabled": True,
        "poll_seconds": 0.03,
        "bindings": DEFAULT_CONTROL_BINDINGS,
        "feedback": DEFAULT_CONTROL_FEEDBACK,
    },
    "developer": {
        "enabled": False,
        "bindings": {
            "toggle": "CTRL+SHIFT+G", "back": "CTRL+4",
            "forward": "CTRL+5", "next_mission": "CTRL+M",
        },
    },
    "presentation": {
        "enabled": True,
        "status_window": True,
        "window_title": "Guardian Arthur - Story Platform",
        "always_on_top": True,
        "visual_theme_adapter": True,
        "audio_volume_transition_seconds": 2.0
    }
}


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base))
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        atomic_write_json(CONFIG_PATH, DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return _merge(DEFAULT_CONFIG, raw)


def save_config(config: dict[str, Any]) -> None:
    atomic_write_json(CONFIG_PATH, config)


def configured_control_bindings(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Return the standard authoring controls plus any user-defined actions."""
    source = config or load_config()
    raw = source.get("controls", {}).get("bindings", {})
    result = dict(DEFAULT_CONTROL_BINDINGS)
    if isinstance(raw, dict):
        result.update({str(name): str(binding) for name, binding in raw.items() if str(name).strip()})
    return result


def default_journal_dir() -> Path:
    configured = os.environ.get("ELITE_JOURNAL_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"


def resolve_path(value: str, *, relative_to: Path = APP_DIR) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return path if path.is_absolute() else (relative_to / path).resolve()


def story_media_dir(config: dict[str, Any] | None = None) -> Path | None:
    """Return the authored story-media root for this installation.

    Authoring builds may deliberately point at an external folder.  A clean
    player build, however, owns its media map and must not follow a stale
    absolute path copied from the author's machine.  The marker is kept
    separate from user configuration so the updater can enforce this policy
    without destroying journal/theme settings.
    """
    source = config or load_config()
    # The marker is shipped beside ``Story Platform`` in the player payload,
    # but accept the in-folder location too so older/manual installs remain
    # self-identifying after an update.
    markers = (APP_DIR / "PLAYER_BUILD.marker", APP_DIR.parent / "PLAYER_BUILD.marker")
    if bool(source.get("player_build")) or any(marker.is_file() for marker in markers):
        return (APP_DIR / "story_assets").resolve()
    value = str(source.get("external_story_media_dir", "")).strip()
    return resolve_path(value) if value else None


@dataclass(frozen=True)
class PathReport:
    ok: bool
    lines: list[str]


def validate_external_paths(config: dict[str, Any]) -> PathReport:
    rc = config["reactive_cockpit"]
    lines: list[str] = []
    ok = True
    if rc.get("enabled"):
        install = resolve_path(rc.get("install_dir", "")) if rc.get("install_dir") else None
        core = install / rc.get("core_script", "reactive_cockpit.py") if install else None
        checks = [
            ("Reactive Cockpit core", core, True),
            ("Theme cache", resolve_path(rc["theme_cache_dir"]) if rc.get("theme_cache_dir") else None, True),
            ("Bundled theme cache", resolve_path(rc["bundled_theme_cache_dir"]) if rc.get("bundled_theme_cache_dir") else None, False),
            ("EDHM theme library", resolve_path(rc["edhm_theme_library_dir"]) if rc.get("edhm_theme_library_dir") else None, False),
        ]
        for label, path, required in checks:
            exists = bool(path and path.exists())
            lines.append(f"{label}: {'OK' if exists else 'NOT SET/NOT FOUND'}" + (f" -> {path}" if path else ""))
            if required and not exists:
                ok = False
    journal_value = config.get("journal_dir", "")
    journal = resolve_path(journal_value) if journal_value else default_journal_dir()
    journal_ok = journal.is_dir()
    lines.append(f"Elite Journal: {'OK' if journal_ok else 'NOT FOUND'} -> {journal}")
    ok = ok and journal_ok
    media_path = story_media_dir(config)
    if media_path:
        lines.append(f"External story media: {'OK' if media_path.is_dir() else 'NOT FOUND'} -> {media_path}")
        ok = ok and media_path.is_dir()
    else:
        lines.append("External story media: optional / not configured")
    return PathReport(ok=ok, lines=lines)
