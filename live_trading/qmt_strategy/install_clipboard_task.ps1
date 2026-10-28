param(
    [string]$BridgeRoot = 'D:\qmt_bridge',
    [string]$Strategy = 'alla_v4_ladder_k1h5_postclose_real',
    [string]$TaskName = 'QlibClipboardRelay',
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
if ((Get-TimeZone).Id -ne 'China Standard Time') {
    throw 'Set Windows timezone to China Standard Time before registering the 14:54 task.'
}
if (!(Test-Path -LiteralPath (Join-Path $BridgeRoot 'inbox') -PathType Container)) {
    throw 'BridgeRoot must be the Windows shared directory containing inbox.'
}
foreach ($file in @('qmt_clipboard.py', 'clipboard_relay.py', 'clipboard_auto.py')) {
    if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot $file) -PathType Leaf)) {
        throw "Missing deployment file: $file"
    }
}
if (!$PythonExe) {
    $PythonExe = (& py -c 'import sys; print(sys.executable)').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Python launcher failed.' }
}
if (!(Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Python executable not found.' }
if ($Strategy -notmatch '^[A-Za-z0-9_-]{1,64}$') { throw 'Invalid strategy ID.' }
foreach ($value in @($BridgeRoot, $PSScriptRoot, $PythonExe)) {
    if ($value.Contains('"')) { throw 'Paths containing double quotes are unsupported.' }
}
$root = (Resolve-Path -LiteralPath $BridgeRoot).Path
$script = Join-Path $PSScriptRoot 'clipboard_relay.py'
$output = Join-Path $PSScriptRoot 'output'
$arguments = '-u "{0}" auto --bridge-root "{1}" --strategy "{2}" --output-dir "{3}" --timeout 30' -f $script, $root, $Strategy, $output
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $arguments -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '14:54'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Qlib clipboard relay: signal files to QMT, fills and account snapshots back to Mac.' -Force
Write-Host "Installed $TaskName for $user at 14:54 on weekdays (Windows local time)."
Write-Host 'Clipboard response timeout: 30 seconds (same as the verified deployment check).'
Write-Host 'Windows timezone must be China Standard Time; keep this user logged in and QMT running.'
Write-Host 'The task was registered, not started. Use auto --check for a read-only deployment check.'
