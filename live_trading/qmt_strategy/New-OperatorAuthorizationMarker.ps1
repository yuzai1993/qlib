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
$StateRoot = Join-Path $BridgeRoot "state"
$LockPath = Join-Path $StateRoot "OPERATOR_AUTHORIZATION.lock"
$Deadline = (Get-Date).AddSeconds($LockTimeoutSeconds)
$LockStream = $null
$ByteLocked = $false
$IntentStream = $null
$IntentPath = $null
$OwnMarker = $null
$OtherMarker = $null
$Committed = $false
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

try {
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

  if ($Profile -eq "CLOSE_AUCTION") {
    $CutoffText = "$TradeDate 14:57:05"
    $OwnMarker = Join-Path $StateRoot "LIVE_OK_$TradeDate"
    $OtherMarker = Join-Path $BridgeRoot (
      "pr49_probe\state\PR49_LIVE_OK_$TradeDate"
    )
  }
  else {
    $CutoffText = "$TradeDate 15:05:00"
    $OwnMarker = Join-Path $BridgeRoot (
      "pr49_probe\state\PR49_LIVE_OK_$TradeDate"
    )
    $OtherMarker = Join-Path $StateRoot "LIVE_OK_$TradeDate"
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

if ($null -eq $FailureMessage) {
  $FailureMessage = "authorization did not reach the marker commit point"
}
Write-BestEffortStatus `
  -Message (
    "AUTHORIZATION_NOT_COMMITTED profile=$Profile trade_date=$TradeDate " +
    "intent=$IntentPath error=$FailureMessage"
  ) `
  -PreferError $true
exit 1
