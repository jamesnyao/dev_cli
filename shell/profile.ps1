# Generic PowerShell environment. Sourced from ~/.psrc.ps1 (written by `dev init`).
# Work-specific setup lives in a private hook sourced at the very end, if present.

$DevScripts = Split-Path $PSScriptRoot -Parent
$SETUP = Join-Path $DevScripts "setup"
$env:PATH = "$DevScripts;$env:PATH"

# Startup answers from `dev` are cached until DEVCONFIG or a config file changes,
# so warm shells start without launching Python.
$DevCacheFile = Join-Path $HOME '.dev_temp\dev_cli\profile-cache.json'
$DevCache = @{}
if (Test-Path -LiteralPath $DevCacheFile) {
  try {
    (Get-Content -LiteralPath $DevCacheFile -Raw | ConvertFrom-Json).PSObject.Properties |
      ForEach-Object { $DevCache[$_.Name] = $_.Value }
  }
  catch {}
}
function Invoke-DevCached {
  param([Parameter(Mandatory)][string[]]$Arguments, [string]$ErrorMessage)
  $Stamp = foreach ($File in @((Join-Path $DevScripts 'dev_config.json'), (Join-Path $HOME 'dev_config.json'))) {
    if (Test-Path -LiteralPath $File) { (Get-Item -LiteralPath $File).LastWriteTimeUtc.Ticks }
  }
  $Key = (@($env:DEVCONFIG) + $Arguments + $Stamp) -join '|'
  if ($DevCache.ContainsKey($Key)) {
    return $DevCache[$Key]
  }
  $Value = & "$DevScripts\dev.ps1" @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw $ErrorMessage
  }
  $DevCache[$Key] = $Value
  New-Item -ItemType Directory -Force -Path (Split-Path $DevCacheFile) | Out-Null
  $DevCache | ConvertTo-Json -Compress | Set-Content -LiteralPath $DevCacheFile -Encoding utf8
  return $Value
}

. $SETUP\install_winget.ps1
. $SETUP\install_prompt.ps1
. $SETUP\install_ghcli.ps1
. $SETUP\install_golang.ps1
. $SETUP\install_llvm.ps1
. $SETUP\install_chocolatey.ps1
. $SETUP\install_zoxide.ps1
. $SETUP\install_fzf.ps1
. $SETUP\install_pwsh.ps1

$DevPython = Invoke-DevCached python, path -ErrorMessage "Could not provision the configured Python runtime"
if (-not (Test-Path -LiteralPath $DevPython)) {
  $DevCache.Clear()
  $DevPython = Invoke-DevCached python, path -ErrorMessage "Could not provision the configured Python runtime"
}
$env:PATH = "$DevPython;$DevScripts;$env:PATH"

if (-not $env:DEVCONFIG) {
  $env:DEVCONFIG = "example-machine"
}
$env:DEV = Invoke-DevCached repo, root -ErrorMessage "Could not read workspaceRoots for DEVCONFIG=$env:DEVCONFIG"
if (-not $env:DEV_PROMPT_USER) {
  $ConfiguredUsername = Invoke-DevCached config, get, identity.username -ErrorMessage "Could not read identity.username"
  if ($ConfiguredUsername) {
    $env:DEV_PROMPT_USER = $ConfiguredUsername
  }
}

# Private, work-specific setup -- runs last so it can override anything above.
$WorkEnv = "$HOME\dev_env\env_windows.ps1"
if (Test-Path $WorkEnv) {
  . $WorkEnv
}
