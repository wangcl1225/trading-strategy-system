import os
import sys
sys.path.insert(0, r"D:\PycharmProjects\trading-strategy-system")
os.chdir(r"D:\PycharmProjects\trading-strategy-system")

from app.strategies.registry import list_strategies
print("strategies", len(list_strategies()))
from app.risk.engine import RiskConfig, calculate_rebalance
rc = RiskConfig.load()
print("risk", rc.max_single_position_pct, rc.single_stop_loss_pct, rc.portfolio_drawdown_de_risk_pct)
from app.benchmark import BenchmarkProvider
bp = BenchmarkProvider()
s = bp.get_series("hs300", days=30)
print("bench hs300", None if s is None else len(s), None if s is None else s["close"].iloc[-1])

from app.backtest.engine_v2 import BacktestEngineV2
bt = BacktestEngineV2()
print("backtest short_term a_share ...")
res = bt.run("short_term_hot", "a_share", top_n=5, holding_days=3, benchmark_id="hs300", run_sensitivity=True)
print("metrics", {k: res.get(k) for k in ["total_return_pct","max_drawdown_pct","sharpe","excess_return_pct","qualified","qualified_reason"]})
print("bench", res.get("benchmark"))
sens = res.get("sensitivity") or {}
print("sens", sens.get("conclusion"), "variants", len(sens.get("variants") or []))
print("account trades", (res.get("account") or {}).get("trade_count"), "buys", (res.get("account") or {}).get("buy_count"))
print("benchmark_equity_len", len(res.get("benchmark_equity") or []))

print("backtest jq_dual_momentum ...")
res2 = bt.run("jq_dual_momentum", "a_share", top_n=5, holding_days=5, benchmark_id="hs300", run_sensitivity=False)
print("jq metrics", {k: res2.get(k) for k in ["total_return_pct","max_drawdown_pct","qualified"]})

# rebalance smoke
r = calculate_rebalance(
    target_codes=[{"code":"600519","name":"茅台","market":"a_share","strategy":"s","weight_pct":20},
                  {"code":"000001","name":"平安","market":"a_share","strategy":"s","weight_pct":20}],
    positions=[{"code":"600519","name":"茅台","market":"a_share","strategy":"s","shares":500,"buy_price":1000,"last_price":700}],
    prices={"600519":700,"000001":12},
    equity=1000000,
    cash=200000,
)
print("rebalance buys/sells", r["buy_count"], r["sell_count"], "alerts", r["risk_state"]["alerts"])
print("orders", [(o["action"], o["code"], o["delta_shares"], o["reason"][:20]) for o in r["orders"][:6]])
