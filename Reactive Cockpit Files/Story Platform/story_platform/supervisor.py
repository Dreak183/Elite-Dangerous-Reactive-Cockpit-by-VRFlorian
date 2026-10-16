from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from . import VERSION
from .config import APP_DIR, load_config, resolve_path, validate_external_paths
from .graph_manager import GRAPH_SWITCH_EXIT_CODE, GraphManager, GraphSelectionError
from .instance_lock import AlreadyRunningError, InstanceLock


def start(command: list[str], cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(command, cwd=cwd)


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        process.kill()


def story_mode_state_path(config: dict) -> Path | None:
    """Return the optional player-launcher state file controlling story services."""
    settings = config.get("player_story_mode")
    if not isinstance(settings, dict):
        return None
    value = str(settings.get("state_file", "")).strip()
    return resolve_path(value) if value else None


def story_mode_enabled(config: dict) -> bool:
    """Read the launcher's durable toggle without making dev builds mode-aware.

    A normal author/developer installation has no ``player_story_mode`` block,
    so its graph stack remains enabled exactly as before. Public player builds
    opt in by pointing at the launcher's state file.
    """
    path = story_mode_state_path(config)
    if path is None:
        return True
    settings = config.get("player_story_mode", {})
    default = bool(settings.get("default_enabled", False))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default
    return bool(value.get("story_mode_enabled", default)) if isinstance(value, dict) else default


def run(story_only: bool = False) -> int:
    try:
        with InstanceLock("supervisor", APP_DIR):
            return _run_locked(story_only)
    except AlreadyRunningError:
        print("[SUPERVISOR] This Story Platform installation is already running.", flush=True)
        print("Use the existing platform window; a second copy was not started.", flush=True)
        return 3


def _run_locked(story_only: bool = False) -> int:
    config = load_config()
    mode_path = story_mode_state_path(config)
    mode_controlled = mode_path is not None
    story_enabled = story_mode_enabled(config)
    rc = config["reactive_cockpit"]
    report = validate_external_paths(config)
    journal_ok = any(line.startswith("Elite Journal: OK") for line in report.lines)
    if not journal_ok:
        print("Elite Journal folder is not available. Run CONFIGURE_EXTERNAL_FILES.bat.")
        return 2
    core: subprocess.Popen | None = None
    graph: subprocess.Popen | None = None
    presentation: subprocess.Popen | None = None
    controls: subprocess.Popen | None = None
    status_window: subprocess.Popen | None = None
    visual_theme: subprocess.Popen | None = None
    install: Path | None = None
    manager = GraphManager()
    restarts = {"graph": 0, "presentation": 0, "controls": 0, "status_window": 0, "visual_theme": 0}
    switch_times: list[float] = []
    if not story_only and rc.get("enabled"):
        if not report.ok:
            print("One or more configured external paths are unavailable:")
            for line in report.lines:
                print("  " + line)
            print("Run CONFIGURE_EXTERNAL_FILES.bat to refresh the links.")
            return 2
        if not rc.get("install_dir") or not rc.get("theme_cache_dir"):
            print("Reactive Cockpit external paths are not configured.")
            print("Run CONFIGURE_EXTERNAL_FILES.bat first, or use START_STORY_PLATFORM_ONLY.bat.")
            return 2
        install = resolve_path(rc["install_dir"])
        core_script = install / rc.get("core_script", "reactive_cockpit.py")
        if not core_script.is_file():
            print(f"Reactive Cockpit core not found: {core_script}")
            return 2

    def start_story_services() -> bool:
        nonlocal graph, presentation, controls, status_window, visual_theme
        try:
            selected_graph = manager.resolve_startup(config)
        except GraphSelectionError as exc:
            print(f"[GRAPH MANAGER] Cannot select a graph: {exc}", flush=True)
            return False
        print(f"[STORY MODE] ON — selected graph: {selected_graph}", flush=True)
        if config.get("presentation", {}).get("enabled", True):
            presentation = start([sys.executable, "-m", "story_platform.presentation_adapter"], APP_DIR)
            time.sleep(0.20)
            if config.get("presentation", {}).get("status_window", True):
                status_window = start([sys.executable, "-m", "story_platform.status_window"], APP_DIR)
        graph = start([sys.executable, "-m", "story_platform.runner"], APP_DIR)
        time.sleep(0.35)
        if config.get("controls", {}).get("enabled", True):
            controls = start([sys.executable, "-m", "story_platform.control_adapter"], APP_DIR)
        if install is not None and config.get("presentation", {}).get("visual_theme_adapter", True):
            visual_theme = start([sys.executable, "-m", "story_platform.visual_theme_adapter"], APP_DIR)
        for key in restarts:
            restarts[key] = 0
        return True

    def stop_story_services() -> None:
        nonlocal graph, presentation, controls, status_window, visual_theme
        # Stop the graph first so its final audio-gate closure reaches the
        # presentation adapter before that adapter is stopped.
        stop(graph)
        graph = None
        stop(visual_theme)
        visual_theme = None
        stop(controls)
        controls = None
        stop(status_window)
        status_window = None
        stop(presentation)
        presentation = None
        print("[STORY MODE] OFF — mission progress preserved; Reactive Cockpit remains active.", flush=True)

    print("=" * 78, flush=True)
    print(f"VRFlorian & Guardian Arthur - Reactive Cockpit / Story Platform {VERSION}", flush=True)
    print("Graph, controls, presentation and proven cockpit controller run as isolated siblings.", flush=True)
    print(f"Story Mode: {'ON' if story_enabled else 'OFF'}", flush=True)
    print("=" * 78, flush=True)
    if story_enabled and not start_story_services():
        if not mode_controlled:
            return 2
    if install is not None:
        core = start([sys.executable, str(install / rc.get("core_script", "reactive_cockpit.py")), "run"], install)
    try:
        while True:
            if core is not None and core.poll() is not None:
                print(f"[SUPERVISOR] Reactive Cockpit stopped (code {core.returncode}).", flush=True)
                return int(core.returncode or 0)
            if mode_controlled:
                requested = story_mode_enabled(config)
                if requested != story_enabled:
                    story_enabled = requested
                    if requested:
                        start_story_services()
                    else:
                        stop_story_services()
            if graph is not None and graph.poll() is not None:
                code = int(graph.returncode or 0)
                if code == GRAPH_SWITCH_EXIT_CODE:
                    now = time.monotonic()
                    switch_times = [item for item in switch_times if now - item < 60.0]
                    limit = max(1, int(config.get("graph_manager", {}).get("maximum_switches_per_minute", 8)))
                    if len(switch_times) >= limit:
                        print("[SUPERVISOR] Graph switching stopped: too many switches within one minute.", flush=True)
                        graph = None
                        if core is None:
                            return 2
                        continue
                    switch_times.append(now)
                    print("[SUPERVISOR] Graph switch committed; restarting graph-facing services.", flush=True)
                    stop(visual_theme)
                    visual_theme = None
                    stop(presentation)
                    presentation = None
                    if config.get("presentation", {}).get("enabled", True):
                        presentation = start([sys.executable, "-m", "story_platform.presentation_adapter"], APP_DIR)
                        time.sleep(0.20)
                    graph = start([sys.executable, "-m", "story_platform.runner"], APP_DIR)
                    time.sleep(0.35)
                    if install is not None and config.get("presentation", {}).get("visual_theme_adapter", True):
                        visual_theme = start([sys.executable, "-m", "story_platform.visual_theme_adapter"], APP_DIR)
                    restarts["graph"] = 0
                    restarts["presentation"] = 0
                    restarts["visual_theme"] = 0
                    continue
                if code and restarts["graph"] < 3:
                    restarts["graph"] += 1
                    print(f"[SUPERVISOR] Graph runtime exited with code {code}; cockpit is unaffected. Restarting ({restarts['graph']}/3).", flush=True)
                    time.sleep(1)
                    graph = start([sys.executable, "-m", "story_platform.runner"], APP_DIR)
                else:
                    print(f"[SUPERVISOR] Graph runtime stopped (code {code}).", flush=True)
                    if core is None:
                        return code
                    graph = None
            if presentation is not None and presentation.poll() is not None:
                code = int(presentation.returncode or 0)
                if code and restarts["presentation"] < 3:
                    restarts["presentation"] += 1
                    print(f"[SUPERVISOR] Presentation adapter failed ({code}); restarting ({restarts['presentation']}/3).", flush=True)
                    presentation = start([sys.executable, "-m", "story_platform.presentation_adapter"], APP_DIR)
                else:
                    presentation = None
            if controls is not None and controls.poll() is not None:
                code = int(controls.returncode or 0)
                if code and restarts["controls"] < 3:
                    restarts["controls"] += 1
                    print(f"[SUPERVISOR] Control adapter failed ({code}); restarting ({restarts['controls']}/3).", flush=True)
                    controls = start([sys.executable, "-m", "story_platform.control_adapter"], APP_DIR)
                else:
                    controls = None
            if visual_theme is not None and visual_theme.poll() is not None:
                code = int(visual_theme.returncode or 0)
                if code and restarts["visual_theme"] < 3:
                    restarts["visual_theme"] += 1
                    visual_theme = start([sys.executable, "-m", "story_platform.visual_theme_adapter"], APP_DIR)
                else:
                    visual_theme = None
            if status_window is not None and status_window.poll() is not None:
                code = status_window.returncode
                if code and restarts["status_window"] < 3:
                    restarts["status_window"] += 1
                    status_window = start([sys.executable, "-m", "story_platform.status_window"], APP_DIR)
                else:
                    status_window = None
            if graph is None and core is None:
                return 0
            time.sleep(0.35)
    except KeyboardInterrupt:
        return 0
    finally:
        stop(status_window)
        stop(visual_theme)
        stop(controls)
        stop(presentation)
        stop(graph)
        stop(core)
