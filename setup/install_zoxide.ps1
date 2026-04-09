if (-not (Get-Command zoxide -ErrorAction SilentlyContinue)) {
  winget install ajeetdsouza.zoxide --source winget --accept-package-agreements --accept-source-agreements
}
if (Get-Command zoxide -ErrorAction SilentlyContinue) {
  Invoke-Expression (& { (zoxide init --cmd cd powershell | Out-String) })
}
