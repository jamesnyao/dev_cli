# Generic PowerShell environment. Sourced from ~/.psrc.ps1 (written by `dev init`).
# Work-specific setup lives in a private hook sourced at the very end, if present.

$SETUP = "$env:USERPROFILE\dev_scripts\setup"

. $SETUP\install_winget.ps1
. $SETUP\install_prompt.ps1
. $SETUP\install_python.ps1
. $SETUP\install_ghcli.ps1
. $SETUP\install_golang.ps1
. $SETUP\install_llvm.ps1
. $SETUP\install_chocolatey.ps1
. $SETUP\install_zoxide.ps1
. $SETUP\install_fzf.ps1
. $SETUP\install_agency.ps1
. $SETUP\install_pwsh.ps1

# Private, work-specific setup -- runs last so it can override anything above.
$WorkHooks = "$env:USERPROFILE\work_scripts\hooks.ps1"
if (Test-Path $WorkHooks) { . $WorkHooks }
