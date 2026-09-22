function prompt {
  $lastSuccess = $?
  $currentPath = (Get-Location).Path

  $gitBranch = ""
  try {
    $branch = git branch --show-current 2>$null
    if ($branch) {
      $gitBranch = " `e[33m($branch)`e[0m"
    }
  } catch {}

  # DEV_PROMPT_USER/DEV_PROMPT_HOST let a private config give machines
  # friendly short names (e.g. "devbox" instead of a real hostname);
  # unset, this just shows the real username@hostname.
  $userHostName = $env:DEV_PROMPT_USER
  if (-not $userHostName) { $userHostName = $env:USERNAME }
  $hostName = $env:DEV_PROMPT_HOST
  if (-not $hostName) { $hostName = $env:COMPUTERNAME.ToLower() }

  $userHost = "`e[32m$userHostName@$hostName`e[0m"
  $pathDisplay = "`e[96m$currentPath`e[0m"
  if ($lastSuccess) {
    $promptChar = "`e[0m$ "
  } else {
    $promptChar = "`e[31m$ `e[0m"
  }
  if (Get-Command __zoxide_hook -ErrorAction SilentlyContinue) {
    $null = __zoxide_hook
  }

  return "$userHost $pathDisplay$gitBranch $promptChar"
}
