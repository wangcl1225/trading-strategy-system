"""全策略模拟盘入口（供 Windows 计划任务调用）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.paper_trade.engine import PaperTradeEngine  # noqa: E402


def main() -> int:
    rs = PaperTradeEngine().run_all_strategy_accounts(force_refresh_signals=False)
    print("accounts", len(rs))
    errs = 0
    for r in rs:
        if r.get("error"):
            errs += 1
        print(r.get("strategy_id") or r.get("account_id"), "buys", len(r.get("buys") or []), "sells", len(r.get("sells") or []), "err", r.get("error"))
    return 1 if errs == len(rs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
