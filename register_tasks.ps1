# TradeLab 系统级定时任务（不依赖 MiMo Desktop 是否打开）
# 用管理员或普通用户 PowerShell 执行一次注册即可

$py = "D:\PycharmProjects\trading-strategy-system\.venv\Scripts\python.exe"
$work = "D:\PycharmProjects\trading-strategy-system"
if (-not (Test-Path $py)) { $py = "C:\Python314\python.exe" }

function Register-TradeLabTask {
  param([string]$Name, [string]$Time, [string[]]$Args)
  $action = New-ScheduledTaskAction -Execute $py -Argument ($Args -join ' ') -WorkingDirectory $work
  $trigger = New-ScheduledTaskTrigger -Daily -At $Time
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
  Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Force -Description "TradeLab 自动推送"
  Write-Output "registered $Name at $Time"
}

# 早盘 09:20 全策略关注
Register-TradeLabTask -Name "TradeLab_Watch_Morning" -Time "09:20" -Args @(
  "-c", "from app.paper_trade.engine import PaperTradeEngine as P; P().watch_all_strategies(slot='morning', market='all', push=True)"
)
# 午盘 12:30
Register-TradeLabTask -Name "TradeLab_Watch_Midday" -Time "12:30" -Args @(
  "-c", "from app.paper_trade.engine import PaperTradeEngine as P; P().watch_all_strategies(slot='midday', market='all', push=True)"
)
# 模拟盘 14:45 全策略
Register-TradeLabTask -Name "TradeLab_Paper_1445" -Time "14:45" -Args @(
  "-c", "from app.paper_trade.engine import PaperTradeEngine as P; P().run_all_strategy_accounts(force_refresh_signals=False)"
)
# 收盘 17:30 关注 + 信号
Register-TradeLabTask -Name "TradeLab_Watch_Evening" -Time "17:30" -Args @(
  "-c", "from app.paper_trade.engine import PaperTradeEngine as P; P().watch_all_strategies(slot='evening', market='all', push=True)"
)
Register-TradeLabTask -Name "TradeLab_Daily_Push" -Time "17:35" -Args @(
  "run_daily.py", "--market", "all", "--push"
)

Write-Output "done. 查看：Get-ScheduledTask -TaskName 'TradeLab_*'"
