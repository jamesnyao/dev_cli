if (-not (Get-Command fzf -ErrorAction SilentlyContinue)) {
  winget install junegunn.fzf --source winget --accept-package-agreements --accept-source-agreements
}
