$PYTHON_VERSION = Get-Content "$SETUP\PYTHON_VERSION"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  winget install "Python.Python.$PYTHON_VERSION" --source winget --accept-package-agreements --accept-source-agreements
}
