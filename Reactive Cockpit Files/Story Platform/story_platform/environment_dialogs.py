from __future__ import annotations

import copy
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .authoring import predicate_summary
from .authoring_dialogs import PredicateBuilderDialog
from .config import DEFAULT_CONTROL_BINDINGS, load_config, resolve_path, save_config, story_media_dir
from .environment_authoring import (
    default_environment,
    default_planetary_glide_audio_layer,
    read_live_location,
)
from .model import validate_graph
from .presentation_adapter import AudioPlayer
from .wind_dialogs import WindLayerDialog, WindPauseDialog


_CONTINUOUS_PLAYBACK = "Continuous ambience / one-shot"
_DETECTION_PLAYBACK = "Detection pulse — distance + motion"


def _slug(value: str, fallback: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip()).strip("_").lower()
    return value or fallback


def environment_layer_summary(layer: dict[str, Any]) -> str:
    if layer.get("type") == "wind":
        return f"{layer.get('id')} · wind FROM {layer.get('wind_from',270)}° · {layer.get('strength',85)}% · {layer.get('mode','stable')}"
    delay = float(layer.get("start_delay_seconds", 0))
    response = layer.get("depth_response") if isinstance(layer.get("depth_response"), dict) else {}
    depth = str(response.get("curve", "constant"))
    suffix = f" · after {delay:g}s" if delay else ""
    if layer.get("type") == "audio":
        playback = (
            "detection pulse" if layer.get("playback_mode") == "scanner_pulse"
            else ("loop" if layer.get("loop", True) else "one shot")
        )
        return f"{layer.get('id')} · audio · {layer.get('asset')} · {float(layer.get('volume', 1)) * 100:.0f}% · {depth} · {playback}{suffix}"
    return f"{layer.get('id')} · visual · {layer.get('cue')} · {float(layer.get('intensity', 1)) * 100:.0f}% · {depth}{suffix}"


def layer_adjustment_summary(adjustment: dict[str, Any]) -> str:
    if adjustment.get("action") == "pause_wind":
        return f"Pause wind: {adjustment.get('target_layer', '')} · resumes on exit"
    return (
        f"{adjustment.get('target_layer', '')} → {float(adjustment.get('volume', 1)) * 100:.0f}%"
        f" · {float(adjustment.get('transition_seconds', 2)):.1f}s smoothing"
        f" · priority {int(adjustment.get('priority', 0))}"
    )


class LayerAdjustmentDialog(tk.Toplevel):
    def __init__(
        self, parent: tk.Misc, layer_options: list[str], initial: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Adjust another environment layer")
        self.geometry("620x300")
        self.transient(parent); self.grab_set()
        value = copy.deepcopy(initial or {})
        options = [item for item in layer_options if item.startswith("environment.")]
        self.result: dict[str, Any] | None = None
        self.target = tk.StringVar(value=str(value.get("target_layer", options[0] if options else "")))
        self.volume = tk.DoubleVar(value=float(value.get("volume", 1.0)) * 100)
        self.transition = tk.StringVar(value=str(value.get("transition_seconds", 4.0)))
        self.priority = tk.StringVar(value=str(value.get("priority", 0)))
        self.volume_label = tk.StringVar()

        outer = ttk.Frame(self, padding=14); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="While this environment is active, keep this existing audio layer at:").pack(anchor="w")
        ttk.Combobox(outer, textvariable=self.target, values=options, state="normal").pack(fill="x", pady=(3, 10))
        row = ttk.Frame(outer); row.pack(fill="x")
        ttk.Label(row, text="Target volume").pack(side="left")
        ttk.Label(row, textvariable=self.volume_label).pack(side="right")
        ttk.Scale(outer, variable=self.volume, from_=0, to=100, command=self._volume_changed).pack(fill="x")
        self._volume_changed(self.volume.get())
        values = ttk.Frame(outer); values.pack(fill="x", pady=(12, 0))
        ttk.Label(values, text="Smoothing in and out (seconds)").grid(row=0, column=0, sticky="w")
        ttk.Entry(values, textvariable=self.transition).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(values, text="Priority (higher wins)").grid(row=0, column=1, sticky="w")
        ttk.Entry(values, textvariable=self.priority).grid(row=1, column=1, sticky="ew")
        values.columnconfigure(0, weight=1); values.columnconfigure(1, weight=1)
        ttk.Label(
            outer,
            text="When this environment ends, the target automatically returns to its next active rule or its normal authored level. Equal-priority overlaps use the quietest value.",
            foreground="#666666", wraplength=570,
        ).pack(anchor="w", pady=(10, 0))
        buttons = ttk.Frame(outer); buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Use adjustment", command=self._accept).pack(side="right", padx=6)

    def _volume_changed(self, value: Any) -> None:
        self.volume_label.set(f"{round(float(value))}%")

    def _accept(self) -> None:
        try:
            target = self.target.get().strip()
            if not target.startswith("environment.") or len(target.split(".")) < 3:
                raise ValueError("Choose an existing environment audio layer")
            transition = float(self.transition.get())
            if transition < 0:
                raise ValueError("Smoothing cannot be negative")
            self.result = {
                "target_layer": target,
                "volume": round(max(0.0, min(100.0, self.volume.get())) / 100, 4),
                "transition_seconds": transition,
                "priority": int(self.priority.get()),
            }
            self.destroy()
        except ValueError as exc:
            messagebox.showerror("Invalid layer adjustment", str(exc), parent=self)


class EnvironmentLayerDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, initial: dict[str, Any] | None = None, force_type: str | None = None) -> None:
        super().__init__(parent)
        self.title("Environment layer")
        self.geometry("720x760")
        self.minsize(620, 680)
        self.transient(parent); self.grab_set()
        value = copy.deepcopy(initial or {})
        kind = force_type or str(value.get("type", "audio"))
        response = value.get("depth_response") if isinstance(value.get("depth_response"), dict) else {}
        scanner_pulse = value.get("scanner_pulse") if isinstance(value.get("scanner_pulse"), dict) else {}
        maximum = float(value.get("volume", value.get("intensity", 1.0)))
        self.result: dict[str, Any] | None = None
        self.preview = AudioPlayer("storyenvironmentpreview")
        self.vars = {
            "type": tk.StringVar(value=kind),
            "id": tk.StringVar(value=str(value.get("id", "base" if kind == "audio" else "atmosphere"))),
            "asset": tk.StringVar(value=str(value.get("asset", ""))),
            "cue": tk.StringVar(value=str(value.get("cue", "deep_danger"))),
            "maximum": tk.DoubleVar(value=maximum * 100),
            "loop": tk.BooleanVar(value=bool(value.get("loop", True))),
            "playback_mode": tk.StringVar(value=(
                _DETECTION_PLAYBACK if value.get("playback_mode") == "scanner_pulse"
                else _CONTINUOUS_PLAYBACK
            )),
            "scanner_outer_gap": tk.StringVar(value=str(scanner_pulse.get("outer_gap_seconds", 5.0))),
            "scanner_core_gap": tk.StringVar(value=str(scanner_pulse.get("core_gap_seconds", 0.0))),
            "scanner_gap_exponent": tk.StringVar(value=str(scanner_pulse.get("exponent", 1.0))),
            "scanner_momentum_step": tk.StringVar(value=str(scanner_pulse.get("momentum_step_percent", 4.0))),
            "scanner_momentum_limit": tk.StringVar(value=str(scanner_pulse.get("momentum_limit_percent", 10.0))),
            "start_delay": tk.StringVar(value=str(value.get("start_delay_seconds", 0))),
            "stop_delay": tk.StringVar(value=str(value.get("stop_delay_seconds", 0))),
            "volume_transition": tk.StringVar(value=str(value.get("volume_transition_seconds", 2.0))),
            "curve": tk.StringVar(value=str(response.get("curve", "constant"))),
            "start_percent": tk.StringVar(value=str(response.get("start_percent", 0))),
            "minimum": tk.StringVar(value=str(float(response.get("minimum", 0)) * 100)),
            "exponent": tk.StringVar(value=str(response.get("exponent", 2))),
            "silent_before_start": tk.BooleanVar(value=bool(response.get("silent_before_start", False))),
        }

        outer = ttk.Frame(self, padding=14); outer.pack(fill="both", expand=True)
        identity = ttk.LabelFrame(outer, text="Layer identity", padding=8); identity.pack(fill="x")
        ttk.Label(identity, text="Type").grid(row=0, column=0, sticky="w", padx=3, pady=3)
        ttk.Combobox(identity, textvariable=self.vars["type"], values=("audio", "visual"), state="readonly", width=16).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Label(identity, text="Layer name").grid(row=1, column=0, sticky="w", padx=3, pady=3)
        ttk.Entry(identity, textvariable=self.vars["id"]).grid(row=1, column=1, sticky="ew", padx=3)
        identity.columnconfigure(1, weight=1)

        content = ttk.LabelFrame(outer, text="Content", padding=8); content.pack(fill="x", pady=(8, 0))
        ttk.Label(content, text="Audio file (for audio layers)").grid(row=0, column=0, sticky="w", columnspan=3)
        ttk.Entry(content, textvariable=self.vars["asset"]).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=3)
        ttk.Button(content, text="Browse…", command=self._browse_audio).grid(row=1, column=1, padx=2)
        ttk.Button(content, text="Preview", command=self._preview).grid(row=1, column=2, padx=2)
        ttk.Button(content, text="Stop", command=self.preview.stop).grid(row=1, column=3, padx=2)
        ttk.Label(content, text="Visual cue (for visual layers)").grid(row=2, column=0, sticky="w", columnspan=3, pady=(6, 0))
        ttk.Entry(content, textvariable=self.vars["cue"]).grid(row=3, column=0, columnspan=4, sticky="ew", pady=3)
        content.columnconfigure(0, weight=1)

        level = ttk.LabelFrame(outer, text="Maximum level", padding=8); level.pack(fill="x", pady=(8, 0))
        self.level_label = tk.StringVar()
        ttk.Label(level, text="Volume / visual intensity").pack(side="left")
        ttk.Label(level, textvariable=self.level_label).pack(side="right")
        ttk.Scale(
            outer, variable=self.vars["maximum"], from_=0, to=100,
            command=self._level_changed,
        ).pack(fill="x", padx=8)
        self._level_changed(self.vars["maximum"].get())
        playback = ttk.LabelFrame(outer, text="Playback behavior", padding=8); playback.pack(fill="x", pady=(5, 0))
        ttk.Label(playback, text="Mode").grid(row=0, column=0, sticky="w", padx=3)
        ttk.Combobox(
            playback, textvariable=self.vars["playback_mode"], state="readonly",
            values=(_CONTINUOUS_PLAYBACK, _DETECTION_PLAYBACK),
        ).grid(row=0, column=1, columnspan=3, sticky="ew", padx=3)
        ttk.Checkbutton(playback, text="Loop audio until this layer is stopped", variable=self.vars["loop"]).grid(
            row=1, column=0, columnspan=4, sticky="w", padx=3, pady=3,
        )
        for column, (label, key) in enumerate((
            ("Outer-edge silence (s)", "scanner_outer_gap"),
            ("100% core silence (s)", "scanner_core_gap"),
            ("Frequency curve", "scanner_gap_exponent"),
        )):
            ttk.Label(playback, text=label).grid(row=2, column=column, sticky="w", padx=3)
            ttk.Entry(playback, textvariable=self.vars[key], width=14).grid(row=3, column=column, sticky="ew", padx=3)
            playback.columnconfigure(column, weight=1)
        for column, (label, key) in enumerate((
            ("Motion step %", "scanner_momentum_step"),
            ("Prediction cap %", "scanner_momentum_limit"),
        ), start=3):
            ttk.Label(playback, text=label).grid(row=2, column=column, sticky="w", padx=3)
            ttk.Entry(playback, textvariable=self.vars[key], width=11).grid(row=3, column=column, sticky="ew", padx=3)
            playback.columnconfigure(column, weight=1)
        ttk.Label(
            playback,
            text="Detection mode always plays the complete sound. Distance sets the measured silence; movement gives successive pings a small bounded acceleration or deceleration until Elite publishes another coordinate. At the 100% core the sound loops continuously.",
            foreground="#666666", wraplength=620,
        ).grid(row=4, column=0, columnspan=5, sticky="w", padx=3, pady=(5, 0))

        timing = ttk.LabelFrame(outer, text="Explicit timing", padding=8); timing.pack(fill="x", pady=(5, 0))
        for column, (label, key) in enumerate((("Start delay (seconds)", "start_delay"), ("Stop delay (seconds)", "stop_delay"), ("Volume smoothing (seconds)", "volume_transition"))):
            ttk.Label(timing, text=label).grid(row=0, column=column, sticky="w", padx=3)
            ttk.Entry(timing, textvariable=self.vars[key]).grid(row=1, column=column, sticky="ew", padx=3)
            timing.columnconfigure(column, weight=1)

        depth = ttk.LabelFrame(outer, text="Response to zone depth", padding=8); depth.pack(fill="x", pady=(8, 0))
        fields = (
            ("Curve", "curve", ("constant", "linear", "exponential")),
            ("Begin changing at depth %", "start_percent", None),
            ("Minimum level %", "minimum", None),
            ("Exponent", "exponent", None),
        )
        for row, (label, key, options) in enumerate(fields):
            ttk.Label(depth, text=label).grid(row=row, column=0, sticky="w", pady=3)
            if options:
                ttk.Combobox(depth, textvariable=self.vars[key], values=options, state="readonly").grid(row=row, column=1, sticky="ew", pady=3)
            else:
                ttk.Entry(depth, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Checkbutton(
            depth,
            text="Silent before the authored start depth",
            variable=self.vars["silent_before_start"],
        ).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(5, 2))
        depth.columnconfigure(1, weight=1)
        ttk.Label(
            outer, text="Looping ambience uses smoothing for volume changes, fades in/out and vehicle changes. Start/stop delays still apply.",
            foreground="#666666", wraplength=630,
        ).pack(anchor="w", pady=(7, 0))

        buttons = ttk.Frame(outer); buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self._close).pack(side="right")
        ttk.Button(buttons, text="Use layer", command=self._accept).pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _media_root(self, ask: bool) -> Path | None:
        config = load_config()
        root = story_media_dir(config)
        if root and root.is_dir():
            return root
        if not ask:
            return None
        messagebox.showinfo(
            "Story audio folder", "First choose the main folder containing your story audio.\n\nYou will choose the individual file next.",
            parent=self,
        )
        selected = filedialog.askdirectory(title="Step 1 of 2 — Choose the main story-audio folder", parent=self)
        if not selected:
            return None
        root = Path(selected).resolve()
        config["external_story_media_dir"] = str(root)
        save_config(config)
        return root

    def _browse_audio(self) -> None:
        root = self._media_root(True)
        if root is None:
            return
        selected = filedialog.askopenfilename(
            title="Step 2 of 2 — Choose the audio file", initialdir=str(root), parent=self,
            filetypes=(("Audio files", ("*.mp3", "*.wav", "*.wma", "*.m4a", "*.aac", "*.flac", "*.ogg")), ("All files", "*.*")),
        )
        if not selected:
            return
        path = Path(selected).resolve()
        if not path.is_relative_to(root):
            messagebox.showerror("Audio outside media folder", f"Choose a file inside:\n{root}", parent=self)
            return
        self.vars["asset"].set(path.relative_to(root).as_posix())
        self.vars["type"].set("audio")

    def _preview(self) -> None:
        try:
            root = self._media_root(False)
            asset = self.vars["asset"].get().strip()
            if root is None or not asset:
                raise ValueError("Choose an audio file first")
            path = (root / asset).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"Audio file not found: {path}")
            should_loop = self.vars["playback_mode"].get() != _DETECTION_PLAYBACK and bool(self.vars["loop"].get())
            self.preview.play_file(path, self.vars["maximum"].get() / 100, should_loop)
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Cannot preview layer", str(exc), parent=self)

    def _level_changed(self, value: Any) -> None:
        percent = max(0.0, min(100.0, float(value)))
        self.level_label.set(f"{round(percent):d}%")
        self.preview.set_volume(percent / 100)

    def _accept(self) -> None:
        try:
            layer_id = _slug(self.vars["id"].get(), "layer")
            kind = self.vars["type"].get()
            maximum = self.vars["maximum"].get() / 100
            start_delay = float(self.vars["start_delay"].get())
            stop_delay = float(self.vars["stop_delay"].get())
            volume_transition = float(self.vars["volume_transition"].get())
            if start_delay < 0 or stop_delay < 0 or volume_transition < 0:
                raise ValueError("Delays and volume smoothing cannot be negative")
            layer: dict[str, Any] = {"id": layer_id, "type": kind}
            if kind == "audio":
                asset = self.vars["asset"].get().strip()
                if not asset:
                    raise ValueError("Choose an audio file")
                layer.update({
                    "asset": asset, "volume": round(maximum, 4), "loop": bool(self.vars["loop"].get()),
                    "volume_transition_seconds": volume_transition,
                })
                if self.vars["playback_mode"].get() == _DETECTION_PLAYBACK:
                    outer_gap = float(self.vars["scanner_outer_gap"].get())
                    core_gap = float(self.vars["scanner_core_gap"].get())
                    gap_exponent = float(self.vars["scanner_gap_exponent"].get())
                    momentum_step = float(self.vars["scanner_momentum_step"].get())
                    momentum_limit = float(self.vars["scanner_momentum_limit"].get())
                    if outer_gap < 0 or core_gap < 0 or core_gap > outer_gap or gap_exponent <= 0:
                        raise ValueError("Scanner gaps must be non-negative, core pause cannot exceed outer pause, and frequency curve must be greater than zero")
                    if not 0 <= momentum_step <= momentum_limit <= 25:
                        raise ValueError("Scanner motion change and maximum prediction must satisfy 0% ≤ change ≤ maximum ≤ 25%")
                    layer.update({
                        "playback_mode": "scanner_pulse", "loop": False,
                        "scanner_pulse": {
                            "outer_gap_seconds": outer_gap,
                            "core_gap_seconds": core_gap,
                            "exponent": gap_exponent,
                            "momentum_step_percent": momentum_step,
                            "momentum_limit_percent": momentum_limit,
                        },
                    })
            else:
                cue = self.vars["cue"].get().strip()
                if not cue:
                    raise ValueError("Enter a visual cue")
                layer.update({"cue": cue, "intensity": round(maximum, 4)})
            if start_delay:
                layer["start_delay_seconds"] = start_delay
            if stop_delay:
                layer["stop_delay_seconds"] = stop_delay
            curve = self.vars["curve"].get()
            if curve != "constant":
                start = float(self.vars["start_percent"].get())
                minimum = float(self.vars["minimum"].get()) / 100
                exponent = float(self.vars["exponent"].get())
                if not 0 <= start < 100 or not 0 <= minimum <= 1 or exponent <= 0:
                    raise ValueError("Depth start, minimum level or exponent is outside its valid range")
                layer["depth_response"] = {
                    "curve": curve, "start_percent": start, "minimum": round(minimum, 4), "exponent": exponent,
                }
                if self.vars["silent_before_start"].get():
                    layer["depth_response"]["silent_before_start"] = True
            self.result = layer
            self.preview.stop()
            self.destroy()
        except ValueError as exc:
            messagebox.showerror("Invalid environment layer", str(exc), parent=self)

    def _close(self) -> None:
        self.preview.stop(); self.destroy()


