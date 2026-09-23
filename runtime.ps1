$ErrorActionPreference = 'Stop'
$RuntimeRoot = Join-Path $env:USERPROFILE '.dev_temp\dev_cli'
$Uv = Join-Path $RuntimeRoot 'uv\uv.exe'

function Invoke-RuntimeProcess {
    param(
        [string]$File,
        [string[]]$Arguments,
        [switch]$Capture,
        [switch]$Diagnostic,
        [switch]$AllowFailure,
        [switch]$InheritEnvironment
    )
    $Info = New-Object System.Diagnostics.ProcessStartInfo
    $Info.FileName = $File
    $Info.UseShellExecute = $false
    # Windows PowerShell 5 drops empty arguments and embedded quotes with native splatting.
    $Quoted = foreach ($Argument in $Arguments) {
        $Escaped = [regex]::Replace($Argument, '(\\*)"', '$1$1\"')
        $Escaped = [regex]::Replace($Escaped, '(\\+)$', '$1$1')
        '"' + $Escaped + '"'
    }
    $Info.Arguments = $Quoted -join ' '
    if (-not $InheritEnvironment) {
        foreach ($Key in @($Info.EnvironmentVariables.Keys)) {
            if ($Key -match '^(UV_|PYTHON|INSTALLER_)' -or
                $Key -in @('VIRTUAL_ENV', 'CONDA_PREFIX', '__PYVENV_LAUNCHER__')) {
                $Info.EnvironmentVariables.Remove($Key)
            }
        }
        $Values = @{
            UV_UNMANAGED_INSTALL = (Join-Path $RuntimeRoot 'uv')
            UV_NO_MODIFY_PATH = '1'
            UV_NO_CONFIG = '1'
            UV_MANAGED_PYTHON = '1'
            UV_PYTHON_INSTALL_DIR = (Join-Path $RuntimeRoot 'python')
            UV_PYTHON_BIN_DIR = (Join-Path $RuntimeRoot 'python-bin')
            UV_PYTHON_INSTALL_BIN = '0'
            UV_PYTHON_INSTALL_REGISTRY = '0'
            UV_CACHE_DIR = (Join-Path $RuntimeRoot 'cache')
            UV_TOOL_DIR = (Join-Path $RuntimeRoot 'tools')
            UV_TOOL_BIN_DIR = (Join-Path $RuntimeRoot 'tool-bin')
            UV_LINK_MODE = 'copy'
            TMPDIR = (Join-Path $RuntimeRoot 'tmp')
            TMP = (Join-Path $RuntimeRoot 'tmp')
            TEMP = (Join-Path $RuntimeRoot 'tmp')
            PSModulePath = (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules')
        }
        foreach ($Entry in $Values.GetEnumerator()) {
            $Info.EnvironmentVariables[$Entry.Key] = $Entry.Value
        }
    }
    if ($Capture -or $Diagnostic) {
        $Info.RedirectStandardOutput = $true
        $Info.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    }
    $Process = [System.Diagnostics.Process]::Start($Info)
    try {
        if ($Capture -or $Diagnostic) {
            $Output = $Process.StandardOutput.ReadToEnd()
            if ($Diagnostic) {
                [Console]::Error.Write($Output)
            }
        }
        $Process.WaitForExit()
        if ($Process.ExitCode -ne 0 -and -not $AllowFailure) {
            [Console]::Error.WriteLine("Managed Python command failed ($($Process.ExitCode)): $File")
            exit $Process.ExitCode
        }
        if ($Capture) {
            return $Output.TrimEnd("`r", "`n")
        }
        if ($AllowFailure) {
            return $Process.ExitCode
        }
    }
    finally {
        $Process.Dispose()
    }
}

$Interpreter = $null
if ($args[0] -notin @('--install-uv-only', '--refresh-uv')) {
    $Pattern = '^\s*"pythonVersion"\s*:\s*"(3\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"\s*,?\s*(?://.*)?$'
    $Pins = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'dev_config.json') | ForEach-Object {
        if ($_ -match $Pattern) {
            $Matches[1]
        }
    })
    if ($Pins.Count -ne 1) {
        throw 'dev_config.json must contain one exact pythonVersion default on its own line'
    }
    $Cached = Join-Path $RuntimeRoot "venvs\$($Pins[0])\Scripts\python.exe"
    if (Test-Path -LiteralPath $Cached -PathType Leaf) {
        $Interpreter = $Cached
    }
}

if (-not $Interpreter) {
    New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeRoot 'tmp') | Out-Null
}
function Install-ManagedUv {
    $Installer = Join-Path $RuntimeRoot "tmp\install-uv-$PID.ps1"
    try {
        Invoke-WebRequest -Uri 'https://astral.sh/uv/install.ps1' -OutFile $Installer -UseBasicParsing
        $PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        Invoke-RuntimeProcess $PowerShell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Installer) -Diagnostic
        if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
            throw "uv installer did not create $Uv"
        }
    }
    finally {
        if (Test-Path -LiteralPath $Installer) {
            Remove-Item -LiteralPath $Installer
        }
    }
}
if (-not $Interpreter -and (-not (Test-Path -LiteralPath $Uv -PathType Leaf) -or $args[0] -eq '--refresh-uv')) {
    Install-ManagedUv
}
if ($args[0] -in @('--install-uv-only', '--refresh-uv')) {
    exit 0
}

if (-not $Interpreter) {
    $InstallArguments = @('--no-config', '--no-progress', 'python', 'install', '--no-bin', '--no-registry', $Pins[0])
    $InstallResult = Invoke-RuntimeProcess $Uv $InstallArguments -Diagnostic -AllowFailure
    if ($InstallResult -ne 0) {
        [Console]::Error.WriteLine("Python $($Pins[0]) installation failed; refreshing private uv and retrying once.")
        Install-ManagedUv
        Invoke-RuntimeProcess $Uv $InstallArguments -Diagnostic
    }
    $Interpreter = Invoke-RuntimeProcess $Uv @('--no-config', '--no-progress', 'python', 'find', '--system', '--managed-python', '--no-python-downloads', $Pins[0]) -Capture
    $ManagedDirectory = (Join-Path $RuntimeRoot 'python') + '\'
    if (-not (Test-Path -LiteralPath $Interpreter -PathType Leaf) -or
        -not $Interpreter.StartsWith($ManagedDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "uv did not return a private managed Python: $Interpreter"
    }
}

$Json = ConvertTo-Json -InputObject @($args) -Compress
$Encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Json))
$PreviousEncoding = [Console]::OutputEncoding
$PreviousOutputEncoding = $global:OutputEncoding
try {
    $global:OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    [Console]::OutputEncoding = $global:OutputEncoding
    if ($MyInvocation.ExpectingInput) {
        $input | & $Interpreter -X utf8 -I "$PSScriptRoot\runtime.py" --encoded-args $Encoded
    }
    else {
        & $Interpreter -X utf8 -I "$PSScriptRoot\runtime.py" --encoded-args $Encoded
    }
    $Result = $LASTEXITCODE
}
finally {
    [Console]::OutputEncoding = $PreviousEncoding
    $global:OutputEncoding = $PreviousOutputEncoding
}
exit $Result
