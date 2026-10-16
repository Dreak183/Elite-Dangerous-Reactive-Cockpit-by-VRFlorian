from __future__ import annotations

import copy
import json
import re
import shutil
from collections import deque
from pathlib import Path
from typing import Any

from .model import NODE_KINDS, validate_graph
from .storage import atomic_write_json


class EditorError(ValueError):
    pass


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip()).strip("_").lower()
    return value or "node"


def _unique(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    number = 2
    while f"{base}_{number}" in existing:
        number += 1
    return f"{base}_{number}"


class GraphDocument:
    """Pure graph-editing model used by the Tk editor and headless tests.

    Runtime data is preserved verbatim unless a corresponding editor operation
    changes it. Canvas positions live below ``editor.layout`` and are ignored by
    the graph runtime.
    """

    def __init__(self, data: dict[str, Any], path: Path | None = None) -> None:
        if not isinstance(data, dict):
            raise EditorError("Graph root must be a JSON object")
        self.data = copy.deepcopy(data)
        self.path = path.resolve() if path else None
        self.dirty = False
        self._ensure_editor_data()
        if self.nodes and not any(node_id in self._layout_nodes() for node_id in self.nodes):
            self.auto_layout()
            self.dirty = False

    @classmethod
    def new(cls) -> "GraphDocument":
        return cls(
            {
                "schema": "platform.story-graph.v1",
                "id": "new_story",
                "version": "1.0.0",
                "title": "New story graph",
                "description": "",
                "start": "start",
                "variables": {},
                "environments": [],
                "solar_clocks": [],
                "solar_sites": [],
                "time_gates": [],
                "time_events": [],
                "scan_sets": [],
                "nodes": {"start": {"kind": "start"}},
                "edges": [],
                "editor": {
                    "version": 1,
                    "layout": {"nodes": {"start": {"x": 160.0, "y": 160.0}}},
                },
            }
        )

    @classmethod
    def load(cls, path: Path) -> "GraphDocument":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise EditorError(f"Cannot open {path}: {exc}") from exc
        return cls(data, path)

    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        nodes = self.data.setdefault("nodes", {})
        return nodes if isinstance(nodes, dict) else {}

    @property
    def edges(self) -> list[dict[str, Any]]:
        edges = self.data.setdefault("edges", [])
        return edges if isinstance(edges, list) else []

    @property
    def environments(self) -> list[dict[str, Any]]:
        environments = self.data.setdefault("environments", [])
        return environments if isinstance(environments, list) else []

    @property
    def navigators(self) -> list[dict[str, Any]]:
        navigators = self.data.setdefault("navigators", [])
        return navigators if isinstance(navigators, list) else []

    def collection(self, name: str) -> list[dict[str, Any]]:
        value = self.data.setdefault(name, [])
        return value if isinstance(value, list) else []

    @property
    def solar_clocks(self) -> list[dict[str, Any]]:
        return self.collection("solar_clocks")

    @property
    def solar_sites(self) -> list[dict[str, Any]]:
        return self.collection("solar_sites")

    @property
    def time_gates(self) -> list[dict[str, Any]]:
        return self.collection("time_gates")

    @property
    def time_events(self) -> list[dict[str, Any]]:
        return self.collection("time_events")

    @property
    def scan_sets(self) -> list[dict[str, Any]]:
        return self.collection("scan_sets")

    def collection_item(self, name: str, item_id: str) -> dict[str, Any]:
        for item in self.collection(name):
            if item.get("id") == item_id:
                return item
        raise EditorError(f"Unknown {name} item {item_id!r}")

    def update_collection_item(self, name: str, item_id: str, replacement: dict[str, Any]) -> str:
        current = self.collection_item(name, item_id)
        new_id = _slug(str(replacement.get("id") or item_id))
        if any(item is not current and item.get("id") == new_id for item in self.collection(name)):
            raise EditorError(f"{name} id {new_id!r} already exists")
        value = copy.deepcopy(replacement)
        value["id"] = new_id
        if value != current:
            current.clear(); current.update(value); self.dirty = True
            if new_id != item_id:
                if name == "solar_clocks":
                    for site in self.solar_sites:
                        if site.get("clock") == item_id:
                            site["clock"] = new_id
                elif name == "solar_sites":
                    for gate in self.time_gates:
                        if gate.get("site") == item_id:
                            gate["site"] = new_id
                elif name == "time_gates":
                    for zone in self.environments:
                        if zone.get("time_gate") == item_id:
                            zone["time_gate"] = new_id
                    for event in self.time_events:
                        if event.get("gate") == item_id:
                            event["gate"] = new_id
                    old_prefix = f"world.time.gates.{item_id}."
                    new_prefix = f"world.time.gates.{new_id}."

                    def replace_paths(value: Any) -> None:
                        if isinstance(value, dict):
                            if isinstance(value.get("path"), str) and value["path"].startswith(old_prefix):
                                value["path"] = new_prefix + value["path"][len(old_prefix):]
                            for child in value.values():
                                replace_paths(child)
                        elif isinstance(value, list):
                            for child in value:
                                replace_paths(child)

                    replace_paths(self.edges)
                    replace_paths(self.environments)
        return new_id

    def delete_collection_item(self, name: str, item_id: str) -> None:
        references: list[str] = []
        if name == "solar_clocks":
            references = [f"site {item.get('id')}" for item in self.solar_sites if item.get("clock") == item_id]
        elif name == "solar_sites":
            references = [f"gate {item.get('id')}" for item in self.time_gates if item.get("site") == item_id]
        elif name == "time_gates":
            references.extend(f"environment {item.get('id')}" for item in self.environments if item.get("time_gate") == item_id)
            references.extend(f"event {item.get('id')}" for item in self.time_events if item.get("gate") == item_id)
            needle = f"world.time.gates.{item_id}."
            if needle in json.dumps(self.edges):
                references.append("one or more graph edges")
        elif name == "scan_sets":
            needle = f"scan.{item_id}."
            if needle in json.dumps(self.edges):
                references.append("one or more graph edges")
        if references:
            raise EditorError(f"Cannot delete {item_id!r}; it is used by " + ", ".join(references) + ".")
        before = len(self.collection(name))
        self.data[name] = [item for item in self.collection(name) if item.get("id") != item_id]
        if len(self.data[name]) == before:
            raise EditorError(f"Unknown {name} item {item_id!r}")
        self.dirty = True

    def _ensure_editor_data(self) -> None:
        editor = self.data.get("editor")
        if not isinstance(editor, dict):
            editor = {}
            self.data["editor"] = editor
        editor.setdefault("version", 1)
        layout = editor.get("layout")
        if not isinstance(layout, dict):
            layout = {}
            editor["layout"] = layout
        if not isinstance(layout.get("nodes"), dict):
            layout["nodes"] = {}

    def _layout_nodes(self) -> dict[str, dict[str, float]]:
        self._ensure_editor_data()
        return self.data["editor"]["layout"]["nodes"]

    def position(self, node_id: str) -> tuple[float, float]:
        value = self._layout_nodes().get(node_id, {})
        try:
            return float(value["x"]), float(value["y"])
        except (KeyError, TypeError, ValueError):
            index = list(self.nodes).index(node_id) if node_id in self.nodes else 0
            return 160.0 + (index % 4) * 270.0, 140.0 + (index // 4) * 170.0

    def set_position(self, node_id: str, x: float, y: float) -> None:
        if node_id not in self.nodes:
            raise EditorError(f"Unknown node {node_id!r}")
        position = {"x": round(float(x), 1), "y": round(float(y), 1)}
        if self._layout_nodes().get(node_id) != position:
            self._layout_nodes()[node_id] = position
            self.dirty = True

    def set_metadata(self, graph_id: str, version: str, title: str, description: str, start: str) -> None:
        graph_id = graph_id.strip()
        version = version.strip()
        title = title.strip()
        if not graph_id or not version or not title:
            raise EditorError("Graph id, version and title cannot be empty")
        if start not in self.nodes:
            raise EditorError(f"Start node {start!r} does not exist")
        updates = {
            "id": graph_id,
            "version": version,
            "title": title,
            "description": description,
            "start": start,
        }
        for key, value in updates.items():
            if self.data.get(key) != value:
                self.data[key] = value
                self.dirty = True

    def set_variables(self, variables: dict[str, Any]) -> None:
        if not isinstance(variables, dict):
            raise EditorError("Variables must be a JSON object")
        if self.data.get("variables") != variables:
            self.data["variables"] = copy.deepcopy(variables)
            self.dirty = True

    def add_node(self, kind: str = "beat", node_id: str | None = None, x: float = 200, y: float = 200) -> str:
        if kind not in NODE_KINDS:
            raise EditorError(f"Unsupported node kind {kind!r}")
        base = _slug(node_id or kind)
        node_id = _unique(base, set(self.nodes))
        node: dict[str, Any] = {"kind": kind}
        if kind == "checkpoint":
            node["checkpoint_id"] = node_id.upper()
        elif kind == "join":
            node["join_count"] = 2
        self.nodes[node_id] = node
        self._layout_nodes()[node_id] = {"x": round(float(x), 1), "y": round(float(y), 1)}
        self.dirty = True
        return node_id

    def rename_node(self, old_id: str, new_id: str) -> str:
        new_id = _slug(new_id)
        if old_id not in self.nodes:
            raise EditorError(f"Unknown node {old_id!r}")
        if new_id != old_id and new_id in self.nodes:
            raise EditorError(f"Node {new_id!r} already exists")
        if new_id == old_id:
            return old_id
        rebuilt: dict[str, dict[str, Any]] = {}
        for node_id, node in self.nodes.items():
            rebuilt[new_id if node_id == old_id else node_id] = node
        self.data["nodes"] = rebuilt
        if self.data.get("start") == old_id:
            self.data["start"] = new_id
        for edge in self.edges:
            if edge.get("from") == old_id:
                edge["from"] = new_id
            target = edge.get("to")
            if target == old_id:
                edge["to"] = new_id
            elif isinstance(target, list):
                edge["to"] = [new_id if value == old_id else value for value in target]
        for node in self.nodes.values():
            if isinstance(node.get("join_from"), list):
                node["join_from"] = [new_id if value == old_id else value for value in node["join_from"]]
        layout = self._layout_nodes()
        if old_id in layout:
            layout[new_id] = layout.pop(old_id)
        self.dirty = True
        return new_id

    def update_node(
        self,
        node_id: str,
        *,
        new_id: str,
        kind: str,
        checkpoint_id: str = "",
        join_from: list[str] | None = None,
        join_count: int | None = None,
        on_enter: list[dict[str, Any]] | None = None,
    ) -> str:
        if kind not in NODE_KINDS:
            raise EditorError(f"Unsupported node kind {kind!r}")
        new_id = self.rename_node(node_id, new_id)
        node = self.nodes[new_id]
        before = copy.deepcopy(node)
        node["kind"] = kind
        if kind == "checkpoint":
            node["checkpoint_id"] = checkpoint_id.strip() or new_id.upper()
        else:
            node.pop("checkpoint_id", None)
        if kind == "join":
            if join_from:
                node["join_from"] = list(join_from)
                node.pop("join_count", None)
            else:
                node["join_count"] = int(join_count or 2)
                node.pop("join_from", None)
        else:
            node.pop("join_from", None)
            node.pop("join_count", None)
        if on_enter:
            node["on_enter"] = copy.deepcopy(on_enter)
        else:
            node.pop("on_enter", None)
        if node != before:
            self.dirty = True
        return new_id

    def delete_node(self, node_id: str) -> list[str]:
        if node_id not in self.nodes:
            raise EditorError(f"Unknown node {node_id!r}")
        if self.data.get("start") == node_id:
            raise EditorError("Choose a different start node before deleting this node")
        removed_edges = [str(edge.get("id", "")) for edge in self.edges if edge.get("from") == node_id or node_id in self._targets(edge)]
        self.data["edges"] = [edge for edge in self.edges if edge.get("from") != node_id and node_id not in self._targets(edge)]
        del self.nodes[node_id]
        self._layout_nodes().pop(node_id, None)
        for node in self.nodes.values():
            if isinstance(node.get("join_from"), list):
                node["join_from"] = [value for value in node["join_from"] if value != node_id]
        self.dirty = True
        return removed_edges

    @staticmethod
    def _targets(edge: dict[str, Any]) -> list[str]:
        target = edge.get("to")
        return list(target) if isinstance(target, list) else [target]

    def add_edge(
        self,
        source: str,
        target: str | list[str],
        when: dict[str, Any] | None = None,
        edge_id: str | None = None,
        priority: int = 0,
    ) -> str:
        if source not in self.nodes:
            raise EditorError(f"Unknown source node {source!r}")
        targets = target if isinstance(target, list) else [target]
        if not targets or any(item not in self.nodes for item in targets):
            raise EditorError("Every edge target must be an existing node")
        base = _slug(edge_id or f"{source}_to_{'_'.join(targets)}")
        edge_id = _unique(base, {str(edge.get("id")) for edge in self.edges})
        edge: dict[str, Any] = {
            "id": edge_id,
            "from": source,
            "to": list(targets) if len(targets) > 1 else targets[0],
            "when": copy.deepcopy(when or {"always": True}),
        }
        if priority:
            edge["priority"] = int(priority)
        self.edges.append(edge)
        self.dirty = True
        return edge_id

    def edge(self, edge_id: str) -> dict[str, Any]:
        for edge in self.edges:
            if edge.get("id") == edge_id:
                return edge
        raise EditorError(f"Unknown edge {edge_id!r}")

    def update_edge(
        self,
        edge_id: str,
        *,
        new_id: str,
        source: str,
        targets: list[str],
        when: dict[str, Any],
        priority: int = 0,
    ) -> str:
        edge = self.edge(edge_id)
        new_id = _slug(new_id)
        if any(other is not edge and other.get("id") == new_id for other in self.edges):
            raise EditorError(f"Edge {new_id!r} already exists")
        if source not in self.nodes or not targets or any(target not in self.nodes for target in targets):
            raise EditorError("Edge source and targets must be existing nodes")
        if not isinstance(when, dict):
            raise EditorError("Edge predicate must be a JSON object")
        replacement: dict[str, Any] = copy.deepcopy(edge)
        replacement.update({
            "id": new_id,
            "from": source,
            "to": targets if len(targets) > 1 else targets[0],
            "when": copy.deepcopy(when),
        })
        if priority:
            replacement["priority"] = int(priority)
        else:
            replacement.pop("priority", None)
        if replacement != edge:
            edge.clear()
            edge.update(replacement)
            self.dirty = True
        return new_id

    def delete_edge(self, edge_id: str) -> None:
        before = len(self.edges)
        self.data["edges"] = [edge for edge in self.edges if edge.get("id") != edge_id]
        if len(self.data["edges"]) == before:
            raise EditorError(f"Unknown edge {edge_id!r}")
        self.dirty = True

    def environment(self, environment_id: str) -> dict[str, Any]:
        for environment in self.environments:
            if environment.get("id") == environment_id:
                return environment
        raise EditorError(f"Unknown environment {environment_id!r}")

    def add_environment(self, environment: dict[str, Any]) -> str:
        if not isinstance(environment, dict):
            raise EditorError("Environment must be an object")
        value = copy.deepcopy(environment)
        environment_id = _unique(_slug(str(value.get("id") or "environment")), {
            str(item.get("id")) for item in self.environments
        })
        value["id"] = environment_id
        self.environments.append(value)
        self.dirty = True
        return environment_id

    def update_environment(self, environment_id: str, replacement: dict[str, Any]) -> str:
        current = self.environment(environment_id)
        value = copy.deepcopy(replacement)
        new_id = _slug(str(value.get("id") or environment_id))
        if any(item is not current and item.get("id") == new_id for item in self.environments):
            raise EditorError(f"Environment {new_id!r} already exists")
        value["id"] = new_id
        if current != value:
            old_layers = current.get("layers", []) if isinstance(current.get("layers"), list) else []
            new_layers = value.get("layers", []) if isinstance(value.get("layers"), list) else []
            layer_renames: dict[str, str] = {}
            new_by_id = {str(item.get("id")): item for item in new_layers if isinstance(item, dict) and item.get("id")}
            for old in old_layers:
                if not isinstance(old, dict) or not old.get("id"):
                    continue
                old_id = str(old["id"])
                candidate = new_by_id.get(old_id)
                if candidate is None:
                    matches = [item for item in new_layers if isinstance(item, dict) and item.get("type") == old.get("type") and item.get("asset", item.get("cue")) == old.get("asset", old.get("cue"))]
                    candidate = matches[0] if len(matches) == 1 else None
                if candidate is not None:
                    old_ref = f"environment.{environment_id}.{old_id}"
                    new_ref = f"environment.{new_id}.{candidate['id']}"
                    if old_ref != new_ref:
                        layer_renames[old_ref] = new_ref
            for node in self.nodes.values():
                for effect in node.get("on_enter", []):
                    payload = effect.get("payload") if isinstance(effect, dict) else None
                    if not isinstance(payload, dict) or not isinstance(payload.get("layer"), str):
                        continue
                    layer = payload["layer"]
                    if layer in layer_renames:
                        payload["layer"] = layer_renames[layer]
                    elif layer.startswith(f"environment.{environment_id}.") and new_id != environment_id:
                        payload["layer"] = f"environment.{new_id}." + layer.split(".", 2)[2]
            for environment in self.environments:
                adjustments = environment.get("layer_adjustments", []) if isinstance(environment, dict) else []
                for adjustment in adjustments:
                    if not isinstance(adjustment, dict) or not isinstance(adjustment.get("target_layer"), str):
                        continue
                    target = adjustment["target_layer"]
                    if target in layer_renames:
                        adjustment["target_layer"] = layer_renames[target]
                    elif target.startswith(f"environment.{environment_id}.") and new_id != environment_id:
                        adjustment["target_layer"] = f"environment.{new_id}." + target.split(".", 2)[2]
            for adjustment in value.get("layer_adjustments", []):
                if not isinstance(adjustment, dict) or not isinstance(adjustment.get("target_layer"), str):
                    continue
                target = adjustment["target_layer"]
                if target in layer_renames:
                    adjustment["target_layer"] = layer_renames[target]
                elif target.startswith(f"environment.{environment_id}.") and new_id != environment_id:
                    adjustment["target_layer"] = f"environment.{new_id}." + target.split(".", 2)[2]
            if new_id != environment_id:
                for scan_set in self.scan_sets:
                    for target in scan_set.get("targets", []):
                        if target.get("detection_zone") == environment_id:
                            target["detection_zone"] = new_id
            current.clear()
            current.update(value)
            self.dirty = True
        return new_id

    def delete_environment(self, environment_id: str) -> None:
        prefix = f"environment.{environment_id}."
        references = []
        for node_id, node in self.nodes.items():
            for effect in node.get("on_enter", []):
                payload = effect.get("payload") if isinstance(effect, dict) else None
                if isinstance(payload, dict) and str(payload.get("layer", "")).startswith(prefix):
                    references.append(node_id)
        if references:
            raise EditorError(
                f"Environment {environment_id!r} is used by audio effects on: "
                + ", ".join(sorted(set(references)))
                + ". Remove or retarget those effects first."
            )
        adjustment_references = [
            str(environment.get("id"))
            for environment in self.environments
            for adjustment in environment.get("layer_adjustments", [])
            if isinstance(adjustment, dict)
            and str(adjustment.get("target_layer", "")).startswith(prefix)
        ]
        if adjustment_references:
            raise EditorError(
                f"Environment {environment_id!r} is adjusted by: "
                + ", ".join(sorted(set(adjustment_references)))
                + ". Remove or retarget those adjustments first."
            )
        scan_references = [
            f"{scan_set.get('id')}.{target.get('id')}"
            for scan_set in self.scan_sets for target in scan_set.get("targets", [])
            if target.get("detection_zone") == environment_id
        ]
        if scan_references:
            raise EditorError(
                f"Environment {environment_id!r} is a detection field for: "
                + ", ".join(scan_references)
                + ". Retarget those scan objectives first."
            )
        before = len(self.environments)
        self.data["environments"] = [
            environment for environment in self.environments if environment.get("id") != environment_id
        ]
        if len(self.data["environments"]) == before:
            raise EditorError(f"Unknown environment {environment_id!r}")
        self.dirty = True

    def auto_layout(self) -> None:
        nodes = list(self.nodes)
        if not nodes:
            return
        start = self.data.get("start") if self.data.get("start") in self.nodes else nodes[0]
        levels: dict[str, int] = {start: 0}
        queue: deque[str] = deque([start])
        while queue:
            source = queue.popleft()
            for edge in self.edges:
                if edge.get("from") != source:
                    continue
                for target in self._targets(edge):
                    if target in self.nodes and target not in levels:
                        levels[target] = levels[source] + 1
                        queue.append(target)
        next_level = max(levels.values(), default=-1) + 1
        for node_id in nodes:
            if node_id not in levels:
                levels[node_id] = next_level
        grouped: dict[int, list[str]] = {}
        for node_id in nodes:
            grouped.setdefault(levels[node_id], []).append(node_id)
        for level, members in sorted(grouped.items()):
            for row, node_id in enumerate(members):
                self._layout_nodes()[node_id] = {"x": 170.0 + level * 300.0, "y": 130.0 + row * 160.0}
        self.dirty = True

    def validate(self) -> list[str]:
        return validate_graph(self.data)

    def save(self, path: Path | None = None) -> Path:
        destination = (path or self.path)
        if destination is None:
            raise EditorError("Choose a graph file before saving")
        destination = destination.resolve()
        errors = self.validate()
        if errors:
            raise EditorError("Graph is invalid and was not saved:\n- " + "\n- ".join(errors))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.copy2(destination, destination.with_suffix(destination.suffix + ".bak"))
        atomic_write_json(destination, self.data)
        self.path = destination
        self.dirty = False
        return destination
