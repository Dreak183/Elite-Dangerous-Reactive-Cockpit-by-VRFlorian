from __future__ import annotations

import copy
import json
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .authoring import (
    SignalDefinition,
    build_location_predicate,
    effect_summary,
    location_fields_from_predicate,
    predicate_summary,
    signal_catalog,
)
from .config import DEFAULT_CONTROL_BINDINGS, load_config, resolve_path, save_config, story_media_dir
from .control_adapter import binding_codes
from .environment_authoring import read_live_location
from .presentation_adapter import AudioPlayer


def _parse_json(text: str, label: str, expected: type | None = None) -> Any:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: line {exc.lineno}, column {exc.colno}") from exc
    if expected is not None and not isinstance(value, expected):
        raise ValueError(f"{label} must be a JSON {expected.__name__}")
    return value


def _write_text(widget: tk.Text, value: str) -> None:
    widget.delete("1.0", "end")
    widget.insert("1.0", value)


class SignalBrowserDialog(tk.Toplevel):
    def __init__(
        self, parent: tk.Misc, control_bindings: dict[str, Any] | None = None,
        initial: str = "", category: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Telemetry and control signal browser")
        self.geometry("850x540")
        self.minsize(650, 420)
        self.transient(parent)
        self.grab_set()
        self.result: SignalDefinition | None = None
        self.catalog = signal_catalog(control_bindings)
        self.required_category = category
        self.initial_signal = initial
        # The current signal is a selection hint, not a search query.  Using it
        # as the query made a newly opened browser appear to contain one item.
        self.search = tk.StringVar(value="")
        self.category = tk.StringVar(value=category or "All")

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        filters = ttk.Frame(body)
        filters.pack(fill="x", pady=(0, 8))
        ttk.Label(filters, text="Search").pack(side="left")
        entry = ttk.Entry(filters, textvariable=self.search)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        categories = ["All"] + sorted({item.category for item in self.catalog})
        category_box = ttk.Combobox(filters, textvariable=self.category, values=categories, state="readonly", width=16)
        category_box.pack(side="right")

        self.tree = ttk.Treeview(body, columns=("category", "signal", "description"), show="headings", selectmode="browse")
        self.tree.heading("category", text="Category")
        self.tree.heading("signal", text="Signal")
        self.tree.heading("description", text="What it means")
        self.tree.column("category", width=100, stretch=False)
        self.tree.column("signal", width=230, stretch=False)
        self.tree.column("description", width=430)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")

        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Use selected signal", command=self._accept).pack(side="right", padx=(0, 6))
        self.search.trace_add("write", lambda *_: self._refresh())
        self.category.trace_add("write", lambda *_: self._refresh())
        self.tree.bind("<Double-Button-1>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self._refresh()
        entry.focus_set()

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        needle = self.search.get().strip().casefold()
        category = self.category.get()
        initial_iid: str | None = None
        for index, item in enumerate(self.catalog):
            if self.required_category and item.category != self.required_category:
                continue
            if category != "All" and item.category != category:
                continue
            haystack = f"{item.category} {item.name} {item.description}".casefold()
            if needle and needle not in haystack:
                continue
            self.tree.insert("", "end", iid=str(index), values=(item.category, item.name, item.description))
            if item.name == self.initial_signal:
                initial_iid = str(index)
        children = self.tree.get_children()
        if children:
            selected = initial_iid if initial_iid in children else children[0]
            self.tree.selection_set(selected)
            self.tree.see(selected)

    def _accept(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Choose a signal", "Select a signal first.", parent=self)
            return
        self.result = self.catalog[int(selected[0])]
        self.destroy()


class LocationPredicateDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, initial: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.title("Elite location and surface geofence")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: dict[str, Any] | None = None
        values = location_fields_from_predicate(initial or {})
        self.capture_status = tk.StringVar(value="Capture reads Elite's current Status.json position; it does not change the trigger radius or altitude limits.")
        self.vars = {key: tk.StringVar(value=str(values.get(key, ""))) for key in (
            "system", "body", "station", "settlement", "latitude", "longitude", "radius_m",
            "body_radius_km", "min_altitude_m", "max_altitude_m",
        )}

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Named location", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        row = 1
        for key, label in (("system", "Star system"), ("body", "Body"), ("station", "Station / carrier"), ("settlement", "Settlement")):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=self.vars[key], width=46).grid(row=row, column=1, sticky="ew", pady=4)
            row += 1
        ttk.Separator(body).grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        row += 1
        ttk.Label(body, text="Optional surface geofence", font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        for key, label in (
            ("latitude", "Latitude (degrees)"), ("longitude", "Longitude (degrees)"),
            ("radius_m", "Trigger radius (metres)"), ("body_radius_km", "Body radius (kilometres)"),
            ("min_altitude_m", "Minimum altitude (optional)"), ("max_altitude_m", "Maximum altitude (optional)"),
        ):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=self.vars[key], width=24).grid(row=row, column=1, sticky="w", pady=4)
            row += 1
        ttk.Label(
            body,
            text="The body radius makes metre distances accurate on small moons. It may be omitted only when live telemetry supplies it.",
            foreground="#666666", wraplength=430,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 4))
        row += 1
        ttk.Button(body, text="Use current Elite location", command=self._capture_live).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(7, 3),
        )
        row += 1
        ttk.Label(body, textvariable=self.capture_status, foreground="#285f45", wraplength=430).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 4),
        )
        row += 1
        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Use location trigger", command=self._accept).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _event: self.destroy())

    def _capture_live(self) -> None:
        try:
            location = read_live_location()
            for key in ("system", "body", "station", "settlement"):
                self.vars[key].set(str(location.get(key, "")))
            for key in ("latitude", "longitude"):
                self.vars[key].set(str(location[key]))
            body_radius = location.get("body_radius_m")
            self.vars["body_radius_km"].set("" if body_radius is None else str(float(body_radius) / 1000.0))
            altitude = f" · altitude {location['altitude']} m (reference only)" if location.get("altitude") is not None else ""
            self.capture_status.set(
                f"Captured: {location.get('system', '')} / {location.get('body', '')} at "
                f"{location.get('captured_at', '')}{altitude}"
            )
        except (KeyError, TypeError, ValueError) as exc:
            messagebox.showerror("Cannot capture Elite location", str(exc), parent=self)

    def _accept(self) -> None:
        try:
            self.result = build_location_predicate({key: value.get() for key, value in self.vars.items()})
            self.destroy()
        except ValueError as exc:
            messagebox.showerror("Invalid location trigger", str(exc), parent=self)


