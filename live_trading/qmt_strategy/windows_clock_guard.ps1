param(
    [switch]$Apply,
    [string]$OutputDirectory = 'D:\qmt_bridge\logs\clock_sync',
    [switch]$LibraryOnly
)

$ErrorActionPreference = 'Stop'
$script:ClockGuardVersion = '2026-11-09-ntp-ephemeral-v1'

function ConvertFrom-ClockStripchart {
    param([string[]]$Lines, [string]$Peer)
    $pattern = '^\s*\d+\s*,\s*\d+\s*,\s*(\d+)\s*,\s*([+-]\d+\.\d+)\s*,\s*([+-]\d+\.\d+)\s*$'
    foreach ($line in $Lines) {
        if ($line.Replace([string][char]0, '') -match $pattern) {
            try {
                [pscustomobject]@{
                    Peer = $Peer
                    CollectedAtUtc = [DateTime]::FromFileTimeUtc([Int64]::Parse($Matches[1]))
                    DelaySeconds = [Double]::Parse($Matches[2], [Globalization.CultureInfo]::InvariantCulture)
                    OffsetSeconds = [Double]::Parse($Matches[3], [Globalization.CultureInfo]::InvariantCulture)
                }
            } catch { continue }
        }
    }
}

function Get-ClockReadings {
    $samples = @()
    $errors = @()
    $w32tm = Join-Path $env:SystemRoot 'System32\w32tm.exe'
    foreach ($peer in @('time.cloudflare.com', 'time.apple.com', 'time.windows.com')) {
        $lines = @(& $w32tm /stripchart /computer:$peer /samples:2 /period:1 /rdtsc 2>&1 | ForEach-Object { [string]$_ })
        $valid = @(ConvertFrom-ClockStripchart -Lines $lines -Peer $peer)
        $samples += $valid
        if ($valid.Count -eq 0) { $errors += [pscustomobject]@{ Peer=$peer; Output=($lines -join "`n") } }
    }
    [pscustomobject]@{ Samples=$samples; Errors=$errors }
}

function Get-ClockUtcNow { [DateTime]::UtcNow }

function Get-StableClockReadings {
    $startUtc = Get-ClockUtcNow
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $readings = Get-ClockReadings
    $wallElapsed = ((Get-ClockUtcNow) - $startUtc).TotalSeconds
    if ([Math]::Abs($wallElapsed - $timer.Elapsed.TotalSeconds) -gt 0.2) {
        throw 'System clock changed while sampling; these samples cannot be used.'
    }
    $readings
}

function Get-ClockConsensus {
    param([object[]]$Samples, [DateTime]$NowUtc = [DateTime]::UtcNow)
    $valid = @($Samples | Where-Object {
        $age = ($NowUtc - $_.CollectedAtUtc).TotalSeconds
        $_.Peer -and $age -ge -1 -and $age -le 30 -and
        -not [Double]::IsNaN($_.OffsetSeconds) -and -not [Double]::IsInfinity($_.OffsetSeconds) -and
        $_.DelaySeconds -ge 0 -and $_.DelaySeconds -le 1
    })
    $best = @($valid | Group-Object Peer | ForEach-Object { $_.Group | Sort-Object DelaySeconds | Select-Object -First 1 } | Sort-Object OffsetSeconds)
    if ($best.Count -lt 2) { throw 'At least two independent time sources with fresh, low-delay samples are required.' }
    $clusters = @()
    for ($i = 0; $i -lt $best.Count; $i++) {
        $members = @($best | Where-Object { $_.OffsetSeconds -ge $best[$i].OffsetSeconds -and $_.OffsetSeconds - $best[$i].OffsetSeconds -le 0.25 })
        if ($members.Count -ge 2) {
            $key = (@($members.Peer | Sort-Object) -join '|')
            if (-not @($clusters | Where-Object { $_.Key -eq $key }).Count) {
                $clusters += [pscustomobject]@{ Members=$members; Size=$members.Count; Key=$key }
            }
        }
    }
    if ($clusters.Count -eq 0) { throw 'Independent time sources disagree by more than 250 ms.' }
    $largest = ($clusters | Measure-Object Size -Maximum).Maximum
    $winners = @($clusters | Where-Object { $_.Size -eq $largest })
    if ($winners.Count -ne 1) { throw 'Time-source consensus is ambiguous.' }
    $members = @($winners[0].Members | Sort-Object OffsetSeconds)
    $offset = ($members | Measure-Object OffsetSeconds -Average).Average
    if ($members.Count -eq 3) { $offset = $members[1].OffsetSeconds }
    [pscustomobject]@{ OffsetSeconds=[Double]$offset; Peers=@($members.Peer); Samples=$members }
}

