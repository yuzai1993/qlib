[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("CLOSE_AUCTION", "AFTER_HOURS_FIXED_PRICE")]
  [string]$Profile,

  [Parameter(Mandatory = $true)]
  [ValidatePattern("^\d{4}-\d{2}-\d{2}$")]
  [string]$TradeDate,

  [string]$BridgeRoot = "D:\qmt_bridge",

  [ValidateRange(1, 60)]
  [int]$LockTimeoutSeconds = 10
)

$ErrorActionPreference = "Stop"
$StateRoot = $null
$LockPath = $null
$Deadline = $null
$LockStream = $null
$ByteLocked = $false
$IntentStream = $null
$IntentPath = $null
$OwnMarker = $null
$OtherMarker = $null
$Committed = $false
$StateUnknown = $false
$StateUnknownMessage = $null
$NotCommittedEmittedUnderLock = $false
$CommitAttempted = $false
$CommitSource = "none"
$FailureMessage = $null
$PostCommitWarnings = New-Object 'System.Collections.Generic.List[string]'

function Write-BestEffortStatus {
  param(
    [string]$Message,
    [bool]$PreferError
  )
  try {
    if ($PreferError) {
      [Console]::Error.WriteLine($Message)
    }
    else {
      [Console]::Out.WriteLine($Message)
    }
    return
  }
  catch {
    # Fall through to the other stream. Status output is best effort only.
  }
  try {
    if ($PreferError) {
      [Console]::Out.WriteLine($Message)
    }
    else {
      [Console]::Error.WriteLine($Message)
    }
  }
  catch {
    # Exit code and the final marker remain the authoritative contract.
  }
}

function Get-FinalMarkerState {
  param([string]$Path)
  if ([string]::IsNullOrWhiteSpace($Path)) {
    return [pscustomobject]@{
      State = "UNKNOWN"
      Detail = "final marker path could not be derived"
    }
  }
  try {
    $FinalItem = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($FinalItem.PSIsContainer) {
      return [pscustomobject]@{
        State = "ABSENT"
        Detail = "final marker path is occupied by a directory"
      }
    }
    return [pscustomobject]@{
      State = "PRESENT"
      Detail = "final marker file exists"
    }
  }
  catch [System.Management.Automation.ItemNotFoundException] {
    return [pscustomobject]@{
      State = "ABSENT"
      Detail = "final marker file is positively absent"
    }
  }
  catch {
    return [pscustomobject]@{
      State = "UNKNOWN"
      Detail = "final marker state read failed: " + $_.Exception.Message
    }
  }
}

