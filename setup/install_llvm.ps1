if (-not (Get-Command clang -ErrorAction SilentlyContinue)) {
  winget install LLVM.LLVM --source winget --accept-package-agreements --accept-source-agreements
}
if (-not ($env:PATH -like "*$env:ProgramFiles\LLVM\bin*")) {
  $env:PATH = "$env:ProgramFiles\LLVM\bin;$env:PATH"
}
