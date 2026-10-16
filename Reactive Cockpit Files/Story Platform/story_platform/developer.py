from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

from .engine import utc_now
from .predicates import evaluate
from .storage import append_jsonl, atomic_write_json


class DeveloperTools:
    """Hidden, reversible live story navigation for authorized test builds."""

    def __init__(
        self, state_path: Path, history_path: Path, outbox: Path, printer: Callable[[str], None] = print,
    ) -> None:
        self.state_path = state_path
        self.history_path = history_path
        self.outbox = outbox
        self.print = printer
        self.active = False
        self.base: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.future: list[dict[str, Any]] = []

    @staticmethod
    def capture(engine: Any, environments: Any, navigation: Any, time_gates: Any, scans: Any) -> dict[str, Any]:
        return {
            "engine": copy.deepcopy(engine.state),
            "environments": copy.deepcopy(environments.state),
            "navigation": copy.deepcopy(navigation.state),
            "time_gates": copy.deepcopy(time_gates.state),
            "scans": copy.deepcopy(scans.state),
        }

    @staticmethod
    def restore(snapshot: dict[str, Any], engine: Any, environments: Any, navigation: Any, time_gates: Any, scans: Any) -> None:
        engine.state = copy.deepcopy(snapshot["engine"])
        environments.state = copy.deepcopy(snapshot["environments"])
        navigation.state = copy.deepcopy(snapshot["navigation"])
        time_gates.state = copy.deepcopy(snapshot["time_gates"])
        scans.state = copy.deepcopy(snapshot["scans"])
        engine.save()
        environments.save()
        navigation.save()
        time_gates.save()
        scans.save()

    def recover_abandoned(self, engine: Any, environments: Any, navigation: Any, time_gates: Any, scans: Any) -> bool:
        if not self.state_path.exists():
            return False
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            base = saved.get("base")
            if isinstance(base, dict):
                self.restore(base, engine, environments, navigation, time_gates, scans)
                self.print("[GOD MODE] Restored real progress after an interrupted developer session.")
                return True
        finally:
            self.state_path.unlink(missing_ok=True)
        return False

    def toggle(self, engine: Any, environments: Any, navigation: Any, time_gates: Any, scans: Any) -> bool:
        if not self.active:
            self.base = self.capture(engine, environments, navigation, time_gates, scans)
            atomic_write_json(self.state_path, {"schema": "platform.developer-session.v1", "base": self.base})
            self.history = self._load_history(engine)
            self.history.append(self.capture(engine, environments, navigation, time_gates, scans))
            self.future = []
            self.active = True
            self.print("[GOD MODE] ON — test navigation is isolated from real mission progress.")
            self.show_position(engine)
            return True
        self.finish(engine, environments, navigation, time_gates, scans)
        return False

    def _load_history(self, engine: Any) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        try:
            value = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if value.get("graph_id") != engine.graph.graph_id or value.get("graph_version") != engine.graph.version:
            return []
        snapshots = value.get("snapshots", [])
        return copy.deepcopy(snapshots[-100:]) if isinstance(snapshots, list) else []

    def observe_real_transition(self, before: dict[str, Any], engine: Any) -> None:
        if self.active:
            return
        snapshots = self._load_history(engine)
        snapshots.append(copy.deepcopy(before))
        atomic_write_json(self.history_path, {
            "schema": "platform.developer-history.v1",
            "graph_id": engine.graph.graph_id, "graph_version": engine.graph.version,
            "snapshots": snapshots[-100:],
        })

    def finish(self, engine: Any, environments: Any, navigation: Any, time_gates: Any, scans: Any) -> None:
        if self.base is not None:
            self.restore(self.base, engine, environments, navigation, time_gates, scans)
        self.active = False
        self.base = None
        self.history = []
        self.future = []
        self.state_path.unlink(missing_ok=True)
        self._sync_audio(engine, environments)
        engine.reemit_current_objective()
        self.print("[GOD MODE] OFF — real mission progress restored.")
        self.show_position(engine)

    def _sync_audio(self, engine: Any, environments: Any) -> None:
        append_jsonl(self.outbox, {
            "at": utc_now(), "graph_id": engine.graph.graph_id, "node": "developer.restore",
            "channel": "audio", "payload": {"stop": True},
        })
        for zone in environments.zones:
            zone_state = environments.state.get("zones", {}).get(str(zone["id"]), {})
            if zone_state.get("phase") != "active":
                continue
            for layer in zone.get("layers", []):
                if zone_state.get("layers", {}).get(str(layer["id"]), {}).get("playing"):
                    environments._start_layer(zone, layer, zone_state)

    def record_if_changed(self, before_nodes: list[str], engine: Any, environments: Any, navigation: Any, time_gates: Any, scans: Any) -> None:
        if not self.active or before_nodes == list(engine.state.get("active_nodes", [])):
            return
        self.history.append(self.capture(engine, environments, navigation, time_gates, scans))
        self.future = []
        self.show_position(engine)

    def back(self, engine: Any, environments: Any, navigation: Any, time_gates: Any, scans: Any) -> bool:
        if not self.active:
            return False
        if len(self.history) <= 1:
            self.print("[GOD MODE] Already at the beginning of this test session.")
            return False
        self.future.append(self.history.pop())
        self.restore(self.history[-1], engine, environments, navigation, time_gates, scans)
        self._sync_audio(engine, environments)
        self.print("[GOD MODE] BACK")
        self.show_position(engine)
        return True

    def forward(self, engine: Any, environments: Any, navigation: Any, time_gates: Any, scans: Any, world: dict[str, Any]) -> bool:
        if not self.active:
            return False
        if self.future:
            snapshot = self.future.pop()
            self.restore(snapshot, engine, environments, navigation, time_gates, scans)
            self._sync_audio(engine, environments)
            self.history.append(snapshot)
            self.print("[GOD MODE] FORWARD (redo)")
            self.show_position(engine)
            return True
        active = list(engine.state.get("active_nodes", []))
        candidates: list[dict[str, Any]] = []
        for node_id in active:
            outgoing = engine.graph.outgoing(node_id)
            preferred = engine.graph.nodes[node_id].get("developer_next_edge")
            if preferred:
                edge = next((item for item in outgoing if item.get("id") == preferred), None)
                if edge is None:
                    self.print(f"[GOD MODE] Node {node_id} names missing developer edge {preferred}.")
                    return False
                candidates.append(edge)
            elif len(outgoing) > 1:
                signal = {"name": "developer.force_forward", "source": "developer", "at": utc_now(), "data": {}}
                context = {
                    "signal": signal, "world": world, "variables": engine.state.get("variables", {}),
                    "state": engine.state, "node_elapsed_seconds": 0.0,
                }
                satisfied = [edge for edge in outgoing if evaluate(edge.get("when", {"always": True}), context)]
                candidates.extend(satisfied if len(satisfied) == 1 else outgoing)
            else:
                candidates.extend(outgoing)
        if len(candidates) != 1:
            names = ", ".join(str(item.get("id")) for item in candidates) or "none"
            self.print(f"[GOD MODE] Forward is ambiguous ({names}). Set developer_next_edge on the active node.")
            return False
        edge = candidates[0]
        source = str(edge["from"])
        signal = {"name": "developer.force_forward", "source": "developer", "at": utc_now(), "data": {"edge": edge["id"]}}
        engine._transition(source, edge, signal)
        engine.save()
        self.history.append(self.capture(engine, environments, navigation, time_gates, scans))
        self.future = []
        self.print(f"[GOD MODE] FORWARD via {edge['id']}")
        self.show_position(engine)
        return True

    def next_mission(self, engine: Any, environments: Any, navigation: Any, time_gates: Any, scans: Any) -> bool:
        if not self.active:
            return False
        missions = engine.graph.data.get("missions", [])
        if not isinstance(missions, list) or len(missions) < 2:
            self.print("[GOD MODE] No later mission is defined in this graph.")
            return False
        active = set(engine.state.get("active_nodes", []))
        current_index = -1
        for index, mission in enumerate(missions):
            nodes = set(mission.get("nodes", []))
            if active & nodes:
                current_index = index
                break
        next_index = current_index + 1
        if next_index >= len(missions):
            self.print("[GOD MODE] Already at the final mission.")
            return False
        target = str(missions[next_index]["start_node"])
        engine.state["active_nodes"] = [target]
        engine.state.setdefault("entered_at", {})[target] = utc_now()
        engine._enter_nodes([target])
        engine.save()
        self._sync_audio(engine, environments)
        self.history.append(self.capture(engine, environments, navigation, time_gates, scans))
        self.future = []
        self.print(f"[GOD MODE] MISSION SKIP → {missions[next_index].get('title', target)}")
        self.show_position(engine)
        return True

    def show_position(self, engine: Any) -> None:
        values: list[str] = []
        for node_id in engine.state.get("active_nodes", []):
            node = engine.graph.nodes.get(node_id, {})
            values.append(str(node.get("title") or node_id.replace("_", " ").title()))
        self.print("[GOD MODE] Current node(s): " + (" | ".join(values) if values else "none"))
