#!/usr/bin/env python3
"""GUI launcher backend for VRFlorian Reactive Cockpit.

This file intentionally sits *on top of* reactive_cockpit.py instead of
reimplementing the runtime. It provides non-interactive setup/install actions
for the Windows launcher while leaving the proven controller intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import reactive_cockpit as rc

LAUNCHER_VERSION = "0.8-OPEN-TEST"

STORY_PLATFORM_DIR = Path(__file__).resolve().parent / "Story Platform"
if str(STORY_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(STORY_PLATFORM_DIR))

from story_platform.config import (  # noqa: E402
    APP_DIR as STORY_APP_DIR,
    load_config as load_story_config,
    resolve_path as resolve_story_path,
    save_config as save_story_config,
)
from story_platform.graph_manager import GraphManager, GraphSelectionError  # noqa: E402
from story_platform.instance_lock import AlreadyRunningError, InstanceLock  # noqa: E402
from story_platform.model import GraphError, load_graph  # noqa: E402


STORY_SLOTS = (
    {
        "story_id": "living_world",
        "graph_id": "yggdrasil",
        "label": "LIVING WORLD",
        "resettable": False,
    },
    {
        "story_id": "mission_1",
        "graph_id": "guardian_signal_mission_01",
        "label": "MISSION 1",
        "resettable": True,
    },
    {
        "story_id": "mission_2",
        "graph_id": "Test mission 2",
        "label": "MISSION 2",
        "resettable": True,
    },
)

STORY_STATE_PREFIXES = (
    "state",
    "environment_state",
    "navigation_state",
    "time_gate_state",
    "scan_state",
    "developer_session",
)

# Journal short names -> friendly names. Unknown/new ships fall back to a clean
# title-cased version of the journal key, so a future Frontier ship still shows.
SHIP_NAMES = {
    "adder": "Adder", "anaconda": "Anaconda", "asp": "Asp Explorer",
    "asp_scout": "Asp Scout", "belugaliner": "Beluga Liner",
    "cobramkiii": "Cobra Mk III", "cobra_mk_iii": "Cobra Mk III",
    "cobramkiv": "Cobra Mk IV", "cobra_mk_iv": "Cobra Mk IV",
    "corsair": "Corsair", "cutter": "Imperial Cutter",
    "diamondback": "Diamondback Scout", "diamondbackxl": "Diamondback Explorer",
    "dolphin": "Dolphin", "eagle": "Eagle Mk II", "empire_courier": "Imperial Courier",
    "empire_eagle": "Imperial Eagle", "empire_trader": "Imperial Clipper",
    "explorer_nx": "Caspian Explorer", "federation_corvette": "Federal Corvette",
    "federation_dropship": "Federal Dropship", "federation_dropship_mkii": "Federal Assault Ship",
    "federation_gunship": "Federal Gunship", "ferdelance": "Fer-de-Lance",
    "hauler": "Hauler", "independant_trader": "Keelback", "keelback": "Keelback",
    "kestrel": "Kestrel Mk 2", "krait_light": "Krait Phantom", "krait_mkii": "Krait Mk II",
    "lakonminer": "Type 11 Prospector", "mamba": "Mamba", "orca": "Orca",
    "python": "Python", "python_nx": "Python Mk II", "sidewinder": "Sidewinder",
    "type6": "Type-6 Transporter", "type7": "Type-7 Transporter",
    "type9": "Type-9 Heavy", "typex": "Alliance Chieftain", "typex_2": "Alliance Crusader",
    "typex_3": "Alliance Challenger", "viper": "Viper Mk III", "viper_mkiv": "Viper Mk IV",
    "vulture": "Vulture",
}


def norm(v: Any) -> str:
    return str(v or "").strip().casefold()


def friendly_kind(kind: str) -> str:
    key = norm(kind)
    if key in SHIP_NAMES:
        return SHIP_NAMES[key]
    return str(kind or "Unknown Ship").replace("_", " ").replace("$", "").strip().title()


def journal_dir() -> Path:
    return rc.DEFAULT_JOURNAL


def shipyard_path() -> Path:
    return rc.DEFAULT_SHIPYARD


def ship_token(ship: dict) -> str:
    sig = "|".join([
        norm(ship.get("kind_short")),
        norm(ship.get("name")),
        norm(ship.get("plate")),
    ])
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]


def scan_journals() -> tuple[str, list[dict]]:
    folder = journal_dir()
    if not folder.exists():
        return "", []

    player = ""
    found: dict[tuple[str, str, str], dict] = {}
    files = sorted(folder.glob("Journal*.log"), key=lambda p: p.stat().st_mtime)
    for path in files:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"event"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if e.get("event") == "Commander" and e.get("Name"):
                        player = str(e["Name"])
                    elif e.get("event") == "Loadout" and e.get("Ship"):
                        kind = str(e.get("Ship") or "").strip()
                        name = str(e.get("ShipName") or "").strip()
                        plate = str(e.get("ShipIdent") or "").strip()
                        key = (norm(kind), norm(name), norm(plate))
                        found[key] = {
                            # EDHM's current Shipyard source treats ship_id as metadata;
                            # matching is kind/name/plate. If the native model-id is not
                            # available, the Elite instance ID is a stable opaque value.
                            "ship_id": e.get("ShipID"),
                            "kind_short": kind.lower(),
                            "kind_full": friendly_kind(kind),
                            "name": name,
                            "plate": plate,
                            "theme": "Current Settings",
                            "image": f"{kind.lower()}.jpg",
                        }
        except OSError:
            continue
    return player, list(found.values())


def backup_file(path: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    rc.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = rc.BACKUPS_DIR / f"{path.stem}.{label}_{stamp}{path.suffix}"
    shutil.copyfile(path, dst)
    return dst


def bootstrap_shipyard() -> dict:
    """Enable EDHM Shipyard without inventing EDHM model IDs.

    Existing Shipyard entries/themes are preserved exactly. If Shipyard has not
    been enabled yet, the launcher enables it and seeds only the Commander name.
    EDHM itself remains responsible for creating ship entries when a Loadout is
    observed, because EDHM's ship_id is its own model identifier and must never
    be guessed from Elite's Journal ShipID. A backup is made before every write.
    """
    sp = shipyard_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    player, journal_ships = scan_journals()

    if sp.exists():
        data = rc.load_json(sp, {})
        if not isinstance(data, dict):
            data = {}
    else:
        data = {}
    ships = data.get("ships")
    if not isinstance(ships, list):
        ships = []
    data["ships"] = ships

    changed = False
    if data.get("enabled") is not True:
        data["enabled"] = True
        changed = True
    if player and not data.get("player_name"):
        data["player_name"] = player
        changed = True
    data.setdefault("player_name", player or "")

    backup = None
    if changed:
        backup = backup_file(sp, "before_launcher_shipyard_bootstrap")
        tmp = sp.with_suffix(sp.suffix + ".reactive_tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, sp)

    cfg = rc.load_config()
    cfg["shipyard_file"] = str(sp)
    if journal_dir().exists():
        cfg["journal_dir"] = str(journal_dir())
    rc.save_config(cfg)

    return {
        "shipyard": str(sp), "enabled": True, "ships": len(ships),
        "journal_ships_seen": len(journal_ships),
        "backup": str(backup) if backup else None,
    }


def current_ships() -> list[dict]:
    bootstrap_shipyard()
    data = rc.load_shipyard(shipyard_path())
    out = []
    for s in data.get("ships", []):
        if not isinstance(s, dict):
            continue
        item = dict(s)
        item["token"] = ship_token(s)
        selector = rc.ship_selector_from_entry(s)
        name = str(selector.get("name") or "(unnamed)")
        kind = str(selector.get("kind_full") or selector.get("kind_short") or "?")
        plate = str(selector.get("plate") or "-")
        # ASCII separator is intentional: it survives Windows console/JSON/WPF
        # code-page boundaries cleanly and is easier to scan in the dropdown.
        item["label"] = f"{name} - {kind} - {plate}"
        out.append(item)
    out.sort(key=lambda x: (norm(x.get("name")), norm(x.get("kind_full"))))
    return out


def install_package(package_id: str, token: str) -> dict:
    if package_id not in rc.COCKPIT_PACKAGES:
        raise RuntimeError(f"Unknown Reactive Cockpit package: {package_id}")

    bootstrap_shipyard()
    sp = shipyard_path()
    shipyard = rc.load_shipyard(sp)
    selected = next((s for s in shipyard.get("ships", []) if ship_token(s) == token), None)
    if selected is None:
        raise RuntimeError("The selected ship is no longer present in EDHM Shipyard. Re-open the cockpit page and select it again.")

    package = rc.COCKPIT_PACKAGES[package_id]
    cfg = rc.load_config()
    cfg["shipyard_file"] = str(sp)
    if journal_dir().exists():
        cfg["journal_dir"] = str(journal_dir())

    selector = rc.ship_selector_from_entry(selected)
    assignment, added = rc.upsert_assignment(cfg, package_id, selector)
    rc.save_config(cfg)

    install_themes = list(package.get("library_themes") or rc.package_authored_themes(package))
    for theme in rc.AUXILIARY_THEME_NAMES:
        if theme not in install_themes:
            install_themes.append(theme)

    themes_path, library_installed, library_preserved = rc.install_bundled_edhm_themes(sp.parent, install_themes)
    cache_installed, cache_preserved = rc.merge_bundled_cache(preserve_existing=True)

    # Launcher v0.1 follows the existing installer's recommended/default choice:
    # designer Base is assigned to the chosen ship. Shipyard is backed up first.
    base_backup, base_changed = rc.backup_and_assign_base_to_ship(sp, selector, package["base_theme"])

    missing = [t for t in rc.package_authored_themes(package) if rc.find_cached_theme(t) is None]
    missing += [t for t in rc.configured_auxiliary_themes(cfg) if rc.find_cached_theme(t) is None]
    if missing:
        raise RuntimeError("Bundled runtime cache is incomplete for: " + ", ".join(missing))

    return {
        "ok": True,
        "package": package["display_name"],
        "ship": f"{selector.get('name') or '(unnamed)'} - {selector.get('kind_full') or selector.get('kind_short') or '?'} - {selector.get('plate') or '-'}",
        "assignment_added": added,
        "themes_path": str(themes_path),
        "library_installed": len(library_installed),
        "library_preserved": len(library_preserved),
        "cache_restored": len(cache_installed),
        "cache_preserved": len(cache_preserved),
        "base_theme": package["base_theme"],
        "shipyard_backup": str(base_backup),
        "base_changed": bool(base_changed),
    }


def story_slot(story_id: str) -> dict:
    selected = next((item for item in STORY_SLOTS if item["story_id"] == story_id), None)
    if selected is None:
        raise RuntimeError(f"Unknown story selection: {story_id}")
    return selected


def active_story_graph_id(config: dict | None = None) -> str:
    config = config or load_story_config()
    value = str(config.get("active_graph", "")).strip()
    if not value:
        return ""
    try:
        return load_graph(resolve_story_path(value, relative_to=STORY_APP_DIR)).graph_id
    except (GraphError, OSError, ValueError):
        return ""


def story_status() -> dict:
    config = load_story_config()
    active_graph_id = active_story_graph_id(config)
    manager = GraphManager()
    try:
        catalog = manager.catalog()
    except GraphSelectionError as exc:
        raise RuntimeError(str(exc)) from exc
    runtime_dir = STORY_APP_DIR / "runtime"
    stories = []
    active_story_id = "living_world"
    for item in STORY_SLOTS:
        installed = bool(catalog.get(item["graph_id"]))
        active = installed and item["graph_id"] == active_graph_id
        if active:
            active_story_id = item["story_id"]
        has_progress = any(
            (runtime_dir / f"{prefix}_{item['graph_id']}.json").is_file()
            for prefix in STORY_STATE_PREFIXES
        )
        stories.append({
            **item,
            "installed": installed,
            "active": active,
            "has_progress": has_progress,
        })
    return {
        "ok": True,
        "active_story_id": active_story_id,
        "active_graph_id": active_graph_id,
        "stories": stories,
        "locked": [{"story_id": "mission_3", "label": "MISSION 3"}],
    }


def select_story(story_id: str) -> dict:
    slot = story_slot(story_id)
    try:
        with InstanceLock("supervisor", STORY_APP_DIR):
            config = load_story_config()
            manager = GraphManager()
            entry = manager.resolve_graph(slot["graph_id"])
            selected = entry.state_value(STORY_APP_DIR)
            config["active_graph"] = selected["path"]
            save_story_config(config)
            manager.resolve_startup(config)
    except AlreadyRunningError as exc:
        raise RuntimeError(
            "Reactive Cockpit is currently running. Close the active player session before changing missions."
        ) from exc
    except GraphSelectionError as exc:
        raise RuntimeError(str(exc)) from exc
    return {
        "ok": True,
        "active_story_id": slot["story_id"],
        "active_graph_id": entry.graph_id,
        "title": entry.title,
        "version": entry.version,
    }


def reset_story(story_id: str) -> dict:
    slot = story_slot(story_id)
    if not slot["resettable"]:
        raise RuntimeError("Living World has no mission progress to reset.")
    try:
        with InstanceLock("supervisor", STORY_APP_DIR):
            manager = GraphManager()
            entry = manager.resolve_graph(slot["graph_id"])
            runtime_dir = STORY_APP_DIR / "runtime"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backups = []
            for prefix in STORY_STATE_PREFIXES:
                source = runtime_dir / f"{prefix}_{entry.graph_id}.json"
                if not source.is_file():
                    continue
                backup = source.with_name(source.name + f".reset_{timestamp}.bak")
                os.replace(source, backup)
                backups.append(str(backup))
    except AlreadyRunningError as exc:
        raise RuntimeError(
            "Reactive Cockpit is currently running. Close the active player session before resetting a mission."
        ) from exc
    except GraphSelectionError as exc:
        raise RuntimeError(str(exc)) from exc
    return {
        "ok": True,
        "story_id": slot["story_id"],
        "graph_id": entry.graph_id,
        "backups": backups,
        "reset": True,
    }


def status() -> dict:
    boot = bootstrap_shipyard()
    cfg = rc.load_config()
    return {
        "launcher_version": LAUNCHER_VERSION,
        "core_version": rc.VERSION,
        "shipyard": boot,
        "assignments": len(cfg.get("cockpit_assignments") or []),
        "ships": len(current_ships()),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Reactive Cockpit visual launcher backend")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap")
    sub.add_parser("status")
    sub.add_parser("list-ships")
    sub.add_parser("story-status")
    select = sub.add_parser("select-story")
    select.add_argument("--story-id", required=True)
    reset = sub.add_parser("reset-story")
    reset.add_argument("--story-id", required=True)
    ip = sub.add_parser("install")
    ip.add_argument("--package", required=True)
    ip.add_argument("--ship-token", required=True)
    args = p.parse_args()

    try:
        if args.cmd == "bootstrap":
            result = bootstrap_shipyard()
        elif args.cmd == "status":
            result = status()
        elif args.cmd == "list-ships":
            result = current_ships()
        elif args.cmd == "story-status":
            result = story_status()
        elif args.cmd == "select-story":
            result = select_story(args.story_id)
        elif args.cmd == "reset-story":
            result = reset_story(args.story_id)
        elif args.cmd == "install":
            result = install_package(args.package, args.ship_token)
        else:
            raise RuntimeError("Unknown command")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
