# Story Platform Subsystem (`v0.12.6-LIVING-WORLD`)

> **Base Directory:** [`Reactive Cockpit Files/Story Platform/`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/)
> **Entry Point:** [`platform.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/platform.py) | **Config:** [`platform_config.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/platform_config.json) | **Schema:** [`schema/story_graph.schema.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/schema/story_graph.schema.json)

---

## 1. Overview & Module Map

`Story Platform` is a deterministic, graph-driven narrative, environmental audio/visual, and physical weather engine for Elite Dangerous. It runs alongside [`reactive_cockpit.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py) under the supervision of [`supervisor.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/supervisor.py).

| Module | File | Responsibility |
| :--- | :--- | :--- |
| **Supervisor** | [`story_platform/supervisor.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/supervisor.py) | Spawns/monitors `core` (`reactive_cockpit.py`), `presentation`, `status_window`, `graph` (`runner`), `controls`, and `visual_theme`. Watches `launcher_state.json` (`0.35s`) and handles graph switches (`exit 75`). |
| **Runner** | [`story_platform/runner.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/runner.py) | Main story event loop (`StoryPlatformRunner`). Manages the **Live Telemetry Gate**, polls Journal/`Status.json`/`control_inbox.jsonl`, steps all 6 sub-runtimes, and writes `world_snapshot.json`. |
| **Graph Engine** | [`story_platform/engine.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/engine.py) | Token-based state machine (`GraphRuntime`): active nodes, priority-sorted edges, `fork`/`join`, checkpoints, crash recovery, and `recovery_when` (e.g., `journal.Died`). |
| **Model & Validator** | [`story_platform/model.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/model.py) | Loads and validates `platform.story-graph.v1` JSON graphs (`StoryGraph`, `validate_graph()`). |
| **Predicates** | [`story_platform/predicates.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/predicates.py) | Evaluates boolean combinators (`all`, `any`, `not`, `always`), signal matches, `elapsed_seconds`, dotted `path`/`variable` comparisons, and Haversine `geofence` checks. |
| **Telemetry** | [`story_platform/telemetry.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/telemetry.py) | `WorldModel`, `JournalTail`, `StatusWatcher`, live telemetry confirmation filters, planetary coordinate cleanup, and `status.glide.*` transition signals. |
| **Environments** | [`story_platform/environments.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/environments.py) | `EnvironmentRuntime`: evaluates `surface`, `event`, and `detection` zones, depth curves (`0..100%`), `scanner_pulse` momentum timing, priority volume ducking, and `pause_wind`. |
| **Scans** | [`story_platform/scans.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/scans.py) | `ScanRuntime`: multi-target anomaly scan sets (`idle` → `acquiring` → `completed`), hold timers, detection zone suppression, and `scan.*` signals. |
| **Time Gates** | [`story_platform/time_gates.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/time_gates.py) | `TimeGateRuntime`: deterministic planetary `solar_clocks` and `solar_sites` local solar hour (`0..24h`), `time_gates` open/close windows, and countdowns. |
| **Navigation** | [`story_platform/navigation.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/navigation.py) | `NavigationRuntime`: computes compass bearing (`0..360°`) and surface distance to active `navigators`. |
| **Presentation** | [`story_platform/presentation_adapter.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/presentation_adapter.py) | `LayeredAudioMixer` + `AudioPlayer` (headless PowerShell WPF `MediaPlayer` + `winmm.dll` MCI fallback) and Win32 clipboard writer. |
| **Visual Theme** | [`story_platform/visual_theme_adapter.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/visual_theme_adapter.py) | Applies one-shot `visual_sequences` and persistent environment visual layers to `EDHM-ini`, yielding to Disco Mode and higher-priority core states. |
| **WIND System** | [`wind.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/wind.py), [`wind_runtime.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/wind_runtime.py), [`wind_input.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/wind_input.py), [`wind_bindings.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/wind_bindings.py) | Physical wind simulation (`constant`, `variable`, `gusts`, `dynamic`), `.binds` XML key discovery, and guarded 50 Hz Win32 `SendInput` pulse scheduling. |
| **Player Reader** | [`player_reader.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/player_reader.py), [`status_window.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/status_window.py) | Synthesizes active clue, solar survey windows, remaining scan counts, scanner status, and cockpit state into the Tkinter Live Reader window. |
| **Authoring IDE** | [`graph_editor.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/graph_editor.py), [`editor_model.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/editor_model.py), [`authoring*.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/authoring.py), [`environment_*.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/environment_authoring.py) | Full visual Tkinter node/edge/environment editor with live volume sync and embedded `DryRunSession` simulator ([`dry_run.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/dry_run.py)). |

