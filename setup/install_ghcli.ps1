if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  winget install GitHub.cli --source winget --accept-package-agreements --accept-source-agreements
}
if (-not ($env:PATH -like "*$env:ProgramFiles\GitHub CLI*")) {
  $env:PATH = "$env:ProgramFiles\GitHub CLI;$env:PATH"
}
