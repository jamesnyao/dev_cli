if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
  winget install Chocolatey.Chocolatey --source winget --accept-package-agreements --accept-source-agreements
}
$ChocolateyProfile = "$env:ChocolateyInstall\helpers\chocolateyProfile.psm1"
if (Test-Path($ChocolateyProfile)) {
  # Importing costs ~0.7s, so load it on first use of its commands.
  function global:Update-SessionEnvironment {
    Remove-Item Function:\Update-SessionEnvironment, Function:\refreshenv -ErrorAction SilentlyContinue
    Import-Module "$env:ChocolateyInstall\helpers\chocolateyProfile.psm1" -Global
    Update-SessionEnvironment @args
  }
  function global:refreshenv { Update-SessionEnvironment @args }
}
