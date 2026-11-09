param([string]$BridgeRoot = 'D:\qmt_bridge')
$ErrorActionPreference = 'Stop'
$taskName = 'QlibClockGuard'
$description = 'Qlib clock guard: verified multi-source NTP over ephemeral UDP ports; no trading.'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run the installer in Administrator PowerShell.'
}
if ((Get-TimeZone).Id -ne 'China Standard Time') { throw 'China Standard Time is required.' }
$source = Join-Path $PSScriptRoot 'windows_clock_guard.ps1'
$tests = Join-Path $PSScriptRoot 'test_windows_clock_guard.ps1'
if (-not (Test-Path -LiteralPath $tests)) {
    $tests = Join-Path $PSScriptRoot '../../tests/live_trading/test_windows_clock_guard.ps1'
}
if (-not (Test-Path -LiteralPath $source) -or -not (Test-Path -LiteralPath $tests)) { throw 'Clock guard and tests must be present.' }
& $tests -ScriptPath $source
if (-not (Test-Path -LiteralPath (Join-Path $BridgeRoot 'inbox') -PathType Container)) { throw 'Invalid bridge root.' }
$output = Join-Path (Resolve-Path -LiteralPath $BridgeRoot).Path 'logs\clock_sync'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$targetRoot = Join-Path $env:ProgramFiles 'QlibClockGuard'
$marker = Join-Path $targetRoot 'installation.json'
$target = Join-Path $targetRoot 'windows_clock_guard.ps1'
foreach ($path in @($targetRoot, $marker, $target)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Reparse points are not allowed in the privileged installation: $path" }
}
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask -and $existingTask.Description -ne $description) { throw 'A different task already uses QlibClockGuard.' }
if ($existingTask -and $existingTask.State -eq 'Running') { throw 'Clock guard is running; retry installation after it finishes.' }
if ((Test-Path -LiteralPath $targetRoot) -and -not (Test-Path -LiteralPath $marker)) {
    throw 'Existing target directory has no clock guard installation marker; leaving it unchanged.'
}
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
# Privileged scheduled code lives in a protected directory, never on the shared drive.
$acl = New-Object Security.AccessControl.DirectorySecurity
$adminSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
$acl.SetOwner($adminSid)
$acl.SetAccessRuleProtection($true, $false)
foreach ($entry in @(@('S-1-5-18','FullControl'), @('S-1-5-32-544','FullControl'), @('S-1-5-32-545','ReadAndExecute'))) {
    $sid = New-Object Security.Principal.SecurityIdentifier($entry[0])
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, $entry[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $targetRoot -AclObject $acl
function Set-ProtectedClockFileAcl([string]$File) {
    $fileAcl = New-Object Security.AccessControl.FileSecurity
    $fileAcl.SetOwner($adminSid)
    $fileAcl.SetAccessRuleProtection($true, $false)
    foreach ($entry in @(@('S-1-5-18','FullControl'), @('S-1-5-32-544','FullControl'), @('S-1-5-32-545','ReadAndExecute'))) {
        $sid = New-Object Security.Principal.SecurityIdentifier($entry[0])
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, $entry[1], 'Allow')
        $fileAcl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $File -AclObject $fileAcl
}
Copy-Item -LiteralPath $source -Destination $target -Force
Set-ProtectedClockFileAcl $target
$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $sourceHash) { throw 'Installed script hash mismatch.' }
$manifest = @{ installed_at=[DateTime]::UtcNow.ToString('o'); source_sha256=$sourceHash; source=$source; installed=$target; task=$taskName; tests_passed=$true; tested_powershell=$PSVersionTable.PSVersion.ToString() }
$manifest | ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding UTF8
Set-ProtectedClockFileAcl $marker
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
foreach ($path in @($target, $output)) { if ($path.Contains('"')) { throw 'Double quotes in paths are unsupported.' } }
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Apply -OutputDirectory "{1}"' -f $target, $output
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $targetRoot
$startup = New-ScheduledTaskTrigger -AtStartup
$startup.Delay = 'PT2M'
$triggers = @((New-ScheduledTaskTrigger -Daily -At '09:00'), (New-ScheduledTaskTrigger -Daily -At '14:40'), $startup)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Principal $taskPrincipal `
    -Settings $settings -Description $description -Force | Out-Null
Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $output 'scheduled-task.xml') -Encoding UTF8
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'installation.json') -Encoding UTF8
$statusPath = Join-Path $output 'clock.status.json'
$previousRunId = $null
if (Test-Path -LiteralPath $statusPath) {
    try { $previousRunId = (Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json).run_id } catch { }
}
$previousTaskRun = (Get-ScheduledTaskInfo -TaskName $taskName).LastRunTime
$startedUtc = [DateTime]::UtcNow
$wait = [Diagnostics.Stopwatch]::StartNew()
Start-ScheduledTask -TaskName $taskName
$verified = $false
while ($wait.Elapsed.TotalSeconds -lt 90) {
    Start-Sleep -Milliseconds 500
    if (-not (Test-Path -LiteralPath $statusPath)) { continue }
    $report = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
    if (-not $report.run_id -or $report.run_id -eq $previousRunId) { continue }
    if ([DateTime]::Parse($report.started_at).ToUniversalTime() -lt $startedUtc.AddSeconds(-1)) { continue }
    if ($report.status -ne 'SYNCHRONIZED') { throw "Clock guard did not verify synchronization: $($report.error)" }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    if ($info.LastRunTime -eq $previousTaskRun) { continue }
    if ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running') { continue }
    if ($info.LastTaskResult -ne 0) { throw "Clock task returned $($info.LastTaskResult)." }
    $verified = $true
    $report | Select-Object status,offset_before_seconds,offset_after_seconds,adjusted,consensus_peers,verification_peers | ConvertTo-Json -Depth 4
    $info | Select-Object LastRunTime,LastTaskResult,NextRunTime | Format-List
    break
}
if (-not $verified) { throw 'Task installed, but synchronization was not verified within 90 seconds. Inspect clock.status.json and task history.' }
Write-Output 'QlibClockGuard installed and verified under SYSTEM: daily 09:00, 14:40 and two minutes after startup.'