class PredicateBuilderDialog(tk.Toplevel):
    TYPES = ("Always", "Telemetry signal", "Control", "Elite location / geofence", "Time gate", "Elapsed time", "Variable", "Custom JSON")

    def __init__(
        self, parent: tk.Misc, initial: dict[str, Any] | None = None,
        control_bindings: dict[str, Any] | None = None, time_gates: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Build trigger condition")
        self.geometry("620x430")
        self.minsize(540, 360)
        self.transient(parent)
        self.grab_set()
        self.result: dict[str, Any] | None = None
        self.initial = copy.deepcopy(initial or {"always": True})
        self.controls = {**DEFAULT_CONTROL_BINDINGS, **dict(control_bindings or {})}
        self.time_gates = list(time_gates or [])
        self.location_predicate: dict[str, Any] | None = None
        self.values: dict[str, Any] = {}
        self.kind = tk.StringVar(value=self._initial_kind(self.initial))

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Trigger type").pack(anchor="w")
        chooser = ttk.Combobox(outer, textvariable=self.kind, values=self.TYPES, state="readonly")
        chooser.pack(fill="x", pady=(2, 10))
        chooser.bind("<<ComboboxSelected>>", lambda _event: self._build_fields())
        self.fields = ttk.Frame(outer)
        self.fields.pack(fill="both", expand=True)
        self.summary = tk.StringVar()
        ttk.Label(outer, textvariable=self.summary, foreground="#285f45", wraplength=570).pack(anchor="w", pady=(8, 4))
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Use condition", command=self._accept).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _event: self.destroy())
        self._build_fields()

    @staticmethod
    def _initial_kind(predicate: dict[str, Any]) -> str:
        if predicate == {"always": True}:
            return "Always"
        if str(predicate.get("signal", "")).startswith("control."):
            return "Control"
        if "signal" in predicate:
            return "Telemetry signal"
        if "elapsed_seconds" in predicate:
            return "Elapsed time"
        if "variable" in predicate:
            return "Variable"
        if str(predicate.get("path", "")).startswith("world.time.gates."):
            return "Time gate"
        if PredicateBuilderDialog._is_location_predicate(predicate):
            return "Elite location / geofence"
        return "Custom JSON"

    @staticmethod
    def _is_location_predicate(predicate: Any) -> bool:
        if not isinstance(predicate, dict):
            return False
        if "geofence" in predicate:
            return True
        if str(predicate.get("path", "")).startswith("world.location."):
            return True
        if "all" in predicate and isinstance(predicate["all"], list) and predicate["all"]:
            return all(PredicateBuilderDialog._is_location_predicate(child) for child in predicate["all"])
        return False

    def _build_fields(self) -> None:
        for child in self.fields.winfo_children():
            child.destroy()
        self.values = {}
        kind = self.kind.get()
        if kind == "Always":
            ttk.Label(self.fields, text="The edge is taken immediately when its source node becomes active.", wraplength=560).pack(anchor="w")
        elif kind == "Telemetry signal":
            value = tk.StringVar(value=str(self.initial.get("signal", "journal.Disembark")))
            self.values["signal"] = value
            ttk.Label(self.fields, text="Normalized Journal or Status signal").pack(anchor="w")
            row = ttk.Frame(self.fields)
            row.pack(fill="x", pady=4)
            ttk.Entry(row, textvariable=value).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="Browse…", command=self._browse_signal).pack(side="right", padx=(5, 0))
        elif kind == "Control":
            options = [f"control.{name}" for name in self.controls] or ["control.answer"]
            current = str(self.initial.get("signal", options[0]))
            value = tk.StringVar(value=current if current in options else options[0])
            self.values["signal"] = value
            ttk.Label(self.fields, text="Configured generic control action").pack(anchor="w")
            ttk.Combobox(self.fields, textvariable=value, values=options, state="readonly").pack(fill="x", pady=4)
            ttk.Label(self.fields, text="Bindings can be changed from Authoring → Edit generic control bindings.", foreground="#666666").pack(anchor="w")
        elif kind == "Elite location / geofence":
            self.location_predicate = copy.deepcopy(self.initial) if self._initial_kind(self.initial) == kind else None
            ttk.Label(self.fields, text="Match a system, body, station, settlement and/or surface radius.", wraplength=560).pack(anchor="w")
            ttk.Button(self.fields, text="Open location builder…", command=self._open_location).pack(anchor="w", pady=8)
        elif kind == "Time gate":
            path = str(self.initial.get("path", ""))
            initial_gate = path.split(".")[3] if path.startswith("world.time.gates.") and len(path.split(".")) > 3 else ""
            options = self.time_gates or ([initial_gate] if initial_gate else [])
            self.values = {
                "gate": tk.StringVar(value=initial_gate or (options[0] if options else "")),
                "state": tk.StringVar(value=("Open" if self.initial.get("value", True) else "Closed")),
            }
            ttk.Label(self.fields, text="Named planetary time gate").pack(anchor="w")
            ttk.Combobox(self.fields, textvariable=self.values["gate"], values=options, state="normal").pack(fill="x", pady=4)
            ttk.Label(self.fields, text="Required state").pack(anchor="w", pady=(6, 1))
            ttk.Combobox(self.fields, textvariable=self.values["state"], values=("Open", "Closed"), state="readonly").pack(fill="x")
            ttk.Label(self.fields, text="Time gates are evaluated continuously, even while Elite telemetry is not moving.", foreground="#666666", wraplength=560).pack(anchor="w", pady=(6, 0))
        elif kind == "Elapsed time":
            value = tk.StringVar(value=str(self.initial.get("elapsed_seconds", 3)))
            self.values["seconds"] = value
            ttk.Label(self.fields, text="Seconds after entering the source node").pack(anchor="w")
            ttk.Entry(self.fields, textvariable=value).pack(fill="x", pady=4)
        elif kind == "Variable":
            self.values = {
                "name": tk.StringVar(value=str(self.initial.get("variable", ""))),
                "operator": tk.StringVar(value=str(self.initial.get("op", "eq"))),
                "value": tk.StringVar(value=json.dumps(self.initial.get("value", ""), ensure_ascii=False)),
            }
            for label, key in (("Variable name", "name"), ("Operator", "operator"), ("JSON value", "value")):
                ttk.Label(self.fields, text=label).pack(anchor="w", pady=(5, 1))
                if key == "operator":
                    ttk.Combobox(self.fields, textvariable=self.values[key], values=("eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists"), state="readonly").pack(fill="x")
                else:
                    ttk.Entry(self.fields, textvariable=self.values[key]).pack(fill="x")
        else:
            text = tk.Text(self.fields, height=12, wrap="none", font=("Consolas", 9))
            text.insert("1.0", json.dumps(self.initial, indent=2, ensure_ascii=False))
            text.pack(fill="both", expand=True)
            self.values["json"] = text
        self._update_summary()

    def _browse_signal(self) -> None:
        dialog = SignalBrowserDialog(self, self.controls, self.values["signal"].get())
        self.wait_window(dialog)
        if dialog.result:
            self.values["signal"].set(dialog.result.name)
            self._update_summary()

    def _open_location(self) -> None:
        dialog = LocationPredicateDialog(self, self.location_predicate or {})
        self.wait_window(dialog)
        if dialog.result:
            self.location_predicate = dialog.result
            self._update_summary()

    def _predicate(self) -> dict[str, Any]:
        kind = self.kind.get()
        if kind == "Always":
            return {"always": True}
        if kind in {"Telemetry signal", "Control"}:
            signal = self.values["signal"].get().strip()
            if not signal:
                raise ValueError("Choose a signal")
            return {"signal": signal}
        if kind == "Elite location / geofence":
            if self.location_predicate is None:
                raise ValueError("Open the location builder and enter a location")
            return copy.deepcopy(self.location_predicate)
        if kind == "Time gate":
            gate = self.values["gate"].get().strip()
            if not gate:
                raise ValueError("Choose a time gate")
            return {"path": f"world.time.gates.{gate}.open", "op": "eq", "value": self.values["state"].get() == "Open"}
        if kind == "Elapsed time":
            seconds = float(self.values["seconds"].get())
            if seconds < 0:
                raise ValueError("Elapsed time cannot be negative")
            return {"elapsed_seconds": seconds}
        if kind == "Variable":
            name = self.values["name"].get().strip()
            if not name:
                raise ValueError("Variable name cannot be empty")
            return {"variable": name, "op": self.values["operator"].get(), "value": _parse_json(self.values["value"].get(), "Variable value")}
        return _parse_json(self.values["json"].get("1.0", "end-1c"), "Condition", dict)

    def _update_summary(self) -> None:
        try:
            self.summary.set(predicate_summary(self._predicate()))
        except (ValueError, KeyError):
            self.summary.set("Complete the fields to build this condition.")

    def _accept(self) -> None:
        try:
            self.result = self._predicate()
            self.destroy()
        except ValueError as exc:
            messagebox.showerror("Invalid condition", str(exc), parent=self)


