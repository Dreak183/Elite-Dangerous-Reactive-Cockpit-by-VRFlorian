param(
    [Parameter(Mandatory = $true)][string]$PackagePath,
    [Parameter(Mandatory = $true)][string]$TargetDir,
    [int]$ParentPid = 0,
    [switch]$LaunchAfter
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework

function Is-Alive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Is-Protected([string]$RelativePath) {
    $normalized = $RelativePath.Replace('/', '\').TrimStart('\')
    foreach ($protected in @(
        'user_data',
        'runtime',
        'logs',
        'reactive_cockpit_config.json',
        'Story Platform\wind_settings.json',
        'Story Platform\runtime',
        'Story Platform\platform_config.json'
    )) {
        if ($normalized -eq $protected -or $normalized.StartsWith($protected + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

$target = (Resolve-Path -LiteralPath $TargetDir).Path
$package = (Resolve-Path -LiteralPath $PackagePath).Path
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$staging = Join-Path $env:TEMP ('reactive-cockpit-update-' + [Guid]::NewGuid().ToString('N'))
$backup = Join-Path $env:TEMP ('reactive-cockpit-backup-' + $stamp + '-' + [Guid]::NewGuid().ToString('N'))
$script:CopiedFiles = New-Object System.Collections.Generic.List[string]
$script:NewFiles = New-Object System.Collections.Generic.List[string]
$script:BackupFiles = New-Object System.Collections.Generic.List[object]

try {
    $deadline = (Get-Date).AddSeconds(120)
    while ((Is-Alive $ParentPid) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (Is-Alive $ParentPid) {
        throw 'The launcher did not close in time. The update was not applied.'
    }

    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    Expand-Archive -LiteralPath $package -DestinationPath $staging -Force

    $source = Join-Path $staging 'Reactive Cockpit Files'
    if (!(Test-Path -LiteralPath $source -PathType Container)) {
        $nested = Get-ChildItem -LiteralPath $staging -Directory -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq 'Reactive Cockpit Files' } | Select-Object -First 1
        if ($nested) { $source = $nested.FullName }
    }
    if (!(Test-Path -LiteralPath $source -PathType Container)) {
        throw 'The update package does not contain a Reactive Cockpit Files payload.'
    }
    if (!(Test-Path -LiteralPath (Join-Path $source 'PLAYER_BUILD.marker') -PathType Leaf)) {
        throw 'The update package is not a recognised player-build package.'
    }

    function Copy-UpdateTree([string]$SourceDir, [string]$TargetRoot, [string]$RelativeRoot) {
        foreach ($item in Get-ChildItem -LiteralPath $SourceDir -Force) {
            $relative = if ($RelativeRoot) { Join-Path $RelativeRoot $item.Name } else { $item.Name }
            if (Is-Protected $relative) { continue }
            $destination = Join-Path $TargetRoot $relative
            if ($item.PSIsContainer) {
                New-Item -ItemType Directory -Path $destination -Force | Out-Null
                Copy-UpdateTree $item.FullName $TargetRoot $relative
                continue
            }
            $parent = Split-Path -Parent $destination
            if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                $backupPath = Join-Path $backup $relative
                $backupParent = Split-Path -Parent $backupPath
                New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
                Copy-Item -LiteralPath $destination -Destination $backupPath -Force
                $script:BackupFiles.Add([PSCustomObject]@{ Target = $destination; Backup = $backupPath })
            } else {
                $script:NewFiles.Add($destination)
            }
            Copy-Item -LiteralPath $item.FullName -Destination $destination -Force
            $script:CopiedFiles.Add($destination)
        }
    }

    Copy-UpdateTree $source $target ''
    Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $package -Force -ErrorAction SilentlyContinue

    if ($LaunchAfter) {
        $launcher = Join-Path $target 'START_REACTIVE_COCKPIT_LAUNCHER.bat'
        if (Test-Path -LiteralPath $launcher) {
            Start-Process -FilePath 'cmd.exe' -ArgumentList ('/c "' + $launcher + '"') -WorkingDirectory $target
        }
    }
} catch {
    foreach ($path in $script:CopiedFiles) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
    foreach ($item in $script:BackupFiles) {
        if (Test-Path -LiteralPath $item.Backup -PathType Leaf) {
            $parent = Split-Path -Parent $item.Target
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
            Copy-Item -LiteralPath $item.Backup -Destination $item.Target -Force
        }
    }
    foreach ($path in $script:NewFiles) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    [System.Windows.MessageBox]::Show("The Reactive Cockpit update could not be applied.`n`n$($_.Exception.Message)", 'Reactive Cockpit update') | Out-Null
    exit 1
}
