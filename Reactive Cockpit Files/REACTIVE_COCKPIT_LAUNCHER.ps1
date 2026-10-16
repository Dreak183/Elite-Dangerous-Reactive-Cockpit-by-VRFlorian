# VRFlorian Reactive Cockpit Launcher v0.8 OPEN TEST
# Native Windows/WPF shell. This intentionally does NOT require Python to open,
# so it can detect/help install Python before the Reactive Cockpit engine runs.

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

$script:AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:BackgroundPath = Join-Path $script:AppDir 'launcher_assets\background_main.png'
$script:StoryBackgroundPath = Join-Path $script:AppDir 'launcher_assets\story_mode_missions.jpg'
$script:CockpitBackgrounds = @{
  'corsair' = (Join-Path $script:AppDir 'launcher_assets\Fighter_Corsair.png')
  'kestrel' = (Join-Path $script:AppDir 'launcher_assets\Fighter_Kestrel.png')
  'stealth' = (Join-Path $script:AppDir 'launcher_assets\Stealth Fighter.png')
  'miner' = (Join-Path $script:AppDir 'launcher_assets\Miner.png')
  'explorer' = (Join-Path $script:AppDir 'launcher_assets\Science_explorer.png')
  'anaconda' = (Join-Path $script:AppDir 'launcher_assets\Multi_purp_conda.png')
  'classic' = (Join-Path $script:AppDir 'launcher_assets\Classic_Elite.png')
}
$script:MusicPath = Join-Path $script:AppDir 'launcher_assets\launcher_music.mp3'
$script:BackendPath = Join-Path $script:AppDir 'reactive_launcher_backend.py'
$script:RunBat = Join-Path $script:AppDir 'RUN_REACTIVE_COCKPIT_PLAYER.bat'
$script:CoreOnlyRunBat = Join-Path $script:AppDir 'RUN_REACTIVE_COCKPIT.bat'
$script:ConfigPath = Join-Path $script:AppDir 'reactive_cockpit_config.json'
$script:LauncherStatePath = Join-Path $script:AppDir 'user_data\launcher_state.json'
$script:MusicPreferencePath = Join-Path $script:AppDir 'user_data\launcher_music.json'
$script:PythonExe = $null
$script:MusicEnabled = $true
$script:StoryModeEnabled = $false
$script:StorySelection = 'living_world'
$script:EdhmReady = $false
$script:EdhmInstalled = $false
$script:EdhmExe = $null
$script:UpdateConfigPath = Join-Path $script:AppDir 'update_config.json'
$script:UpdateScriptPath = Join-Path $script:AppDir 'UPDATE_REACTIVE_COCKPIT.ps1'
$script:UpdateManifest = $null
$script:IsBusy = $false
$script:InitialRefreshPending = $true
$script:LastStatusRefresh = [DateTime]::MinValue

function Set-BusyMessage([string]$Message) {
    if (-not $BusyMessage -or [string]::IsNullOrWhiteSpace($Message)) { return }
    $BusyMessage.Text = $Message.ToUpperInvariant()
    if ($Window) { $Window.Dispatcher.Invoke([action]{}, 'Render') }
}

function Set-BusyState([bool]$Busy, [string]$Message = 'Working - a moment please...') {
    $script:IsBusy = $Busy
    if (-not $BusyOverlay -or -not $Window) { return }
    if ($Busy) {
        Set-BusyMessage $Message
        $BusyOverlay.Visibility = 'Visible'
        $Window.Cursor = [System.Windows.Input.Cursors]::Wait
    } else {
        $BusyOverlay.Visibility = 'Collapsed'
        $Window.Cursor = [System.Windows.Input.Cursors]::Arrow
    }
    $Window.Dispatcher.Invoke([action]{}, 'Render')
}

function Invoke-WithBusy([string]$Message, [scriptblock]$Action) {
    # The overlay absorbs clicks, while this guard also protects keyboard or
    # queued events from starting the same long-running action twice.
    if ($script:IsBusy) { return }
    Set-BusyState $true $Message
    try {
        & $Action
    } finally {
        Set-BusyState $false
    }
}

