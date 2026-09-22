"""早盘 9:20 关注提醒。用法: python run_morning.py [--market all]"""
from __future__ import annotations

import argparse
import json
import sys

from app.paper_trade.engine import PaperTradeEngine


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="all")
    args = parser.parse_args()
    result = PaperTradeEngine().morning_watchlist(market=args.market)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    push = result.get("push") or {}
    return 0 if push.get("ok") or result.get("watchlist_text") else 1


if __name__ == "__main__":
    raise SystemExit(main())
