if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
  winget install Chocolatey.Chocolatey --source winget --accept-package-agreements --accept-source-agreements
}
$ChocolateyProfile = "$env:ChocolateyInstall\helpers\chocolateyProfile.psm1"
if (Test-Path($ChocolateyProfile)) {
  Import-Module "$ChocolateyProfile"
}