try {
  # FINAL_MARKER_PATH_DERIVED: pure path construction precedes every SMB
  # read, lock open, timeout, and state-root validation.
  $StateRoot = [System.IO.Path]::Combine($BridgeRoot, "state")
  $LockPath = [System.IO.Path]::Combine(
    $StateRoot, "OPERATOR_AUTHORIZATION.lock"
  )
  if ($Profile -eq "CLOSE_AUCTION") {
    $CutoffText = "$TradeDate 14:57:05"
    $OwnMarker = [System.IO.Path]::Combine(
      $StateRoot, "LIVE_OK_$TradeDate"
    )
    $OtherMarker = [System.IO.Path]::Combine(
      $StateRoot, "PR49_LIVE_OK_$TradeDate"
    )
  }
  else {
    $CutoffText = "$TradeDate 15:05:00"
    $OwnMarker = [System.IO.Path]::Combine(
      $StateRoot, "PR49_LIVE_OK_$TradeDate"
    )
    $OtherMarker = [System.IO.Path]::Combine(
      $StateRoot, "LIVE_OK_$TradeDate"
    )
  }
  $Deadline = (Get-Date).AddSeconds($LockTimeoutSeconds)

  if (-not (Test-Path -LiteralPath $StateRoot -PathType Container)) {
    throw "shared authorization state root is missing"
  }

  while ($null -eq $LockStream) {
    try {
      $LockStream = [System.IO.File]::Open(
        $LockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
      )
      try {
        if ($LockStream.Length -lt 1) {
          $LockStream.SetLength(1)
        }
        $LockStream.Lock(0, 1)
        $ByteLocked = $true
      }
      catch {
        $LockStream.Dispose()
        $LockStream = $null
        throw
      }
    }
    catch [System.IO.IOException] {
      if ((Get-Date) -ge $Deadline) {
        throw "authorization lock timeout"
      }
      Start-Sleep -Milliseconds 100
    }
  }

  # A final marker is already an irreversible authorization fact. Never
  # downgrade it to an ordinary failure, even if this is crash recovery.
  if (Test-Path -LiteralPath $OwnMarker -PathType Leaf) {
    $Committed = $true
    $CommitSource = "pre-existing-final-marker"
    [void]$PostCommitWarnings.Add(
      "authorization marker already exists before this invocation"
    )
    if (Test-Path -LiteralPath $OtherMarker -PathType Leaf) {
      [void]$PostCommitWarnings.Add(
        "other profile authorization also exists; stop both QMT strategies"
      )
    }
  }
  else {
    # All fallible authorization decisions happen before the commit point.
    $Today = (Get-Date).ToString("yyyy-MM-dd")
    if ($TradeDate -ne $Today) {
      throw "trade date must equal today"
    }
    $Cutoff = [datetime]::ParseExact(
      $CutoffText,
      "yyyy-MM-dd HH:mm:ss",
      [System.Globalization.CultureInfo]::InvariantCulture
    )
    if ((Get-Date) -ge $Cutoff) {
      throw "authorization cutoff has passed"
    }
    if (Test-Path -LiteralPath $OtherMarker -PathType Leaf) {
      throw "other profile authorization exists"
    }
    if (-not (
      Test-Path -LiteralPath (Split-Path $OwnMarker) -PathType Container
    )) {
      throw "authorization marker state root is missing"
    }

    $IntentPath = (
      "$OwnMarker.intent." + [guid]::NewGuid().ToString("N") + ".tmp"
    )
    $IntentPayload = (@{
      protocol = "operator-authorization-v1"
      profile = $Profile
      trade_date = $TradeDate
      final_marker = $OwnMarker
      created_at = (Get-Date).ToString("o")
    } | ConvertTo-Json -Compress) + "`n"
    $IntentBytes = [System.Text.Encoding]::UTF8.GetBytes($IntentPayload)

    $IntentStream = [System.IO.File]::Open(
      $IntentPath,
      [System.IO.FileMode]::CreateNew,
      [System.IO.FileAccess]::Write,
      [System.IO.FileShare]::None
    )
    try {
      $IntentStream.Write($IntentBytes, 0, $IntentBytes.Length)
      $IntentStream.Flush($true)
    }
    finally {
      $IntentStream.Dispose()
      $IntentStream = $null
    }
    $IntentReadback = [System.IO.File]::ReadAllText(
      $IntentPath,
      [System.Text.Encoding]::UTF8
    )
    if ($IntentReadback -cne $IntentPayload) {
      throw "authorization intent readback mismatch"
    }

    # Sole irreversible commit point: same-directory atomic rename to the
    # exact filename recognized by QMT. Never delete $OwnMarker as rollback.
    $CommitAttempted = $true
    try {
      [System.IO.File]::Move($IntentPath, $OwnMarker)
      $Committed = $true
      $CommitSource = "atomic-rename-returned"
    }
    catch {
      $RenameFailure = $_.Exception.Message
      try {
        $FinalExists = Test-Path -LiteralPath $OwnMarker -PathType Leaf
        $IntentExists = Test-Path -LiteralPath $IntentPath -PathType Leaf
        if ($FinalExists) {
          $Committed = $true
          $CommitSource = "rename-threw-final-present"
          [void]$PostCommitWarnings.Add(
            "rename raised after authorization committed: $RenameFailure"
          )
        }
        elseif ($IntentExists) {
          throw "marker commit failed; final absent and intent remains: $RenameFailure"
        }
        else {
          # Both paths absent is not proof of non-authorization on an SMB
          # error. Conservatively report committed/unknown, never false safety.
          $Committed = $true
          $CommitSource = "rename-state-unknown"
          [void]$PostCommitWarnings.Add(
            "rename state ambiguous; treat authorization as committed: $RenameFailure"
          )
        }
      }
      catch {
        if ($_.Exception.Message -like "marker commit failed;*") {
          throw
        }
        $Committed = $true
        $CommitSource = "rename-state-read-failed"
        [void]$PostCommitWarnings.Add(
          "rename state could not be read; treat authorization as committed: " +
          $_.Exception.Message
        )
      }
    }

    if ($Committed) {
      try {
        $FinalReadback = Get-Item -LiteralPath $OwnMarker -ErrorAction Stop
        if (-not $FinalReadback.PSIsContainer) {
          $CommitSource = "$CommitSource+readback"
        }
        else {
          throw "final authorization path is not a file"
        }
      }
      catch {
        [void]$PostCommitWarnings.Add(
          "post-commit marker readback failed: " + $_.Exception.Message
        )
      }
    }
  }
}
catch {
  if ($Committed) {
    [void]$PostCommitWarnings.Add(
      "post-commit operation failed: " + $_.Exception.Message
    )
  }
  elseif ($CommitAttempted) {
    # A failure after calling Move must never be reported as definitely
    # uncommitted unless final is absent and intent is positively present.
    try {
      $FinalExists = Test-Path -LiteralPath $OwnMarker -PathType Leaf
      $IntentExists = Test-Path -LiteralPath $IntentPath -PathType Leaf
      if ($FinalExists -or -not $IntentExists) {
        $Committed = $true
        $CommitSource = "outer-catch-state-ambiguous"
        [void]$PostCommitWarnings.Add(
          "commit state ambiguous; treat authorization as committed: " +
          $_.Exception.Message
        )
      }
      else {
        $FailureMessage = $_.Exception.Message
      }
    }
    catch {
      $Committed = $true
      $CommitSource = "outer-catch-state-read-failed"
      [void]$PostCommitWarnings.Add(
        "commit state unreadable; treat authorization as committed"
      )
    }
  }
  else {
    $FailureMessage = $_.Exception.Message
  }
}
finally {
  if ($null -ne $IntentStream) {
    try {
      $IntentStream.Dispose()
    }
    catch {
      if ($Committed) {
        [void]$PostCommitWarnings.Add(
          "post-commit intent stream dispose failed: " + $_.Exception.Message
        )
      }
      elseif ($null -eq $FailureMessage) {
        $FailureMessage = "intent stream dispose failed: " + $_.Exception.Message
      }
    }
  }
  if ($ByteLocked) {
    # FINAL_MARKER_PROBE_UNDER_LOCK: only this probe can establish stable
    # absence against another conforming marker creator.
    try {
      $LockedFinalProbe = Get-FinalMarkerState -Path $OwnMarker
    }
    catch {
      $LockedFinalProbe = [pscustomobject]@{
        State = "UNKNOWN"
        Detail = "locked final marker probe threw: " + $_.Exception.Message
      }
    }
    if ($LockedFinalProbe.State -eq "PRESENT") {
      if (-not $Committed) {
        $Committed = $true
        $CommitSource = "locked-final-reconcile-present"
        [void]$PostCommitWarnings.Add(
          "earlier operation failed but final marker exists: $FailureMessage"
        )
      }
    }
    elseif (
      $LockedFinalProbe.State -eq "ABSENT" -and -not $Committed
    ) {
      # Complete the NOT_COMMITTED classification and status write before
      # releasing the lock. An unlocked ABSENT read is never stable evidence.
      if ($null -eq $FailureMessage) {
        $FailureMessage = "authorization did not reach the marker commit point"
      }
      Write-BestEffortStatus `
        -Message (
          "AUTHORIZATION_NOT_COMMITTED profile=$Profile " +
          "trade_date=$TradeDate intent=$IntentPath error=$FailureMessage"
        ) `
        -PreferError $true
      $NotCommittedEmittedUnderLock = $true
    }
  }
  if ($null -ne $LockStream) {
    if ($ByteLocked) {
      try {
        $LockStream.Unlock(0, 1)
      }
      catch {
        if ($Committed) {
          [void]$PostCommitWarnings.Add(
            "post-commit unlock failed: " + $_.Exception.Message
          )
        }
        elseif ($null -eq $FailureMessage) {
          $FailureMessage = "authorization unlock failed: " + $_.Exception.Message
        }
      }
    }
    try {
      $LockStream.Dispose()
    }
    catch {
      if ($Committed) {
        [void]$PostCommitWarnings.Add(
          "post-commit lock dispose failed: " + $_.Exception.Message
        )
      }
      elseif ($null -eq $FailureMessage) {
        $FailureMessage = "authorization lock dispose failed: " + $_.Exception.Message
      }
    }
  }
}