---

## 2. Live Telemetry Gate & Planetary Telemetry Hygiene

### 2.1 The Live Telemetry Gate ([`telemetry.py#L31-L85`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/telemetry.py#L31-L85), [`runner.py#L193-L241`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/runner.py#L193-L241))
To prevent cached coordinates or menu sessions from playing story audio before the commander enters the game:
1. On startup, `runner.py` immediately emits `set_audio_gate(False)` to `outbox.jsonl` and arms `JournalTail` at EOF **before** hydrating `WorldModel`.
2. Runtimes (`GraphRuntime`, `EnvironmentRuntime`, etc.) remain **unstarted** until `live_telemetry_confirmation_source()` returns a valid confirmation:
   - A newly appended Journal event passing `is_live_journal_telemetry(event)` (rejects `Fileheader`, `Commander`, `LoadGame`, `Shutdown`, `MusicTrack == "MainMenu"`, and requires non-empty `StarSystem` on `Location`/`FSDJump`/`CarrierJump`), **OR**
   - A post-baseline `Status.json` payload change passing `is_live_status_update(current, previous)` (comparing payloads with `"timestamp"` stripped).
3. When confirmed, `_confirm_live_audio()` emits `set_audio_gate(True)`, initializes all sub-runtimes via `_start_live_runtime()`, and immediately dispatches the confirming signal itself.
4. On `journal.Shutdown` or `MusicTrack == "MainMenu"`, `_set_live_audio_gate(False)` closes the gate and stops all audio layers.

### 2.2 Planetary Altitude & Loss-of-Fix Hardening ([`telemetry.py#L188-L199`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/telemetry.py#L188-L199), [`environments.py#L349-L399`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/environments.py#L349-L399))
- When a ship climbs out of planetary atmosphere into orbit or supercruise, Elite Dangerous clears `Status.json` `Flags` bit 21 (`has_lat_long = False`) and stops publishing `Latitude`, `Longitude`, and `Altitude`.
- `WorldModel.status_signals()` explicitly removes `latitude`, `longitude`, `altitude`, and `heading` when `not has_lat_long and vehicle != "on_foot"`.
- In `EnvironmentRuntime._altitude_inside(rule, world)`:
  - When `vehicle == "on_foot"` (where Elite omits `Altitude`), `altitude=None` returns `(True, False)` so surface zones remain active on foot.
  - When `vehicle != "on_foot"` and `flags.has_lat_long is False`, `altitude=None` returns `(False, True)` (`altitude_inside=False, altitude_outside=True`), immediately exiting any altitude-bounded atmospheric zone after its `exit_hold_seconds`.
  - When `has_lat_long` is still `True` during a transient `SupercruiseEntry` gap (`altitude=None`), it returns `(False, False)`, preventing false activation while avoiding premature exit before the next `Status.json` tick.

---

## 3. Authored Story Graphs (`stories/`)

