#!/usr/bin/env python3
"""Release guard for a clean, first-time-player Reactive Cockpit package."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def main() -> int:
    errors: list[str] = []
    cockpit = read_json(ROOT / "reactive_cockpit_config.json")
    launcher = read_json(ROOT / "user_data" / "launcher_state.json")
    platform = read_json(ROOT / "Story Platform" / "platform_config.json")

    if cockpit.get("cockpit_assignments"):
        errors.append("reactive_cockpit_config.json contains personal cockpit assignments")
    for key in ("active_package_id", "reactive_ship"):
        if cockpit.get(key) is not None:
            errors.append(f"reactive_cockpit_config.json contains {key}")
    if cockpit.get("theme_names"):
        errors.append("reactive_cockpit_config.json contains legacy theme overrides")
    for key in ("edhm_ini", "shipyard_file", "journal_dir"):
        if str(cockpit.get(key, "")).strip():
            errors.append(f"reactive_cockpit_config.json contains machine path {key}")
    if float(cockpit.get("disco_interval_seconds", 0.0)) != 2.0:
        errors.append("Disco Mode must ship with a two-second palette interval")
    if (ROOT / "runtime" / "disco_mode.active").exists():
        errors.append("Disco Mode must not be active or persisted in a clean player build")

    if launcher.get("story_mode_enabled") is not False:
        errors.append("Story Mode must default to OFF")
    if launcher.get("selected_story_id") != "living_world":
        errors.append("Living World must be the default story selection")
    if platform.get("active_graph") != "stories/yggdrasil/graph.json":
        errors.append("Story Platform must default to the Living World graph")

    for runtime in (ROOT / "Story Platform" / "runtime", ROOT / "runtime"):
        if runtime.exists() and any(item.is_file() for item in runtime.rglob("*")):
            errors.append(f"Runtime state is present in {runtime.relative_to(ROOT)}")

    sys.path.insert(0, str(ROOT))
    import reactive_cockpit as rc

    expected_themes: set[str] = set(rc.AUXILIARY_THEME_NAMES)
    for package in rc.COCKPIT_PACKAGES.values():
        expected_themes.update(rc.package_authored_themes(package))
    library = ROOT / "bundled_edhm_themes"
    missing_library = sorted(theme for theme in expected_themes if not (library / theme).is_dir())
    missing_cache = sorted(theme for theme in expected_themes if rc.find_cached_theme(theme) is None)
    if missing_library:
        errors.append("Bundled EDHM themes missing: " + ", ".join(missing_library))
    if missing_cache:
        errors.append("Bundled runtime cache missing: " + ", ".join(missing_cache))

    disco_keys = tuple(key for keys in rc.DISCO_COLOR_KEYS.values() for key in keys)
    for theme in sorted(expected_themes):
        cached = rc.find_cached_theme(theme)
        advanced = (cached / "Advanced.ini") if cached is not None else Path()
        if not advanced.is_file():
            errors.append(f"Bundled runtime theme has no Advanced.ini: {theme}")
            continue
        text = advanced.read_text(encoding="utf-8-sig")
        invalid = [
            key for key in disco_keys
            if len(re.findall(rf"^\s*{re.escape(key)}\s*=", text, re.MULTILINE)) != 1
        ]
        if invalid:
            errors.append(f"Bundled theme cannot support Disco colours ({theme}): " + ", ".join(invalid))

    if errors:
        print("CLEAN PLAYER BUILD: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "CLEAN PLAYER BUILD: PASS — no player state or machine paths; "
        f"{len(expected_themes)} required themes are bundled and cached."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
