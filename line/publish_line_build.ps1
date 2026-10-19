param()

$ErrorActionPreference = 'Stop'
$projectPath = [System.IO.Path]::GetFullPath($PSScriptRoot)
$stagePath = [System.IO.Path]::GetFullPath((Join-Path $projectPath 'build\stage'))
$distPath = [System.IO.Path]::GetFullPath((Join-Path $projectPath 'dist'))
$previousPath = [System.IO.Path]::GetFullPath((Join-Path $projectPath 'dist.previous'))
$distPrefix = $distPath.TrimEnd('\') + '\'
$mutex = [System.Threading.Mutex]::new($false, 'Local\LINEStatusChanger')
$ownsMutex = $false
$movedOldDist = $false

function Get-Sha256Hex([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash($stream)
        return ([System.BitConverter]::ToString($digest)).Replace('-', '')
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function Test-LineBuildManifest([string]$Directory) {
    $exePath = Join-Path $Directory 'line-status-changer.exe'
    $manifestPath = Join-Path $Directory 'SHA256SUMS.txt'
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        return $false
    }
    $lines = @(Get-Content -LiteralPath $manifestPath -Encoding ASCII)
    if ($lines.Count -ne 1 -or $lines[0] -notmatch '^([0-9a-fA-F]{64})  line-status-changer\.exe$') {
        return $false
    }
    $expected = $Matches[1]
    $actual = Get-Sha256Hex $exePath
    return $actual.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)
}

try {
    try { $ownsMutex = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) {
        throw 'Another LINE Status Changer build is publishing.'
    }

    $stagedExe = Join-Path $stagePath 'line-status-changer.exe'
    $stagedConfig = Join-Path $stagePath 'line_config.json'
    if (-not (Test-Path -LiteralPath $stagedExe -PathType Leaf) -or
        (Get-Item -LiteralPath $stagedExe).Length -lt 1MB -or
        -not (Test-LineBuildManifest $stagePath) -or
        -not (Test-Path -LiteralPath $stagedConfig -PathType Leaf)) {
        throw 'Staged EXE, checksum manifest, or config is missing, invalid, or incomplete; existing dist was not changed.'
    }

    $targets = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='line-status-changer.exe'" |
        Where-Object {
            $_.ExecutablePath -and
            $_.ExecutablePath.StartsWith($distPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        })
    foreach ($process in $targets) {
        Write-Host ("End task: line-status-changer.exe (PID {0})" -f $process.ProcessId)
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    }

    if ($targets.Count -gt 0) {
        $deadline = [DateTime]::UtcNow.AddSeconds(5)
        do {
            Start-Sleep -Milliseconds 150
            $remaining = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='line-status-changer.exe'" |
                Where-Object {
                    $_.ExecutablePath -and
                    $_.ExecutablePath.StartsWith($distPrefix, [System.StringComparison]::OrdinalIgnoreCase)
                })
        } while ($remaining.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline)
        if ($remaining.Count -gt 0) {
            throw 'Could not end all LINE Status Changer processes; existing dist was not changed.'
        }
    }

    # The GUI autosaves drafts. Refresh the staged config after ending the old
    # process so the publish cannot replace newer settings with an earlier copy.
    $currentConfig = Join-Path $distPath 'line_config.json'
    if (Test-Path -LiteralPath $currentConfig -PathType Leaf) {
        Copy-Item -LiteralPath $currentConfig -Destination $stagedConfig -Force -ErrorAction Stop
    }

    if (Test-Path -LiteralPath $previousPath) {
        if (-not (Test-Path -LiteralPath $distPath)) {
            Move-Item -LiteralPath $previousPath -Destination $distPath -ErrorAction Stop
        }
        else {
            Remove-Item -LiteralPath $previousPath -Recurse -Force
        }
    }
    if (Test-Path -LiteralPath $distPath) {
        Move-Item -LiteralPath $distPath -Destination $previousPath -ErrorAction Stop
        $movedOldDist = $true
    }
    try {
        Move-Item -LiteralPath $stagePath -Destination $distPath -ErrorAction Stop
        if (-not (Test-LineBuildManifest $distPath) -or
            -not (Test-Path -LiteralPath (Join-Path $distPath 'line_config.json') -PathType Leaf)) {
            throw 'Published output verification failed.'
        }
    }
    catch {
        if (Test-Path -LiteralPath $distPath) {
            Remove-Item -LiteralPath $distPath -Recurse -Force
        }
        if ($movedOldDist -and (Test-Path -LiteralPath $previousPath)) {
            Move-Item -LiteralPath $previousPath -Destination $distPath -ErrorAction Stop
            $movedOldDist = $false
        }
        throw
    }

    if ($movedOldDist -and (Test-Path -LiteralPath $previousPath)) {
        try { Remove-Item -LiteralPath $previousPath -Recurse -Force }
        catch { Write-Warning "New build is published; old dist backup could not be removed: $_" }
    }
    Write-Host 'Published LINE Status Changer build; executable SHA-256 manifest verified.'
}
finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
