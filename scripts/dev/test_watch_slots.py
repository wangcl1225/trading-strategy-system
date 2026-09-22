import os
import sys
sys.path.insert(0, r"D:\PycharmProjects\trading-strategy-system")
os.chdir(r"D:\PycharmProjects\trading-strategy-system")

from app.paper_trade.engine import PaperTradeEngine

eng = PaperTradeEngine()
print("watch_strategies", eng._watch_strategies())
print("running watch_all_strategies morning ...")
res = eng.watch_all_strategies(slot="morning", market="all", push=True)
print("slot", res.get("slot"), res.get("slot_label"), res.get("as_of_date"))
print("strategies", len(res.get("sections") or []))
for s in (res.get("sections") or [])[:4]:
    print(" -", s.get("strategy_id"), "held", s.get("held_count"), "watch", len(s.get("watch") or []))
print("push_ok", (res.get("push") or {}).get("ok"), (res.get("push") or {}).get("error"))
md = res.get("markdown") or ""
print("--- md excerpt ---")
print(md[:900])
print("has_orange", "orange" in md)
print("score_last_header", "评分" in md.split("\n")[0] or any("评分" in line and line.strip().endswith("评分 |") or "| 评分 |" in line for line in md.split("\n")[:30]))
# 检查评分是否在表头最后
for line in md.split("\n"):
    if line.startswith("| 代码"):
        print("HEADER", line)
        break

print("running run-all (may take a while) ...")
allres = eng.run_all_strategy_accounts(force_refresh_signals=False)
print("ran", len(allres), "accounts")
for r in allres:
    print(" ", r.get("strategy_id") or r.get("account_id"), "buys", len(r.get("buys") or []), "err", r.get("error"))
