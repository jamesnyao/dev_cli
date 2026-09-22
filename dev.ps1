$PythonCmd = Get-Command py -ErrorAction SilentlyContinue
if (-not $PythonCmd) { $PythonCmd = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $PythonCmd) { $PythonCmd = Get-Command python -ErrorAction SilentlyContinue }

if ($PythonCmd) {
  & $PythonCmd.Source "$PSScriptRoot\dev.py" @args
  exit $LASTEXITCODE
}

if ($args.Count -gt 0 -and $args[0] -eq "python") {
  Write-Host "Python not found. Bootstrapping..." -ForegroundColor Blue
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "winget not found; cannot bootstrap Python automatically." -ForegroundColor Red
    exit 1
  }
  $PythonVersion = Get-Content "$PSScriptRoot\setup\PYTHON_VERSION"
  winget install "Python.Python.$PythonVersion" --source winget --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to install Python." -ForegroundColor Red
    exit 1
  }
  $env:PATH = "$env:LOCALAPPDATA\Programs\Python\Launcher;$env:PATH"
  if (Get-Command py -ErrorAction SilentlyContinue) {
    Write-Host "Python installed. Run your dev command again." -ForegroundColor Green
    exit 0
  }
  Write-Host "Python installed, but 'py' still isn't on PATH. Open a new terminal and retry." -ForegroundColor Yellow
  exit 1
} else {
  Write-Host "Python is not installed. Run 'dev python update' to bootstrap it." -ForegroundColor Red
  exit 1
}
