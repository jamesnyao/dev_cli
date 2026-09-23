$ErrorActionPreference = 'Stop'
if ($MyInvocation.ExpectingInput) {
    $input | & "$PSScriptRoot\runtime.ps1" --python @args
}
else {
    & "$PSScriptRoot\runtime.ps1" --python @args
}
exit $LASTEXITCODE
