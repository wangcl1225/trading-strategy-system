# TradeLab 每日信号推送（Windows 计划任务可调用本脚本）
# 用法:
#   powershell -ExecutionPolicy Bypass -File run_daily.ps1
#   powershell -ExecutionPolicy Bypass -File run_daily.ps1 -Market all -Push
param(
    [string]$Market = "a_share",
    [int]$TopN = 20,
    [switch]$Push,
    [switch]$NoPush
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = "C:\Python314\python.exe"
}
$argsList = @("run_daily.py", "--market", $Market, "--top-n", "$TopN")
if ($Push) { $argsList += "--push" }
if ($NoPush) { $argsList += "--no-push" }
& $py @argsList
exit $LASTEXITCODE
