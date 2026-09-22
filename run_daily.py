"""每日信号生成 + 飞书推送入口。

用法:
  python run_daily.py
  python run_daily.py --market all --top-n 15 --push
"""
from __future__ import annotations

import argparse
import json
import sys

from app.config import load_config
from app.signals.service import SignalService


def main() -> int:
    cfg = load_config()
    sig_cfg = cfg.get("signals") or {}
    parser = argparse.ArgumentParser(description="TradeLab 每日策略信号")
    parser.add_argument("--market", default=str(sig_cfg.get("market") or "a_share"))
    parser.add_argument("--top-n", type=int, default=int(sig_cfg.get("top_n") or 20))
    parser.add_argument(
        "--push",
        action="store_true",
        help="强制推送飞书（即使 feishu_enabled=false）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="只生成报告不推送",
    )
    args = parser.parse_args()

    push: bool | None
    if args.no_push:
        push = False
    elif args.push:
        push = True
    else:
        push = None  # 按配置

    service = SignalService()
    report = service.generate(market=args.market, top_n=args.top_n, push_feishu=push)

    summary = {
        "generated_at": report.get("generated_at"),
        "market": report.get("market"),
        "universe_size": report.get("universe_size"),
        "report_path": report.get("report_path"),
        "markdown_path": report.get("markdown_path"),
        "feishu_ready": report.get("feishu_ready"),
        "feishu_push": report.get("feishu_push"),
        "signal_counts": {
            k: len(v) for k, v in (report.get("signals") or {}).items()
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    push_result = report.get("feishu_push") or {}
    if push_result.get("skipped"):
        print(
            "\n[提示] 未推送飞书。请在 config.yaml 填写 signals.feishu_webhook，"
            "并设置 feishu_enabled=true，或运行时加 --push。",
            file=sys.stderr,
        )
        return 0 if report.get("report_path") else 1
    if not push_result.get("ok"):
        print(f"\n[失败] 飞书推送未成功: {push_result}", file=sys.stderr)
        return 2
    print("\n[成功] 已生成信号并推送飞书。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