class EnvironmentDialog(tk.Toplevel):
    def __init__(
        self, parent: tk.Misc, initial: dict[str, Any] | None = None,
        template: str = "surface", control_bindings: dict[str, Any] | None = None,
        time_gates: list[str] | None = None, layer_options: list[str] | None = None,
        wind_options: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("Environment & zone designer")
        self.geometry("820x720")
        self.minsize(700, 620)
        self.transient(parent); self.grab_set()
        self.result: dict[str, Any] | None = None
        self.controls = {**DEFAULT_CONTROL_BINDINGS, **dict(control_bindings or {})}
        self.time_gates = list(time_gates or [])
        self.layer_options = list(dict.fromkeys(layer_options or []))
        self.wind_options = list(dict.fromkeys(wind_options or []))
        self.value = copy.deepcopy(initial or default_environment(template))
        location = self.value.get("location") if isinstance(self.value.get("location"), dict) else {}
        self.entry_when = copy.deepcopy(self.value.get("entry_when", {"always": True}))
        self.exit_when = copy.deepcopy(self.value.get("exit_when")) if isinstance(self.value.get("exit_when"), dict) else None
        self.layers = copy.deepcopy(self.value.get("layers", []))
        self.layer_adjustments = copy.deepcopy(self.value.get("layer_adjustments", []))
        active_vehicles = self.value.get("active_vehicles")
        if not isinstance(active_vehicles, list):
            active_vehicles = ["on_foot", "srv", "ship", "fighter"]
        self.vars = {
            "id": tk.StringVar(value=str(self.value.get("id", "new_environment"))),
            "title": tk.StringVar(value=str(self.value.get("title", "New environment"))),
            "enabled": tk.BooleanVar(value=bool(self.value.get("enabled", True))),
            "rearm": tk.StringVar(value=str(self.value.get("rearm", "after_exit"))),
            "exit_hold_seconds": tk.StringVar(value=str(self.value.get("exit_hold_seconds", 0))),
            "control_toggle": tk.BooleanVar(value=bool(self.value.get("requires_control"))),
            "toggle_signal": tk.StringVar(value=str(self.value.get("requires_control", "control.scanner"))),
            "time_gate_enabled": tk.BooleanVar(value=bool(self.value.get("time_gate"))),
            "time_gate": tk.StringVar(value=str(self.value.get("time_gate", self.time_gates[0] if self.time_gates else ""))),
            "time_gate_state": tk.StringVar(value=str(self.value.get("time_gate_state", "open"))),
            "vehicle_on_foot": tk.BooleanVar(value="on_foot" in active_vehicles),
            "vehicle_srv": tk.BooleanVar(value="srv" in active_vehicles),
            "vehicle_ship": tk.BooleanVar(value="ship" in active_vehicles),
            "vehicle_fighter": tk.BooleanVar(value="fighter" in active_vehicles),
        }
        for key in (
            "system", "body", "station", "settlement", "latitude", "longitude", "entry_radius_m",
            "exit_radius_m", "core_radius_m", "min_altitude_m", "max_altitude_m",
        ):
            self.vars[key] = tk.StringVar(value=str(location.get(key, "")))
        body_radius = location.get("body_radius_m")
        self.vars["body_radius_km"] = tk.StringVar(value="" if body_radius in (None, "") else str(float(body_radius) / 1000))
        self.capture_status = tk.StringVar(value="No live location captured in this dialog.")

        outer = ttk.Frame(self, padding=12); outer.pack(fill="both", expand=True)
        identity = ttk.Frame(outer); identity.pack(fill="x")
        ttk.Label(identity, text="Name").grid(row=0, column=0, sticky="w", padx=3)
        ttk.Entry(identity, textvariable=self.vars["title"]).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Label(identity, text="ID").grid(row=1, column=0, sticky="w", padx=3, pady=4)
        ttk.Entry(identity, textvariable=self.vars["id"]).grid(row=1, column=1, sticky="ew", padx=3, pady=4)
        ttk.Checkbutton(identity, text="Environment enabled", variable=self.vars["enabled"]).grid(row=0, column=2, rowspan=2, padx=8)
        identity.columnconfigure(1, weight=1)

        tabs = ttk.Notebook(outer); tabs.pack(fill="both", expand=True, pady=(8, 0))
        location_tab = ttk.Frame(tabs, padding=10); lifecycle_tab = ttk.Frame(tabs, padding=10); layers_tab = ttk.Frame(tabs, padding=10)
        tabs.add(location_tab, text="Where / zone depth")
        tabs.add(lifecycle_tab, text="Begin & end")
        tabs.add(layers_tab, text="Sound, visual & wind layers")
        self._build_location_tab(location_tab)
        self._build_lifecycle_tab(lifecycle_tab)
        self._build_layers_tab(layers_tab)

        buttons = ttk.Frame(outer); buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Use environment", command=self._accept).pack(side="right", padx=6)

    def _build_location_tab(self, tab: ttk.Frame) -> None:
        ttk.Label(
            tab, text="Named fields may be used alone. Latitude/longitude adds continuous 0–100% zone depth.",
            foreground="#666666", wraplength=720,
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 7))
        row = 1
        for key, label in (("system", "Star system"), ("body", "Body"), ("station", "Station / instance"), ("settlement", "Settlement")):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(tab, textvariable=self.vars[key]).grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)
            row += 1
        ttk.Separator(tab).grid(row=row, column=0, columnspan=4, sticky="ew", pady=8); row += 1
        fields = (
            ("latitude", "Latitude"), ("longitude", "Longitude"),
            ("entry_radius_m", "Entry radius (m)"), ("exit_radius_m", "Exit radius (m)"),
            ("core_radius_m", "100% core radius (m)"), ("body_radius_km", "Body radius (km)"),
            ("min_altitude_m", "Minimum altitude"), ("max_altitude_m", "Maximum altitude"),
        )
        for index, (key, label) in enumerate(fields):
            grid_row, column = row + index // 2, (index % 2) * 2
            ttk.Label(tab, text=label).grid(row=grid_row, column=column, sticky="w", pady=3, padx=(0, 4))
            ttk.Entry(tab, textvariable=self.vars[key], width=18).grid(row=grid_row, column=column + 1, sticky="ew", pady=3, padx=(0, 8))
        row += 4
        modes = ttk.LabelFrame(tab, text="Where this ambience is allowed (all selected by default)", padding=5)
        modes.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(7, 2))
        for column, (key, label) in enumerate((("vehicle_on_foot", "On foot"), ("vehicle_srv", "SRV"), ("vehicle_ship", "Main ship"), ("vehicle_fighter", "Fighter"))):
            ttk.Checkbutton(modes, text=label, variable=self.vars[key]).grid(row=0, column=column, sticky="w", padx=7)
        row += 1
        ttk.Button(tab, text="Capture current Elite location", command=self._capture_live).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(9, 3))
        ttk.Button(tab, text="Clear location fields", command=self._clear_location).grid(row=row, column=2, columnspan=2, sticky="ew", pady=(9, 3))
        row += 1
        ttk.Label(tab, textvariable=self.capture_status, foreground="#285f45", wraplength=700).grid(row=row, column=0, columnspan=4, sticky="w")
        for column in (1, 3):
            tab.columnconfigure(column, weight=1)

    def _build_lifecycle_tab(self, tab: ttk.Frame) -> None:
        toggle = ttk.LabelFrame(tab, text="Optional persistent control dependency", padding=8)
        toggle.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(
            toggle, text="This zone works only while this global control is ON",
            variable=self.vars["control_toggle"],
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(toggle, text="Control").grid(row=1, column=0, sticky="w", padx=3, pady=4)
        control_options = [f"control.{name}" for name in self.controls]
        ttk.Combobox(
            toggle, textvariable=self.vars["toggle_signal"], values=control_options,
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", padx=3, pady=4)
        ttk.Button(toggle, text="Use global scanner (CTRL+S)", command=self._make_scanner_zone).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 2),
        )
        ttk.Label(
            toggle,
            text="The global state persists across ship, SRV and on-foot changes. Location still decides where this particular zone can be heard.",
            foreground="#666666", wraplength=680,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 0))
        toggle.columnconfigure(1, weight=1)
        gate = ttk.LabelFrame(tab, text="Optional planetary time gate", padding=8)
        gate.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(gate, text="Allow this environment only during a named gate state", variable=self.vars["time_gate_enabled"]).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(gate, text="Gate").grid(row=1, column=0, sticky="w", padx=3, pady=4)
        ttk.Combobox(gate, textvariable=self.vars["time_gate"], values=self.time_gates, state="normal").grid(row=1, column=1, sticky="ew", padx=3, pady=4)
        ttk.Label(gate, text="When").grid(row=2, column=0, sticky="w", padx=3, pady=4)
        ttk.Combobox(gate, textvariable=self.vars["time_gate_state"], values=("open", "closed"), state="readonly").grid(row=2, column=1, sticky="ew", padx=3, pady=4)
        gate.columnconfigure(1, weight=1)
        ttk.Label(tab, text="When this environment begins", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.entry_summary = tk.StringVar(value=predicate_summary(self.entry_when))
        ttk.Label(tab, textvariable=self.entry_summary, foreground="#285f45", wraplength=720).pack(anchor="w", pady=4)
        ttk.Button(tab, text="Build beginning condition…", command=self._build_entry).pack(fill="x")
        ttk.Separator(tab).pack(fill="x", pady=14)
        ttk.Label(tab, text="When this environment ends", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.exit_summary = tk.StringVar(value=predicate_summary(self.exit_when) if self.exit_when else "Geographic/named-location exit only")
        ttk.Label(tab, textvariable=self.exit_summary, foreground="#285f45", wraplength=720).pack(anchor="w", pady=4)
        row = ttk.Frame(tab); row.pack(fill="x")
        ttk.Button(row, text="Build additional ending condition…", command=self._build_exit).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(row, text="Clear", command=self._clear_exit).pack(side="right", padx=(3, 0))
        ttk.Separator(tab).pack(fill="x", pady=14)
        ttk.Label(tab, text="After ending, allow it to begin again").pack(anchor="w")
        ttk.Combobox(
            tab, textvariable=self.vars["rearm"], state="readonly",
            values=("after_exit", "immediate", "never"),
        ).pack(fill="x", pady=4)
        ttk.Label(tab, text="Geographic exit confirmation delay (seconds)").pack(anchor="w", pady=(7, 2))
        ttk.Entry(tab, textvariable=self.vars["exit_hold_seconds"]).pack(fill="x")
        ttk.Label(
            tab,
            text="After exit is safest: a story-triggered stop remains suppressed while the player is still inside, then rearms after they leave.",
            foreground="#666666", wraplength=700,
        ).pack(anchor="w", pady=4)

    def _build_layers_tab(self, tab: ttk.Frame) -> None:
        ttk.Label(
            tab, text="Every layer has its own player. Dialogue and other environments continue simultaneously.",
            foreground="#666666", wraplength=700,
        ).pack(anchor="w", pady=(0, 6))
        self.layer_list = tk.Listbox(tab, height=7, font=("Segoe UI", 9), activestyle="dotbox")
        self.layer_list.pack(fill="both", expand=True)
        self.layer_list.bind("<Double-Button-1>", lambda _event: self._edit_layer())
        add = ttk.Frame(tab); add.pack(fill="x", pady=6)
        ttk.Button(add, text="+ Sound layer", command=lambda: self._add_layer("audio")).pack(side="left", padx=2)
        ttk.Button(add, text="+ Visual layer", command=lambda: self._add_layer("visual")).pack(side="left", padx=2)
        ttk.Button(add, text="+ Wind layer", command=lambda: self._add_layer("wind")).pack(side="left", padx=2)
        ttk.Button(add, text="Edit", command=self._edit_layer).pack(side="left", padx=8)
        ttk.Button(add, text="Delete", command=self._delete_layer).pack(side="left", padx=2)
        ttk.Button(add, text="Move up", command=lambda: self._move_layer(-1)).pack(side="right", padx=2)
        ttk.Button(add, text="Move down", command=lambda: self._move_layer(1)).pack(side="right", padx=2)
        ttk.Separator(tab).pack(fill="x", pady=8)
        ttk.Label(tab, text="Adjust existing audio or wind while this environment is active", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(
            tab, text="These rules are durable: relogs and platform restarts reconstruct the correct mix automatically.",
            foreground="#666666", wraplength=700,
        ).pack(anchor="w", pady=(1, 4))
        self.adjustment_list = tk.Listbox(tab, height=4, font=("Segoe UI", 9), activestyle="dotbox")
        self.adjustment_list.pack(fill="x")
        self.adjustment_list.bind("<Double-Button-1>", lambda _event: self._edit_adjustment())
        adjust = ttk.Frame(tab); adjust.pack(fill="x", pady=5)
        ttk.Button(adjust, text="+ Layer adjustment", command=self._add_adjustment).pack(side="left", padx=2)
        ttk.Button(adjust, text="+ Modify wind", command=self._add_wind_pause).pack(side="left", padx=2)
        ttk.Button(adjust, text="Edit", command=self._edit_adjustment).pack(side="left", padx=8)
        ttk.Button(adjust, text="Delete", command=self._delete_adjustment).pack(side="left", padx=2)
        self._refresh_layers()
        self._refresh_adjustments()

    def _capture_live(self) -> None:
        try:
            location = read_live_location()
            self._apply_captured_location(location)
        except ValueError as exc:
            messagebox.showerror("Cannot capture Elite location", str(exc), parent=self)

    def _apply_captured_location(self, location: dict[str, Any]) -> None:
        for key in ("system", "body", "station", "settlement", "latitude", "longitude"):
            if key in location:
                self.vars[key].set(str(location[key]))
        if location.get("body_radius_m") is not None:
            self.vars["body_radius_km"].set(str(float(location["body_radius_m"]) / 1000))
        altitude = f" · altitude {location['altitude']} m" if location.get("altitude") is not None else ""
        self.capture_status.set(
            f"Captured current Elite position: {location.get('system', '')} / {location.get('body', '')} at {location.get('captured_at', '')}{altitude}"
        )

    def _clear_location(self) -> None:
        for key in (
            "system", "body", "station", "settlement", "latitude", "longitude", "entry_radius_m",
            "exit_radius_m", "core_radius_m", "body_radius_km", "min_altitude_m", "max_altitude_m",
        ):
            self.vars[key].set("")
        self.capture_status.set("Location fields cleared; this environment will be bounded by events/story conditions.")

    def _build_entry(self) -> None:
        dialog = PredicateBuilderDialog(self, self.entry_when, self.controls, self.time_gates); self.wait_window(dialog)
        if dialog.result:
            self.entry_when = dialog.result; self.entry_summary.set(predicate_summary(dialog.result))

    def _make_scanner_zone(self) -> None:
        self.vars["control_toggle"].set(True)
        self.vars["toggle_signal"].set("control.scanner")
        self.vars["rearm"].set("immediate")
        self.entry_when = {"always": True}
        self.exit_when = None
        self.entry_summary.set(predicate_summary(self.entry_when))
        self.exit_summary.set("Geographic/named-location exit only")

    def _build_exit(self) -> None:
        dialog = PredicateBuilderDialog(self, self.exit_when or {"always": True}, self.controls, self.time_gates); self.wait_window(dialog)
        if dialog.result:
            self.exit_when = dialog.result; self.exit_summary.set(predicate_summary(dialog.result))

    def _clear_exit(self) -> None:
        self.exit_when = None; self.exit_summary.set("Geographic/named-location exit only")

    def _refresh_layers(self, selected: int | None = None) -> None:
        self.layer_list.delete(0, "end")
        for layer in self.layers:
            self.layer_list.insert("end", environment_layer_summary(layer))
        if self.layers:
            index = max(0, min(len(self.layers) - 1, selected if selected is not None else 0))
            self.layer_list.selection_set(index); self.layer_list.see(index)

    def _selected_layer(self) -> int | None:
        selected = self.layer_list.curselection()
        return int(selected[0]) if selected else None

    def _add_layer(self, kind: str) -> None:
        initial = (
            default_planetary_glide_audio_layer()
            if kind == "audio" and self.entry_when == {"signal": "status.glide.entered"}
            else None
        )
        dialog = WindLayerDialog(self, initial) if kind == "wind" else EnvironmentLayerDialog(self, initial, force_type=kind)
        self.wait_window(dialog)
        if dialog.result:
            self.layers.append(dialog.result); self._refresh_layers(len(self.layers) - 1)

    def _edit_layer(self) -> None:
        index = self._selected_layer()
        if index is None:
            return
        dialog = WindLayerDialog(self, self.layers[index]) if self.layers[index].get("type") == "wind" else EnvironmentLayerDialog(self, self.layers[index])
        self.wait_window(dialog)
        if dialog.result:
            self.layers[index] = dialog.result; self._refresh_layers(index)

    def _delete_layer(self) -> None:
        index = self._selected_layer()
        if index is not None:
            del self.layers[index]; self._refresh_layers(min(index, len(self.layers) - 1))

    def _move_layer(self, amount: int) -> None:
        index = self._selected_layer()
        if index is None or not 0 <= index + amount < len(self.layers):
            return
        target = index + amount
        self.layers[index], self.layers[target] = self.layers[target], self.layers[index]
        self._refresh_layers(target)

    def _refresh_adjustments(self, selected: int | None = None) -> None:
        self.adjustment_list.delete(0, "end")
        for adjustment in self.layer_adjustments:
            self.adjustment_list.insert("end", layer_adjustment_summary(adjustment))
        if self.layer_adjustments:
            index = max(0, min(len(self.layer_adjustments) - 1, selected if selected is not None else 0))
            self.adjustment_list.selection_set(index); self.adjustment_list.see(index)

    def _selected_adjustment(self) -> int | None:
        selected = self.adjustment_list.curselection()
        return int(selected[0]) if selected else None

    def _add_wind_pause(self) -> None:
        if not self.wind_options:
            messagebox.showinfo("Modify wind", "Save a wind layer in the graph first, then select it here.", parent=self)
            return
        dialog=WindPauseDialog(self,self.wind_options);self.wait_window(dialog)
        if dialog.result:
            self.layer_adjustments.append(dialog.result)
            self._refresh_adjustments(len(self.layer_adjustments)-1)

    def _add_adjustment(self) -> None:
        dialog = LayerAdjustmentDialog(self, self.layer_options); self.wait_window(dialog)
        if dialog.result:
            self.layer_adjustments.append(dialog.result)
            self._refresh_adjustments(len(self.layer_adjustments) - 1)

    def _edit_adjustment(self) -> None:
        index = self._selected_adjustment()
        if index is None:
            return
        value=self.layer_adjustments[index]
        dialog = WindPauseDialog(self,self.wind_options,value) if value.get('action')=='pause_wind' else LayerAdjustmentDialog(self, self.layer_options, value)
        self.wait_window(dialog)
        if dialog.result:
            self.layer_adjustments[index] = dialog.result
            self._refresh_adjustments(index)

    def _delete_adjustment(self) -> None:
        index = self._selected_adjustment()
        if index is not None:
            del self.layer_adjustments[index]
            self._refresh_adjustments(min(index, len(self.layer_adjustments) - 1))

    def _location(self) -> dict[str, Any] | None:
        location: dict[str, Any] = {}
        for key in ("system", "body", "station", "settlement"):
            value = self.vars[key].get().strip()
            if value:
                location[key] = value
        latitude, longitude = self.vars["latitude"].get().strip(), self.vars["longitude"].get().strip()
        if latitude or longitude:
            if not latitude or not longitude:
                raise ValueError("Both latitude and longitude are required")
            location["latitude"] = float(latitude); location["longitude"] = float(longitude)
            for key, default in (("entry_radius_m", None), ("exit_radius_m", None), ("core_radius_m", 0)):
                raw = self.vars[key].get().strip()
                if not raw and default is None:
                    raise ValueError("Entry and exit radii are required for a coordinate zone")
                location[key] = float(raw or default)
            body_radius = self.vars["body_radius_km"].get().strip()
            if body_radius:
                location["body_radius_m"] = float(body_radius) * 1000
        for key in ("min_altitude_m", "max_altitude_m"):
            raw = self.vars[key].get().strip()
            if raw:
                location[key] = float(raw)
        return location or None

    def _accept(self) -> None:
        try:
            zone: dict[str, Any] = {
                "id": _slug(self.vars["id"].get(), "environment"),
                "title": self.vars["title"].get().strip(),
                "enabled": bool(self.vars["enabled"].get()),
                "entry_when": copy.deepcopy(self.entry_when),
                "rearm": self.vars["rearm"].get(),
                "exit_hold_seconds": float(self.vars["exit_hold_seconds"].get() or 0),
                "layers": copy.deepcopy(self.layers),
            }
            if self.layer_adjustments:
                zone["layer_adjustments"] = copy.deepcopy(self.layer_adjustments)
            if self.value.get("zone_type"):
                zone["zone_type"] = self.value["zone_type"]
            if bool(self.vars["control_toggle"].get()):
                toggle_signal = self.vars["toggle_signal"].get().strip()
                if not toggle_signal.startswith("control."):
                    raise ValueError("Choose a configured control signal for this toggle zone")
                zone["requires_control"] = toggle_signal
                zone["rearm"] = "immediate"
            if bool(self.vars["time_gate_enabled"].get()):
                gate_id = self.vars["time_gate"].get().strip()
                if not gate_id:
                    raise ValueError("Choose a named time gate")
                zone["time_gate"] = gate_id
                zone["time_gate_state"] = self.vars["time_gate_state"].get()
            selected_vehicles = [
                value for key, value in (("vehicle_on_foot", "on_foot"), ("vehicle_srv", "srv"), ("vehicle_ship", "ship"), ("vehicle_fighter", "fighter"))
                if bool(self.vars[key].get())
            ]
            if not selected_vehicles:
                raise ValueError("Select at least one allowed player/vehicle mode")
            if len(selected_vehicles) != 4:
                zone["active_vehicles"] = selected_vehicles
            if not zone["title"]:
                raise ValueError("Environment name cannot be empty")
            location = self._location()
            if self.entry_when == {"signal": "status.glide.entered"}:
                if not location or not location.get("system") or not location.get("body"):
                    raise ValueError("A planetary glide sound requires both a star system and body")
            if location:
                zone["location"] = location
            if self.exit_when:
                zone["exit_when"] = copy.deepcopy(self.exit_when)
            probe = {
                "schema": "platform.story-graph.v1", "id": "probe", "version": "1", "title": "Probe",
                "start": "start", "nodes": {"start": {"kind": "start"}}, "edges": [], "environments": [zone],
            }
            synthetic: dict[str, dict[str, str]] = {}
            own_layers = {str(layer.get("id")) for layer in zone.get("layers", []) if isinstance(layer, dict)}
            for adjustment in zone.get("layer_adjustments", []):
                parts = str(adjustment.get("target_layer", "")).split(".", 2)
                if len(parts) != 3 or (parts[1] == zone["id"] and parts[2] in own_layers):
                    continue
                synthetic.setdefault(parts[1], {})[parts[2]] = "wind" if adjustment.get("action") == "pause_wind" else "audio"
            for target_zone, target_layers in synthetic.items():
                probe["environments"].append({
                    "id": target_zone, "title": target_zone,
                    "location": {"body": "probe_body"},
                    "layers": [
                        ({"id": layer_id, "type": "wind"} if target_layers[layer_id] == "wind"
                         else {"id": layer_id, "type": "audio", "asset": "probe.mp3"})
                        for layer_id in sorted(target_layers)
                    ],
                })
            if zone.get("time_gate"):
                probe.update({
                    "solar_clocks": [{"id": "probe_clock", "solar_day_seconds": 86400, "direction": 1, "anchor": {"at_utc": "2000-01-01T00:00:00Z", "longitude": 0, "local_hour": 0}}],
                    "solar_sites": [{"id": "probe_site", "clock": "probe_clock", "latitude": 0, "longitude": 0}],
                    "time_gates": [{"id": zone["time_gate"], "site": "probe_site", "open_local_hour": 0, "close_local_hour": 12}],
                })
            errors = [error for error in validate_graph(probe) if error.startswith("environment")]
            if errors:
                raise ValueError("\n".join(errors))
            self.result = zone; self.destroy()
        except ValueError as exc:
            messagebox.showerror("Invalid environment", str(exc), parent=self)
