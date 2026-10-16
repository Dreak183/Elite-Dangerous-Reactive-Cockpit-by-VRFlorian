from __future__ import annotations

import json
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .authoring import effect_summary, predicate_summary, simulator_preset_for
from .authoring_dialogs import (
    ControlBindingsDialog,
    EffectDialog,
    EffectsDialog,
    PredicateBuilderDialog,
    SignalBrowserDialog,
)
from .config import APP_DIR, configured_control_bindings, load_config, resolve_path, save_config
from .dry_run import DryRunError, DryRunSession
from .editor_model import EditorError, GraphDocument
from .environment_authoring import default_environment, environment_entry_preset, environment_summary
from .environment_dialogs import EnvironmentDialog
from .model import NODE_KINDS


NODE_COLORS = {
    "start": "#1f8a70",
    "wait": "#355c7d",
    "beat": "#6c5b7b",
    "choice": "#c06c84",
    "checkpoint": "#b8860b",
    "fork": "#8f5e2e",
    "join": "#7251a3",
    "end": "#a63d40",
}
NODE_WIDTH = 220
NODE_HEIGHT = 86


def _json(text: str, expected: type | tuple[type, ...], label: str) -> Any:
    try:
        value = json.loads(text or ("{}" if expected is dict else "[]"))
    except json.JSONDecodeError as exc:
        raise EditorError(f"{label} is not valid JSON: line {exc.lineno}, column {exc.colno}") from exc
    if not isinstance(value, expected):
        names = "/".join(item.__name__ for item in expected) if isinstance(expected, tuple) else expected.__name__
        raise EditorError(f"{label} must be a JSON {names}")
    return value


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


class EdgeDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, nodes: list[str], source: str | None = None) -> None:
        super().__init__(parent)
        self.title("Connect nodes")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: tuple[str, str, dict[str, Any]] | None = None
        self.source = tk.StringVar(value=source if source in nodes else (nodes[0] if nodes else ""))
        target_default = next((node for node in nodes if node != self.source.get()), self.source.get())
        self.target = tk.StringVar(value=target_default)
        self.condition = tk.StringVar(value="Always")
        self.value = tk.StringVar(value="")

        body = ttk.Frame(self, padding=16)
        body.grid(sticky="nsew")
        ttk.Label(body, text="From").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(body, textvariable=self.source, values=nodes, state="readonly", width=32).grid(row=0, column=1, pady=4)
        ttk.Label(body, text="To").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(body, textvariable=self.target, values=nodes, state="readonly", width=32).grid(row=1, column=1, pady=4)
        ttk.Label(body, text="Condition").grid(row=2, column=0, sticky="w", pady=4)
        condition = ttk.Combobox(
            body,
            textvariable=self.condition,
            values=["Always", "Signal", "Elapsed seconds", "Variable equals", "Custom JSON"],
            state="readonly",
            width=32,
        )
        condition.grid(row=2, column=1, pady=4)
        condition.bind("<<ComboboxSelected>>", self._hint)
        ttk.Label(body, text="Value").grid(row=3, column=0, sticky="w", pady=4)
        self.value_entry = ttk.Entry(body, textvariable=self.value, width=35)
        self.value_entry.grid(row=3, column=1, pady=4)
        self.hint = ttk.Label(body, text="No value required", foreground="#666666")
        self.hint.grid(row=4, column=1, sticky="w")
        ttk.Button(body, text="Open full trigger builder…", command=self._build_predicate).grid(
            row=5, column=1, sticky="ew", pady=(8, 0)
        )
        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Create edge", command=self._accept).pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.wait_visibility()
        self.focus_set()

    def _build_predicate(self) -> None:
        try:
            initial = self._predicate()
        except EditorError:
            initial = {"always": True}
        controls = load_config().get("controls", {}).get("bindings", {})
        dialog = PredicateBuilderDialog(self, initial, controls)
        self.wait_window(dialog)
        if dialog.result:
            self.condition.set("Custom JSON")
            self.value.set(json.dumps(dialog.result, ensure_ascii=False))
            self.hint.configure(text=predicate_summary(dialog.result), wraplength=330)

    def _hint(self, _event: tk.Event | None = None) -> None:
        hints = {
            "Always": "No value required",
            "Signal": "Example: journal.Disembark or control.accept",
            "Elapsed seconds": "Example: 3",
            "Variable equals": "Example: choice=accepted",
            "Custom JSON": 'Example: {"path":"world.vehicle","value":"on_foot"}',
        }
        self.hint.configure(text=hints[self.condition.get()])

    def _predicate(self) -> dict[str, Any]:
        condition = self.condition.get()
        value = self.value.get().strip()
        if condition == "Always":
            return {"always": True}
        if condition == "Signal":
            if not value:
                raise EditorError("Enter a signal name")
            return {"signal": value}
        if condition == "Elapsed seconds":
            try:
                seconds = float(value)
            except ValueError as exc:
                raise EditorError("Elapsed seconds must be a number") from exc
            return {"elapsed_seconds": seconds}
        if condition == "Variable equals":
            if "=" not in value:
                raise EditorError("Use variable=value")
            name, raw = value.split("=", 1)
            raw = raw.strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            return {"variable": name.strip(), "op": "eq", "value": parsed}
        return _json(value, dict, "Custom predicate")

    def _accept(self) -> None:
        try:
            predicate = self._predicate()
            if not self.source.get() or not self.target.get():
                raise EditorError("Choose both nodes")
            self.result = (self.source.get(), self.target.get(), predicate)
            self.destroy()
        except (EditorError, ValueError) as exc:
            messagebox.showerror("Cannot create edge", str(exc), parent=self)


