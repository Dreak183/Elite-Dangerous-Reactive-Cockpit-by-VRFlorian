#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from story_platform import VERSION
from story_platform.config import APP_DIR, load_config, resolve_path, story_media_dir, validate_external_paths
from story_platform.control_adapter import emit_control
from story_platform.graph_manager import GraphManager, GraphSelectionError
from story_platform.model import GraphError, load_graph, validate_graph
from story_platform.runner import main as run_engine
from story_platform.setup_paths import configure, configure_media
from story_platform.supervisor import run as run_supervisor


def referenced_audio_assets(value: object) -> set[str]:
    """Collect every authored media reference from a graph-shaped JSON value."""
    keys = {"asset", "ping_asset", "acquisition_asset", "completion_asset", "on_asset", "off_asset"}
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in keys and isinstance(child, str) and child.strip():
                    found.add(child.strip())
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return found


def validate(path: Path | None) -> int:
    config = load_config()
    path = path or resolve_path(config["active_graph"])
    try:
        graph = load_graph(path)
    except GraphError as exc:
        print(exc)
        return 1
    print(f"VALID: {graph.graph_id} {graph.version} — {len(graph.nodes)} nodes / {len(graph.edges)} edges")
    return 0


def paths() -> int:
    report = validate_external_paths(load_config())
    for line in report.lines:
        print(line)
    return 0 if report.ok else 1


def media_check(path: Path | None) -> int:
    config = load_config()
    graph = load_graph(path or resolve_path(config["active_graph"]))
    root = story_media_dir(config)
    if root is None:
        print("Story-media folder is not configured. Configure the authored story-media folder first.")
        return 2
    assets = referenced_audio_assets(graph.data)
    missing = [asset for asset in sorted(assets) if not (root / asset).is_file()]
    print(f"Graph references {len(assets)} audio asset(s) beneath {root}")
    if missing:
        print(f"MISSING {len(missing)}:")
        for asset in missing: print("  " + asset)
        return 1
    print("All referenced audio assets are present.")
    return 0


def tests() -> int:
    return subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=APP_DIR)


def list_graphs() -> int:
    config = load_config()
    active = resolve_path(str(config["active_graph"]))
    try:
        catalog = GraphManager().catalog()
    except GraphSelectionError as exc:
        print(f"Cannot build graph catalog: {exc}")
        return 1
    for graph_id in sorted(catalog):
        for entry in catalog[graph_id]:
            marker = "*" if entry.path == active else " "
            try:
                path = entry.path.relative_to(APP_DIR)
            except ValueError:
                path = entry.path
            print(f"{marker} {entry.graph_id} {entry.version} — {entry.title} — {path}")
    return 0


def select_graph(graph_id: str, version: str | None) -> int:
    config = load_config()
    manager = GraphManager()
    try:
        manager.resolve_startup(config)
        changed = manager.request_switch(
            config, graph_id, version=version, reason="selected from platform command",
        )
    except GraphSelectionError as exc:
        print(f"Graph was not selected: {exc}")
        return 1
    target = manager.switch_target or manager.resolve_graph(graph_id, version)
    suffix = "Selected for the next launch" if changed else "Already selected"
    print(f"{suffix}: {target.graph_id} {target.version} — {target.path}")
    return 0


def reset_active_graph() -> int:
    graph = load_graph(resolve_path(load_config()["active_graph"]))
    state_paths = [
        APP_DIR / "runtime" / f"state_{graph.graph_id}.json",
        APP_DIR / "runtime" / f"environment_state_{graph.graph_id}.json",
        APP_DIR / "runtime" / f"navigation_state_{graph.graph_id}.json",
        APP_DIR / "runtime" / f"time_gate_state_{graph.graph_id}.json",
        APP_DIR / "runtime" / f"scan_state_{graph.graph_id}.json",
        APP_DIR / "runtime" / f"developer_session_{graph.graph_id}.json",
    ]
    existing = [path for path in state_paths if path.exists()]
    if not existing:
        print(f"No saved state exists for {graph.graph_id}.")
        return 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: list[str] = []
    for state_path in existing:
        backup = state_path.with_name(state_path.stem + f"_backup_{stamp}.json")
        state_path.replace(backup)
        backups.append(backup.name)
    print(f"Active graph reset. Previous state preserved as {', '.join(backups)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Reactive Cockpit Story Platform {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="run graph platform and the external Reactive Cockpit core")
    run_parser.add_argument("--story-only", action="store_true")
    sub.add_parser("engine", help="run only the graph engine in the current process")
    config_parser = sub.add_parser("configure", help="link the existing Reactive Cockpit/theme paths")
    config_parser.add_argument("folder", nargs="?")
    media_parser = sub.add_parser("configure-media", help="link the external story audio folder")
    media_parser.add_argument("folder", nargs="?")
    validate_parser = sub.add_parser("validate", help="validate a story graph")
    validate_parser.add_argument("graph", nargs="?")
    sub.add_parser("paths", help="check all configured external paths")
    media_check_parser = sub.add_parser("media-check", help="check every active-graph audio asset")
    media_check_parser.add_argument("graph", nargs="?")
    sub.add_parser("selftest", help="run the platform regression tests")
    sub.add_parser("graph-list", help="list installed graph ids and versions")
    select_parser = sub.add_parser("select-graph", help="select an installed graph for the next launch")
    select_parser.add_argument("graph_id")
    select_parser.add_argument("--version")
    inject_parser = sub.add_parser("inject", help="inject a generic control for testing")
    inject_parser.add_argument("action", choices=["answer", "accept", "decline", "paste", "scanner"])
    sub.add_parser("reset", help="reset active graph state while preserving a timestamped backup")
    editor_parser = sub.add_parser("editor", help="open the visual story graph editor")
    editor_parser.add_argument("graph", nargs="?", help="graph JSON to open; defaults to the active graph")
    args = parser.parse_args()
    if args.command == "run":
        return run_supervisor(story_only=args.story_only)
    if args.command == "engine":
        return run_engine()
    if args.command == "configure":
        return configure(Path(args.folder) if args.folder else None)
    if args.command == "configure-media":
        return configure_media(Path(args.folder) if args.folder else None)
    if args.command == "validate":
        return validate(Path(args.graph) if args.graph else None)
    if args.command == "paths":
        return paths()
    if args.command == "media-check":
        return media_check(Path(args.graph) if args.graph else None)
    if args.command == "selftest":
        return tests()
    if args.command == "graph-list":
        return list_graphs()
    if args.command == "select-graph":
        return select_graph(args.graph_id, args.version)
    if args.command == "inject":
        row = emit_control(args.action)
        print(f"Injected {row['name']}")
        return 0
    if args.command == "reset":
        return reset_active_graph()
    if args.command == "editor":
        from story_platform.graph_editor import main as run_editor

        return run_editor(Path(args.graph) if args.graph else None)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
