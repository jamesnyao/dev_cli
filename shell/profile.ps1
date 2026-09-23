# Generic PowerShell environment. Sourced from ~/.psrc.ps1 (written by `dev init`).
# Work-specific setup lives in a private hook sourced at the very end, if present.

$DevScripts = Split-Path $PSScriptRoot -Parent
$SETUP = Join-Path $DevScripts "setup"
$env:PATH = "$DevScripts;$env:PATH"

. $SETUP\install_winget.ps1
. $SETUP\install_prompt.ps1
. $SETUP\install_python.ps1
. $SETUP\install_ghcli.ps1
. $SETUP\install_golang.ps1
. $SETUP\install_llvm.ps1
. $SETUP\install_chocolatey.ps1
. $SETUP\install_zoxide.ps1
. $SETUP\install_fzf.ps1
. $SETUP\install_pwsh.ps1

$DevPython = & "$DevScripts\dev.ps1" python path
if ($LASTEXITCODE -ne 0) {
  throw "Could not provision the configured Python runtime"
}
$env:PATH = "$DevPython;$DevScripts;$env:PATH"

if (-not $env:DEVCONFIG) {
  $env:DEVCONFIG = "example-machine"
}
$Workspace = & dev repo root
if ($LASTEXITCODE -ne 0) {
  throw "Could not read workspaceRoots for DEVCONFIG=$env:DEVCONFIG"
}
$env:DEV = $Workspace
if (-not $env:DEV_PROMPT_USER) {
  $ConfiguredUsername = & dev config get identity.username
  if ($LASTEXITCODE -ne 0) {
    throw "Could not read identity.username"
  }
  if ($ConfiguredUsername) {
    $env:DEV_PROMPT_USER = $ConfiguredUsername
  }
}

# Private, work-specific setup -- runs last so it can override anything above.
$WorkEnv = "$HOME\dev_env\env_windows.ps1"
if (-not (Test-Path $WorkEnv)) {
  $WorkEnv = "$HOME\dev_env\env.ps1"
}
if (Test-Path $WorkEnv) {
  . $WorkEnv
}
