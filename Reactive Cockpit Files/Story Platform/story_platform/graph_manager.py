from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import APP_DIR, load_config, resolve_path, save_config
from .engine import utc_now
from .model import GraphError, StoryGraph, load_graph
from .storage import append_jsonl, atomic_write_json


GRAPH_SWITCH_EXIT_CODE = 75


class GraphSelectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class GraphEntry:
    graph_id: str
    version: str
    title: str
    path: Path

    def state_value(self, app_dir: Path) -> dict[str, str]:
        try:
            path = self.path.relative_to(app_dir).as_posix()
        except ValueError:
            path = str(self.path)
        return {
            "id": self.graph_id,
            "version": self.version,
            "title": self.title,
            "path": path,
        }


def _version_key(value: str) -> tuple[tuple[int, int | str], ...]:
    """Sort ordinary semantic-ish versions without imposing a graph format."""
    parts = [item for item in re.split(r"([0-9]+)", str(value).casefold()) if item]
    return tuple((1, int(item)) if item.isdigit() else (0, item) for item in parts)


class GraphManager:
    """Generic, durable selection and switching for ordinary story graphs.

    ``active_graph`` remains the author-facing source of truth. The manager
    validates it at startup, records the exact id/version/path for recovery,
    and falls back to the configured world graph only when that selection is
    unavailable. A live switch updates the same configuration atomically, then
    asks the supervisor to restart only graph-facing sibling processes.
    """

    def __init__(
        self,
        app_dir: Path = APP_DIR,
        *,
        config_saver: Callable[[dict[str, Any]], None] = save_config,
        state_path: Path | None = None,
        event_log: Path | None = None,
    ) -> None:
        self.app_dir = app_dir.resolve()
        self.stories_dir = self.app_dir / "stories"
        runtime_dir = self.app_dir / "runtime"
        self.state_path = state_path or (runtime_dir / "graph_manager_state.json")
        self.event_log = event_log or (runtime_dir / "graph_manager_events.jsonl")
        self.config_saver = config_saver
        self.switch_requested = False
        self.switch_target: GraphEntry | None = None

    def _event(self, event_type: str, **fields: Any) -> None:
        append_jsonl(self.event_log, {"at": utc_now(), "type": event_type, **fields})

    def catalog(self) -> dict[str, list[GraphEntry]]:
        result: dict[str, list[GraphEntry]] = {}
        exact: dict[tuple[str, str], Path] = {}
        if not self.stories_dir.is_dir():
            return result
        for path in sorted(self.stories_dir.rglob("*.json")):
            try:
                graph = load_graph(path)
            except GraphError:
                continue
            key = (graph.graph_id, graph.version)
            previous = exact.get(key)
            if previous is not None and previous != graph.path:
                raise GraphSelectionError(
                    f"Duplicate graph identity {graph.graph_id} {graph.version}: "
                    f"{previous} and {graph.path}"
                )
            exact[key] = graph.path
            result.setdefault(graph.graph_id, []).append(GraphEntry(
                graph.graph_id, graph.version, str(graph.data.get("title", graph.graph_id)), graph.path,
            ))
        for entries in result.values():
            entries.sort(key=lambda item: (_version_key(item.version), str(item.path)), reverse=True)
        return result

    def resolve_graph(self, graph_id: str, version: str | None = None) -> GraphEntry:
        entries = self.catalog().get(str(graph_id), [])
        if version is not None:
            entries = [item for item in entries if item.version == str(version)]
        if not entries:
            suffix = f" version {version}" if version is not None else ""
            raise GraphSelectionError(f"Graph {graph_id!r}{suffix} is not installed")
        return entries[0]

    def _entry_from_path(self, path: Path) -> GraphEntry:
        try:
            graph = load_graph(path)
        except GraphError as exc:
            raise GraphSelectionError(str(exc)) from exc
        return GraphEntry(
            graph.graph_id, graph.version,
            str(graph.data.get("title", graph.graph_id)), graph.path,
        )

    def _save_selection(
        self,
        config: dict[str, Any],
        entry: GraphEntry,
        *,
        reason: str,
        count_switch: bool,
    ) -> None:
        previous: dict[str, Any] | None = None
        prior_previous: dict[str, Any] | None = None
        switch_count = 0
        if self.state_path.is_file():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(state.get("active_graph"), dict):
                    previous = state["active_graph"]
                if isinstance(state.get("previous_graph"), dict):
                    prior_previous = state["previous_graph"]
                switch_count = int(state.get("switch_count", 0))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        selected = entry.state_value(self.app_dir)
        changed = not previous or any(
            previous.get(key) != selected.get(key) for key in ("id", "version", "path")
        )
        if count_switch and changed:
            switch_count += 1
        value = {
            "schema": "platform.graph-manager-state.v1",
            "active_graph": selected,
            "previous_graph": previous if changed else prior_previous,
            "switch_count": switch_count,
            "last_reason": reason,
            "updated_at": utc_now(),
        }
        atomic_write_json(self.state_path, value)
        if config.get("active_graph") != selected["path"]:
            config["active_graph"] = selected["path"]
            self.config_saver(config)
        if changed:
            self._event(
                "graph.switched" if count_switch else "graph.selected",
                previous=previous, active=selected, reason=reason,
            )

    def resolve_startup(self, config: dict[str, Any] | None = None) -> Path:
        config = config or load_config()
        raw = str(config.get("active_graph", "")).strip()
        if raw:
            try:
                entry = self._entry_from_path(resolve_path(raw, relative_to=self.app_dir))
                self._save_selection(config, entry, reason="startup selection", count_switch=False)
                return entry.path
            except GraphSelectionError as active_error:
                failure = str(active_error)
        else:
            failure = "No active graph is configured"
        manager = config.get("graph_manager") if isinstance(config.get("graph_manager"), dict) else {}
        fallback_id = str(manager.get("default_graph_id", "yggdrasil")).strip() or "yggdrasil"
        try:
            fallback = self.resolve_graph(fallback_id)
        except GraphSelectionError as fallback_error:
            raise GraphSelectionError(
                f"{failure}. Fallback graph {fallback_id!r} is unavailable: {fallback_error}"
            ) from fallback_error
        self._save_selection(config, fallback, reason=f"fallback after: {failure}", count_switch=False)
        self._event("graph.fallback", active=fallback.state_value(self.app_dir), failure=failure)
        return fallback.path

    def request_switch(
        self,
        config: dict[str, Any],
        graph_id: str,
        *,
        version: str | None = None,
        reason: str = "graph switch request",
    ) -> bool:
        try:
            target = self.resolve_graph(graph_id, version)
            current = self._entry_from_path(resolve_path(str(config["active_graph"]), relative_to=self.app_dir))
        except GraphSelectionError as exc:
            self._event(
                "graph.switch_rejected", requested_id=graph_id,
                requested_version=version, reason=reason, error=str(exc),
            )
            raise
        if current.path == target.path:
            self._event("graph.switch_ignored", active=current.state_value(self.app_dir), reason="already active")
            return False
        self._save_selection(config, target, reason=reason, count_switch=True)
        self.switch_target = target
        self.switch_requested = True
        return True

    def observe_signal(self, signal: dict[str, Any], config: dict[str, Any]) -> bool:
        if signal.get("name") != "platform.graph.switch":
            return False
        data = signal.get("data") if isinstance(signal.get("data"), dict) else {}
        graph_id = str(data.get("graph_id", "")).strip()
        if not graph_id:
            error = "platform.graph.switch requires data.graph_id"
            self._event(
                "graph.switch_rejected", requested_id="", requested_version=None,
                reason=str(data.get("reason", "external graph switch")), error=error,
            )
            raise GraphSelectionError(error)
        version = str(data["version"]).strip() if data.get("version") is not None else None
        return self.request_switch(
            config, graph_id, version=version,
            reason=str(data.get("reason", "external graph switch")),
        )

    def observe_command(self, command: dict[str, Any], config: dict[str, Any]) -> bool:
        if command.get("channel") != "platform":
            return False
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        if payload.get("action") != "switch_graph":
            return False
        graph_id = str(payload.get("graph_id", "")).strip()
        if not graph_id:
            error = "platform switch_graph requires payload.graph_id"
            self._event(
                "graph.switch_rejected", requested_id="", requested_version=None,
                reason=str(payload.get("reason", f"requested by {command.get('graph_id', 'graph')}")),
                error=error,
            )
            raise GraphSelectionError(error)
        version = str(payload["version"]).strip() if payload.get("version") is not None else None
        return self.request_switch(
            config, graph_id, version=version,
            reason=str(payload.get("reason", f"requested by {command.get('graph_id', 'graph')}")),
        )
