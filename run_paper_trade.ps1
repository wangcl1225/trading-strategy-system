param(
    [switch]$RefreshSignals
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "C:\Python314\python.exe" }
$argsList = @("run_paper_trade.py")
if ($RefreshSignals) { $argsList += "--refresh-signals" }
& $py @argsList
exit $LASTEXITCODE
