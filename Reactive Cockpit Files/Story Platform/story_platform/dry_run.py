from __future__ import annotations

import copy
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .engine import GraphRuntime, utc_now
from .environments import EnvironmentRuntime
from .model import StoryGraph, validate_graph
from .navigation import NavigationRuntime
from .scans import ScanRuntime
from .time_gates import TimeGateRuntime


class DryRunError(ValueError):
    pass


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


class DryRunSession:
    """An isolated editor session backed by the production graph runtime.

    All durable files live in a temporary directory. Nothing below the live
    ``runtime/`` folder, the Elite adapters, presentation, or Reactive Cockpit
    is opened by this class.
    """

    def __init__(self, graph_data: dict[str, Any], printer: Callable[[str], None] | None = None) -> None:
        errors = validate_graph(graph_data)
        if errors:
            raise DryRunError("Graph has validation issues:\n- " + "\n- ".join(errors))
        self._temporary = tempfile.TemporaryDirectory(prefix="story_platform_dry_run_")
        self.root = Path(self._temporary.name)
        self.graph = StoryGraph(path=self.root / "graph.json", data=copy.deepcopy(graph_data))
        self.state_path = self.root / "state.json"
        self.event_log = self.root / "events.jsonl"
        self.outbox = self.root / "outbox.jsonl"
        self.environment_state_path = self.root / "environment_state.json"
        self.environment_event_log = self.root / "environment_events.jsonl"
        self.navigation_state_path = self.root / "navigation_state.json"
        self.navigation_event_log = self.root / "navigation_events.jsonl"
        self.time_state_path = self.root / "time_state.json"
        self.time_event_log = self.root / "time_events.jsonl"
        self.scan_state_path = self.root / "scan_state.json"
        self.scan_event_log = self.root / "scan_events.jsonl"
        self.console: list[str] = []
        self.world: dict[str, Any] = {}
        self.simulated_now = datetime.now(timezone.utc)
        self.last_signal: dict[str, Any] = {
            "name": "system.start", "source": "simulator", "at": utc_now(), "data": {}
        }
        self._printer = printer or self.console.append
        self.runtime = self._new_runtime()
        self.environments = self._new_environment_runtime()
        self.runtime.emit_observer = self.environments.observe_emit
        self.navigation = self._new_navigation_runtime()
        self.time_gates = self._new_time_runtime()
        self.scans = self._new_scan_runtime()
        self.running = False

    def _new_runtime(self) -> GraphRuntime:
        return GraphRuntime(
            self.graph, self.state_path, self.event_log, self.outbox, printer=self._printer
        )

    def _new_environment_runtime(self) -> EnvironmentRuntime:
        return EnvironmentRuntime(
            self.graph, self.environment_state_path, self.environment_event_log,
            self.outbox, printer=self._printer,
        )

    def _new_navigation_runtime(self) -> NavigationRuntime:
        return NavigationRuntime(
            self.graph, self.navigation_state_path, self.navigation_event_log,
            self.outbox, printer=self._printer,
        )

    def _new_time_runtime(self) -> TimeGateRuntime:
        return TimeGateRuntime(
            self.graph, self.time_state_path, self.time_event_log, self.outbox,
            printer=self._printer, now_provider=lambda: self.simulated_now,
        )

    def _new_scan_runtime(self) -> ScanRuntime:
        return ScanRuntime(
            self.graph, self.scan_state_path, self.scan_event_log, self.outbox,
            printer=self._printer,
        )

    @property
    def state(self) -> dict[str, Any]:
        return copy.deepcopy(self.runtime.state)

    @property
    def active_nodes(self) -> list[str]:
        return list(self.runtime.state.get("active_nodes", []))

    @property
    def environment_state(self) -> dict[str, Any]:
        return self.environments.snapshot()

    @property
    def navigation_state(self) -> dict[str, Any]:
        return copy.deepcopy(self.navigation.state)

    @property
    def time_state(self) -> dict[str, Any]:
        return self.time_gates.snapshot()

    @property
    def scan_state(self) -> dict[str, Any]:
        return self.scans.snapshot()

    def start(self) -> None:
        if self.running:
            return
        self.time_gates.refresh_world(self.world)
        self.runtime.start_session()
        self.environments.start_session(self.world, self.runtime.state.get("variables", {}))
        self.navigation.start_session(self.world, self.runtime.state.get("variables", {}))
        startup = {"name": "system.start", "source": "system", "at": utc_now(), "data": {}}
        if self.scans.dispatch(startup, self.world, self.runtime.state.get("variables", {}), self.environments.effective_control_states()):
            self.runtime.save()
        self.time_gates.process(startup, self.world, self.runtime.state.get("variables", {}))
        self._drain_generated_signals()
        self.running = True

    def _drain_generated_signals(self) -> bool:
        moved = False
        for _ in range(100):
            generated = [
                *self.environments.drain_signals(), *self.navigation.drain_signals(),
                *self.scans.drain_signals(), *self.time_gates.drain_signals(),
            ]
            if not generated:
                return moved
            for item in generated:
                self.last_signal = item
                moved = self._dispatch_signal(item) or moved
        raise DryRunError("Generated signal chain exceeded 100 events")

    def stop(self) -> None:
        if self.running:
            self.environments.stop_session()
            self.navigation.stop_session()
            self.time_gates.save()
            self.scans.save()
            self.runtime.stop_session()
            self.running = False

    def reset(self) -> None:
        self.runtime.reset()
        self.environments.reset()
        self.navigation.reset()
        self.time_gates.reset()
        self.scans.reset()
        self.world = {}
        self.simulated_now = datetime.now(timezone.utc)
        self.last_signal = {
            "name": "system.start", "source": "simulator", "at": utc_now(), "data": {}
        }
        self.runtime.start_session()
        self.time_gates.refresh_world(self.world)
        self.environments.start_session(self.world, self.runtime.state.get("variables", {}))
        self.navigation.start_session(self.world, self.runtime.state.get("variables", {}))
        startup = {"name": "system.start", "source": "system", "at": utc_now(), "data": {}}
        if self.scans.dispatch(startup, self.world, self.runtime.state.get("variables", {}), self.environments.effective_control_states()):
            self.runtime.save()
        self.time_gates.process(startup, self.world, self.runtime.state.get("variables", {}))
        self._drain_generated_signals()
        self.running = True

    def _dispatch_signal(self, signal: dict[str, Any]) -> bool:
        self.time_gates.refresh_world(self.world)
        story_moved = self.runtime.dispatch(signal, self.world)
        variables = self.runtime.state.get("variables", {})
        environment_changed = self.environments.dispatch(signal, self.world, variables)
        navigation_changed = self.navigation.dispatch(signal, self.world, variables)
        scan_changed = self.scans.dispatch(
            signal, self.world, variables, self.environments.effective_control_states(),
        )
        if scan_changed:
            self.runtime.save()
        time_changed = self.time_gates.process(signal, self.world, variables)
        return story_moved or environment_changed or navigation_changed or scan_changed or time_changed

    def dispatch(self, name: str, data: dict[str, Any] | None = None, source: str = "simulator") -> bool:
        if not self.running:
            raise DryRunError("Start the dry run before injecting a signal")
        name = name.strip()
        if not name:
            raise DryRunError("Signal name cannot be empty")
        signal = {"name": name, "source": source, "at": utc_now(), "data": copy.deepcopy(data or {})}
        self.last_signal = signal
        moved = self._dispatch_signal(signal)
        moved = self._drain_generated_signals() or moved
        return moved

    def replace_world(self, value: dict[str, Any], *, evaluate_now: bool = True) -> bool:
        if not isinstance(value, dict):
            raise DryRunError("World snapshot must be a JSON object")
        self.world = copy.deepcopy(value)
        if evaluate_now and self.running:
            return self.dispatch("simulator.world_changed", {"world": copy.deepcopy(value)})
        return False

    def _shift_time(self, seconds: float) -> None:
        if not self.running:
            raise DryRunError("Start the dry run before advancing time")
        if seconds < 0:
            raise DryRunError("Time advance must be zero or greater")
        for node_id in self.active_nodes:
            raw = self.runtime.state.get("entered_at", {}).get(node_id)
            if not raw:
                continue
            entered = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            shifted = entered - timedelta(seconds=float(seconds))
            self.runtime.state["entered_at"][node_id] = shifted.astimezone(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
        self.runtime.save()
        self.environments.advance_time(seconds)
        self.scans.advance_time(seconds)
        self.simulated_now += timedelta(seconds=float(seconds))

    def advance_time(self, seconds: float) -> bool:
        self._shift_time(seconds)
        return self.dispatch("system.tick", {"advanced_seconds": float(seconds)}, source="system")

    def set_site_local_hour(self, site_id: str, local_hour: float) -> bool:
        """Move only the simulated celestial clock to the next requested site hour."""
        if not self.running:
            raise DryRunError("Start the dry run before setting celestial time")
        if not 0 <= local_hour < 24:
            raise DryRunError("Local hour must be at least 0 and below 24")
        self.time_gates.refresh_world(self.world)
        fact = self.world.get("time", {}).get("sites", {}).get(site_id)
        if not isinstance(fact, dict):
            raise DryRunError(f"Unknown solar site {site_id!r}")
        delta_hours = (float(local_hour) - float(fact["local_hour"])) % 24.0
        self.simulated_now += timedelta(
            seconds=delta_hours * float(fact["solar_day_earth_seconds"]) / 24.0
        )
        return self.dispatch(
            "system.tick", {"celestial_site": site_id, "local_hour": float(local_hour)}, source="system",
        )

    def run_trigger(
        self, signal_name: str, signal_data: dict[str, Any], world: dict[str, Any],
        advance_seconds: float = 0.0,
    ) -> bool:
        """Apply a complete authoring preset and evaluate it as one simulated event."""
        if not self.running:
            raise DryRunError("Start the dry run before running a trigger")
        self.replace_world(world, evaluate_now=False)
        self._shift_time(float(advance_seconds))
        return self.dispatch(signal_name, signal_data)

    def force_edge(self, edge_id: str) -> bool:
        """Traverse one edge without its predicate, only within this isolated session."""
        if not self.running:
            raise DryRunError("Start the dry run before forcing an edge")
        edge = next((item for item in self.graph.edges if item.get("id") == edge_id), None)
        if edge is None:
            raise DryRunError(f"Edge {edge_id!r} does not exist in the running graph snapshot")
        source = str(edge.get("from", ""))
        if source not in self.active_nodes:
            active = ", ".join(self.active_nodes) or "none"
            raise DryRunError(
                f"Edge {edge_id!r} starts at {source!r}, but the active node is {active}. "
                "Select an outgoing edge from the active node."
            )
        signal = {
            "name": "simulator.force_edge", "source": "simulator", "at": utc_now(),
            "data": {"edge_id": edge_id},
        }
        self.last_signal = signal
        # Reuse the production transition implementation while bypassing only
        # predicate evaluation. This API exists solely on the isolated dry run.
        self.runtime._transition(source, edge, signal)
        self.runtime.save()
        self.runtime._drain_immediate(signal, self.world)
        self.environments.dispatch(signal, self.world, self.runtime.state.get("variables", {}))
        self.navigation.dispatch(signal, self.world, self.runtime.state.get("variables", {}))
        self._drain_generated_signals()
        return True

    def crash_and_recover(self) -> dict[str, Any]:
        """Simulate process loss and restart, deliberately without clean shutdown."""
        if not self.running:
            raise DryRunError("Start the dry run before testing recovery")
        before = self.state
        self.runtime.save()
        self.runtime = self._new_runtime()
        self.environments = self._new_environment_runtime()
        self.navigation = self._new_navigation_runtime()
        self.time_gates = self._new_time_runtime()
        self.scans = self._new_scan_runtime()
        restored = self.state
        self.runtime.start_session()
        self.environments.start_session(self.world, self.runtime.state.get("variables", {}))
        self.navigation.start_session(self.world, self.runtime.state.get("variables", {}))
        startup = {"name": "system.start", "source": "system", "at": utc_now(), "data": {}}
        self.scans.dispatch(startup, self.world, self.runtime.state.get("variables", {}), self.environments.effective_control_states())
        self.time_gates.process(startup, self.world, self.runtime.state.get("variables", {}))
        self._drain_generated_signals()
        self.runtime.reemit_current_objective()
        self.running = True
        return {
            "before_active_nodes": before.get("active_nodes", []),
            "after_active_nodes": self.active_nodes,
            "checkpoint": (restored.get("checkpoint") or {}).get("id"),
        }

    def events(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.event_log)

    def outputs(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.outbox)

    def environment_events(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.environment_event_log)

    def time_events(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.time_event_log)

    def scan_events(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.scan_event_log)

    def outgoing_edges(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for node_id in self.active_nodes:
            for edge in self.graph.outgoing(node_id):
                result.append({
                    "node": node_id,
                    "edge": edge.get("id"),
                    "to": copy.deepcopy(edge.get("to")),
                    "when": copy.deepcopy(edge.get("when", {"always": True})),
                    "priority": int(edge.get("priority", 0)),
                })
        return result

    def close(self) -> None:
        self.stop()
        self._temporary.cleanup()

    def __enter__(self) -> "DryRunSession":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
