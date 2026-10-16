#!/usr/bin/env python3
"""
VRFlorian & Guardian Arthur's - Reactive Cockpit v1.10 DISCO TEST
============================

Local EDHM UI v3 companion for Elite Dangerous.

Base cockpit identity comes from EDHM Shipyard_v3.json.
Reactive Cockpit adds live operational / danger states from Elite's Journal + Status.json.

v0.4 state palette:
    BASE                    Shipyard-assigned theme
    COMBAT                  confirmed combat / wanted-entry
    SUPERCRUISE             supercruise + glide
    DEEP_DANGER             shield loss in danger, low fuel, overheating
    CRITICAL                hull <20%, canopy <=20% when known, canopy breach
    STEALTH                 silent running
    DOCKING                 landing/takeoff sequence
    SCOOP                   fuel scooping
    FIGHTER_BASE/COMBAT     shared Ship-Launched Fighter cockpit
    SRV_BASE                shared SRV cockpit
    DISCO                   temporary CTRL+SHIFT+D lighting override

No third-party Python modules are required.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

VERSION = "1.10.0-DISCO-TEST"

# Elite Status.json flags
FLAG_DOCKED = 1
FLAG_LANDED = 2
FLAG_LANDING_GEAR_DOWN = 4
FLAG_SHIELDS_UP = 8
FLAG_SUPERCRUISE = 16
FLAG_SILENT_RUNNING = 1024
FLAG_SCOOPING_FUEL = 2048
FLAG_LOW_FUEL_GAME = 524288          # Elite's own <25% flag; diagnostic only
FLAG_OVERHEATING = 1048576           # >100%
FLAG_IS_IN_DANGER = 4194304
FLAG_BEING_INTERDICTED = 8388608
FLAG_IN_MAIN_SHIP = 16777216
FLAG_IN_FIGHTER = 33554432
FLAG_IN_SRV = 67108864

# Elite Status.json Flags2
FLAG2_ON_FOOT = 1
FLAG2_IN_TAXI = 2
FLAG2_IN_MULTICREW = 4
FLAG2_ON_FOOT_IN_STATION = 8
FLAG2_ON_FOOT_ON_PLANET = 16
FLAG2_GLIDE_MODE = 4096
FLAG2_ON_FOOT_IN_HANGAR = 8192
FLAG2_ON_FOOT_SOCIAL_SPACE = 16384
FLAG2_ON_FOOT_EXTERIOR = 32768

CORE_FILES = [
    "Advanced.ini",
    "Startup-Profile.ini",
    "SuitHud.ini",
    "XML-Profile.ini",
    "Custom.ini",
]
SIGNAL_FILE = "ThemeSettings.json"

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / "theme_cache"
CACHE_INDEX = CACHE_DIR / "index.json"
CONFIG_FILE = APP_DIR / "reactive_cockpit_config.json"
_CONFIG_WRITES_ENABLED = True

# Tester-friendly rolling session logs live directly beside theme_cache.
LOGS_DIR = APP_DIR / "logs"
MAX_SESSION_LOGS = 5
ACTIVE_SESSION_LOG_PATH: Optional[Path] = None

BUNDLED_EDHM_THEMES_DIR = APP_DIR / "bundled_edhm_themes"
BUNDLED_MASTER_CACHE_DIR = APP_DIR / "bundled_theme_cache"
BACKUPS_DIR = APP_DIR / "backups"

DESIGNER_BASE_THEME = "VRFlo Reactive Cockpit - Base"

SLOT_LABELS = {
    "combat": "Combat",
    "deep_danger": "Deep Danger",
    "critical": "Imminent Destruction",
    "stealth": "Stealth",
    "docking": "Docking",
    "scoop": "Scoop",
    "supercruise": "Supercruise / Glide",
}

CORSAIR_THEME_NAMES = [
    "VRFlo Reactive Cockpit - Base",
    "VRFlo Reactive Cockpit - Combat",
    "VRFlo Reactive Cockpit - Deep danger",
    "VRFlo Reactive Cockpit - IMMINENT DESTRUCTION",
    "VRFlo Reactive Cockpit - Stealth",
    "VRFlo Reactive Cockpit - Docking mode",
    "VRFlo Reactive Cockpit - Scoop",
    "VRFlo Reactive Cockpit - Super-cruise",
]

TYPE11_THEME_NAMES = [
    "Type 11 - Reactive Cockpit Base",
    "Type 11 Reactive Cockpit - Combat mode",
    "Type 11 reactive cockpit - Deep Danger mode",
    "Type 11 Reactive Cockpit - Imminent destruction",
    "Type 11 Reactive Cockpit - Stealth",
    "Type 11 - Reactive Cockpit Docking",
    "Type 11 Reactive Cockpit - Scoop",
    "Type 11 - Reactive Cockpit super-cruise",
]

PYTHONMKII_THEME_NAMES = [
    "Python MKII - Reactive Cockpit Base",
    "Python MKII - Reactive Cockpit Combat",
    "Python MKII - Reactive Cockpit Deep danger",
    "Python MKII - Reactive Cockpit Imminent Destruction",
    "Python MKII - Reactive Cockpit Stealth",
    "Python MKII - Reactive Cockpit Docking",
    "Python MKII - Reactive Cockpit Scoop",
    "Python MKII - Reactive Cockpit Super cruise",
]

CASPIAN_THEME_NAMES = [
    "Caspian - Reactive Cockpit base",
    "Caspian - Reactive Cockpit Combat",
    "Caspian Reactive Cockpit Deep danger",
    "Caspian - Reactive Cockpit Imminent destruction",
    "Caspian - Reactive Cockpit Stealth",
    "Caspian - Reactive Cockpit Docking",
    "Caspian - Reactive Cockpit Scoop",
    "Caspian - Reactive Cockpit Super Cruise",
]

NEBULA_HOPPER_ANACONDA_THEME_NAMES = [
    "Nebula Hopper Anaconda - Reactive Cockpit Base",
    "Nebula Hopper Anaconda Reactive Cockpit - Combat mode",
    "Nebula Hopper Anaconda reactive cockpit - Deep Danger mode",
    "Nebula Hopper Anaconda Reactive Cockpit - Imminent destruction",
    "Nebula Hopper Anaconda Reactive Cockpit - Stealth",
    "Nebula Hopper Anaconda - Reactive Cockpit Docking",
    "Nebula Hopper Anaconda Reactive Cockpit - Scoop",
    "Nebula Hopper Anaconda - Reactive Cockpit super-cruise",
]

KESTREL_THEME_NAMES = [
    "Kestrel (heart eater) Reactive Cockpit - Base",
    "Kestrel (Heart eater) Reactive Cockpit - Combat mode",
    "Kestrel (Heart eater) Reactive Cockpit - Deep damage mode",
    "Kestrel (Heart eater) Reactive Cockpit - Imminent Destruction",
    "Kestrel (Heart eater) Reactive Cockpit - Stealth mode",
    "Kestrel (Heart eater) Reactive Cockpit - Docking mode",
    "Kestrel (Heart eater) Reactive Cockpit - Scoop mode",
    "Kestrel (Heart eater) Reactive Cockpit - Super Cruise mode",
]

CLASSIC_ELITE_THEME_NAMES = [
    "Classic Elite Reactive Cockpit Base",
    "Classic Reactive Cockpit - Combat mode",
    "Classic Reactive Cockpit - Deep Danger mode",
    "Classic Reactive Cockpit - Imminent Destruction",
    "Classic Reactive Cockpit - Stealth mode",
    "Classic Reactive Cockpit - Docking mode",
    "Classic Reactive Cockpit - Scoop mode",
    "Classic Reactive Cockpit - Super cruise mode",
]

FIGHTER_THEME_NAMES = [
    "Ship Launched Fighter - Reactive Cockpit Base",
    "Ship Launched Fighter - Reactive Cockpit Combat",
]

SRV_THEME_NAMES = [
    "SRV Reactive Cockpit Base",
]

AUXILIARY_THEME_NAMES = FIGHTER_THEME_NAMES + SRV_THEME_NAMES
PUBLIC_THEME_NAMES = CORSAIR_THEME_NAMES + TYPE11_THEME_NAMES + PYTHONMKII_THEME_NAMES + CASPIAN_THEME_NAMES + NEBULA_HOPPER_ANACONDA_THEME_NAMES + KESTREL_THEME_NAMES + CLASSIC_ELITE_THEME_NAMES + AUXILIARY_THEME_NAMES

DEFAULT_AUXILIARY_COCKPITS = {
    "fighter": {
        "base": "Ship Launched Fighter - Reactive Cockpit Base",
        "combat": "Ship Launched Fighter - Reactive Cockpit Combat",
    },
    "srv": {
        "base": "SRV Reactive Cockpit Base",
    },
}

AUXILIARY_SLOT_LABELS = {
    ("fighter", "base"): "Fighter Base",
    ("fighter", "combat"): "Fighter Combat",
    ("srv", "base"): "SRV Base",
}

# A cockpit package is a visual design with optional reactive state slots.
# Empty/None slot = that logical state contributes no visual override. The
# resolver continues to the next active authored state, with Shipyard Base as
# the final fallback. Packages are assigned to exact ships and
# several assignments can coexist at the same time.
COCKPIT_PACKAGES = {
    "battle_corsair_snow_vrflorian": {
        "display_name": "Battle Corsair Snow - by VRFlorian",
        "intended_ship_type": "Corsair",
        "base_theme": "VRFlo Reactive Cockpit - Base",
        "theme_names": {
            "combat": "VRFlo Reactive Cockpit - Combat",
            "deep_danger": "VRFlo Reactive Cockpit - Deep danger",
            "critical": "VRFlo Reactive Cockpit - IMMINENT DESTRUCTION",
            "stealth": "VRFlo Reactive Cockpit - Stealth",
            "docking": "VRFlo Reactive Cockpit - Docking mode",
            "scoop": "VRFlo Reactive Cockpit - Scoop",
            "supercruise": "VRFlo Reactive Cockpit - Super-cruise",
        },
        "slot_suggestions": {},
        "library_themes": list(CORSAIR_THEME_NAMES),
    },
    "type11_rocky5_vrflorian": {
        "display_name": "Type 11 Rocky 5 - by VRFlorian",
        "intended_ship_type": "Type 11 Prospector",
        "base_theme": "Type 11 - Reactive Cockpit Base",
        "theme_names": {
            "combat": "Type 11 Reactive Cockpit - Combat mode",
            "deep_danger": "Type 11 reactive cockpit - Deep Danger mode",
            "critical": "Type 11 Reactive Cockpit - Imminent destruction",
            "stealth": "Type 11 Reactive Cockpit - Stealth",
            "docking": "Type 11 - Reactive Cockpit Docking",
            "scoop": "Type 11 Reactive Cockpit - Scoop",
            "supercruise": "Type 11 - Reactive Cockpit super-cruise",
        },
        "slot_suggestions": {},
        "library_themes": list(TYPE11_THEME_NAMES),
    },
    "python_mkii_vrflorian": {
        "display_name": "Python MKII - by VRFlorian",
        "intended_ship_type": "Python Mk II",
        "base_theme": "Python MKII - Reactive Cockpit Base",
        "theme_names": {
            "combat": "Python MKII - Reactive Cockpit Combat",
            "deep_danger": "Python MKII - Reactive Cockpit Deep danger",
            "critical": "Python MKII - Reactive Cockpit Imminent Destruction",
            "stealth": "Python MKII - Reactive Cockpit Stealth",
            "docking": "Python MKII - Reactive Cockpit Docking",
            "scoop": "Python MKII - Reactive Cockpit Scoop",
            "supercruise": "Python MKII - Reactive Cockpit Super cruise",
        },
        "slot_suggestions": {},
        "library_themes": list(PYTHONMKII_THEME_NAMES),
    },
    "caspian_explorer_vrflorian": {
        "display_name": "Caspian Explorer - by VRFlorian",
        "intended_ship_type": "Caspian Explorer",
        "base_theme": "Caspian - Reactive Cockpit base",
        "theme_names": {
            "combat": "Caspian - Reactive Cockpit Combat",
            "deep_danger": "Caspian Reactive Cockpit Deep danger",
            "critical": "Caspian - Reactive Cockpit Imminent destruction",
            "stealth": "Caspian - Reactive Cockpit Stealth",
            "docking": "Caspian - Reactive Cockpit Docking",
            "scoop": "Caspian - Reactive Cockpit Scoop",
            "supercruise": "Caspian - Reactive Cockpit Super Cruise",
        },
        "slot_suggestions": {},
        "library_themes": list(CASPIAN_THEME_NAMES),
    },
    "nebula_hopper_anaconda_vrflorian": {
        "display_name": "Nebula Hopper Anaconda - by VRFlorian",
        "intended_ship_type": "Anaconda",
        "base_theme": "Nebula Hopper Anaconda - Reactive Cockpit Base",
        "theme_names": {
            "combat": "Nebula Hopper Anaconda Reactive Cockpit - Combat mode",
            "deep_danger": "Nebula Hopper Anaconda reactive cockpit - Deep Danger mode",
            "critical": "Nebula Hopper Anaconda Reactive Cockpit - Imminent destruction",
            "stealth": "Nebula Hopper Anaconda Reactive Cockpit - Stealth",
            "docking": "Nebula Hopper Anaconda - Reactive Cockpit Docking",
            "scoop": "Nebula Hopper Anaconda Reactive Cockpit - Scoop",
            "supercruise": "Nebula Hopper Anaconda - Reactive Cockpit super-cruise",
        },
        "slot_suggestions": {},
        "library_themes": list(NEBULA_HOPPER_ANACONDA_THEME_NAMES),
    },    "kestrel_heart_eater_vrflorian": {
        "display_name": "Kestrel - by VRFlorian",
        "intended_ship_type": "Kestrel Mk 2",
        "base_theme": "Kestrel (heart eater) Reactive Cockpit - Base",
        "theme_names": {
            "combat": "Kestrel (Heart eater) Reactive Cockpit - Combat mode",
            "deep_danger": "Kestrel (Heart eater) Reactive Cockpit - Deep damage mode",
            "critical": "Kestrel (Heart eater) Reactive Cockpit - Imminent Destruction",
            "stealth": "Kestrel (Heart eater) Reactive Cockpit - Stealth mode",
            "docking": "Kestrel (Heart eater) Reactive Cockpit - Docking mode",
            "scoop": "Kestrel (Heart eater) Reactive Cockpit - Scoop mode",
            "supercruise": "Kestrel (Heart eater) Reactive Cockpit - Super Cruise mode",
        },
        "slot_suggestions": {},
        "library_themes": list(KESTREL_THEME_NAMES),
    },
    "classic_elite_vrflorian": {
        "display_name": "Classic Elite Reactive Cockpit - by VRFlorian",
        "intended_ship_type": "Any ship",
        "base_theme": "Classic Elite Reactive Cockpit Base",
        "theme_names": {
            "combat": "Classic Reactive Cockpit - Combat mode",
            "deep_danger": "Classic Reactive Cockpit - Deep Danger mode",
            "critical": "Classic Reactive Cockpit - Imminent Destruction",
            "stealth": "Classic Reactive Cockpit - Stealth mode",
            "docking": "Classic Reactive Cockpit - Docking mode",
            "scoop": "Classic Reactive Cockpit - Scoop mode",
            "supercruise": "Classic Reactive Cockpit - Super cruise mode",
        },
        "slot_suggestions": {},
        "library_themes": list(CLASSIC_ELITE_THEME_NAMES),
    },
}
DEFAULT_PACKAGE_ID = "battle_corsair_snow_vrflorian"


DEFAULT_SHIPYARD = Path.home() / "EDHM_UI" / "Shipyard_v3.json"
DEFAULT_JOURNAL = (
    Path.home()
    / "Saved Games"
    / "Frontier Developments"
    / "Elite Dangerous"
)

DEFAULT_CONFIG = {
    "combat_cooldown_seconds": 15.0,
    "wanted_entry_seconds": 15.0,
    "glide_exit_cooldown_seconds": 3.0,
    "supercruise_exit_cooldown_seconds": 3.0,
    "docking_complete_cooldown_seconds": 3.0,
    "critical_hull_threshold": 0.20,
    "deep_danger_hull_threshold": 0.50,
    "critical_canopy_threshold": 0.20,
    "low_fuel_threshold": 0.20,
    "disco_interval_seconds": 2.0,
    "cockpit_assignments": [],
    "auxiliary_cockpits": json.loads(json.dumps(DEFAULT_AUXILIARY_COCKPITS)),

    # Legacy v1.1 fields are retained only for automatic migration.
    "active_package_id": None,
    "reactive_ship": None,
    "theme_names": {},
}


# Disco Mode deliberately changes only these three EDHM lighting colours. Each
# entry is a coordinated (ambient glow, primary cabin, secondary cabin) palette.
# Eight steps make a simple 16-second loop at the default two-second tempo.
DISCO_PALETTES = (
    ("NEON HORIZON", "FF2BD6", "00E5FF", "7B36FF"),
    ("BLUE MONDAY", "126BFF", "FF2A8A", "FFB000"),
    ("XENO LIME", "8CFF2E", "8A35FF", "00D9FF"),
    ("SOL FLARE", "FF253A", "FFD21A", "168BFF"),
    ("TURQUOISE DREAM", "00D8B4", "743CFF", "FF4DA6"),
    ("ORANGE PULSE", "FF7800", "00BFFF", "FF00AA"),
    ("BIOLOGICAL", "35F56F", "FF397D", "4A42FF"),
    ("CELEBRATION", "147DFF", "FF5A1F", "00FFD0"),
)
DISCO_HOTKEY = "CTRL+SHIFT+D"
DISCO_MARKER_FILE = APP_DIR / "runtime" / "disco_mode.active"
DISCO_COLOR_KEYS = {
    "ambient": ("x190", "y190", "z190"),
    "primary": ("x69", "y69", "z69"),
    "secondary": ("x70", "y70", "z70"),
}



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TeeStream:
    """Write to the normal console and a session log at the same time."""

    def __init__(self, console, log_file):
        self.console = console
        self.log_file = log_file

    @property
    def encoding(self):
        return getattr(self.console, "encoding", "utf-8")

    def write(self, text):
        # Keep the controller usable even if a log write ever fails.
        result = None
        try:
            result = self.console.write(text)
        finally:
            try:
                self.log_file.write(text)
            except Exception:
                pass
        return result if result is not None else len(text)

    def flush(self):
        try:
            self.console.flush()
        finally:
            try:
                self.log_file.flush()
            except Exception:
                pass

    def isatty(self):
        return bool(getattr(self.console, "isatty", lambda: False)())

    def fileno(self):
        return self.console.fileno()


class SessionLog:
    def __init__(self, path: Path, log_file):
        self.path = path
        self.log_file = log_file
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self.stdout_tee = TeeStream(self.original_stdout, log_file)
        self.stderr_tee = TeeStream(self.original_stderr, log_file)
        self.closed = False

        sys.stdout = self.stdout_tee
        sys.stderr = self.stderr_tee

    def close(self):
        if self.closed:
            return
        self.closed = True

        try:
            self.stdout_tee.flush()
            self.stderr_tee.flush()
        except Exception:
            pass

        if sys.stdout is self.stdout_tee:
            sys.stdout = self.original_stdout
        if sys.stderr is self.stderr_tee:
            sys.stderr = self.original_stderr

        try:
            self.log_file.close()
        except Exception:
            pass


def prune_session_logs(log_dir: Path, keep_previous: int = 4):
    """Keep only the newest prior Reactive Cockpit session logs."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logs = list(log_dir.glob("ReactiveCockpit_*.log"))

        def sort_key(path: Path):
            try:
                return (path.stat().st_mtime_ns, path.name)
            except OSError:
                return (0, path.name)

        logs.sort(key=sort_key)

        while len(logs) > max(0, int(keep_previous)):
            old = logs.pop(0)
            try:
                old.unlink()
            except OSError:
                # Logging must never stop gameplay because an old file is locked.
                pass
    except Exception:
        pass


def start_session_logging() -> Optional[SessionLog]:
    """Start one gameplay/controller session log and retain at most five."""
    global ACTIVE_SESSION_LOG_PATH

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # Before opening the new session, retain only four older sessions.
        # New session + four previous = five total.
        prune_session_logs(LOGS_DIR, MAX_SESSION_LOGS - 1)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = LOGS_DIR / f"ReactiveCockpit_{timestamp}.log"
        suffix = 2
        while path.exists():
            path = LOGS_DIR / f"ReactiveCockpit_{timestamp}_{suffix}.log"
            suffix += 1

        log_file = path.open("w", encoding="utf-8", buffering=1)
        session = SessionLog(path, log_file)
        ACTIVE_SESSION_LOG_PATH = path
        atexit.register(session.close)

        print("=" * 82)
        print("REACTIVE COCKPIT SESSION LOG")
        print("=" * 82)
        print(f"Started        : {datetime.now().astimezone().isoformat(timespec='seconds')}")
        print(f"Build          : {VERSION}")
        print(f"Python         : {sys.version.split()[0]}")
        print(f"Program folder : {APP_DIR}")
        print(f"Log file       : {path}")
        print(f"Log retention  : newest {MAX_SESSION_LOGS} gameplay sessions")
        print("=" * 82)
        print()

        return session

    except Exception as exc:
        ACTIVE_SESSION_LOG_PATH = None
        print(f"[{stamp()}] WARNING: session logging could not start: {exc}")
        print("Reactive Cockpit will continue normally without a log.")
        return None


