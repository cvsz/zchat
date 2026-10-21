$ErrorActionPreference = 'Stop'

$distPath = [System.IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $PSScriptRoot) 'dist'))
$distPrefix = $distPath.TrimEnd('\') + '\'
$names = @(
    'camfrog-auto.exe',
    'camfrog-auto-gui.exe',
    'zcfato.exe',
    'room-control.exe',
    'chat-im-private.exe',
    'status-random.exe',
    'status-marquee.exe',
    'status-changer.exe',
    'im-autoreply.exe',
    'web-status.exe',
    'name-color-changer.exe',
    'icon-changer.exe',
    'name-changer.exe',
    'zchat-client.exe'
)

function Get-ProjectProcesses {
    @(Get-WmiObject -Class Win32_Process | Where-Object {
        ($names -contains $_.Name) -and $_.ExecutablePath -and
        $_.ExecutablePath.StartsWith($distPrefix, [System.StringComparison]::OrdinalIgnoreCase)
    })
}

$targets = @(Get-ProjectProcesses)
foreach ($process in $targets) {
    Write-Host ("End task: {0} (PID {1})" -f $process.Name, $process.ProcessId)
    Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
}

if ($targets.Count -gt 0) {
    Start-Sleep -Milliseconds 500
    $remaining = @(Get-ProjectProcesses)
    if ($remaining.Count -gt 0) {
        $pids = ($remaining | ForEach-Object { $_.ProcessId }) -join ', '
        Write-Error "Could not end project process(es): $pids"
        exit 1
    }
}

exit 0
