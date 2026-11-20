<#
.SYNOPSIS
  Block until the 3ds Max render-done signal file appears, then print it and exit.

.DESCRIPTION
  The native MCP bridge writes <job>.done.json at Max's NOTIFY_POST_RENDER event.
  This watcher uses a FileSystemWatcher (event-driven, NOT a polling loop) so it
  sits idle at ~0% CPU and never touches 3ds Max. The agent runs it in the
  background; when the render finishes the script exits, which pings the agent to
  continue the automation (swap the next colour, start the next render, etc.).

.PARAMETER SignalPath
  Full path to the expected <job_id>.done.json signal file.

.PARAMETER TimeoutSec
  Optional cap. 0 (default) waits indefinitely.

.OUTPUTS
  The signal JSON on success (exit 0); {"status":"timeout"} on cap (exit 2).
#>
param(
    [Parameter(Mandatory = $true)][string]$SignalPath,
    [int]$TimeoutSec = 0
)

$ErrorActionPreference = 'Stop'

$dir  = Split-Path -Parent $SignalPath
$file = Split-Path -Leaf   $SignalPath

if (-not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

function Emit-Signal {
    # Brief settle so we never read a half-flushed file (the bridge writes via
    # tmp+rename, so this is just belt-and-suspenders).
    Start-Sleep -Milliseconds 120
    Get-Content -LiteralPath $SignalPath -Raw
}

# Race guard: the signal may already exist (render finished before we armed).
if (Test-Path -LiteralPath $SignalPath) {
    Emit-Signal
    exit 0
}

$fsw = New-Object System.IO.FileSystemWatcher
$fsw.Path = $dir
$fsw.Filter = $file
$fsw.IncludeSubdirectories = $false
$fsw.NotifyFilter = [System.IO.NotifyFilters]::FileName -bor [System.IO.NotifyFilters]::LastWrite
$fsw.EnableRaisingEvents = $true

try {
    $changeTypes = [System.IO.WatcherChangeTypes]::Created `
        -bor [System.IO.WatcherChangeTypes]::Changed `
        -bor [System.IO.WatcherChangeTypes]::Renamed
    $timeoutMs = if ($TimeoutSec -gt 0) { $TimeoutSec * 1000 } else { [System.Int32]::MaxValue }

    while ($true) {
        $r = $fsw.WaitForChanged($changeTypes, $timeoutMs)
        if ($r.TimedOut) {
            if (Test-Path -LiteralPath $SignalPath) { Emit-Signal; exit 0 }
            Write-Output '{"status":"timeout"}'
            exit 2
        }
        if (Test-Path -LiteralPath $SignalPath) {
            Emit-Signal
            exit 0
        }
        # A change fired for something else under the filter — keep waiting.
    }
}
finally {
    $fsw.EnableRaisingEvents = $false
    $fsw.Dispose()
}
