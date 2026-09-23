$ErrorActionPreference = "Stop"
$Repository = if ($env:DEV_CLI_REPOSITORY) {
    $env:DEV_CLI_REPOSITORY
} else {
    "https://github.com/jamesnyao/dev_cli.git"
}
$Destination = Join-Path $HOME "dev_cli"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Install Git for Windows, then rerun this installer (winget was not found)."
    }
    & winget install --id Git.Git --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Git installation failed (exit $LASTEXITCODE)."
    }
    $env:PATH = "$env:ProgramFiles\Git\cmd;$env:LOCALAPPDATA\Programs\Git\cmd;$env:PATH"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git installed but could not be found. Open a new terminal and rerun this installer."
    }
}

if (-not (Test-Path (Join-Path $Destination ".git"))) {
    $Entry = ""
    if (Test-Path (Join-Path $HOME ".git")) {
        $Entry = & git -C $HOME ls-files --stage -- dev_cli
        if ($LASTEXITCODE -ne 0) {
            throw "Could not inspect the existing home repository (exit $LASTEXITCODE)."
        }
    }
    if ($Entry -match "^160000 ") {
        & git -C $HOME submodule update --init --recursive -- dev_cli
        if ($LASTEXITCODE -ne 0) {
            throw "Could not initialize the dev_cli submodule (exit $LASTEXITCODE)."
        }
    } elseif ((Test-Path $Destination) -and
              ((-not (Test-Path $Destination -PathType Container)) -or
               (Get-ChildItem -LiteralPath $Destination -Force | Select-Object -First 1))) {
        throw "Refusing to overwrite $Destination; move it aside or initialize its Git checkout."
    } else {
        & git clone -- $Repository $Destination
        if ($LASTEXITCODE -ne 0) {
            throw "Could not clone dev_cli (exit $LASTEXITCODE)."
        }
    }
}

if (-not (Test-Path (Join-Path $Destination "dev.ps1")) -or
    -not (Test-Path (Join-Path $Destination "dev.py"))) {
    throw "$Destination is not a dev_cli checkout; no files were replaced."
}

if ((Get-ExecutionPolicy) -eq "Restricted") {
    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
}
if ((Get-ExecutionPolicy) -in @("Restricted", "AllSigned")) {
    throw "Execution policy prevents loading dev_cli's shell profile. Use your organization's approved policy."
}

& (Join-Path $Destination "dev.ps1") init
if ($LASTEXITCODE -ne 0) {
    throw "dev init failed (exit $LASTEXITCODE)."
}
