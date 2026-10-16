from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .channels import JsonlTail
from .config import APP_DIR, load_config, resolve_path
from .engine import utc_now
from .storage import append_jsonl


class VisualThemeAdapter:
    """Generic, isolated graph-visual to cached-theme adapter.

    It imports the already configured Reactive Cockpit helper module only in this
    sibling process, uses unique atomic sidecars, and pauses all writes for five
    seconds after a vehicle-context change.
    """

    PRIORITY = {"critical": 7, "deep_danger": 6, "combat": 5, "stealth": 4, "docking": 3, "scoop": 2, "supercruise": 1, "base": 0}

    def __init__(self) -> None:
        self.config = load_config()
        install = resolve_path(self.config["reactive_cockpit"]["install_dir"])
        sys.path.insert(0, str(install))
        self.rc = importlib.import_module(Path(self.config["reactive_cockpit"].get("core_script", "reactive_cockpit.py")).stem)
        self.install = install
        self.tail = JsonlTail(APP_DIR / "runtime" / "outbox.jsonl", from_end=True)
        self.audit = APP_DIR / "runtime" / "visual_theme_events.jsonl"
        graph = json.loads(resolve_path(self.config["active_graph"]).read_text(encoding="utf-8"))
        self.sequences = graph.get("visual_sequences", {}) if isinstance(graph.get("visual_sequences"), dict) else {}
        self.environment_layers: dict[str, dict[str, Any]] = {}
        self.sequence: list[dict[str, Any]] = []
        self.sequence_index = 0
        self.next_step_at = 0.0
        self.restore_theme: str | None = None
        self.last_persistent_slot: str | None = None
        self.persistent_refresh_pending = False
        self.last_core_state_marker = self._latest_core_state_marker()
        self.last_vehicle: str | None = None
        self.unsafe_until = time.monotonic() + 5.0
        self.disco_paused = False
        self.running = True

    def _log(self, outcome: str, **fields: Any) -> None:
        append_jsonl(self.audit, {"at": utc_now(), "outcome": outcome, **fields})

    def _core_lines(self) -> list[str]:
        try:
            files = sorted((self.install / "logs").glob("ReactiveCockpit_*.log"), key=lambda item: item.stat().st_mtime_ns)
            return files[-1].read_text(encoding="utf-8-sig", errors="replace").splitlines()[-5000:] if files else []
        except Exception:
            return []

    def _latest_core_theme(self) -> str | None:
        for line in reversed(self._core_lines()):
            if "STATE ->" in line and " | " in line:
                parts = line.split(" | ")
                if len(parts) >= 2 and parts[1].strip(): return parts[1].strip()
        return None

    def _latest_core_state_marker(self) -> str | None:
        for line in reversed(self._core_lines()):
            if "STATE ->" in line:
                return line.strip()
        return None

    @classmethod
    def _core_slot(cls, marker: str | None) -> str:
        if not marker or "STATE ->" not in marker:
            return "base"
        state = marker.split("STATE ->", 1)[1].split(" | ", 1)[0].strip().casefold()
        aliases = (
            ("critical", ("critical", "imminent destruction")),
            ("deep_danger", ("deep danger", "deep_danger")),
            ("combat", ("combat",)),
            ("stealth", ("stealth", "wanted")),
            ("docking", ("docking", "docked")),
            ("scoop", ("scoop",)),
            ("supercruise", ("supercruise",)),
        )
        for slot, names in aliases:
            if any(name in state for name in names):
                return slot
        return "base"

    def _observe_core_state(self) -> None:
        marker = self._latest_core_state_marker()
        if marker is None or marker == self.last_core_state_marker:
            return
        self.last_core_state_marker = marker
        persistent = self._persistent_slot()
        if persistent and self.PRIORITY[persistent] > self.PRIORITY[self._core_slot(marker)]:
            # Reactive Cockpit legitimately changed its own state/theme. Reapply
            # a stronger location layer once, without fighting Critical.
            self.last_persistent_slot = None

    def _ship_identity(self) -> dict[str, str] | None:
        for line in reversed(self._core_lines()):
            marker = "Current ship ->" if "Current ship ->" in line else ("SHIP ->" if "SHIP ->" in line else None)
            if marker:
                parts = [item.strip() for item in line.split(marker, 1)[1].split("/")]
                if len(parts) >= 3: return {"name": parts[0], "kind_short": parts[1], "plate": parts[2]}
        return None

    @staticmethod
    def _norm(value: Any) -> str: return str(value or "").strip().casefold()

    def _assignment(self, cfg: dict[str, Any]) -> dict[str, Any] | None:
        assignments = [item for item in cfg.get("cockpit_assignments", []) if isinstance(item, dict)]
        identity = self._ship_identity()
        if identity:
            scored = []
            for assignment in assignments:
                ship = assignment.get("ship", {}); score = 0
                if self._norm(ship.get("kind_short")) == self._norm(identity.get("kind_short")): score += 4
                if self._norm(ship.get("name")) == self._norm(identity.get("name")): score += 3
                if self._norm(ship.get("plate")) == self._norm(identity.get("plate")): score += 2
                if score: scored.append((score, assignment))
            if scored: return max(scored, key=lambda item: item[0])[1]
        return assignments[0] if len(assignments) == 1 else None

    def _slot_theme(self, slot: str) -> str | None:
        cfg = self.rc.load_config(); assignment = self._assignment(cfg)
        if not assignment: return None
        package = self.rc.package_for_assignment(assignment) or {}
        if slot == "base": return str(package.get("base_theme") or "").strip() or None
        return (self.rc.assignment_slots(assignment) or {}).get(slot)

    def _edhm_path(self) -> Path | None:
        cfg = self.rc.load_config(); raw = cfg.get("edhm_ini")
        if raw and self.rc.valid_edhm_ini(Path(raw)): return Path(raw)
        valid = [path for path in self.rc.edhm_candidates(cfg) if self.rc.valid_edhm_ini(path)]
        return valid[0] if len(valid) == 1 else None

    def _atomic_copy(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + f".story_visual_{os.getpid()}_{time.time_ns()}.tmp")
        try:
            shutil.copyfile(source, temporary); os.replace(temporary, destination)
        finally:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass

    def _apply_theme(self, theme: str | None, slot: str, reason: str) -> bool:
        if not theme or time.monotonic() < self.unsafe_until: return False
        if self.rc.disco_mode_active(): return False
        live = self._edhm_path(); source = self.rc.find_cached_theme(theme)
        if live is None or source is None: return False
        try:
            for filename in self.rc.CORE_FILES:
                if (source / filename).exists(): self._atomic_copy(source / filename, live / filename)
            time.sleep(.06)
            # Close the small race where Disco is toggled during a story copy.
            # Without this signal write, EDHM never presents the interrupted copy.
            if self.rc.disco_mode_active(): return False
            self._atomic_copy(source / self.rc.SIGNAL_FILE, live / self.rc.SIGNAL_FILE)
            try: os.utime(live / self.rc.SIGNAL_FILE, None)
            except OSError: pass
            print(f"[STORY VISUAL] {reason}: {slot} -> {theme}", flush=True)
            self._log("applied", slot=slot, theme=theme, reason=reason)
            return True
        except Exception as exc:
            self._log("failed", slot=slot, theme=theme, reason=reason, error=str(exc)); return False

    def _vehicle_guard(self) -> None:
        try:
            snapshot = json.loads((APP_DIR / "runtime" / "world_snapshot.json").read_text(encoding="utf-8"))
            vehicle = str(snapshot.get("vehicle", "unknown"))
        except Exception:
            return
        if self.last_vehicle is None: self.last_vehicle = vehicle
        elif vehicle != self.last_vehicle:
            self.last_vehicle = vehicle; self.unsafe_until = time.monotonic() + 5.0
            # The cockpit controller loads a different vehicle theme during the
            # guarded interval. Reassert the active environment afterwards.
            self.last_persistent_slot = None
            self._log("vehicle_guard", vehicle=vehicle)

    def _persistent_slot(self) -> str | None:
        candidates = []
        for payload in self.environment_layers.values():
            cue = str(payload.get("cue", "")).casefold()
            intensity = float(payload.get("intensity", 0.0))
            if cue in self.PRIORITY and intensity >= .05:
                candidates.append((self.PRIORITY[cue], intensity, cue))
        return max(candidates)[2] if candidates else None

    def handle(self, command: dict[str, Any]) -> None:
        if command.get("channel") != "visual": return
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        action = payload.get("action")
        if action == "set_environment_layer":
            before = self._persistent_slot()
            self.environment_layers[str(payload.get("layer"))] = payload
            if self._persistent_slot() != before:
                self.last_persistent_slot = None
                self.persistent_refresh_pending = True
            return
        if action == "stop_environment_layer":
            before = self._persistent_slot()
            self.environment_layers.pop(str(payload.get("layer")), None)
            if self._persistent_slot() != before:
                self.last_persistent_slot = None
                self.persistent_refresh_pending = True
            return
        reference = payload.get("sequence_ref")
        if isinstance(reference, str) and isinstance(self.sequences.get(reference), list):
            self.sequence = list(self.sequences[reference]); self.sequence_index = 0; self.next_step_at = 0.0
            self.restore_theme = self._latest_core_theme() or self.restore_theme
            self._log("sequence_started", sequence=reference, node=command.get("node"))

    def _advance_visuals(self) -> None:
        now = time.monotonic()
        if self.sequence:
            if now < self.next_step_at or now < self.unsafe_until: return
            if self.sequence_index < len(self.sequence):
                step = self.sequence[self.sequence_index]
                slot = str(step.get("slot", "base")).casefold()
                if self._apply_theme(self._slot_theme(slot), slot, "story sequence"):
                    self.sequence_index += 1
                    self.next_step_at = now + max(0.05, float(step.get("duration", .5)))
                return
            self.sequence = []; self.sequence_index = 0
            self.last_persistent_slot = None
            self.persistent_refresh_pending = True
        slot = self._persistent_slot()
        # None is also a valid desired state: release the final story layer and
        # restore the current cockpit even when invalidation cleared the cache.
        if (slot != self.last_persistent_slot or self.persistent_refresh_pending) and now >= self.unsafe_until:
            theme = self._slot_theme(slot) if slot else (self._latest_core_theme() or self.restore_theme)
            if self._apply_theme(theme, slot or "restore", "environment" if slot else "restore"):
                self.last_persistent_slot = slot
                self.persistent_refresh_pending = False

    def _advance_suppressed_sequence(self) -> None:
        """Let finite story visual time pass without writing during Disco."""
        if not self.sequence:
            return
        now = time.monotonic()
        if self.next_step_at <= 0.0:
            self.next_step_at = now
        while self.sequence and now >= self.next_step_at:
            if self.sequence_index >= len(self.sequence):
                self.sequence = []
                self.sequence_index = 0
                self.next_step_at = 0.0
                return
            step = self.sequence[self.sequence_index]
            self.sequence_index += 1
            self.next_step_at += max(0.05, float(step.get("duration", .5)))

    def run(self) -> int:
        print("[STORY VISUAL] Generic cached-theme adapter active and isolated.", flush=True)
        def stop(*_: Any) -> None: self.running = False
        signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)
        while self.running:
            self._vehicle_guard()
            for command in self.tail.poll(): self.handle(command)
            if self.rc.disco_mode_active():
                if not self.disco_paused:
                    self.disco_paused = True
                    print("[STORY VISUAL] Disco Mode owns lighting; story visual writes paused.", flush=True)
                    self._log("disco_paused")
                self._advance_suppressed_sequence()
                time.sleep(.08)
                continue
            if self.disco_paused:
                self.disco_paused = False
                # Reassert only an active persistent environment. If none exists,
                # the core restores its telemetry-derived theme.
                self.last_persistent_slot = None
                print("[STORY VISUAL] Disco Mode ended; current story environment reconciled.", flush=True)
                self._log("disco_resumed")
            self._observe_core_state()
            self._advance_visuals()
            time.sleep(.08)
        return 0


def main() -> int:
    try: return VisualThemeAdapter().run()
    except Exception as exc:
        print(f"[STORY VISUAL] Adapter unavailable; story/cockpit continue: {exc}", flush=True)
        return 0


if __name__ == "__main__": raise SystemExit(main())
