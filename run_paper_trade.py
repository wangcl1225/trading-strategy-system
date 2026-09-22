"""模拟盘每日运行入口（约对应 14:45 信号买入）。

用法:
  python run_paper_trade.py
  python run_paper_trade.py --refresh-signals
"""
from __future__ import annotations

import argparse
import json
import sys

from app.paper_trade.engine import PaperTradeEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="TradeLab 模拟交易")
    parser.add_argument(
        "--refresh-signals",
        action="store_true",
        help="运行前重新扫描策略信号（默认优先复用 reports 最新信号）",
    )
    args = parser.parse_args()
    engine = PaperTradeEngine()
    result = engine.run_daily(force_refresh_signals=args.refresh_signals)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
