param([string]$ScriptPath = (Join-Path $PSScriptRoot '../../live_trading/qmt_strategy/windows_clock_guard.ps1'))
$ErrorActionPreference = 'Stop'
. $ScriptPath -LibraryOnly
$script:checks = 0
function Assert-True($Value, [string]$Message) {
    if (-not $Value) { throw "FAILED: $Message" }
    $script:checks++
}
function Assert-Throws([scriptblock]$Action, [string]$Message) {
    $threw = $false
    try { & $Action | Out-Null } catch { $threw = $true }
    Assert-True $threw $Message
}
$now = [DateTime]::SpecifyKind([DateTime]'2026-11-09T15:24:00', [DateTimeKind]::Utc)
function Sample($Peer, $Offset, $Delay = 0.1, $Age = 1) {
    [pscustomobject]@{ Peer=$Peer; OffsetSeconds=$Offset; DelaySeconds=$Delay; CollectedAtUtc=$now.AddSeconds(-$Age) }
}
$lines = @(
    'RdtscStart,RdtscEnd,FileTime,RoundtripDelay,NtpOffset',
    '19044430728314653, 19044431501760775, 134387114346919599, +00.2146899, +07.8102274',
    '19044440520756553, 19044444166700221, 134387114374120787, error: 0x800705B4'
)
$parsed = @(ConvertFrom-ClockStripchart -Lines $lines -Peer 'cloudflare')
Assert-True ($parsed.Count -eq 1) 'ignore headers and timeout rows'
Assert-True ([Math]::Abs($parsed[0].OffsetSeconds - 7.8102274) -lt 0.0000001) 'parse actual Windows offset'
Assert-True ([Math]::Abs($parsed[0].DelaySeconds - 0.2146899) -lt 0.0000001) 'parse actual Windows RTT'
Assert-True ($parsed[0].CollectedAtUtc.ToString('o') -eq '2026-11-09T15:23:54.6919599Z') 'decode FILETIME as UTC'
$negative = @(ConvertFrom-ClockStripchart -Lines @('1, 2, 134387114346919599, +00.1000000, -00.2000000') -Peer 'a')
Assert-True ($negative[0].OffsetSeconds -eq -0.2) 'negative offset'
$good = @((Sample 'a' 7.83), (Sample 'b' 7.84), (Sample 'c' 7.82))
$choice = Get-ClockConsensus -Samples $good -NowUtc $now
Assert-True ([Math]::Abs($choice.OffsetSeconds - 7.83) -lt 0.000001 -and $choice.Peers.Count -eq 3) 'three-source consensus'
$choice = Get-ClockConsensus -Samples @((Sample 'a' 7.83), (Sample 'b' 7.84), (Sample 'c' -10)) -NowUtc $now
Assert-True ($choice.Peers.Count -eq 2 -and [Math]::Abs($choice.OffsetSeconds - 7.835) -lt 0.000001) 'reject an outlier'
Assert-Throws { Get-ClockConsensus -Samples @((Sample 'a' 1), (Sample 'a' 1.01)) -NowUtc $now } 'one source cannot form consensus'
Assert-Throws { Get-ClockConsensus -Samples @((Sample 'a' 1), (Sample 'b' 3)) -NowUtc $now } 'disagreement blocks correction'
Assert-Throws { Get-ClockConsensus -Samples @((Sample 'a' 0), (Sample 'b' 0.2), (Sample 'c' 0.4)) -NowUtc $now } 'ambiguous overlapping pairs block correction'
Assert-Throws { Get-ClockConsensus -Samples @((Sample 'a' 1 0.1 60), (Sample 'b' 1 0.1 60)) -NowUtc $now } 'old samples rejected'
Assert-Throws { Get-ClockConsensus -Samples @((Sample 'a' 1 0.1 -3), (Sample 'b' 1 0.1 -3)) -NowUtc $now } 'future samples rejected'
Assert-Throws { Get-ClockConsensus -Samples @((Sample 'a' 1 2), (Sample 'b' 1 2)) -NowUtc $now } 'slow samples rejected'
Assert-Throws { Get-ClockConsensus -Samples @((Sample 'a' ([Double]::NaN)), (Sample 'b' 1)) -NowUtc $now } 'nonfinite samples rejected'
$choice = Get-ClockConsensus -Samples @((Sample 'a' 5 0.9), (Sample 'a' 1 0.1), (Sample 'b' 1.01)) -NowUtc $now
Assert-True ([Math]::Abs($choice.OffsetSeconds - 1.005) -lt 0.000001) 'choose lowest-delay reading per source'
$choice = Get-ClockConsensus -Samples @((Sample 'a' 0), (Sample 'b' 0), (Sample 'c' 0)) -NowUtc $now
Assert-True ($choice.OffsetSeconds -eq 0 -and $choice.Peers.Count -eq 3) 'identical offsets form one consensus'
Assert-Throws { Assert-ClockAdjustmentAllowed -LocalNow ([DateTime]'2026-11-09T14:55:00') -OffsetSeconds 1 } 'do not step clock during execution'
Assert-Throws { Assert-ClockAdjustmentAllowed -LocalNow ([DateTime]'2026-11-09T14:49:55') -OffsetSeconds 10 } 'do not step into execution window'
Assert-Throws { Assert-ClockAdjustmentAllowed -LocalNow ([DateTime]'2026-11-09T15:05:05') -OffsetSeconds -10 } 'do not step back into execution window'
Assert-Throws { Assert-ClockAdjustmentAllowed -LocalNow ([DateTime]'2026-11-09T09:00:00') -OffsetSeconds 61 } 'large adjustments require operator investigation'
Assert-ClockAdjustmentAllowed -LocalNow ([DateTime]'2026-11-09T14:40:00') -OffsetSeconds 7.83
Assert-True $true 'pre-execution correction permitted'
function Get-TimeZone { [pscustomobject]@{ Id='China Standard Time' } }
function Set-Date { param($Date) $script:adjustments++ }
function Get-ClockReadings {
    $offset = $script:offsetQueue[$script:readCount]
    $script:readCount++
    if ($script:jumpOnRead -eq $script:readCount) { $script:fakeClockJump += 8 }
    [pscustomobject]@{ Errors=@(); Samples=@(
        [pscustomobject]@{ Peer='a'; OffsetSeconds=$offset; DelaySeconds=0.1; CollectedAtUtc=[DateTime]::UtcNow },
        [pscustomobject]@{ Peer='b'; OffsetSeconds=$offset + 0.01; DelaySeconds=0.1; CollectedAtUtc=[DateTime]::UtcNow }
    ) }
}
# Keep these orchestration tests independent of the real local execution window.
function Assert-ClockAdjustmentAllowed { param($LocalNow, $OffsetSeconds) }
$script:adjustments=0; $script:readCount=0; $script:offsetQueue=@(7.83, 0.02)
$report = Invoke-ClockGuard -Apply
Assert-True ($report.status -eq 'SYNCHRONIZED' -and $report.adjusted -and $script:readCount -eq 2 -and $script:adjustments -eq 1) 'success requires a fresh post-adjustment measurement'
$script:adjustments=0; $script:readCount=0; $script:offsetQueue=@(7.83, 2)
$report = Invoke-ClockGuard -Apply
Assert-True ($report.status -eq 'ERROR' -and $report.adjusted) 'do not report success if post-adjustment clock is wrong'
$script:adjustments=0; $script:readCount=0; $script:offsetQueue=@(7.83)
$report = Invoke-ClockGuard
Assert-True ($report.status -eq 'OFFSET_TOO_LARGE' -and $script:adjustments -eq 0 -and $script:readCount -eq 1) 'check mode cannot change the clock'
$script:adjustments=0; $script:readCount=0; $script:offsetQueue=@(0.02, 0.02)
$report = Invoke-ClockGuard -Apply
Assert-True ($report.status -eq 'SYNCHRONIZED' -and -not $report.adjusted -and $script:adjustments -eq 0 -and $script:readCount -eq 2) 'already accurate clock is measured again without stepping'
function Get-ClockUtcNow { [DateTime]::UtcNow.AddSeconds($script:fakeClockJump) }
$script:fakeClockJump=0; $script:jumpOnRead=2; $script:adjustments=0; $script:readCount=0; $script:offsetQueue=@(7.83, 0.02)
$report = Invoke-ClockGuard -Apply
Assert-True ($report.status -eq 'ERROR' -and $report.adjusted) 'clock jump during post-adjustment sampling cannot report success'
$script:fakeClockJump=0; $script:jumpOnRead=1; $script:adjustments=0; $script:readCount=0; $script:offsetQueue=@(0.02)
$report = Invoke-ClockGuard
Assert-True ($report.status -eq 'ERROR' -and $script:adjustments -eq 0) 'check mode rejects mixed samples across a clock jump'
$firstRunId = $report.run_id
$script:fakeClockJump=0; $script:jumpOnRead=0; $script:readCount=0; $script:offsetQueue=@(0.02)
$report = Invoke-ClockGuard
Assert-True ($firstRunId -and $report.run_id -and $firstRunId -ne $report.run_id) 'every invocation has a distinct verification identity'
Write-Output "PASS: $script:checks clock guard checks"
