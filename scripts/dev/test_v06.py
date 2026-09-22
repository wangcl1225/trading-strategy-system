import sys

sys.path.insert(0, r"D:\PycharmProjects\trading-strategy-system-wt-v06")
import os

os.chdir(r"D:\PycharmProjects\trading-strategy-system-wt-v06")

from app.data.market_store import MarketBarStore
from app.data.joinquant import JoinQuantClient
from app.analytics.report_export import export_backtest_report

print("=== 1 incremental market store ===")
store = MarketBarStore()
r1 = store.sync_universe(market="a_share", days=80, max_codes=5)
print("sync1", r1.get("full_synced"), r1.get("incremental_synced"), "db_bars", r1.get("db_bars"))
r2 = store.sync_universe(market="a_share", days=80, max_codes=5)
print("sync2 incremental", r2.get("incremental_synced"), "full", r2.get("full_synced"))
code = r1["sample"][0]["code"] if r1.get("sample") else "600519"
df = store.get_history("a_share", code, days=20)
print("hist", code, None if df is None else len(df))

print("=== 2 joinquant adapter ===")
jq = JoinQuantClient().status()
print("jq", jq)

print("=== 3 report export ===")
fake = {
    "strategy": {"id": "short_term_hot", "name": "短线热门", "source": "builtin"},
    "dates": ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"],
    "equity": [1000000, 1002000, 1001000, 1005000],
    "benchmark_equity": [1000000, 1000500, 999800, 1001200],
    "total_return_pct": 0.5,
    "max_drawdown_pct": 0.2,
    "sharpe": 1.1,
    "annualized_pct": 12.0,
    "excess_return_pct": 0.3,
    "benchmark": {"name": "沪深300", "total_return_pct": 0.12, "max_drawdown_pct": 0.5},
    "qualified": True,
    "qualified_reason": "示例",
    "monthly_returns": [{"month": "2026-09", "return_pct": 0.5}],
    "pnl_distribution": {"count": 2, "bins": [{"from": -1, "to": 0, "count": 1}, {"from": 0, "to": 1, "count": 1}]},
    "sensitivity": {"stable": True},
    "execution_rule": "T+1",
}
out = export_backtest_report(fake, fmt="html")
print("export", out.get("path"), out.get("ok"))
out2 = export_backtest_report(fake, fmt="md")
print("export_md", out2.get("path"))
print("ALL_OK")
