$ErrorActionPreference = 'Stop'
if ($MyInvocation.ExpectingInput) {
    $input | & "$PSScriptRoot\runtime.ps1" --dev @args
}
else {
    & "$PSScriptRoot\runtime.ps1" --dev @args
}
exit $LASTEXITCODE