if ($NotCommittedEmittedUnderLock) {
  exit 1
}

# FINAL_MARKER_POST_CLEANUP_REPROBE: every exit path rechecks the exact final
# marker before output unless NOT_COMMITTED was already conclusively emitted
# while holding the lock. PRESENT safely upgrades any outcome because final
# markers are irreversible; unlocked ABSENT is always UNKNOWN.
try {
  $PostCleanupFinalProbe = Get-FinalMarkerState -Path $OwnMarker
}
catch {
  $PostCleanupFinalProbe = [pscustomobject]@{
    State = "UNKNOWN"
    Detail = "final marker probe threw: " + $_.Exception.Message
  }
}

if ($PostCleanupFinalProbe.State -eq "PRESENT") {
  if (-not $Committed) {
    $Committed = $true
    $CommitSource = "final-reconcile-present"
    [void]$PostCommitWarnings.Add(
      "earlier operation failed but final marker exists: $FailureMessage"
    )
  }
}
elseif ($PostCleanupFinalProbe.State -eq "UNKNOWN") {
  if ($Committed) {
    [void]$PostCommitWarnings.Add(
      "final marker state is unreadable after known commit: " +
      $PostCleanupFinalProbe.Detail
    )
  }
  else {
    $StateUnknown = $true
    $StateUnknownMessage = $PostCleanupFinalProbe.Detail
  }
}
elseif ($Committed) {
  # A successful/possible commit may have been visible to QMT before a later
  # disappearance. Never downgrade that historical authorization fact.
  [void]$PostCommitWarnings.Add(
    "final marker now reads absent after a committed outcome"
  )
}
else {
  # A point-in-time ABSENT probe without the shared lock races a creator that
  # may already hold the lock and commit immediately after this read.
  $StateUnknown = $true
  $StateUnknownMessage = (
    "final marker is currently absent but absence was not proven " +
    "while holding the shared authorization lock"
  )
}

if ($Committed) {
  foreach ($WarningText in $PostCommitWarnings) {
    Write-BestEffortStatus `
      -Message "AUTHORIZATION_COMMITTED_WARNING $WarningText" `
      -PreferError $true
  }
  Write-BestEffortStatus `
    -Message (
      "AUTHORIZATION_COMMITTED profile=$Profile trade_date=$TradeDate " +
      "marker=$OwnMarker source=$CommitSource"
    ) `
    -PreferError $false
  exit 0
}

if ($StateUnknown) {
  Write-BestEffortStatus `
    -Message (
      "AUTHORIZATION_STATE_UNKNOWN profile=$Profile trade_date=$TradeDate " +
      "marker=$OwnMarker error=$FailureMessage probe=$StateUnknownMessage " +
      "action=STOP_BOTH_QMT_NO_RETRY"
    ) `
    -PreferError $true
  exit 2
}

Write-BestEffortStatus `
  -Message (
    "AUTHORIZATION_STATE_UNKNOWN profile=$Profile trade_date=$TradeDate " +
    "marker=$OwnMarker error=$FailureMessage " +
    "probe=no-safe-final-classification " +
    "action=STOP_BOTH_QMT_NO_RETRY"
  ) `
  -PreferError $true
exit 2
