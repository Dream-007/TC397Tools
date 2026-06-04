param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]] $ArgsFromBat
)

$ErrorActionPreference = "Stop"

function Show-Usage {
  @"
Usage:
  setup_ccfc_stepcode.bat            Patch native Windows ccfc to launch "stepcode claude"
  setup_ccfc_stepcode.bat --check    Show current patch status
  setup_ccfc_stepcode.bat --restore  Restore the latest non-identical backup

Environment overrides:
  CCFC_BIN=C:\path\to\ccfc.cmd
  CCFC_CLI_JS=C:\path\to\@hyposomnia\cc-feishu-connector\dist\cli.js
  STEPCODE_BIN=C:\path\to\stepcode.cmd
"@
}

$Mode = "patch"
if ($ArgsFromBat.Count -gt 0) {
  switch ($ArgsFromBat[0]) {
    "--check" { $Mode = "check" }
    "--restore" { $Mode = "restore" }
    "-h" { Show-Usage; exit 0 }
    "--help" { Show-Usage; exit 0 }
    default { Show-Usage; exit 2 }
  }
}

function Resolve-CommandPath([string] $Name) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $cmd) { return $null }
  return $cmd.Source
}

function Resolve-CcfcCliJs {
  if ($env:CCFC_CLI_JS) {
    if (Test-Path $env:CCFC_CLI_JS) {
      return (Resolve-Path $env:CCFC_CLI_JS).Path
    }
    throw "CCFC_CLI_JS does not exist: $env:CCFC_CLI_JS"
  }

  if ($env:CCFC_BIN) {
    $ccfcBin = $env:CCFC_BIN
  } else {
    $ccfcBin = Resolve-CommandPath "ccfc"
  }

  if (-not $ccfcBin) {
    throw "ccfc not found. Set CCFC_BIN or CCFC_CLI_JS."
  }

  if ($ccfcBin -match '\.js$' -and (Test-Path $ccfcBin)) {
    return (Resolve-Path $ccfcBin).Path
  }

  $npmRoot = $null
  try {
    $npmRoot = (& npm root -g 2>$null | Select-Object -First 1).Trim()
  } catch {
    $npmRoot = $null
  }

  if ($npmRoot) {
    $candidate = Join-Path $npmRoot "@hyposomnia\cc-feishu-connector\dist\cli.js"
    if (Test-Path $candidate) {
      return (Resolve-Path $candidate).Path
    }
  }

  $ccfcDir = Split-Path -Parent $ccfcBin
  $candidate = Join-Path $ccfcDir "node_modules\@hyposomnia\cc-feishu-connector\dist\cli.js"
  if (Test-Path $candidate) {
    return (Resolve-Path $candidate).Path
  }

  if (Test-Path $ccfcBin) {
    $raw = Get-Content $ccfcBin -Raw -ErrorAction SilentlyContinue
    if ($raw -match 'node_modules[\\/]+@hyposomnia[\\/]+cc-feishu-connector[\\/]+dist[\\/]+cli\.js') {
      $candidate = Join-Path $ccfcDir "node_modules\@hyposomnia\cc-feishu-connector\dist\cli.js"
      if (Test-Path $candidate) {
        return (Resolve-Path $candidate).Path
      }
    }
  }

  throw "Unable to locate @hyposomnia/cc-feishu-connector dist\cli.js. Set CCFC_CLI_JS explicitly."
}

function Resolve-StepcodeBin {
  if ($env:STEPCODE_BIN) {
    if (Test-Path $env:STEPCODE_BIN) {
      return (Resolve-Path $env:STEPCODE_BIN).Path
    }
    throw "STEPCODE_BIN does not exist: $env:STEPCODE_BIN"
  }

  $stepcode = Resolve-CommandPath "stepcode"
  if (-not $stepcode) {
    throw "stepcode not found. Set STEPCODE_BIN."
  }
  return $stepcode
}

$cliJs = Resolve-CcfcCliJs
$stepcodeBin = Resolve-StepcodeBin

if ($Mode -eq "restore") {
  $backups = Get-ChildItem -Path "$cliJs.bak-stepcode-*" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending
  foreach ($backup in $backups) {
    $same = $false
    try {
      $same = ((Get-FileHash $backup.FullName).Hash -eq (Get-FileHash $cliJs).Hash)
    } catch {
      $same = $false
    }
    if (-not $same) {
      Copy-Item $backup.FullName $cliJs -Force
      Write-Host "Restored ccfc from: $($backup.FullName)"
      if (Get-Command node -ErrorAction SilentlyContinue) {
        & node --check $cliJs
      }
      exit 0
    }
  }
  throw "No non-identical backup found for $cliJs"
}

$source = Get-Content $cliJs -Raw
$alreadyPatched = $source -match 'this\.proc = spawn\(stepcodeLauncher, stepcodeArgs, \{'

if ($Mode -eq "check") {
  if ($alreadyPatched) {
    if ($source -match 'const stepcodeCommand = (?<cmd>"(?:\\.|[^"])*");') {
      $cmd = $Matches["cmd"] | ConvertFrom-Json
      Write-Host "PATCHED: ccfc launches $cmd claude"
    } else {
      Write-Host "PATCHED: ccfc launches stepcode claude"
    }
    Write-Host "ccfc cli.js: $cliJs"
    Write-Host "stepcode:    $stepcodeBin"
    exit 0
  }

  if ($source -match 'this\.proc = spawn\(this\.claudeBin, args, \{') {
    Write-Host "NOT PATCHED: ccfc still launches this.claudeBin"
    exit 1
  }

  Write-Host "UNKNOWN: launch pattern was not recognized"
  exit 2
}

if ($alreadyPatched) {
  Write-Host "Already patched: ccfc launches stepcode claude"
  Write-Host "ccfc cli.js: $cliJs"
  Write-Host "stepcode:    $stepcodeBin"
  Write-Host "Done. Start ccfc normally, for example:"
  Write-Host "  ccfc start C:\path\to\config.toml"
  exit 0
}

$originalPattern = '        this\.proc = spawn\(this\.claudeBin, args, \{\r?\n'
if ($source -notmatch $originalPattern) {
  throw "Unable to find ccfc Claude spawn line. The installed ccfc version may have changed."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = "$cliJs.bak-stepcode-$timestamp"
Copy-Item $cliJs $backup -Force
Write-Host "Backup: $backup"

$stepcodeJson = ConvertTo-Json $stepcodeBin -Compress
$replacement = @"
        process.stderr.write(``Launching Claude via stepcode claude `${args.join(" ")}\n``);
        const stepcodeCommand = $stepcodeJson;
        const stepcodeLauncher = process.platform === "win32" ? (process.env.ComSpec || "cmd.exe") : stepcodeCommand;
        const stepcodeArgs = process.platform === "win32" ? ["/d", "/s", "/c", stepcodeCommand, "claude", ...args] : ["claude", ...args];
        this.proc = spawn(stepcodeLauncher, stepcodeArgs, {
"@

$next = [regex]::Replace($source, $originalPattern, $replacement, 1)
Set-Content -Path $cliJs -Value $next -NoNewline -Encoding UTF8

if (Get-Command node -ErrorAction SilentlyContinue) {
  & node --check $cliJs
}

Write-Host "Patched: ccfc now launches $stepcodeBin claude"
Write-Host "ccfc cli.js: $cliJs"
Write-Host "stepcode:    $stepcodeBin"
Write-Host "Done. Start ccfc normally, for example:"
Write-Host "  ccfc start C:\path\to\config.toml"
