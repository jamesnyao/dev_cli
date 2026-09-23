& "$PSScriptRoot\..\runtime.ps1" --ensure
if ($LASTEXITCODE -ne 0) {
    throw "Managed Python installation failed (exit $LASTEXITCODE)"
}
