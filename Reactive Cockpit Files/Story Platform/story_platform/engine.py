from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .model import StoryGraph
from .predicates import evaluate
from .storage import append_jsonl, atomic_write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GraphRuntime:
    """Durable token runtime for a directed story graph.

    State is persisted after every transition. If the previous process did not
    shut down cleanly, the exact last checkpoint snapshot is restored.
    """

    def __init__(
        self,
        graph: StoryGraph,
        state_path: Path,
        event_log: Path,
        outbox: Path,
        printer: Callable[[str], None] = print,
        emit_observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.graph = graph
        self.state_path = state_path
        self.event_log = event_log
        self.outbox = outbox
        self.print = printer
        self.emit_observer = emit_observer
        self.state = self._load_or_create()

    def _fresh_state(self) -> dict[str, Any]:
        now = utc_now()
        return {
            "schema": "platform.story-state.v1",
            "graph_id": self.graph.graph_id,
            "graph_version": self.graph.version,
            "active_nodes": [self.graph.start],
            "entered_at": {self.graph.start: now},
            "variables": copy.deepcopy(self.graph.defaults),
            "visits": {},
            "completed_nodes": [],
            "join_arrivals": {},
            "next_step": "",
            "next_choices": [],
            "checkpoint": None,
            "clean_shutdown": True,
            "updated_at": now,
        }

    def _load_or_create(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._fresh_state()
        try:
            import json

            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Cannot load runtime state: {exc}") from exc
        if saved.get("graph_id") != self.graph.graph_id:
            raise RuntimeError("Saved state belongs to a different graph; use reset before changing graphs")
        if saved.get("graph_version") != self.graph.version:
            raise RuntimeError("Saved state graph version differs; migrate or reset explicitly")
        if not saved.get("clean_shutdown", True) and saved.get("checkpoint"):
            checkpoint = saved["checkpoint"]
            restored = copy.deepcopy(checkpoint["state"])
            restored["checkpoint"] = checkpoint
            restored["clean_shutdown"] = True
            restored["updated_at"] = utc_now()
            append_jsonl(self.event_log, {"at": utc_now(), "type": "recovery.rollback", "checkpoint": checkpoint["id"]})
            return restored
        return saved

    def _checkpoint_snapshot(self, checkpoint_id: str) -> None:
        snapshot = copy.deepcopy(self.state)
        snapshot["checkpoint"] = None
        snapshot["clean_shutdown"] = True
        self.state["checkpoint"] = {"id": checkpoint_id, "at": utc_now(), "state": snapshot}
        self._event("checkpoint.saved", checkpoint=checkpoint_id)

    def rollback_to_checkpoint(self, reason: str) -> bool:
        checkpoint = self.state.get("checkpoint")
        if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("state"), dict):
            return False
        restored = copy.deepcopy(checkpoint["state"])
        restored["checkpoint"] = copy.deepcopy(checkpoint)
        restored["clean_shutdown"] = False
        restored["updated_at"] = utc_now()
        self.state = restored
        self.save()
        self._event("recovery.rollback", checkpoint=checkpoint.get("id"), reason=reason)
        self.reemit_current_objective()
        return True

    def _event(self, event_type: str, **fields: Any) -> None:
        row = {"at": utc_now(), "type": event_type, "graph_id": self.graph.graph_id, **fields}
        append_jsonl(self.event_log, row)

    def _emit(self, node_id: str, channel: str, payload: dict[str, Any]) -> None:
        command = {
            "at": utc_now(),
            "graph_id": self.graph.graph_id,
            "node": node_id,
            "channel": channel,
            "payload": self._render(payload),
        }
        append_jsonl(self.outbox, command)
        if self.emit_observer is not None:
            self.emit_observer(copy.deepcopy(command))
        self._event("effect.emit", node=node_id, channel=channel)

    def save(self) -> None:
        self.state["updated_at"] = utc_now()
        atomic_write_json(self.state_path, self.state)

    def start_session(self) -> None:
        self.state["clean_shutdown"] = False
        if not self.state["visits"]:
            self._enter_nodes(list(self.state["active_nodes"]), initial=True)
        self.save()
        self._drain_immediate({"name": "system.start", "source": "system", "at": utc_now(), "data": {}})

    def stop_session(self) -> None:
        self.state["clean_shutdown"] = True
        self.save()
        self._event("runtime.clean_shutdown")

    def reset(self) -> None:
        self.state = self._fresh_state()
        self.save()

    def _render(self, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return value.format_map({"variables": self.state["variables"], **self.state["variables"]})
            except (KeyError, ValueError):
                return value
        return copy.deepcopy(value)

    def _apply_effect(self, effect: dict[str, Any], node_id: str) -> None:
        kind = effect["type"]
        if kind == "log":
            message = str(self._render(effect.get("message", "")))
            self.print(f"[GRAPH] {message}")
            self._event("effect.log", node=node_id, message=message)
        elif kind == "set":
            self.state["variables"][effect["variable"]] = self._render(effect.get("value"))
        elif kind == "increment":
            name = effect["variable"]
            self.state["variables"][name] = self.state["variables"].get(name, 0) + effect.get("amount", 1)
        elif kind == "next_step":
            self.state["next_step"] = str(self._render(effect.get("text", "")))
            self.print(f"[NEXT] {self.state['next_step']}")
            payload = {"text": self.state["next_step"]}
            if isinstance(effect.get("choices"), list):
                self.state["next_choices"] = self._render(effect["choices"])
            else:
                self.state["next_choices"] = []
            if self.state["next_choices"]:
                payload["choices"] = self.state["next_choices"]
            self._emit(node_id, "objective", payload)
        elif kind == "emit":
            self._emit(node_id, effect["channel"], effect.get("payload", {}))

    def reemit_current_objective(self) -> None:
        text = str(self.state.get("next_step") or "").strip()
        if text:
            self._emit("runtime", "objective", {
                "text": text, "choices": list(self.state.get("next_choices") or []), "restored": True
            })

    def _enter_nodes(self, node_ids: list[str], initial: bool = False) -> None:
        now = utc_now()
        for node_id in node_ids:
            node = self.graph.nodes[node_id]
            self.state["entered_at"][node_id] = now
            self.state["visits"][node_id] = int(self.state["visits"].get(node_id, 0)) + 1
            if not initial or self.state["visits"][node_id] == 1:
                for effect in node.get("on_enter", []):
                    self._apply_effect(effect, node_id)
            self._event("node.enter", node=node_id, kind=node["kind"], visit=self.state["visits"][node_id])
            if node["kind"] == "checkpoint":
                self._checkpoint_snapshot(node.get("checkpoint_id", node_id))

    def _context(self, node_id: str, signal: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
        entered = parse_time(self.state["entered_at"].get(node_id, utc_now()))
        elapsed = max(0.0, (datetime.now(timezone.utc) - entered).total_seconds())
        return {
            "signal": signal,
            "world": world,
            "variables": self.state["variables"],
            "state": self.state,
            "node_elapsed_seconds": elapsed,
        }

    def dispatch(self, signal: dict[str, Any], world: dict[str, Any]) -> bool:
        recovery_when = self.graph.data.get("recovery_when")
        if isinstance(recovery_when, dict):
            context = {
                "signal": signal, "world": world, "variables": self.state["variables"],
                "state": self.state, "node_elapsed_seconds": 0.0,
            }
            if evaluate(recovery_when, context):
                rolled_back = self.rollback_to_checkpoint(str(signal.get("name", "recovery signal")))
                if rolled_back:
                    self._drain_immediate(signal, world)
                return rolled_back
        transitioned = False
        for node_id in list(self.state["active_nodes"]):
            context = self._context(node_id, signal, world)
            for edge in self.graph.outgoing(node_id):
                if evaluate(edge.get("when", {"always": True}), context):
                    self._transition(node_id, edge, signal)
                    transitioned = True
                    break
        if transitioned:
            self.save()
            self._drain_immediate(signal, world)
        return transitioned

    def _transition(self, source: str, edge: dict[str, Any], signal: dict[str, Any]) -> None:
        targets = edge["to"] if isinstance(edge["to"], list) else [edge["to"]]
        active = [node for node in self.state["active_nodes"] if node != source]
        entered_targets: list[str] = []
        for target in targets:
            target_node = self.graph.nodes[target]
            if target_node["kind"] == "join":
                arrivals = self.state.setdefault("join_arrivals", {}).setdefault(target, [])
                if source not in arrivals:
                    arrivals.append(source)
                required_sources = target_node.get("join_from")
                ready = (
                    all(item in arrivals for item in required_sources)
                    if isinstance(required_sources, list)
                    else len(arrivals) >= int(target_node["join_count"])
                )
                if ready and target not in active:
                    active.append(target)
                    entered_targets.append(target)
            elif target not in active:
                active.append(target)
                entered_targets.append(target)
        self.state["active_nodes"] = active
        if source not in self.state["completed_nodes"]:
            self.state["completed_nodes"].append(source)
        self._event("edge.traverse", edge=edge["id"], source=source, targets=targets, signal=signal.get("name"))
        self._enter_nodes(entered_targets)

    def _drain_immediate(self, signal: dict[str, Any], world: dict[str, Any] | None = None) -> None:
        world = world or {}
        for _ in range(100):
            moved = False
            for node_id in list(self.state["active_nodes"]):
                for edge in self.graph.outgoing(node_id):
                    predicate = edge.get("when", {"always": True})
                    if predicate == {"always": True} and evaluate(predicate, self._context(node_id, signal, world)):
                        self._transition(node_id, edge, signal)
                        self.save()
                        moved = True
                        break
                if moved:
                    break
            if not moved:
                return
        raise RuntimeError("Graph exceeded 100 immediate transitions; possible unconditional cycle")
