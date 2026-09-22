import os
import sys
sys.path.insert(0, r"D:\PycharmProjects\trading-strategy-system")
os.chdir(r"D:\PycharmProjects\trading-strategy-system")

from app.paper_trade.engine import PaperTradeEngine

eng = PaperTradeEngine()
print("=== run all strategy accounts (buy fix) ===")
results = eng.run_all_strategy_accounts(force_refresh_signals=False)
for r in results:
    sid = r.get("strategy_id") or r.get("account_id")
    buys = r.get("buys") or []
    print(f"{sid:22} buys={len(buys)} open_before_run skip_n={len(r.get('skips') or [])}")
    if buys[:1]:
        print("   sample", buys[0].get("code"), buys[0].get("price"), buys[0].get("market"))

print("=== watch page data sort sample ===")
data = eng.watch_all_strategies(slot="morning", market="all", push=False)
for s in (data.get("sections") or [])[:3]:
    print("section", s["strategy_id"], "held", s.get("held_count"), "holdings", len(s.get("holdings") or []))
    for w in (s.get("watch") or [])[:6]:
        print(
            " ",
            w.get("market"),
            "held" if w.get("held") else "free",
            w.get("code"),
            "chg_buy",
            w.get("hold_change_pct"),
            "days",
            w.get("hold_days"),
            "score",
            w.get("score"),
        )
print("push", data.get("push", {}).get("ok"), "legend", data.get("legend"))
