param([string]$Market = "all")
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "C:\Python314\python.exe" }
& $py run_morning.py --market $Market
exit $LASTEXITCODE
