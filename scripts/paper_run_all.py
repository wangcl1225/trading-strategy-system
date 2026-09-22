from app.paper_trade.engine import PaperTradeEngine

rs = PaperTradeEngine().run_all_strategy_accounts(force_refresh_signals=False)
print("accounts", len(rs))
for r in rs:
    print(r.get("strategy_id") or r.get("account_id"), "buys", len(r.get("buys") or []), "sells", len(r.get("sells") or []))
