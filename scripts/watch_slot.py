"""时段关注推送入口（供 Windows 计划任务调用）。

用法: python scripts/watch_slot.py morning|midday|evening
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.paper_trade.engine import PaperTradeEngine  # noqa: E402


def main() -> int:
    slot = sys.argv[1] if len(sys.argv) > 1 else "morning"
    r = PaperTradeEngine().watch_all_strategies(slot=slot, market="all", push=True)
    ok = bool((r.get("push") or {}).get("ok"))
    print("slot", slot, "push_ok", ok, "sections", len(r.get("sections") or []), "as_of", r.get("as_of_date"))
    if not ok:
        print("push_error", (r.get("push") or {}).get("error") or (r.get("push") or {}).get("channels"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