def heading(text: str):
    print()
    print("=" * 82)
    print(f"  {text}")
    print("=" * 82)
    print()


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def normalized(value) -> str:
    return str(value or "").strip().casefold()


def safe_fraction(value) -> Optional[float]:
    """Accept 0..1 or 0..100 and normalize to 0..1."""
    try:
        v = float(value)
        if 1.0 < v <= 100.0:
            v /= 100.0
        return max(0.0, min(1.0, v))
    except (TypeError, ValueError):
        return None


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    saved = load_json(CONFIG_FILE, {})
    if isinstance(saved, dict):
        for key, value in saved.items():
            if key == "theme_names" and isinstance(value, dict):
                cfg["theme_names"].update(value)
            else:
                cfg[key] = value

    # Migrate the v1.1 single-package model once, in memory. The next save
    # writes the new additive assignments model.
    assignments = cfg.get("cockpit_assignments")
    if not isinstance(assignments, list):
        assignments = []
    if not assignments:
        legacy_id = cfg.get("active_package_id")
        legacy_ship = cfg.get("reactive_ship")
        package = COCKPIT_PACKAGES.get(str(legacy_id)) if legacy_id else None
        if package and isinstance(legacy_ship, dict):
            legacy_slots = dict(package.get("theme_names") or {})
            if isinstance(cfg.get("theme_names"), dict) and cfg["theme_names"]:
                legacy_slots.update(cfg["theme_names"])
            assignments.append({
                "package_id": str(legacy_id),
                "ship": legacy_ship,
                "theme_slots": legacy_slots,
            })
    cfg["cockpit_assignments"] = assignments

    # Global auxiliary cockpits are installed with every Reactive Cockpit design.
    # Preserve user replacements while automatically adding any newly introduced
    # default slots in future versions.
    saved_aux = cfg.get("auxiliary_cockpits")
    merged_aux = json.loads(json.dumps(DEFAULT_AUXILIARY_COCKPITS))
    if isinstance(saved_aux, dict):
        for vehicle, slots in saved_aux.items():
            if not isinstance(slots, dict):
                continue
            merged_aux.setdefault(vehicle, {})
            for slot, theme in slots.items():
                merged_aux[vehicle][slot] = str(theme).strip() if theme else None
    cfg["auxiliary_cockpits"] = merged_aux
    return cfg


def save_config(cfg: dict):
    if not _CONFIG_WRITES_ENABLED:
        return
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def atomic_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".reactive_cockpit_tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def disco_mode_active(marker_path: Optional[Path] = None) -> bool:
    """Shared, transient signal used by the isolated Story visual adapter."""
    return (marker_path or DISCO_MARKER_FILE).is_file()


def set_disco_marker(active: bool, marker_path: Optional[Path] = None):
    marker = marker_path or DISCO_MARKER_FILE
    if not active:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_name(marker.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(f"active {datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
        os.replace(tmp, marker)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _hex_rgb(value: str) -> tuple[float, float, float]:
    text = str(value).strip().lstrip("#")
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise ValueError(f"Invalid Disco colour: {value!r}")
    return tuple(int(text[index:index + 2], 16) / 255.0 for index in (0, 2, 4))


def _ini_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def apply_disco_palette(edhm_ini: Path, palette: tuple[str, str, str, str]):
    """Atomically alter only Ambient, Primary and Secondary cabin colours."""
    advanced = edhm_ini / "Advanced.ini"
    trigger = edhm_ini / SIGNAL_FILE
    if not advanced.is_file() or not trigger.is_file():
        raise RuntimeError("EDHM live Advanced.ini / ThemeSettings.json is unavailable.")

    _name, ambient, primary, secondary = palette
    values: dict[str, float] = {}
    for family, colour in (("ambient", ambient), ("primary", primary), ("secondary", secondary)):
        for key, number in zip(DISCO_COLOR_KEYS[family], _hex_rgb(colour)):
            values[key] = number

    text = advanced.read_text(encoding="utf-8-sig")
    for key, number in values.items():
        pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)[^\r\n]*$", re.MULTILINE)
        text, count = pattern.subn(rf"\g<1>{_ini_number(number)}", text)
        if count != 1:
            raise RuntimeError(f"EDHM Advanced.ini expected one {key} value; found {count}.")

    advanced_tmp = advanced.with_name(advanced.name + f".disco_{os.getpid()}.tmp")
    trigger_tmp = trigger.with_name(trigger.name + f".disco_{os.getpid()}.tmp")
    try:
        advanced_tmp.write_text(text, encoding="utf-8")
        os.replace(advanced_tmp, advanced)
        time.sleep(0.03)
        trigger_tmp.write_bytes(trigger.read_bytes())
        os.replace(trigger_tmp, trigger)
        try:
            os.utime(trigger, None)
        except OSError:
            pass
    finally:
        for temporary in (advanced_tmp, trigger_tmp):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class DiscoHotkey:
    """Edge-triggered global CTRL+SHIFT+D detector with no external modules."""

    def __init__(self):
        self.previous = False
        self.user32 = None
        if os.name == "nt":
            try:
                import ctypes
                self.user32 = ctypes.windll.user32
            except Exception:
                self.user32 = None

    def edge(self, down: bool) -> bool:
        pressed = bool(down and not self.previous)
        self.previous = bool(down)
        return pressed

    def poll(self) -> bool:
        if self.user32 is None:
            return False
        down = all(
            bool(self.user32.GetAsyncKeyState(code) & 0x8000)
            for code in (0x11, 0x10, ord("D"))
        )
        return self.edge(down)


# ---------------------------------------------------------------------------
# Locate EDHM / Shipyard / Elite Journal
# ---------------------------------------------------------------------------

def valid_edhm_ini(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / SIGNAL_FILE).exists()
        and any((path / f).exists() for f in CORE_FILES)
    )


def edhm_candidates(cfg: dict) -> list[Path]:
    home = Path.home()
    candidates = []

    if cfg.get("edhm_ini"):
        candidates.append(Path(cfg["edhm_ini"]))

    candidates.extend([
        home / "EDHM_UI" / "ODYSS" / "EDHM" / "EDHM-Ini",
        home / "EDHM_UI" / "ODYSS" / "EDHM" / "EDHM-ini",
        home / "EDHM_UI" / "HORIZ" / "EDHM" / "EDHM-Ini",
        home / "EDHM_UI" / "HORIZ" / "EDHM" / "EDHM-ini",
    ])

    root = home / "EDHM_UI"
    if root.exists():
        try:
            for signal in root.rglob(SIGNAL_FILE):
                if signal.parent.name.casefold() == "edhm-ini":
                    candidates.append(signal.parent)
        except Exception:
            pass

    out, seen = [], set()
    for p in candidates:
        key = str(p).casefold()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def choose_edhm_ini(cfg: dict) -> Path:
    valid = [p for p in edhm_candidates(cfg) if valid_edhm_ini(p)]

    if len(valid) == 1:
        cfg["edhm_ini"] = str(valid[0])
        save_config(cfg)
        return valid[0]

    if len(valid) > 1:
        print("More than one live EDHM-ini folder was found:")
        for i, p in enumerate(valid, 1):
            print(f"  {i}. {p}")
        while True:
            answer = input("Choose the Elite instance you use [1]: ").strip() or "1"
            try:
                chosen = valid[int(answer) - 1]
                cfg["edhm_ini"] = str(chosen)
                save_config(cfg)
                return chosen
            except (ValueError, IndexError):
                print("Please choose one of the listed numbers.")

    print("Could not automatically locate EDHM's live EDHM-ini folder.")
    while True:
        raw = input("Paste the full EDHM-ini path: ").strip().strip('"')
        p = Path(os.path.expandvars(raw)).expanduser()
        if valid_edhm_ini(p):
            cfg["edhm_ini"] = str(p)
            save_config(cfg)
            return p
        print(f"That folder must contain {SIGNAL_FILE} and EDHM live INI files.")


def valid_shipyard(path: Path) -> bool:
    data = load_json(path)
    return isinstance(data, dict) and isinstance(data.get("ships"), list)


def choose_shipyard(cfg: dict) -> Path:
    candidates = []
    if cfg.get("shipyard_file"):
        candidates.append(Path(cfg["shipyard_file"]))
    candidates.append(DEFAULT_SHIPYARD)

    for p in candidates:
        if valid_shipyard(p):
            cfg["shipyard_file"] = str(p)
            save_config(cfg)
            return p

    print("Could not automatically locate EDHM Shipyard_v3.json.")
    while True:
        raw = input("Paste the full Shipyard_v3.json path: ").strip().strip('"')
        p = Path(os.path.expandvars(raw)).expanduser()
        if valid_shipyard(p):
            cfg["shipyard_file"] = str(p)
            save_config(cfg)
            return p
        print("That file does not look like an EDHM Shipyard_v3.json.")


def valid_journal_dir(path: Path) -> bool:
    return path.is_dir() and (path / "Status.json").exists()


def choose_journal_dir(cfg: dict) -> Path:
    candidates = []
    if cfg.get("journal_dir"):
        candidates.append(Path(cfg["journal_dir"]))

    candidates.extend([
        DEFAULT_JOURNAL,
        Path.home() / "Saved Games" / "Elite Dangerous",
    ])

    for p in candidates:
        if valid_journal_dir(p):
            cfg["journal_dir"] = str(p)
            save_config(cfg)
            return p

    print("Could not automatically locate Elite's Journal folder.")
    while True:
        raw = input("Paste the folder containing Journal*.log and Status.json: ").strip().strip('"')
        p = Path(os.path.expandvars(raw)).expanduser()
        if valid_journal_dir(p):
            cfg["journal_dir"] = str(p)
            save_config(cfg)
            return p
        print("That folder does not contain Status.json.")


def load_shipyard(path: Path) -> dict:
    data = load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("ships"), list):
        raise RuntimeError(f"Invalid Shipyard file: {path}")
    return data


def assigned_base_themes(shipyard: dict) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for ship in shipyard.get("ships", []):
        theme = str(ship.get("theme") or "").strip()
        if not theme or normalized(theme) == normalized("Current Settings"):
            continue
        desc = (
            f"{ship.get('name') or '(unnamed)'}"
            f" [{ship.get('kind_full') or ship.get('kind_short') or '?'}"
            f" / {ship.get('plate') or '-'}]"
        )
        result.setdefault(theme, []).append(desc)
    return result


# ---------------------------------------------------------------------------
# Theme cache
# ---------------------------------------------------------------------------

def live_theme_name(edhm_ini: Path) -> str:
    data = load_json(edhm_ini / SIGNAL_FILE, {})
    if not isinstance(data, dict):
        return ""
    credits = data.get("credits") or {}
    if isinstance(credits, dict) and credits.get("theme"):
        return str(credits["theme"])
    for key in ("ThemeName", "theme"):
        if data.get(key):
            return str(data[key])
    return ""


def load_cache_index() -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = load_json(CACHE_INDEX, {"themes": {}})
    if not isinstance(data, dict):
        data = {"themes": {}}
    data.setdefault("themes", {})
    return data


