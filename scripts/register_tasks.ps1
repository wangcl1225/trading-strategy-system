$ErrorActionPreference = "Stop"
$work = "D:\PycharmProjects\trading-strategy-system"
$py = Join-Path $work ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "C:\Python314\python.exe" }

function New-TradeLabTask {
  param([string]$Name, [string]$Time, [string]$Script, [string[]]$ExtraArgs = @())
  $argTail = ($ExtraArgs -join ' ')
  $cmd = "/c cd /d `"$work`" && `"$py`" `"$Script`" $argTail"
  $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd -WorkingDirectory $work
  $trigger = New-ScheduledTaskTrigger -Daily -At $Time
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
  Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Force -Description "TradeLab schedule" | Out-Null
  Write-Output ("REGISTERED {0} {1}" -f $Name, $Time)
}

New-TradeLabTask -Name "TradeLab_Watch_Morning" -Time "09:20" -Script "scripts\watch_slot.py" -ExtraArgs @("morning")
New-TradeLabTask -Name "TradeLab_Watch_Midday"  -Time "12:30" -Script "scripts\watch_slot.py" -ExtraArgs @("midday")
New-TradeLabTask -Name "TradeLab_Paper_1445"    -Time "14:45" -Script "scripts\paper_run_all.py"
New-TradeLabTask -Name "TradeLab_Watch_Evening" -Time "17:30" -Script "scripts\watch_slot.py" -ExtraArgs @("evening")
New-TradeLabTask -Name "TradeLab_Daily_Signals" -Time "17:35" -Script "run_daily.py" -ExtraArgs @("--market","all","--push")

Get-ScheduledTask -TaskName "TradeLab_*" | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo
  Write-Output ("INFO {0} last={1} result={2} next={3}" -f $_.TaskName, $i.LastRunTime, $i.LastTaskResult, $i.NextRunTime)
}