function Assert-ClockAdjustmentAllowed {
    param([DateTime]$LocalNow, [Double]$OffsetSeconds)
    if ([Double]::IsNaN($OffsetSeconds) -or [Double]::IsInfinity($OffsetSeconds) -or [Math]::Abs($OffsetSeconds) -gt 60) {
        throw 'Clock adjustment exceeds the automatic 60-second limit.'
    }
    $start = $LocalNow.Date.AddHours(14).AddMinutes(50)
    $end = $LocalNow.Date.AddHours(15).AddMinutes(5)
    $target = $LocalNow.AddSeconds($OffsetSeconds)
    $earliest = if ($target -lt $LocalNow) { $target } else { $LocalNow }
    $latest = if ($target -gt $LocalNow) { $target } else { $LocalNow }
    if ($earliest -le $end -and $latest -ge $start) {
        throw 'Clock changes are prohibited in or across the 14:50-15:05 execution window.'
    }
}

function Invoke-ClockGuard {
    param([switch]$Apply)
    $report = [ordered]@{
        run_id=[Guid]::NewGuid().ToString('N')
        version=$script:ClockGuardVersion; started_at=[DateTime]::UtcNow.ToString('o')
        mode=$(if ($Apply) { 'apply' } else { 'check' }); status='ERROR'; adjusted=$false
    }
    try {
        if ((Get-TimeZone).Id -ne 'China Standard Time') { throw 'China Standard Time is required.' }
        $startUtc = Get-ClockUtcNow
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $before = Get-StableClockReadings
        $report.before_samples = @($before.Samples)
        $report.source_errors = @($before.Errors)
        $choice = Get-ClockConsensus -Samples $before.Samples
        $report.offset_before_seconds = $choice.OffsetSeconds
        $report.consensus_peers = @($choice.Peers)
        if (-not $Apply) {
            $report.status = if ([Math]::Abs($choice.OffsetSeconds) -le 0.5) { 'CHECK_OK' } else { 'OFFSET_TOO_LARGE' }
        } else {
            Assert-ClockAdjustmentAllowed -LocalNow ([DateTime]::Now) -OffsetSeconds $choice.OffsetSeconds
            $wallElapsed = ((Get-ClockUtcNow) - $startUtc).TotalSeconds
            if ([Math]::Abs($wallElapsed - $timer.Elapsed.TotalSeconds) -gt 0.2) {
                throw 'System clock changed while sampling; no adjustment made.'
            }
            if ([Math]::Abs($choice.OffsetSeconds) -gt 0.1) {
                Set-Date -Date ([DateTime]::Now.AddSeconds($choice.OffsetSeconds)) | Out-Null
                $report.adjusted = $true
            }
            $after = Get-StableClockReadings
            $report.after_samples = @($after.Samples)
            $report.verification_source_errors = @($after.Errors)
            $verified = Get-ClockConsensus -Samples $after.Samples
            $report.offset_after_seconds = $verified.OffsetSeconds
            $report.verification_peers = @($verified.Peers)
            if ([Math]::Abs($verified.OffsetSeconds) -gt 0.5) { throw 'Post-adjustment offset remains above 500 ms.' }
            $report.status = 'SYNCHRONIZED'
        }
    } catch {
        $report.error = $_.Exception.Message
    }
    $report.finished_at = [DateTime]::UtcNow.ToString('o')
    [pscustomobject]$report
}

function Save-ClockReport {
    param($Report, [string]$Directory)
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $json = $Report | ConvertTo-Json -Depth 8
    $utf8 = New-Object Text.UTF8Encoding($false)
    $runName = 'clock-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N') + '.json'
    [IO.File]::WriteAllText((Join-Path $Directory $runName), $json, $utf8)
    $temporary = Join-Path $Directory ([Guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($temporary, $json, $utf8)
    Move-Item -LiteralPath $temporary -Destination (Join-Path $Directory 'clock.status.json') -Force
}

if (-not $LibraryOnly) {
    $report = Invoke-ClockGuard -Apply:$Apply
    Save-ClockReport -Report $report -Directory $OutputDirectory
    $report | ConvertTo-Json -Depth 8
    if ($report.status -notin @('SYNCHRONIZED', 'CHECK_OK')) { exit 1 }
}
