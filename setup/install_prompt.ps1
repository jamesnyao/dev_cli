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

  $userHostName = "developer"
  if ($env:COMPUTERNAME -like "CPC-jamya*") {
    $hostName = "cloud-devbox"
  } elseif ($env:COMPUTERNAME -eq "developer-DEVBOX") {
    $hostName = "devbox"
  } elseif ($env:COMPUTERNAME -eq "developer-SURFACE") {
    $hostName = "surface"
  } else {
    $hostName = $env:COMPUTERNAME.ToLower()
    $userHostName = $env:USERNAME
  }

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
