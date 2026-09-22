import os
import sys
sys.path.insert(0, r"D:\PycharmProjects\trading-strategy-system")
os.chdir(r"D:\PycharmProjects\trading-strategy-system")

from app.paper_trade.engine import PaperTradeEngine

eng = PaperTradeEngine()
print("strategies", eng._watch_strategies())
res = eng.watch_all_strategies(slot="morning", market="all", push=True)
print("sections", len(res.get("sections") or []))
print("push", res.get("push", {}).get("ok"), res.get("push", {}).get("error"))
for s in res.get("sections") or []:
    print(s["strategy_id"], "watch", len(s.get("watch") or []), "held", s.get("held_count"))
md = res.get("markdown") or ""
for line in md.splitlines():
    if line.startswith("| 代码"):
        print("HEADER:", line)
        break
print("ORANGE_MARKERS", md.count("orange"), "BLACK_MARKERS", md.count("black"))
print("SAMPLE\n", "\n".join(md.splitlines()[:25]))
