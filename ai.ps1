$ErrorActionPreference = 'Stop'
if ($MyInvocation.ExpectingInput) {
    $input | & "$PSScriptRoot\dev.ps1" ai '--' @args
}
else {
    & "$PSScriptRoot\dev.ps1" ai '--' @args
}
exit $LASTEXITCODE
