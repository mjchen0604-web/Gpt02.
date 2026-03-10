param(
  [Parameter(Mandatory = $true)]
  [int]$Count,
  [string]$OutputDir = (Join-Path $env:USERPROFILE "Desktop\chatmock-auths"),
  [string]$RepoDir = ""
)

if ($Count -lt 1) {
  throw "Count must be at least 1."
}

if ([string]::IsNullOrWhiteSpace($RepoDir)) {
  $scriptDir = Split-Path -Parent $PSCommandPath
  $RepoDir = Split-Path -Parent $scriptDir
}

$chatmockPy = Join-Path $RepoDir "chatmock.py"
if (-not (Test-Path $chatmockPy)) {
  throw "chatmock.py not found: $chatmockPy"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$existingIndexes = @()
Get-ChildItem -Path $OutputDir -File -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.Name -match "^auth(\d+)\.json$") {
    $existingIndexes += [int]$Matches[1]
  }
}
Get-ChildItem -Path $OutputDir -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.Name -match "^acc(\d+)$") {
    $existingIndexes += [int]$Matches[1]
  }
}

$startIndex = 1
if ($existingIndexes.Count -gt 0) {
  $startIndex = (($existingIndexes | Measure-Object -Maximum).Maximum) + 1
}

function Test-AuthFileReady {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  if (-not (Test-Path $Path)) {
    return $false
  }

  try {
    $raw = Get-Content -Path $Path -Raw -ErrorAction Stop
    if ([string]::IsNullOrWhiteSpace($raw)) {
      return $false
    }
    $null = $raw | ConvertFrom-Json -ErrorAction Stop
    return $true
  }
  catch {
    return $false
  }
}

function Convert-ToPsLiteral {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Value
  )

  return "'" + $Value.Replace("'", "''") + "'"
}

$created = @()
$captureRoot = Join-Path $OutputDir ".capture"

New-Item -ItemType Directory -Force -Path $captureRoot | Out-Null

Push-Location $RepoDir
try {
  for ($offset = 0; $offset -lt $Count; $offset++) {
    $index = $startIndex + $offset
    $label = "auth" + $index.ToString("00")
    $targetDir = Join-Path $captureRoot $label
    $authPath = Join-Path $targetDir "auth.json"
    $savedAuthPath = Join-Path $OutputDir ($label + ".json")

    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

    Write-Host ""
    Write-Host "[$label] Starting login flow"
    Write-Host "[$label] Capture path: $savedAuthPath"

    $repoDirLiteral = Convert-ToPsLiteral $RepoDir
    $chatmockPyLiteral = Convert-ToPsLiteral $chatmockPy
    $targetDirLiteral = Convert-ToPsLiteral $targetDir
    $command = @"
`$env:CHATGPT_LOCAL_HOME = $targetDirLiteral
Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue
Set-Location $repoDirLiteral
python $chatmockPyLiteral login
"@

    $process = Start-Process `
      -FilePath "powershell.exe" `
      -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        $command
      ) `
      -PassThru

    if (-not $process) {
      throw "Unable to start login process for $label"
    }

    $authReady = $false
    while (-not $process.HasExited) {
      if (Test-AuthFileReady -Path $authPath) {
        $authReady = $true
        break
      }
      Start-Sleep -Milliseconds 300
    }

    if (-not $authReady -and (Test-AuthFileReady -Path $authPath)) {
      $authReady = $true
    }

    if (-not $authReady) {
      $process.WaitForExit()
      if (-not (Test-AuthFileReady -Path $authPath)) {
        throw "Login failed for $label"
      }
      $authReady = $true
    }

    if ($process -and -not $process.HasExited) {
      if (-not $process.WaitForExit(5000)) {
        Write-Host "[$label] auth.json is ready; stopping lingering login process"
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
      }
    }

    Copy-Item -Path $authPath -Destination $savedAuthPath -Force
    Remove-Item -Recurse -Force $targetDir

    $created += $savedAuthPath
    Write-Host "[$label] Saved: $savedAuthPath"
  }
}
finally {
  Pop-Location
}

if (Test-Path $captureRoot) {
  Remove-Item -Recurse -Force $captureRoot -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Done. Auth files:"
$created | ForEach-Object { Write-Host $_ }
