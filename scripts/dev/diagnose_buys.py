import os
import sys
sys.path.insert(0, r"D:\PycharmProjects\trading-strategy-system")
os.chdir(r"D:\PycharmProjects\trading-strategy-system")

from app.paper_trade.engine import PaperTradeEngine
from app.strategies.registry import run_strategy_by_id
import app.db as db

eng = PaperTradeEngine()
for sid in eng._watch_strategies():
    acc = eng.account_id_for(sid)
    mtm = eng.mark_to_market(acc)
    opens = mtm.get("open_positions") or []
    items = eng._signals_for_strategy(sid)
    print(
        f"{sid:22} open={len(opens):2} signals={len(items):2} "
        f"cash={mtm.get('cash')} score_top={items[0].get('score') if items else None}"
    )

# 现场扫描一个 jq 策略，看是否有信号
frames, names = eng.engine.load_frames("a_share")
print("frames", len(frames))
for sid in ["jq_dual_momentum", "long_term_layout", "bottom_fishing"]:
    items = run_strategy_by_id(sid, frames, names, top_n=5)
    print("scan", sid, "n", len(items), "scores", [i.get("score") for i in items[:3]])
    # 试买
    if items:
        run = eng.run_daily(force_refresh_signals=False, strategy_id=sid)
        print("  run buys", len(run.get("buys") or []), "skips", (run.get("skips") or [])[:2])
