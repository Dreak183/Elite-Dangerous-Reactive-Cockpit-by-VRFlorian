from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from .channels import JsonlTail
from .config import APP_DIR, load_config, resolve_path, story_media_dir
from .engine import utc_now
from .instance_lock import AlreadyRunningError, InstanceLock
from .storage import append_jsonl, atomic_write_json


class AudioPlayer:
    def __init__(self, alias: str = "storyplatformaudio") -> None:
        self.alias = alias
        self.winmm = None
        self.process: subprocess.Popen[str] | None = None
        self.simulated_playing = False
        self.current_volume = 1.0
        self.volume_ramp: tuple[float, float, float, float] | None = None
        self.stop_after_ramp = False
        if os.name == "nt":
            self.winmm = ctypes.windll.winmm
            self.winmm.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
            self.winmm.mciSendStringW.restype = ctypes.c_uint

    def system_sound(self, name: str) -> None:
        if os.name != "nt":
            print(f"[AUDIO SIMULATED] {name}", flush=True)
            return
        import winsound

        mapping = {
            "asterisk": winsound.MB_ICONASTERISK, "exclamation": winsound.MB_ICONEXCLAMATION,
            "hand": winsound.MB_ICONHAND, "question": winsound.MB_ICONQUESTION, "ok": winsound.MB_OK,
        }
        winsound.MessageBeep(mapping.get(name.casefold(), winsound.MB_OK))

    def play_file(self, path: Path, volume: float = 1.0, loop: bool = False) -> None:
        self.stop()
        volume = max(0.0, min(1.0, float(volume)))
        self.current_volume = volume
        if os.name != "nt":
            print(f"[AUDIO SIMULATED] {path}" + (" [loop]" if loop else ""), flush=True)
            self.simulated_playing = True
            return
        try:
            self.process = subprocess.Popen(
                self._powershell_args(path, volume, loop),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # Catch immediate launch/codec failures and retain MCI as a fallback.
            time.sleep(0.08)
            if self.process.poll() is None:
                return
            self.process = None
        except OSError:
            self.process = None
        self._play_file_mci(path, volume, loop)

    @staticmethod
    def _powershell_args(path: Path, volume: float, loop: bool) -> list[str]:
        path64 = base64.b64encode(str(path).encode("utf-8")).decode("ascii")
        loop_literal = "$true" if loop else "$false"
        script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{path64}'))
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open([Uri]::new($path))
$player.Volume = {max(0.0, min(1.0, float(volume))):.6f}
$player.Play()
$loopPlayback = {loop_literal}
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (-not $player.NaturalDuration.HasTimeSpan -and [DateTime]::UtcNow -lt $deadline) {{
    Start-Sleep -Milliseconds 40
}}
if (-not $player.NaturalDuration.HasTimeSpan) {{ throw 'Windows could not open the audio file.' }}
$duration = $player.NaturalDuration.TimeSpan
$stdinStream = [Console]::OpenStandardInput()
$inputBuffer = New-Object byte[] 4096
$inputText = ''
$readTask = $stdinStream.ReadAsync($inputBuffer, 0, $inputBuffer.Length)
$stopRequested = $false
$rampActive = $false
$stopAfterRamp = $false
$rampStart = [DateTime]::UtcNow
$rampSeconds = 0.0
$rampFrom = $player.Volume
$rampTo = $player.Volume
$playStarted = [DateTime]::UtcNow
$expectedEnd = $playStarted.Add($duration)
$lastPosition = [TimeSpan]::Zero
$lastProgress = $playStarted
$recoveryAttempts = 0
while ($true) {{
    if ($readTask.IsCompleted) {{
        $byteCount = $readTask.Result
        if ($byteCount -le 0) {{
            $stopRequested = $true
        }} else {{
            $inputText += [Text.Encoding]::UTF8.GetString($inputBuffer, 0, $byteCount)
            $newline = $inputText.IndexOf("`n")
            while ($newline -ge 0) {{
                $line = $inputText.Substring(0, $newline).TrimEnd("`r")
                $inputText = $inputText.Substring($newline + 1)
                if ($line -eq 'stop') {{
                    $stopRequested = $true
                    break
                }}
                if ($line.StartsWith('volume ')) {{
                    $stopAfterRamp = $false
                    $parts = $line -split '\\s+'
                    $rampTo = [double]::Parse($parts[1], [Globalization.CultureInfo]::InvariantCulture)
                    $rampSeconds = if ($parts.Length -ge 3) {{
                        [double]::Parse($parts[2], [Globalization.CultureInfo]::InvariantCulture)
                    }} else {{ 0.0 }}
                    if ($rampSeconds -le 0.0) {{
                        $player.Volume = $rampTo
                        $rampActive = $false
                    }} else {{
                        $rampFrom = $player.Volume
                        $rampStart = [DateTime]::UtcNow
                        $rampActive = $true
                    }}
                }}
                elseif ($line.StartsWith('fade-stop ')) {{
                    $rampFrom = $player.Volume
                    $rampTo = 0.0
                    $rampSeconds = [double]::Parse($line.Substring(10), [Globalization.CultureInfo]::InvariantCulture)
                    if ($rampSeconds -le 0.0) {{
                        $stopRequested = $true
                    }} else {{
                        $rampStart = [DateTime]::UtcNow
                        $rampActive = $true
                        $stopAfterRamp = $true
                    }}
                }}
                $newline = $inputText.IndexOf("`n")
            }}
        }}
        if (-not $stopRequested) {{
            $readTask = $stdinStream.ReadAsync($inputBuffer, 0, $inputBuffer.Length)
        }}
    }}
    if ($stopRequested) {{ break }}
    if ($rampActive) {{
        $ratio = ([DateTime]::UtcNow - $rampStart).TotalSeconds / $rampSeconds
        if ($ratio -ge 1.0) {{
            $player.Volume = $rampTo
            $rampActive = $false
            if ($stopAfterRamp) {{ break }}
        }} else {{
            $player.Volume = $rampFrom + (($rampTo - $rampFrom) * [Math]::Max(0.0, $ratio))
        }}
    }}
    $now = [DateTime]::UtcNow
    $position = $player.Position
    if (($position - $lastPosition).TotalMilliseconds -gt 5) {{
        $lastPosition = $position
        $lastProgress = $now
        $expectedEnd = $now.Add($duration - $position)
        $recoveryAttempts = 0
    }}
    $atBoundary = $position -gt [TimeSpan]::Zero -and (($duration - $position).TotalMilliseconds -le 40)
    if ($atBoundary -or $now -ge $expectedEnd) {{
        if ($loopPlayback) {{
            $player.Stop()
            $player.Position = [TimeSpan]::Zero
            $player.Play()
            $playStarted = $now
            $expectedEnd = $now.Add($duration)
            $lastPosition = [TimeSpan]::Zero
            $lastProgress = $now
        }} else {{ break }}
    }}
    elseif ($loopPlayback -and ($now - $playStarted).TotalSeconds -ge 3.0 -and ($now - $lastProgress).TotalSeconds -ge 3.0) {{
        # MediaPlayer can remain alive after playback silently stalls. Recover here;
        # exit after repeated failures so the Python watchdog can replace the process.
        $recoveryAttempts += 1
        if ($recoveryAttempts -gt 2) {{ throw 'Windows audio playback stalled repeatedly.' }}
        $player.Stop()
        $player.Position = [TimeSpan]::Zero
        $player.Play()
        $playStarted = $now
        $expectedEnd = $now.Add($duration)
        $lastPosition = [TimeSpan]::Zero
        $lastProgress = $now
    }}
    Start-Sleep -Milliseconds 20
}}
$player.Close()
"""
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded]

    def _play_file_mci(self, path: Path, volume: float, loop: bool) -> None:
        winmm = self.winmm

        def command(text: str) -> None:
            error = winmm.mciSendStringW(text, None, 0, None)
            if error:
                raise RuntimeError(f"Windows MCI error {error}: {text}")

        winmm.mciSendStringW(f"close {self.alias}", None, 0, None)
        command(f'open "{path}" alias {self.alias}')
        command(f"setaudio {self.alias} volume to {max(0, min(1000, int(volume * 1000)))}")
        command(f"play {self.alias}" + (" repeat" if loop else ""))

    def set_volume(self, volume: float, transition_seconds: float = 0.0) -> None:
        volume = max(0.0, min(1.0, float(volume)))
        transition_seconds = max(0.0, float(transition_seconds))
        if self.process is not None and self.process.poll() is None and self.process.stdin is not None:
            try:
                suffix = f" {transition_seconds:.3f}" if transition_seconds > 0 else ""
                self.process.stdin.write(f"volume {volume:.6f}{suffix}\n")
                self.process.stdin.flush()
                return
            except (BrokenPipeError, OSError):
                self.process = None
        self._advance_volume_ramp()
        self.stop_after_ramp = False
        if transition_seconds > 0:
            self.volume_ramp = (time.monotonic(), transition_seconds, self.current_volume, volume)
            return
        self.volume_ramp = None
        self.current_volume = volume
        if self.winmm is not None:
            self.winmm.mciSendStringW(
                f"setaudio {self.alias} volume to {int(volume * 1000)}", None, 0, None,
            )

    def fade_out(self, transition_seconds: float) -> None:
        duration = max(0.0, float(transition_seconds))
        if duration <= 0:
            self.stop()
            return
        if self.process is not None and self.process.poll() is None and self.process.stdin is not None:
            try:
                self.process.stdin.write(f"fade-stop {duration:.3f}\n")
                self.process.stdin.flush()
                return
            except (BrokenPipeError, OSError):
                self.process = None
        self.set_volume(0.0, duration)
        self.stop_after_ramp = True

    def _advance_volume_ramp(self) -> None:
        if self.volume_ramp is None:
            return
        started, duration, initial, target = self.volume_ramp
        ratio = max(0.0, min(1.0, (time.monotonic() - started) / duration))
        self.current_volume = initial + (target - initial) * ratio
        if self.winmm is not None:
            self.winmm.mciSendStringW(
                f"setaudio {self.alias} volume to {int(self.current_volume * 1000)}", None, 0, None,
            )
        if ratio >= 1.0:
            self.volume_ramp = None
            if self.stop_after_ramp:
                self.stop()

    def stop(self) -> None:
        self.simulated_playing = False
        self.volume_ramp = None
        self.stop_after_ramp = False
        if self.process is not None:
            process, self.process = self.process, None
            if process.poll() is None:
                try:
                    if process.stdin is not None:
                        process.stdin.write("stop\n")
                        process.stdin.flush()
                    process.wait(timeout=0.5)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    process.terminate()
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
        if self.winmm is not None:
            self.winmm.mciSendStringW(f"close {self.alias}", None, 0, None)

    def is_playing(self) -> bool:
        self._advance_volume_ramp()
        if os.name != "nt":
            return self.simulated_playing
        if self.process is not None:
            return self.process.poll() is None
        if self.winmm is not None:
            buffer = ctypes.create_unicode_buffer(64)
            error = self.winmm.mciSendStringW(f"status {self.alias} mode", buffer, 64, None)
            return not error and buffer.value.casefold() == "playing"
        return False


class LayeredAudioMixer:
    """Independent named players for dialogue and persistent environments."""

    def __init__(self, volume_transition_seconds: float = 2.0) -> None:
        self.players: dict[str, AudioPlayer] = {}
        self.volumes: dict[str, float] = {}
        self.specs: dict[str, dict[str, Any]] = {}
        self.volume_transition_seconds = max(0.0, float(volume_transition_seconds))
        self.fading_out: set[str] = set()
        self.retiring_players: list[AudioPlayer] = []
        self.player_serial = 0

    @staticmethod
    def _alias(layer: str) -> str:
        safe = "".join(character for character in layer if character.isalnum())[-18:] or "layer"
        digest = hashlib.sha256(layer.encode("utf-8")).hexdigest()[:8]
        return f"storyaudio{safe}{digest}"

    def system_sound(self, name: str) -> None:
        AudioPlayer().system_sound(name)

    def _new_player(self, layer: str) -> AudioPlayer:
        self.player_serial += 1
        return AudioPlayer(f"{self._alias(layer)}x{self.player_serial}")

    def play_layer(
        self, layer: str, path: Path, volume: float = 1.0, loop: bool = False,
        transition_seconds: float = 0.0,
    ) -> None:
        value = max(0.0, min(1.0, float(volume)))
        duration = max(0.0, float(transition_seconds)) if loop else 0.0
        existing = self.players.get(layer)
        spec = self.specs.get(layer, {})
        if duration > 0 and existing is not None and spec.get("loop") and spec.get("path") == path and existing.is_playing():
            # Cancel the pending stop and continue from the actual current
            # volume and recording position, including a rapid vehicle return.
            self.fading_out.discard(layer)
            self.set_layer_volume(layer, value, duration)
            spec["transition_seconds"] = duration
            return
        if existing is not None and duration > 0 and spec.get("loop"):
            self.stop_layer(layer, duration)
            self.retiring_players.append(self.players.pop(layer))
            self.volumes.pop(layer, None)
            self.specs.pop(layer, None)
            self.fading_out.discard(layer)
        else:
            self.stop_layer(layer)
        player = self._new_player(layer)
        player.play_file(path, 0.0 if duration > 0 else value, loop)
        self.players[layer] = player
        self.volumes[layer] = value
        self.specs[layer] = {
            "path": path, "volume": value, "loop": bool(loop), "transition_seconds": duration,
        }
        if duration > 0:
            self.set_layer_volume(layer, value, duration)

    def set_layer_volume(
        self, layer: str, volume: float, transition_seconds: float | None = None,
    ) -> bool:
        player = self.players.get(layer)
        if player is None or layer in self.fading_out:
            return False
        value = max(0.0, min(1.0, float(volume)))
        duration = self.volume_transition_seconds if transition_seconds is None else max(0.0, float(transition_seconds))
        try:
            player.set_volume(value, duration)
        except TypeError:
            # Compatibility with simple injected players used by integrations and tests.
            player.set_volume(value)
        self.volumes[layer] = value
        if layer in self.specs:
            self.specs[layer]["volume"] = value
        return True

    def stop_layer(self, layer: str, transition_seconds: float = 0.0) -> None:
        player = self.players.get(layer)
        duration = max(0.0, float(transition_seconds))
        if player is not None and duration > 0 and self.specs.get(layer, {}).get("loop"):
            if layer not in self.fading_out:
                player.fade_out(duration)
                self.fading_out.add(layer)
            return
        player = self.players.pop(layer, None)
        self.volumes.pop(layer, None)
        self.specs.pop(layer, None)
        self.fading_out.discard(layer)
        if player is not None:
            player.stop()

    def play_file(self, path: Path, volume: float = 1.0, loop: bool = False) -> None:
        self.play_layer("dialogue", path, volume, loop)

    def stop(self) -> None:
        for layer in list(self.players):
            self.stop_layer(layer)
        for player in self.retiring_players:
            player.stop()
        self.retiring_players.clear()

    def maintain_layers(self) -> dict[str, list[str]]:
        """Recover persistent loops and report genuine one-shot completion."""
        restarted: list[str] = []
        finished: list[str] = []
        for player in list(self.retiring_players):
            if not player.is_playing():
                player.stop()
                self.retiring_players.remove(player)
        for layer, player in list(self.players.items()):
            if player.is_playing():
                continue
            spec = self.specs.get(layer, {})
            if layer in self.fading_out:
                self.stop_layer(layer)
                finished.append(layer)
                continue
            if bool(spec.get("loop")) and isinstance(spec.get("path"), Path):
                player.stop()
                replacement = self._new_player(layer)
                duration = float(spec.get("transition_seconds", 0.0))
                value = float(spec.get("volume", 1.0))
                replacement.play_file(Path(spec["path"]), 0.0 if duration > 0 else value, True)
                if duration > 0:
                    replacement.set_volume(value, duration)
                self.players[layer] = replacement
                restarted.append(layer)
                continue
            self.players.pop(layer, None)
            self.volumes.pop(layer, None)
            self.specs.pop(layer, None)
            finished.append(layer)
        return {"restarted": restarted, "finished": finished}

    def finished_layers(self) -> list[str]:
        return self.maintain_layers()["finished"]


class PresentationAdapter:
    def __init__(self, runtime_dir: Path | None = None, config: dict[str, Any] | None = None, audio: Any | None = None) -> None:
        self.config = config or load_config()
        self.runtime_dir = runtime_dir or (APP_DIR / "runtime")
        self.tail = JsonlTail(self.runtime_dir / "outbox.jsonl", from_end=True)
        self.state_path = self.runtime_dir / "player_status.json"
        self.audit_path = self.runtime_dir / "presentation_events.jsonl"
        self.signal_inbox = self.runtime_dir / "control_inbox.jsonl"
        self.media_root = story_media_dir(self.config)
        self.platform_media_root = (APP_DIR / "story_platform" / "media").resolve()
        self.visual_sequences: dict[str, Any] = {}
        try:
            graph_data = json.loads(resolve_path(self.config["active_graph"]).read_text(encoding="utf-8"))
            if isinstance(graph_data.get("visual_sequences"), dict):
                self.visual_sequences = graph_data["visual_sequences"]
        except Exception:
            pass
        presentation = self.config.get("presentation") if isinstance(self.config.get("presentation"), dict) else {}
        self.default_volume_transition_seconds = max(
            0.0, float(presentation.get("audio_volume_transition_seconds", 2.0)),
        )
        self.audio_gate_open = bool(presentation.get("audio_gate_initially_open", True))
        self.audio = audio or LayeredAudioMixer(self.default_volume_transition_seconds)
        self.state: dict[str, Any] = {
            "schema": "platform.player-status.v1", "updated_at": utc_now(), "objective": "Waiting for story graph…",
            "choices": [], "cue": "idle", "graph_id": "", "node": "", "audio_layers": {},
            "audio_gate_open": self.audio_gate_open,
        }
        self.pending_audio_completions: dict[str, dict[str, str]] = {}
        atomic_write_json(self.state_path, self.state)
        self.running = True

    def _save_state(self) -> None:
        self.state["updated_at"] = utc_now()
        atomic_write_json(self.state_path, self.state)

    def _media_path(self, asset: str) -> Path:
        if not self.media_root:
            raise RuntimeError("external_story_media_dir is not configured")
        candidate = (self.media_root / asset).resolve()
        if not candidate.is_relative_to(self.media_root.resolve()):
            raise RuntimeError("Audio asset escapes external_story_media_dir")
        if not candidate.is_file():
            raise RuntimeError(f"Audio asset not found: {candidate}")
        return candidate

    def _platform_media_path(self, asset: str) -> Path:
        candidate = (self.platform_media_root / asset).resolve()
        if not candidate.is_relative_to(self.platform_media_root):
            raise RuntimeError("Platform audio asset escapes the bundled media folder")
        if not candidate.is_file():
            raise RuntimeError(f"Platform audio asset not found: {candidate}")
        return candidate

    def handle(self, command: dict[str, Any]) -> None:
        channel = str(command.get("channel", ""))
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        outcome = "accepted"
        detail = ""
        audit_action: str | None = None
        audit_layer: str | None = None
        audit_volume: float | None = None
        try:
            if channel == "system" and payload.get("action") == "set_audio_gate":
                opened = bool(payload.get("open"))
                self.audio_gate_open = opened
                self.state["audio_gate_open"] = opened
                if not opened:
                    self.audio.stop()
                    self.pending_audio_completions.clear()
                    self.state["audio_layers"] = {}
                self._save_state()
                detail = str(payload.get("reason", ""))
                print(
                    "[AUDIO] Story sound is live." if opened else "[AUDIO] Story sound is waiting for Elite.",
                    flush=True,
                )
            elif channel == "objective":
                self.state.update({
                    "objective": str(payload.get("text", "")), "choices": list(payload.get("choices", [])),
                    "graph_id": str(command.get("graph_id", "")), "node": str(command.get("node", ""))
                })
                self._save_state()
                print(f"[OBJECTIVE] {self.state['objective']}", flush=True)
            elif channel == "visual":
                action = payload.get("action")
                if action == "set_environment_layer":
                    layers = self.state.setdefault("environment_visual_layers", {})
                    layers[str(payload["layer"])] = dict(payload)
                elif action == "stop_environment_layer":
                    self.state.setdefault("environment_visual_layers", {}).pop(str(payload["layer"]), None)
                else:
                    reference = payload.get("sequence_ref")
                    if isinstance(reference, str) and reference in self.visual_sequences:
                        payload = {**payload, "sequence": self.visual_sequences[reference]}
                    self.state["cue"] = str(payload.get("cue", "pulse"))
                    self.state["visual"] = payload
                self._save_state()
                print(f"[VISUAL] {payload.get('cue', action or self.state['cue'])}", flush=True)
            elif channel == "audio":
                audit_action = str(payload.get("action") or (
                    "play_layer" if payload.get("asset") or payload.get("platform_asset") else "audio"
                ))
                audit_layer = str(payload["layer"]) if payload.get("layer") is not None else None
                if not self.audio_gate_open:
                    outcome = "suppressed"
                    detail = "waiting for fresh Elite telemetry"
                elif payload.get("system_sound"):
                    self.audio.system_sound(str(payload["system_sound"]))
                elif payload.get("action") == "set_layer_volume":
                    layer = str(payload["layer"])
                    value = max(0.0, min(1.0, float(payload.get("volume", 1.0))))
                    duration = max(0.0, float(payload.get("transition_seconds", self.default_volume_transition_seconds)))
                    audit_volume = value
                    applied: Any = True
                    if hasattr(self.audio, "set_layer_volume"):
                        try:
                            applied = self.audio.set_layer_volume(layer, value, duration)
                        except TypeError:
                            applied = self.audio.set_layer_volume(layer, value)
                    if applied is False:
                        outcome = "ignored"
                        detail = f"layer inactive: {layer}"
                    else:
                        entry = self.state.setdefault("audio_layers", {}).setdefault(layer, {})
                        entry["effective_volume"] = value
                        entry["target_volume"] = value
                        entry["transition_seconds"] = duration
                        entry["last_command_node"] = str(command.get("node", ""))
                        self._save_state()
                elif payload.get("action") == "clear_layer_volume_override":
                    # EnvironmentRuntime follows this internal state command with
                    # the resolved authored/adjusted set_layer_volume command.
                    detail = "environment mix override cleared"
                elif payload.get("action") == "stop_layer":
                    if hasattr(self.audio, "stop_layer"):
                        duration = max(0.0, float(payload.get("transition_seconds", 0.0)))
                        if duration > 0:
                            try:
                                self.audio.stop_layer(str(payload["layer"]), duration)
                            except TypeError:
                                self.audio.stop_layer(str(payload["layer"]))
                        else:
                            self.audio.stop_layer(str(payload["layer"]))
                    else:
                        self.audio.stop()
                    layer = str(payload["layer"])
                    self.pending_audio_completions.pop(layer, None)
                    self.state.setdefault("audio_layers", {}).pop(layer, None)
                    self._save_state()
                elif payload.get("asset") or payload.get("platform_asset"):
                    platform_asset = payload.get("platform_asset")
                    asset_name = str(platform_asset if platform_asset else payload["asset"])
                    path = (
                        self._platform_media_path(asset_name)
                        if platform_asset else self._media_path(asset_name)
                    )
                    layer = str(payload.get("layer", "dialogue"))
                    if hasattr(self.audio, "play_layer"):
                        args = (layer, path, float(payload.get("volume", 1.0)), bool(payload.get("loop", False)))
                        duration = max(0.0, float(payload.get("transition_seconds", 0.0)))
                        if duration > 0:
                            try:
                                self.audio.play_layer(*args, duration)
                            except TypeError:
                                self.audio.play_layer(*args)
                        else:
                            self.audio.play_layer(*args)
                    else:
                        self.audio.play_file(path, float(payload.get("volume", 1.0)), bool(payload.get("loop", False)))
                    self.state.setdefault("audio_layers", {})[layer] = {
                        "asset": asset_name,
                        "effective_volume": float(payload.get("volume", 1.0)),
                        "target_volume": float(payload.get("volume", 1.0)),
                        "loop": bool(payload.get("loop", False)),
                        "health": "playing",
                        "restart_count": 0,
                        "last_command_node": str(command.get("node", "")),
                    }
                    completion = payload.get("completion_signal")
                    if isinstance(completion, str) and completion.strip() and not payload.get("loop", False):
                        self.pending_audio_completions[layer] = {
                            "signal": completion.strip(),
                            "graph_id": str(command.get("graph_id", "")),
                            "node": str(command.get("node", "")),
                        }
                    else:
                        self.pending_audio_completions.pop(layer, None)
                    self._save_state()
                elif payload.get("stop"):
                    layer = payload.get("layer")
                    if layer and hasattr(self.audio, "stop_layer"):
                        self.audio.stop_layer(str(layer))
                    else:
                        self.audio.stop()
                        self.pending_audio_completions.clear()
                        self.state["audio_layers"] = {}
                    self._save_state()
            elif channel == "clipboard":
                self._clipboard(str(payload.get("text", "")))
            elif channel == "diagnostics":
                detail = "diagnostic command recorded"
            else:
                outcome = "ignored"
                detail = f"unknown channel {channel!r}"
        except Exception as exc:
            outcome = "failed"
            detail = str(exc)
            print(f"[PRESENTATION] {channel} failed: {exc}", flush=True)
        append_jsonl(self.audit_path, {
            "at": utc_now(), "channel": channel, "outcome": outcome, "detail": detail,
            "graph_id": command.get("graph_id"), "node": command.get("node"),
            **({"action": audit_action} if audit_action else {}),
            **({"layer": audit_layer} if audit_layer else {}),
            **({"volume": audit_volume} if audit_volume is not None else {}),
            **({"transition_seconds": payload["transition_seconds"]}
               if channel == "audio" and "transition_seconds" in payload else {}),
        })

    def poll_audio_completions(self) -> list[str]:
        if not hasattr(self.audio, "finished_layers") and not hasattr(self.audio, "maintain_layers"):
            return []
        restarted: list[str] = []
        if hasattr(self.audio, "maintain_layers"):
            result = self.audio.maintain_layers()
            restarted = list(result.get("restarted", []))
            finished = list(result.get("finished", []))
        else:
            finished = list(self.audio.finished_layers())
        emitted: list[str] = []
        state_changed = False
        for layer in restarted:
            entry = self.state.setdefault("audio_layers", {}).get(layer)
            if isinstance(entry, dict):
                entry["health"] = "recovered"
                entry["restart_count"] = int(entry.get("restart_count", 0)) + 1
                entry["last_restarted_at"] = utc_now()
                state_changed = True
            append_jsonl(self.audit_path, {
                "at": utc_now(), "channel": "audio", "outcome": "recovered",
                "detail": "looping layer restarted after unexpected playback end",
                "action": "loop_restarted", "layer": layer,
            })
            print(f"[AUDIO] Recovered looping layer: {layer}", flush=True)
        for layer in finished:
            self.state.setdefault("audio_layers", {}).pop(layer, None)
            state_changed = True
            pending = self.pending_audio_completions.pop(layer, None)
            if not pending:
                continue
            name = pending["signal"]
            append_jsonl(self.signal_inbox, {
                "name": name, "source": "presentation", "at": utc_now(),
                "data": {"layer": layer, "graph_id": pending["graph_id"], "node": pending["node"]},
            })
            emitted.append(name)
        if state_changed:
            self._save_state()
        return emitted

    def _clipboard(self, text: str) -> None:
        if os.name != "nt":
            print(f"[CLIPBOARD SIMULATED] {text}", flush=True)
            return
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        if not user32.OpenClipboard(None):
            raise RuntimeError("Cannot open Windows clipboard")
        memory = None
        try:
            memory = kernel32.GlobalAlloc(GMEM_MOVEABLE, (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar))
            if not memory:
                raise RuntimeError("Cannot allocate clipboard memory")
            pointer = kernel32.GlobalLock(memory)
            if not pointer:
                raise RuntimeError("Cannot lock clipboard memory")
            ctypes.memmove(pointer, ctypes.create_unicode_buffer(text), (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar))
            kernel32.GlobalUnlock(memory)
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, memory):
                raise RuntimeError("Cannot publish clipboard text")
            memory = None
        finally:
            user32.CloseClipboard()
            if memory:
                kernel32.GlobalFree(memory)

    def run(self) -> int:
        try:
            with InstanceLock("presentation", APP_DIR):
                return self._run_locked()
        except AlreadyRunningError:
            print("[PRESENTATION] Another presentation adapter is already running for this installation.", flush=True)
            return 3

    def _run_locked(self) -> int:
        print("[PRESENTATION] Objective, visual, audio and clipboard adapter active.", flush=True)

        def stop(*_: Any) -> None:
            self.running = False

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        try:
            while self.running:
                for command in self.tail.poll():
                    self.handle(command)
                self.poll_audio_completions()
                time.sleep(0.08)
            return 0
        finally:
            self.audio.stop()


def main() -> int:
    return PresentationAdapter().run()


if __name__ == "__main__":
    raise SystemExit(main())
