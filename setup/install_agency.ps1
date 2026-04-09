if (-not (Get-Command agency -ErrorAction SilentlyContinue)) {
  iex "& { $(irm aka.ms/InstallTool.ps1)} agency"
}
