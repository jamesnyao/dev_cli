$PWSH_VERSION = Get-Content "$SETUP\PWSH_VERSION"
$pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $pwshCmd -or [version]$pwshCmd.Version -lt [version]$PWSH_VERSION) {
  winget install Microsoft.PowerShell --version $PWSH_VERSION --source winget --accept-package-agreements --accept-source-agreements --force
}
