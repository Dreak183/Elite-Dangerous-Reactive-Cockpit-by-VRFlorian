from __future__ import annotations

import json
import hashlib
import signal as os_signal
import time
from pathlib import Path
from typing import Any

from . import VERSION
from .channels import JsonlTail
from .config import APP_DIR, DEFAULT_CONFIG, default_journal_dir, load_config, resolve_path
from .developer import DeveloperTools
from .engine import GraphRuntime, utc_now
from .environments import EnvironmentRuntime
from .wind_runtime import WindRuntime
from .graph_manager import GRAPH_SWITCH_EXIT_CODE, GraphManager, GraphSelectionError
from .instance_lock import AlreadyRunningError, InstanceLock
from .model import load_graph
from .navigation import NavigationRuntime
from .scans import ScanRuntime
from .storage import append_jsonl, atomic_write_json
from .signals import ExactDeduplicator, ensure_signal_id
from .telemetry import (
    JournalTail,
    StatusWatcher,
    WorldModel,
    is_live_journal_telemetry,
    is_live_status_update,
    seed_world_from_latest_journal,
    status_age_seconds,
)
from .time_gates import TimeGateRuntime


def control_feedback_command(
    config: dict[str, Any], signal_name: str, before: Any, after: Any,
) -> dict[str, Any] | None:
    """Build audio feedback only for a control state that really changed."""
    if not signal_name.startswith("control.") or not isinstance(after, bool) or before == after:
        return None
    action = signal_name.removeprefix("control.")
    controls = config.get("controls") if isinstance(config.get("controls"), dict) else {}
    feedback = controls.get("feedback") if isinstance(controls.get("feedback"), dict) else {}
    spec = feedback.get(action) if isinstance(feedback.get(action), dict) else None
    if not spec:
        return None
    asset = str(spec.get("on_asset" if after else "off_asset", "")).strip()
    if not asset:
        return None
    return {
        "action": "play_layer",
        "layer": str(spec.get("layer", f"system.control.{action}.feedback")),
        "platform_asset": asset,
        "volume": max(0.0, min(1.0, float(spec.get("volume", 1.0)))),
        "loop": False,
        "control": signal_name,
        "enabled": after,
        **({"completion_signal": "presentation.scanner_startup_complete"} if after else {}),
    }


def accepts_control_inbox_signal(signal: dict[str, Any]) -> bool:
    """Accept controls plus completion callbacks emitted by presentation."""
    name = str(signal.get("name", ""))
    source = str(signal.get("source", ""))
    return source == "presentation" or name.startswith((
        "control.", "presentation.", "navigation.", "environment.", "scan.", "timegate.", "developer.", "platform.",
    ))