class EffectDialog(tk.Toplevel):
    TYPES = ("Objective", "Audio", "Visual", "Set variable", "Increment variable", "Clipboard", "Log", "Custom JSON")

    def __init__(
        self, parent: tk.Misc, initial: dict[str, Any] | None = None,
        force_type: str | None = None, layer_options: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Story effect")
        self.geometry("650x560")
        self.minsize(560, 480)
        self.transient(parent)
        self.grab_set()
        self.result: dict[str, Any] | None = None
        self.initial = copy.deepcopy(initial or {})
        self.layer_options = list(dict.fromkeys(layer_options or ["dialogue", "music"]))
        self.kind = tk.StringVar(value=force_type or self._kind_for(self.initial))
        self.values: dict[str, Any] = {}
        self.preview = AudioPlayer()

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Effect type").pack(anchor="w")
        chooser = ttk.Combobox(outer, textvariable=self.kind, values=self.TYPES, state="readonly")
        chooser.pack(fill="x", pady=(2, 8))
        chooser.bind("<<ComboboxSelected>>", lambda _event: self._build_fields())
        self.fields = ttk.Frame(outer)
        self.fields.pack(fill="both", expand=True)
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self._close).pack(side="right")
        ttk.Button(buttons, text="Use effect", command=self._accept).pack(side="right", padx=(0, 6))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())
        self._build_fields()

    @staticmethod
    def _kind_for(effect: dict[str, Any]) -> str:
        kind = effect.get("type")
        if kind == "next_step": return "Objective"
        if kind == "set": return "Set variable"
        if kind == "increment": return "Increment variable"
        if kind == "log": return "Log"
        if kind == "emit":
            channel = effect.get("channel")
            return {"audio": "Audio", "visual": "Visual", "clipboard": "Clipboard"}.get(str(channel), "Custom JSON")
        return "Objective" if not effect else "Custom JSON"

    def _build_fields(self) -> None:
        for child in self.fields.winfo_children(): child.destroy()
        self.values = {}
        kind = self.kind.get()
        payload = self.initial.get("payload") if isinstance(self.initial.get("payload"), dict) else {}
        if kind == "Objective":
            ttk.Label(self.fields, text="Objective text").pack(anchor="w")
            text = tk.Text(self.fields, height=6, wrap="word")
            text.insert("1.0", str(self.initial.get("text", "")))
            text.pack(fill="x", pady=(2, 6))
            ttk.Label(self.fields, text="Choices — one visible option per line (optional)").pack(anchor="w")
            choices = tk.Text(self.fields, height=5, wrap="word")
            choices.insert("1.0", "\n".join(self.initial.get("choices", [])))
            choices.pack(fill="x", pady=2)
            self.values.update(text=text, choices=choices)
        elif kind == "Audio":
            action = payload.get("action")
            mode = (
                "Set layer volume" if action == "set_layer_volume" else
                "Return to authored volume" if action == "clear_layer_volume_override" else
                "Stop named layer" if action == "stop_layer" else
                "Play / replace layer" if payload.get("asset") else
                "System sound" if payload.get("system_sound") else
                "Stop all audio" if payload.get("stop") else "Play / replace layer"
            )
            self.values = {
                "mode": tk.StringVar(value=mode), "asset": tk.StringVar(value=str(payload.get("asset", ""))),
                "volume": tk.DoubleVar(value=float(payload.get("volume", 1.0))),
                "loop": tk.BooleanVar(value=bool(payload.get("loop", False))),
                "system": tk.StringVar(value=str(payload.get("system_sound", "asterisk"))),
                "layer": tk.StringVar(value=str(payload.get("layer", "dialogue"))),
                "completion_signal": tk.StringVar(value=str(payload.get("completion_signal", ""))),
                "transition_seconds": tk.StringVar(value=str(payload.get("transition_seconds", 2.0))),
            }
            ttk.Label(self.fields, text="Playback action").pack(anchor="w")
            ttk.Combobox(
                self.fields, textvariable=self.values["mode"],
                values=("Play / replace layer", "Set layer volume", "Return to authored volume", "Stop named layer", "Stop all audio", "System sound"),
                state="readonly",
            ).pack(fill="x", pady=2)
            ttk.Label(self.fields, text="Named layer (dialogue, music, or environment.<zone>.<layer>)").pack(anchor="w", pady=(7, 1))
            ttk.Combobox(
                self.fields, textvariable=self.values["layer"], values=self.layer_options, state="normal",
            ).pack(fill="x")
            ttk.Label(self.fields, text="Audio asset beneath the configured story-media folder").pack(anchor="w", pady=(7, 1))
            row = ttk.Frame(self.fields); row.pack(fill="x")
            ttk.Entry(row, textvariable=self.values["asset"]).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="Browse…", command=self._browse_audio).pack(side="left", padx=4)
            ttk.Button(row, text="Preview", command=self._preview_audio).pack(side="left", padx=2)
            ttk.Button(row, text="Stop", command=self.preview.stop).pack(side="left", padx=2)
            volume_row = ttk.Frame(self.fields); volume_row.pack(fill="x", pady=(7, 1))
            ttk.Label(volume_row, text="Volume").pack(side="left")
            self.volume_label = tk.StringVar()
            ttk.Label(volume_row, textvariable=self.volume_label).pack(side="right")
            self._preview_volume_changed(self.values["volume"].get())
            ttk.Scale(
                self.fields, variable=self.values["volume"], from_=0.0, to=1.0,
                command=self._preview_volume_changed,
            ).pack(fill="x")
            ttk.Label(self.fields, text="Volume smoothing (seconds; used by Set/Return volume)").pack(anchor="w", pady=(7, 1))
            ttk.Entry(self.fields, textvariable=self.values["transition_seconds"]).pack(fill="x")
            ttk.Checkbutton(self.fields, text="Loop until another audio command stops/replaces it", variable=self.values["loop"]).pack(anchor="w", pady=5)
            ttk.Label(self.fields, text="Signal emitted when one-shot playback finishes (optional)").pack(anchor="w", pady=(7, 1))
            ttk.Entry(self.fields, textvariable=self.values["completion_signal"]).pack(fill="x")
            ttk.Label(self.fields, text="Windows system sound").pack(anchor="w", pady=(7, 1))
            ttk.Combobox(self.fields, textvariable=self.values["system"], values=("asterisk", "exclamation", "hand", "question", "ok"), state="readonly").pack(fill="x")
        elif kind == "Visual":
            self.values["cue"] = tk.StringVar(value=str(payload.get("cue", "pulse")))
            ttk.Label(self.fields, text="Visual cue name").pack(anchor="w")
            ttk.Combobox(self.fields, textvariable=self.values["cue"], values=("incoming", "choice", "scanner", "success", "decline", "pulse"), state="normal").pack(fill="x", pady=2)
            ttk.Label(self.fields, text="Additional payload fields (JSON object)").pack(anchor="w", pady=(8, 1))
            extra = {key: value for key, value in payload.items() if key != "cue"}
            text = tk.Text(self.fields, height=10, wrap="none", font=("Consolas", 9))
            text.insert("1.0", json.dumps(extra, indent=2, ensure_ascii=False)); text.pack(fill="both", expand=True)
            self.values["extra"] = text
        elif kind in {"Set variable", "Increment variable"}:
            self.values["variable"] = tk.StringVar(value=str(self.initial.get("variable", "")))
            ttk.Label(self.fields, text="Variable name").pack(anchor="w")
            ttk.Entry(self.fields, textvariable=self.values["variable"]).pack(fill="x", pady=2)
            if kind == "Set variable":
                self.values["value"] = tk.StringVar(value=json.dumps(self.initial.get("value", ""), ensure_ascii=False))
                ttk.Label(self.fields, text="New value (JSON)").pack(anchor="w", pady=(8, 1))
                ttk.Entry(self.fields, textvariable=self.values["value"]).pack(fill="x")
            else:
                self.values["amount"] = tk.StringVar(value=str(self.initial.get("amount", 1)))
                ttk.Label(self.fields, text="Amount").pack(anchor="w", pady=(8, 1))
                ttk.Entry(self.fields, textvariable=self.values["amount"]).pack(fill="x")
        elif kind in {"Clipboard", "Log"}:
            key = "text" if kind == "Clipboard" else "message"
            value = payload.get("text", "") if kind == "Clipboard" else self.initial.get("message", "")
            ttk.Label(self.fields, text=f"{kind} text").pack(anchor="w")
            text = tk.Text(self.fields, height=12, wrap="word"); text.insert("1.0", str(value)); text.pack(fill="both", expand=True)
            self.values[key] = text
        else:
            text = tk.Text(self.fields, height=16, wrap="none", font=("Consolas", 9))
            text.insert("1.0", json.dumps(self.initial or {"type": "log", "message": ""}, indent=2, ensure_ascii=False)); text.pack(fill="both", expand=True)
            self.values["json"] = text

    def _media_root(self, ask: bool = False) -> Path | None:
        config = load_config()
        root = story_media_dir(config)
        if root and root.is_dir(): return root
        if not ask: return None
        selected = filedialog.askdirectory(title="Choose the root folder containing story audio", parent=self)
        if not selected: return None
        root = Path(selected).resolve()
        config["external_story_media_dir"] = str(root)
        save_config(config)
        return root

    def _browse_audio(self) -> None:
        root = self._media_root(ask=True)
        if not root: return
        selected = filedialog.askopenfilename(
            title="Choose story audio", initialdir=str(root), parent=self,
            filetypes=(("Audio", "*.mp3 *.wav *.wma *.mid *.midi"), ("All files", "*.*")),
        )
        if not selected: return
        path = Path(selected).resolve()
        if not path.is_relative_to(root):
            messagebox.showerror("Audio outside media folder", f"Choose a file inside:\n{root}", parent=self)
            return
        self.values["asset"].set(path.relative_to(root).as_posix())

    def _preview_audio(self) -> None:
        try:
            root = self._media_root(ask=False)
            asset = self.values["asset"].get().strip()
            if not root or not asset: raise ValueError("Choose an audio asset first")
            path = (root / asset).resolve()
            if not path.is_relative_to(root) or not path.is_file(): raise ValueError(f"Audio asset not found: {path}")
            self.preview.play_file(path, float(self.values["volume"].get()), bool(self.values["loop"].get()))
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Cannot preview audio", str(exc), parent=self)

    def _preview_volume_changed(self, value: Any) -> None:
        volume = max(0.0, min(1.0, float(value)))
        if hasattr(self, "volume_label"):
            self.volume_label.set(f"{round(volume * 100):d}%")
        self.preview.set_volume(volume)

    def _effect(self) -> dict[str, Any]:
        kind = self.kind.get()
        if kind == "Objective":
            text = self.values["text"].get("1.0", "end-1c").strip()
            if not text: raise ValueError("Objective text cannot be empty")
            effect: dict[str, Any] = {"type": "next_step", "text": text}
            choices = [line.strip() for line in self.values["choices"].get("1.0", "end-1c").splitlines() if line.strip()]
            if choices: effect["choices"] = choices
            return effect
        if kind == "Audio":
            mode = self.values["mode"].get()
            layer = self.values["layer"].get().strip() or "dialogue"
            if mode == "Stop all audio": return {"type": "emit", "channel": "audio", "payload": {"stop": True}}
            if mode == "Stop named layer":
                return {"type": "emit", "channel": "audio", "payload": {"action": "stop_layer", "layer": layer}}
            if mode == "Set layer volume":
                transition = float(self.values["transition_seconds"].get())
                if transition < 0:
                    raise ValueError("Volume smoothing cannot be negative")
                return {"type": "emit", "channel": "audio", "payload": {
                    "action": "set_layer_volume", "layer": layer,
                    "volume": round(float(self.values["volume"].get()), 3),
                    "transition_seconds": transition,
                }}
            if mode == "Return to authored volume":
                transition = float(self.values["transition_seconds"].get())
                if transition < 0:
                    raise ValueError("Volume smoothing cannot be negative")
                return {"type": "emit", "channel": "audio", "payload": {
                    "action": "clear_layer_volume_override", "layer": layer,
                    "transition_seconds": transition,
                }}
            if mode == "System sound": return {"type": "emit", "channel": "audio", "payload": {"system_sound": self.values["system"].get()}}
            asset = self.values["asset"].get().strip()
            if not asset: raise ValueError("Choose an audio asset")
            payload: dict[str, Any] = {
                "action": "play_layer", "layer": layer, "asset": asset,
                "volume": round(float(self.values["volume"].get()), 3),
            }
            if self.values["loop"].get(): payload["loop"] = True
            completion = self.values["completion_signal"].get().strip()
            if completion:
                if self.values["loop"].get():
                    raise ValueError("A looping sound cannot emit a finished signal")
                payload["completion_signal"] = completion
            return {"type": "emit", "channel": "audio", "payload": payload}
        if kind == "Visual":
            extra = _parse_json(self.values["extra"].get("1.0", "end-1c"), "Visual payload", dict)
            return {"type": "emit", "channel": "visual", "payload": {"cue": self.values["cue"].get().strip() or "pulse", **extra}}
        if kind == "Set variable":
            name = self.values["variable"].get().strip()
            if not name: raise ValueError("Variable name cannot be empty")
            return {"type": "set", "variable": name, "value": _parse_json(self.values["value"].get(), "Variable value")}
        if kind == "Increment variable":
            name = self.values["variable"].get().strip()
            if not name: raise ValueError("Variable name cannot be empty")
            return {"type": "increment", "variable": name, "amount": float(self.values["amount"].get())}
        if kind == "Clipboard":
            return {"type": "emit", "channel": "clipboard", "payload": {"text": self.values["text"].get("1.0", "end-1c")}}
        if kind == "Log":
            return {"type": "log", "message": self.values["message"].get("1.0", "end-1c")}
        return _parse_json(self.values["json"].get("1.0", "end-1c"), "Effect", dict)

    def _accept(self) -> None:
        try:
            self.result = self._effect()
            self.preview.stop()
            self.destroy()
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Invalid effect", str(exc), parent=self)

    def _close(self) -> None:
        self.preview.stop()
        self.destroy()


class EffectsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, effects: list[dict[str, Any]], layer_options: list[str] | None = None) -> None:
        super().__init__(parent)
        self.title("Structured node effects")
        self.geometry("850x520")
        self.minsize(680, 420)
        self.transient(parent)
        self.grab_set()
        self.effects = copy.deepcopy(effects)
        self.layer_options = layer_options or ["dialogue", "music"]
        self.result: list[dict[str, Any]] | None = None
        body = ttk.Frame(self, padding=12); body.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(body, font=("Segoe UI", 9), activestyle="dotbox")
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda _event: self._edit())
        add = ttk.Frame(body); add.pack(fill="x", pady=6)
        for label, kind in (("+ Objective", "Objective"), ("+ Audio", "Audio"), ("+ Visual", "Visual"), ("+ Variable", "Set variable"), ("+ Clipboard", "Clipboard"), ("+ Log", "Log")):
            ttk.Button(add, text=label, command=lambda value=kind: self._add(value)).pack(side="left", padx=2)
        actions = ttk.Frame(body); actions.pack(fill="x")
        ttk.Button(actions, text="Edit selected", command=self._edit).pack(side="left", padx=2)
        ttk.Button(actions, text="Delete", command=self._delete).pack(side="left", padx=2)
        ttk.Button(actions, text="Move up", command=lambda: self._move(-1)).pack(side="left", padx=2)
        ttk.Button(actions, text="Move down", command=lambda: self._move(1)).pack(side="left", padx=2)
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Apply effects", command=self._accept).pack(side="right", padx=6)
        self.bind("<Escape>", lambda _event: self.destroy())
        self._refresh()

    def _refresh(self, select: int | None = None) -> None:
        self.listbox.delete(0, "end")
        for index, effect in enumerate(self.effects, 1): self.listbox.insert("end", f"{index:02d}  {effect_summary(effect)}")
        if self.effects:
            index = max(0, min(len(self.effects) - 1, select if select is not None else 0))
            self.listbox.selection_set(index); self.listbox.see(index)

    def _selected(self) -> int | None:
        selected = self.listbox.curselection()
        return int(selected[0]) if selected else None

    def _add(self, kind: str) -> None:
        dialog = EffectDialog(self, force_type=kind, layer_options=self.layer_options); self.wait_window(dialog)
        if dialog.result:
            self.effects.append(dialog.result); self._refresh(len(self.effects) - 1)

    def _edit(self) -> None:
        index = self._selected()
        if index is None: return
        dialog = EffectDialog(self, self.effects[index], layer_options=self.layer_options); self.wait_window(dialog)
        if dialog.result:
            self.effects[index] = dialog.result; self._refresh(index)

    def _delete(self) -> None:
        index = self._selected()
        if index is None: return
        del self.effects[index]; self._refresh(min(index, len(self.effects) - 1))

    def _move(self, amount: int) -> None:
        index = self._selected()
        if index is None: return
        target = index + amount
        if 0 <= target < len(self.effects):
            self.effects[index], self.effects[target] = self.effects[target], self.effects[index]
            self._refresh(target)

    def _accept(self) -> None:
        self.result = copy.deepcopy(self.effects); self.destroy()


class ControlBindingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, bindings: dict[str, Any]) -> None:
        super().__init__(parent)
        self.title("Generic story controls")
        self.geometry("560x430")
        self.transient(parent); self.grab_set()
        self.bindings = {str(key): str(value) for key, value in bindings.items()}
        self.result: dict[str, str] | None = None
        body = ttk.Frame(self, padding=12); body.pack(fill="both", expand=True)
        ttk.Label(body, text="Actions stay generic; graphs receive control.<action> signals.", wraplength=520).pack(anchor="w", pady=(0, 6))
        self.tree = ttk.Treeview(body, columns=("action", "binding"), show="headings", selectmode="browse")
        self.tree.heading("action", text="Action / signal"); self.tree.heading("binding", text="Key binding")
        self.tree.pack(fill="both", expand=True); self.tree.bind("<Double-Button-1>", lambda _event: self._edit())
        row = ttk.Frame(body); row.pack(fill="x", pady=(7, 0))
        ttk.Button(row, text="Add", command=self._add).pack(side="left", padx=2)
        ttk.Button(row, text="Edit", command=self._edit).pack(side="left", padx=2)
        ttk.Button(row, text="Delete", command=self._delete).pack(side="left", padx=2)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(row, text="Save bindings", command=self._accept).pack(side="right", padx=6)
        self._refresh()

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for action, binding in self.bindings.items(): self.tree.insert("", "end", iid=action, values=(f"control.{action}", binding))

    def _details(self, action: str = "", binding: str = "") -> tuple[str, str] | None:
        action_value = simpledialog.askstring("Control action", "Action name (creates control.<name>):", initialvalue=action, parent=self)
        if action_value is None: return None
        action_value = re.sub(r"[^a-zA-Z0-9_]+", "_", action_value.strip()).strip("_").lower()
        if not action_value:
            messagebox.showerror("Invalid action", "Action name cannot be empty.", parent=self); return None
        binding_value = simpledialog.askstring("Key binding", "Binding, for example CTRL+S:", initialvalue=binding, parent=self)
        if binding_value is None or not binding_value.strip(): return None
        return action_value, binding_value.strip().upper()

    def _add(self) -> None:
        result = self._details()
        if result:
            action, binding = result; self.bindings[action] = binding; self._refresh(); self.tree.selection_set(action)

    def _edit(self) -> None:
        selected = self.tree.selection()
        if not selected: return
        old = selected[0]; result = self._details(old, self.bindings[old])
        if result:
            action, binding = result
            if action != old: del self.bindings[old]
            self.bindings[action] = binding; self._refresh(); self.tree.selection_set(action)

    def _delete(self) -> None:
        selected = self.tree.selection()
        if selected: del self.bindings[selected[0]]; self._refresh()

    def _accept(self) -> None:
        try:
            for binding in self.bindings.values():
                binding_codes(binding)
            normalized = [binding.replace(" ", "").upper() for binding in self.bindings.values()]
            if len(normalized) != len(set(normalized)):
                raise ValueError("Two control actions cannot use the same key binding")
            self.result = dict(self.bindings); self.destroy()
        except ValueError as exc:
            messagebox.showerror("Invalid control bindings", str(exc), parent=self)
