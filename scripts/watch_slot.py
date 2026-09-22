import sys
from app.paper_trade.engine import PaperTradeEngine

slot = sys.argv[1] if len(sys.argv) > 1 else "morning"
r = PaperTradeEngine().watch_all_strategies(slot=slot, market="all", push=True)
print("slot", slot, "push_ok", (r.get("push") or {}).get("ok"), "sections", len(r.get("sections") or []))
