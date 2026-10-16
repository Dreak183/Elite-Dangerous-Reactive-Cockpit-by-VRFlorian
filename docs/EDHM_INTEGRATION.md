# EDHM & 3DMigoto Technical Integration Reference

> **Reference Files:** [`.internal/EDHM_reference/GameDir/`](file:///X:/Projects/EDProjects/RC_dev/.internal/EDHM_reference/GameDir) & [`.internal/EDHM_reference/UserDir/`](file:///X:/Projects/EDProjects/RC_dev/.internal/EDHM_reference/UserDir)
> **Reactive Cockpit Theme Cache:** [`Reactive Cockpit Files/theme_cache/`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/theme_cache/index.json) & [`Reactive Cockpit Files/bundled_edhm_themes/`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/bundled_edhm_themes)

---

## 1. How EDHM Works Under the Hood (3DMigoto + DirectX 11 Shaders)

**EDHM (Elite Dangerous HUD Mod)** is built on **3DMigoto**, a DirectX 11 `d3d11.dll` wrapper injected into the Elite Dangerous game folder.

### 1.1 The `IniParams` (`t120`) Register Array
- In 3DMigoto, every `[Constants]` key `x0..x255`, `y0..y255`, `z0..z255`, `w0..w255` defined across `Startup-Profile.ini`, `Advanced.ini`, `SuitHud.ini`, `XML-Profile.ini`, and `35aac.ini` is packed into a **256-texel 1D `float4` texture bound to shader resource slot `t120`**:
  ```hlsl
  dcl_resource_texture1d (float,float,float,float) t120
  ld_indexable(texture1d)(float,float,float,float) rX.xyzw, l(Index, 0, 0, 0), t120.xyzw
  ```
- Each index $N \in [0, 255]$ holds `float4(xN, yN, zN, wN)`.
- **Component Packing Rule:** Because `t120` has 256 slots, EDHM frequently packs an RGB color into `xN, yN, zN` and an **unrelated scalar mode switch or opacity multiplier** into `wN`.
  - *Example (`Index 150..152`):* `x150..z150`, `x151..z151`, `x152..z152` hold the $3\times 3$ XML Color Matrix in `XML-Profile.ini`, while `w150` is **Asteroid Outlines Radar Mode**, `w151` is **Jump System Info Background Opacity**, and `w152` is **Shield Depleted Mode** in `Advanced.ini`!
  - **Consequence for Developers:** Never blindly overwrite `wN` when modifying an RGB triple `xN/yN/zN` unless `ThemeSettings.json` explicitly binds `xN|yN|zN|wN` together as a 4-channel RGBA color.

### 1.2 Texture Hash Filtering (`35aac.ini` → `35aac13bbb1540de-ps.txt`)
In [`.internal/EDHM_reference/GameDir/EDHM-ini/35aac.ini`](file:///X:/Projects/EDProjects/RC_dev/.internal/EDHM_reference/GameDir/EDHM-ini/35aac.ini#L1-L29):
- `[ShaderOverride-35aac13bbb1540de]` intercepts Elite Dangerous's primary HUD pixel shader (`35aac13bbb1540de-ps.txt`).
- Before each draw call, `w105 = ps-t1` writes the numeric `filter_index` of the bound HUD texture (`[TextureOverride-*]` matched by 32-bit CRC hash) into `t120[105].w`.
- Inside [`35aac13bbb1540de-ps.txt`](file:///X:/Projects/EDProjects/RC_dev/.internal/EDHM_reference/GameDir/ShaderFixes/35aac13bbb1540de-ps.txt#L67-L253), the shader reads `w105` alongside UV coordinates (`v0.xy`), vertex colors (`v1`, `v2`), and primitive IDs (`v4.x`) to identify the exact HUD element being drawn (e.g., inner vs. middle vs. outer shield ring, radar holo, fuel gauge, or cabin light).

### 1.3 Element Color Modes (`Startup-Profile.ini`)
For each HUD element, its mode key in [`Startup-Profile.ini`](file:///X:/Projects/EDProjects/RC_dev/.internal/EDHM_reference/GameDir/EDHM-ini/Startup-Profile.ini) controls which shader branch executes:

| Mode Value | Name | Shader Math in `35aac13bbb1540de-ps.txt` |
| :---: | :--- | :--- |
| **`200`** | **Elite Default (Original)** | Leaves the sampled texture pixel `r1.xyz` untouched (original Frontier orange/cyan). |
| **`199`** | **XML Color Matrix** | Multiplies the input RGB `r1.xyz` by the $3\times 3$ matrix in `XML-Profile.ini` (`t120[150..152].xyz`):<br>$\text{RGB}_{\text{out}} = R_{\text{in}}\cdot(x_{150},y_{150},z_{150}) + G_{\text{in}}\cdot(x_{151},y_{151},z_{151}) + B_{\text{in}}\cdot(x_{152},y_{152},z_{152})$ |
| **`198`** | **Invert XML / XML Variant** | Applies inverted XML matrix transformation (e.g., for dark-on-light inverted panel text `z143`) or XML without glow (`y143`). |
| **`100`** | **Custom Color** | Extracts scalar pixel luminance $I = \max(R_{\text{in}}, G_{\text{in}}, B_{\text{in}})$ and multiplies by the linear sRGB float triple `(xN, yN, zN)` from `Advanced.ini`, scaling opacity/brightness by `wN`. |
| **`101..103`** | **Specialized Custom Modes** | Element-specific multi-color or preset modes (e.g., `z105 = 101` Single-Color Shield vs `100` 3-Ring Multi-Color Shield; `y136 = 101/102/103` Single/Bi/Tri-Color Power Distributor; `y19 = 101` Bi-Color Cabin Lighting). |

---

## 2. Key `Advanced.ini`, `Startup-Profile.ini`, and `XML-Profile.ini` Constants

| HUD Subsystem | Mode / Toggle Key (`Startup-Profile.ini` or `Advanced.ini`) | Color / Value Keys (`Advanced.ini` / `XML-Profile.ini`) | Notes |
| :--- | :--- | :--- | :--- |
| **XML Color Matrix** | Per-element `199` | `<MatrixRed>`: `x150, y150, z150`<br>`<MatrixGreen>`: `x151, y151, z151`<br>`<MatrixBlue>`: `x152, y152, z152` | Defined in `XML-Profile.ini`. |
| **Global Color Protection** | `z3, y4, z4, w4, x5, y5, z5` (`Startup-Profile.ini:1153-1187`) | `1` = Protect, `0` = Unprotected | Protects semantic Yellow, White, Green, Red, Cyan, and Superpower icons from XML matrix tinting. |
| **Cabin Lighting — Background Glow** | `w159` (`100` Custom, `200` Default) | `x190, y190, z190` | **Mutated by Disco Mode (`ambient`)**. |
| **Cabin Lighting — Primary Light** | `y19` (`100` Custom, `101` Bi-Color, `199` XML, `200` Default) | `x69, y69, z69` | **Mutated by Disco Mode (`primary`)**. |
| **Cabin Lighting — Secondary Light** | `w160` (`100` Custom, `101` Bi-Color, `199` XML, `200` Default) | `x70, y70, z70` | **Mutated by Disco Mode (`secondary`)**. |
| **Cabin Lighting — Master Brightness** | — | `w69` (`0.0..2.0`) | Overall cockpit light intensity (`Advanced.ini:1347`). |
| **Shields (Single vs Multi-Ring)** | `z105` (`100` Multi, `101` Single, `199` XML, `200` Default) | Single Up: `x228..z228`<br>Down: `x159..z159`<br>Multi Rings: `x201..w203` | `w152` controls Shield Depleted visibility (`100` Invisible, `200` Default). |
| **Own Ship & Target Radar Holo** | `x106` (Own), `z106` (Target) | Own: `x230..w230`<br>Target: `x231..w231` | — |
| **Power Distributor (SYS/ENG/WEP)** | `y136` (`101` 1-Col, `102` 2-Col, `103` 3-Col, `199` XML, `200` Default) | SYS: `x161..z161`<br>ENG: `x162..z162`<br>WEP: `x163..z163`<br>Brightness: `w163` | — |
| **Heat & Hull Bars** | `x143` (Heat), `w142` (Hull) | Heat `<35%`: `x219..w219`<br>Heat `<65%`: `x220..w220`<br>Heat `>65%`: `x221..w221`<br>Hull: `x216..w216` | — |
| **Odyssey Suit HUD** | `x30` (Panel), `y30` (Shield), `z30` (Text), `w30` (Inv Text) | `x31..w38`, O2/Energy `x126..w127` | Defined in `SuitHud.ini`. |

---

## 3. How Reactive Cockpit Switches Themes Without Sending `F11` Directly

1. **Bundled Library Themes (`bundled_edhm_themes/<ThemeName>/`):**
   - Contains only `ThemeSettings.json` (where colors are stored as signed 32-bit ARGB integers under `ui_groups`) plus preview `.jpg` images. **3DMigoto cannot read `ThemeSettings.json` directly.**
2. **Pre-Captured Runtime Cache (`bundled_theme_cache/themes/<12-char-sha1>/` → `theme_cache/themes/<12-char-sha1>/`):**
   - Contains the **compiled `.ini` files** (`Advanced.ini`, `Startup-Profile.ini`, `SuitHud.ini`, `XML-Profile.ini`, optional `Custom.ini`) plus `ThemeSettings.json` and `manifest.json`.
   - Folder hash formula ([`reactive_cockpit.py#L920-L922`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L920-L922)):
     ```python
     hashlib.sha1(theme_name.encode("utf-8")).hexdigest()[:12]
     ```
3. **Live Application Protocol ([`apply_cached_theme()`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L980-L997)):**
   - Atomically copies `Advanced.ini`, `Startup-Profile.ini`, `SuitHud.ini`, `XML-Profile.ini`, and `Custom.ini` into `EDHM-ini/` via `.reactive_cockpit_tmp` + `os.replace()`.
   - Sleeps **`60 ms`** (`time.sleep(0.06)`) so all `.ini` files settle on disk.
   - Atomically copies `ThemeSettings.json` (`SIGNAL_FILE`) and updates its mtime via `os.utime()`, signaling EDHM's file watcher (`WatchMe: true` in `Settings.json`) / 3DMigoto to reload the shader constants in-game.
   - **Important:** The EDHM UI desktop window itself must remain **closed** while Reactive Cockpit runs so it does not overwrite `EDHM-ini/` with its own UI state.

---

## 4. Complete Catalog of the 59 Authored Themes & Cache Hashes

Defined in [`reactive_cockpit.py#L109-L331`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/reactive_cockpit.py#L109-L331) and verified by [`VALIDATE_CLEAN_PLAYER_BUILD.py`](file:///X:/Projects/EDProjects/RC_dev/Reactive%20Cockpit%20Files/VALIDATE_CLEAN_PLAYER_BUILD.py#L54-L81):

| Package ID | Ship Type (`ed_short`) | Slot | Exact Theme Name | Cache Folder (`sha1[:12]`) |
| :--- | :--- | :--- | :--- | :--- |
| `battle_corsair_snow_vrflorian` | `corsair` | `base`<br>`combat`<br>`deep_danger`<br>`critical`<br>`stealth`<br>`docking`<br>`scoop`<br>`supercruise` | `VRFlo Reactive Cockpit - Base`<br>`VRFlo Reactive Cockpit - Combat`<br>`VRFlo Reactive Cockpit - Deep danger`<br>`VRFlo Reactive Cockpit - IMMINENT DESTRUCTION`<br>`VRFlo Reactive Cockpit - Stealth`<br>`VRFlo Reactive Cockpit - Docking mode`<br>`VRFlo Reactive Cockpit - Scoop`<br>`VRFlo Reactive Cockpit - Super-cruise` | `6c81fef00d60`<br>`06eae48c1399`<br>`b400c3a9ca38`<br>`d9a5337b5d3c`<br>`d41c3f558783`<br>`e817f707afa9`<br>`cdcbbdd5ad3d`<br>`ca786671ea49` |
| `kestrel_heart_eater_vrflorian` | `smallcombat01_nx` (Kestrel Mk 2) | `base`<br>`combat`<br>`deep_danger`<br>`critical`<br>`stealth`<br>`docking`<br>`scoop`<br>`supercruise` | `Kestrel (heart eater) Reactive Cockpit - Base`<br>`Kestrel (Heart eater) Reactive Cockpit - Combat mode`<br>`Kestrel (Heart eater) Reactive Cockpit - Deep damage mode`<br>`Kestrel (Heart eater) Reactive Cockpit - Imminent Destruction`<br>`Kestrel (Heart eater) Reactive Cockpit - Stealth mode`<br>`Kestrel (Heart eater) Reactive Cockpit - Docking mode`<br>`Kestrel (Heart eater) Reactive Cockpit - Scoop mode`<br>`Kestrel (Heart eater) Reactive Cockpit - Super Cruise mode` | `98d28abff790`<br>`55ed7639877f`<br>`545df103ea3a`<br>`09819e0b7ec0`<br>`50e594c9cfe5`<br>`a9de1d542a76`<br>`8c5faf1deba0`<br>`c7f3fbcda246` |
| `python_mkii_vrflorian` | `python_nx` (Python Mk II) | `base`<br>`combat`<br>`deep_danger`<br>`critical`<br>`stealth`<br>`docking`<br>`scoop`<br>`supercruise` | `Python MKII - Reactive Cockpit Base`<br>`Python MKII - Reactive Cockpit Combat`<br>`Python MKII - Reactive Cockpit Deep danger`<br>`Python MKII - Reactive Cockpit Imminent Destruction`<br>`Python MKII - Reactive Cockpit Stealth`<br>`Python MKII - Reactive Cockpit Docking`<br>`Python MKII - Reactive Cockpit Scoop`<br>`Python MKII - Reactive Cockpit Super cruise` | `277bc59e979a`<br>`21fdbc74aff2`<br>`595176a4d9c4`<br>`aab8b2ea86d6`<br>`a4ba11c8c5b8`<br>`a8279e318245`<br>`743c616d6bb0`<br>`71cd603cdd9f` |
| `type11_rocky5_vrflorian` | `lakonminer` (Type 11 Prospector) | `base`<br>`combat`<br>`deep_danger`<br>`critical`<br>`stealth`<br>`docking`<br>`scoop`<br>`supercruise` | `Type 11 - Reactive Cockpit Base`<br>`Type 11 Reactive Cockpit - Combat mode`<br>`Type 11 reactive cockpit - Deep Danger mode`<br>`Type 11 Reactive Cockpit - Imminent destruction`<br>`Type 11 Reactive Cockpit - Stealth`<br>`Type 11 - Reactive Cockpit Docking`<br>`Type 11 Reactive Cockpit - Scoop`<br>`Type 11 - Reactive Cockpit super-cruise` | `1b3b41aa91b4`<br>`8c6b643755b8`<br>`6642c372d380`<br>`95456f39420c`<br>`4505ff46c02c`<br>`b37c823f84e4`<br>`7dd3361c9cba`<br>`17441c5a042b` |
| `caspian_explorer_vrflorian` | `explorer_nx` (Caspian Explorer) | `base`<br>`combat`<br>`deep_danger`<br>`critical`<br>`stealth`<br>`docking`<br>`scoop`<br>`supercruise` | `Caspian - Reactive Cockpit base`<br>`Caspian - Reactive Cockpit Combat`<br>`Caspian Reactive Cockpit Deep danger`<br>`Caspian - Reactive Cockpit Imminent destruction`<br>`Caspian - Reactive Cockpit Stealth`<br>`Caspian - Reactive Cockpit Docking`<br>`Caspian - Reactive Cockpit Scoop`<br>`Caspian - Reactive Cockpit Super Cruise` | `2ae079c3d21a`<br>`74c11e6ddc09`<br>`a8690900686a`<br>`323d1e3dd586`<br>`25031a0a6042`<br>`6d342a52c4c0`<br>`a76c078e8faa`<br>`9c6cb2dd87d2` |
| `nebula_hopper_anaconda_vrflorian` | `anaconda` | `base`<br>`combat`<br>`deep_danger`<br>`critical`<br>`stealth`<br>`docking`<br>`scoop`<br>`supercruise` | `Nebula Hopper Anaconda - Reactive Cockpit Base`<br>`Nebula Hopper Anaconda Reactive Cockpit - Combat mode`<br>`Nebula Hopper Anaconda reactive cockpit - Deep Danger mode`<br>`Nebula Hopper Anaconda Reactive Cockpit - Imminent destruction`<br>`Nebula Hopper Anaconda Reactive Cockpit - Stealth`<br>`Nebula Hopper Anaconda - Reactive Cockpit Docking`<br>`Nebula Hopper Anaconda Reactive Cockpit - Scoop`<br>`Nebula Hopper Anaconda - Reactive Cockpit super-cruise` | `966752b38e20`<br>`19fc4f45ee2a`<br>`5a8b8e5648da`<br>`991ddc4ac6fd`<br>`3190ae7ad100`<br>`a6be5016d4ce`<br>`920a145c5d26`<br>`1478cdf2246e` |
| `classic_elite_vrflorian` | *(Any ship)* | `base`<br>`combat`<br>`deep_danger`<br>`critical`<br>`stealth`<br>`docking`<br>`scoop`<br>`supercruise` | `Classic Elite Reactive Cockpit Base`<br>`Classic Reactive Cockpit - Combat mode`<br>`Classic Reactive Cockpit - Deep Danger mode`<br>`Classic Reactive Cockpit - Imminent Destruction`<br>`Classic Reactive Cockpit - Stealth mode`<br>`Classic Reactive Cockpit - Docking mode`<br>`Classic Reactive Cockpit - Scoop mode`<br>`Classic Reactive Cockpit - Super cruise mode` | `1c35d782ee4e`<br>`605caf068d16`<br>`0b3a8e105fbf`<br>`299e4cf3e965`<br>`5266e78bbb7a`<br>`b0eca3e01666`<br>`d08037690be8`<br>`1204e75d4376` |
| *(Shared Auxiliary)* | `fighter` / `srv` | `fighter.base`<br>`fighter.combat`<br>`srv.base` | `Ship Launched Fighter - Reactive Cockpit Base`<br>`Ship Launched Fighter - Reactive Cockpit Combat`<br>`SRV Reactive Cockpit Base` | `bcfcb4cf17d6`<br>`59a37d2001c2`<br>`efd21795d857` |
