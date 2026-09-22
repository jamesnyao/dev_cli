if (-not (git config --global user.email)) {
  $email = $env:DEV_GIT_EMAIL
  if (-not $email) { $email = (& dev config get identity.gitEmail 2>$null) }
  if (-not $email) { $email = Read-Host "git user.email" }
  if ($email) { git config --global user.email "$email" }
}
if (-not (git config --global user.name)) {
  $name = $env:DEV_GIT_NAME
  if (-not $name) { $name = (& dev config get identity.gitName 2>$null) }
  if (-not $name) { $name = Read-Host "git user.name" }
  if ($name) { git config --global user.name "$name" }
}