$script:Cockpits = @{
  'corsair' = @{
    Package = 'battle_corsair_snow_vrflorian'; Title = 'CORSAIR FIGHTER REACTIVE COCKPIT';
    Description = @'
The Fighter Corsair Reactive Cockpit is the ultimate upgrade for your Corsair or other fighter, turning your ship into the ultimate battle platform.

Its ultra-clean UX is tailored to the practicalities of combat: 1-v-1 engagements, large-scale war operations and alien combat. It subtly guides your attention to the most important information as the situation changes — from hunting criminals and infiltration to Thargoid conflict zones. Advanced stealth mode and dynamic threat scaling help you through the fog of the most intense battles.

The Fighter Corsair cockpit also features adaptive control systems for any surface vehicles (SRVs) and Ship-Launched Fighters you may carry on board.

FEATURES
• Standard dynamic operation: Docking, Supercruise, scooping, analysis
• Stealth dynamic operation: Silent running, stealth battle, stealth recon
• Combat dynamic operation: Active Combat, Deep Danger, Imminent Destruction
• SRV support
• Ship-Launched Fighter Base + Combat support

HOW TO USE
Reactive Cockpits adapt to your situation and actions dynamically. Once installed on a ship, the optimal cockpit environment triggers autonomously whenever Reactive Cockpit is running.
'@ }
  'kestrel' = @{
    Package = 'kestrel_heart_eater_vrflorian'; Title = 'KESTREL FIGHTER REACTIVE COCKPIT';
    Description = @'
The Kestrel Fighter Reactive Cockpit is the ultimate upgrade for your Kestrel Mk II or other fast combat platform. It turns the ship into a focused, high-performance battle environment built around rapid transitions between navigation, stealth, attack and survival.

The UX is clean and aggressive without becoming visually exhausting. Reactive states subtly change the cockpit as the tactical situation develops, helping you recognize when the ship is calm, actively engaged, badly hurt or approaching destruction.

The Kestrel cockpit also features adaptive support for surface vehicles (SRVs) and Ship-Launched Fighters carried by the mothership.

FEATURES
• Standard dynamic operation: Docking, Supercruise, scooping, analysis
• Stealth dynamic operation: Silent running, stealth battle, stealth recon
• Combat dynamic operation: Active Combat, Deep Danger, Imminent Destruction
• SRV support
• Ship-Launched Fighter Base + Combat support

HOW TO USE
Install it on the ship of your choice. From then on, Reactive Cockpit changes the environment automatically whenever the controller is running.
'@ }
  'stealth' = @{
    Package = 'python_mkii_vrflorian'; Title = 'STEALTH FIGHTER REACTIVE COCKPIT';
    Description = @'
The Python MKII Stealth Fighter Reactive Cockpit is the ultimate upgrade for your Python MKII or other stealthy fighter, turning your ship into the ultimate battle platform.

The UX design is ultra clean and tailored to combat, from 1-v-1 engagements to large-scale war operations and alien conflict. It is designed as intuitive support, subtly guiding your attention during each activity — from hunting criminals and infiltration to Thargoid conflict zones. Advanced stealth mode and dynamic threat scaling help you sneak up on your target and survive the fog of the most intense battles.

The Python MKII cockpit also features adaptive control systems for any SRVs and Ship-Launched Fighters carried on board.

FEATURES
• Standard dynamic operation: Docking, Supercruise, scooping, analysis
• Stealth dynamic operation: Silent running, stealth battle, stealth recon
• Combat dynamic operation: Active Combat, Deep Danger, Imminent Destruction
• SRV support
• Ship-Launched Fighter Base + Combat support

HOW TO USE
Once installed, the cockpit adapts autonomously to the ship's situation and actions whenever Reactive Cockpit is running.
'@ }
  'miner' = @{
    Package = 'type11_rocky5_vrflorian'; Title = 'MINER / INDUSTRIAL REACTIVE COCKPIT';
    Description = @'
The Type 11 Miner Reactive Cockpit is the ultimate upgrade for your Type 11 or other dedicated mining or industrial platform, turning your ship into the ultimate industrial vessel.

The UX design is ultra robust and tailored to mining and deep-core work in remote asteroid belts. It supports the full flow from searching and scanning for core asteroids to precise carrier docking with a loaded cargo hold. It also includes advanced stealth operation for evading pirates, danger-state awareness for hazardous mining environments and the full dynamic battle-awareness suite if you are interdicted on the way home.

The Type 11 Miner cockpit also features adaptive control systems for SRVs and Ship-Launched Fighters.

FEATURES
• Standard dynamic operation: Docking, Supercruise, scooping, analysis, mining
• Stealth dynamic operation: Silent running, stealth battle, stealth recon
• Combat dynamic operation: Active Combat, Deep Danger, Imminent Destruction
• SRV support
• Ship-Launched Fighter Base + Combat support

HOW TO USE
Install it on the chosen ship and leave Reactive Cockpit running. The cockpit reacts automatically to the ship's state.
'@ }
  'explorer' = @{
    Package = 'caspian_explorer_vrflorian'; Title = 'SCIENCE EXPLORER REACTIVE COCKPIT';
    Description = @'
The Explorer Reactive Cockpit is the ultimate upgrade for your Caspian Explorer, Anaconda or other exploration platform, turning your ship into the ultimate scientific deep-space exploration vessel.

The UX is ultra clean, restful to the eyes and tailored to long-range exploration and long periods in space. It is designed as intuitive support, subtly guiding your attention to the most important information during each activity — from continuous hyper-jumping sessions and planetary exploration to stealth operation and, if needed, combat.

The Caspian Explorer Reactive Cockpit also features adaptive control systems for SRVs and Ship-Launched Fighters carried on board.

FEATURES
• Standard dynamic operation: Docking, Supercruise, scooping, analysis
• Stealth dynamic operation: Silent running, stealth battle, stealth recon
• Combat dynamic operation: Active Combat, Deep Danger, Imminent Destruction
• SRV support
• Ship-Launched Fighter Base + Combat support

HOW TO USE
Once installed on a ship, Reactive Cockpit dynamically selects the optimal cockpit environment whenever the controller is running.
'@ }
  'anaconda' = @{
    Package = 'nebula_hopper_anaconda_vrflorian'; Title = 'ANACONDA / NEBULA HOPPER REACTIVE COCKPIT';
    Description = @'
The Nebula Hopper Anaconda Reactive Cockpit is the ultimate upgrade for your Anaconda or other explorer, nebula hopper or mobile home in space, turning your ship into a deep-space exploration vessel and home base.

Its UX is warm, restful to the eyes and specifically tailored to deep exploration and long space travel. It subtly guides your attention through long hyper-jumping sessions, planetary exploration and stealth operation, while still providing the complete dynamic battle-awareness suite if the ship is attacked.

The Nebula Hopper Anaconda cockpit also features adaptive control systems for SRVs and Ship-Launched Fighters carried on board.

FEATURES
• Standard dynamic operation: Docking, Supercruise, scooping, analysis
• Stealth dynamic operation: Silent running, stealth battle, stealth recon
• Combat dynamic operation: Active Combat, Deep Danger, Imminent Destruction
• SRV support
• Ship-Launched Fighter Base + Combat support

HOW TO USE
Install it on the ship of your choice. The cockpit then adapts automatically whenever Reactive Cockpit is running.
'@ }
  'classic' = @{
    Package = 'classic_elite_vrflorian'; Title = 'CLASSIC ELITE REACTIVE COCKPIT';
    Description = @'
The Classic Elite Reactive Cockpit is the ultimate upgrade for those pilots who love the Classic orange ED cockpit for all their ships, but who want to unlock the power of the reactive cockpit for this classic design.

This Reactive Cockpit is designed to be an intuitive support, subtly guiding your attention to the most important information during each activity. From long
continues hyper jumping sessions, to planetary exploration, stealth-mode and (if you are attacked) it is also the perfect tool to get you through the fog of battle victoriously.

Keep it classic, but still gain the awesome power of Reactive Cockpit!

Features:

Standard dynamic operation mode (Docking, Super-cruise, scooping, analysis).
Stealth dynamic operation mode (silent running, Stealth battle. stealth recon).
Combat dynamic operation mode (Active Combat mode, Deep danger mode, imminent destruction mode).
SRV (Exploration mode, Combat mode).
SLV Standard operation mode (exploration, analysis).
SLV(Fighter) Combat mode (Active Combat mode).

How to use?

Reactive Cockpits adapt to your situations and actions dynamically. The optimal cockpit environment for each situation triggers
fully autonomously once a Reactive Cockpit is installed on your ship. Each Cockpit is specific to the ship you installed it on. If you enter
a ship which has a Reactive cockpit installed. It will trigger automatically (as long as Reactive Cockpit is running).
'@ }
}

function Load-MusicPreference {
    $script:MusicEnabled = $true
    if (!(Test-Path -LiteralPath $script:MusicPreferencePath)) { return }
    try {
        $preference = Get-Content -LiteralPath $script:MusicPreferencePath -Raw | ConvertFrom-Json
        if ($preference.music_enabled -is [bool]) {
            $script:MusicEnabled = $preference.music_enabled
        }
    } catch {
        $script:MusicEnabled = $true
    }
}

function Save-MusicPreference {
    $folder = Split-Path -Parent $script:MusicPreferencePath
    if (!(Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder -Force -ErrorAction Stop | Out-Null }
    $preference = @{ music_enabled = [bool]$script:MusicEnabled }
    $temporary = $script:MusicPreferencePath + '.tmp'
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporary, ($preference | ConvertTo-Json), $encoding)
    Move-Item -LiteralPath $temporary -Destination $script:MusicPreferencePath -Force -ErrorAction Stop
}

function Load-StoryModeState {
    $script:StoryModeEnabled = $false
    $script:StorySelection = 'living_world'
    if (!(Test-Path $script:LauncherStatePath)) { return }
    try {
        $state = Get-Content $script:LauncherStatePath -Raw | ConvertFrom-Json
        if ($null -ne $state.story_mode_enabled) {
            $script:StoryModeEnabled = [bool]$state.story_mode_enabled
        }
        if ($state.selected_story_id -in @('living_world','mission_1','mission_2')) {
            $script:StorySelection = [string]$state.selected_story_id
        }
    } catch {
        $script:StoryModeEnabled = $false
        $script:StorySelection = 'living_world'
    }
}

function Save-StoryModeState {
    $folder = Split-Path -Parent $script:LauncherStatePath
    if (!(Test-Path $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
    $state = [ordered]@{
        schema = 'reactive-cockpit.launcher-state.v2'
        story_mode_enabled = [bool]$script:StoryModeEnabled
        selected_story_id = [string]$script:StorySelection
    }
    $temporary = $script:LauncherStatePath + '.tmp'
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporary, ($state | ConvertTo-Json), $encoding)
    Move-Item -LiteralPath $temporary -Destination $script:LauncherStatePath -Force
}

function Refresh-StoryModeButton {
    if (-not $StoryModeButton) { return }
    if ($script:StoryModeEnabled) {
        $StoryModeButton.Content = 'S T O R Y   M O D E   O N'
        $StoryModeButton.ToolTip = 'Story Mode is ON. Missions resume from their saved checkpoint.'
    } else {
        $StoryModeButton.Content = 'S T O R Y   M O D E   O F F'
        $StoryModeButton.ToolTip = 'Story Mode is OFF. Reactive Cockpit runs normally without a story graph.'
    }
    if ($StoryOptionsButton) {
        $StoryOptionsButton.Visibility = $(if ($script:StoryModeEnabled -and $HomePage.Visibility -eq 'Visible') { 'Visible' } else { 'Collapsed' })
    }
}

function Test-PythonRuntime([string]$Executable, [bool]$ViaPy = $false) {
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Executable
        $prefix = $(if ($ViaPy) { '-3 ' } else { '' })
        $psi.Arguments = $prefix + '-c "import sys,json,ctypes; assert sys.version_info >= (3,10); print(sys.executable)"'
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $p = New-Object System.Diagnostics.Process
        $p.StartInfo = $psi
        [void]$p.Start()
        $out = $p.StandardOutput.ReadToEndAsync()
        $err = $p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit(8000)) { try { $p.Kill() } catch {}; return $null }
        $resolved = $out.Result.Trim()
        if ($p.ExitCode -eq 0 -and $resolved -and (Test-Path -LiteralPath $resolved -PathType Leaf)) { return $resolved }
    } catch {}
    return $null
}
function Find-Python {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { $result = Test-PythonRuntime $py.Source $true; if ($result) { return $result } }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike '*\Microsoft\WindowsApps\*') {
        $result = Test-PythonRuntime $python.Source; if ($result) { return $result }
    }
    foreach ($folder in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), (Join-Path $env:LOCALAPPDATA 'Python'), (Join-Path $env:ProgramFiles 'Python*'))) {
        foreach ($candidate in @(Get-ChildItem $folder -Filter python.exe -Recurse -ErrorAction SilentlyContinue)) {
            $result = Test-PythonRuntime $candidate.FullName
            if ($result) { return $result }
        }
    }
    return $null
}

function Get-EdhmState {
    $userData = Join-Path $env:USERPROFILE 'EDHM_UI'
    $ready = (Test-Path (Join-Path $userData 'ODYSS')) -or (Test-Path (Join-Path $userData 'HORIZ'))
    $installRoot = Join-Path $env:LOCALAPPDATA 'EDHM-UI-V3'
    $exe = $null
    if (Test-Path $installRoot) {
        $exe = Get-ChildItem $installRoot -Filter *.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notmatch 'unins|uninstall' } | Select-Object -First 1
    }
    return @{ Ready=$ready; Installed=([bool]$exe -or $ready); Exe=($exe.FullName); UserData=$userData }
}

function Has-Assignments {
    if (!(Test-Path $script:ConfigPath)) { return $false }
    try {
        $cfg = Get-Content $script:ConfigPath -Raw | ConvertFrom-Json
        return ($cfg.cockpit_assignments -and $cfg.cockpit_assignments.Count -gt 0)
    } catch { return $false }
}

function Invoke-Backend([string[]]$BackendArgs) {
    if (-not $script:PythonExe) { throw 'Python is not installed.' }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $script:PythonExe
    $quoted = @('"' + $script:BackendPath.Replace('"','\"') + '"')
    foreach ($a in $BackendArgs) { $quoted += ('"' + $a.Replace('"','\"') + '"') }
    $psi.Arguments = ($quoted -join ' ')
    $psi.WorkingDirectory = $script:AppDir
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    [void]$p.Start()
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    if ($p.ExitCode -ne 0) {
        $backendError = $null
        if ($stdout) {
            try {
                $obj = $stdout.Trim() | ConvertFrom-Json
                if ($obj.error) { $backendError = [string]$obj.error }
            } catch {}
        }
        if ($backendError) { throw $backendError }
        throw ("Reactive Cockpit backend failed.`n" + $stderr + "`n" + $stdout)
    }
    return $stdout.Trim()
}

function Set-StoryChoiceAppearance($Button, [bool]$Active, [bool]$Installed) {
    if (-not $Button) { return }
    if (-not $Installed) {
        $Button.Content = 'U N A V A I L A B L E'
        $Button.Foreground = '#EEEEEE'
        $Button.IsEnabled = $false
        return
    }
    $Button.IsEnabled = $true
    if ($Active) {
        $Button.Content = 'A C T I V E'
        $Button.Foreground = '#109B29'
    } else {
        $Button.Content = 'S E T   A C T I V E'
        $Button.Foreground = '#080808'
    }
}

function Refresh-StorySelector {
    if (-not $script:PythonExe) { return }
    Set-BusyMessage 'Reading Story Mode missions...'
    try {
        $raw = Invoke-Backend -BackendArgs @('story-status')
        $status = $raw | ConvertFrom-Json
        $script:StorySelection = [string]$status.active_story_id
        foreach ($item in @($status.stories)) {
            switch ([string]$item.story_id) {
                'living_world' { Set-StoryChoiceAppearance $LivingWorldActivateButton ([bool]$item.active) ([bool]$item.installed) }
                'mission_1' { Set-StoryChoiceAppearance $Mission1ActivateButton ([bool]$item.active) ([bool]$item.installed) }
                'mission_2' { Set-StoryChoiceAppearance $Mission2ActivateButton ([bool]$item.active) ([bool]$item.installed) }
            }
        }
    } catch {
        $StoryStatusText.Text = 'The story catalogue could not be read.'
        $StoryStatusText.Foreground = '#FF5454'
    }
}

function Select-Story([string]$StoryId) {
    Set-BusyMessage 'Saving the selected mission...'
    try {
        $raw = Invoke-Backend -BackendArgs @('select-story','--story-id',$StoryId)
        $result = $raw | ConvertFrom-Json
        $script:StorySelection = [string]$result.active_story_id
        Save-StoryModeState
        $StoryStatusText.Text = 'Selection saved. It will start or resume when Reactive Cockpit is activated.'
        $StoryStatusText.Foreground = 'White'
        Refresh-StorySelector
    } catch {
        [System.Windows.MessageBox]::Show("The story selection could not be changed.`n`n$($_.Exception.Message)", 'Reactive Cockpit') | Out-Null
    }
}

function Reset-Story([string]$StoryId, [string]$Label) {
    $answer = [System.Windows.MessageBox]::Show(
        "Reset ${Label}?`n`nIts current progress will be preserved in a dated backup, and the mission will begin from the start next time.",
        'Reset mission progress', 'YesNo', 'Warning'
    )
    if ($answer -ne 'Yes') { return }
    Set-BusyMessage "Resetting $Label and preserving its previous progress..."
    try {
        $raw = Invoke-Backend -BackendArgs @('reset-story','--story-id',$StoryId)
        $result = $raw | ConvertFrom-Json
        $count = @($result.backups).Count
        $StoryStatusText.Text = $(if ($count -gt 0) { "$Label reset. Previous progress was preserved." } else { "$Label is already at its starting point." })
        $StoryStatusText.Foreground = 'White'
        Refresh-StorySelector
    } catch {
        [System.Windows.MessageBox]::Show("$Label could not be reset.`n`n$($_.Exception.Message)", 'Reactive Cockpit') | Out-Null
    }
}

function Initialize-ShipyardSilently {
    if ($script:PythonExe -and $script:EdhmReady) {
        try { [void](Invoke-Backend -BackendArgs @('bootstrap')) } catch {}
    }
}

function Refresh-SystemStatus {
    Set-BusyMessage 'Checking Python, EDHM and Shipyard...'
    $script:PythonExe = Find-Python
    $ed = Get-EdhmState
    $script:EdhmReady = $ed.Ready
    $script:EdhmInstalled = $ed.Installed
    $script:EdhmExe = $ed.Exe
    if ($script:EdhmReady -and $script:PythonExe) { Initialize-ShipyardSilently }

    if ($script:PythonExe) {
        $PythonStatus.Text = 'PYTHON installed?  ✓'
        $PythonStatus.Foreground = '#16C41C'
        $PythonInstallButton.Visibility = 'Collapsed'
    } else {
        $PythonStatus.Text = 'PYTHON installed?  ✕'
        $PythonStatus.Foreground = '#FF5454'
        $PythonInstallButton.Visibility = 'Visible'
    }

    if ($script:EdhmReady) {
        $EdhmStatus.Text = 'EDHM ready?  ✓'
        $EdhmStatus.Foreground = '#16C41C'
        $EdhmInstallButton.Visibility = 'Collapsed'
    } elseif ($script:EdhmInstalled) {
        $EdhmStatus.Text = 'EDHM setup complete?  ✕'
        $EdhmStatus.Foreground = '#FFB000'
        $EdhmInstallButton.Content = 'OPEN EDHM SETUP'
        $EdhmInstallButton.Visibility = 'Visible'
    } else {
        $EdhmStatus.Text = 'EDHM installed?  ✕'
        $EdhmStatus.Foreground = '#FF5454'
        $EdhmInstallButton.Content = 'DOWNLOAD / INSTALL EDHM'
        $EdhmInstallButton.Visibility = 'Visible'
    }

    $ActivateButton.Visibility = $(if (Has-Assignments) { 'Visible' } else { 'Collapsed' })
    if ($UpdateStatusText -and [string]::IsNullOrWhiteSpace($UpdateStatusText.Text)) {
        $UpdateStatusText.Text = 'UPDATES: click CHECK FOR UPDATES'
        $UpdateStatusText.Foreground = '#FFD21A'
    }
    $script:LastStatusRefresh = [DateTime]::Now
}

function Get-UpdateConfig {
    if (!(Test-Path -LiteralPath $script:UpdateConfigPath)) { return $null }
    try {
        return Get-Content -LiteralPath $script:UpdateConfigPath -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Convert-ToComparableVersion([string]$Value) {
    $clean = (($Value -replace '^[vV]', '') -replace '[-+].*$', '').Trim()
    try { return [version]$clean } catch { return $null }
}

function Test-IsNewerVersion([string]$Candidate, [string]$Current) {
    if ([string]::IsNullOrWhiteSpace($Current)) { return $true }
    $candidateVersion = Convert-ToComparableVersion $Candidate
    $currentVersion = Convert-ToComparableVersion $Current
    if ($candidateVersion -and $currentVersion) {
        return $candidateVersion -gt $currentVersion
    }
    return $Candidate -ne $Current
}

function Check-ForUpdates([bool]$Prompt = $true) {
    Set-BusyMessage 'Checking for Reactive Cockpit updates...'
    $cfg = Get-UpdateConfig
    $url = if ($cfg) { [string]$cfg.manifest_url } else { '' }
    if ([string]::IsNullOrWhiteSpace($url)) {
        $UpdateStatusText.Text = 'UPDATES: feed not configured yet'
        $UpdateStatusText.Foreground = '#FFD21A'
        return
    }
    try {
        $manifest = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 12 -Headers @{ 'User-Agent' = 'VRFlorian-Reactive-Cockpit' }
        $version = [string]$manifest.version
        $current = if ($cfg) { [string]$cfg.current_version } else { '' }
        if ([string]::IsNullOrWhiteSpace($version) -or [string]::IsNullOrWhiteSpace([string]$manifest.package_url)) {
            throw 'The update feed is missing version or package information.'
        }
        $script:UpdateManifest = $manifest
        if ($version -eq $current) {
            $UpdateStatusText.Text = "UPDATES: current ($current)"
            $UpdateStatusText.Foreground = '#16C41C'
            if ($Prompt) { [System.Windows.MessageBox]::Show("Reactive Cockpit is already up to date ($current).", 'Reactive Cockpit updates') | Out-Null }
            return
        }
        if (!(Test-IsNewerVersion $version $current)) {
            $UpdateStatusText.Text = "UPDATES: current ($current)"
            $UpdateStatusText.Foreground = '#16C41C'
            if ($Prompt) { [System.Windows.MessageBox]::Show("Reactive Cockpit $current is newer than the published update feed ($version).", 'Reactive Cockpit updates') | Out-Null }
            return
        }
        $UpdateStatusText.Text = "UPDATE AVAILABLE: $version"
        $UpdateStatusText.Foreground = '#FFD21A'
        $notes = if ($manifest.notes) { "`n`n$([string]$manifest.notes)" } else { '' }
        if ($Prompt) {
            $answer = [System.Windows.MessageBox]::Show("A new Reactive Cockpit release is available: $version.$notes`n`nDownload and install it now?", 'Reactive Cockpit update', 'YesNo', 'Question')
            if ($answer -eq 'Yes') { Install-Update $manifest }
        }
    } catch {
        $UpdateStatusText.Text = 'UPDATES: check failed'
        $UpdateStatusText.Foreground = '#FF5454'
        if ($Prompt) { [System.Windows.MessageBox]::Show("The update feed could not be checked.`n`n$($_.Exception.Message)", 'Reactive Cockpit updates') | Out-Null }
    }
}

function Install-Update($Manifest) {
    if (!(Test-Path -LiteralPath $script:UpdateScriptPath)) {
        [System.Windows.MessageBox]::Show('The updater is missing from this installation.', 'Reactive Cockpit update') | Out-Null
        return
    }
    try {
        $version = ([string]$Manifest.version) -replace '[^A-Za-z0-9._-]', '_'
        $packagePath = Join-Path $env:TEMP ("reactive-cockpit-update-$version.zip")
        Set-BusyMessage "Downloading update $version - a moment please..."
        $UpdateStatusText.Text = 'DOWNLOADING UPDATE...'
        $UpdateStatusText.Foreground = '#FFD21A'
        Invoke-WebRequest -Uri ([string]$Manifest.package_url) -OutFile $packagePath -UseBasicParsing -TimeoutSec 1800
        $expectedSize = [int64]$Manifest.size_bytes
        if ($expectedSize -gt 0) {
            $actualSize = (Get-Item -LiteralPath $packagePath).Length
            if ($actualSize -ne $expectedSize) { throw 'The downloaded update has an unexpected file size.' }
        }
        $expected = ([string]$Manifest.sha256).Trim().ToUpperInvariant()
        if ([string]::IsNullOrWhiteSpace($expected)) { throw 'The update feed is missing its SHA-256 integrity value.' }
        Set-BusyMessage 'Verifying the downloaded update...'
        $actual = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($actual -ne $expected) { throw 'The downloaded update failed its SHA-256 integrity check.' }
        $quoted = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script:UpdateScriptPath,
            '-PackagePath', $packagePath, '-TargetDir', $script:AppDir,
            '-ParentPid', [string]$PID, '-LaunchAfter'
        ) | ForEach-Object { '"' + ([string]$_).Replace('"', '\"') + '"' }
        Start-Process -FilePath 'powershell.exe' -ArgumentList ($quoted -join ' ') -WorkingDirectory $script:AppDir
        try { $script:Player.Stop(); $script:Player.Close() } catch {}
        $Window.Close()
    } catch {
        $UpdateStatusText.Text = 'UPDATES: download failed'
        $UpdateStatusText.Foreground = '#FF5454'
        [System.Windows.MessageBox]::Show("The update could not be downloaded.`n`n$($_.Exception.Message)", 'Reactive Cockpit update') | Out-Null
    }
}

function Install-Python {
    Set-BusyMessage 'Preparing Python setup...'
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        $answer = [System.Windows.MessageBox]::Show('Reactive Cockpit will ask Windows Package Manager to install Python 3.14. Continue?', 'Install Python', 'YesNo', 'Question')
        if ($answer -eq 'Yes') {
            try {
                Set-BusyMessage 'Installing Python - please leave this window open...'
                Start-Process $winget.Source -ArgumentList 'install -e --id Python.Python.3.14 --accept-package-agreements --accept-source-agreements' -Wait
                Start-Sleep -Seconds 1
                Refresh-SystemStatus
                if ($script:PythonExe) { return }
            } catch {}
        }
    }
    Start-Process 'https://www.python.org/downloads/windows/'
    [System.Windows.MessageBox]::Show('The official Python Windows download page has been opened. Install Python, then click RECHECK.', 'Python') | Out-Null
}

function Install-Or-Open-Edhm {
    Set-BusyMessage 'Preparing EDHM setup...'
    if ($script:EdhmInstalled -and -not $script:EdhmReady -and $script:EdhmExe) {
        Start-Process $script:EdhmExe
        [System.Windows.MessageBox]::Show('Complete EDHM first-run setup, then return here and click RECHECK. Reactive Cockpit will set up Shipyard automatically.', 'EDHM setup') | Out-Null
        return
    }
    try {
        $answer = [System.Windows.MessageBox]::Show('Download the current official EDHM UI v3 Windows installer from BlueMystical GitHub and launch it now?', 'Install EDHM', 'YesNo', 'Question')
        if ($answer -ne 'Yes') { return }
        Set-BusyMessage 'Downloading and installing EDHM - a moment please...'
        $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/BlueMystical/EDHM_UI/releases/latest' -Headers @{ 'User-Agent'='ReactiveCockpitLauncher' }
        $asset = $release.assets | Where-Object { $_.name -eq 'edhm-ui-v3-windows-x64.exe' } | Select-Object -First 1
        if (-not $asset) { throw 'Current release does not expose the expected Windows installer asset.' }
        $dest = Join-Path $env:TEMP 'edhm-ui-v3-windows-x64.exe'
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $dest
        Start-Process $dest -Wait
        Refresh-SystemStatus
    } catch {
        Start-Process 'https://github.com/BlueMystical/EDHM_UI/releases/latest'
        [System.Windows.MessageBox]::Show("Automatic EDHM download could not complete. The official release page has been opened instead.`n`n$($_.Exception.Message)", 'EDHM') | Out-Null
    }
}

function Set-LauncherBackground([string]$Path) {
    if (-not (Test-Path $Path)) { $Path = $script:BackgroundPath }
    if (-not (Test-Path $Path)) { return }
    try {
        $bmp = New-Object System.Windows.Media.Imaging.BitmapImage
        $bmp.BeginInit()
        $bmp.CacheOption = 'OnLoad'
        $bmp.UriSource = New-Object System.Uri($Path)
        $bmp.EndInit()
        $BgBrush.ImageSource = $bmp
    } catch {}
}

function Get-Ships {
    if (-not ($script:PythonExe -and $script:EdhmReady)) { return @() }
    try {
        $raw = Invoke-Backend -BackendArgs @('list-ships')
        if (-not $raw) { return @() }
        $obj = $raw | ConvertFrom-Json
        return @($obj)
    } catch {
        [System.Windows.MessageBox]::Show($_.Exception.Message, 'Could not read ships') | Out-Null
        return @()
    }
}

function Invoke-WindBindings([string]$Command, [string]$Expected = '') {
    if (-not $script:PythonExe) { $script:PythonExe = Find-Python }
    if (-not $script:PythonExe) { throw 'Python is not ready. Install Python and recheck on the home page.' }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $script:PythonExe
    $settings = Join-Path $script:AppDir 'Story Platform\wind_settings.json'
    $argsList = @('-m', 'story_platform.wind_bindings', $Command, '--settings', $settings)
    if ($BindingsFolderBox -and $BindingsFolderBox.Text.Trim()) { $argsList += @('--folder', $BindingsFolderBox.Text.Trim()) }
    if ($Expected) { $argsList += @('--expected', $Expected) }
    $psi.Arguments = (($argsList | ForEach-Object { '"' + $_.Replace('"','\"').TrimEnd('\') + '"' }) -join ' ')
    $psi.WorkingDirectory = Join-Path $script:AppDir 'Story Platform'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    [void]$p.Start()
    $stdoutTask = $p.StandardOutput.ReadToEndAsync()
    $stderrTask = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit(15000)) { try { $p.Kill() } catch {}; throw 'Reading Elite bindings timed out. Please retry.' }
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    if ($p.ExitCode -ne 0) {
        try { $detail = ($stdout | ConvertFrom-Json).error } catch { $detail = $stderr }
        throw ('Could not read keybinds: ' + $detail)
    }
    return ($stdout | ConvertFrom-Json)
}

function Refresh-WindBindings {
    $script:WindBindingReview = Invoke-WindBindings 'review'
    $review = $script:WindBindingReview
    $BindingsFolderBox.Text = [string]$review.folder
    $BindingsRows.ItemsSource = @($review.rows)
    $BindingsConfirmButton.IsEnabled = [bool]$review.ready -and -not [bool]$review.confirmed
    $BindingsConfirmButton.Visibility = $(if ($review.confirmed) { 'Collapsed' } else { 'Visible' })
    $BindingsSkipButton.Visibility = $(if ($review.confirmed) { 'Collapsed' } else { 'Visible' })
    $BindingsUpdateButton.Visibility = $(if ($review.confirmed) { 'Visible' } else { 'Collapsed' })
    $details = @($review.errors) + @($review.warnings)
    if ($details.Count -gt 0) { $BindingsMessage.Text = $details -join "`n" }
    elseif ($review.ready) { $BindingsMessage.Text = 'Please confirm these keybinds are correct.' }
    else { $BindingsMessage.Text = 'Assign a secondary letter, number or numpad digit for each missing action in Elite, then click Read Again.' }
    $BindingsStatusButton.Content = $(if ($review.confirmed) { 'KEYBINDS CONFIRMED  ✓' } else { 'CONFIRM KEYBINDS  ✗' })
    $BindingsStatusButton.Foreground = $(if ($review.confirmed) { '#79D7AA' } else { '#FF6666' })
    $BindingsStatusButton.ToolTip = 'Review the active flight, SRV and on-foot keyboard assignments.'
    return [bool]$review.confirmed
}

function Show-WindBindings {
    Set-LauncherBackground $script:StoryBackgroundPath
    $HomePage.Visibility = 'Collapsed'
    $StoryPage.Visibility = 'Collapsed'
    $CockpitPage.Visibility = 'Collapsed'
    $StoryOptionsButton.Visibility = 'Collapsed'
    $StoryModeButton.Visibility = 'Collapsed'
    $BindingsPage.Visibility = 'Visible'
    $BindingsConfirmButton.IsEnabled = $false
    $BindingsConfirmButton.Visibility = 'Visible'
    $BindingsSkipButton.Visibility = 'Visible'
    $BindingsUpdateButton.Visibility = 'Collapsed'
    try { [void](Refresh-WindBindings) }
    catch { $BindingsMessage.Text = $_.Exception.Message; $BindingsRows.ItemsSource = @() }
}

function Show-Home {
    $BindingsPage.Visibility = 'Collapsed'
    Set-LauncherBackground $script:BackgroundPath
    $CockpitPage.Visibility = 'Collapsed'
    $StoryPage.Visibility = 'Collapsed'
    $HomePage.Visibility = 'Visible'
    $StoryModeButton.Visibility = 'Visible'
    Refresh-SystemStatus
    Refresh-StoryModeButton
}

function Show-Cockpit([string]$Key) {
    $BindingsPage.Visibility = 'Collapsed'
    Set-BusyMessage 'Reading your cockpit and Shipyard setup...'
    Refresh-SystemStatus
    if (-not $script:PythonExe) {
        [System.Windows.MessageBox]::Show('Install Python first. The launcher can do that from the home page.', 'Python required') | Out-Null
        return
    }
    if (-not $script:EdhmReady) {
        [System.Windows.MessageBox]::Show('EDHM must be installed and its first-run setup completed first.', 'EDHM required') | Out-Null
        return
    }
    $script:SelectedCockpitKey = $Key
    $c = $script:Cockpits[$Key]
    if ($script:CockpitBackgrounds.ContainsKey($Key)) {
        Set-LauncherBackground $script:CockpitBackgrounds[$Key]
    } else {
        Set-LauncherBackground $script:BackgroundPath
    }
    $CockpitTitle.Text = $c.Title
    $CockpitDescription.Text = $c.Description
    $InstallLog.Text = ''
    $ShipCombo.Items.Clear()
    $ships = Get-Ships
    foreach ($s in $ships) {
        $item = New-Object PSObject -Property @{ Label=$s.label; Token=$s.token }
        [void]$ShipCombo.Items.Add($item)
    }
    $ShipCombo.DisplayMemberPath = 'Label'
    if ($ShipCombo.Items.Count -gt 0) { $ShipCombo.SelectedIndex = 0 }
    $InstallButton.IsEnabled = ($ShipCombo.Items.Count -gt 0)
    if ($ShipCombo.Items.Count -eq 0) {
        $InstallLog.Text = 'No ships were found in EDHM Shipyard. If EDHM has not seen your ships yet, enter a ship in Elite once with EDHM running, then reopen this cockpit page.'
    }
    $HomePage.Visibility = 'Collapsed'
    $StoryPage.Visibility = 'Collapsed'
    $StoryOptionsButton.Visibility = 'Collapsed'
    $StoryModeButton.Visibility = 'Collapsed'
    $CockpitPage.Visibility = 'Visible'
}

function Show-StoryOptions {
    $BindingsPage.Visibility = 'Collapsed'
    if (-not $script:StoryModeEnabled) { return }
    Set-BusyMessage 'Opening Story Mode options...'
    Refresh-SystemStatus
    if (-not $script:PythonExe) {
        [System.Windows.MessageBox]::Show('Install Python first. The mission selector uses the same safe graph validator as Story Mode.', 'Python required') | Out-Null
        return
    }
    Set-LauncherBackground $script:StoryBackgroundPath
    $HomePage.Visibility = 'Collapsed'
    $CockpitPage.Visibility = 'Collapsed'
    $StoryOptionsButton.Visibility = 'Collapsed'
    $StoryPage.Visibility = 'Visible'
    $StoryModeButton.Visibility = 'Visible'
    $StoryStatusText.Text = ''
    try { [void](Refresh-WindBindings) } catch { $BindingsStatusButton.Content = 'CONFIRM KEYBINDS  ✗'; $BindingsStatusButton.Foreground = '#FF6666' }
    Refresh-StorySelector
}

function Install-SelectedCockpit {
    if ($ShipCombo.SelectedItem -eq $null) { return }
    $c = $script:Cockpits[$script:SelectedCockpitKey]
    $token = $ShipCombo.SelectedItem.Token
    Set-BusyMessage "Installing $($c.Title) - a moment please..."
    $InstallButton.IsEnabled = $false
    $InstallLog.Text = "Installing $($c.Title)...`n`nReactive Cockpit is enabling/updating Shipyard, installing the authored EDHM themes, seeding the runtime cache and assigning the cockpit to the selected ship."
    $Window.Dispatcher.Invoke([action]{}, 'Background')
    try {
        $raw = Invoke-Backend -BackendArgs @('install','--package',[string]$c.Package,'--ship-token',[string]$token)
        $r = $raw | ConvertFrom-Json
        $InstallLog.Text = "INSTALLATION COMPLETE`n`n$($r.package)`n$($r.ship)`n`nBase theme: $($r.base_theme)`nRuntime cache ready.`nShipyard backup: $($r.shipyard_backup)"
        [System.Windows.MessageBox]::Show("Reactive Cockpit installed successfully.`n`n$($r.package)`n$($r.ship)", 'Installation complete') | Out-Null
        Show-Home
    } catch {
        $InstallLog.Text = "INSTALLATION FAILED`n`n$($_.Exception.Message)"
        $InstallButton.IsEnabled = $true
    }
}

[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="VRFlorian Reactive Cockpit" WindowStyle="None" ResizeMode="CanResizeWithGrip"
        WindowStartupLocation="CenterScreen" WindowState="Normal" Width="1280" Height="800"
        Background="Black" MinWidth="1100" MinHeight="720">
  <Window.Resources>
    <Style x:Key="NavButton" TargetType="Button">
      <Setter Property="Background" Value="Transparent"/><Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Foreground" Value="#FFD21A"/><Setter Property="FontWeight" Value="Bold"/>
      <Setter Property="FontSize" Value="28"/><Setter Property="HorizontalContentAlignment" Value="Left"/>
      <Setter Property="Cursor" Value="Hand"/><Setter Property="Padding" Value="0,4"/>
    </Style>
    <Style x:Key="ActionButton" TargetType="Button">
      <Setter Property="Background" Value="#CC0098D8"/><Setter Property="Foreground" Value="White"/>
      <Setter Property="BorderThickness" Value="0"/><Setter Property="FontWeight" Value="Bold"/>
      <Setter Property="FontSize" Value="17"/><Setter Property="Padding" Value="16,10"/><Setter Property="Cursor" Value="Hand"/>
    </Style>
    <Style x:Key="TopButton" TargetType="Button">
      <Setter Property="Background" Value="Transparent"/><Setter Property="Foreground" Value="#00A8E8"/>
      <Setter Property="BorderThickness" Value="0"/><Setter Property="FontWeight" Value="Bold"/>
      <Setter Property="Cursor" Value="Hand"/>
    </Style>
    <Style x:Key="StoryModeButton" TargetType="Button">
      <Setter Property="Background" Value="#D9000000"/><Setter Property="Foreground" Value="#F01818"/>
      <Setter Property="BorderBrush" Value="#D90000"/><Setter Property="BorderThickness" Value="4"/>
      <Setter Property="FontSize" Value="27"/><Setter Property="FontWeight" Value="Normal"/>
      <Setter Property="FontFamily" Value="Segoe UI Light"/><Setter Property="Cursor" Value="Hand"/>
    </Style>
    <Style x:Key="StoryChoiceButton" TargetType="Button">
      <Setter Property="Background" Value="Transparent"/><Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Foreground" Value="#080808"/><Setter Property="FontFamily" Value="Segoe UI Light"/>
      <Setter Property="FontSize" Value="25"/><Setter Property="Cursor" Value="Hand"/>
      <Setter Property="HorizontalContentAlignment" Value="Left"/><Setter Property="Padding" Value="0"/>
    </Style>
    <Style x:Key="StoryResetButton" TargetType="Button" BasedOn="{StaticResource StoryChoiceButton}">
      <Setter Property="FontSize" Value="22"/>
    </Style>
  </Window.Resources>
  <Grid>
    <Grid.Background><ImageBrush x:Name="BgBrush" Stretch="UniformToFill"/></Grid.Background>
    <Rectangle Fill="#22000000"/>

    <Grid HorizontalAlignment="Right" VerticalAlignment="Top" Margin="0,18,24,0" Panel.ZIndex="20">
      <Grid.ColumnDefinitions><ColumnDefinition Width="Auto"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
      <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <Button x:Name="MusicButton" Grid.Row="0" Grid.Column="0" Style="{StaticResource TopButton}" Content="MUSIC ON" FontSize="17" HorizontalAlignment="Right" Margin="0,0,18,0"/>
      <Button x:Name="StoryOptionsButton" Grid.Row="1" Grid.Column="0" Style="{StaticResource TopButton}" Content="STORY MODE OPTIONS" FontSize="17" HorizontalAlignment="Right" Margin="0,1,18,0" Visibility="Collapsed"/>
      <Button x:Name="CloseButton" Grid.Row="0" Grid.RowSpan="2" Grid.Column="1" Style="{StaticResource TopButton}" Content="X" FontSize="38" VerticalAlignment="Top"/>
    </Grid>

    <StackPanel HorizontalAlignment="Left" VerticalAlignment="Top" Margin="250,105,0,0" Panel.ZIndex="10">
      <TextBlock Text="VRFLORIAN REACTIVE COCKPIT" Foreground="White" FontSize="40" FontWeight="Bold"/>
      <TextBlock Text="BY CAPTAIN ROOZ &amp; GUARDIAN ARTHUR" Foreground="#009FE3" FontSize="20" FontWeight="Bold" HorizontalAlignment="Center"/>
    </StackPanel>

    <Grid x:Name="HomePage" Margin="95,200,0,20" HorizontalAlignment="Left" Width="720" Panel.ZIndex="10">
      <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/></Grid.RowDefinitions>
      <StackPanel Grid.Row="0" Margin="0,0,0,20">
        <StackPanel Orientation="Horizontal" Margin="0,0,0,5">
          <TextBlock x:Name="EdhmStatus" Foreground="White" FontSize="20" FontWeight="Bold" VerticalAlignment="Center"/>
          <Button x:Name="EdhmInstallButton" Style="{StaticResource ActionButton}" FontSize="13" Padding="10,5" Margin="14,0,0,0"/>
        </StackPanel>
        <StackPanel Orientation="Horizontal">
          <TextBlock x:Name="PythonStatus" Foreground="White" FontSize="20" FontWeight="Bold" VerticalAlignment="Center"/>
          <Button x:Name="PythonInstallButton" Style="{StaticResource ActionButton}" Content="DOWNLOAD / INSTALL PYTHON" FontSize="13" Padding="10,5" Margin="14,0,0,0"/>
        </StackPanel>
        <Button x:Name="RecheckButton" Style="{StaticResource TopButton}" Content="RECHECK" FontSize="13" HorizontalAlignment="Left" Margin="0,7,0,0"/>
        <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
          <TextBlock x:Name="UpdateStatusText" Foreground="#FFD21A" FontSize="13" FontWeight="Bold" VerticalAlignment="Center"/>
          <Button x:Name="UpdateButton" Style="{StaticResource ActionButton}" Content="CHECK FOR UPDATES" FontSize="12" Padding="10,5" Margin="14,0,0,0"/>
        </StackPanel>
      </StackPanel>

      <StackPanel Grid.Row="1" Orientation="Horizontal" Margin="0,5,0,25">
        <Button x:Name="ActivateButton" Style="{StaticResource ActionButton}" Content="ACTIVATE REACTIVE COCKPIT!" Width="360" HorizontalAlignment="Left"/>
        <TextBlock Text="D I S C O   M O D E&#x0a;CTRL + SHIFT + D" Foreground="#FFD21A" FontSize="13" FontWeight="Bold" Margin="22,4,0,0" LineHeight="20"/>
      </StackPanel>

      <TextBlock Grid.Row="2" Text="CHOOSE YOUR COCKPIT:" Foreground="#00A8E8" FontSize="34" FontWeight="Bold" Margin="0,0,0,15"/>
      <StackPanel Grid.Row="3">
        <Button x:Name="CorsairButton" Style="{StaticResource NavButton}" Content="CORSAIR (FIGHTER)"/>
        <Button x:Name="KestrelButton" Style="{StaticResource NavButton}" Content="KESTREL (FIGHTER)"/>
        <Button x:Name="StealthButton" Style="{StaticResource NavButton}" Content="STEALTH FIGHTER"/>
        <Button x:Name="MinerButton" Style="{StaticResource NavButton}" Content="MINER (INDUSTRIAL SHIP)"/>
        <Button x:Name="ExplorerButton" Style="{StaticResource NavButton}" Content="SCIENCE EXPLORER"/>
        <Button x:Name="AnacondaButton" Style="{StaticResource NavButton}" Content="ANACONDA (MULTI PURPOSE)"/>
        <Button x:Name="ClassicButton" Style="{StaticResource NavButton}" Content="CLASSIC ELITE"/>
      </StackPanel>
    </Grid>

    <Grid x:Name="StoryPage" Visibility="Collapsed" Margin="95,200,60,150" Panel.ZIndex="10">
      <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
      <TextBlock Grid.Row="0" Text="Once a mission is set to active here, jump into Sol to start it."
                 Foreground="White" FontSize="23" FontFamily="Segoe UI Light" HorizontalAlignment="Center" Margin="130,0,0,38">
        <TextBlock.Effect><DropShadowEffect Color="Black" BlurRadius="7" ShadowDepth="1" Opacity="0.9"/></TextBlock.Effect>
      </TextBlock>
      <StackPanel Grid.Row="1" Width="770" HorizontalAlignment="Left">
        <Grid Height="52">
          <Grid.ColumnDefinitions><ColumnDefinition Width="270"/><ColumnDefinition Width="195"/><ColumnDefinition Width="150"/></Grid.ColumnDefinitions>
          <TextBlock Grid.Column="0" Text="L I V I N G   W O R L D" Foreground="#080808" FontFamily="Segoe UI Light" FontSize="25" VerticalAlignment="Center"/>
          <Button x:Name="LivingWorldActivateButton" Grid.Column="1" Style="{StaticResource StoryChoiceButton}" Content="A C T I V E"/>
        </Grid>
        <Grid Height="52">
          <Grid.ColumnDefinitions><ColumnDefinition Width="270"/><ColumnDefinition Width="195"/><ColumnDefinition Width="150"/></Grid.ColumnDefinitions>
          <TextBlock Grid.Column="0" Text="M I S S I O N   1" Foreground="#080808" FontFamily="Segoe UI Light" FontSize="25" VerticalAlignment="Center"/>
          <Button x:Name="Mission1ActivateButton" Grid.Column="1" Style="{StaticResource StoryChoiceButton}" Content="S E T   A C T I V E"/>
          <Button x:Name="Mission1ResetButton" Grid.Column="2" Style="{StaticResource StoryResetButton}" Content="R E S E T"/>
        </Grid>
        <Grid Height="52">
          <Grid.ColumnDefinitions><ColumnDefinition Width="270"/><ColumnDefinition Width="195"/><ColumnDefinition Width="150"/></Grid.ColumnDefinitions>
          <TextBlock Grid.Column="0" Text="M I S S I O N   2" Foreground="#080808" FontFamily="Segoe UI Light" FontSize="25" VerticalAlignment="Center"/>
          <Button x:Name="Mission2ActivateButton" Grid.Column="1" Style="{StaticResource StoryChoiceButton}" Content="S E T   A C T I V E"/>
          <Button x:Name="Mission2ResetButton" Grid.Column="2" Style="{StaticResource StoryResetButton}" Content="R E S E T"/>
        </Grid>
        <Grid Height="52">
          <Grid.ColumnDefinitions><ColumnDefinition Width="270"/><ColumnDefinition Width="195"/><ColumnDefinition Width="150"/></Grid.ColumnDefinitions>
          <TextBlock Grid.Column="0" Text="M I S S I O N   3" Foreground="#080808" FontFamily="Segoe UI Light" FontSize="25" VerticalAlignment="Center"/>
          <TextBlock Grid.Column="1" Text="L O C K E D" Foreground="White" FontFamily="Segoe UI Light" FontSize="25" VerticalAlignment="Center">
            <TextBlock.Effect><DropShadowEffect Color="Black" BlurRadius="6" ShadowDepth="1" Opacity="0.9"/></TextBlock.Effect>
          </TextBlock>
        </Grid>
      </StackPanel>
      <TextBlock x:Name="StoryStatusText" Grid.Row="2" Foreground="White" FontSize="15" Margin="0,14,0,0">
        <TextBlock.Effect><DropShadowEffect Color="Black" BlurRadius="5" ShadowDepth="1" Opacity="0.9"/></TextBlock.Effect>
      </TextBlock>
      <StackPanel Grid.Row="3"><Button x:Name="BindingsStatusButton" Style="{StaticResource TopButton}" Content="CONFIRM KEYBINDS  ✗" Foreground="#FF6666" FontSize="16" HorizontalAlignment="Left" Margin="0,6,0,0"/><Button x:Name="StoryBackButton" Style="{StaticResource TopButton}" Content="← BACK TO COCKPITS" FontSize="16" HorizontalAlignment="Left" Margin="0,10,0,0"/></StackPanel>
    </Grid>

    <Border x:Name="BindingsPage" Visibility="Collapsed" Margin="80,180,80,65" Background="#E5101824" Padding="24" CornerRadius="8" Panel.ZIndex="12">
      <Grid>
        <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
        <StackPanel Grid.Row="0">
          <TextBlock Text="CONFIRM YOUR KEYBINDS" FontSize="28" Foreground="#8ED8FF" Margin="0,0,0,8"/>
          <TextBlock Text="To make some upcoming story elements even more amazing, Reactive Cockpit needs a few alternative keybinds. We use your existing secondary keys as assigned. Where one is missing, any free key we find is proposed below and assigned when you confirm." FontSize="15" Foreground="White" TextWrapping="Wrap"/>
          <TextBlock Text="Please use a regular letter, a number, or a numpad digit (0–9). Special characters and symbol keys are not supported yet. Assign any missing secondary keys of your choice in Elite, then close Elite and click Read Again. You can also skip this step and return later through Story Mode Options." FontSize="12" Foreground="#B6CADB" TextWrapping="Wrap" Margin="0,8,0,0"/>
        </StackPanel>
        <DockPanel Grid.Row="1" Margin="0,12,0,10">
          <Button x:Name="BindingsRefreshButton" DockPanel.Dock="Right" Content="READ AGAIN" Style="{StaticResource ActionButton}" FontSize="14" Margin="12,0,0,0"/>
          <TextBox x:Name="BindingsFolderBox" FontSize="13" VerticalContentAlignment="Center" ToolTip="Elite Options/Bindings folder. Normally detected automatically."/>
        </DockPanel>
        <DataGrid x:Name="BindingsRows" Grid.Row="2" IsReadOnly="True" AutoGenerateColumns="False" CanUserAddRows="False" CanUserDeleteRows="False" CanUserSortColumns="False" HeadersVisibility="Column" RowHeight="30" FontSize="14" Background="#FFF3F8FC" Foreground="#FF162536" GridLinesVisibility="Horizontal" SelectionMode="Single">
          <DataGrid.Columns>
            <DataGridTextColumn Header="Mode" Binding="{Binding mode}" Width="100"/>
            <DataGridTextColumn Header="Action in Elite" Binding="{Binding action}" Width="170"/>
            <DataGridTextColumn Header="Detected key / action needed" Binding="{Binding display}" Width="*"/>
            <DataGridTextColumn Header="Active preset" Binding="{Binding source}" Width="180"/>
          </DataGrid.Columns>
        </DataGrid>
        <ScrollViewer Grid.Row="3" MaxHeight="95" VerticalScrollBarVisibility="Auto" Margin="0,10,0,10">
          <TextBlock x:Name="BindingsMessage" Foreground="#FFD58B" TextWrapping="Wrap" FontSize="14"/>
        </ScrollViewer>
        <StackPanel Grid.Row="4" Orientation="Horizontal">
          <Button x:Name="BindingsConfirmButton" Content="CONFIRM KEYBINDS" Style="{StaticResource ActionButton}" Margin="0,0,18,0"/>
          <Button x:Name="BindingsUpdateButton" Content="UPDATE KEY BINDS" Visibility="Collapsed" Style="{StaticResource ActionButton}" Margin="0,0,18,0"/>
          <Button x:Name="BindingsSkipButton" Content="SKIP / I WILL DO THIS LATER" Style="{StaticResource TopButton}" FontSize="14" Margin="0,0,18,0"/>
          <Button x:Name="BindingsBackButton" Content="BACK TO COCKPITS" Style="{StaticResource TopButton}" FontSize="15"/>
        </StackPanel>
      </Grid>
    </Border>

    <Grid x:Name="CockpitPage" Visibility="Collapsed" Margin="85,205,85,55" Panel.ZIndex="10">
      <Grid.ColumnDefinitions><ColumnDefinition Width="760"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
      <Border Grid.Column="0" Background="#B0000000" Padding="28" CornerRadius="4">
        <Grid>
          <Grid.RowDefinitions><RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/><RowDefinition Height="Auto"/></Grid.RowDefinitions>
          <TextBlock x:Name="CockpitTitle" Grid.Row="0" Foreground="#00A8E8" FontSize="31" FontWeight="Bold" TextWrapping="Wrap" Margin="0,0,0,18"/>
          <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto" Margin="0,0,0,18">
            <TextBlock x:Name="CockpitDescription" Foreground="White" FontSize="16" LineHeight="24" TextWrapping="Wrap"/>
          </ScrollViewer>
          <TextBlock Grid.Row="2" Text="SELECT YOUR SHIP FOR THIS COCKPIT" Foreground="White" FontSize="17" FontWeight="Bold" Margin="0,0,0,6"/>
          <ComboBox x:Name="ShipCombo" Grid.Row="3" Height="38" FontSize="16" Margin="0,0,0,12"/>
          <StackPanel Grid.Row="4">
            <Button x:Name="InstallButton" Style="{StaticResource ActionButton}" Content="INSTALL THIS REACTIVE COCKPIT ON YOUR CHOSEN SHIP NOW!" HorizontalAlignment="Stretch"/>
            <TextBlock Text="Missing a ship? Close this launcher, open EDHM, and board each missing ship in Elite while EDHM stays open. Once the ship(s) appear in EDHM’s Shipyard, reopen this launcher and your missing ships should appear in this list too." FontSize="12" Foreground="#73CFFF" TextWrapping="Wrap" Margin="0,8,0,0"/>
            <TextBox x:Name="InstallLog" Background="#CC050505" Foreground="#D8F4FF" BorderThickness="0" FontFamily="Consolas" FontSize="12" TextWrapping="Wrap" IsReadOnly="True" MinHeight="70" MaxHeight="150" Margin="0,12,0,0" Padding="10" VerticalScrollBarVisibility="Auto"/>
            <Button x:Name="BackButton" Style="{StaticResource TopButton}" Content="← BACK TO COCKPITS" FontSize="16" HorizontalAlignment="Left" Margin="0,10,0,0"/>
          </StackPanel>
        </Grid>
      </Border>
    </Grid>

    <Button x:Name="StoryModeButton" Style="{StaticResource StoryModeButton}"
            Content="S T O R Y   M O D E   O F F" Width="540" Height="82"
            HorizontalAlignment="Right" VerticalAlignment="Bottom" Margin="0,0,125,88"
            Panel.ZIndex="15">
      <Button.Effect>
        <DropShadowEffect Color="#FF0000" BlurRadius="18" ShadowDepth="0" Opacity="0.85"/>
      </Button.Effect>
    </Button>

    <Border x:Name="BusyOverlay" Visibility="Collapsed" Background="#E805090D"
            Panel.ZIndex="1000" IsHitTestVisible="True">
      <Border Width="620" Padding="48,38" HorizontalAlignment="Center" VerticalAlignment="Center"
              Background="#F20A1118" BorderBrush="#00A8E8" BorderThickness="2" CornerRadius="4">
        <StackPanel>
          <TextBlock Text="R E A C T I V E   C O C K P I T" Foreground="#00A8E8"
                     FontSize="16" FontWeight="Bold" HorizontalAlignment="Center"/>
          <TextBlock Text="W O R K I N G" Foreground="#FFD21A" FontSize="34" FontWeight="Bold"
                     HorizontalAlignment="Center" Margin="0,12,0,18"/>
          <ProgressBar Height="8" IsIndeterminate="True" Foreground="#00A8E8"
                       Background="#25323C" BorderThickness="0"/>
          <TextBlock x:Name="BusyMessage" Text="A MOMENT PLEASE..." Foreground="White"
                     FontSize="17" FontWeight="Bold" TextAlignment="Center" TextWrapping="Wrap"
                     Margin="0,20,0,0"/>
          <TextBlock Text="Please leave the launcher open while it completes this task."
                     Foreground="#AFC5D2" FontSize="13" TextAlignment="Center" Margin="0,8,0,0"/>
        </StackPanel>
      </Border>
    </Border>
  </Grid>
</Window>
'@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$Window = [Windows.Markup.XamlReader]::Load($reader)

# Bind named controls into variables.
@('BindingsPage','BindingsRows','BindingsFolderBox','BindingsMessage','BindingsConfirmButton','BindingsRefreshButton','BindingsBackButton','BindingsSkipButton','BindingsUpdateButton','BindingsStatusButton','BgBrush','MusicButton','StoryOptionsButton','CloseButton','HomePage','StoryPage','CockpitPage','StoryModeButton','StoryStatusText','LivingWorldActivateButton','Mission1ActivateButton','Mission1ResetButton','Mission2ActivateButton','Mission2ResetButton','StoryBackButton','EdhmStatus','EdhmInstallButton','PythonStatus','PythonInstallButton','RecheckButton','UpdateStatusText','UpdateButton','ActivateButton','CorsairButton','KestrelButton','StealthButton','MinerButton','ExplorerButton','AnacondaButton','ClassicButton','CockpitTitle','CockpitDescription','ShipCombo','InstallButton','InstallLog','BackButton','BusyOverlay','BusyMessage') | ForEach-Object {
    Set-Variable -Name $_ -Value $Window.FindName($_) -Scope Script
}

# Keep the launcher comfortably windowed on different desktop sizes / DPI scales.
$work = [System.Windows.SystemParameters]::WorkArea
$Window.Width = [Math]::Min(1280, [Math]::Max(1100, $work.Width * 0.80))
$Window.Height = [Math]::Min(800, [Math]::Max(720, $work.Height * 0.86))
Set-LauncherBackground $script:BackgroundPath
Load-StoryModeState
Refresh-StoryModeButton
Load-MusicPreference
if ($script:MusicEnabled) { $MusicButton.Content = 'MUSIC ON' }
else { $MusicButton.Content = 'MUSIC OFF' }

$script:Player = New-Object System.Windows.Media.MediaPlayer
if (Test-Path $script:MusicPath) {
    $script:Player.Open((New-Object System.Uri($script:MusicPath)))
    $script:Player.Volume = 0.30
    $script:Player.Add_MediaEnded({ $script:Player.Position = [TimeSpan]::Zero; if ($script:MusicEnabled) { $script:Player.Play() } })
    if ($script:MusicEnabled) { $script:Player.Play() }
}

$CloseButton.Add_Click({ try { $script:Player.Stop(); $script:Player.Close() } catch {}; $Window.Close() })
$MusicButton.Add_Click({
    $script:MusicEnabled = -not $script:MusicEnabled
    if ($script:MusicEnabled) { $script:Player.Play(); $MusicButton.Content='MUSIC ON' }
    else { $script:Player.Pause(); $MusicButton.Content='MUSIC OFF' }
    try { Save-MusicPreference }
    catch {
        [System.Windows.MessageBox]::Show('Your music choice works for this session, but could not be saved. Check that the Reactive Cockpit folder is writable.', 'Music preference') | Out-Null
    }
})
$RecheckButton.Add_Click({ Invoke-WithBusy 'Rechecking Python, EDHM and Shipyard...' { Refresh-SystemStatus } })
$UpdateButton.Add_Click({ Invoke-WithBusy 'Checking for Reactive Cockpit updates...' { Check-ForUpdates $true } })
$PythonInstallButton.Add_Click({ Invoke-WithBusy 'Preparing Python setup...' { Install-Python } })
$EdhmInstallButton.Add_Click({ Invoke-WithBusy 'Preparing EDHM setup...' { Install-Or-Open-Edhm } })
function Start-ReactiveCockpit {
    $target = $script:RunBat
    if (!(Test-Path $target) -and -not $script:StoryModeEnabled) { $target = $script:CoreOnlyRunBat }
    if (Test-Path $target) {
        Start-Process $target -WorkingDirectory $script:AppDir
        try { $script:Player.Stop(); $script:Player.Close() } catch {}
        $Window.Close()
    }
    else { [System.Windows.MessageBox]::Show('The Reactive Cockpit player runtime was not found.', 'Reactive Cockpit') | Out-Null }
}
$ActivateButton.Add_Click({
    if ($script:StoryModeEnabled) {
        try {
            $setup = Invoke-WindBindings 'activation'
            if (-not ($setup.confirmed -or $setup.deferred)) {
                $script:BindingsActivationPending = $true
                Show-WindBindings
                return
            }
        } catch {
            $script:BindingsActivationPending = $true
            Show-WindBindings
            return
        }
    }
    Start-ReactiveCockpit
})
$StoryModeButton.Add_Click({
    Invoke-WithBusy 'Updating Story Mode...' {
        $script:StoryModeEnabled = -not $script:StoryModeEnabled
        try {
            Save-StoryModeState
            Refresh-StoryModeButton
            if ($script:StoryModeEnabled) {
                Refresh-StorySelector
            } elseif ($StoryPage.Visibility -eq 'Visible') {
                Show-Home
            }
        } catch {
            $script:StoryModeEnabled = -not $script:StoryModeEnabled
            Refresh-StoryModeButton
            [System.Windows.MessageBox]::Show("Story Mode could not be changed.`n`n$($_.Exception.Message)", 'Reactive Cockpit') | Out-Null
        }
    }
})
$BindingsStatusButton.Add_Click({ $script:BindingsActivationPending = $false; Invoke-WithBusy 'Reading Elite keybinds...' { Show-WindBindings } })
$BindingsRefreshButton.Add_Click({ Invoke-WithBusy 'Reading Elite keybinds...' { Show-WindBindings } })
$BindingsBackButton.Add_Click({ $script:BindingsActivationPending = $false; Show-Home })
$BindingsSkipButton.Add_Click({
    Invoke-WithBusy 'Saving your choice...' {
        try {
            $result = Invoke-WindBindings 'defer'
            if (-not $result.deferred) { throw 'Could not save the skip choice.' }
            $startNow = $script:BindingsActivationPending
            $script:BindingsActivationPending = $false
            if ($startNow) { Start-ReactiveCockpit } else { Show-Home }
        } catch { $BindingsMessage.Text = $_.Exception.Message }
    }
})
$BindingsUpdateButton.Add_Click({
    Invoke-WithBusy 'Opening keybind setup...' {
        try {
            [void](Invoke-WindBindings 'reset')
            Show-WindBindings
        } catch { $BindingsMessage.Text = $_.Exception.Message }
    }
})
$BindingsConfirmButton.Add_Click({
    Invoke-WithBusy 'Confirming Elite keybinds...' {
        try {
            $confirmed = Invoke-WindBindings 'apply' ([string]$script:WindBindingReview.fingerprint)
            if (-not $confirmed.confirmed) { throw 'Bindings could not be confirmed. Re-read the current bindings.' }
            [System.Windows.MessageBox]::Show('Controls ready. If you closed Elite for setup, you can now start it again. Return here and activate Reactive Cockpit as normal.', 'Controls ready') | Out-Null
            Show-Home
        } catch { $BindingsMessage.Text = $_.Exception.Message; $BindingsConfirmButton.IsEnabled = $false }
    }
})
$StoryOptionsButton.Add_Click({ Invoke-WithBusy 'Opening Story Mode options...' { Show-StoryOptions } })
$LivingWorldActivateButton.Add_Click({ Invoke-WithBusy 'Activating Living World...' { Select-Story 'living_world' } })
$Mission1ActivateButton.Add_Click({ Invoke-WithBusy 'Activating Mission 1...' { Select-Story 'mission_1' } })
$Mission2ActivateButton.Add_Click({ Invoke-WithBusy 'Activating Mission 2...' { Select-Story 'mission_2' } })
$Mission1ResetButton.Add_Click({ Invoke-WithBusy 'Preparing Mission 1 reset...' { Reset-Story 'mission_1' 'Mission 1' } })
$Mission2ResetButton.Add_Click({ Invoke-WithBusy 'Preparing Mission 2 reset...' { Reset-Story 'mission_2' 'Mission 2' } })
$StoryBackButton.Add_Click({ Invoke-WithBusy 'Returning to cockpit selection...' { Show-Home } })
$CorsairButton.Add_Click({ Invoke-WithBusy 'Reading your Corsair setup...' { Show-Cockpit 'corsair' } })
$KestrelButton.Add_Click({ Invoke-WithBusy 'Reading your Kestrel setup...' { Show-Cockpit 'kestrel' } })
$StealthButton.Add_Click({ Invoke-WithBusy 'Reading your stealth fighter setup...' { Show-Cockpit 'stealth' } })
$MinerButton.Add_Click({ Invoke-WithBusy 'Reading your miner setup...' { Show-Cockpit 'miner' } })
$ExplorerButton.Add_Click({ Invoke-WithBusy 'Reading your explorer setup...' { Show-Cockpit 'explorer' } })
$AnacondaButton.Add_Click({ Invoke-WithBusy 'Reading your Anaconda setup...' { Show-Cockpit 'anaconda' } })
$ClassicButton.Add_Click({ Invoke-WithBusy 'Reading your Classic Elite setup...' { Show-Cockpit 'classic' } })
$BackButton.Add_Click({ Invoke-WithBusy 'Returning to cockpit selection...' { Show-Home } })
$ShipCombo.Add_SelectionChanged({ $InstallButton.IsEnabled = ($ShipCombo.SelectedItem -ne $null) })
$InstallButton.Add_Click({ Invoke-WithBusy 'Installing the selected Reactive Cockpit...' { Install-SelectedCockpit } })
$Window.Add_Activated({
    if (-not $script:InitialRefreshPending -and -not $script:IsBusy) {
        $elapsed = ([DateTime]::Now - $script:LastStatusRefresh).TotalSeconds
        if ($elapsed -ge 15) {
            Invoke-WithBusy 'Refreshing Python, EDHM and Shipyard status...' { Refresh-SystemStatus }
        }
    }
})
$Window.Add_ContentRendered({
    if ($script:InitialRefreshPending) {
        $script:InitialRefreshPending = $false
        Invoke-WithBusy 'Starting launcher - checking Python, EDHM and Shipyard...' { Refresh-SystemStatus }
    }
})

[void]$Window.ShowDialog()