class GraphEditor:
    def __init__(self, root: tk.Tk, graph_path: Path | None = None) -> None:
        self.root = root
        self.document = self._initial_document(graph_path)
        self.selection: tuple[str, str | None] = ("graph", None)
        self.drag_node: str | None = None
        self.drag_offset = (0.0, 0.0)
        self.inspector: ttk.Frame | None = None
        self.inspector_vars: dict[str, Any] = {}
        self.changing_selection = False
        self.simulator: DryRunSession | None = None
        self.simulator_graph: dict[str, Any] | None = None
        self.simulator_last_edge: str | None = None

        self.root.geometry("1520x900")
        self.root.minsize(1100, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._configure_style()
        self._build_menu()
        self._build_ui()
        self._bind_keys()
        self.refresh()

    @staticmethod
    def _initial_document(graph_path: Path | None) -> GraphDocument:
        if graph_path:
            return GraphDocument.load(graph_path)
        try:
            active = resolve_path(load_config()["active_graph"])
            if active.exists():
                return GraphDocument.load(active)
        except Exception:
            pass
        return GraphDocument.new()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Toolbar.TButton", padding=(9, 5))
        style.configure("Kind.TButton", padding=(5, 4))
        style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"))

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="New", accelerator="Ctrl+N", command=self.new)
        file_menu.add_command(label="Open…", accelerator="Ctrl+O", command=self.open)
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(label="Save As…", accelerator="Ctrl+Shift+S", command=self.save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menu.add_cascade(label="File", menu=file_menu)
        graph_menu = tk.Menu(menu, tearoff=False)
        from .wind_dialogs import WindSettingsDialog
        graph_menu.add_command(label="Player wind bindings…", command=lambda: WindSettingsDialog(self.root))
        graph_menu.add_command(label="Validate", accelerator="F5", command=self.validate)
        graph_menu.add_command(label="Auto layout", accelerator="Ctrl+L", command=self.auto_layout)
        graph_menu.add_command(label="Connect nodes…", accelerator="Ctrl+E", command=self.add_edge)
        graph_menu.add_command(label="Set active for next launch", command=self.set_active)
        graph_menu.add_command(label="Delete selection", accelerator="Delete", command=self.delete_selection)
        menu.add_cascade(label="Graph", menu=graph_menu)
        author_menu = tk.Menu(menu, tearoff=False)
        author_menu.add_command(label="Browse telemetry signals…", command=self.browse_simulation_signal)
        author_menu.add_command(label="Edit generic control bindings…", command=self.edit_control_bindings)
        author_menu.add_separator()
        author_menu.add_command(label="Add surface environment…", command=lambda: self.add_environment("surface"))
        author_menu.add_command(label="Add detection environment…", command=lambda: self.add_environment("detection"))
        author_menu.add_command(label="Add event-bounded environment…", command=lambda: self.add_environment("event"))
        author_menu.add_command(label="Add planetary glide sound…", command=lambda: self.add_environment("planetary_glide"))
        author_menu.add_command(label="Add coordinate navigator…", command=self.add_navigator)
        author_menu.add_separator()
        author_menu.add_command(label="Add body solar clock…", command=lambda: self.add_timing_item("solar_clock"))
        author_menu.add_command(label="Add named solar site…", command=lambda: self.add_timing_item("solar_site"))
        author_menu.add_command(label="Add time gate…", command=lambda: self.add_timing_item("time_gate"))
        author_menu.add_command(label="Add time-boundary announcement…", command=lambda: self.add_timing_item("time_event"))
        author_menu.add_command(label="Add mission scan set…", command=lambda: self.add_timing_item("scan_set"))
        menu.add_cascade(label="Authoring", menu=author_menu)
        debug_menu = tk.Menu(menu, tearoff=False)
        debug_menu.add_command(label="Start / restart dry run", accelerator="F6", command=self.start_simulation)
        debug_menu.add_command(label="Reset to graph start", command=self.reset_simulation)
        debug_menu.add_command(label="Simulate crash + recover", command=self.recover_simulation)
        debug_menu.add_command(label="Run selected edge trigger", command=self.run_selected_edge_trigger)
        debug_menu.add_command(label="Force selected edge / skip", command=self.force_selected_edge)
        debug_menu.add_command(label="Load selected edge fields", command=self.load_selected_edge_preset)
        debug_menu.add_separator()
        debug_menu.add_command(label="Stop dry run", command=self.stop_simulation)
        menu.add_cascade(label="Simulator", menu=debug_menu)
        self.root.configure(menu=menu)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=(8, 6))
        toolbar.pack(fill="x")
        for label, command in [
            ("New", self.new), ("Open", self.open), ("Save", self.save),
            ("Validate", self.validate), ("Auto layout", self.auto_layout), ("Connect", self.add_edge),
            ("Set Active (next launch)", self.set_active), ("▶ Dry Run", self.start_simulation),
        ]:
            ttk.Button(toolbar, text=label, command=command, style="Toolbar.TButton").pack(side="left", padx=2)
        ttk.Separator(self.root).pack(fill="x")

        panes = ttk.Panedwindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, padding=8, width=230)
        center = ttk.Frame(panes)
        side = ttk.Frame(panes, width=430)
        self.side_tabs = ttk.Notebook(side)
        self.side_tabs.pack(fill="both", expand=True)
        self.inspector_host = ttk.Frame(self.side_tabs, padding=10)
        self.simulator_host = ttk.Frame(self.side_tabs, padding=10)
        self.side_tabs.add(self.inspector_host, text="Inspector")
        self.side_tabs.add(self.simulator_host, text="Dry-run debugger")
        panes.add(left, weight=0)
        panes.add(center, weight=1)
        panes.add(side, weight=0)

        ttk.Label(left, text="Graph contents", style="Heading.TLabel").pack(anchor="w")
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse", height=18)
        self.tree.pack(fill="both", expand=True, pady=(6, 8))
        self.tree.bind("<<TreeviewSelect>>", self._tree_selected)
        ttk.Label(left, text="Add node", style="Heading.TLabel").pack(anchor="w", pady=(4, 4))
        kinds = ttk.Frame(left)
        kinds.pack(fill="x")
        for index, kind in enumerate(sorted(NODE_KINDS)):
            ttk.Button(kinds, text=kind, command=lambda value=kind: self.add_node(value), style="Kind.TButton").grid(
                row=index // 2, column=index % 2, sticky="ew", padx=2, pady=2
            )
        kinds.columnconfigure(0, weight=1)
        kinds.columnconfigure(1, weight=1)
        ttk.Label(left, text="Environments & zones", style="Heading.TLabel").pack(anchor="w", pady=(12, 4))
        ttk.Button(left, text="+ Surface environment", command=lambda: self.add_environment("surface")).pack(fill="x", pady=2)
        ttk.Button(left, text="+ Event environment", command=lambda: self.add_environment("event")).pack(fill="x", pady=2)
        ttk.Button(left, text="+ Planetary glide sound", command=lambda: self.add_environment("planetary_glide")).pack(fill="x", pady=2)
        ttk.Button(left, text="Connect nodes…", command=self.add_edge).pack(fill="x", pady=(10, 3))
        ttk.Button(left, text="Delete selection", command=self.delete_selection).pack(fill="x")

        ttk.Label(center, text="Story & Dialogue flow", style="Heading.TLabel", padding=(8, 5)).grid(row=0, column=0, sticky="ew")
        self.canvas = tk.Canvas(center, bg="#111820", highlightthickness=0, scrollregion=(0, 0, 3000, 2200))
        xbar = ttk.Scrollbar(center, orient="horizontal", command=self.canvas.xview)
        ybar = ttk.Scrollbar(center, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        ybar.grid(row=1, column=1, sticky="ns")
        xbar.grid(row=2, column=0, sticky="ew")
        center.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)
        self.canvas.bind("<Button-1>", self._canvas_press)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self.canvas.bind("<Double-Button-1>", self._canvas_double_click)

        self._build_simulator_ui()

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status, anchor="w", padding=(8, 4)).pack(fill="x")

    def _build_simulator_ui(self) -> None:
        host = self.simulator_host
        ttk.Label(host, text="Isolated story dry run", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            host,
            text="Uses the real graph runtime. Elite, live runtime files and presentation outputs stay disconnected.",
            foreground="#666666", wraplength=390,
        ).pack(anchor="w", pady=(2, 8))

        buttons = ttk.Frame(host)
        buttons.pack(fill="x")
        for column, (label, command) in enumerate((
            ("▶ Start", self.start_simulation), ("Reset", self.reset_simulation),
            ("Crash + recover", self.recover_simulation), ("Stop + editor", self.stop_simulation),
        )):
            ttk.Button(buttons, text=label, command=command).grid(row=0, column=column, sticky="ew", padx=2)
            buttons.columnconfigure(column, weight=1)
        ttk.Button(
            host, text="← Back to editor (keep dry run running)", command=self.return_to_editor,
        ).pack(fill="x", pady=(5, 0))

        self.sim_status = tk.StringVar(value="Stopped")
        ttk.Label(host, textvariable=self.sim_status, foreground="#236b45", wraplength=390).pack(anchor="w", pady=(8, 6))

        edge_box = ttk.LabelFrame(host, text="Selected edge", padding=7)
        edge_box.pack(fill="x", pady=(2, 6))
        ttk.Button(
            edge_box, text="▶ Run its simulated trigger", command=self.run_selected_edge_trigger,
        ).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(
            edge_box, text="Force / skip", command=self.force_selected_edge,
        ).pack(side="right", fill="x", expand=True, padx=(3, 0))

        signal_box = ttk.LabelFrame(host, text="Inject signal", padding=7)
        signal_box.pack(fill="x", pady=(2, 6))
        self.sim_signal = tk.StringVar(value="control.answer")
        signal_row = ttk.Frame(signal_box)
        signal_row.pack(fill="x")
        ttk.Entry(signal_row, textvariable=self.sim_signal).pack(side="left", fill="x", expand=True)
        ttk.Button(signal_row, text="Browse…", command=self.browse_simulation_signal).pack(side="right", padx=(5, 0))
        self.sim_signal_data = tk.Text(signal_box, height=3, wrap="none", font=("Consolas", 9))
        self.sim_signal_data.insert("1.0", "{}")
        self.sim_signal_data.pack(fill="x", pady=(4, 4))
        inject_row = ttk.Frame(signal_box)
        inject_row.pack(fill="x", pady=(4, 0))
        ttk.Button(inject_row, text="Inject", command=self.inject_simulation_signal).pack(side="left", fill="x", expand=True)
        ttk.Button(inject_row, text="Load selected edge fields", command=self.load_selected_edge_preset).pack(side="right", padx=(5, 0))
        self.sim_controls_frame = ttk.Frame(signal_box)
        self.sim_controls_frame.pack(fill="x", pady=(5, 0))
        self._refresh_sim_control_buttons()

        time_box = ttk.Frame(host)
        time_box.pack(fill="x", pady=(0, 6))
        ttk.Label(time_box, text="Advance time").pack(side="left")
        self.sim_seconds = tk.StringVar(value="3")
        ttk.Entry(time_box, textvariable=self.sim_seconds, width=7).pack(side="left", padx=5)
        ttk.Label(time_box, text="seconds").pack(side="left")
        ttk.Button(time_box, text="Advance + tick", command=self.advance_simulation_time).pack(side="right")
        ttk.Label(host, text="Advances elapsed-time edges plus environment start, stop and exit-confirmation timers.", foreground="#666666", wraplength=390).pack(anchor="w", pady=(0, 5))

        solar_box = ttk.Frame(host)
        solar_box.pack(fill="x", pady=(0, 6))
        ttk.Label(solar_box, text="Set celestial site").pack(side="left")
        site_ids = [str(item.get("id")) for item in self.document.solar_sites if item.get("id")]
        self.sim_solar_site = tk.StringVar(value=site_ids[0] if site_ids else "")
        self.sim_solar_hour = tk.StringVar(value="0")
        self.sim_solar_combo = ttk.Combobox(solar_box, textvariable=self.sim_solar_site, values=site_ids, state="normal", width=16)
        self.sim_solar_combo.pack(side="left", padx=4)
        ttk.Entry(solar_box, textvariable=self.sim_solar_hour, width=5).pack(side="left")
        ttk.Button(solar_box, text="Set local hour", command=self.set_simulation_solar_hour).pack(side="right")
        ttk.Label(host, text="Moves only celestial time to the next occurrence of that 0–24 local hour; story wait timers are unchanged.", foreground="#666666", wraplength=390).pack(anchor="w", pady=(0, 5))

        world_box = ttk.LabelFrame(host, text="World snapshot (JSON)", padding=7)
        world_box.pack(fill="x", pady=(0, 6))
        self.sim_world = tk.Text(world_box, height=4, wrap="none", font=("Consolas", 9))
        self.sim_world.insert("1.0", "{}")
        self.sim_world.pack(fill="x")
        ttk.Button(world_box, text="Apply world + evaluate", command=self.apply_simulation_world).pack(fill="x", pady=(4, 0))

        detail_tabs = ttk.Notebook(host)
        detail_tabs.pack(fill="both", expand=True)
        self.sim_state_text = self._debug_text_tab(detail_tabs, "State")
        self.sim_environment_text = self._debug_text_tab(detail_tabs, "Environments")
        self.sim_events_text = self._debug_text_tab(detail_tabs, "Events")
        self.sim_outputs_text = self._debug_text_tab(detail_tabs, "Outputs")

    @staticmethod
    def _debug_text_tab(tabs: ttk.Notebook, label: str) -> tk.Text:
        frame = ttk.Frame(tabs)
        text = tk.Text(frame, wrap="none", font=("Consolas", 8), state="disabled")
        ybar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        text.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tabs.add(frame, text=label)
        return text

    def _control_bindings(self) -> dict[str, Any]:
        return configured_control_bindings()

    def _refresh_sim_control_buttons(self) -> None:
        if not hasattr(self, "sim_controls_frame"):
            return
        for child in self.sim_controls_frame.winfo_children():
            child.destroy()
        for index, (name, binding) in enumerate(self._control_bindings().items()):
            ttk.Button(
                self.sim_controls_frame, text=f"{name}  ({binding})",
                command=lambda value=name: self.inject_named_signal(f"control.{value}"),
            ).grid(row=index // 3, column=index % 3, sticky="ew", padx=2, pady=2)
        for column in range(3):
            self.sim_controls_frame.columnconfigure(column, weight=1)

    def browse_simulation_signal(self) -> None:
        dialog = SignalBrowserDialog(self.root, self._control_bindings(), self.sim_signal.get() if hasattr(self, "sim_signal") else "")
        self.root.wait_window(dialog)
        if not dialog.result:
            return
        self.sim_signal.set(dialog.result.name)
        _write = json.dumps(dialog.result.data, indent=2, ensure_ascii=False)
        self.sim_signal_data.delete("1.0", "end")
        self.sim_signal_data.insert("1.0", _write)
        self.side_tabs.select(self.simulator_host)
        self.sim_status.set(f"Loaded {dialog.result.name}: {dialog.result.description}")

    def edit_control_bindings(self) -> None:
        config = load_config()
        dialog = ControlBindingsDialog(self.root, configured_control_bindings(config))
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        config.setdefault("controls", {})["bindings"] = dialog.result
        save_config(config)
        self._refresh_sim_control_buttons()
        self.status.set(f"Saved {len(dialog.result)} generic control binding(s). Restart the live platform to apply them.")

    def load_selected_edge_preset(self) -> None:
        if self.selection[0] != "edge" or not self.selection[1]:
            messagebox.showinfo("Trigger preset", "Select an edge first.", parent=self.root)
            return
        if not self._apply_current():
            return
        edge = self.document.edge(str(self.selection[1]))
        preset = simulator_preset_for(edge.get("when", {"always": True}))
        self.sim_signal.set(str(preset["signal_name"]))
        self.sim_signal_data.delete("1.0", "end")
        self.sim_signal_data.insert("1.0", _pretty(preset["signal_data"]))
        self.sim_world.delete("1.0", "end")
        self.sim_world.insert("1.0", _pretty(preset["world"]))
        if float(preset["advance_seconds"]) > 0:
            self.sim_seconds.set(f"{float(preset['advance_seconds']):g}")
        notes = "; ".join(preset["notes"])
        self.sim_status.set(
            f"Loaded simulator preset for {edge.get('id')}: {predicate_summary(edge.get('when', {}))}"
            + (f"\n{notes}" if notes else "")
        )
        self.side_tabs.select(self.simulator_host)

    def _running_selected_edge(self) -> dict[str, Any] | None:
        if self.simulator is None:
            messagebox.showinfo("Dry run", "Start the dry run first.", parent=self.root)
            return None
        if self.selection[0] != "edge" or not self.selection[1]:
            messagebox.showinfo("Selected edge", "Select the edge you want to test or skip.", parent=self.root)
            return None
        edge_id = str(self.selection[1])
        edge = next((item for item in self.simulator.graph.edges if item.get("id") == edge_id), None)
        if edge is None:
            messagebox.showinfo(
                "Restart dry run", "That edge is not in the running graph snapshot. Restart the dry run to include your edits.",
                parent=self.root,
            )
            return None
        source = str(edge.get("from", ""))
        if source not in self.simulator.active_nodes:
            active = ", ".join(self.simulator.active_nodes) or "none"
            messagebox.showinfo(
                "Edge is not ready",
                f"The selected edge starts at {source!r}, while the active node is {active}.\n\n"
                "Select an outgoing edge from the highlighted active node.",
                parent=self.root,
            )
            return None
        return edge

    def run_selected_edge_trigger(self) -> None:
        edge = self._running_selected_edge()
        if edge is None or self.simulator is None:
            return
        try:
            preset = simulator_preset_for(edge.get("when", {"always": True}))
            self.sim_signal.set(str(preset["signal_name"]))
            self.sim_signal_data.delete("1.0", "end")
            self.sim_signal_data.insert("1.0", _pretty(preset["signal_data"]))
            self.sim_world.delete("1.0", "end")
            self.sim_world.insert("1.0", _pretty(preset["world"]))
            if float(preset["advance_seconds"]) > 0:
                self.sim_seconds.set(f"{float(preset['advance_seconds']):g}")
            before_traversals = len([row for row in self.simulator.events() if row.get("type") == "edge.traverse"])
            moved = self.simulator.run_trigger(
                str(preset["signal_name"]), preset["signal_data"], preset["world"],
                float(preset["advance_seconds"]),
            )
            notes = "; ".join(preset["notes"])
            traversals = [row for row in self.simulator.events() if row.get("type") == "edge.traverse"]
            actual = traversals[before_traversals].get("edge") if moved and len(traversals) > before_traversals else None
            if moved and actual == edge.get("id"):
                message = f"Ran trigger for {edge.get('id')} — transitioned."
            elif moved:
                message = f"The trigger traversed {actual} instead of {edge.get('id')} because runtime priority won."
            else:
                message = f"Ran trigger for {edge.get('id')} — condition was not satisfied."
            if notes:
                message += f" {notes}"
            self._refresh_simulator(message)
        except (DryRunError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Trigger simulation failed", str(exc), parent=self.root)

    def force_selected_edge(self) -> None:
        edge = self._running_selected_edge()
        if edge is None or self.simulator is None:
            return
        try:
            self.simulator.force_edge(str(edge.get("id")))
            self._refresh_simulator(f"Forced {edge.get('id')} — predicate bypassed in dry run only.")
        except (DryRunError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Cannot force edge", str(exc), parent=self.root)

    @staticmethod
    def _replace_debug_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _close_simulator_session(self) -> None:
        if self.simulator is not None:
            self.simulator.close()
        self.simulator = None
        self.simulator_graph = None
        self.simulator_last_edge = None

    def start_simulation(self) -> None:
        if not self._apply_current():
            return
        errors = self.document.validate()
        if errors:
            messagebox.showerror(
                "Dry run cannot start", "Fix these graph issues first:\n\n• " + "\n• ".join(errors), parent=self.root
            )
            return
        try:
            self._close_simulator_session()
            self.simulator_graph = deepcopy(self.document.data)
            self.simulator = DryRunSession(self.simulator_graph)
            self.simulator.start()
            self.side_tabs.select(self.simulator_host)
            self._refresh_simulator("Dry run started from the graph's start node.")
        except (DryRunError, RuntimeError, ValueError) as exc:
            self._close_simulator_session()
            messagebox.showerror("Dry run failed", str(exc), parent=self.root)

    def stop_simulation(self) -> None:
        self._close_simulator_session()
        self.sim_status.set("Stopped — no live files were changed.")
        for widget in (self.sim_state_text, self.sim_environment_text, self.sim_events_text, self.sim_outputs_text):
            self._replace_debug_text(widget, "")
        self._redraw_canvas()
        self.return_to_editor()

    def return_to_editor(self) -> None:
        self.side_tabs.select(self.inspector_host)
        self._show_inspector()

    def reset_simulation(self) -> None:
        if self.simulator is None:
            self.start_simulation()
            return
        try:
            self.simulator.reset()
            self._refresh_simulator("Reset to graph start.")
        except (DryRunError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Reset failed", str(exc), parent=self.root)

    def inject_named_signal(self, name: str) -> None:
        self.sim_signal.set(name)
        self.inject_simulation_signal()

    def inject_simulation_signal(self) -> None:
        if self.simulator is None:
            messagebox.showinfo("Dry run", "Start the dry run first.", parent=self.root)
            return
        try:
            data = _json(self.sim_signal_data.get("1.0", "end-1c"), dict, "Signal data")
            name = self.sim_signal.get().strip()
            moved = self.simulator.dispatch(name, data)
            self._refresh_simulator(f"Injected {name} — {'runtime reacted' if moved else 'no story or environment change'}.")
        except (EditorError, DryRunError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Signal rejected", str(exc), parent=self.root)

    def apply_simulation_world(self) -> None:
        if self.simulator is None:
            messagebox.showinfo("Dry run", "Start the dry run first.", parent=self.root)
            return
        try:
            world = _json(self.sim_world.get("1.0", "end-1c"), dict, "World snapshot")
            moved = self.simulator.replace_world(world)
            self._refresh_simulator(f"World updated — {'runtime reacted' if moved else 'no story or environment change'}.")
        except (EditorError, DryRunError, RuntimeError, ValueError) as exc:
            messagebox.showerror("World snapshot rejected", str(exc), parent=self.root)

    def advance_simulation_time(self) -> None:
        if self.simulator is None:
            messagebox.showinfo("Dry run", "Start the dry run first.", parent=self.root)
            return
        try:
            seconds = float(self.sim_seconds.get())
            moved = self.simulator.advance_time(seconds)
            self._refresh_simulator(
                f"Advanced {seconds:g} seconds — {'runtime reacted' if moved else 'no story or environment timer ready'}."
            )
        except (DryRunError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Time advance rejected", str(exc), parent=self.root)

    def set_simulation_solar_hour(self) -> None:
        if self.simulator is None:
            messagebox.showinfo("Dry run", "Start the dry run first.", parent=self.root)
            return
        try:
            site = self.sim_solar_site.get().strip()
            hour = float(self.sim_solar_hour.get())
            moved = self.simulator.set_site_local_hour(site, hour)
            fact = self.simulator.world.get("time", {}).get("sites", {}).get(site, {})
            self._refresh_simulator(
                f"Celestial clock set: {site} {fact.get('local_time', '?')} ({fact.get('phase', '?')}) — "
                + ("runtime reacted." if moved else "no story/environment change.")
            )
        except (DryRunError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Celestial time rejected", str(exc), parent=self.root)

    def recover_simulation(self) -> None:
        if self.simulator is None:
            messagebox.showinfo("Dry run", "Start the dry run first.", parent=self.root)
            return
        try:
            report = self.simulator.crash_and_recover()
            checkpoint = report.get("checkpoint") or "none"
            before = ", ".join(report.get("before_active_nodes", [])) or "none"
            after = ", ".join(report.get("after_active_nodes", [])) or "none"
            self._refresh_simulator(f"Crash recovery: {before} → {after} (checkpoint {checkpoint}).")
        except (DryRunError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Recovery test failed", str(exc), parent=self.root)

    def _refresh_simulator(self, message: str = "") -> None:
        if self.simulator is None:
            return
        state = self.simulator.state
        events = self.simulator.events()
        traversals = [row for row in events if row.get("type") == "edge.traverse"]
        self.simulator_last_edge = str(traversals[-1].get("edge")) if traversals else None
        active = ", ".join(self.simulator.active_nodes) or "none"
        checkpoint = (state.get("checkpoint") or {}).get("id") or "none"
        changed = self.simulator_graph != self.document.data
        suffix = " Graph has changed; restart to test the edits." if changed else ""
        self.sim_status.set(f"{message}\nActive: {active} · checkpoint: {checkpoint}.{suffix}".strip())

        state_view = {
            "active_nodes": state.get("active_nodes", []),
            "checkpoint": checkpoint,
            "next_step": state.get("next_step", ""),
            "next_choices": state.get("next_choices", []),
            "variables": state.get("variables", {}),
            "visits": state.get("visits", {}),
            "completed_nodes": state.get("completed_nodes", []),
            "join_arrivals": state.get("join_arrivals", {}),
            "world": self.simulator.world,
            "waiting_edges": self.simulator.outgoing_edges(),
            "navigation": self.simulator.navigation_state,
            "celestial_time": self.simulator.world.get("time", {}),
            "time_delivery": self.simulator.time_state,
            "scans": self.simulator.scan_state,
        }
        self._replace_debug_text(self.sim_state_text, _pretty(state_view))
        environment_view = self.simulator.environment_state
        environment_view["active_zones"] = self.simulator.environments.active_zones
        self._replace_debug_text(self.sim_environment_text, _pretty(environment_view))
        event_lines = [
            f"{row.get('type', '?')}: "
            + " ".join(
                f"{key}={row[key]}" for key in ("node", "edge", "source", "targets", "signal", "checkpoint") if key in row
            )
            for row in events[-250:]
        ]
        environment_lines = [
            f"{row.get('type', '?')}: "
            + " ".join(f"{key}={row[key]}" for key in ("zone", "layer", "reason", "level") if key in row)
            for row in self.simulator.environment_events()[-150:]
        ]
        if environment_lines:
            event_lines.extend(["", "--- ENVIRONMENTS ---", *environment_lines])
        self._replace_debug_text(self.sim_events_text, "\n".join(event_lines))
        output_lines = [
            f"{row.get('channel', '?')} @ {row.get('node', '?')}\n{_pretty(row.get('payload', {}))}"
            for row in self.simulator.outputs()[-100:]
        ]
        self._replace_debug_text(self.sim_outputs_text, "\n\n".join(output_lines))
        self._redraw_canvas()

    def _bind_keys(self) -> None:
        self.root.bind("<Control-n>", lambda _event: self.new())
        self.root.bind("<Control-o>", lambda _event: self.open())
        self.root.bind("<Control-s>", lambda _event: self.save())
        self.root.bind("<Control-Shift-S>", lambda _event: self.save_as())
        self.root.bind("<Control-l>", lambda _event: self.auto_layout())
        self.root.bind("<Control-e>", lambda _event: self.add_edge())
        self.root.bind("<F5>", lambda _event: self.validate())
        self.root.bind("<F6>", lambda _event: self.start_simulation())
        self.root.bind("<Delete>", lambda _event: self.delete_selection())

    def _title(self) -> None:
        name = self.document.path.name if self.document.path else "Untitled graph"
        mark = " *" if self.document.dirty else ""
        self.root.title(f"Reactive Cockpit Story Graph Editor — {name}{mark}")

    def refresh(self) -> None:
        kind, selected_id = self.selection
        if kind == "node" and selected_id not in self.document.nodes:
            self.selection = ("graph", None)
        if kind == "edge" and not any(edge.get("id") == selected_id for edge in self.document.edges):
            self.selection = ("graph", None)
        if kind == "environment" and not any(zone.get("id") == selected_id for zone in self.document.environments):
            self.selection = ("graph", None)
        if kind == "navigator" and not any(item.get("id") == selected_id for item in self.document.navigators):
            self.selection = ("graph", None)
        collection_names = {"solar_clock": "solar_clocks", "solar_site": "solar_sites", "time_gate": "time_gates", "time_event": "time_events", "scan_set": "scan_sets"}
        if kind in collection_names and not any(item.get("id") == selected_id for item in self.document.collection(collection_names[kind])):
            self.selection = ("graph", None)
        self._title()
        if hasattr(self, "sim_solar_combo"):
            site_ids = [str(item.get("id")) for item in self.document.solar_sites if item.get("id")]
            self.sim_solar_combo.configure(values=site_ids)
            if self.sim_solar_site.get() not in site_ids:
                self.sim_solar_site.set(site_ids[0] if site_ids else "")
        self._refresh_tree()
        self._redraw_canvas()
        self._show_inspector()
        errors = self.document.validate()
        self.status.set(
            f"{len(self.document.nodes)} nodes · {len(self.document.edges)} edges · {len(self.document.environments)} environments · {len(self.document.navigators)} navigators · "
            + ("graph valid" if not errors else f"{len(errors)} validation issue(s)")
        )

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        graph_item = self.tree.insert("", "end", iid="graph", text=f"📘 {self.document.data.get('title', 'Graph')}", open=True)
        nodes_item = self.tree.insert(graph_item, "end", iid="nodes", text=f"Nodes ({len(self.document.nodes)})", open=True)
        for node_id, node in self.document.nodes.items():
            self.tree.insert(nodes_item, "end", iid=f"node:{node_id}", text=f"{node_id}  [{node.get('kind', '?')}]")
        edges_item = self.tree.insert(graph_item, "end", iid="edges", text=f"Edges ({len(self.document.edges)})", open=True)
        for edge in self.document.edges:
            target = edge.get("to")
            target_text = ", ".join(target) if isinstance(target, list) else str(target)
            self.tree.insert(edges_item, "end", iid=f"edge:{edge.get('id')}", text=f"{edge.get('id')}  → {target_text}")
        environments_item = self.tree.insert(
            graph_item, "end", iid="environments", text=f"Environments & zones ({len(self.document.environments)})", open=True,
        )
        for zone in self.document.environments:
            marker = "🌍" if zone.get("location") else "🎛"
            self.tree.insert(
                environments_item, "end", iid=f"environment:{zone.get('id')}",
                text=f"{marker} {zone.get('title', zone.get('id'))}",
            )
        navigators_item = self.tree.insert(
            graph_item, "end", iid="navigators", text=f"Coordinate navigators ({len(self.document.navigators)})", open=True,
        )
        for navigator in self.document.navigators:
            self.tree.insert(
                navigators_item, "end", iid=f"navigator:{navigator.get('id')}",
                text=f"🧭 {navigator.get('title', navigator.get('id'))}",
            )
        for kind, collection, label, marker in (
            ("solar_clock", "solar_clocks", "Body solar clocks", "☀"),
            ("solar_site", "solar_sites", "Named solar sites", "◉"),
            ("time_gate", "time_gates", "Time gates", "◷"),
            ("time_event", "time_events", "Time boundary announcements", "🔔"),
            ("scan_set", "scan_sets", "Mission scan sets", "⌁"),
        ):
            values = self.document.collection(collection)
            parent = self.tree.insert(graph_item, "end", iid=collection, text=f"{label} ({len(values)})", open=bool(values))
            for item in values:
                self.tree.insert(parent, "end", iid=f"{kind}:{item.get('id')}", text=f"{marker} {item.get('title', item.get('id'))}")
        desired = "graph" if self.selection[0] == "graph" else f"{self.selection[0]}:{self.selection[1]}"
        if self.tree.exists(desired):
            self.tree.selection_set(desired)
            self.tree.see(desired)

    def _grid(self, width: int, height: int) -> None:
        for x in range(0, width + 1, 50):
            color = "#26313b" if x % 250 == 0 else "#1b252e"
            self.canvas.create_line(x, 0, x, height, fill=color, tags=("grid",))
        for y in range(0, height + 1, 50):
            color = "#26313b" if y % 250 == 0 else "#1b252e"
            self.canvas.create_line(0, y, width, y, fill=color, tags=("grid",))

    def _redraw_canvas(self) -> None:
        self.canvas.delete("all")
        positions = {node_id: self.document.position(node_id) for node_id in self.document.nodes}
        width = max(3000, int(max((position[0] for position in positions.values()), default=0) + 300))
        height = max(2200, int(max((position[1] for position in positions.values()), default=0) + 200))
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self._grid(width, height)
        for edge in self.document.edges:
            source = edge.get("from")
            if source not in positions:
                continue
            targets = edge.get("to") if isinstance(edge.get("to"), list) else [edge.get("to")]
            for target in targets:
                if target not in positions:
                    continue
                self._draw_edge(str(edge.get("id", "")), positions[source], positions[target], edge.get("when", {}))
        for node_id, node in self.document.nodes.items():
            self._draw_node(node_id, node, positions[node_id])

    def _draw_edge(self, edge_id: str, source: tuple[float, float], target: tuple[float, float], when: Any) -> None:
        x1, y1 = source
        x2, y2 = target
        if source == target:
            points = [x1 + NODE_WIDTH / 2, y1, x1 + 175, y1 - 75, x1, y1 - NODE_HEIGHT / 2]
            label_x, label_y = x1 + 165, y1 - 68
        else:
            direction = 1 if x2 >= x1 else -1
            start_x = x1 + direction * NODE_WIDTH / 2
            end_x = x2 - direction * NODE_WIDTH / 2
            mid_x = (start_x + end_x) / 2
            points = [start_x, y1, mid_x, y1, mid_x, y2, end_x, y2]
            label_x, label_y = mid_x, (y1 + y2) / 2 - 12
        selected = self.selection == ("edge", edge_id)
        traversed = edge_id == self.simulator_last_edge
        color = "#5cf2a5" if traversed else ("#ffcc66" if selected else "#89a6bb")
        self.canvas.create_line(*points, fill=color, width=4 if traversed else (3 if selected else 2), arrow="last", smooth=True, tags=("edge", f"edge:{edge_id}"))
        summary = self._predicate_summary(when)
        text = self.canvas.create_text(label_x, label_y, text=f"{edge_id}\n{summary}", fill="#d5e2ea", font=("Segoe UI", 8), justify="center", tags=("edge", f"edge:{edge_id}"))
        box = self.canvas.bbox(text)
        if box:
            background = self.canvas.create_rectangle(box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2, fill="#17212a", outline="#334654", tags=("edge", f"edge:{edge_id}"))
            self.canvas.tag_lower(background, text)

    @staticmethod
    def _predicate_summary(when: Any) -> str:
        summary = predicate_summary(when)
        return summary if len(summary) <= 70 else summary[:67] + "…"

    def _draw_node(self, node_id: str, node: dict[str, Any], position: tuple[float, float]) -> None:
        x, y = position
        selected = self.selection == ("node", node_id)
        active = bool(self.simulator and node_id in self.simulator.active_nodes)
        outline = "#5cf2a5" if active else ("#ffffff" if selected else "#9fb4c2")
        width = 5 if active else (4 if selected else 2)
        tags = ("node", f"node:{node_id}")
        self.canvas.create_rectangle(
            x - NODE_WIDTH / 2, y - NODE_HEIGHT / 2, x + NODE_WIDTH / 2, y + NODE_HEIGHT / 2,
            fill=NODE_COLORS.get(str(node.get("kind")), "#555555"), outline=outline, width=width, tags=tags,
        )
        marker = "▶ " if self.document.data.get("start") == node_id else ""
        self.canvas.create_text(x, y - 16, text=marker + node_id, fill="white", font=("Segoe UI", 11, "bold"), width=NODE_WIDTH - 18, tags=tags)
        detail = str(node.get("kind", "unknown"))
        if node.get("checkpoint_id"):
            detail += f" · {node['checkpoint_id']}"
        self.canvas.create_text(x, y + 4, text=detail, fill="#edf4f8", font=("Segoe UI", 9), width=NODE_WIDTH - 18, tags=tags)
        effects = node.get("on_enter") if isinstance(node.get("on_enter"), list) else []
        if effects:
            summary = effect_summary(effects[0])
            if len(summary) > 42:
                summary = summary[:39] + "…"
            self.canvas.create_text(x, y + 25, text=summary, fill="#d7e4ec", font=("Segoe UI", 8), width=NODE_WIDTH - 18, tags=tags)
        if active:
            self.canvas.create_text(
                x + NODE_WIDTH / 2 - 8, y - NODE_HEIGHT / 2 + 8, text="● ACTIVE", anchor="ne",
                fill="#b8ffd7", font=("Segoe UI", 8, "bold"), tags=tags,
            )

    def _canvas_item(self, event: tk.Event) -> tuple[str, str | None]:
        item = self.canvas.find_withtag("current")
        if not item:
            return "graph", None
        for tag in self.canvas.gettags(item[0]):
            if tag.startswith("node:"):
                return "node", tag.split(":", 1)[1]
            if tag.startswith("edge:"):
                return "edge", tag.split(":", 1)[1]
        return "graph", None

    def _canvas_press(self, event: tk.Event) -> None:
        selection = self._canvas_item(event)
        if not self._change_selection(selection):
            return
        if selection[0] == "node" and selection[1]:
            self.drag_node = selection[1]
            x, y = self.document.position(selection[1])
            self.drag_offset = (self.canvas.canvasx(event.x) - x, self.canvas.canvasy(event.y) - y)
        else:
            self.drag_node = None

    def _canvas_drag(self, event: tk.Event) -> None:
        if not self.drag_node:
            return
        x = self.canvas.canvasx(event.x) - self.drag_offset[0]
        y = self.canvas.canvasy(event.y) - self.drag_offset[1]
        self.document.set_position(self.drag_node, max(120, x), max(70, y))
        self._title()
        self._redraw_canvas()

    def _canvas_release(self, _event: tk.Event) -> None:
        self.drag_node = None

    def _canvas_double_click(self, event: tk.Event) -> None:
        selection = self._canvas_item(event)
        if selection[0] == "node" and selection[1]:
            self._change_selection(selection)

    def _tree_selected(self, _event: tk.Event) -> None:
        if self.changing_selection:
            return
        selected = self.tree.selection()
        if not selected:
            return
        value = selected[0]
        if value.startswith("node:"):
            selection = ("node", value.split(":", 1)[1])
        elif value.startswith("edge:"):
            selection = ("edge", value.split(":", 1)[1])
        elif value.startswith("environment:"):
            selection = ("environment", value.split(":", 1)[1])
        elif value.startswith("navigator:"):
            selection = ("navigator", value.split(":", 1)[1])
        elif any(value.startswith(prefix + ":") for prefix in ("solar_clock", "solar_site", "time_gate", "time_event", "scan_set")):
            prefix, item_id = value.split(":", 1)
            selection = (prefix, item_id)
        elif value == "graph":
            selection = ("graph", None)
        else:
            return
        self._change_selection(selection)

    def _change_selection(self, selection: tuple[str, str | None]) -> bool:
        if selection == self.selection:
            return True
        self.changing_selection = True
        try:
            if self.inspector is not None and not self._apply_current():
                return False
            self.selection = selection
            self.refresh()
            return True
        finally:
            self.changing_selection = False

    def _clear_inspector(self, title: str) -> ttk.Frame:
        for child in self.inspector_host.winfo_children():
            child.destroy()
        frame = ttk.Frame(self.inspector_host)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title, style="Heading.TLabel").pack(anchor="w", pady=(0, 10))
        self.inspector = frame
        self.inspector_vars = {}
        return frame

    @staticmethod
    def _field(parent: ttk.Frame, label: str, variable: tk.StringVar, values: list[str] | None = None) -> None:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(7, 2))
        if values is None:
            ttk.Entry(parent, textvariable=variable).pack(fill="x")
        else:
            ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").pack(fill="x")

    @staticmethod
    def _text_field(parent: ttk.Frame, label: str, value: str, height: int = 8) -> tk.Text:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(7, 2))
        text = tk.Text(parent, height=height, wrap="none", undo=True, font=("Consolas", 9))
        text.insert("1.0", value)
        text.pack(fill="both", expand=True)
        return text

    def _show_inspector(self) -> None:
        kind, selected_id = self.selection
        if kind == "node" and selected_id:
            self._node_inspector(selected_id)
        elif kind == "edge" and selected_id:
            self._edge_inspector(selected_id)
        elif kind == "environment" and selected_id:
            self._environment_inspector(selected_id)
        elif kind == "navigator" and selected_id:
            self._navigator_inspector(selected_id)
        elif kind in {"solar_clock", "solar_site", "time_gate", "time_event", "scan_set"} and selected_id:
            self._collection_inspector(kind, selected_id)
        else:
            self._graph_inspector()

    def _graph_inspector(self) -> None:
        frame = self._clear_inspector("Graph properties")
        for name, value in [
            ("id", self.document.data.get("id", "")),
            ("version", self.document.data.get("version", "")),
            ("title", self.document.data.get("title", "")),
            ("start", self.document.data.get("start", "")),
        ]:
            variable = tk.StringVar(value=str(value))
            self.inspector_vars[name] = variable
            self._field(frame, name.replace("_", " ").title(), variable, list(self.document.nodes) if name == "start" else None)
        description = self._text_field(frame, "Description", str(self.document.data.get("description", "")), 4)
        variables = self._text_field(frame, "Initial variables (JSON object)", _pretty(self.document.data.get("variables", {})), 10)
        recovery = self._text_field(frame, "Global recovery condition (JSON or null)", _pretty(self.document.data.get("recovery_when")), 4)
        self.inspector_vars.update(description=description, variables=variables, recovery=recovery)
        ttk.Button(frame, text="Apply graph properties", command=self._apply_graph).pack(fill="x", pady=(12, 0))

    def _node_inspector(self, node_id: str) -> None:
        node = self.document.nodes[node_id]
        frame = self._clear_inspector(f"Node · {node_id}")
        values = {
            "id": tk.StringVar(value=node_id),
            "kind": tk.StringVar(value=str(node.get("kind", "beat"))),
            "checkpoint_id": tk.StringVar(value=str(node.get("checkpoint_id", ""))),
            "join_from": tk.StringVar(value=", ".join(node.get("join_from", [])) if isinstance(node.get("join_from"), list) else ""),
            "join_count": tk.StringVar(value=str(node.get("join_count", ""))),
        }
        self.inspector_vars.update(values)
        self._field(frame, "Node id", values["id"])
        self._field(frame, "Kind", values["kind"], sorted(NODE_KINDS))
        self._field(frame, "Checkpoint id", values["checkpoint_id"])
        self._field(frame, "Join from (comma separated)", values["join_from"])
        self._field(frame, "Join count", values["join_count"])
        effects_value = node.get("on_enter", [])
        effects = self._text_field(frame, "On-enter effects (advanced JSON)", _pretty(effects_value), 9)
        self.inspector_vars["effects"] = effects
        summaries = [effect_summary(effect) for effect in effects_value] if isinstance(effects_value, list) else []
        ttk.Label(
            frame, text=("Then: " + "  •  ".join(summaries[:3])) if summaries else "No entry effects yet.",
            foreground="#285f45", wraplength=360,
        ).pack(anchor="w", pady=(5, 1))
        templates = ttk.Frame(frame)
        templates.pack(fill="x", pady=(5, 0))
        ttk.Button(templates, text="Edit effects…", command=self._edit_effects).pack(side="left", padx=(0, 4))
        ttk.Button(templates, text="+ Objective", command=lambda: self._append_structured_effect("Objective")).pack(side="left", padx=4)
        ttk.Button(templates, text="+ Audio", command=lambda: self._append_structured_effect("Audio")).pack(side="left", padx=4)
        ttk.Button(frame, text="Apply node changes", command=self._apply_node).pack(fill="x", pady=(12, 0))

    def _effects_from_inspector(self) -> list[dict[str, Any]] | None:
        text: tk.Text = self.inspector_vars["effects"]
        try:
            return _json(text.get("1.0", "end-1c"), list, "On-enter effects")
        except EditorError as exc:
            messagebox.showerror("Cannot add effect", str(exc), parent=self.root)
            return None

    def _set_inspector_effects(self, effects: list[dict[str, Any]]) -> None:
        text: tk.Text = self.inspector_vars["effects"]
        text.delete("1.0", "end")
        text.insert("1.0", _pretty(effects))

    def _edit_effects(self) -> None:
        effects = self._effects_from_inspector()
        if effects is None:
            return
        dialog = EffectsDialog(self.root, effects, self._audio_layer_options())
        self.root.wait_window(dialog)
        if dialog.result is not None:
            self._set_inspector_effects(dialog.result)

    def _append_structured_effect(self, effect_type: str) -> None:
        effects = self._effects_from_inspector()
        if effects is None:
            return
        dialog = EffectDialog(self.root, force_type=effect_type, layer_options=self._audio_layer_options())
        self.root.wait_window(dialog)
        if dialog.result:
            effects.append(dialog.result)
            self._set_inspector_effects(effects)

    def _wind_layer_options(self) -> list[str]:
        return [f"environment.{zone['id']}.{layer['id']}"
                for zone in self.document.environments for layer in zone.get('layers', [])
                if layer.get('type') == 'wind']

    def _audio_layer_options(self) -> list[str]:
        result = ["dialogue", "music"]
        for zone in self.document.environments:
            zone_id = str(zone.get("id", "")).strip()
            for layer in zone.get("layers", []):
                if zone_id and isinstance(layer, dict) and layer.get("type") == "audio" and layer.get("id"):
                    result.append(f"environment.{zone_id}.{layer['id']}")
        return list(dict.fromkeys(result))

    def _edge_inspector(self, edge_id: str) -> None:
        edge = self.document.edge(edge_id)
        frame = self._clear_inspector(f"Edge · {edge_id}")
        target = edge.get("to")
        targets = ", ".join(target) if isinstance(target, list) else str(target)
        values = {
            "id": tk.StringVar(value=edge_id),
            "source": tk.StringVar(value=str(edge.get("from", ""))),
            "targets": tk.StringVar(value=targets),
            "priority": tk.StringVar(value=str(edge.get("priority", 0))),
        }
        self.inspector_vars.update(values)
        self._field(frame, "Edge id", values["id"])
        self._field(frame, "From", values["source"], list(self.document.nodes))
        self._field(frame, "To (comma separated)", values["targets"])
        self._field(frame, "Priority", values["priority"])
        predicate = self._text_field(frame, "Condition (JSON object)", _pretty(edge.get("when", {"always": True})), 14)
        self.inspector_vars["predicate"] = predicate
        self.inspector_vars["predicate_summary"] = tk.StringVar(value=predicate_summary(edge.get("when", {})))
        ttk.Label(frame, textvariable=self.inspector_vars["predicate_summary"], foreground="#285f45", wraplength=360).pack(anchor="w", pady=(5, 0))
        trigger_buttons = ttk.Frame(frame)
        trigger_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(trigger_buttons, text="Build condition…", command=self._build_edge_condition).pack(side="left")
        ttk.Button(trigger_buttons, text="Run trigger", command=self.run_selected_edge_trigger).pack(side="left", padx=5)
        ttk.Button(trigger_buttons, text="Force / skip", command=self.force_selected_edge).pack(side="right")
        ttk.Button(frame, text="Apply edge changes", command=self._apply_edge).pack(fill="x", pady=(12, 0))

    def _environment_inspector(self, environment_id: str) -> None:
        zone = self.document.environment(environment_id)
        frame = self._clear_inspector(f"Environment · {zone.get('title', environment_id)}")
        ttk.Label(frame, text=environment_summary(zone), foreground="#285f45", wraplength=370).pack(anchor="w", pady=(0, 10))
        ttk.Label(
            frame,
            text="This lifecycle runs beside Story & Dialogue. Its named sound and visual layers remain independent until this environment ends.",
            foreground="#666666", wraplength=370,
        ).pack(anchor="w", pady=(0, 10))
        layers = zone.get("layers") if isinstance(zone.get("layers"), list) else []
        for layer in layers:
            response = layer.get("depth_response", {})
            detail = f"{layer.get('id')} · {layer.get('type')} · {response.get('curve', 'constant')} depth"
            ttk.Label(frame, text=f"• {detail}", wraplength=370).pack(anchor="w", pady=2)
        ttk.Button(frame, text="Edit environment…", command=self.edit_environment).pack(fill="x", pady=(14, 4))
        test_row = ttk.Frame(frame); test_row.pack(fill="x", pady=4)
        for label, depth in (("Test edge 0%", 0.0), ("Test halfway", 50.0), ("Test core 100%", 100.0)):
            ttk.Button(
                test_row, text=label,
                command=lambda value=depth: self.test_environment_entry(value),
            ).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(frame, text="Delete environment", command=self.delete_selection).pack(fill="x", pady=(12, 0))

    def _navigator_inspector(self, navigator_id: str) -> None:
        navigator = next(item for item in self.document.navigators if item.get("id") == navigator_id)
        frame = self._clear_inspector(f"Coordinate navigator · {navigator.get('title', navigator_id)}")
        ttk.Label(
            frame, text="Reusable distance pings and hold-to-acquire targets. This advanced JSON remains ordinary validated graph content.",
            foreground="#666666", wraplength=370,
        ).pack(anchor="w", pady=(0, 8))
        value = self._text_field(frame, "Navigator definition", _pretty(navigator), 24)
        self.inspector_vars["navigator"] = value
        ttk.Button(frame, text="Apply navigator", command=self._apply_navigator).pack(fill="x", pady=(10, 0))

    def _collection_inspector(self, kind: str, item_id: str) -> None:
        collections = {"solar_clock": "solar_clocks", "solar_site": "solar_sites", "time_gate": "time_gates", "time_event": "time_events", "scan_set": "scan_sets"}
        labels = {"solar_clock": "Body solar clock", "solar_site": "Named solar site", "time_gate": "Time gate", "time_event": "Boundary announcement", "scan_set": "Mission scan set"}
        collection = collections[kind]
        item = self.document.collection_item(collection, item_id)
        frame = self._clear_inspector(f"{labels[kind]} · {item.get('title', item_id)}")
        descriptions = {
            "solar_clock": "One calibrated solar cycle for an Elite body. Stored durations use Earth seconds.",
            "solar_site": "A named location on that body. Add an anchor only when this site has its own observed calibration.",
            "time_gate": "An author-facing local-time window. It can cross local midnight.",
            "time_event": "A durable open/close-boundary effect with restart-safe repeat and catch-up behavior.",
            "scan_set": "Order-independent, once-only objectives linked to persistent detection environments.",
        }
        ttk.Label(frame, text=descriptions[kind], foreground="#666666", wraplength=370).pack(anchor="w", pady=(0, 8))
        value = self._text_field(frame, "Validated definition", _pretty(item), 26)
        self.inspector_vars.update(collection_kind=kind, collection_name=collection, collection_value=value)
        ttk.Button(frame, text=f"Apply {labels[kind].lower()}", command=self._apply_collection_item).pack(fill="x", pady=(10, 0))

    def _build_edge_condition(self) -> None:
        try:
            current = _json(self.inspector_vars["predicate"].get("1.0", "end-1c"), dict, "Condition")
        except EditorError as exc:
            messagebox.showerror("Cannot build condition", str(exc), parent=self.root)
            return
        dialog = PredicateBuilderDialog(self.root, current, self._control_bindings(), self._time_gate_ids())
        self.root.wait_window(dialog)
        if dialog.result:
            text: tk.Text = self.inspector_vars["predicate"]
            text.delete("1.0", "end")
            text.insert("1.0", _pretty(dialog.result))
            self.inspector_vars["predicate_summary"].set(predicate_summary(dialog.result))

    def _apply_graph(self) -> bool:
        try:
            variables = _json(self.inspector_vars["variables"].get("1.0", "end-1c"), dict, "Variables")
            self.document.set_metadata(
                self.inspector_vars["id"].get(), self.inspector_vars["version"].get(),
                self.inspector_vars["title"].get(), self.inspector_vars["description"].get("1.0", "end-1c"),
                self.inspector_vars["start"].get(),
            )
            self.document.set_variables(variables)
            recovery = _json(self.inspector_vars["recovery"].get("1.0", "end-1c"), (dict, type(None)), "Recovery condition")
            if recovery is None:
                self.document.data.pop("recovery_when", None)
            else:
                self.document.data["recovery_when"] = recovery
            self.refresh()
            return True
        except EditorError as exc:
            messagebox.showerror("Cannot apply graph properties", str(exc), parent=self.root)
            return False

    def _apply_node(self) -> bool:
        node_id = self.selection[1]
        if not node_id:
            return False
        try:
            effects = _json(self.inspector_vars["effects"].get("1.0", "end-1c"), list, "On-enter effects")
            join_from = [value.strip() for value in self.inspector_vars["join_from"].get().split(",") if value.strip()]
            raw_count = self.inspector_vars["join_count"].get().strip()
            count = int(raw_count) if raw_count else None
            new_id = self.document.update_node(
                node_id,
                new_id=self.inspector_vars["id"].get(),
                kind=self.inspector_vars["kind"].get(),
                checkpoint_id=self.inspector_vars["checkpoint_id"].get(),
                join_from=join_from,
                join_count=count,
                on_enter=effects,
            )
            self.selection = ("node", new_id)
            self.refresh()
            return True
        except (EditorError, ValueError) as exc:
            messagebox.showerror("Cannot apply node", str(exc), parent=self.root)
            return False

    def _apply_edge(self) -> bool:
        edge_id = self.selection[1]
        if not edge_id:
            return False
        try:
            predicate = _json(self.inspector_vars["predicate"].get("1.0", "end-1c"), dict, "Condition")
            targets = [value.strip() for value in self.inspector_vars["targets"].get().split(",") if value.strip()]
            priority = int(self.inspector_vars["priority"].get().strip() or 0)
            new_id = self.document.update_edge(
                edge_id,
                new_id=self.inspector_vars["id"].get(), source=self.inspector_vars["source"].get(),
                targets=targets, when=predicate, priority=priority,
            )
            self.selection = ("edge", new_id)
            self.refresh()
            return True
        except (EditorError, ValueError) as exc:
            messagebox.showerror("Cannot apply edge", str(exc), parent=self.root)
            return False

    def _apply_navigator(self) -> bool:
        navigator_id = self.selection[1]
        if not navigator_id:
            return False

    def _apply_collection_item(self) -> bool:
        kind, item_id = self.selection
        if not item_id:
            return False
        try:
            value = _json(self.inspector_vars["collection_value"].get("1.0", "end-1c"), dict, "Definition")
            collection = str(self.inspector_vars["collection_name"])
            new_id = self.document.update_collection_item(collection, item_id, value)
            self.selection = (kind, new_id)
            self.refresh()
            return True
        except (EditorError, ValueError) as exc:
            messagebox.showerror("Cannot apply definition", str(exc), parent=self.root)
            return False

    def _time_gate_ids(self) -> list[str]:
        return [str(item.get("id")) for item in self.document.time_gates if item.get("id")]
        try:
            value = _json(self.inspector_vars["navigator"].get("1.0", "end-1c"), dict, "Navigator")
            new_id = str(value.get("id", "")).strip()
            if not new_id:
                raise EditorError("Navigator id cannot be empty")
            if any(item.get("id") == new_id and item.get("id") != navigator_id for item in self.document.navigators):
                raise EditorError(f"Navigator {new_id!r} already exists")
            index = next(index for index, item in enumerate(self.document.navigators) if item.get("id") == navigator_id)
            self.document.navigators[index] = value
            self.document.dirty = True
            self.selection = ("navigator", new_id)
            self.refresh()
            return True
        except (EditorError, ValueError, StopIteration) as exc:
            messagebox.showerror("Cannot apply navigator", str(exc), parent=self.root)
            return False
    def _apply_current(self) -> bool:
        if self.selection[0] == "node":
            return self._apply_node()
        if self.selection[0] == "edge":
            return self._apply_edge()
        if self.selection[0] == "environment":
            return True
        if self.selection[0] == "navigator":
            return self._apply_navigator()
        if self.selection[0] in {"solar_clock", "solar_site", "time_gate", "time_event", "scan_set"}:
            return self._apply_collection_item()
        return self._apply_graph()

    def add_environment(self, template: str) -> None:
        dialog = EnvironmentDialog(
            self.root, default_environment(template), template=template,
            control_bindings=self._control_bindings(), time_gates=self._time_gate_ids(),
            layer_options=self._audio_layer_options(), wind_options=self._wind_layer_options(),
        )
        self.root.wait_window(dialog)
        if dialog.result:
            environment_id = self.document.add_environment(dialog.result)
            self.selection = ("environment", environment_id)
            self.refresh()

    def add_navigator(self) -> None:
        existing = {str(item.get("id")) for item in self.document.navigators}
        number = 1
        while f"navigator_{number}" in existing: number += 1
        navigator_id = f"navigator_{number}"
        self.document.navigators.append({
            "id": navigator_id, "title": "New coordinate navigator",
            "active_when": {"variable": "navigator_active", "op": "eq", "value": True},
            "toggle_signal": "control.scanner", "ping_asset": "scanner_ping.mp3",
            "acquisition_asset": "scan_acquisition.mp3", "trigger_radius_m": 150,
            "hold_radius_m": 225, "targets": [{
                "id": "target_1", "title": "Target 1", "complete_when": {"always": False},
                "location": {"latitude": 0.0, "longitude": 0.0, "body_radius_m": 1000000.0},
            }],
        })
        self.document.dirty = True
        self.selection = ("navigator", navigator_id)
        self.refresh()

    def add_timing_item(self, kind: str) -> None:
        collections = {"solar_clock": "solar_clocks", "solar_site": "solar_sites", "time_gate": "time_gates", "time_event": "time_events", "scan_set": "scan_sets"}
        collection = collections[kind]
        existing = {str(item.get("id")) for item in self.document.collection(collection)}
        number = 1
        base = kind
        while f"{base}_{number}" in existing:
            number += 1
        item_id = f"{base}_{number}"
        if kind == "solar_clock":
            value = {"id": item_id, "title": "New body solar clock", "system": "", "body": "", "solar_day_seconds": 86400.0, "direction": 1, "anchor": {"at_utc": "2000-01-01T00:00:00Z", "longitude": 0.0, "local_hour": 0.0}}
        elif kind == "solar_site":
            if not self.document.solar_clocks:
                messagebox.showinfo("Solar site", "Add a body solar clock first.", parent=self.root); return
            value = {"id": item_id, "title": "New named solar site", "clock": self.document.solar_clocks[0]["id"], "latitude": 0.0, "longitude": 0.0}
        elif kind == "time_gate":
            if not self.document.solar_sites:
                messagebox.showinfo("Time gate", "Add a named solar site first.", parent=self.root); return
            value = {"id": item_id, "title": "New time gate", "site": self.document.solar_sites[0]["id"], "open_local_hour": 0.0, "close_local_hour": 12.0}
        elif kind == "time_event":
            if not self.document.time_gates:
                messagebox.showinfo("Boundary announcement", "Add a time gate first.", parent=self.root); return
            value = {"id": item_id, "title": "Window opening announcement", "gate": self.document.time_gates[0]["id"], "boundary": "open", "repeat": "once_per_cycle", "delay_seconds": 0.0, "catch_up": True, "valid_while": "open", "effects": [{"type": "log", "message": "Time window opened"}]}
        else:
            if not self.document.environments:
                messagebox.showinfo("Mission scan set", "Add a detection environment first.", parent=self.root); return
            zone = self.document.environments[0]
            location = zone.get("location", {})
            value = {"id": item_id, "title": "New mission scan set", "requires_control": "control.scanner", "active_vehicles": ["on_foot"], "hold_seconds": 8.0, "acquisition_asset": "scan_acquisition.mp3", "completion_asset": "scan_complete.mp3", "targets": [{"id": "target_1", "title": "Target 1", "detection_zone": zone["id"], "location": {"system": location.get("system", ""), "body": location.get("body", ""), "latitude": location.get("latitude", 0.0), "longitude": location.get("longitude", 0.0), "core_radius_m": location.get("core_radius_m", 25.0), "body_radius_m": location.get("body_radius_m", 1_000_000.0)}}]}
        self.document.collection(collection).append(value)
        self.document.dirty = True
        self.selection = (kind, item_id)
        self.refresh()

    def edit_environment(self) -> None:
        environment_id = self.selection[1] if self.selection[0] == "environment" else None
        if not environment_id:
            return
        dialog = EnvironmentDialog(
            self.root, self.document.environment(environment_id),
            control_bindings=self._control_bindings(), time_gates=self._time_gate_ids(),
            layer_options=self._audio_layer_options(), wind_options=self._wind_layer_options(),
        )
        self.root.wait_window(dialog)
        if dialog.result:
            new_id = self.document.update_environment(environment_id, dialog.result)
            self.selection = ("environment", new_id)
            self.refresh()

    def test_environment_entry(self, depth_percent: float = 100.0) -> None:
        environment_id = self.selection[1] if self.selection[0] == "environment" else None
        if not environment_id:
            return
        if self.simulator is None:
            self.start_simulation()
        if self.simulator is None:
            return
        zone = next((item for item in self.simulator.graph.data.get("environments", []) if item.get("id") == environment_id), None)
        if zone is None:
            messagebox.showinfo("Restart dry run", "Restart the dry run to include this environment edit.", parent=self.root)
            return
        try:
            preset = environment_entry_preset(zone, depth_percent)
            required_control = str(zone.get("requires_control", "")).strip()
            control_states = self.simulator.environment_state.get("control_states", {})
            if required_control and not bool(control_states.get(required_control)):
                self.simulator.dispatch(required_control)
            self.sim_signal.set(str(preset["signal_name"]))
            self.sim_signal_data.delete("1.0", "end")
            self.sim_signal_data.insert("1.0", _pretty(preset["signal_data"]))
            self.sim_world.delete("1.0", "end")
            self.sim_world.insert("1.0", _pretty(preset["world"]))
            self.simulator.run_trigger(
                str(preset["signal_name"]), preset["signal_data"], preset["world"], float(preset["advance_seconds"]),
            )
            state = self.simulator.environment_state.get("zones", {}).get(environment_id, {})
            control_note = f" with {required_control} ON" if required_control else ""
            self._refresh_simulator(
                f"Tested {environment_id} entry{control_note} — {state.get('phase', 'unknown')} at {state.get('depth_percent')}% depth."
            )
            self.side_tabs.select(self.simulator_host)
        except (DryRunError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Environment test failed", str(exc), parent=self.root)

    def add_node(self, kind: str) -> None:
        x = self.canvas.canvasx(self.canvas.winfo_width() // 2)
        y = self.canvas.canvasy(self.canvas.winfo_height() // 2)
        node_id = self.document.add_node(kind, x=max(150, x), y=max(100, y))
        self.selection = ("node", node_id)
        self.refresh()

    def add_edge(self) -> None:
        if len(self.document.nodes) < 2:
            messagebox.showinfo("Connect nodes", "Add at least two nodes first.", parent=self.root)
            return
        source = self.selection[1] if self.selection[0] == "node" else None
        dialog = EdgeDialog(self.root, list(self.document.nodes), source)
        self.root.wait_window(dialog)
        if dialog.result:
            source, target, predicate = dialog.result
            edge_id = self.document.add_edge(source, target, predicate)
            self.selection = ("edge", edge_id)
            self.refresh()

    def delete_selection(self) -> None:
        kind, selected_id = self.selection
        if kind == "graph" or not selected_id:
            return
        if not messagebox.askyesno("Delete", f"Delete {kind} {selected_id!r}?", parent=self.root):
            return
        try:
            if kind == "node":
                removed = self.document.delete_node(selected_id)
                suffix = f" and {len(removed)} connected edge(s)" if removed else ""
                self.status.set(f"Deleted node {selected_id}{suffix}")
            elif kind == "edge":
                self.document.delete_edge(selected_id)
            elif kind == "environment":
                self.document.delete_environment(selected_id)
            elif kind == "navigator":
                self.document.data["navigators"] = [item for item in self.document.navigators if item.get("id") != selected_id]
                self.document.dirty = True
            else:
                collections = {"solar_clock": "solar_clocks", "solar_site": "solar_sites", "time_gate": "time_gates", "time_event": "time_events", "scan_set": "scan_sets"}
                collection = collections[kind]
                self.document.delete_collection_item(collection, selected_id)
            self.selection = ("graph", None)
            self.refresh()
        except EditorError as exc:
            messagebox.showerror("Cannot delete", str(exc), parent=self.root)

    def auto_layout(self) -> None:
        self.document.auto_layout()
        self.refresh()

    def validate(self) -> bool:
        errors = self.document.validate()
        if errors:
            messagebox.showerror("Graph validation", "The graph has validation issues:\n\n• " + "\n• ".join(errors), parent=self.root)
            return False
        messagebox.showinfo(
            "Graph validation",
            f"Graph is valid.\n\n{len(self.document.nodes)} nodes · {len(self.document.edges)} edges · "
            f"{len(self.document.environments)} environments · {len(self.document.navigators)} navigators",
            parent=self.root,
        )
        return True

    def _confirm_discard(self) -> bool:
        if self.inspector is not None and not self._apply_current():
            return False
        if not self.document.dirty:
            return True
        answer = messagebox.askyesnocancel("Unsaved graph", "Save changes before continuing?", parent=self.root)
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def new(self) -> None:
        if not self._confirm_discard():
            return
        self._close_simulator_session()
        self.document = GraphDocument.new()
        self.selection = ("graph", None)
        self.refresh()

    def open(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Open story graph", initialdir=str(APP_DIR / "stories"),
            filetypes=[("Story graph", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self._close_simulator_session()
            self.document = GraphDocument.load(Path(path))
            self.selection = ("graph", None)
            self.refresh()
        except EditorError as exc:
            messagebox.showerror("Cannot open graph", str(exc), parent=self.root)

    def save(self) -> bool:
        if not self._apply_current():
            return False
        if self.document.path is None:
            return self.save_as(apply=False)
        try:
            path = self.document.save()
            self.status.set(f"Saved {path}")
            self.refresh()
            return True
        except EditorError as exc:
            messagebox.showerror("Graph not saved", str(exc), parent=self.root)
            return False

    def save_as(self, apply: bool = True) -> bool:
        if apply and not self._apply_current():
            return False
        path = filedialog.asksaveasfilename(
            title="Save story graph", initialdir=str(APP_DIR / "stories"), initialfile="graph.json",
            defaultextension=".json", filetypes=[("Story graph", "*.json")],
        )
        if not path:
            return False
        try:
            destination = self.document.save(Path(path))
            self.status.set(f"Saved {destination}")
            self.refresh()
            return True
        except EditorError as exc:
            messagebox.showerror("Graph not saved", str(exc), parent=self.root)
            return False

    def set_active(self) -> None:
        if not self.save() or self.document.path is None:
            return
        config = load_config()
        try:
            active_path = self.document.path.relative_to(APP_DIR).as_posix()
        except ValueError:
            active_path = str(self.document.path)
        config["active_graph"] = active_path
        save_config(config)
        messagebox.showinfo(
            "Active for next launch",
            "This graph has been saved and selected for the next Story Platform launch.\n\n"
            "A platform that is already running keeps the graph it loaded at startup. Stop it with Ctrl+C, "
            "close any remaining platform window, and start it once again to load these edits.\n\n"
            "If you changed the graph id or version, use RESET_ACTIVE_GRAPH.bat before restarting.",
            parent=self.root,
        )
        self.status.set(f"Active next launch: {active_path} — restart a running platform to load edits")

    def close(self) -> None:
        if self._confirm_discard():
            self._close_simulator_session()
            self.root.destroy()


def main(graph_path: Path | None = None) -> int:
    root = tk.Tk()
    GraphEditor(root, graph_path)
    root.mainloop()
    return 0