def live_telemetry_confirmation_source(
    source: str,
    payload: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> str | None:
    """Name fresh Elite telemetry that can safely start the story runtime."""
    if source == "journal" and is_live_journal_telemetry(payload):
        return f"journal.{payload.get('event', 'Unknown')}"
    if source == "status" and is_live_status_update(payload, previous):
        return "status.changed"
    return None


def live_location_confirmation_source(source: str, payload: dict[str, Any]) -> str | None:
    """Backward-compatible name retained for older focused test imports."""
    return live_telemetry_confirmation_source(source, payload)


class StoryPlatformRunner:
    def __init__(self) -> None:
        config = load_config()
        self.config = config
        self.poll_seconds = float(config.get("poll_seconds", 0.2))
        self.status_max_initial_age = float(config.get("status_max_initial_age_seconds", 15.0))
        self.graph_manager = GraphManager()
        graph_path = self.graph_manager.resolve_startup(config)
        self.graph_path = graph_path
        self.graph_digest = hashlib.sha256(graph_path.read_bytes()).hexdigest()[:12]
        graph = load_graph(graph_path)
        runtime_dir = APP_DIR / "runtime"
        self.world_path = runtime_dir / "world_snapshot.json"
        self.signal_log = runtime_dir / "signals.jsonl"
        self.control_inbox = JsonlTail(runtime_dir / "control_inbox.jsonl", from_end=True)
        self.engine = GraphRuntime(
            graph,
            runtime_dir / f"state_{graph.graph_id}.json",
            runtime_dir / f"events_{graph.graph_id}.jsonl",
            runtime_dir / "outbox.jsonl",
        )
        self.environments = EnvironmentRuntime(
            graph,
            runtime_dir / f"environment_state_{graph.graph_id}.json",
            runtime_dir / f"environment_events_{graph.graph_id}.jsonl",
            runtime_dir / "outbox.jsonl",
            outputs_enabled=False,
        )
        self.engine.emit_observer = self._observe_emit
        self.navigation = NavigationRuntime(
            graph,
            runtime_dir / f"navigation_state_{graph.graph_id}.json",
            runtime_dir / f"navigation_events_{graph.graph_id}.jsonl",
            runtime_dir / "outbox.jsonl",
        )
        self.time_gates = TimeGateRuntime(
            graph,
            runtime_dir / f"time_gate_state_{graph.graph_id}.json",
            runtime_dir / f"time_gate_events_{graph.graph_id}.jsonl",
            runtime_dir / "outbox.jsonl",
        )
        self.scans = ScanRuntime(
            graph,
            runtime_dir / f"scan_state_{graph.graph_id}.json",
            runtime_dir / f"scan_events_{graph.graph_id}.jsonl",
            runtime_dir / "outbox.jsonl",
        )
        self.developer_enabled = bool(config.get("developer", {}).get("enabled", False))
        self.developer = DeveloperTools(
            runtime_dir / f"developer_session_{graph.graph_id}.json",
            runtime_dir / f"developer_history_{graph.graph_id}.json",
            runtime_dir / "outbox.jsonl",
        )
        if self.developer_enabled:
            self.developer.recover_abandoned(
                self.engine, self.environments, self.navigation, self.time_gates, self.scans,
            )
        journal_value = config.get("journal_dir", "")
        self.journal_dir = resolve_path(journal_value) if journal_value else default_journal_dir()
        self.journal = JournalTail(self.journal_dir, from_end=True)
        self.status = StatusWatcher(self.journal_dir / "Status.json")
        self.world = WorldModel()
        self.deduplicator = ExactDeduplicator()
        self.have_fresh_status = False
        self.audio_live = False
        self.runtime_started = False
        self.running = True

    def _observe_emit(self, command: dict[str, Any]) -> None:
        self.environments.observe_emit(command)
        try:
            if self.graph_manager.observe_command(command, self.config):
                target = self.graph_manager.switch_target
                print(
                    f"[GRAPH MANAGER] Switching to {target.graph_id} {target.version}."
                    if target else "[GRAPH MANAGER] Graph switch requested.",
                    flush=True,
                )
                self.running = False
        except GraphSelectionError as exc:
            print(f"[GRAPH MANAGER] Switch rejected: {exc}", flush=True)

    def _dispatch_platform_control(self, signal: dict[str, Any]) -> bool:
        manager = getattr(self, "graph_manager", None)
        if manager is None or signal.get("name") != "platform.graph.switch":
            return False
        try:
            if manager.observe_signal(signal, self.config):
                target = manager.switch_target
                print(
                    f"[GRAPH MANAGER] Switching to {target.graph_id} {target.version}."
                    if target else "[GRAPH MANAGER] Graph switch requested.",
                    flush=True,
                )
                self.running = False
        except GraphSelectionError as exc:
            print(f"[GRAPH MANAGER] Switch rejected: {exc}", flush=True)
        return True

    def _graph_switch_requested(self) -> bool:
        manager = getattr(self, "graph_manager", None)
        return bool(manager and manager.switch_requested)

    def _set_audio_gate(self, opened: bool, reason: str) -> None:
        append_jsonl(self.engine.outbox, {
            "at": utc_now(), "graph_id": self.engine.graph.graph_id,
            "node": "runtime", "channel": "system",
            "payload": {"action": "set_audio_gate", "open": bool(opened), "reason": reason},
        })

    def _confirm_live_audio(self, source: str) -> None:
        if self.audio_live:
            return
        self.audio_live = True
        self.environments.set_outputs_enabled(True)
        self._set_audio_gate(True, source)
        self._start_live_runtime()
        print("[AUDIO] Fresh Elite telemetry confirmed — restoring the current soundscape.", flush=True)

    def _start_live_runtime(self) -> None:
        """Start story evaluation only after Elite has published fresh telemetry.

        The presentation audio gate alone cannot protect one-shot dialogue: a
        graph timer could previously advance behind the closed gate and its
        emitted audio would then be gone forever.  Starting all stateful story
        siblings here makes the first genuinely new gameplay Journal record or
        meaningful post-baseline Status.json change the readiness boundary for
        graph, scan, navigation and time events. Cached hydration, timestamp-only
        status rewrites, and menu/session lifecycle records remain inert.
        """
        if self.runtime_started:
            return
        self.runtime_started = True
        self.time_gates.refresh_world(self.world.data)
        resumed = bool(self.engine.state.get("visits"))
        self.engine.start_session()
        if self._graph_switch_requested():
            return
        self.environments.start_session(self.world.data, self.engine.state.get("variables", {}))
        self.navigation.start_session(self.world.data, self.engine.state.get("variables", {}))
        startup = {"name": "system.start", "source": "system", "at": utc_now(), "data": {}}
        startup_scan_changed = self.scans.dispatch(
            startup, self.world.data, self.engine.state.get("variables", {}),
            self.environments.effective_control_states(),
        )
        if startup_scan_changed:
            self.engine.save()
        self.time_gates.process(startup, self.world.data, self.engine.state.get("variables", {}))
        self._drain_generated_signals()
        if resumed:
            self.engine.reemit_current_objective()

    def _dispatch(self, signal: dict[str, Any]) -> None:
        pending = [signal]
        for _ in range(100):
            if not pending:
                break
            current = pending.pop(0)
            ensure_signal_id(current)
            append_jsonl(self.signal_log, current)
            if self._dispatch_platform_control(current):
                continue
            if self._dispatch_developer_control(current):
                continue
            if not self.runtime_started:
                # Controls can still reach the hidden developer tools above,
                # but ordinary story input is inert until Elite is live.
                continue
            before_nodes = list(self.engine.state.get("active_nodes", []))
            signal_name = str(current.get("name", ""))
            before_control = self.environments.state.get("control_states", {}).get(signal_name)
            predicted_control = not before_control if isinstance(before_control, bool) else None
            feedback = control_feedback_command(
                getattr(self, "config", DEFAULT_CONFIG),
                signal_name, before_control, predicted_control,
            )
            if feedback and feedback["enabled"]:
                data = current.setdefault("data", {})
                if isinstance(data, dict):
                    data["control_feedback_pending"] = True
                append_jsonl(self.engine.outbox, {
                    "at": utc_now(), "graph_id": self.engine.graph.graph_id,
                    "node": f"control:{signal_name.removeprefix('control.')}",
                    "channel": "audio", "payload": feedback,
                })
            before_snapshot = self.developer.capture(
                self.engine, self.environments, self.navigation, self.time_gates, self.scans,
            ) if self.developer_enabled else None
            self.time_gates.refresh_world(self.world.data)
            self.engine.dispatch(current, self.world.data)
            if self._graph_switch_requested():
                pending.clear()
                break
            variables = self.engine.state.get("variables", {})
            self.environments.dispatch(current, self.world.data, variables)
            self.navigation.dispatch(current, self.world.data, variables)
            scan_changed = self.scans.dispatch(
                current, self.world.data, variables,
                self.environments.effective_control_states(),
            )
            if scan_changed:
                self.engine.save()
            actual_control = self.environments.state.get("control_states", {}).get(signal_name)
            if feedback and not feedback["enabled"] and actual_control is predicted_control:
                # Shutdown feedback is deliberately queued after environment
                # pings and any active acquisition layer have been stopped.
                append_jsonl(self.engine.outbox, {
                    "at": utc_now(), "graph_id": self.engine.graph.graph_id,
                    "node": f"control:{signal_name.removeprefix('control.')}",
                    "channel": "audio", "payload": feedback,
                })
            self.time_gates.process(current, self.world.data, variables)
            self.developer.record_if_changed(
                before_nodes, self.engine, self.environments, self.navigation,
                self.time_gates, self.scans,
            )
            if (
                before_snapshot is not None and not self.developer.active
                and before_nodes != list(self.engine.state.get("active_nodes", []))
            ):
                self.developer.observe_real_transition(before_snapshot, self.engine)
            pending.extend(self.environments.drain_signals())
            pending.extend(self.navigation.drain_signals())
            pending.extend(self.scans.drain_signals())
            pending.extend(self.time_gates.drain_signals())
        else:
            raise RuntimeError("Generated signal chain exceeded 100 events")
        atomic_write_json(self.world_path, {"at": utc_now(), **self.world.data})

    def _dispatch_developer_control(self, signal: dict[str, Any]) -> bool:
        name = str(signal.get("name", ""))
        if not name.startswith("developer."):
            return False
        if not self.developer_enabled:
            return True
        args = (self.engine, self.environments, self.navigation, self.time_gates, self.scans)
        if name == "developer.toggle":
            self.developer.toggle(*args)
        elif name == "developer.back":
            self.developer.back(*args)
        elif name == "developer.forward":
            self.developer.forward(*args, self.world.data)
        elif name == "developer.next_mission":
            self.developer.next_mission(*args)
        return True

    def _hydrate_world(self) -> None:
        replayed = seed_world_from_latest_journal(self.journal_dir, self.world)
        current = self.status.poll()
        if current:
            status, _previous = current
            self.world.status_signals(status, None)
        atomic_write_json(self.world_path, {"at": utc_now(), **self.world.data})
        if replayed:
            print(f"[TELEMETRY] Restored current context from {replayed} recent journal record(s).", flush=True)

    def _dispatch_live_journal_event(self, event: dict[str, Any]) -> None:
        """Open the gate first, then deliver the same event to the story."""
        signal = self.world.journal_signal(event)
        confirmation = live_telemetry_confirmation_source("journal", event)
        if confirmation:
            self._confirm_live_audio(confirmation)
        self._dispatch(signal)

    def _dispatch_live_status_update(
        self,
        current: dict[str, Any],
        previous: dict[str, Any] | None,
    ) -> None:
        """Open the gate from a fresh status change without losing its signals."""
        status_signals = self.world.status_signals(current, previous)
        confirmation = live_telemetry_confirmation_source("status", current, previous)
        if confirmation:
            self._confirm_live_audio(confirmation)
        for item in status_signals:
            self._dispatch(item)

    def _record_only(self, signal: dict[str, Any]) -> None:
        ensure_signal_id(signal)
        append_jsonl(self.signal_log, signal)

    def _drain_generated_signals(self) -> None:
        generated = [
            *self.environments.drain_signals(), *self.navigation.drain_signals(),
            *self.scans.drain_signals(), *self.time_gates.drain_signals(),
        ]
        for item in generated:
            self._dispatch(item)

    def run(self) -> int:
        try:
            with InstanceLock("graph", APP_DIR):
                return self._run_locked()
        except AlreadyRunningError:
            print("[STORY PLATFORM] Another graph runtime is already running for this installation.", flush=True)
            return 3

    def _run_locked(self) -> int:
        print("=" * 78)
        print(f"REACTIVE COCKPIT STORY PLATFORM {VERSION}")
        print(f"Graph   : {self.engine.graph.graph_id} {self.engine.graph.version}")
        print(f"Source  : {self.graph_path}")
        print(f"Loaded  : {self.graph_digest} / {len(self.environments.zones)} environment zone(s)")
        print(f"Journal : {self.journal_dir}")
        print("Graph runtime is isolated from the Reactive Cockpit core.")
        print("=" * 78)
        self._set_audio_gate(False, "waiting for fresh in-game Elite telemetry")
        print("[AUDIO] Waiting for fresh in-game Elite telemetry before story audio becomes live.", flush=True)
        self.journal.arm()
        self._hydrate_world()
        self.time_gates.refresh_world(self.world.data)
        wind = WindRuntime(APP_DIR, self.journal_dir, self.environments)
        wind.start()

        def stop(*_: Any) -> None:
            self.running = False

        for name in ("SIGINT", "SIGTERM"):
            if hasattr(os_signal, name):
                os_signal.signal(getattr(os_signal, name), stop)
        try:
            next_tick = time.monotonic()
            while self.running:
                for event in self.journal.poll():
                    accepted, identity = self.deduplicator.accept("journal", event)
                    if accepted:
                        self._dispatch_live_journal_event(event)
                    else:
                        self._record_only({
                            "name": "system.duplicate_signal_ignored", "source": "system", "at": utc_now(),
                            "data": {"duplicate_id": identity, "original_name": f"journal.{event.get('event', 'Unknown')}"}
                        })
                status_result = self.status.poll()
                if status_result:
                    current, previous = status_result
                    age = status_age_seconds(current)
                    if not self.have_fresh_status and age is not None and age > self.status_max_initial_age:
                        self.status.discard_current_as_stale()
                        self._record_only({
                            "name": "status.stale_snapshot_ignored", "source": "system", "at": utc_now(),
                            "data": {"status_timestamp": current.get("timestamp"), "age_seconds": round(age, 3)}
                        })
                    else:
                        self.have_fresh_status = True
                        self._dispatch_live_status_update(current, previous)
                for control_signal in self.control_inbox.poll():
                    if accepts_control_inbox_signal(control_signal):
                        self._dispatch(control_signal)
                now = time.monotonic()
                if self.runtime_started and now >= next_tick:
                    tick = {"name": "system.tick", "source": "system", "at": utc_now(), "data": {}}
                    self._dispatch(tick)
                    next_tick = now + 1.0
                wind.update(self.world.data, enabled=self.runtime_started and self.running and not self.developer.active)
                time.sleep(self.poll_seconds)
            manager = getattr(self, "graph_manager", None)
            return GRAPH_SWITCH_EXIT_CODE if manager and manager.switch_requested else 0
        finally:
            try:
                wind.close()
            except Exception as exc:
                print(f"[WIND] Shutdown status: {exc}", flush=True)
            reason = "graph switch" if self._graph_switch_requested() else "graph runtime stopped"
            self._set_audio_gate(False, reason)
            self.journal.close()
            if self.developer.active:
                self.developer.finish(
                    self.engine, self.environments, self.navigation, self.time_gates, self.scans,
                )
            self.environments.stop_session()
            self.navigation.stop_session()
            self.time_gates.save()
            self.scans.save()
            self.engine.stop_session()


def main() -> int:
    return StoryPlatformRunner().run()


if __name__ == "__main__":
    raise SystemExit(main())
