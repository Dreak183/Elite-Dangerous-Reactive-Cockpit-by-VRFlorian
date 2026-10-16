# Launcher, Backend, Updater & Release Packaging

> **Files Covered:**
> - [`Reactive Cockpit Files/REACTIVE_COCKPIT_LAUNCHER.ps1`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1)
> - [`Reactive Cockpit Files/reactive_launcher_backend.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py) (`v0.8-OPEN-TEST`)
> - [`Reactive Cockpit Files/UPDATE_REACTIVE_COCKPIT.ps1`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/UPDATE_REACTIVE_COCKPIT.ps1) & [`update_config.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/update_config.json)
> - [`Reactive Cockpit Files/VALIDATE_CLEAN_PLAYER_BUILD.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/VALIDATE_CLEAN_PLAYER_BUILD.py) & [`PLAYER_BUILD.marker`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/PLAYER_BUILD.marker)

---

## 1. PowerShell WPF Launcher (`REACTIVE_COCKPIT_LAUNCHER.ps1`)

### 1.1 Why PowerShell + WPF?
[`REACTIVE_COCKPIT_LAUNCHER.ps1`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L1-L8) is built in native Windows PowerShell using `.NET` WPF (`PresentationFramework`, `PresentationCore`, `WindowsBase`) so that a first-time player can open the GUI **before Python or EDHM is installed**.

### 1.2 Prerequisite Detection & Bootstrap
- **Python 3.10+ Detection ([`Test-PythonRuntime` & `Find-Python`, lines 285–320](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L285-L320)):**
  - Probes `py.exe -3`, `python.exe` (skipping `Microsoft\WindowsApps` Store stubs), `%LOCALAPPDATA%\Programs\Python`, and `%ProgramFiles%\Python*`.
  - Executes `import sys,json,ctypes; assert sys.version_info >= (3,10); print(sys.executable)` with an 8-second timeout.
  - If missing, [`Install-Python`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L599-L616) runs `winget install -e --id Python.Python.3.14` or opens `python.org`.
- **EDHM UI v3 Detection ([`Get-EdhmState`, lines 322–332](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L322-L332); [`Install-Or-Open-Edhm`, lines 618–640](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L618-L640)):**
  - Checks `%USERPROFILE%\EDHM_UI\ODYSS` or `HORIZ` (`Ready`) and `%LOCALAPPDATA%\EDHM-UI-V3\*.exe` (`Installed`).
  - Downloads `edhm-ui-v3-windows-x64.exe` from `https://api.github.com/repos/BlueMystical/EDHM_UI/releases/latest` if not installed.

### 1.3 Four Launcher Views
1. **`HomePage` ([lines 881–914](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L881-L914)):** Readiness indicators, `CHECK FOR UPDATES`, 7 Cockpit package selector buttons, `STORY MODE ON/OFF` toggle, `STORY MODE OPTIONS` button, and `ACTIVATE REACTIVE COCKPIT!` (shown once at least one cockpit assignment exists).
2. **`CockpitPage` ([lines 986–1005](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L986-L1005)):** Displays package preview background, Shipyard ship selector dropdown (`ShipCombo`), and `INSTALL ON SELECTED SHIP`.
3. **`StoryPage` ([lines 916–952](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L916-L952)):** Selects `LIVING WORLD`, `MISSION 1`, `MISSION 2` (plus locked `MISSION 3`), provides `RESET` buttons for Missions 1 & 2, and opens the WIND keybind review dialog (`CONFIRM KEYBINDS` / `UPDATE KEY BINDS`).
4. **`BindingsPage` ([lines 954–984](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L954-L984)):** Displays Elite Dangerous movement/thruster keybinds parsed by `story_platform.wind_bindings`, allowing the player to confirm, auto-populate missing secondary bindings (`apply`), or skip (`defer`).

---

## 2. Python Backend CLI Contract (`reactive_launcher_backend.py`)

Invoked via [`Invoke-Backend`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L342-L372), [`reactive_launcher_backend.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py#L426-L464) prints a single JSON object to `stdout` and exits with `0` (success) or `2` (error):

| Subcommand | Function | Behavior |
| :--- | :--- | :--- |
| `bootstrap` | [`bootstrap_shipyard()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py#L174-L224) | Reads `Shipyard_v3.json`, extracts `Commander` name from `Journal*.log`, enables Shipyard (`enabled: true`), backs up `Shipyard_v3.json`, and saves discovered paths to `reactive_cockpit_config.json`. **Never fabricates `ships` entries from Journal `Loadout` events** because EDHM's `ship_id` is an internal EDHM identifier. |
| `status` | [`status()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py#L414-L423) | Returns paths, ship list (`current_ships()`), current `cockpit_assignments`, and `story_status()`. |
| `list-ships` | [`current_ships()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py#L227-L245) | Returns ships from `Shipyard_v3.json` with deterministic 16-char SHA-1 `token = sha1(f"{norm(kind_short)}\|{norm(name)}\|{norm(plate)}")[:16]`. |
| `install --package <id> --ship-token <tok>` | [`install_package()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py#L248-L299) | Copies the package's 8 themes + 3 auxiliary themes into EDHM's `ODYSS/Themes/`, seeds `theme_cache/`, upserts the ship assignment in `reactive_cockpit_config.json`, and sets `ship["theme"] = base_theme` in `Shipyard_v3.json`. |
| `story-status` | [`story_status()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py#L320-L352) | Returns active graph ID, installation status, and whether saved checkpoint files exist for `living_world`, `mission_1`, and `mission_2`. |
| `select-story --story-id <id>` | [`select_story()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py#L355-L378) | Acquires `InstanceLock("supervisor")` (refusing to switch while a session is running), updates `active_graph` in `Story Platform/platform_config.json`, and updates `runtime/graph_manager_state.json`. |
| `reset-story --story-id <id>` | [`reset_story()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_launcher_backend.py#L381-L411) | Acquires `InstanceLock("supervisor")` and renames all 6 runtime state files (`state_*`, `environment_state_*`, `navigation_state_*`, `time_gate_state_*`, `scan_state_*`, `developer_session_*`) to `.reset_<YYYYMMDD_HHMMSS>.bak`. |

---

## 3. In-Place Updater (`UPDATE_REACTIVE_COCKPIT.ps1`)

1. **Manifest Check & Integrity Verification ([`REACTIVE_COCKPIT_LAUNCHER.ps1#L495-L597`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/REACTIVE_COCKPIT_LAUNCHER.ps1#L495-L597)):**
   - Fetches `manifest_url` from [`update_config.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/update_config.json).
   - Compares semantic `[version]` numbers (`Test-IsNewerVersion`).
   - Downloads the release ZIP to `%TEMP%`, verifies exact `size_bytes`, and verifies `Get-FileHash -Algorithm SHA256` against `$Manifest.sha256`.
   - Spawns a detached `powershell.exe` process running [`UPDATE_REACTIVE_COCKPIT.ps1`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/UPDATE_REACTIVE_COCKPIT.ps1) with `-ParentPid $PID` and closes the launcher.
2. **Protected User Files ([`Is-Protected`, lines 16–32](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/UPDATE_REACTIVE_COCKPIT.ps1#L16-L32)):**
   The updater **never overwrites** any file matching these 7 relative paths:
   - `user_data` (`launcher_state.json`, `launcher_music.json`)
   - `runtime` (`disco_mode.active`)
   - `logs` (`ReactiveCockpit_*.log`)
   - `reactive_cockpit_config.json` (player's ship assignments and paths)
   - `Story Platform\wind_settings.json` (confirmed/deferred keybind state)
   - `Story Platform\runtime` (all mission progress checkpoints and state files)
   - `Story Platform\platform_config.json` (selected story graph)
3. **Transactional Backup & Rollback ([lines 80–122](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/UPDATE_REACTIVE_COCKPIT.ps1#L80-L122)):**
   - Before overwriting any file, copies the original to `%TEMP%\reactive-cockpit-backup-<timestamp>-<guid>`.
   - If any file copy fails, the `catch` block deletes partially copied files, restores every backed-up file, removes newly created files, and alerts the user.

---

## 4. Clean Player Build Validation (`VALIDATE_CLEAN_PLAYER_BUILD.py`)

Before packaging a release ZIP, run [`VALIDATE_CLEAN_PLAYER_BUILD.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/VALIDATE_CLEAN_PLAYER_BUILD.py#L1-L97):
- Verifies `reactive_cockpit_config.json` has empty `cockpit_assignments` (`[]`), `active_package_id: null`, `reactive_ship: null`, `theme_names: {}`, `disco_interval_seconds: 2.0`, and **no machine-specific paths** (`edhm_ini`, `shipyard_file`, `journal_dir` must be absent or empty).
- Verifies `user_data/launcher_state.json` has `story_mode_enabled: false` and `selected_story_id: "living_world"`.
- Verifies `Story Platform/platform_config.json` has `active_graph: "stories/yggdrasil/graph.json"`.
- Verifies neither `runtime/` nor `Story Platform/runtime/` contains any files.
- Verifies all **59 required themes** exist in both `bundled_edhm_themes/` and `theme_cache/`, and that every cached `Advanced.ini` contains each of the 9 `DISCO_COLOR_KEYS` (`x190, y190, z190, x69, y69, z69, x70, y70, z70`) **exactly once**.