Configured in [`platform_config.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/platform_config.json#L20-L39) and mapped to Launcher slots in [`reactive_launcher_backend.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py#L39-L58):

| Launcher Slot | Graph ID | File | Highlights |
| :--- | :--- | :--- | :--- |
| `living_world` | `yggdrasil` | [`stories/yggdrasil/graph.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/stories/yggdrasil/graph.json) | Default ambient mode (`v1.0.0`). Features planetary surface audio/visual zones, planetary glide events (`planetary_glide_arrival_2`), dynamic wind zones (`real_wind_motion_layer_storm_planet`), and indoor shelter wind-pause zones (`inside_on_storm_planet`). |
| `mission_1` | `guardian_signal_mission_01` | [`stories/mission_01/graph.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/stories/mission_01/graph.json) | *Guardian Signal — Mission One* (`v1.1.2`, 97 nodes, 99 edges, 45 audio assets). Features checkpoints, `recovery_when: {"signal": "journal.Died"}`, parallel `fork`/`join` moon scans (`scan_iapetus` / `scan_enceladus`), scanner pulse fields, and 11 custom `visual_sequences`. |
| `mission_2` | `Test mission 2` | [`stories/mission_02/graph.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/stories/mission_02/graph.json) | *Mission 2 — The Secrets of Aiabiko* (`v1.1.0`, 59 nodes, 69 edges, 54 audio assets). Features planetary `solar_clocks`, `time_gates` (`mountain_window`, `canyon_window`), 12-target `scan_sets` (`mission2_aiabiko_anomalies`), dynamic wind landing windows, and [`player_reader.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/stories/mission_02/player_reader.json). |

---

## 4. Output Channels: Audio, Visuals, WIND & Live Reader

### 4.1 Layered Audio Mixer ([`presentation_adapter.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/presentation_adapter.py#L21-L579))
- Spawns a persistent hidden PowerShell STA process per active audio layer using `System.Windows.Media.MediaPlayer`, controlled via non-blocking `stdin` commands (`volume <target> <ramp_s>`, `fade-stop <ramp_s>`, `stop`). Falls back to `winmm.dll` (`mciSendStringW`) if PowerShell is unavailable.
- **Reversible Fades:** If a layer is fading out (`stop_layer` with `transition_seconds > 0`) and `play_layer` is called for the same layer and asset before the fade finishes, the pending stop is cancelled and volume ramps smoothly back up from its current level without restarting the track.
- **Asset Crossfades:** Replacing an active layer with a *different* asset moves the old player to `retiring_players` to finish fading out concurrently while the new player fades in.
- **Dialogue Rule:** Any layer named `"dialogue"` forces `fade_in_seconds = 0.0` and immediately replaces any prior `"dialogue"` clip without overlap.

### 4.2 Visual Theme Adapter ([`visual_theme_adapter.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/visual_theme_adapter.py#L1-L297))
- Resolves story slot names (`combat`, `deep_danger`, `critical`, `stealth`, `docking`, `scoop`, `supercruise`, `base`) against the player's currently active ship assignment in `reactive_cockpit_config.json`.
- **Priority Coexistence (`PRIORITY`, line 28):** Tails `logs/ReactiveCockpit_*.log` for `STATE ->` and `SHIP ->` lines. Never allows a persistent story environment visual layer to overwrite a higher-priority core Reactive Cockpit state (e.g., `CRITICAL` or `DEEP_DANGER`).
- **Safety Guards:**
  - Defers writes/restores for `5.0s` (`VEHICLE_GUARD_SECONDS`) after any vehicle transition (`ship` ↔ `srv` ↔ `on_foot`).
  - Completely yields to Disco Mode (`rc.disco_mode_active()`) while advancing finite sequence clocks in the background so stale flashes never replay when Disco Mode ends.
  - Restores the latest core theme (`_latest_core_theme()`) as soon as a sequence or environment visual layer ends.

### 4.3 WIND Physical Weather System ([`wind.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/wind.py), [`wind_input.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/Story%20Platform/story_platform/wind_input.py))
- Decomposes planetary wind bearing (`wind_from`) vs. player `heading` into longitudinal (`forward`) and lateral (`right`) components across 4 modes (`constant`, `variable`, `gusts`, `dynamic`).
- Schedules 50 Hz Win32 `SendInput` scan-code pulses mapped to the player's confirmed Elite Dangerous keybinds (`thrust_*` for ship, `steer_*`/`throttle_*` for SRV, `move_*`/`strafe_*` for on-foot).
- **6 Mandatory Safety Guards:**
  1. `10.0s` startup arming delay (`STARTUP_ARM_DELAY_SECONDS`).
  2. Foreground window check (`elitedangerous64.exe` / `elitedangerous.exe` must be the active foreground window).
  3. Immediate key release if any modifier key (`Shift`, `Ctrl`, `Alt`, `Win`) is pressed.
  4. Telemetry freshness & state guard (`Status.json` age `<= 4.0s`, `GuiFocus == 0`, not docked, not landed, not in supercruise/FSD jump, not dead, not indoors on foot).
  5. Zone shelter override (`{"action": "pause_wind"}`).
  6. `atexit` emergency release of all pressed scan codes.
