if ($env:WORK -eq "MSFT") {
  if (-not (Get-Command agency -ErrorAction SilentlyContinue)) {
    Write-Host "Installing agency"
    iex "& { $(irm aka.ms/InstallTool.ps1)} agency"
  }
} else {
  $env:PATH="C:\Users\example-user\.local\bin;$env:PATH"
  if (-not (Get-Command claude.exe -ErrorAction SilentlyContinue)) {
    Write-Host "Installing claude"
    irm https://claude.ai/install.ps1 | iex
  }
}
