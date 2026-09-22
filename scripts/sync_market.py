"""全市场增量行情同步脚本。

用法:
  python scripts/sync_market.py --market all --max-codes 80
"""
from __future__ import annotations

import argparse
import json

from app.data.market_store import MarketBarStore


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market", default="all", choices=["a_share", "crypto", "all"])
    p.add_argument("--days", type=int, default=250)
    p.add_argument("--max-codes", type=int, default=80)
    args = p.parse_args()
    store = MarketBarStore()
    result = store.sync_universe(market=args.market, days=args.days, max_codes=args.max_codes)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
