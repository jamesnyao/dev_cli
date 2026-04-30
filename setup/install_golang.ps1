$GoVersion = "1.24.2"
$GoZip = "go${GoVersion}.windows-amd64.zip"
$GoUrl = "https://go.dev/dl/$GoZip"
$GoRoot = "C:\Go"

if (Test-Path "$GoRoot\bin\go.exe") {
    return
}

$tempZip = Join-Path $env:TEMP $GoZip

Write-Host "Downloading Go $GoVersion..."
Invoke-WebRequest -Uri $GoUrl -OutFile $tempZip -UseBasicParsing

Write-Host "Extracting to $GoRoot..."
if (Test-Path $GoRoot) { Remove-Item -Recurse -Force $GoRoot }
Expand-Archive -Path $tempZip -DestinationPath "C:\" -Force
Remove-Item $tempZip

if (Test-Path "$GoRoot\bin\go.exe") {
    Write-Host "Go $GoVersion installed successfully."
} else {
    Write-Error "Go installation failed."
}
