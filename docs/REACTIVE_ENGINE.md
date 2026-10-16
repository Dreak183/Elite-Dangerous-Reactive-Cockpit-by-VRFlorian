# Core Reactive Cockpit Engine (`reactive_cockpit.py`)

> **Source File:** [`Reactive Cockpit Files/reactive_cockpit.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py) (`4,524` lines, `VERSION = "1.10.0-DISCO-TEST"`)
> **Config File:** [`Reactive Cockpit Files/reactive_cockpit_config.json`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit_config.json)

---

## 1. Engine Responsibilities & Polling Loop

[`run_controller()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L3164-L3340) runs a single-threaded, deterministic `10 Hz` (`time.sleep(0.10)`) control loop that:
1. Seeds `theme_cache/` from `bundled_theme_cache/` (`merge_bundled_cache(preserve_existing=True)`).
2. Reconstructs the player's current ship state from the newest 12 `Journal*.log` files (`reconstruct_current_ship()`).
3. Tails new lines appended to the active `Journal*.log` (`JournalTail.events()`), rotating automatically when Elite Dangerous starts a new journal file.
4. Polls `Status.json` whenever its `st_mtime_ns` changes (`read_status()` with 4× `25 ms` retry backoff for Elite's in-place truncation race).
5. Reloads `Shipyard_v3.json` whenever its `st_mtime_ns` changes (`Runtime.refresh_shipyard_if_changed()`).
6. Polls `CTRL+SHIFT+D` via Win32 `GetAsyncKeyState` (`DiscoHotkey.poll()`).
7. Computes the highest-priority desired cockpit state (`Runtime.desired(now)`) and, if changed, atomically swaps the pre-compiled EDHM `.ini` files in `EDHM-ini/` (`Runtime.apply_desired()`).

---

## 2. Telemetry Ingestion

### 2.1 `Status.json` Bitflags ([`reactive_cockpit.py#L47-L71`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L47-L71), [`update_status()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L2385-L2586))

| Constant | Bit | Value | Engine Usage |
| :--- | :--- | :--- | :--- |
| `FLAG_DOCKED` | `Flags` bit 0 | `1` | Suppresses contextual danger (`COMBAT`, shields-down `DEEP_DANGER`, `GLIDE`); triggers 3s docking completion hold on `False -> True`. |
| `FLAG_LANDED` | `Flags` bit 1 | `2` | Clears airborne `docking_active`; triggers 3s docking completion hold on `False -> True`. |
| `FLAG_LANDING_GEAR_DOWN` | `Flags` bit 2 | `4` | Activates `DOCKING` when airborne (`not docked and not landed and not supercruise`); triggers 3s hold on retraction (`True -> False`). |
| `FLAG_SHIELDS_UP` | `Flags` bit 3 | `8` | Escalates active contextual danger to `DEEP_DANGER` when `False` on a shielded ship. |
| `FLAG_SUPERCRUISE` | `Flags` bit 4 | `16` | Activates `SUPERCRUISE`; clears any docking latch; triggers 3s hold on exit (`True -> False`). |
| `FLAG_SILENT_RUNNING` | `Flags` bit 10 | `1024` | Activates Priority 1 `STEALTH` (`"stealth"` slot). |
| `FLAG_SCOOPING_FUEL` | `Flags` bit 11 | `2048` | Activates Priority 11 `SCOOP` (`"scoop"` slot). |
| `FLAG_LOW_FUEL_GAME` | `Flags` bit 19 | `524288` | Diagnostic only (`<25%`); engine computes exact `fuel_main / fuel_capacity_main < 0.20` (`<20%`). |
| `FLAG_OVERHEATING` | `Flags` bit 20 | `1048576` | Activates Priority 5 `DEEP_DANGER` (`>100%` heat). |
| `FLAG_IS_IN_DANGER` | `Flags` bit 22 | `4194304` | **Cannot seed combat alone** (to ignore star heat / hazard false positives), but refreshes `last_danger_signal` once combat is already active. |
| `FLAG_BEING_INTERDICTED` | `Flags` bit 23 | `8388608` | Seeds and maintains `COMBAT` (`touch_danger()`). |
| `FLAG_IN_MAIN_SHIP` | `Flags` bit 24 | `16777216` | Main ship vehicle gate; remembers whether mothership is reactive (`remember_auxiliary_parent()`). |
| `FLAG_IN_FIGHTER` | `Flags` bit 25 | `33554432` | Routes to shared SLF themes (`FIGHTER_COMBAT` / `FIGHTER_BASE`) if parent ship is reactive. |
| `FLAG_IN_SRV` | `Flags` bit 26 | `67108864` | Routes to shared SRV theme (`SRV_BASE`) if parent ship is reactive. |
| `FLAG2_ON_FOOT` | `Flags2` bit 0 | `1` | Pauses all ship/vehicle HUD switching (`desired()` returns `None`). |
| `FLAG2_IN_TAXI` | `Flags2` bit 1 | `2` | Pauses all HUD switching (Apex Interstellar / Frontline shuttle). |
| `FLAG2_IN_MULTICREW` | `Flags2` bit 2 | `4` | Pauses all HUD switching when aboard another commander's ship. |
| `FLAG2_GLIDE_MODE` | `Flags2` bit 12 | `4096` | Activates Priority 10 `SUPERCRUISE` during planetary glide + 3s exit hold. |

Additionally, `status["Fuel"]["FuelMain"]` is ingested on every `Status.json` update to compute continuous fuel fraction against `ShipState.fuel_capacity_main`.

### 2.2 Player Journal Events ([`reconstruct_current_ship()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L1155-L1231), [`process_event()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L2588-L2794))

| Journal Event | Fields Used | State Mutation |
| :--- | :--- | :--- |
| `Loadout` | `Ship`, `ShipID`, `ShipName`, `ShipIdent`, `HullHealth`, `FuelCapacity.Main`, `Modules` | Reconstructs `ShipState` (including `has_shield_generator` and `canopy_health`), resets all transient hold timers (`last_danger_signal`, `wanted_until`, `glide_hold_until`, `supercruise_hold_until`, `docking_hold_until`, `docking_active`), and forces theme re-evaluation. |
| `UnderAttack` | `Target` | Calls `touch_danger()` (`last_danger_signal = now`), seeding `COMBAT` for `15.0s` (ignored if on foot, in taxi, or in multicrew). |
| `Interdicted` | — | Calls `touch_danger()`, seeding `COMBAT` for `15.0s`. |
| `FSDJump` | `Wanted` | If `Wanted: true`, sets `wanted_until = now + wanted_entry_seconds` (`15.0s`), triggering `COMBAT` on system arrival. |
| `ShieldState` | `ShieldsUp` | Updates `self.shields_up` (does not seed combat on its own; escalates active danger to `DEEP_DANGER`). |
| `HullDamage` | `Health`, `Fighter`, `PlayerPilot` | If `Fighter: true`, updates `fighter_hull_health`; if `PlayerPilot: true`, updates `ship.hull_health` (triggering `DEEP_DANGER` `<50%` or `CRITICAL` `<20%`). |
| `CockpitBreached` | — | Sets `canopy_breached = True` and `ship.canopy_health = 0.0` (triggering `CRITICAL`). |
| `RepairAll` / `Resurrect` | — | Resets `hull_health = 1.0`, `canopy_health = 1.0`, `canopy_breached = False`. |
| `Repair` | `Item` *(see Known Bug note)* | Restores `hull_health = 1.0` or `canopy_health = 1.0`. |
| `AfmuRepairs` | `Module`, `Health`, `FullyRepaired` | Updates `canopy_health` and clears `canopy_breached` once above `0.20`. |
| `Liftoff` / `Undocked` | `PlayerControlled` | Clears `landed`/`docked`; activates `docking_active` if gear is still down, or starts 3s `docking_hold_until` if gear is already up. |
| `Touchdown` / `Docked` | `PlayerControlled` | Sets `landed`/`docked = True`, clears `docking_active`, starts 3s `docking_hold_until`. |
| `FuelScoop` | `Total` | Updates `self.fuel_main`. |
| `SetUserShipName` | `UserShipName`, `UserShipId` | Updates `ship.ship_name` and `ship.ship_ident`; invalidates cached Shipyard match. |
| `Disembark` / `Embark` | `OnStation`, `OnPlanet`, `Taxi`, `Multicrew` | Updates `on_foot`, `in_taxi`, `in_multicrew`. |

---

## 3. State Evaluation & 14-Tier Priority Hierarchy

Evaluated in [`Runtime.desired(now)`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L2914-L3093):

### 3.1 Pre-Priority Context Gates
1. **Disco Mode Gate (`Runtime.apply_desired`, lines 3098–3100):** While `self.disco_active` is `True`, normal telemetry updates continue in memory, but `apply_desired()` executes `apply_disco(now)` instead of applying `desired(now)`.
2. **External Context Gate (lines 2917–2920):** Returns `None` (no writes) if `on_foot`, `in_taxi`, `in_multicrew`, or if none of `in_main_ship`, `in_fighter`, `in_srv` are set.
3. **Auxiliary Vehicle Gate (lines 2931–2955):**
   - Requires `auxiliary_source_is_reactive()` (`True` only if deployed from a mothership with a Reactive Cockpit assignment).
   - **SRV (`in_srv`):** Returns `("SRV_BASE", "SRV Reactive Cockpit Base", ...)`.
   - **Fighter (`in_fighter`):** Returns `("FIGHTER_COMBAT", ...)` if `general_danger_active(now)` is `True`, else `("FIGHTER_BASE", ...)`.
4. **Unassigned Main Ship Gate (lines 2957–2960):** If the current ship has no assignment in `cockpit_assignments`, returns `base_result()` (restores the ship's normal `Shipyard_v3.json` theme).

### 3.2 Main Ship Priority Stack (Highest to Lowest)

> **Empty Slot Fallthrough (`pick()`, lines 2969–2980):** If a package leaves a reactive slot `None`/empty, `pick()` skips that slot and **continues evaluating lower-priority states** before finally falling back to `BASE`.

| Priority | State Name | Slot Key | Condition | Docked Behavior |
| :---: | :--- | :--- | :--- | :--- |
| **1** | `STEALTH` | `stealth` | `in_main_ship and silent_running` | Active if silent running |
| **2** | `CRITICAL` | `critical` | `active_hull_health() < critical_hull_threshold` (`0.20`) | **Persists while docked** until repaired |
| **3** | `CRITICAL` | `critical` | `canopy_breached` or `canopy_health <= critical_canopy_threshold` (`0.20`) | **Persists while docked** until repaired |
| **4** | `DEEP_DANGER` | `deep_danger` | `in_main_ship and hull < deep_danger_hull_threshold` (`0.50`) | **Persists while docked** until repaired |
| **5** | `DEEP_DANGER` | `deep_danger` | `overheating` (`FLAG_OVERHEATING`) | Active whenever overheating |
| **6** | `DEEP_DANGER` | `deep_danger` | `low_fuel()` (`fuel_main / fuel_capacity_main < 0.20`) | **Persists while docked** until refueled |
| **7** | `DEEP_DANGER` | `deep_danger` | `(general_danger or wanted or glide) and not shields_up and has_shield_generator is not False` | **Suppressed when docked** |
| **8** | `COMBAT` | `combat` | `general_danger_active(now)` (`status_interdicted` or within `15.0s` of `UnderAttack`/`Interdicted`/`IsInDanger` refresh) | **Suppressed when docked** |
| **9** | `COMBAT` | `combat` | `wanted_active(now)` (within `15.0s` of `FSDJump` with `Wanted: true`) | **Suppressed when docked** |
| **10** | `SUPERCRUISE` | `supercruise` | `glide_active(now)` (`glide_mode` or within `3.0s` exit hold) | **Suppressed when docked** |
| **11** | `SCOOP` | `scoop` | `in_main_ship and scooping_fuel` | N/A |
| **12** | `DOCKING` | `docking` | `in_main_ship and docking_state_active(now)` (not supercruise, and airborne with gear down OR within `3.0s` hold after dock/touchdown/gear-up/liftoff) | Holds `3.0s` on dock/touchdown, then settles to `BASE` |
| **13** | `SUPERCRUISE` | `supercruise` | `in_main_ship and supercruise_state_active(now)` (`supercruise` or within `3.0s` exit hold) | N/A |
| **14** | `BASE` | *(base_theme)* | Fallback when no reactive state applies | Active when parked on pad |

---

## 4. Shipyard Matching & Assignment Scoring

[`match_shipyard_ship()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L1108-L1136) and [`assignment_for_ship_entry()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L1448-L1488) match the live `ShipState` against EDHM's `Shipyard_v3.json` and `cockpit_assignments` while strictly enforcing ship hull type (`kind_short`) boundaries:

| Match Tier | Score | Criteria | Notes |
| :--- | :---: | :--- | :--- |
| **Exact Shipyard ID + Kind** | `100..115` | `ship_id == shipyard_id` AND `kind_short` matches (`+10` name, `+5` plate) | Primary match when EDHM `ship_id` is unchanged. |
| **Kind + Name + Plate** | `90` | `kind_short` + `name` + `plate` all match | Survives EDHM `ship_id` regeneration. |
| **Kind + Name** | `80` | `kind_short` + `name` match | Survives callsign/plate change. |
| **Kind + Plate (Self-Healing)** | `75` | `kind_short` + `plate` match | **Uniqueness required:** Accepted only if `kind_short + plate` is unique across both `Shipyard_v3.json` and `cockpit_assignments`. Automatically heals after a ship is renamed in-game (`SetUserShipName`). |
| **Legacy ID without Kind** | `60..75` | `ship_id == shipyard_id` when selector `kind_short` was empty | Legacy v1.1 migration fallback. |

---

## 5. Disco Mode (`CTRL+SHIFT+D`)

Implemented in [`apply_disco_palette()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L663-L700) and [`Runtime.toggle_disco()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L2272-L2294):
- **8 Authored Palettes (`DISCO_PALETTES`, lines 367–376):**
  1. `NEON HORIZON` (ambient `#FF2BD6`, primary `#00E5FF`, secondary `#7B36FF`)
  2. `BLUE MONDAY` (ambient `#126BFF`, primary `#FF2A8A`, secondary `#FFB000`)
  3. `XENO LIME` (ambient `#8CFF2E`, primary `#8A35FF`, secondary `#00D9FF`)
  4. `SOL FLARE` (ambient `#FF253A`, primary `#FFD21A`, secondary `#168BFF`)
  5. `TURQUOISE DREAM` (ambient `#00D8B4`, primary `#743CFF`, secondary `#FF4DA6`)
  6. `ORANGE PULSE` (ambient `#FF7800`, primary `#00BFFF`, secondary `#FF00AA`)
  7. `BIOLOGICAL` (ambient `#35F56F`, primary `#FF397D`, secondary `#4A42FF`)
  8. `CELEBRATION` (ambient `#147DFF`, primary `#FF5A1F`, secondary `#00FFD0`)
- **Surgical INI Mutation:** Every `disco_interval_seconds` (`2.0s`), regex-replaces only the 9 cabin-lighting keys (`x190/y190/z190` ambient glow, `x69/y69/z69` primary cabin light, `x70/y70/z70` secondary cabin light) inside `EDHM-ini/Advanced.ini`, sleeps `30 ms`, and touches `ThemeSettings.json`.
- **Clean Restoration:** Captures `disco_restore_theme = live_theme_name(edhm_ini)` when turned ON. When turned OFF, removes `runtime/disco_mode.active`, resets `applied_key = None`, and immediately applies the current live telemetry state (or `disco_restore_theme` if on foot).

---

## 6. CLI Subcommands (`reactive_cockpit.py`)

| Command | Function | Description |
| :--- | :--- | :--- |
| `py -3 reactive_cockpit.py run` | `run_controller()` | Starts the live 10 Hz reactive controller loop. |
| `py -3 reactive_cockpit.py diagnostics` | `diagnostics()` | Prints paths, active ship, Shipyard match, flags, and cached theme status. |
| `py -3 reactive_cockpit.py selftest` | `selftest()` | Runs the comprehensive in-memory self-test suite (disables config writes). |
| `py -3 reactive_cockpit.py community_install` | `community_install()` | Interactive CLI installer for assigning packages to Shipyard ships. |
| `py -3 reactive_cockpit.py setup` | `setup()` | Interactive wizard to capture live themes from EDHM UI into `theme_cache`. |
| `py -3 reactive_cockpit.py update` | `update_themes()` | Selective theme re-capture utility for customized EDHM themes. |