def save_cache_index(index: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_INDEX.write_text(json.dumps(index, indent=2), encoding="utf-8")


def cache_folder_for(theme_name: str) -> Path:
    digest = hashlib.sha1(theme_name.encode("utf-8")).hexdigest()[:12]
    return CACHE_DIR / "themes" / digest


def capture_theme(theme_name: str, edhm_ini: Path, role: str):
    actual = live_theme_name(edhm_ini)
    if normalized(actual) != normalized(theme_name):
        raise RuntimeError(f"EDHM currently reports '{actual}', not '{theme_name}'.")

    dest = cache_folder_for(theme_name)
    dest.mkdir(parents=True, exist_ok=True)

    copied = []
    for filename in CORE_FILES + [SIGNAL_FILE]:
        src = edhm_ini / filename
        if src.exists():
            shutil.copyfile(src, dest / filename)
            copied.append(filename)

    if SIGNAL_FILE not in copied:
        raise RuntimeError(f"{SIGNAL_FILE} was not captured.")

    manifest = {
        "theme": theme_name,
        "role": role,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "files": copied,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    index = load_cache_index()
    index["themes"][theme_name] = {
        "folder": dest.name,
        "role": role,
        "captured_at": manifest["captured_at"],
        "files": copied,
    }
    save_cache_index(index)


def find_cached_theme(theme_name: str) -> Optional[Path]:
    index = load_cache_index()

    item = index["themes"].get(theme_name)
    if item:
        p = CACHE_DIR / "themes" / item["folder"]
        if (p / SIGNAL_FILE).exists():
            return p

    wanted = normalized(theme_name)
    for saved_name, item in index["themes"].items():
        if normalized(saved_name) == wanted:
            p = CACHE_DIR / "themes" / item["folder"]
            if (p / SIGNAL_FILE).exists():
                return p

    return None


def apply_cached_theme(theme_name: str, edhm_ini: Path):
    srcdir = find_cached_theme(theme_name)
    if srcdir is None:
        raise RuntimeError(f"Theme '{theme_name}' is not cached.")

    for filename in CORE_FILES:
        src = srcdir / filename
        if src.exists():
            atomic_copy(src, edhm_ini / filename)

    # Same proven ordering as v0.3.
    time.sleep(0.06)
    atomic_copy(srcdir / SIGNAL_FILE, edhm_ini / SIGNAL_FILE)

    try:
        os.utime(edhm_ini / SIGNAL_FILE, None)
    except Exception:
        pass


def wait_and_capture(theme_name: str, role: str, edhm_ini: Path, ships=None):
    print()
    print("-" * 82)
    print(f"NEXT THEME: {theme_name}")

    if ships:
        print("Used by:")
        for ship in ships:
            print(f"  - {ship}")

    print()
    print(f"In EDHM UI, APPLY:  {theme_name}")
    print("Reactive Cockpit will capture it automatically when EDHM reports that live theme.")
    print("Ctrl+C stops setup; already-captured themes remain saved.")
    print()

    last_seen = None

    while True:
        current = live_theme_name(edhm_ini)

        if current != last_seen and current:
            print(f"  Live EDHM theme: {current}")
            last_seen = current

        if normalized(current) == normalized(theme_name):
            time.sleep(0.20)
            capture_theme(theme_name, edhm_ini, role)
            print(f"  [CAPTURED] {theme_name}")
            return

        time.sleep(0.25)


# ---------------------------------------------------------------------------
# Ship state + Shipyard matching
# ---------------------------------------------------------------------------

def is_canopy_module(module: dict) -> bool:
    slot = normalized(module.get("Slot"))
    item = normalized(module.get("Item"))
    return (
        "cockpit" in slot
        or "canopy" in slot
        or "cockpit" in item
        or "canopy" in item
    )


@dataclass
class ShipState:
    ship_type: str = ""
    ship_id: Optional[int] = None
    ship_name: str = ""
    ship_ident: str = ""
    hull_health: Optional[float] = None
    canopy_health: Optional[float] = None
    fuel_capacity_main: Optional[float] = None
    has_shield_generator: Optional[bool] = None

    def friendly(self) -> str:
        return (
            f"{self.ship_name or '(unnamed)'} / {self.ship_type or '?'} / "
            f"{self.ship_ident or '-'} / Elite ID {self.ship_id}"
        )


def parse_fuel_capacity(value) -> Optional[float]:
    if isinstance(value, dict):
        try:
            return float(value.get("Main"))
        except (TypeError, ValueError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ship_from_loadout(event: dict) -> ShipState:
    modules = event.get("Modules")
    has_shields = None
    canopy_health = None

    if isinstance(modules, list):
        has_shields = any(
            "shieldgenerator" in normalized(m.get("Item"))
            for m in modules
            if isinstance(m, dict)
        )

        for m in modules:
            if isinstance(m, dict) and is_canopy_module(m):
                canopy_health = safe_fraction(m.get("Health"))
                break

    return ShipState(
        ship_type=str(event.get("Ship") or "").strip(),
        ship_id=event.get("ShipID"),
        ship_name=str(event.get("ShipName") or "").strip(),
        ship_ident=str(event.get("ShipIdent") or "").strip(),
        hull_health=safe_fraction(event.get("HullHealth")),
        canopy_health=canopy_health,
        fuel_capacity_main=parse_fuel_capacity(event.get("FuelCapacity")),
        has_shield_generator=has_shields,
    )


def match_shipyard_ship(state: ShipState, shipyard: dict) -> Optional[dict]:
    """Conservatively match Elite's current ship to one EDHM Shipyard entry.

    Whenever Elite supplies ship type, no fallback is allowed to cross that
    type boundary. This prevents a stale name/ID from selecting another class.
    """
    ships = shipyard.get("ships", [])
    t = normalized(state.ship_type)
    n = normalized(state.ship_name)
    p = normalized(state.ship_ident)

    exact = [s for s in ships
             if normalized(s.get("kind_short")) == t
             and normalized(s.get("name")) == n
             and normalized(s.get("plate")) == p]
    if len(exact) == 1: return exact[0]

    if t:
        by_kind_name=[s for s in ships if normalized(s.get("kind_short"))==t and n and normalized(s.get("name"))==n]
        if len(by_kind_name)==1: return by_kind_name[0]
        by_kind_plate=[s for s in ships if normalized(s.get("kind_short"))==t and p and normalized(s.get("plate"))==p]
        if len(by_kind_plate)==1: return by_kind_plate[0]
        by_kind=[s for s in ships if normalized(s.get("kind_short"))==t]
        if len(by_kind)==1: return by_kind[0]
        return None

    by_name=[s for s in ships if n and normalized(s.get("name"))==n]
    if len(by_name)==1: return by_name[0]
    return None


# ---------------------------------------------------------------------------
# Journal reading
# ---------------------------------------------------------------------------

def journal_files(journal_dir: Path) -> list[Path]:
    files = list(journal_dir.glob("Journal*.log"))
    return sorted(files, key=lambda p: p.stat().st_mtime)


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []


def reconstruct_current_ship(journal_dir: Path) -> ShipState:
    files = journal_files(journal_dir)
    if not files:
        return ShipState()

    load_file_i = None
    load_line_i = None
    load_event = None

    recent = files[-12:]
    base_i = len(files) - len(recent)

    for local_i in range(len(recent) - 1, -1, -1):
        lines = read_lines(recent[local_i])
        for line_i in range(len(lines) - 1, -1, -1):
            line = lines[line_i]
            if '"Loadout"' not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") == "Loadout":
                load_file_i = base_i + local_i
                load_line_i = line_i
                load_event = e
                break
        if load_event:
            break

    if not load_event:
        return ShipState()

    state = ship_from_loadout(load_event)

    for file_i in range(load_file_i, len(files)):
        lines = read_lines(files[file_i])
        start = load_line_i + 1 if file_i == load_file_i else 0

        for line in lines[start:]:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue

            ev = e.get("event")

            if ev == "Loadout":
                state = ship_from_loadout(e)

            elif ev == "HullDamage" and not e.get("Fighter", False):
                h = safe_fraction(e.get("Health"))
                if h is not None:
                    state.hull_health = h

            elif ev in ("RepairAll", "Resurrect"):
                state.hull_health = 1.0
                state.canopy_health = 1.0

            elif ev == "Repair":
                item = normalized(e.get("Item"))
                if item in ("all", "hull"):
                    state.hull_health = 1.0
                if item == "all" or "cockpit" in item or "canopy" in item:
                    state.canopy_health = 1.0

            elif ev == "AfmuRepairs":
                module = normalized(e.get("Module"))
                if "cockpit" in module or "canopy" in module:
                    h = safe_fraction(e.get("Health"))
                    if h is not None:
                        state.canopy_health = h

            elif ev == "CockpitBreached":
                state.canopy_health = 0.0

    return state


class JournalTail:
    def __init__(self, journal_dir: Path):
        self.journal_dir = journal_dir
        self.path: Optional[Path] = None
        self.offset = 0

    def prime(self):
        files = journal_files(self.journal_dir)
        self.path = files[-1] if files else None

        if self.path:
            try:
                self.offset = self.path.stat().st_size
            except OSError:
                self.offset = 0

    def events(self) -> list[dict]:
        files = journal_files(self.journal_dir)
        current = files[-1] if files else None

        if current is None:
            return []

        if self.path is None or current != self.path:
            self.path = current
            self.offset = 0

        out = []

        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(self.offset)

                for line in f:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

                self.offset = f.tell()

        except OSError:
            pass

        return out


def read_status(path: Path) -> Optional[dict]:
    for _ in range(4):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.025)
    return None


# ---------------------------------------------------------------------------
# Install / capture wizard
# ---------------------------------------------------------------------------


def folder_digest(path: Path) -> str:
    """Stable SHA256 over file names and contents."""
    h = hashlib.sha256()
    if not path.exists():
        return ""
    for p in sorted((x for x in path.rglob("*") if x.is_file()), key=lambda x: str(x.relative_to(path)).casefold()):
        rel = str(p.relative_to(path)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with p.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


def copytree_replace(src: Path, dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def merge_bundled_cache(preserve_existing: bool = True, theme_names=None):
    """
    Seed Reactive Cockpit's working theme_cache from the bundled master.
    Existing same-name snapshots are preserved by default so users don't lose
    personal recaptures when they rerun the installer.
    """
    master_index = load_json(BUNDLED_MASTER_CACHE_DIR / "index.json", {"themes": {}})
    if not isinstance(master_index, dict) or not isinstance(master_index.get("themes"), dict):
        raise RuntimeError("Bundled master cache is missing or invalid.")

    working = load_cache_index()
    installed = []
    preserved = []

    wanted = set(theme_names) if theme_names else None
    for theme, meta in master_index["themes"].items():
        if wanted is not None and theme not in wanted:
            continue
        if preserve_existing and find_cached_theme(theme) is not None:
            preserved.append(theme)
            continue

        src = BUNDLED_MASTER_CACHE_DIR / "themes" / meta["folder"]
        dst = CACHE_DIR / "themes" / meta["folder"]

        if not (src / SIGNAL_FILE).exists():
            raise RuntimeError(f"Bundled cache snapshot incomplete: {theme}")

        copytree_replace(src, dst)
        working["themes"][theme] = dict(meta)
        installed.append(theme)

    save_cache_index(working)
    return installed, preserved


def install_bundled_edhm_themes(edhm_root: Path, theme_names=None):
    """
    Install the eight authored theme folders into EDHM's user theme library.

    EDHM UI v3 itself stores Odyssey themes under:
        <UserDataFolder>/ODYSS/Themes/<ThemeName>
    We preserve an existing same-name theme so rerunning this installer never
    silently overwrites a user's later edits.
    """
    themes_dest = edhm_root / "ODYSS" / "Themes"
    themes_dest.mkdir(parents=True, exist_ok=True)

    installed = []
    preserved = []

    themes_to_install = list(theme_names) if theme_names else list(PUBLIC_THEME_NAMES)
    for theme in themes_to_install:
        src = BUNDLED_EDHM_THEMES_DIR / theme
        dst = themes_dest / theme

        if not (src / "ThemeSettings.json").exists():
            raise RuntimeError(f"Bundled EDHM theme missing: {theme}")

        if dst.exists():
            preserved.append(theme)
            continue

        shutil.copytree(src, dst)
        installed.append(theme)

    return themes_dest, installed, preserved


def ship_selector_from_entry(ship: dict) -> dict:
    return {
        "shipyard_id": ship.get("ship_id"),
        "kind_short": str(ship.get("kind_short") or "").strip(),
        "kind_full": str(ship.get("kind_full") or "").strip(),
        "name": str(ship.get("name") or "").strip(),
        "plate": str(ship.get("plate") or "").strip(),
    }


def ship_entry_match_score(ship: dict, selector: Optional[dict]) -> int:
    if not isinstance(ship, dict) or not isinstance(selector, dict): return -1
    sk=normalized(ship.get("kind_short")); tk=normalized(selector.get("kind_short"))
    sn=normalized(ship.get("name")); tn=normalized(selector.get("name"))
    sp=normalized(ship.get("plate")); tp=normalized(selector.get("plate"))
    kind_known=bool(sk and tk); kind_match=bool(kind_known and sk==tk)
    if kind_known and not kind_match: return -1
    sid=selector.get("shipyard_id"); ssid=ship.get("ship_id")
    sid_match=sid is not None and ssid is not None and str(sid)==str(ssid)
    name_match=bool(sn and tn and sn==tn); plate_match=bool(sp and tp and sp==tp)
    if sid_match and kind_match: return 100+(10 if name_match else 0)+(5 if plate_match else 0)
    if sid_match and not kind_known: return 60+(10 if name_match else 0)+(5 if plate_match else 0)
    if kind_match and name_match and plate_match: return 90
    if kind_match and name_match: return 80
    if kind_match and plate_match: return 75
    return -1

def ship_entry_matches_selector(ship: dict, selector: Optional[dict]) -> bool:
    return ship_entry_match_score(ship, selector) >= 0


def selector_label(selector: Optional[dict]) -> str:
    if not isinstance(selector, dict):
        return "(not configured)"
    name = selector.get("name") or "(unnamed)"
    kind = selector.get("kind_full") or selector.get("kind_short") or "?"
    plate = selector.get("plate") or "-"
    return f"{name} — {kind} — {plate}"


def package_for_assignment(assignment: Optional[dict]) -> Optional[dict]:
    if not isinstance(assignment, dict):
        return None
    return COCKPIT_PACKAGES.get(str(assignment.get("package_id") or ""))


def assignment_slots(assignment: dict) -> dict:
    package = package_for_assignment(assignment) or {}
    slots = dict(package.get("theme_names") or {})
    custom = assignment.get("theme_slots")
    if isinstance(custom, dict):
        slots.update(custom)
    # Ensure every known slot exists and empty strings behave as empty slots.
    for key in SLOT_LABELS:
        value = slots.get(key)
        slots[key] = str(value).strip() if value else None
    return slots


def assignment_for_ship_entry(cfg: dict, ship: Optional[dict], shipyard: Optional[dict] = None) -> Optional[dict]:
    """Return the strongest Reactive Cockpit assignment for this EDHM Shipyard entry.

    ID/type and type/name matches are strong. A type+plate-only match is useful for
    self-healing after EDHM recreates/renames a Shipyard entry, but is accepted only
    when that identity is unique in both the current Shipyard and assignment list.
    This preserves exact-ship behavior when a commander owns multiple identical ships.
    """
    if not isinstance(ship, dict):
        return None

    assignments=[a for a in (cfg.get("cockpit_assignments") or []) if isinstance(a,dict)]
    best=None; best_score=-1
    sk=normalized(ship.get("kind_short")); sp=normalized(ship.get("plate"))

    for assignment in assignments:
        score=ship_entry_match_score(ship, assignment.get("ship"))
        if score < 0:
            continue

        # type+plate with changed name/ID is deliberately a weak recovery path.
        # It is only safe when there is exactly one possible ship and one possible
        # assignment with that pair.
        if score == 75 and isinstance(shipyard, dict):
            current_candidates=[
                s for s in (shipyard.get("ships") or [])
                if normalized(s.get("kind_short"))==sk
                and normalized(s.get("plate"))==sp
            ]
            assignment_candidates=[
                a for a in assignments
                if normalized((a.get("ship") or {}).get("kind_short"))==sk
                and normalized((a.get("ship") or {}).get("plate"))==sp
            ]
            if len(current_candidates)!=1 or len(assignment_candidates)!=1:
                continue

        if score >= best_score:
            best=assignment; best_score=score

    return best


def upsert_assignment(cfg: dict, package_id: str, selector: dict) -> tuple[dict, bool]:
    assignments=cfg.setdefault("cockpit_assignments",[]); package=COCKPIT_PACKAGES[package_id]
    matches=[i for i,a in enumerate(assignments) if isinstance(a,dict) and selector_matches_selector(a.get("ship"),selector)]
    if matches:
        keep=matches[-1]; assignment=assignments[keep]
        assignment["package_id"]=package_id; assignment["ship"]=dict(selector); assignment["theme_slots"]=dict(package.get("theme_names") or {})
        for i in reversed(matches[:-1]): del assignments[i]
        return assignment,False
    assignment={"package_id":package_id,"ship":dict(selector),"theme_slots":dict(package.get("theme_names") or {})}
    assignments.append(assignment); return assignment,True


def selector_matches_selector(a: Optional[dict], b: Optional[dict]) -> bool:
    if not isinstance(a, dict) or not isinstance(b, dict): return False
    ak=normalized(a.get("kind_short")); bk=normalized(b.get("kind_short"))
    if ak and bk and ak != bk: return False
    aid=a.get("shipyard_id"); bid=b.get("shipyard_id")
    if aid is not None and bid is not None and str(aid)==str(bid): return True
    nm=bool(normalized(a.get("name"))) and normalized(a.get("name"))==normalized(b.get("name"))
    pm=bool(normalized(a.get("plate"))) and normalized(a.get("plate"))==normalized(b.get("plate"))
    return bool(ak and bk and ak==bk and (nm or pm))


def assignment_label(assignment: dict) -> str:
    package = package_for_assignment(assignment)
    package_name = package.get("display_name") if package else assignment.get("package_id", "Unknown package")
    return f"{package_name} -> {selector_label(assignment.get('ship'))}"


def package_authored_themes(package: dict) -> list[str]:
    out=[]
    base=str(package.get("base_theme") or "").strip()
    if base: out.append(base)
    for theme in (package.get("theme_names") or {}).values():
        theme=str(theme or "").strip()
        if theme and theme not in out: out.append(theme)
    return out


def auxiliary_slots(cfg: dict, vehicle: str) -> dict:
    defaults = DEFAULT_AUXILIARY_COCKPITS.get(vehicle, {})
    configured = (cfg.get("auxiliary_cockpits") or {}).get(vehicle, {})
    result = dict(defaults)
    if isinstance(configured, dict):
        result.update(configured)
    return {
        key: (str(value).strip() if value else None)
        for key, value in result.items()
    }


def configured_auxiliary_themes(cfg: dict) -> list[str]:
    out=[]
    for vehicle in ("fighter", "srv"):
        for theme in auxiliary_slots(cfg, vehicle).values():
            if theme and normalized(theme) not in {normalized(x) for x in out}:
                out.append(theme)
    return out


def choose_cockpit_package() -> tuple[str, dict]:
    print("WHICH REACTIVE COCKPIT DO YOU WISH TO INSTALL?")
    print("-----------------------------------------------")
    package_ids = list(COCKPIT_PACKAGES.keys())
    for i, package_id in enumerate(package_ids, 1):
        package = COCKPIT_PACKAGES[package_id]
        print(f"  {i}. {package['display_name']}")
        intended = package.get("intended_ship_type")
        if intended:
            print(f"     Designed for: {intended}")
    print()

    while True:
        raw = input("Choose Reactive Cockpit [1]: ").strip() or "1"
        try:
            idx = int(raw)
            if 1 <= idx <= len(package_ids):
                package_id = package_ids[idx - 1]
                return package_id, COCKPIT_PACKAGES[package_id]
        except ValueError:
            pass
        print("Please choose one of the listed numbers.")


def choose_target_ship(shipyard: dict, package: dict) -> dict:
    ships = shipyard.get("ships", [])
    if not ships:
        raise RuntimeError("No ships were found in EDHM Shipyard.")

    print("WHICH SHIP SHOULD USE THIS REACTIVE COCKPIT?")
    print("--------------------------------------------")
    print(f"Selected design: {package['display_name']}")
    intended = package.get("intended_ship_type")
    if intended:
        print(f"Design intent   : {intended}")
    print()
    print("You may assign it to ANY ship. The design name describes its intended")
    print("look; Reactive Cockpit will obey the ship you choose here.")
    print()

    for i, ship in enumerate(ships, 1):
        name = ship.get("name") or "(unnamed)"
        kind = ship.get("kind_full") or ship.get("kind_short") or "?"
        plate = ship.get("plate") or "-"
        theme = ship.get("theme") or "(no named theme)"
        marker = ""
        if intended and normalized(ship.get("kind_full")) == normalized(intended):
            marker = "  [INTENDED TYPE]"
        elif intended and intended.casefold() in normalized(ship.get("kind_full")):
            marker = "  [INTENDED TYPE]"
        print(f"  {i:>2}. {name} — {kind} — {plate}{marker}")
        print(f"      Current Shipyard theme: {theme}")
    print()

    while True:
        raw = input("Choose ship [1]: ").strip() or "1"
        try:
            idx = int(raw)
            if 1 <= idx <= len(ships):
                return ships[idx - 1]
        except ValueError:
            pass
        print("Please choose one of the listed ship numbers.")


def backup_and_assign_base_to_ship(shipyard_path: Path, selector: dict, base_theme: str):
    data = load_shipyard(shipyard_path)

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUPS_DIR / f"Shipyard_v3.before_ReactiveCockpit_{timestamp}.json"
    shutil.copyfile(shipyard_path, backup)

    changed = 0
    found = False
    for ship in data.get("ships", []):
        if ship_entry_matches_selector(ship, selector):
            found = True
            if str(ship.get("theme") or "") != base_theme:
                ship["theme"] = base_theme
                changed = 1
            break

    if not found:
        raise RuntimeError("The selected ship could not be found in the current Shipyard file.")

    temp = shipyard_path.with_name(shipyard_path.name + ".reactive_tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, shipyard_path)

    return backup, changed


def get_cached_theme_version(theme_name: str) -> str:
    folder = find_cached_theme(theme_name)
    if folder is None:
        return ""
    data = load_json(folder / SIGNAL_FILE, {})
    return str(data.get("version") or "") if isinstance(data, dict) else ""


def community_install():
    heading(f"VRFlorian & Guardian Arthur's - Reactive Cockpit — Installer v{VERSION}")

    print("Reactive Cockpit designs are ship-assigned cockpit packages.")
    print("A package may contain all reactive states or only the states its designer")
    print("actually wants. Empty state slots simply add no visual override;")
    print("Reactive Cockpit falls through to the next active authored state.")
    print()
    print("You can rerun this installer to add another cockpit package to another ship.")
    print("Existing package assignments on other ships are preserved.")
    print()
    print("Every Reactive Cockpit installation also includes the shared auxiliary")
    print("cockpits automatically:")
    print("  - Ship-Launched Fighter: Base + Combat")
    print("  - SRV: Base")
    print("They activate only when launched/deployed from a ship that has a")
    print("Reactive Cockpit assigned.")
    print()
    print("Start this installer with EDHM UI CLOSED.")
    print()

    cfg = load_config()
    shipyard_path = choose_shipyard(cfg)
    journal_dir = choose_journal_dir(cfg)
    edhm_root = shipyard_path.parent

    original_shipyard = load_shipyard(shipyard_path)
    original_base_map = assigned_base_themes(original_shipyard)
    original_base_themes = list(original_base_map.keys())

    print("Detected:")
    print(f"  EDHM user data : {edhm_root}")
    print(f"  Shipyard       : {shipyard_path}")
    print(f"  Ships          : {len(original_shipyard.get('ships', []))}")
    print()

    existing = cfg.get("cockpit_assignments") or []
    if existing:
        print("CURRENT REACTIVE COCKPIT ASSIGNMENTS")
        print("------------------------------------")
        for a in existing:
            print(f"  - {assignment_label(a)}")
        print()

    package_id, package = choose_cockpit_package()
    selected_ship = choose_target_ship(original_shipyard, package)
    selector = ship_selector_from_entry(selected_ship)

    previous = next((a for a in existing if selector_matches_selector(a.get("ship"), selector)), None)
    if previous:
        print()
        print("NOTE: this ship already has a Reactive Cockpit assignment:")
        print(f"  {assignment_label(previous)}")
        print("Installing the selected design will replace the package on THIS ship only.")
        print("Assignments on all other ships stay untouched.")
        print()

    assignment, added = upsert_assignment(cfg, package_id, selector)
    save_config(cfg)

    print("SELECTION")
    print("---------")
    print(f"Reactive Cockpit: {package['display_name']}")
    print(f"Apply to ship   : {selector_label(selector)}")
    print("State slots:")
    slots=assignment_slots(assignment)
    for key,label in SLOT_LABELS.items():
        print(f"  {label:<22} {slots.get(key) or '[empty -> no visual override]'}")
    print()

    # Install authored EDHM library themes and seed the matching tested live snapshots.
    # Both bundled cockpit designs currently have complete authored state sets.
    package_themes = package.get("library_themes") or package_authored_themes(package)
    install_themes = list(package_themes)
    for theme in AUXILIARY_THEME_NAMES:
        if theme not in install_themes:
            install_themes.append(theme)

    themes_path, library_installed, library_preserved = install_bundled_edhm_themes(edhm_root, install_themes)

    # Seed the complete tested runtime master. Existing user recaptures are preserved.
    cache_installed, cache_preserved = merge_bundled_cache(preserve_existing=True)

    print("EDHM theme library:")
    print(f"  Newly installed : {len(library_installed)}")
    print(f"  Already present : {len(library_preserved)}")
    print("Reactive Cockpit cache:")
    print(f"  Bundled master snapshots seeded : {len(cache_installed)}")
    print(f"  Existing snapshots kept         : {len(cache_preserved)}")
    print(f"  Working cache total             : {len(load_cache_index().get('themes', {}))}")
    print()

    # Missing authored package themes need local live snapshots. Empty optional
    # slots are NOT missing; they are deliberate non-overriding states.
    authored = package_authored_themes(package)
    missing_package = [t for t in authored if find_cached_theme(t) is None]
    aux_themes = configured_auxiliary_themes(cfg)
    missing_auxiliary = [t for t in aux_themes if find_cached_theme(t) is None]
    missing_original = [
        t for t in original_base_themes
        if find_cached_theme(t) is None
        and t not in missing_package
        and t not in missing_auxiliary
    ]

    print("DESIGNER BASE OPTION")
    print("--------------------")
    print(f"Recommended normal theme for this design: {package['base_theme']}")
    print(f"Selected ship: {selector_label(selector)}")
    print("YES = assign it to this ship only (Shipyard backup is made first).")
    print("NO  = preserve the ship's current normal Shipyard theme.")
    selected_base=str(selected_ship.get("theme") or "").strip()
    if not selected_base or normalized(selected_base)==normalized("Current Settings"):
        print("NOTE: this ship currently uses EDHM 'Current Settings'. That is not a")
        print("named/restorable theme. If you choose NO, Reactive Cockpit will use the")
        print("selected cockpit's designer Base whenever this assigned ship needs Base.")
    print()
    answer=input("Assign the design's Base theme to this ship? [Y/n]: ").strip().casefold()
    if answer not in ("n","no"):
        backup,changed=backup_and_assign_base_to_ship(shipyard_path,selector,package["base_theme"])
        print(f"Shipyard backup: {backup}")
        print("Base assignment updated." if changed else "Selected ship already used that Base theme.")
    else:
        print("Selected ship's normal Shipyard theme was preserved.")

    capture_items=[]
    for t in missing_package:
        capture_items.append((t,"package",None))
    for t in missing_auxiliary:
        role = "fighter" if t in auxiliary_slots(cfg, "fighter").values() else "srv"
        capture_items.append((t, f"aux_{role}", None))
    for t in missing_original:
        capture_items.append((t,"base",original_base_map.get(t)))

    if capture_items:
        print()
        heading("CAPTURE REQUIRED THEMES")
        if missing_package:
            print("This cockpit package contains authored EDHM theme(s) that do not yet")
            print("have a tested live Reactive Cockpit snapshot on this PC:")
            for t in missing_package: print(f"  - {t}")
            print()
        if missing_auxiliary:
            print("The shared Fighter/SRV cockpit theme(s) also need their first")
            print("local live snapshot:")
            for t in missing_auxiliary: print(f"  - {t}")
            print()
        if missing_original:
            print("Existing Shipyard base theme(s) also need capture:")
            for t in missing_original: print(f"  - {t}")
            print()
        print("Start Elite, OPEN EDHM UI, then press Enter.")
        print("Apply each requested theme in EDHM when Reactive Cockpit asks for it.")
        input("Press Enter when Elite + EDHM UI are ready...")
        edhm_ini=choose_edhm_ini(cfg)
        try:
            for theme,role,ships in capture_items:
                wait_and_capture(theme,role,edhm_ini,ships)
        except KeyboardInterrupt:
            print("\nCapture stopped. Completed snapshots remain saved.")
        print("\nClose EDHM UI before normal gameplay.")

    heading("REACTIVE COCKPIT PACKAGE INSTALLED")
    print(f"Package : {package['display_name']}")
    print(f"Ship    : {selector_label(selector)}")
    print()
    print("Installed assignments:")
    for a in cfg.get("cockpit_assignments") or []:
        print(f"  - {assignment_label(a)}")
    print()
    print("Rerun INSTALL_REACTIVE_COCKPIT.bat to install another package on another ship.")
    print("Use UPDATE_THEME_CACHE.bat to update themes OR add a theme to an empty slot.")
    input("\nPress Enter to close...")



def ask_theme_name(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default




def build_update_choices(cfg: dict, shipyard: dict):
    choices=[]

    # 1) BUNDLED COCKPIT DESIGN MASTERS
    #
    # Always show every authored cockpit package, even when it is not currently
    # assigned to a ship in this build's config. This is important for designers:
    # a Snow theme can be edited in EDHM and recaptured even while the active
    # test assignment happens to be the Type-11 package on another ship.
    for package_id, package in COCKPIT_PACKAGES.items():
        base=str(package.get("base_theme") or "").strip()
        if base:
            choices.append({
                "theme": base,
                "role": "package_master_base",
                "assignment_index": None,
                "slot": None,
                "empty": False,
                "master_package_id": package_id,
                "group": "master",
                "label": f"{package['display_name']} / Designer Base -> {base}",
            })

        master_slots=dict(package.get("theme_names") or {})
        for key,label in SLOT_LABELS.items():
            theme=str(master_slots.get(key) or "").strip() or None
            choices.append({
                "theme": theme,
                "role": key,
                "assignment_index": None,
                "slot": key,
                "empty": not bool(theme),
                "master_package_id": package_id,
                "group": "master",
                "label": (
                    f"{package['display_name']} / {label} -> {theme}"
                    if theme else
                    f"{package['display_name']} / {label} [EMPTY IN MASTER DESIGN]"
                ),
            })

    # 2) GLOBAL AUXILIARY COCKPITS
    #
    # These are shared by every reactive ship. They are not separate ship
    # packages and can be refreshed/replaced independently.
    for vehicle in ("fighter", "srv"):
        slots = auxiliary_slots(cfg, vehicle)
        slot_order = ("base", "combat") if vehicle == "fighter" else ("base",)
        for slot in slot_order:
            theme = slots.get(slot)
            label = AUXILIARY_SLOT_LABELS[(vehicle, slot)]
            choices.append({
                "theme": theme,
                "role": f"aux_{vehicle}_{slot}",
                "assignment_index": None,
                "slot": slot,
                "empty": not bool(theme),
                "master_package_id": None,
                "aux_vehicle": vehicle,
                "aux_slot": slot,
                "group": "auxiliary",
                "label": (
                    f"{label} -> {theme}"
                    if theme else
                    f"{label} [EMPTY - add a theme]"
                ),
            })

    # 3) INSTALLED SHIP ASSIGNMENTS / OVERRIDES
    #
    # These retain the existing ability to replace a state for one exact ship
    # or add a theme to an empty assignment slot.
    for ai, assignment in enumerate(cfg.get("cockpit_assignments") or []):
        package=package_for_assignment(assignment)
        if not package:
            continue
        ship_label=selector_label(assignment.get("ship"))
        slots=assignment_slots(assignment)

        # Only show assignment state slots here. The designer/master Base is
        # already available above, and the actual normal base remains available
        # in the Shipyard section below.
        for key,label in SLOT_LABELS.items():
            theme=slots.get(key)
            choices.append({
                "theme": theme,
                "role": key,
                "assignment_index": ai,
                "slot": key,
                "empty": not bool(theme),
                "master_package_id": None,
                "group": "assignment",
                "label": (
                    f"{package['display_name']} / {ship_label} / {label} -> {theme}"
                    if theme else
                    f"{package['display_name']} / {ship_label} / {label} [EMPTY - add a theme]"
                ),
            })

    # 4) NORMAL SHIPYARD BASES
    for theme,ships in assigned_base_themes(shipyard).items():
        count=len(ships); suffix=f"{count} ship" if count==1 else f"{count} ships"
        choices.append({
            "theme":theme,
            "role":"base",
            "assignment_index":None,
            "slot":None,
            "empty":False,
            "master_package_id":None,
            "group":"shipyard",
            "label":f"{theme} [Shipyard base, {suffix}]",
        })
    return choices

def parse_selection(raw: str, max_n: int) -> list[int]:
    raw=(raw or "").strip()
    if not raw: return []
    if raw.casefold()=="all": return list(range(1,max_n+1))
    result=[]; seen=set()
    for part in raw.split(","):
        token=part.strip()
        if not token: continue
        if "-" in token:
            left,right=token.split("-",1); start=int(left.strip()); end=int(right.strip())
            if start>end: start,end=end,start
            vals=range(start,end+1)
        else:
            vals=[int(token)]
        for value in vals:
            if not 1<=value<=max_n: raise ValueError(f"Selection {value} out of range")
            if value not in seen: seen.add(value); result.append(value)
    return result


def update_themes():
    heading(f"VRFlorian & Guardian Arthur's - Reactive Cockpit — Update Themes v{VERSION}")
    cfg=load_config(); edhm_ini=choose_edhm_ini(cfg); shipyard_path=choose_shipyard(cfg); journal_dir=choose_journal_dir(cfg)

    # Existing users upgrading from an older build should not need to rerun the
    # full installer just to receive the shared Fighter/SRV authored themes.
    _, aux_installed, _ = install_bundled_edhm_themes(shipyard_path.parent, AUXILIARY_THEME_NAMES)
    if aux_installed:
        print("Installed new shared auxiliary EDHM theme(s):")
        for theme in aux_installed:
            print(f"  - {theme}")
        print()

    shipyard=load_shipyard(shipyard_path); choices=build_update_choices(cfg,shipyard)

    if not choices:
        print("No updateable themes or installed cockpit packages were found.")
        input("\nPress Enter to close..."); return

    print("This tool can:")
    print("  - refresh any bundled cockpit-design theme after you edit it in EDHM")
    print("  - refresh/replace the shared Fighter and SRV cockpit themes")
    print("  - replace an existing ship-assigned reactive state with another EDHM theme")
    print("  - ADD a new theme to an EMPTY ship-assigned reactive state")
    print()
    print("MASTER DESIGN themes are always shown, even if that cockpit is not currently")
    print("assigned to a ship. This lets cockpit designers update Snow, Type-11, Python MKII, Caspian, Nebula Hopper, Kestrel, etc.")
    print("independently of the ship assignments active in this test build.")
    print()
    print("Keep Elite + EDHM UI OPEN while capturing.")
    print()

    last_group=None
    group_titles={
        "master": "BUNDLED COCKPIT DESIGN MASTERS",
        "auxiliary": "GLOBAL AUXILIARY COCKPITS",
        "assignment": "INSTALLED SHIP ASSIGNMENTS / OVERRIDES",
        "shipyard": "SHIPYARD BASE THEMES",
    }
    for i,item in enumerate(choices,1):
        group=item.get("group")
        if group != last_group:
            if last_group is not None:
                print()
            print(group_titles.get(group, "THEMES"))
            print("-" * len(group_titles.get(group, "THEMES")))
            last_group=group
        print(f"  {i:>2}. {item['label']}")
    print(); print("Choose one or more numbers (example 1,3,5-7).")
    print("Tip: do not use ALL if you only want to fill one empty slot.")
    while True:
        try:
            nums=parse_selection(input("Select item(s): ").strip(),len(choices))
            if nums: break
        except ValueError as e: print(f"Invalid selection: {e}")
        print("Please choose at least one item.")

    selected=[choices[n-1] for n in nums]
    done=set()
    try:
        for item in selected:
            theme=item.get("theme")
            replacement=False

            # A bundled MASTER design entry is deliberately independent of ship
            # assignment. Its authored theme name is part of this build, so the
            # updater refreshes/captures that exact EDHM theme. Per-ship replacement
            # and empty-slot additions remain available in the assignment section.
            if item.get("master_package_id"):
                package=COCKPIT_PACKAGES.get(item["master_package_id"]) or {}
                if item.get("empty"):
                    print()
                    print(f"{package.get('display_name','Unknown')} has no authored theme in this master slot.")
                    print("Assign the cockpit to a ship first if you want to create a ship-specific override.")
                    continue
                print()
                print(f"REFRESH MASTER THEME")
                print(f"Cockpit: {package.get('display_name','Unknown')}")
                print(f"State  : {SLOT_LABELS.get(item.get('slot'), 'Designer Base') if item.get('slot') else 'Designer Base'}")
                print(f"Theme  : {theme}")
                print("Apply/save this exact theme in EDHM, then continue to capture it.")

            elif item.get("aux_vehicle"):
                vehicle=item["aux_vehicle"]; slot=item["aux_slot"]
                label=AUXILIARY_SLOT_LABELS.get((vehicle,slot),f"{vehicle} {slot}")
                print(); print(f"UPDATE {label.upper()}")
                print("This theme is shared by every Reactive Cockpit ship.")
                if theme:
                    print(f"Current: {theme}")
                    print("Press Enter to recapture the current theme.")
                    print("Or type another exact EDHM theme name to replace this global slot.")
                    entered=input("Theme name [keep current]: ").strip()
                    if entered and normalized(entered) != normalized(theme):
                        theme=entered
                        replacement=True
                        print(f"This global slot will change to: {theme}")
                else:
                    print("Create/save the theme in EDHM first, then enter its exact EDHM theme name.")
                    theme=input("Theme name: ").strip()
                    if not theme:
                        print("No theme name entered; skipped."); continue
                    replacement=True

            elif item.get("empty"):
                ai=item["assignment_index"]; slot=item["slot"]
                assignment=cfg["cockpit_assignments"][ai]
                package=package_for_assignment(assignment) or {}
                suggestion=(package.get("slot_suggestions") or {}).get(slot) or ""
                print(); print(f"ADD {SLOT_LABELS.get(slot,slot)} THEME")
                print(f"Package: {package.get('display_name','Unknown')}")
                print(f"Ship   : {selector_label(assignment.get('ship'))}")
                print("Create/save the theme in EDHM first, then enter its exact EDHM theme name.")
                prompt=f"Theme name [{suggestion}]: " if suggestion else "Theme name: "
                entered=input(prompt).strip()
                theme=entered or suggestion
                if not theme:
                    print("No theme name entered; skipped."); continue
                print(f"This will permanently fill the {SLOT_LABELS.get(slot,slot)} slot with: {theme}")

            elif item.get("assignment_index") is not None and item.get("slot"):
                ai=item["assignment_index"]; slot=item["slot"]
                assignment=cfg["cockpit_assignments"][ai]
                package=package_for_assignment(assignment) or {}
                print(); print(f"UPDATE {SLOT_LABELS.get(slot,slot)} THEME")
                print(f"Package: {package.get('display_name','Unknown')}")
                print(f"Ship   : {selector_label(assignment.get('ship'))}")
                print(f"Current: {theme}")
                print("Press Enter to recapture the current theme.")
                print("Or type another exact EDHM theme name to replace this slot.")
                entered=input("Theme name [keep current]: ").strip()
                if entered and normalized(entered) != normalized(theme):
                    theme=entered
                    replacement=True
                    print(f"This slot will change to: {theme}")

            key=normalized(theme)
            if key not in done:
                wait_and_capture(theme,item["role"],edhm_ini)
                done.add(key)

            # Commit a new/replaced slot ONLY after successful capture.
            if item.get("aux_vehicle") and replacement:
                vehicle=item["aux_vehicle"]; slot=item["aux_slot"]
                cfg.setdefault("auxiliary_cockpits",{}).setdefault(vehicle,{})[slot]=theme
                save_config(cfg)
                print(f"  [GLOBAL SLOT UPDATED] {AUXILIARY_SLOT_LABELS.get((vehicle,slot),slot)} -> {theme}")
            elif item.get("empty") or replacement:
                ai=item["assignment_index"]; slot=item["slot"]
                cfg["cockpit_assignments"][ai].setdefault("theme_slots",{})[slot]=theme
                save_config(cfg)
                action="SLOT ADDED" if item.get("empty") else "SLOT REPLACED"
                print(f"  [{action}] {SLOT_LABELS.get(slot,slot)} -> {theme}")
    except KeyboardInterrupt:
        print("\n\nUpdate stopped. Completed captures and added slots remain saved.")
        return

    heading("THEME UPDATE COMPLETE")
    print("Close EDHM UI, then run RUN_REACTIVE_COCKPIT.bat.")
    input("\nPress Enter to close...")



def setup():
    heading(f"VRFlorian & Guardian Arthur's - Reactive Cockpit — Full Capture v{VERSION}")
    cfg=load_config(); edhm_ini=choose_edhm_ini(cfg); shipyard_path=choose_shipyard(cfg); journal_dir=choose_journal_dir(cfg)
    shipyard=load_shipyard(shipyard_path)

    wanted=[]
    def add(theme,role):
        theme=str(theme or "").strip()
        if theme and normalized(theme) not in {normalized(x[0]) for x in wanted}:
            wanted.append((theme,role))

    for theme in assigned_base_themes(shipyard): add(theme,"base")
    for assignment in cfg.get("cockpit_assignments") or []:
        package=package_for_assignment(assignment) or {}
        add(package.get("base_theme"),"package_base")
        for key,theme in assignment_slots(assignment).items(): add(theme,key)

    for vehicle in ("fighter", "srv"):
        for slot,theme in auxiliary_slots(cfg, vehicle).items():
            add(theme,f"aux_{vehicle}_{slot}")

    if not wanted:
        print("Nothing is configured to capture yet. Run INSTALL_REACTIVE_COCKPIT.bat first.")
        input("\nPress Enter to close..."); return

    print("This advanced tool recaptures every currently-used Shipyard base and every")
    print("non-empty state slot from all installed Reactive Cockpit packages.")
    print("Empty optional slots are intentionally skipped.")
    print()
    for theme,role in wanted: print(f"  - {theme} [{role}]")
    print(); print("Keep Elite + EDHM UI OPEN.")
    input("Press Enter to begin...")
    try:
        for theme,role in wanted: wait_and_capture(theme,role,edhm_ini)
    except KeyboardInterrupt:
        print("\nCapture stopped; completed snapshots remain saved."); return
    heading("FULL CAPTURE COMPLETE")
    print("Close EDHM UI before normal gameplay.")
    input("\nPress Enter to close...")


# ---------------------------------------------------------------------------
# Runtime state machine
# ---------------------------------------------------------------------------

@dataclass
class Runtime:
    cfg: dict
    edhm_ini: Path
    shipyard_path: Path
    journal_dir: Path
    shipyard: dict
    ship: ShipState

    in_main_ship: bool = False
    in_fighter: bool = False
    in_srv: bool = False
    shields_up: bool = False
    supercruise: bool = False
    silent_running: bool = False
    scooping_fuel: bool = False
    overheating: bool = False
    status_danger: bool = False
    status_interdicted: bool = False
    glide_mode: bool = False
    landing_gear_down: bool = False
    docked: bool = False
    landed: bool = False
    fuel_main: Optional[float] = None

    status_initialized: bool = False
    previous_raw_danger: bool = False
    previous_glide: bool = False
    previous_gear_down: bool = False
    previous_in_fighter: bool = False
    previous_in_srv: bool = False
    previous_external_context: bool = False
    previous_silent_running: bool = False
    previous_supercruise: bool = False
    previous_docked: bool = False
    previous_landed: bool = False

    last_danger_signal: float = -1e12
    wanted_until: float = -1e12
    glide_hold_until: float = -1e12
    docking_hold_until: float = -1e12
    supercruise_hold_until: float = -1e12
    docking_active: bool = False

    fighter_hull_health: Optional[float] = None
    canopy_breached: bool = False
    on_foot: bool = False
    in_taxi: bool = False
    in_multicrew: bool = False

    # Remember whether the mothership that launched/deployed the auxiliary
    # vehicle had a Reactive Cockpit assignment. This makes the handoff robust
    # even if Elite's current-vehicle telemetry temporarily makes Shipyard
    # matching unavailable while inside the Fighter/SRV.
    auxiliary_parent_reactive: bool = False
    auxiliary_parent_label: str = ""

    applied_key: Optional[tuple] = None
    matched_entry: Optional[dict] = None
    match_attempted_for: Optional[tuple] = None
    shipyard_mtime: Optional[int] = None
    last_missing_cache_warning: Optional[str] = None
    disco_active: bool = False
    disco_index: int = 0
    disco_next_at: float = -1e12
    disco_marker: Optional[Path] = None
    disco_restore_theme: Optional[str] = None
    disco_restore_pending: bool = False

    @property
    def combat_cooldown(self) -> float:
        return float(self.cfg["combat_cooldown_seconds"])

    @property
    def wanted_seconds(self) -> float:
        return float(self.cfg["wanted_entry_seconds"])

    @property
    def glide_cooldown(self) -> float:
        return float(self.cfg["glide_exit_cooldown_seconds"])

    @property
    def docking_cooldown(self) -> float:
        return float(self.cfg["docking_complete_cooldown_seconds"])

    @property
    def supercruise_cooldown(self) -> float:
        return float(self.cfg["supercruise_exit_cooldown_seconds"])

    def touch_danger(self):
        self.last_danger_signal = time.monotonic()

    @property
    def disco_interval(self) -> float:
        return max(0.5, float(self.cfg.get("disco_interval_seconds", 2.0)))

    def toggle_disco(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        self.disco_active = not self.disco_active
        if self.disco_active:
            self.disco_restore_theme = live_theme_name(self.edhm_ini) or None
            self.disco_restore_pending = False
            self.disco_index = 0
            self.disco_next_at = now
            set_disco_marker(True, self.disco_marker)
            print()
            print(f"[{stamp()}] DISCO MODE ON  -> normal and story visuals overridden")
            print(f"[{stamp()}] Press {DISCO_HOTKEY} again when civilisation must resume.")
        else:
            set_disco_marker(False, self.disco_marker)
            self.disco_index = 0
            self.disco_next_at = -1e12
            self.disco_restore_pending = True
            # The true current telemetry state wins over the theme that happened
            # to be active when the party began.
            self.applied_key = None
            print(f"[{stamp()}] DISCO MODE OFF -> restoring current cockpit state")
            print()
        return self.disco_active

    def stop_disco(self):
        if self.disco_active:
            self.toggle_disco()
        else:
            set_disco_marker(False, self.disco_marker)

    def apply_disco(self, now: Optional[float] = None):
        if not self.disco_active:
            return
        now = time.monotonic() if now is None else now
        if now < self.disco_next_at:
            return
        palette = DISCO_PALETTES[self.disco_index]
        try:
            apply_disco_palette(self.edhm_ini, palette)
            number = self.disco_index + 1
            print(
                f"[{stamp()}] DISCO {number}/{len(DISCO_PALETTES)} -> {palette[0]} | "
                f"ambient #{palette[1]} | primary #{palette[2]} | secondary #{palette[3]}"
            )
            self.disco_index = (self.disco_index + 1) % len(DISCO_PALETTES)
            self.disco_next_at = now + self.disco_interval
        except Exception as exc:
            self.disco_next_at = now + 0.5
            print(f"[{stamp()}] DISCO colour update failed; retrying: {exc}")

    def general_danger_active(self, now: float) -> bool:
        # Elite's generic IsInDanger flag is intentionally NOT a Combat trigger.
        # It can be raised by environmental hazards (for example close-star heat
        # while fuel scooping). Combat is seeded only by combat-specific signals:
        # Being Interdicted or Journal events such as UnderAttack / Interdicted.
        #
        # IsInDanger may keep an already-established combat context alive, but it
        # can never create that context by itself.
        recent_seed = (now - self.last_danger_signal < self.combat_cooldown)
        return self.status_interdicted or recent_seed

    def wanted_active(self, now: float) -> bool:
        return now < self.wanted_until

    def glide_active(self, now: float) -> bool:
        return self.glide_mode or now < self.glide_hold_until

    def docking_state_active(self, now: float) -> bool:
        """Docking is derived from physical state, never from a stale latch."""
        if self.supercruise:
            return False
        if self.docked or self.landed:
            return now < self.docking_hold_until
        if self.landing_gear_down:
            return True
        return now < self.docking_hold_until

    def supercruise_state_active(self, now: float) -> bool:
        return self.supercruise or now < self.supercruise_hold_until

    def low_fuel(self) -> tuple[bool, Optional[float]]:
        if not self.in_main_ship:
            return False, None

        cap = self.ship.fuel_capacity_main

        if cap is None or cap <= 0 or self.fuel_main is None:
            return False, None

        fraction = self.fuel_main / cap
        threshold = float(self.cfg["low_fuel_threshold"])

        return fraction < threshold, fraction

    def active_hull_health(self) -> Optional[float]:
        if self.in_fighter:
            return self.fighter_hull_health
        if self.in_main_ship:
            return self.ship.hull_health
        return None

    def remember_auxiliary_parent(self):
        entry=self.match_current_ship()
        assignment=self.current_assignment(entry)
        self.auxiliary_parent_reactive = assignment is not None
        self.auxiliary_parent_label = assignment_label(assignment) if assignment else ""

    def auxiliary_source_is_reactive(self) -> bool:
        if self.auxiliary_parent_reactive:
            return True
        entry=self.match_current_ship()
        return self.current_assignment(entry) is not None

    def update_status(self, status: dict):
        flags = int(status.get("Flags", 0))
        flags2 = int(status.get("Flags2", 0))
        now = time.monotonic()

        new_in_main = bool(flags & FLAG_IN_MAIN_SHIP)
        new_in_fighter = bool(flags & FLAG_IN_FIGHTER)
        new_in_srv = bool(flags & FLAG_IN_SRV)
        new_on_foot=bool(flags2 & FLAG2_ON_FOOT)
        new_in_taxi=bool(flags2 & FLAG2_IN_TAXI)
        new_in_multicrew=bool(flags2 & FLAG2_IN_MULTICREW)
        new_external_context=new_on_foot or new_in_taxi or new_in_multicrew
        new_glide = bool(flags2 & FLAG2_GLIDE_MODE)
        new_gear = bool(flags & FLAG_LANDING_GEAR_DOWN)
        new_docked = bool(flags & FLAG_DOCKED)
        new_landed = bool(flags & FLAG_LANDED)
        new_silent = bool(flags & FLAG_SILENT_RUNNING)
        new_supercruise = bool(flags & FLAG_SUPERCRUISE)

        generic_danger = bool(flags & FLAG_IS_IN_DANGER)
        being_interdicted = bool(flags & FLAG_BEING_INTERDICTED)

        # Silent Running is an absolute visual override. Only combat-specific
        # status (Being Interdicted) is allowed to seed/hold Combat here.
        # Generic IsInDanger can be environmental and must never start Combat.
        effective_raw_danger = being_interdicted and not new_silent

        # If genuine combat was already seeded by UnderAttack/Interdicted, the
        # generic IsInDanger flag is useful as a *maintainer*: refresh the release
        # timer while the danger persists. It still cannot create Combat from zero.
        if (
            self.status_initialized
            and generic_danger
            and not new_silent
            and (now - self.last_danger_signal < self.combat_cooldown)
        ):
            self.last_danger_signal = now

        if self.status_initialized:
            if self.previous_raw_danger and not effective_raw_danger:
                self.touch_danger()
                print(
                    f"[{stamp()}] Interdiction danger cleared -> "
                    f"{self.combat_cooldown:.0f}s Combat hold"
                )

            if self.previous_glide and not new_glide:
                self.glide_hold_until = now + self.glide_cooldown
                print(
                    f"[{stamp()}] Glide complete -> "
                    f"{self.glide_cooldown:.0f}s Supercruise-theme hold"
                )
            elif not self.previous_glide and new_glide:
                print(f"[{stamp()}] GLIDE MODE -> Supercruise theme")

            if not self.previous_in_fighter and new_in_fighter:
                self.remember_auxiliary_parent()
                self.fighter_hull_health = 1.0
                self.applied_key = None
                source = self.auxiliary_parent_label or "non-reactive mothership"
                print(f"[{stamp()}] ENTERED FIGHTER -> Fighter Base | source: {source}")
            elif self.previous_in_fighter and not new_in_fighter:
                self.fighter_hull_health = None
                self.applied_key = None
                print(f"[{stamp()}] RETURNED FROM FIGHTER")

            if not self.previous_in_srv and new_in_srv:
                self.remember_auxiliary_parent()
                self.applied_key = None
                source = self.auxiliary_parent_label or "non-reactive mothership"
                print(f"[{stamp()}] ENTERED SRV -> SRV Base | source: {source}")
            elif self.previous_in_srv and not new_in_srv:
                self.applied_key = None
                print(f"[{stamp()}] RETURNED FROM SRV")

            if not self.previous_external_context and new_external_context:
                labels=[]
                if new_on_foot: labels.append("ON FOOT")
                if new_in_taxi: labels.append("TAXI/DROPSHIP")
                if new_in_multicrew: labels.append("MULTICREW")
                print(f"[{stamp()}] CONTROL CONTEXT -> {' + '.join(labels)} | ship theme switching paused")
            elif self.previous_external_context and not new_external_context:
                self.applied_key=None
                print(f"[{stamp()}] CONTROL CONTEXT -> own vehicle | reactive logic resumed")

            if not self.previous_silent_running and new_silent:
                print(f"[{stamp()}] SILENT RUNNING -> Stealth absolute override")
            elif self.previous_silent_running and not new_silent:
                print(f"[{stamp()}] Silent Running off -> resume normal state logic")

            if self.previous_supercruise and not new_supercruise:
                self.supercruise_hold_until = now + self.supercruise_cooldown
                print(
                    f"[{stamp()}] Supercruise exit -> "
                    f"Supercruise hold {self.supercruise_cooldown:.0f}s"
                )
            elif not self.previous_supercruise and new_supercruise:
                self.supercruise_hold_until = -1e12
                # Supercruise and Docking are physically mutually exclusive.
                self.docking_active = False
                self.docking_hold_until = -1e12
                print(f"[{stamp()}] ENTERED SUPERCRUISE")

            # Never run docking reconciliation inside Supercruise. This prevents
            # a stale "just left the pad" edge from rebuilding a docking hold
            # immediately after the Supercruise boundary cleared it.
            if not new_supercruise:
                was_parked = self.previous_docked or self.previous_landed
                is_parked = new_docked or new_landed

                # Arrival: touchdown/dock ends the continuous gear-down phase and
                # leaves only the short completion hold.
                if not was_parked and is_parked:
                    self.docking_active = False
                    self.docking_hold_until = max(
                        self.docking_hold_until,
                        now + self.docking_cooldown,
                    )
                    print(
                        f"[{stamp()}] PARKED -> "
                        f"Docking hold {self.docking_cooldown:.0f}s"
                    )

                # While parked, gear naturally remains down. Never allow that
                # physical fact to re-arm a permanent Docking state.
                elif is_parked:
                    self.docking_active = False

                # Airborne/undocked: actual gear level is authoritative.
                else:
                    if new_gear:
                        if not self.docking_active:
                            self.docking_active = True
                            self.docking_hold_until = -1e12
                            print(f"[{stamp()}] Landing gear DOWN while airborne -> Docking")
                    elif self.docking_active:
                        # LEVEL reconciliation, not merely an edge. This clears a
                        # Liftoff/Undocked latch even if the gear-up Status arrived first.
                        self.docking_active = False
                        self.docking_hold_until = max(
                            self.docking_hold_until,
                            now + self.docking_cooldown,
                        )
                        print(
                            f"[{stamp()}] Landing gear UP / docking latch cleared -> "
                            f"Docking hold {self.docking_cooldown:.0f}s"
                        )

                    # If departure is first observed with the gear already up,
                    # retain only the normal short takeoff-completion look.
                    if was_parked and not new_gear:
                        self.docking_hold_until = max(
                            self.docking_hold_until,
                            now + self.docking_cooldown,
                        )

        # If Reactive Cockpit is started while the commander is already inside
        # a Fighter/SRV, establish the mothership relationship on first status read.
        if not self.status_initialized and (new_in_fighter or new_in_srv):
            self.remember_auxiliary_parent()

        # Once back in the main ship, normal package matching takes over.
        if new_in_main and not new_in_fighter and not new_in_srv:
            self.auxiliary_parent_reactive = False
            self.auxiliary_parent_label = ""

        self.status_initialized = True
        self.previous_raw_danger = effective_raw_danger
        self.previous_glide = new_glide
        self.previous_gear_down = new_gear
        self.previous_in_fighter = new_in_fighter
        self.previous_in_srv = new_in_srv
        self.previous_external_context = new_external_context
        self.previous_silent_running = new_silent
        self.previous_supercruise = new_supercruise
        self.previous_docked = new_docked
        self.previous_landed = new_landed

        self.in_main_ship = new_in_main
        self.in_fighter = new_in_fighter
        self.in_srv = new_in_srv
        self.on_foot = new_on_foot
        self.in_taxi = new_in_taxi
        self.in_multicrew = new_in_multicrew
        self.shields_up = bool(flags & FLAG_SHIELDS_UP)
        self.supercruise = new_supercruise
        self.silent_running = new_silent
        self.scooping_fuel = bool(flags & FLAG_SCOOPING_FUEL)
        self.overheating = bool(flags & FLAG_OVERHEATING)
        self.status_danger = generic_danger
        self.status_interdicted = being_interdicted
        self.glide_mode = new_glide
        self.landing_gear_down = new_gear
        self.docked = new_docked
        self.landed = new_landed

        fuel = status.get("Fuel")
        if isinstance(fuel, dict):
            try:
                self.fuel_main = float(fuel.get("FuelMain"))
            except (TypeError, ValueError):
                pass

    def process_event(self, e: dict):
        ev = e.get("event")
        now = time.monotonic()

        if ev == "Loadout":
            self.ship = ship_from_loadout(e)
            self.applied_key = None
            self.matched_entry = None
            self.match_attempted_for = None

            # A ship switch starts with a clean transient state. This prevents a
            # 15-second danger/glide/wanted timer from one vessel leaking into a
            # different vessel, especially one with no reactive package assigned.
            self.last_danger_signal = -1e12
            self.wanted_until = -1e12
            self.glide_hold_until = -1e12
            self.supercruise_hold_until = -1e12
            self.docking_hold_until = -1e12
            self.docking_active = False

            self.canopy_breached = (
                self.ship.canopy_health is not None
                and self.ship.canopy_health <= 0.0
            )

            print(f"[{stamp()}] SHIP -> {self.ship.friendly()}")

            if self.ship.hull_health is not None:
                print(f"[{stamp()}] Hull {self.ship.hull_health * 100:.1f}%")

            if self.ship.canopy_health is not None:
                print(f"[{stamp()}] Canopy {self.ship.canopy_health * 100:.1f}%")

            if self.ship.fuel_capacity_main is not None:
                print(
                    f"[{stamp()}] Main fuel capacity "
                    f"{self.ship.fuel_capacity_main:.2f}t"
                )
            return

        if ev == "Disembark":
            self.on_foot=True; self.in_taxi=bool(e.get("Taxi",False)); self.in_multicrew=bool(e.get("Multicrew",False)); self.applied_key=None
            print(f"[{stamp()}] DISEMBARKED -> ship theme switching paused")
            return

        if ev == "Embark":
            self.on_foot=False; self.in_taxi=bool(e.get("Taxi",False)); self.in_multicrew=bool(e.get("Multicrew",False)); self.applied_key=None
            print(f"[{stamp()}] EMBARKED -> reactive context will re-evaluate")
            return

        if ev == "UnderAttack":
            if self.on_foot or self.in_taxi or self.in_multicrew:
                print(f"[{stamp()}] UnderAttack outside own cockpit -> ignored by ship theme logic")
                return
            self.touch_danger()
            print(f"[{stamp()}] UnderAttack -> Combat")
            return

        if ev == "Interdicted":
            self.touch_danger()
            print(f"[{stamp()}] Interdicted -> Combat")
            return

        if ev == "ShieldState":
            self.shields_up = bool(e.get("ShieldsUp"))
            if not self.shields_up:
                if self.silent_running:
                    print(f"[{stamp()}] Shields down during Silent Running -> intentional")
                else:
                    # Shield loss can escalate an already-genuine combat/danger
                    # context to Deep Danger, but it does not create Combat alone.
                    print(f"[{stamp()}] SHIELDS DOWN")
            else:
                print(f"[{stamp()}] Shields restored")
            return

        if ev == "FSDJump":
            if bool(e.get("Wanted", False)):
                self.wanted_until = now + self.wanted_seconds
                print(
                    f"[{stamp()}] ENTERED SYSTEM WANTED -> "
                    f"Combat for {self.wanted_seconds:.0f}s"
                )
            return

        if ev == "HullDamage":
            h = safe_fraction(e.get("Health"))

            if h is not None:
                if e.get("Fighter", False):
                    self.fighter_hull_health = h
                    print(f"[{stamp()}] FIGHTER HULL {h * 100:.1f}%")
                elif e.get("PlayerPilot", True):
                    self.ship.hull_health = h
                    print(f"[{stamp()}] HULL {h * 100:.1f}%")
            return

        if ev == "CockpitBreached":
            self.canopy_breached = True
            self.ship.canopy_health = 0.0
            print(f"[{stamp()}] COCKPIT BREACHED -> Imminent Destruction")
            return

        if ev == "AfmuRepairs":
            module = normalized(e.get("Module"))

            if "cockpit" in module or "canopy" in module:
                h = safe_fraction(e.get("Health"))
                if h is not None:
                    self.ship.canopy_health = h
                    if h > float(self.cfg["critical_canopy_threshold"]):
                        self.canopy_breached = False
                    print(f"[{stamp()}] Canopy repair -> {h * 100:.1f}%")
            return

        if ev == "RepairAll":
            self.ship.hull_health = 1.0
            self.ship.canopy_health = 1.0
            self.canopy_breached = False
            print(f"[{stamp()}] Full repair -> hull/canopy 100%")
            return

        if ev == "Repair":
            item = normalized(e.get("Item"))

            if item in ("all", "hull"):
                self.ship.hull_health = 1.0

            if item == "all" or "cockpit" in item or "canopy" in item:
                self.ship.canopy_health = 1.0
                self.canopy_breached = False
            return

        if ev == "Resurrect":
            self.ship.hull_health = 1.0
            self.ship.canopy_health = 1.0
            self.canopy_breached = False
            return

        if ev in ("Liftoff", "Undocked"):
            if e.get("PlayerControlled", True):
                if ev == "Liftoff":
                    self.landed = False
                if ev == "Undocked":
                    self.docked = False

                if self.landing_gear_down:
                    self.docking_active = True
                    self.docking_hold_until = -1e12
                    print(f"[{stamp()}] {ev} -> Docking while landing gear remains down")
                else:
                    # Journal can arrive after the gear-up Status change.
                    self.docking_active = False
                    self.docking_hold_until = max(
                        self.docking_hold_until,
                        now + self.docking_cooldown,
                    )
                    print(
                        f"[{stamp()}] {ev} with gear already UP -> "
                        f"Docking hold {self.docking_cooldown:.0f}s"
                    )
            return

        if ev in ("Touchdown", "Docked"):
            if e.get("PlayerControlled", True):
                if ev == "Touchdown":
                    self.landed = True
                if ev == "Docked":
                    self.docked = True

                self.docking_active = False
                self.docking_hold_until = max(
                    self.docking_hold_until,
                    now + self.docking_cooldown,
                )
                print(
                    f"[{stamp()}] {ev} -> "
                    f"Docking hold {self.docking_cooldown:.0f}s"
                )
            return

        if ev == "FuelScoop":
            try:
                self.fuel_main = float(e.get("Total"))
            except (TypeError, ValueError):
                pass
            return

        if ev == "SetUserShipName":
            if e.get("ShipName") is not None:
                self.ship.ship_name = str(e.get("ShipName") or "").strip()
            elif e.get("UserShipName") is not None:
                self.ship.ship_name = str(e.get("UserShipName") or "").strip()

            if e.get("ShipIdent") is not None:
                self.ship.ship_ident = str(e.get("ShipIdent") or "").strip()
            elif e.get("UserShipId") is not None:
                self.ship.ship_ident = str(e.get("UserShipId") or "").strip()

            self.matched_entry = None
            self.match_attempted_for = None
            self.applied_key = None
            return

        if ev == "HeatWarning":
            print(f"[{stamp()}] HeatWarning event (>100%)")
            return

    def refresh_shipyard_if_changed(self):
        try:
            mtime = self.shipyard_path.stat().st_mtime_ns
        except OSError:
            return

        if self.shipyard_mtime is None:
            self.shipyard_mtime = mtime
            return

        if mtime != self.shipyard_mtime:
            try:
                self.shipyard = load_shipyard(self.shipyard_path)
                self.shipyard_mtime = mtime
                self.matched_entry = None
                self.match_attempted_for = None
                self.applied_key = None
                print(f"[{stamp()}] Shipyard changed -> reloaded")
            except Exception as exc:
                print(f"[{stamp()}] WARNING: could not reload Shipyard: {exc}")

    def match_current_ship(self) -> Optional[dict]:
        signature = (
            normalized(self.ship.ship_type),
            normalized(self.ship.ship_name),
            normalized(self.ship.ship_ident),
        )

        if self.matched_entry is not None:
            return self.matched_entry

        if self.match_attempted_for == signature:
            return None

        self.match_attempted_for = signature
        self.matched_entry = match_shipyard_ship(self.ship, self.shipyard)

        if self.matched_entry:
            print(
                f"[{stamp()}] Shipyard match -> "
                f"{self.matched_entry.get('name')} = "
                f"{self.matched_entry.get('theme')}"
            )
        elif self.ship.ship_type or self.ship.ship_name:
            print(
                f"[{stamp()}] WARNING: current ship is not "
                "uniquely matched in EDHM Shipyard."
            )

        return self.matched_entry

    def current_assignment(self, entry: Optional[dict] = None) -> Optional[dict]:
        if entry is None:
            entry=self.match_current_ship()

        assignment=assignment_for_ship_entry(self.cfg,entry,self.shipyard)
        if assignment is None or not isinstance(entry,dict):
            return assignment

        # EDHM may refresh/recreate a Shipyard entry after an in-game ship rename.
        # Once a conservative match succeeds, refresh the stored selector so the
        # assignment follows the exact current Shipyard identity from then on.
        current_selector=ship_selector_from_entry(entry)
        old_selector=assignment.get("ship") if isinstance(assignment.get("ship"),dict) else {}
        selector_changed=any(
            normalized(old_selector.get(k)) != normalized(current_selector.get(k))
            for k in ("shipyard_id","kind_short","kind_full","name","plate")
        )
        if selector_changed:
            old_label=selector_label(old_selector)
            assignment["ship"]=current_selector
            try:
                save_config(self.cfg)
                print(f"[{stamp()}] Assignment identity refreshed -> {old_label} -> {selector_label(current_selector)}")
            except Exception as exc:
                print(f"[{stamp()}] WARNING: could not save refreshed assignment identity: {exc}")

        return assignment

    def current_ship_is_reactive(self, entry: Optional[dict] = None) -> bool:
        return self.current_assignment(entry) is not None

    def base_result(self, entry: Optional[dict], reason: str = "Shipyard base", assignment: Optional[dict] = None) -> Optional[tuple[str,str,str]]:
        if not entry:
            return None

        base=str(entry.get("theme") or "").strip()
        if base and normalized(base)!=normalized("Current Settings"):
            return ("BASE",base,reason)

        # "Current Settings" is not a named/restorable EDHM theme. Returning None
        # here used to leave whatever Reactive Cockpit theme happened to be loaded
        # from the previous ship. For a ship that HAS a Reactive Cockpit assignment,
        # the package designer Base is the only deterministic safe fallback.
        package=package_for_assignment(assignment)
        package_base=str((package or {}).get("base_theme") or "").strip()
        if package_base:
            return (
                "BASE",
                package_base,
                reason + " | Shipyard Current Settings -> Reactive Cockpit designer Base",
            )

        return None

    def slot_result(self, assignment: dict, entry: Optional[dict], slot: str, state_name: str, reason: str):
        """Return this state's authored theme, or None when the package leaves the slot empty.

        IMPORTANT: an empty slot is *not* a command to restore Base. It means this
        logical state has no visual override in this cockpit design, so desired()
        must continue down the active-state priority stack. Base is only the final
        fallback when no active state with an authored theme remains.
        """
        theme=assignment_slots(assignment).get(slot)
        if theme:
            return (state_name,theme,reason)
        return None

    def desired(self, now: float) -> Optional[tuple[str, str, str]]:
        # Flags2 is authoritative when Odyssey says the commander is outside
        # their own controlled cockpit. Never switch a ship HUD while on foot.
        if self.on_foot or self.in_taxi or self.in_multicrew:
            return None
        if not self.in_main_ship and not self.in_fighter and not self.in_srv:
            return None

        entry=self.match_current_ship()
        assignment=self.current_assignment(entry)

        # --------------------------------------------------------------
        # GLOBAL AUXILIARY COCKPITS
        # --------------------------------------------------------------
        # They only activate when launched/deployed from a mothership that has
        # a Reactive Cockpit assigned. They deliberately do NOT inherit the
        # mothership's eight-state palette.
        if self.in_srv:
            if not self.auxiliary_source_is_reactive():
                return None
            theme=auxiliary_slots(self.cfg,"srv").get("base")
            return ("SRV_BASE",theme,"SRV deployed from reactive mothership") if theme else None

        if self.in_fighter:
            if not self.auxiliary_source_is_reactive():
                return None

            fighter=auxiliary_slots(self.cfg,"fighter")
            # Fighter hardpoints/weapons are effectively always deployed, so
            # weapon deployment is intentionally not a Combat trigger. Use the
            # same confirmed-combat signals and release timer as a normal ship.
            if self.general_danger_active(now):
                theme=fighter.get("combat")
                if theme:
                    reason="being interdicted" if self.status_interdicted else "recent confirmed combat"
                    if self.status_danger and not self.status_interdicted:
                        reason += " (IsInDanger maintaining hold)"
                    return ("FIGHTER_COMBAT",theme,reason)

            theme=fighter.get("base")
            return ("FIGHTER_BASE",theme,"fighter idle / no confirmed combat") if theme else None

        # No package on this main ship: normal Shipyard switching only.
        if assignment is None:
            if self.in_main_ship:
                return self.base_result(entry,"Shipyard base — no Reactive Cockpit assigned")
            return None

        # A package may intentionally omit any reactive slot. Missing slots do not
        # force Base; they simply do not override whatever lower-priority authored
        # state is also active. This is important for nested states such as:
        #   Supercruise + Fuel Scoop, where Scoop may be empty but Supercruise exists.
        # Base is only reached after every applicable authored state has been tried.
        skipped=[]

        def pick(slot: str, state_name: str, reason: str):
            result=self.slot_result(assignment,entry,slot,state_name,reason)
            if result:
                if skipped:
                    state,theme,why=result
                    why += "; empty higher slot(s): " + ", ".join(skipped)
                    return (state,theme,why)
                return result
            label=SLOT_LABELS.get(slot,slot)
            if label not in skipped:
                skipped.append(label)
            return None

        # Silent Running still has first priority when this cockpit actually authors
        # a Stealth look. If Stealth is intentionally empty, it contributes no visual
        # override and the resolver continues to other genuinely active states.
        if self.in_main_ship and self.silent_running:
            result=pick("stealth","STEALTH","silent running absolute override")
            if result:
                return result

        hull=self.active_hull_health()
        if hull is not None and hull < float(self.cfg["critical_hull_threshold"]):
            result=pick("critical","CRITICAL",f"ship hull {hull*100:.1f}%")
            if result:
                return result

        if self.in_main_ship:
            canopy=self.ship.canopy_health
            if self.canopy_breached:
                result=pick("critical","CRITICAL","cockpit canopy breached")
                if result:
                    return result
            elif canopy is not None and canopy <= float(self.cfg["critical_canopy_threshold"]):
                result=pick("critical","CRITICAL",f"canopy {canopy*100:.1f}%")
                if result:
                    return result

        # Psychological / operational damage band:
        # below 50% hull the ship is genuinely badly hurt and should remain
        # Deep Danger regardless of whether a short Combat hold expires.
        # Below 20% the Critical rule above already wins.
        if (
            self.in_main_ship
            and hull is not None
            and hull < float(self.cfg["deep_danger_hull_threshold"])
        ):
            result=pick("deep_danger","DEEP_DANGER",f"ship hull {hull*100:.1f}%")
            if result:
                return result

        if self.overheating:
            result=pick("deep_danger","DEEP_DANGER","overheating >100%")
            if result:
                return result

        fuel_low,fuel_fraction=self.low_fuel()
        if fuel_low:
            result=pick("deep_danger","DEEP_DANGER",f"fuel {fuel_fraction*100:.1f}%")
            if result:
                return result

        # Genuine ship-condition danger above this point (critical hull/canopy,
        # real overheating, genuinely low fuel) remains authoritative even while
        # landing or docked. A carrier/station does not magically repair/refuel
        # the ship.
        #
        # Docking only suppresses CONTEXTUAL danger below: combat residue,
        # Wanted-entry hold, generic IsInDanger maintenance, and shields-down
        # danger context. These can otherwise linger nonsensically on a safe pad.
        contextual_danger_allowed = not self.docked
        general=self.general_danger_active(now) if contextual_danger_allowed else False
        wanted=self.wanted_active(now) if contextual_danger_allowed else False
        glide=self.glide_active(now) if contextual_danger_allowed else False
        shield_danger_context=general or wanted or glide
        if shield_danger_context and not self.shields_up and self.ship.has_shield_generator is not False:
            result=pick("deep_danger","DEEP_DANGER","shields down")
            if result:
                return result

        if general:
            reason="being interdicted" if self.status_interdicted else "recent confirmed combat"
            if self.status_danger and not self.status_interdicted:
                reason += " (IsInDanger maintaining hold)"
            result=pick("combat","COMBAT",reason)
            if result:
                return result
        if wanted:
            remaining=max(0.0,self.wanted_until-now)
            result=pick("combat","COMBAT",f"wanted system entry ({remaining:.1f}s)")
            if result:
                return result

        if glide:
            reason="glide mode" if self.glide_mode else "3s glide exit hold"
            result=pick("supercruise","SUPERCRUISE",reason)
            if result:
                return result

        if self.in_main_ship and self.scooping_fuel:
            result=pick("scoop","SCOOP","fuel scooping")
            if result:
                return result

        if self.in_main_ship and self.docking_state_active(now):
            reason = "docked / docking completion" if self.docked else "landing/takeoff sequence"
            result=pick("docking","DOCKING",reason)
            if result:
                return result

        if self.in_main_ship and self.supercruise_state_active(now):
            reason="supercruise" if self.supercruise else "3s supercruise exit hold"
            result=pick("supercruise","SUPERCRUISE",reason)
            if result:
                return result

        if self.in_main_ship:
            if skipped:
                return self.base_result(
                    entry,
                    "active slot(s) empty -> Shipyard base: " + ", ".join(skipped),
                    assignment,
                )
            return self.base_result(entry,"Shipyard base",assignment)
        return None

    def apply_desired(self):
        # Telemetry and timers continue underneath; only visual application is
        # suspended while Disco owns the three lighting colours.
        if self.disco_active:
            self.apply_disco()
            return

        desired = self.desired(time.monotonic())

        if desired is None:
            if self.disco_restore_pending:
                fallback = str(self.disco_restore_theme or "").strip()
                self.disco_restore_pending = False
                if fallback and find_cached_theme(fallback) is not None:
                    try:
                        apply_cached_theme(fallback, self.edhm_ini)
                        print(f"[{stamp()}] DISCO RESTORE -> {fallback} | external control context")
                    except Exception as exc:
                        print(f"[{stamp()}] ERROR restoring '{fallback}' after Disco Mode: {exc}")
            return

        state_name, theme_name, reason = desired
        key = (state_name, normalized(theme_name))

        if key == self.applied_key:
            return

        if find_cached_theme(theme_name) is None:
            warning_key = f"{state_name}:{theme_name}"

            if warning_key != self.last_missing_cache_warning:
                print(
                    f"[{stamp()}] CACHE MISSING: '{theme_name}'. "
                    "Run INSTALL_AND_CAPTURE.bat."
                )
                self.last_missing_cache_warning = warning_key
            return

        try:
            apply_cached_theme(theme_name, self.edhm_ini)
            self.applied_key = key
            self.disco_restore_pending = False
            self.last_missing_cache_warning = None

            extras = []

            hull = self.active_hull_health()
            if hull is not None:
                extras.append(f"hull {hull * 100:.1f}%")

            _, fuel_fraction = self.low_fuel()
            if fuel_fraction is not None:
                extras.append(f"fuel {fuel_fraction * 100:.1f}%")

            suffix = " | " + " | ".join(extras) if extras else ""

            print(
                f"[{stamp()}] STATE -> {state_name:<12} | "
                f"{theme_name} | {reason}{suffix}"
            )

        except Exception as exc:
            print(f"[{stamp()}] ERROR applying '{theme_name}': {exc}")


# ---------------------------------------------------------------------------
# Main controller
# ---------------------------------------------------------------------------

def run_controller():
    # Disco is intentionally never durable. A hard termination can leave only
    # this coordination marker behind; every launch clears it before work begins.
    set_disco_marker(False)
    heading(f"VRFlorian & Guardian Arthur's - Reactive Cockpit v{VERSION}")

    cfg=load_config(); edhm_ini=choose_edhm_ini(cfg); shipyard_path=choose_shipyard(cfg); journal_dir=choose_journal_dir(cfg)

    try:
        healed, _preserved = merge_bundled_cache(preserve_existing=True)
        if healed:
            print(f"[{stamp()}] Runtime cache self-heal -> restored {len(healed)} bundled snapshot(s)")
    except Exception as exc:
        print(f"[{stamp()}] WARNING: bundled cache self-heal failed: {exc}")

    cache_index = load_cache_index()
    cache_count = len(cache_index.get("themes", {})) if isinstance(cache_index, dict) else 0
    print("SESSION DIAGNOSTICS")
    print("-------------------")
    print(f"Program folder  : {APP_DIR}")
    print(f"Session log     : {ACTIVE_SESSION_LOG_PATH or '(logging unavailable)'}")
    print(f"Python          : {sys.version.split()[0]}")
    print(f"Cache snapshots : {cache_count}")
    print()
    assignments=[a for a in (cfg.get("cockpit_assignments") or []) if package_for_assignment(a)]
    if not assignments:
        print("No Reactive Cockpit package is assigned to any ship yet.")
        print("Run INSTALL_REACTIVE_COCKPIT.bat first.")
        input("\nPress Enter to close..."); return

    required=[]
    for a in assignments:
        package=package_for_assignment(a) or {}
        base=str(package.get("base_theme") or "").strip()
        if base: required.append(base)
        required.extend([t for t in assignment_slots(a).values() if t])
    missing=[]; seen=set()
    for name in required:
        key=normalized(name)
        if key in seen: continue
        seen.add(key)
        if find_cached_theme(name) is None: missing.append(name)
    if missing:
        master_index = load_json(BUNDLED_MASTER_CACHE_DIR / "index.json", {"themes": {}})
        master_themes = master_index.get("themes", {}) if isinstance(master_index, dict) else {}
        bundled_missing = [name for name in missing if name in master_themes]
        custom_missing = [name for name in missing if name not in master_themes]

        if bundled_missing:
            print("ERROR: bundled tested snapshot(s) could not be restored:")
            for name in bundled_missing:
                print(f"  - {name}")
            print("Run DIAGNOSTICS.bat and send the session log. Do not recapture these.")

        if custom_missing:
            print("Reactive Cockpit custom/replaced theme(s) still need capture:")
            for name in custom_missing:
                print(f"  - {name}")
            print("\nRun UPDATE_THEME_CACHE.bat or INSTALL_AND_CAPTURE.bat.")

        input("\nPress Enter to close..."); return

    missing_aux=[t for t in configured_auxiliary_themes(cfg) if find_cached_theme(t) is None]
    if missing_aux:
        print("NOTE: shared Fighter/SRV theme snapshot(s) are not captured yet:")
        for name in missing_aux: print(f"  - {name}")
        print("Main-ship Reactive Cockpits will still run normally.")
        print("Run UPDATE_THEME_CACHE.bat before testing Fighter/SRV cockpit switching.")
        print()

    shipyard = load_shipyard(shipyard_path)
    ship = reconstruct_current_ship(journal_dir)

    status_file = journal_dir / "Status.json"
    initial_status = read_status(status_file) or {}

    runtime = Runtime(
        cfg=cfg,
        edhm_ini=edhm_ini,
        shipyard_path=shipyard_path,
        journal_dir=journal_dir,
        shipyard=shipyard,
        ship=ship,
    )

    try:
        runtime.shipyard_mtime = shipyard_path.stat().st_mtime_ns
    except OSError:
        pass

    runtime.update_status(initial_status)

    # v1.0 RC compatibility warning: bundled live snapshots are generated by
    # an installed EDHM core and can theoretically need regeneration after an
    # EDHM update. Warn before Reactive Cockpit overwrites the live signal file.
    live_before = load_json(edhm_ini / SIGNAL_FILE, {})
    live_version = str(live_before.get("version") or "") if isinstance(live_before, dict) else ""
    cache_probe = next((t for a in assignments for t in assignment_slots(a).values() if t and find_cached_theme(t)), "")
    if not cache_probe:
        cache_probe = next((t for t in configured_auxiliary_themes(cfg) if find_cached_theme(t)), "")
    cache_version = get_cached_theme_version(cache_probe)
    if live_version and cache_version and live_version != cache_version:
        print()
        print("WARNING: EDHM live/cache version difference detected:")
        print(f"  EDHM live   : {live_version}")
        print(f"  Reactive Cockpit cache: {cache_version}")
        print("The controller can still run, but if themes behave incorrectly after")
        print("an EDHM update, recapture them with INSTALL_AND_CAPTURE.bat.")
        print()

    print("Installed cockpit assignments:")
    for a in assignments:
        print(f"  - {assignment_label(a)}")
    print("Shared auxiliary cockpits:")
    print(f"  - Fighter Base   : {auxiliary_slots(cfg,'fighter').get('base')}")
    print(f"  - Fighter Combat : {auxiliary_slots(cfg,'fighter').get('combat')}")
    print(f"  - SRV Base       : {auxiliary_slots(cfg,'srv').get('base')}")
    print(f"Shipyard        : {shipyard_path}")
    print(f"Journal         : {journal_dir}")
    print(f"Combat decay   : {cfg['combat_cooldown_seconds']:.0f}s")
    print(f"Wanted entry   : {cfg['wanted_entry_seconds']:.0f}s")
    print(f"Glide exit     : {cfg['glide_exit_cooldown_seconds']:.0f}s")
    print(f"Supercruise exit: {cfg['supercruise_exit_cooldown_seconds']:.0f}s")
    print(f"Dock completion: {cfg['docking_complete_cooldown_seconds']:.0f}s")
    print(
        f"Hull danger    : <{cfg['deep_danger_hull_threshold'] * 100:.0f}% Deep Danger; "
        f"<{cfg['critical_hull_threshold'] * 100:.0f}% Imminent Destruction"
    )
    print(f"Low fuel       : <{cfg['low_fuel_threshold'] * 100:.0f}% exact calculation")
    print("Heat           : >100% (Elite official telemetry limit), immediate recovery")
    print()
    print("IMPORTANT: keep EDHM UI CLOSED while this controller is running.")
    print(f"DISCO MODE: press {DISCO_HOTKEY} to toggle the lighting override.")
    print("Press Ctrl+C to stop.")
    print()
    print(f"[{stamp()}] Current ship -> {ship.friendly()}")
    print("-" * 82)

    tail = JournalTail(journal_dir)
    tail.prime()

    last_status_mtime = None
    disco_hotkey = DiscoHotkey()

    try:
        while True:
            runtime.refresh_shipyard_if_changed()

            for event in tail.events():
                runtime.process_event(event)

            try:
                mtime = status_file.stat().st_mtime_ns
            except OSError:
                mtime = None

            if mtime is not None and mtime != last_status_mtime:
                last_status_mtime = mtime
                status = read_status(status_file)

                if status is not None:
                    runtime.update_status(status)

            if disco_hotkey.poll():
                runtime.toggle_disco()

            runtime.apply_desired()
            time.sleep(0.10)

    except KeyboardInterrupt:
        print()
        print(f"[{stamp()}] Reactive Cockpit stopped.")
    finally:
        was_disco = runtime.disco_active
        runtime.stop_disco()
        if was_disco:
            runtime.apply_desired()


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def diagnostics():
    heading(f"VRFlorian & Guardian Arthur's - Reactive Cockpit — Diagnostics v{VERSION}")

    cfg = load_config()
    edhm_ini = choose_edhm_ini(cfg)
    shipyard_path = choose_shipyard(cfg)
    journal_dir = choose_journal_dir(cfg)

    shipyard = load_shipyard(shipyard_path)
    ship = reconstruct_current_ship(journal_dir)
    status = read_status(journal_dir / "Status.json") or {}

    flags = int(status.get("Flags", 0))
    flags2 = int(status.get("Flags2", 0))
    on_foot=bool(flags2 & FLAG2_ON_FOOT)
    in_taxi=bool(flags2 & FLAG2_IN_TAXI)
    in_multicrew=bool(flags2 & FLAG2_IN_MULTICREW)
    on_foot_station=bool(flags2 & FLAG2_ON_FOOT_IN_STATION)
    on_foot_hangar=bool(flags2 & FLAG2_ON_FOOT_IN_HANGAR)
    on_foot_social=bool(flags2 & FLAG2_ON_FOOT_SOCIAL_SPACE)

    fuel_main = None
    fuel = status.get("Fuel")

    if isinstance(fuel, dict):
        try:
            fuel_main = float(fuel.get("FuelMain"))
        except (TypeError, ValueError):
            pass

    fuel_pct = None

    if (
        fuel_main is not None
        and ship.fuel_capacity_main is not None
        and ship.fuel_capacity_main > 0
    ):
        fuel_pct = fuel_main / ship.fuel_capacity_main

    print(f"EDHM live theme : {live_theme_name(edhm_ini) or '(unknown)'}")
    print(f"Shipyard        : {shipyard_path}")
    print(f"Journal         : {journal_dir}")
    print()
    print(f"Current ship    : {ship.friendly()}")

    matched = match_shipyard_ship(ship, shipyard)
    print(f"Shipyard base   : {matched.get('theme') if matched else 'NO UNIQUE MATCH'}")
    current_assignment = assignment_for_ship_entry(cfg, matched, shipyard)
    print(f"Current package : {assignment_label(current_assignment) if current_assignment else 'NONE FOR THIS SHIP'}")
    print(f"Current reactive: {bool(current_assignment)}")
    print("Installed packages:")
    for a in cfg.get("cockpit_assignments") or []:
        if package_for_assignment(a):
            print(f"  - {assignment_label(a)}")

    print(
        f"Hull            : {ship.hull_health * 100:.1f}%"
        if ship.hull_health is not None
        else "Hull            : unknown"
    )
    print(
        f"Canopy          : {ship.canopy_health * 100:.1f}%"
        if ship.canopy_health is not None
        else "Canopy          : unknown live health"
    )
    print(
        f"Fuel capacity   : {ship.fuel_capacity_main:.2f}t"
        if ship.fuel_capacity_main is not None
        else "Fuel capacity   : unknown"
    )
    print(
        f"Fuel current    : {fuel_main:.2f}t"
        if fuel_main is not None
        else "Fuel current    : unknown"
    )
    print(
        f"Fuel percentage : {fuel_pct * 100:.1f}%"
        if fuel_pct is not None
        else "Fuel percentage : unknown"
    )

    print()
    print(f"On foot         : {on_foot}")
    print(f"In taxi         : {in_taxi}")
    print(f"In multicrew    : {in_multicrew}")
    print(f"On foot station : {on_foot_station}")
    print(f"On foot hangar  : {on_foot_hangar}")
    print(f"On foot social  : {on_foot_social}")
    print(f"Main ship       : {bool(flags & FLAG_IN_MAIN_SHIP)}")
    print(f"Fighter         : {bool(flags & FLAG_IN_FIGHTER)}")
    print(f"SRV             : {bool(flags & FLAG_IN_SRV)}")
    print(f"Supercruise     : {bool(flags & FLAG_SUPERCRUISE)}")
    print(f"Silent running  : {bool(flags & FLAG_SILENT_RUNNING)}")
    print(f"Scooping fuel   : {bool(flags & FLAG_SCOOPING_FUEL)}")
    print(f"Landing gear    : {bool(flags & FLAG_LANDING_GEAR_DOWN)}")
    print(f"Shields up      : {bool(flags & FLAG_SHIELDS_UP)}")
    print(f"IsInDanger      : {bool(flags & FLAG_IS_IN_DANGER)}")
    print(f"Interdicted     : {bool(flags & FLAG_BEING_INTERDICTED)}")
    print(f"Overheating     : {bool(flags & FLAG_OVERHEATING)}")
    print(f"Glide mode      : {bool(flags2 & FLAG2_GLIDE_MODE)}")
    print(
        f"Elite LowFuel   : {bool(flags & FLAG_LOW_FUEL_GAME)} "
        "(<25%; Reactive Cockpit uses exact <20%)"
    )

    print()
    print("Shared auxiliary themes:")
    print(f"  Fighter Base   : {auxiliary_slots(cfg,'fighter').get('base')}")
    print(f"  Fighter Combat : {auxiliary_slots(cfg,'fighter').get('combat')}")
    print(f"  SRV Base       : {auxiliary_slots(cfg,'srv').get('base')}")

    print()
    print("Theme cache:")

    index = load_cache_index()

    if not index["themes"]:
        print("  (empty)")
    else:
        for name, item in index["themes"].items():
            print(f"  {name} [{item.get('role', '?')}]")

    input("\nPress Enter to close...")


# ---------------------------------------------------------------------------
# Internal state priority self-test
# ---------------------------------------------------------------------------

def selftest():
    # Matching tests intentionally exercise identity-refresh paths which normally
    # persist repaired assignments. Never let test fixtures alter the real player
    # configuration or contaminate a clean distribution build.
    global _CONFIG_WRITES_ENABLED
    _CONFIG_WRITES_ENABLED = False
    cfg=json.loads(json.dumps(DEFAULT_CONFIG))

    hotkey = DiscoHotkey()
    hotkey.user32 = None
    assert hotkey.edge(False) is False
    assert hotkey.edge(True) is True
    assert hotkey.edge(True) is False
    assert hotkey.edge(False) is False
    assert hotkey.edge(True) is True

    # Only the nine RGB constants may change; the live signal contents and all
    # unrelated cockpit values remain untouched.
    with tempfile.TemporaryDirectory(prefix="reactive_cockpit_disco_test_") as folder:
        live = Path(folder)
        advanced = live / "Advanced.ini"
        trigger = live / SIGNAL_FILE
        original_lines = ["[Constants]"]
        for key in ("x190", "y190", "z190", "x69", "y69", "z69", "x70", "y70", "z70"):
            original_lines.append(f"{key} = 0.123")
        original_lines.extend(["untouched = 42", "", "[Another Section]", "value = preserved", ""])
        advanced.write_text("\n".join(original_lines), encoding="utf-8")
        signal_bytes = b'{"credits":{"theme":"Selftest Base"}}\n'
        trigger.write_bytes(signal_bytes)
        marker = live / "runtime" / "disco_mode.active"

        apply_disco_palette(live, DISCO_PALETTES[0])
        changed = advanced.read_text(encoding="utf-8")
        expected = {
            key: number
            for family, colour in zip(("ambient", "primary", "secondary"), DISCO_PALETTES[0][1:])
            for key, number in zip(DISCO_COLOR_KEYS[family], _hex_rgb(colour))
        }
        for key, number in expected.items():
            match = re.search(rf"^{key}\s*=\s*([^\r\n]+)$", changed, re.MULTILINE)
            assert match and abs(float(match.group(1)) - number) < 0.000001, (key, match)
        assert "untouched = 42" in changed and "value = preserved" in changed
        assert trigger.read_bytes() == signal_bytes

        set_disco_marker(True, marker)
        assert disco_mode_active(marker)
        set_disco_marker(False, marker)
        assert not disco_mode_active(marker)

        mini = Runtime(
            cfg=cfg, edhm_ini=live, shipyard_path=live / "Shipyard_v3.json",
            journal_dir=live, shipyard={"ships": []}, ship=ShipState(),
            disco_marker=marker,
        )
        mini.applied_key = ("BASE", "selftest base")
        assert mini.toggle_disco(now=10.0) is True
        assert disco_mode_active(marker)
        mini.apply_disco(now=10.0)
        assert mini.disco_index == 1
        assert mini.toggle_disco(now=12.0) is False
        assert not disco_mode_active(marker)
        assert mini.applied_key is None

    snow_selector={"shipyard_id":51,"kind_short":"corsair","kind_full":"Corsair","name":"snow","plate":"VRFLO7"}
    rocky_selector={"shipyard_id":53,"kind_short":"lakonminer","kind_full":"Type 11 Prospector","name":"Rocky 5","plate":"VRFLO"}
    anaconda_selector={"shipyard_id":5,"kind_short":"anaconda","kind_full":"Anaconda","name":"S.S. victorious","plate":"VRFLO"}
    python_selector={"shipyard_id":61,"kind_short":"python_nx","kind_full":"Python Mk II","name":"Python Test","plate":"VRFLO"}
    caspian_selector={"shipyard_id":71,"kind_short":"caspianexplorer","kind_full":"Caspian Explorer","name":"Caspian Test","plate":"VRFLO"}
    nebula_selector={"shipyard_id":72,"kind_short":"anaconda","kind_full":"Anaconda","name":"Nebula Hopper","plate":"VRFLO"}
    kestrel_selector={"shipyard_id":73,"kind_short":"kestrel","kind_full":"Kestrel Mk 2","name":"Heart Eater","plate":"VRFLO"}

    cfg["cockpit_assignments"]=[
        {
            "package_id":"battle_corsair_snow_vrflorian",
            "ship":snow_selector,
            "theme_slots":dict(COCKPIT_PACKAGES["battle_corsair_snow_vrflorian"]["theme_names"]),
        },
        {
            "package_id":"type11_rocky5_vrflorian",
            "ship":rocky_selector,
            "theme_slots":dict(COCKPIT_PACKAGES["type11_rocky5_vrflorian"]["theme_names"]),
        },
        {
            "package_id":"type11_rocky5_vrflorian",
            "ship":anaconda_selector,
            "theme_slots":dict(COCKPIT_PACKAGES["type11_rocky5_vrflorian"]["theme_names"]),
        },
        {
            "package_id":"python_mkii_vrflorian",
            "ship":python_selector,
            "theme_slots":dict(COCKPIT_PACKAGES["python_mkii_vrflorian"]["theme_names"]),
        },
        {
            "package_id":"caspian_explorer_vrflorian",
            "ship":caspian_selector,
            "theme_slots":dict(COCKPIT_PACKAGES["caspian_explorer_vrflorian"]["theme_names"]),
        },
        {
            "package_id":"nebula_hopper_anaconda_vrflorian",
            "ship":nebula_selector,
            "theme_slots":dict(COCKPIT_PACKAGES["nebula_hopper_anaconda_vrflorian"]["theme_names"]),
        },
        {
            "package_id":"kestrel_heart_eater_vrflorian",
            "ship":kestrel_selector,
            "theme_slots":dict(COCKPIT_PACKAGES["kestrel_heart_eater_vrflorian"]["theme_names"]),
        },
    ]

    shipyard={"ships":[
        {"ship_id":51,"kind_short":"corsair","kind_full":"Corsair","name":"snow","plate":"VRFLO7","theme":"VRFlo Reactive Cockpit - Base"},
        {"ship_id":53,"kind_short":"lakonminer","kind_full":"Type 11 Prospector","name":"Rocky 5","plate":"VRFLO","theme":"Type 11 - Reactive Cockpit Base"},
        {"ship_id":5,"kind_short":"anaconda","kind_full":"Anaconda","name":"S.S. victorious","plate":"VRFLO","theme":"Type 11 - Reactive Cockpit Base"},
        {"ship_id":61,"kind_short":"python_nx","kind_full":"Python Mk II","name":"Python Test","plate":"VRFLO","theme":"Python MKII - Reactive Cockpit Base"},
        {"ship_id":71,"kind_short":"caspianexplorer","kind_full":"Caspian Explorer","name":"Caspian Test","plate":"VRFLO","theme":"Caspian - Reactive Cockpit base"},
        {"ship_id":72,"kind_short":"anaconda","kind_full":"Anaconda","name":"Nebula Hopper","plate":"VRFLO","theme":"Nebula Hopper Anaconda - Reactive Cockpit Base"},
        {"ship_id":73,"kind_short":"kestrel","kind_full":"Kestrel Mk 2","name":"Heart Eater","plate":"VRFLO","theme":"Kestrel (heart eater) Reactive Cockpit - Base"},
        {"ship_id":7,"kind_short":"sidewinder","kind_full":"Sidewinder","name":"Explorer 1","plate":"VRFLO","theme":"Crimson Baron"},
    ]}

    fake=Runtime(
        cfg=cfg,edhm_ini=Path('.'),shipyard_path=Path('.'),journal_dir=Path('.'),
        shipyard=shipyard,
        ship=ShipState(
            ship_type='corsair',ship_id=22,ship_name='snow',ship_ident='VRFLO7',
            hull_health=1.0,canopy_health=1.0,fuel_capacity_main=32.0,
            has_shield_generator=True,
        ),
    )
    fake.in_main_ship=True
    fake.shields_up=True
    fake.fuel_main=32.0

    def result():
        return fake.desired(time.monotonic())

    # Base -> Disco -> underlying Combat -> OFF must restore Combat, not Base.
    with tempfile.TemporaryDirectory(prefix="reactive_cockpit_disco_restore_") as folder:
        live = Path(folder)
        base_theme = COCKPIT_PACKAGES["battle_corsair_snow_vrflorian"]["base_theme"]
        base_source = find_cached_theme(base_theme)
        assert base_source is not None
        for filename in CORE_FILES:
            source = base_source / filename
            if source.exists():
                shutil.copyfile(source, live / filename)
        dance = Runtime(
            cfg=cfg, edhm_ini=live, shipyard_path=live / "Shipyard_v3.json",
            journal_dir=live, shipyard=shipyard,
            ship=ShipState(
                ship_type="corsair", ship_name="snow", ship_ident="VRFLO7",
                hull_health=1.0, canopy_health=1.0, fuel_capacity_main=32.0,
                has_shield_generator=True,
            ),
            disco_marker=live / "runtime" / "disco_mode.active",
        )
        dance.in_main_ship = True
        dance.shields_up = True
        dance.fuel_main = 32.0
        dance.apply_desired()
        assert dance.applied_key and dance.applied_key[0] == "BASE"
        dance.toggle_disco(now=100.0)
        dance.apply_disco(now=100.0)
        dance.status_interdicted = True
        assert dance.desired(101.0)[0] == "COMBAT"
        dance.apply_desired()
        assert dance.applied_key and dance.applied_key[0] == "BASE"
        dance.toggle_disco(now=102.0)
        dance.apply_desired()
        assert dance.applied_key and dance.applied_key[0] == "COMBAT"
        assert live_theme_name(live) == "VRFlo Reactive Cockpit - Combat"

    # Basic Snow package behavior.
    assert result()[0]=='BASE',result()
    fake.supercruise=True
    assert result()[0]=='SUPERCRUISE',result()
    fake.supercruise=False
    fake.status_interdicted=True
    assert result()[0]=='COMBAT',result()
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12

    # Type-11 V2 is now a complete eight-state authored cockpit.
    fake.ship=ShipState(
        ship_type='lakonminer',ship_id=99,ship_name='Rocky 5',ship_ident='VRFLO',
        hull_health=1.0,canopy_health=1.0,fuel_capacity_main=64.0,
        has_shield_generator=True,
    )
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.fuel_main=64.0
    fake.supercruise=True
    fake.scooping_fuel=True
    r=result()
    assert r[0]=='SCOOP' and r[1]=='Type 11 Reactive Cockpit - Scoop',r

    fake.scooping_fuel=False
    fake.status_interdicted=True
    r=result()
    assert r[0]=='COMBAT' and r[1]=='Type 11 Reactive Cockpit - Combat mode',r
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12

    fake.supercruise=False
    fake.overheating=True
    r=result()
    assert r[0]=='DEEP_DANGER' and r[1]=='Type 11 reactive cockpit - Deep Danger mode',r

    fake.overheating=False
    fake.silent_running=True
    r=result()
    assert r[0]=='STEALTH' and r[1]=='Type 11 Reactive Cockpit - Stealth',r
    fake.silent_running=False

    fake.ship.hull_health=0.10
    r=result()
    assert r[0]=='CRITICAL' and r[1]=='Type 11 Reactive Cockpit - Imminent destruction',r
    fake.ship.hull_health=1.0

    # Regression reported on an Anaconda:
    # 89% hull + fuel scoop + ordinary close-star IsInDanger must NOT be Combat.
    # The generic flag is environmental here; Scoop remains the authored state.
    fake.ship=ShipState(
        ship_type='anaconda',ship_id=1,ship_name='S.S. victorious',ship_ident='VRFLO',
        hull_health=0.89,canopy_health=1.0,fuel_capacity_main=32.0,
        has_shield_generator=True,
    )
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.shields_up=True
    fake.fuel_main=20.0
    fake.supercruise=True
    fake.scooping_fuel=True
    fake.overheating=False
    fake.status_danger=True
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    r=result()
    assert r[0]=='SCOOP' and r[1]=='Type 11 Reactive Cockpit - Scoop',r

    # The same environmental IsInDanger cannot create Combat if Scoop is an
    # intentionally empty slot; the resolver falls through to Supercruise.
    anaconda_assignment=cfg["cockpit_assignments"][2]
    saved_scoop=anaconda_assignment["theme_slots"]["scoop"]
    anaconda_assignment["theme_slots"]["scoop"]=None
    r=result()
    assert r[0]=='SUPERCRUISE' and r[1]=='Type 11 - Reactive Cockpit super-cruise',r
    anaconda_assignment["theme_slots"]["scoop"]=saved_scoop

    # A confirmed combat seed still outranks Scoop. Generic IsInDanger is then
    # allowed to maintain that already-established combat context.
    fake.process_event({"event":"UnderAttack"})
    r=result()
    assert r[0]=='COMBAT' and r[1]=='Type 11 Reactive Cockpit - Combat mode',r

    # Shield loss by itself no longer seeds Combat/Deep Danger. During ordinary
    # scooping it remains Scoop; once genuine combat exists, shields-down escalates.
    fake.last_danger_signal=-1e12
    fake.status_danger=True
    fake.shields_up=False
    r=result()
    assert r[0]=='SCOOP',r
    fake.process_event({"event":"UnderAttack"})
    r=result()
    assert r[0]=='DEEP_DANGER',r
    fake.shields_up=True
    fake.last_danger_signal=-1e12
    fake.status_danger=False

    # >100% OverHeating is still a genuine Deep Danger condition and remains
    # independent of the generic IsInDanger classification.
    fake.overheating=True
    r=result()
    assert r[0]=='DEEP_DANGER',r
    fake.overheating=False

    # Python Mk II full eight-state package.
    fake.ship=ShipState(
        ship_type='python_nx',ship_id=61,ship_name='Python Test',ship_ident='VRFLO',
        hull_health=1.0,canopy_health=1.0,fuel_capacity_main=32.0,
        has_shield_generator=True,
    )
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.in_main_ship=True
    fake.in_fighter=False
    fake.in_srv=False
    fake.shields_up=True
    fake.fuel_main=32.0
    fake.status_danger=False
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    fake.scooping_fuel=False
    fake.supercruise=False
    fake.glide_mode=False
    fake.overheating=False
    fake.silent_running=False
    fake.docking_active=False
    fake.docking_hold_until=-1e12
    fake.supercruise_hold_until=-1e12
    fake.glide_hold_until=-1e12
    fake.wanted_until=-1e12

    r=result()
    assert r[0]=='BASE' and r[1]=='Python MKII - Reactive Cockpit Base',r

    fake.supercruise=True
    r=result()
    assert r[0]=='SUPERCRUISE' and r[1]=='Python MKII - Reactive Cockpit Super cruise',r

    fake.scooping_fuel=True
    r=result()
    assert r[0]=='SCOOP' and r[1]=='Python MKII - Reactive Cockpit Scoop',r

    fake.scooping_fuel=False
    fake.status_interdicted=True
    r=result()
    assert r[0]=='COMBAT' and r[1]=='Python MKII - Reactive Cockpit Combat',r
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    fake.supercruise=False

    fake.overheating=True
    r=result()
    assert r[0]=='DEEP_DANGER' and r[1]=='Python MKII - Reactive Cockpit Deep danger',r
    fake.overheating=False

    fake.silent_running=True
    r=result()
    assert r[0]=='STEALTH' and r[1]=='Python MKII - Reactive Cockpit Stealth',r
    fake.silent_running=False

    fake.ship.hull_health=0.10
    r=result()
    assert r[0]=='CRITICAL' and r[1]=='Python MKII - Reactive Cockpit Imminent Destruction',r
    fake.ship.hull_health=1.0

    # Caspian Explorer full eight-state package.
    fake.ship=ShipState(
        ship_type='caspianexplorer',ship_id=71,ship_name='Caspian Test',ship_ident='VRFLO',
        hull_health=1.0,canopy_health=1.0,fuel_capacity_main=32.0,
        has_shield_generator=True,
    )
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.in_main_ship=True
    fake.in_fighter=False
    fake.in_srv=False
    fake.shields_up=True
    fake.fuel_main=32.0
    fake.status_danger=False
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    fake.scooping_fuel=False
    fake.supercruise=False
    fake.glide_mode=False
    fake.overheating=False
    fake.silent_running=False
    fake.docking_active=False
    fake.docking_hold_until=-1e12
    fake.supercruise_hold_until=-1e12
    fake.glide_hold_until=-1e12
    fake.wanted_until=-1e12

    r=result()
    assert r[0]=='BASE' and r[1]=='Caspian - Reactive Cockpit base',r

    fake.supercruise=True
    r=result()
    assert r[0]=='SUPERCRUISE' and r[1]=='Caspian - Reactive Cockpit Super Cruise',r

    fake.scooping_fuel=True
    r=result()
    assert r[0]=='SCOOP' and r[1]=='Caspian - Reactive Cockpit Scoop',r

    fake.scooping_fuel=False
    fake.status_interdicted=True
    r=result()
    assert r[0]=='COMBAT' and r[1]=='Caspian - Reactive Cockpit Combat',r
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    fake.supercruise=False

    fake.overheating=True
    r=result()
    assert r[0]=='DEEP_DANGER' and r[1]=='Caspian Reactive Cockpit Deep danger',r
    fake.overheating=False

    fake.silent_running=True
    r=result()
    assert r[0]=='STEALTH' and r[1]=='Caspian - Reactive Cockpit Stealth',r
    fake.silent_running=False

    fake.ship.hull_health=0.10
    r=result()
    assert r[0]=='CRITICAL' and r[1]=='Caspian - Reactive Cockpit Imminent destruction',r
    fake.ship.hull_health=1.0

    # Nebula Hopper Anaconda full eight-state package.
    fake.ship=ShipState(
        ship_type='anaconda',ship_id=72,ship_name='Nebula Hopper',ship_ident='VRFLO',
        hull_health=1.0,canopy_health=1.0,fuel_capacity_main=32.0,
        has_shield_generator=True,
    )
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.in_main_ship=True
    fake.in_fighter=False
    fake.in_srv=False
    fake.shields_up=True
    fake.fuel_main=32.0
    fake.status_danger=False
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    fake.scooping_fuel=False
    fake.supercruise=False
    fake.glide_mode=False
    fake.overheating=False
    fake.silent_running=False
    fake.docking_active=False
    fake.docking_hold_until=-1e12
    fake.supercruise_hold_until=-1e12
    fake.glide_hold_until=-1e12
    fake.wanted_until=-1e12

    r=result()
    assert r[0]=='BASE' and r[1]=='Nebula Hopper Anaconda - Reactive Cockpit Base',r

    fake.supercruise=True
    r=result()
    assert r[0]=='SUPERCRUISE' and r[1]=='Nebula Hopper Anaconda - Reactive Cockpit super-cruise',r

    fake.scooping_fuel=True
    r=result()
    assert r[0]=='SCOOP' and r[1]=='Nebula Hopper Anaconda Reactive Cockpit - Scoop',r

    fake.scooping_fuel=False
    fake.status_interdicted=True
    r=result()
    assert r[0]=='COMBAT' and r[1]=='Nebula Hopper Anaconda Reactive Cockpit - Combat mode',r
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    fake.supercruise=False

    fake.overheating=True
    r=result()
    assert r[0]=='DEEP_DANGER' and r[1]=='Nebula Hopper Anaconda reactive cockpit - Deep Danger mode',r
    fake.overheating=False

    fake.silent_running=True
    r=result()
    assert r[0]=='STEALTH' and r[1]=='Nebula Hopper Anaconda Reactive Cockpit - Stealth',r
    fake.silent_running=False

    fake.ship.hull_health=0.10
    r=result()
    assert r[0]=='CRITICAL' and r[1]=='Nebula Hopper Anaconda Reactive Cockpit - Imminent destruction',r
    fake.ship.hull_health=1.0

    # Kestrel full eight-state package.
    fake.ship=ShipState(
        ship_type='kestrel',ship_id=73,ship_name='Heart Eater',ship_ident='VRFLO',
        hull_health=1.0,canopy_health=1.0,fuel_capacity_main=32.0,
        has_shield_generator=True,
    )
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.in_main_ship=True
    fake.in_fighter=False
    fake.in_srv=False
    fake.shields_up=True
    fake.fuel_main=32.0
    fake.status_danger=False
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    fake.scooping_fuel=False
    fake.supercruise=False
    fake.glide_mode=False
    fake.overheating=False
    fake.silent_running=False
    fake.docking_active=False
    fake.docking_hold_until=-1e12
    fake.supercruise_hold_until=-1e12
    fake.glide_hold_until=-1e12
    fake.wanted_until=-1e12

    r=result()
    assert r[0]=='BASE' and r[1]=='Kestrel (heart eater) Reactive Cockpit - Base',r

    fake.supercruise=True
    r=result()
    assert r[0]=='SUPERCRUISE' and r[1]=='Kestrel (Heart eater) Reactive Cockpit - Super Cruise mode',r

    fake.scooping_fuel=True
    r=result()
    assert r[0]=='SCOOP' and r[1]=='Kestrel (Heart eater) Reactive Cockpit - Scoop mode',r

    fake.scooping_fuel=False
    fake.status_interdicted=True
    r=result()
    assert r[0]=='COMBAT' and r[1]=='Kestrel (Heart eater) Reactive Cockpit - Combat mode',r
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    fake.supercruise=False

    fake.overheating=True
    r=result()
    assert r[0]=='DEEP_DANGER' and r[1]=='Kestrel (Heart eater) Reactive Cockpit - Deep damage mode',r
    fake.overheating=False

    fake.silent_running=True
    r=result()
    assert r[0]=='STEALTH' and r[1]=='Kestrel (Heart eater) Reactive Cockpit - Stealth mode',r
    fake.silent_running=False

    fake.ship.hull_health=0.40
    r=result()
    assert r[0]=='DEEP_DANGER' and r[1]=='Kestrel (Heart eater) Reactive Cockpit - Deep damage mode',r

    fake.ship.hull_health=0.10
    r=result()
    assert r[0]=='CRITICAL' and r[1]=='Kestrel (Heart eater) Reactive Cockpit - Imminent Destruction',r
    fake.ship.hull_health=1.0

    # Shared Fighter cockpit: Base while idle, Combat only after a genuine
    # combat seed, same 15-second release logic as a normal ship. The fighter
    # is launched from Snow, so the parent ship is reactive.
    fake.ship=ShipState(
        ship_type='corsair',ship_id=22,ship_name='snow',ship_ident='VRFLO7',
        hull_health=1.0,canopy_health=1.0,fuel_capacity_main=32.0,
        has_shield_generator=True,
    )
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.in_main_ship=False
    fake.in_srv=False
    fake.in_fighter=True
    fake.auxiliary_parent_reactive=True
    fake.status_danger=True
    fake.status_interdicted=False
    fake.last_danger_signal=-1e12
    r=result()
    assert r[0]=='FIGHTER_BASE' and r[1]=='Ship Launched Fighter - Reactive Cockpit Base',r

    # Generic IsInDanger alone is not fighter Combat.
    fake.status_danger=True
    r=result()
    assert r[0]=='FIGHTER_BASE',r

    fake.process_event({"event":"UnderAttack"})
    r=result()
    assert r[0]=='FIGHTER_COMBAT' and r[1]=='Ship Launched Fighter - Reactive Cockpit Combat',r

    # Still inside the common combat release delay.
    fake.status_danger=False
    fake.last_danger_signal=time.monotonic() - (fake.combat_cooldown - 1.0)
    r=result()
    assert r[0]=='FIGHTER_COMBAT',r

    # Delay expired -> Fighter Base.
    fake.last_danger_signal=time.monotonic() - (fake.combat_cooldown + 1.0)
    r=result()
    assert r[0]=='FIGHTER_BASE',r

    # Fighter hull does not invoke ship Critical/Deep-Danger themes: this
    # auxiliary design intentionally has only Base and Combat.
    fake.fighter_hull_health=0.05
    r=result()
    assert r[0]=='FIGHTER_BASE',r

    # SRV has exactly one authored state: Base. Combat/danger signals do not
    # change it to a mothership theme.
    fake.in_fighter=False
    fake.in_srv=True
    fake.auxiliary_parent_reactive=True
    fake.process_event({"event":"UnderAttack"})
    r=result()
    assert r[0]=='SRV_BASE' and r[1]=='SRV Reactive Cockpit Base',r

    # Auxiliary cockpit must NOT activate when deployed from a non-reactive ship.
    fake.ship=ShipState(
        ship_type='sidewinder',ship_id=2,ship_name='Explorer 1',ship_ident='VRFLO',
        hull_health=1.0,canopy_health=1.0,fuel_capacity_main=4.0,
        has_shield_generator=True,
    )
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.auxiliary_parent_reactive=False
    fake.last_danger_signal=-1e12
    r=result()
    assert r is None,r

    # Return to Snow's main cockpit restores the Snow package immediately.
    fake.ship=ShipState(
        ship_type='corsair',ship_id=22,ship_name='snow',ship_ident='VRFLO7',
        hull_health=1.0,canopy_health=1.0,fuel_capacity_main=32.0,
        has_shield_generator=True,
    )
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.in_srv=False
    fake.in_fighter=False
    fake.in_main_ship=True
    fake.auxiliary_parent_reactive=False
    fake.status_danger=False
    fake.status_interdicted=False
    fake.scooping_fuel=False
    fake.supercruise=False
    fake.glide_mode=False
    fake.overheating=False
    fake.silent_running=False
    fake.docking_active=False
    fake.docking_hold_until=-1e12
    fake.supercruise_hold_until=-1e12
    fake.glide_hold_until=-1e12
    fake.wanted_until=-1e12
    fake.last_danger_signal=-1e12
    r=result()
    assert r[0]=='BASE' and r[1]=='VRFlo Reactive Cockpit - Base',r

    # Updater must expose every populated slot for every installed assignment,
    # including Snow Docking, so an existing theme can be recaptured/replaced.
    choices=build_update_choices(cfg,shipyard)
    assert any(
        item.get("slot")=="docking"
        and item.get("assignment_index")==0
        and item.get("theme")=="VRFlo Reactive Cockpit - Docking mode"
        for item in choices
    ),"Snow Docking slot missing from updater"
    assert any(
        item.get("slot")=="scoop"
        and item.get("assignment_index")==1
        and item.get("theme")=="Type 11 Reactive Cockpit - Scoop"
        for item in choices
    ),"Type-11 Scoop slot missing from updater"
    assert any(
        item.get("master_package_id")=="python_mkii_vrflorian"
        and item.get("slot")=="supercruise"
        and item.get("theme")=="Python MKII - Reactive Cockpit Super cruise"
        for item in choices
    ),"Python MKII master package missing from updater"
    assert any(
        item.get("master_package_id")=="caspian_explorer_vrflorian"
        and item.get("slot")=="supercruise"
        and item.get("theme")=="Caspian - Reactive Cockpit Super Cruise"
        for item in choices
    ),"Caspian Explorer master package missing from updater"
    assert any(
        item.get("master_package_id")=="nebula_hopper_anaconda_vrflorian"
        and item.get("slot")=="supercruise"
        and item.get("theme")=="Nebula Hopper Anaconda - Reactive Cockpit super-cruise"
        for item in choices
    ),"Nebula Hopper Anaconda master package missing from updater"
    assert any(
        item.get("master_package_id")=="kestrel_heart_eater_vrflorian"
        and item.get("slot")=="supercruise"
        and item.get("theme")=="Kestrel (Heart eater) Reactive Cockpit - Super Cruise mode"
        for item in choices
    ),"Kestrel master package missing from updater"
    assert any(
        item.get("master_package_id")=="classic_elite_vrflorian"
        and item.get("slot")=="supercruise"
        and item.get("theme")=="Classic Reactive Cockpit - Super cruise mode"
        for item in choices
    ),"Classic Elite master package missing from updater"
    assert any(
        item.get("aux_vehicle")=="fighter"
        and item.get("aux_slot")=="base"
        and item.get("theme")=="Ship Launched Fighter - Reactive Cockpit Base"
        for item in choices
    ),"Fighter Base missing from updater"
    assert any(
        item.get("aux_vehicle")=="fighter"
        and item.get("aux_slot")=="combat"
        and item.get("theme")=="Ship Launched Fighter - Reactive Cockpit Combat"
        for item in choices
    ),"Fighter Combat missing from updater"
    assert any(
        item.get("aux_vehicle")=="srv"
        and item.get("aux_slot")=="base"
        and item.get("theme")=="SRV Reactive Cockpit Base"
        for item in choices
    ),"SRV Base missing from updater"

    # v1.7.2: On-foot carrier/station must suppress all ship theme switching,
    # even when stale ship telemetry would otherwise qualify for Deep Danger.
    fake.ship=ShipState(ship_type='caspianexplorer',ship_id=71,ship_name='Caspian Test',ship_ident='VRFLO',hull_health=.89,canopy_health=1.0,fuel_capacity_main=32.0,has_shield_generator=True)
    fake.matched_entry=None; fake.match_attempted_for=None
    fake.in_main_ship=True; fake.in_fighter=False; fake.in_srv=False
    fake.on_foot=True; fake.in_taxi=False; fake.in_multicrew=False
    fake.docked=True; fake.fuel_main=2.0; fake.overheating=True; fake.shields_up=False
    fake.status_danger=True; fake.last_danger_signal=time.monotonic()
    r=result(); assert r is None,r

    # v1.7.3: docking is NOT a magic safety blanket. Genuine ship-condition
    # danger remains active until the condition is actually repaired/resolved.
    fake.on_foot=False; fake.docking_active=False; fake.docking_hold_until=-1e12
    fake.status_danger=False; fake.last_danger_signal=-1e12; fake.status_interdicted=False
    fake.overheating=False; fake.shields_up=True

    # Genuinely low fuel stays Deep Danger even while fully docked.
    fake.ship.hull_health=.89; fake.fuel_main=2.0
    r=result(); assert r[0]=='DEEP_DANGER' and r[1]=='Caspian Reactive Cockpit Deep danger',r

    # Refuel resolves the condition; healthy parked ship settles to Base.
    fake.fuel_main=32.0
    r=result(); assert r[0]=='BASE' and r[1]=='Caspian - Reactive Cockpit base',r

    # Genuine overheating also remains Deep Danger while docked until it clears.
    fake.overheating=True
    r=result(); assert r[0]=='DEEP_DANGER' and r[1]=='Caspian Reactive Cockpit Deep danger',r
    fake.overheating=False

    # Critical hull remains Imminent Destruction on the pad until repaired.
    fake.ship.hull_health=.10
    r=result(); assert r[0]=='CRITICAL' and r[1]=='Caspian - Reactive Cockpit Imminent destruction',r

    # Repair resolves it. A still-active docking completion hold may then show Docking.
    fake.ship.hull_health=.89; fake.docking_hold_until=time.monotonic()+2.0
    r=result(); assert r[0]=='DOCKING' and r[1]=='Caspian - Reactive Cockpit Docking',r

    # But purely contextual danger must NOT override a healthy docked ship.
    fake.docking_hold_until=-1e12
    fake.status_danger=True; fake.last_danger_signal=time.monotonic(); fake.shields_up=False
    r=result(); assert r[0]=='BASE' and r[1]=='Caspian - Reactive Cockpit base',r
    fake.status_danger=False; fake.last_danger_signal=-1e12; fake.shields_up=True

    # Reused Shipyard ID across a different ship type may not select old package.
    collision_ship={"ship_id":53,"kind_short":"explorer_nx","kind_full":"Caspian Explorer","name":"Star Surfer","plate":"VRFLO","theme":"Caspian - Reactive Cockpit base"}
    stale_type11={"package_id":"type11_rocky5_vrflorian","ship":{"shipyard_id":53,"kind_short":"lakonminer","kind_full":"Type 11 Prospector","name":"Rocky 5","plate":"VRFLO"},"theme_slots":dict(COCKPIT_PACKAGES["type11_rocky5_vrflorian"]["theme_names"])}
    correct_caspian={"package_id":"caspian_explorer_vrflorian","ship":{"shipyard_id":99,"kind_short":"explorer_nx","kind_full":"Caspian Explorer","name":"Star Surfer","plate":"VRFLO"},"theme_slots":dict(COCKPIT_PACKAGES["caspian_explorer_vrflorian"]["theme_names"])}
    collision_cfg=json.loads(json.dumps(cfg)); collision_cfg["cockpit_assignments"]=[stale_type11,correct_caspian]
    chosen=assignment_for_ship_entry(collision_cfg,collision_ship,{"ships":[collision_ship]}); assert chosen and chosen["package_id"]=="caspian_explorer_vrflorian",chosen

    # Duplicate exact selectors: latest installed assignment wins.
    duplicate_old=json.loads(json.dumps(correct_caspian)); duplicate_old["package_id"]="type11_rocky5_vrflorian"
    duplicate_new=json.loads(json.dumps(correct_caspian)); duplicate_new["package_id"]="caspian_explorer_vrflorian"
    duplicate_cfg=json.loads(json.dumps(cfg)); duplicate_cfg["cockpit_assignments"]=[duplicate_old,duplicate_new]
    chosen=assignment_for_ship_entry(duplicate_cfg,collision_ship,{"ships":[collision_ship]}); assert chosen and chosen["package_id"]=="caspian_explorer_vrflorian",chosen

    # --------------------------------------------------------------
    # v1.7.8 regression: main-ship hull damage band.
    # Deep Danger below 50%; Critical below 20%.
    # This prevents Base from appearing after Combat decay when the ship
    # is still heavily damaged (live report: 39.6% hull).
    # --------------------------------------------------------------
    hull_cfg=json.loads(json.dumps(cfg))
    hull_shipyard={"ships":[{
        "ship_id":181,
        "kind_short":"corsair",
        "kind_full":"Corsair",
        "name":"Hull Band Test",
        "plate":"VRFLO7",
        "theme":"VRFlo Reactive Cockpit - Base",
    }]}
    hull_cfg["cockpit_assignments"]=[{
        "package_id":"battle_corsair_snow_vrflorian",
        "ship":{
            "shipyard_id":181,
            "kind_short":"corsair",
            "kind_full":"Corsair",
            "name":"Hull Band Test",
            "plate":"VRFLO7",
        },
        "theme_slots":dict(COCKPIT_PACKAGES["battle_corsair_snow_vrflorian"]["theme_names"]),
    }]
    hullrt=Runtime(
        cfg=hull_cfg,edhm_ini=Path('.'),shipyard_path=Path('.'),journal_dir=Path('.'),
        shipyard=hull_shipyard,
        ship=ShipState(
            ship_type='corsair',ship_id=801,ship_name='Hull Band Test',ship_ident='VRFLO7',
            hull_health=1.0,canopy_health=1.0,fuel_capacity_main=32.0,
            has_shield_generator=True,
        ),
    )
    hullrt.in_main_ship=True
    hullrt.in_fighter=False
    hullrt.in_srv=False
    hullrt.on_foot=False
    hullrt.docked=False
    hullrt.landed=False
    hullrt.fuel_main=32.0
    hullrt.shields_up=True
    hullrt.overheating=False
    hullrt.silent_running=False
    hullrt.status_danger=False
    hullrt.status_interdicted=False
    hullrt.last_danger_signal=-1e12
    hullrt.wanted_until=-1e12
    hullrt.supercruise=False
    hullrt.scooping_fuel=False
    hullrt.docking_active=False
    hullrt.docking_hold_until=-1e12

    # Healthy / 50% exactly: no hull Deep Danger.
    hullrt.ship.hull_health=0.50
    r=hullrt.desired(time.monotonic())
    assert r[0]=='BASE',r

    # First point below 50%: Deep Danger.
    hullrt.ship.hull_health=0.499
    r=hullrt.desired(time.monotonic())
    assert r[0]=='DEEP_DANGER' and r[1]=='VRFlo Reactive Cockpit - Deep danger',r

    # Exact live-test value from the session log.
    hullrt.ship.hull_health=0.396
    r=hullrt.desired(time.monotonic())
    assert r[0]=='DEEP_DANGER' and '39.6%' in r[2],r

    # 20% exactly remains Deep Danger; below 20% becomes Critical.
    hullrt.ship.hull_health=0.20
    r=hullrt.desired(time.monotonic())
    assert r[0]=='DEEP_DANGER',r

    hullrt.ship.hull_health=0.198
    r=hullrt.desired(time.monotonic())
    assert r[0]=='CRITICAL' and r[1]=='VRFlo Reactive Cockpit - IMMINENT DESTRUCTION',r

    # Heavy damage remains authoritative after Combat decay and while docked.
    hullrt.ship.hull_health=0.396
    hullrt.last_danger_signal=-1e12
    hullrt.status_danger=False
    hullrt.docked=True
    r=hullrt.desired(time.monotonic())
    assert r[0]=='DEEP_DANGER',r

    # --------------------------------------------------------------
    # v1.7.5 regression: docking event/status ordering must never create
    # a state that remains latched until Supercruise or another theme overrides it.
    # --------------------------------------------------------------
    docking_shipyard={"ships":[{
        "ship_id":171,"kind_short":"explorer_nx","kind_full":"Caspian Explorer",
        "name":"Docking Test","plate":"VRFLO","theme":"Caspian - Reactive Cockpit base",
    }]}
    docking_cfg=json.loads(json.dumps(DEFAULT_CONFIG))
    docking_cfg["cockpit_assignments"]=[{
        "package_id":"caspian_explorer_vrflorian",
        "ship":{"shipyard_id":171,"kind_short":"explorer_nx","kind_full":"Caspian Explorer",
                "name":"Docking Test","plate":"VRFLO"},
        "theme_slots":dict(COCKPIT_PACKAGES["caspian_explorer_vrflorian"]["theme_names"]),
    }]
    dock=Runtime(
        cfg=docking_cfg,edhm_ini=Path('.'),shipyard_path=Path('.'),journal_dir=Path('.'),
        shipyard=docking_shipyard,
        ship=ShipState(
            ship_type='explorer_nx',ship_id=701,ship_name='Docking Test',ship_ident='VRFLO',
            hull_health=1.0,canopy_health=1.0,fuel_capacity_main=32.0,
            has_shield_generator=True,
        ),
    )
    dock.fuel_main=32.0

    # Start seated and docked. Gear is down, but a parked ship is Base after
    # any completion hold has expired.
    dock.update_status({"Flags":
        FLAG_IN_MAIN_SHIP|FLAG_DOCKED|FLAG_LANDING_GEAR_DOWN|FLAG_SHIELDS_UP,
        "Flags2":0,"Fuel":{"FuelMain":32.0}})
    dock.docking_hold_until=-1e12
    assert dock.desired(time.monotonic())[0]=='BASE',dock.desired(time.monotonic())

    # Normal takeoff: undock while gear remains down -> Docking.
    dock.process_event({"event":"Undocked","PlayerControlled":True})
    assert dock.desired(time.monotonic())[0]=='DOCKING',dock.desired(time.monotonic())

    # Gear up -> only 3s completion hold, then Base.
    dock.update_status({"Flags":FLAG_IN_MAIN_SHIP|FLAG_SHIELDS_UP,
                        "Flags2":0,"Fuel":{"FuelMain":32.0}})
    assert not dock.docking_active
    assert dock.docking_hold_until > time.monotonic()
    dock.docking_hold_until=-1e12
    assert dock.desired(time.monotonic())[0]=='BASE',dock.desired(time.monotonic())

    # Reported race: gear already UP, then late Liftoff arrives.
    # Must never create an indefinite latch.
    dock.landing_gear_down=False
    dock.landed=True
    dock.process_event({"event":"Liftoff","PlayerControlled":True})
    assert not dock.docking_active
    assert dock.docking_hold_until > time.monotonic()
    dock.docking_hold_until=-1e12
    assert dock.desired(time.monotonic())[0]=='BASE',dock.desired(time.monotonic())

    # Landing approach: airborne + gear down -> Docking.
    dock.docked=False; dock.landed=False
    dock.update_status({"Flags":
        FLAG_IN_MAIN_SHIP|FLAG_LANDING_GEAR_DOWN|FLAG_SHIELDS_UP,
        "Flags2":0,"Fuel":{"FuelMain":32.0}})
    assert dock.desired(time.monotonic())[0]=='DOCKING',dock.desired(time.monotonic())

    # Touchdown: 3s Docking hold, then Base even though gear remains down.
    dock.process_event({"event":"Touchdown","PlayerControlled":True})
    assert dock.docking_hold_until > time.monotonic()
    dock.docking_hold_until=-1e12
    assert dock.desired(time.monotonic())[0]=='BASE',dock.desired(time.monotonic())

    # Later docked Status with gear still down cannot re-latch Docking.
    dock.update_status({"Flags":
        FLAG_IN_MAIN_SHIP|FLAG_DOCKED|FLAG_LANDING_GEAR_DOWN|FLAG_SHIELDS_UP,
        "Flags2":0,"Fuel":{"FuelMain":32.0}})
    dock.docking_hold_until=-1e12
    assert not dock.docking_state_active(time.monotonic())
    assert dock.desired(time.monotonic())[0]=='BASE',dock.desired(time.monotonic())

    # Supercruise is a hard physical boundary and clears any impossible stale latch.
    dock.docked=False; dock.landed=False
    dock.docking_active=True
    dock.docking_hold_until=time.monotonic()+99
    dock.update_status({"Flags":
        FLAG_IN_MAIN_SHIP|FLAG_SUPERCRUISE|FLAG_SHIELDS_UP,
        "Flags2":0,"Fuel":{"FuelMain":32.0}})
    assert not dock.docking_active
    assert dock.docking_hold_until < time.monotonic()
    assert dock.desired(time.monotonic())[0]=='SUPERCRUISE',dock.desired(time.monotonic())

    # --------------------------------------------------------------
    # v1.7.4 regression: package assigned to an EDHM ship whose Shipyard base
    # is "Current Settings" must resolve to the package designer Base, NOT leave
    # the previous ship's loaded theme in place.
    # Also simulate EDHM refreshing the renamed ship with a different Shipyard ID.
    # --------------------------------------------------------------
    renamed_ship={
        "ship_id":154,
        "kind_short":"explorer_nx",
        "kind_full":"Caspian Explorer",
        "name":"THE STAR-SURFER",
        "plate":"VRFLO",
        "theme":"Current Settings",
    }
    stale_caspian_assignment={
        "package_id":"caspian_explorer_vrflorian",
        "ship":{
            "shipyard_id":54,
            "kind_short":"explorer_nx",
            "kind_full":"Caspian Explorer",
            "name":"star-born",
            "plate":"VRFLO",
        },
        "theme_slots":dict(COCKPIT_PACKAGES["caspian_explorer_vrflorian"]["theme_names"]),
    }
    rename_cfg=json.loads(json.dumps(cfg))
    rename_cfg["cockpit_assignments"]=[stale_caspian_assignment]
    rename_shipyard={"ships":[renamed_ship]}
    a=assignment_for_ship_entry(rename_cfg,renamed_ship,rename_shipyard)
    assert a and a["package_id"]=="caspian_explorer_vrflorian",a

    # Weak type+plate recovery must NOT guess when two identical current ships exist.
    ambiguous_shipyard={"ships":[
        renamed_ship,
        {"ship_id":155,"kind_short":"explorer_nx","kind_full":"Caspian Explorer","name":"OTHER EXPLORER","plate":"VRFLO","theme":"Current Settings"},
    ]}
    assert assignment_for_ship_entry(rename_cfg,renamed_ship,ambiguous_shipyard) is None

    # Full runtime reproduction of the screenshot sequence:
    # unassigned Rocky 5 loads its Type-11 Shipyard Base, then renamed Caspian
    # with Current Settings must explicitly load Caspian Base.
    screenshot_cfg=json.loads(json.dumps(DEFAULT_CONFIG))
    screenshot_cfg["cockpit_assignments"]=[json.loads(json.dumps(stale_caspian_assignment))]
    screenshot_shipyard={"ships":[
        {"ship_id":53,"kind_short":"lakonminer","kind_full":"Type 11 Prospector","name":"Rocky 5","plate":"VRFLO","theme":"Type 11 - Reactive Cockpit Base"},
        renamed_ship,
    ]}
    ss=Runtime(
        cfg=screenshot_cfg,
        edhm_ini=Path('/tmp/nonexistent-edhm'),
        shipyard_path=Path('/tmp/nonexistent-shipyard'),
        journal_dir=Path('/tmp'),
        shipyard=screenshot_shipyard,
        ship=ShipState(ship_type='lakonminer',ship_id=51,ship_name='Rocky 5',ship_ident='VRFLO',hull_health=1.0,canopy_health=1.0,fuel_capacity_main=128.0,has_shield_generator=True),
    )
    ss.in_main_ship=True; ss.in_fighter=False; ss.in_srv=False
    ss.on_foot=False; ss.in_taxi=False; ss.in_multicrew=False
    ss.docked=False; ss.fuel_main=120.0; ss.overheating=False; ss.shields_up=True
    r=ss.desired(time.monotonic())
    assert r and r[0]=='BASE' and r[1]=='Type 11 - Reactive Cockpit Base',r

    ss.ship=ShipState(ship_type='explorer_nx',ship_id=30,ship_name='THE STAR-SURFER',ship_ident='VRFLO',hull_health=1.0,canopy_health=1.0,fuel_capacity_main=128.0,has_shield_generator=True)
    ss.matched_entry=None; ss.match_attempted_for=None
    ss.fuel_main=120.0; ss.docked=False; ss.overheating=False; ss.shields_up=True
    r=ss.desired(time.monotonic())
    assert r and r[0]=='BASE' and r[1]=='Caspian - Reactive Cockpit base',r
    assert 'designer Base' in r[2],r
    refreshed=screenshot_cfg['cockpit_assignments'][0]['ship']
    assert refreshed['name']=='THE STAR-SURFER',refreshed
    assert str(refreshed['shipyard_id'])=='154',refreshed

    # A completely unassigned ship still never receives reactive overrides.
    fake.ship=ShipState(
        ship_type='sidewinder',ship_id=2,ship_name='Explorer 1',ship_ident='VRFLO',
        hull_health=0.10,canopy_health=0.10,fuel_capacity_main=4.0,
        has_shield_generator=True,
    )
    fake.in_main_ship=True
    fake.in_fighter=False
    fake.in_srv=False
    fake.on_foot=False
    fake.in_taxi=False
    fake.in_multicrew=False
    fake.docked=False
    fake.auxiliary_parent_reactive=False
    fake.matched_entry=None
    fake.match_attempted_for=None
    fake.silent_running=True
    fake.overheating=True
    fake.status_danger=True
    fake.status_interdicted=True
    fake.fuel_main=0.1
    fake.supercruise=False
    fake.scooping_fuel=False
    r=result()
    assert r[0]=='BASE' and r[1]=='Crimson Baron',r

    _CONFIG_WRITES_ENABLED = True
    print('SELFTEST PASS')

def main():
    parser = argparse.ArgumentParser(description="Reactive Cockpit")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["setup", "run", "diagnostics", "selftest", "update", "community_install"],
        default="run",
    )
    args = parser.parse_args()

    if args.mode == "setup":
        setup()
    elif args.mode == "diagnostics":
        diagnostics()
    elif args.mode == "selftest":
        selftest()
    elif args.mode == "update":
        update_themes()
    elif args.mode == "community_install":
        community_install()
    else:
        session_log = start_session_logging()
        try:
            run_controller()
        finally:
            if session_log is not None:
                print()
                print(f"[{stamp()}] Session complete.")
                print(f"Log saved -> {session_log.path}")
                session_log.close()


if __name__ == "__main__":
    main()
