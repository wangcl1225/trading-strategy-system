import os
import sys
sys.path.insert(0, r"D:\PycharmProjects\trading-strategy-system")
os.chdir(r"D:\PycharmProjects\trading-strategy-system")

from datetime import datetime
from app.paper_trade.engine import PaperTradeEngine
import app.db as db

eng = PaperTradeEngine()
# 确保有账户
r = eng.apply_strategy("short_term_hot")
print("apply", r.get("ok"), r.get("account", {}).get("account_id"))

acc = "acc_short_term_hot"
run = eng.run_daily(force_refresh_signals=False, account_id=acc, strategy_id="short_term_hot")
print("date", run.get("date"))
print("buys", len(run.get("buys") or []), "sells", len(run.get("sells") or []))
print("buy_table", run.get("buy_table"))
print("mark_table_sample", (run.get("mark_table") or [])[:3])
print("push_buy_ok", (run.get("push_buy") or {}).get("ok"), (run.get("push_buy") or {}).get("error"))
print("push_brief_ok", (run.get("push_brief") or {}).get("ok"), (run.get("push_brief") or {}).get("error"))
md = (run.get("push_brief") or {}).get("markdown") or ""
print("brief_md_head:\n", md[:600])

mor = eng.morning_watchlist(account_id=acc)
print("morning_as_of", mor.get("as_of_date"))
print("morning_push", (mor.get("push") or {}).get("ok"))
print("watch_table", (mor.get("watch_table") or [])[:3])
print("morning_md_head:\n", (mor.get("watchlist_text") or "")[:500])

logs = db.query_push_logs(account_id=acc, limit=5)
for lg in logs:
    print("LOG", lg.get("push_type"), lg.get("ok"), (lg.get("content") or "")[:120].replace("\n", " / "))
