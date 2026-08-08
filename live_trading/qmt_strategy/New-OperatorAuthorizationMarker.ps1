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
if (-not (Test-Path -LiteralPath $StateRoot -PathType Container)) {
  throw "shared authorization state root is missing"
}

$LockPath = Join-Path $StateRoot "OPERATOR_AUTHORIZATION.lock"
$Deadline = (Get-Date).AddSeconds($LockTimeoutSeconds)
$LockStream = $null
$ByteLocked = $false

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

try {
  # These decisions must be made again while holding the shared SMB lock.
  $Today = (Get-Date).ToString("yyyy-MM-dd")
  if ($TradeDate -ne $Today) {
    throw "trade date must equal today"
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

  $Cutoff = [datetime]::ParseExact(
    $CutoffText,
    "yyyy-MM-dd HH:mm:ss",
    [System.Globalization.CultureInfo]::InvariantCulture
  )
  if ((Get-Date) -ge $Cutoff) {
    throw "authorization cutoff has passed"
  }
  if (Test-Path -LiteralPath $OtherMarker) {
    throw "other profile authorization exists"
  }
  if (Test-Path -LiteralPath $OwnMarker) {
    throw "authorization marker already exists"
  }
  if (-not (Test-Path -LiteralPath (Split-Path $OwnMarker) -PathType Container)) {
    throw "authorization marker state root is missing"
  }

  New-Item -ItemType File -Path $OwnMarker -ErrorAction Stop | Out-Null
  Get-Item -LiteralPath $OwnMarker
}
finally {
  if ($null -ne $LockStream) {
    try {
      if ($ByteLocked) {
        $LockStream.Unlock(0, 1)
      }
    }
    finally {
      $LockStream.Dispose()
    }
  }
}
