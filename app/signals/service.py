from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.config import load_config, reports_dir
from app.signals.feishu import FeishuPusher, save_feishu_markdown
from app.strategies.engine import STRATEGY_META, StrategyEngine
import app.db as db


class SignalService:
    """信号生成：落盘 JSON/Markdown + 入库 SQLite + 飞书推送。"""

    def __init__(self, engine: StrategyEngine | None = None) -> None:
        self.engine = engine or StrategyEngine()
        self.cfg = load_config()
        self.feishu = FeishuPusher()

    def generate(
        self,
        market: str | None = None,
        top_n: int | None = None,
        push_feishu: bool | None = None,
        report_date: str | None = None,
    ) -> dict[str, Any]:
        signals_cfg = self.cfg.get("signals") or {}
        min_score = float(signals_cfg.get("min_score", 55))
        top_n = top_n or 20
        market = market or str(signals_cfg.get("market") or "all")
        full = self.engine.run_all(market=market, top_n=top_n)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        day = report_date or datetime.now().strftime("%Y%m%d")
        day_iso = (
            datetime.strptime(day, "%Y%m%d").strftime("%Y-%m-%d")
            if len(day) == 8
            else day
        )

        signals: dict[str, list[dict[str, Any]]] = {}
        for key, payload in full["strategies"].items():
            items = payload.get("items") or []
            if key == "dragon_tiger":
                signals[key] = items[:top_n]
                continue
            keep = []
            for item in items:
                if float(item.get("score", 0)) >= min_score:
                    keep.append(
                        {
                            "code": item.get("code"),
                            "name": item.get("name"),
                            "market": item.get("market"),
                            "score": item.get("score"),
                            "price": item.get("price"),
                            "reason": item.get("reason"),
                            "components": item.get("components"),
                            "lhb": item.get("lhb"),
                            "fund_flow": item.get("fund_flow"),
                        }
                    )
            signals[key] = keep

        report = {
            "report_date": day_iso,
            "generated_at": now,
            "market": full.get("market"),
            "universe_size": full.get("universe_size"),
            "min_score": min_score,
            "signals": signals,
            "strategy_meta": STRATEGY_META,
            "lhb_highlight": signals.get("dragon_tiger") or [],
            "crypto_exchange": (self.cfg.get("market") or {}).get("crypto_exchange"),
            "disclaimer": (
                "信号由历史量价、龙虎榜与资金流规则生成，加密行情来自 Binance API；"
                "仅供研究与复盘，不构成买卖建议。"
            ),
        }

        reports = reports_dir()
        json_path = reports / f"signals_{day}.json"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path = reports / f"signals_{day}.md"
        try:
            save_feishu_markdown(report, str(md_path))
            report["markdown_path"] = str(md_path)
        except Exception:
            pass
        report["report_path"] = str(json_path)

        try:
            db.insert_signals(report)
            report["db_saved"] = True
        except Exception as e:
            report["db_saved"] = False
            report["db_error"] = str(e)

        should_push = push_feishu
        if should_push is None:
            should_push = bool(
                signals_cfg.get("feishu_enabled") or signals_cfg.get("push_enabled")
            )
        report["feishu_ready"] = self.feishu.is_ready()
        if should_push:
            push_result = self.feishu.push_report(report, as_card=True)
            report["feishu_push"] = push_result
            try:
                db.insert_push_log(
                    "daily_signals",
                    f"策略信号 {day_iso}",
                    self.feishu.build_text(report),
                    bool(push_result.get("ok")),
                    push_result,
                )
            except Exception:
                pass
        else:
            report["feishu_push"] = {
                "ok": False,
                "skipped": True,
                "error": "未启用飞书推送",
            }
        return report

    def query_history(
        self,
        report_date: str | None = None,
        strategy: str | None = None,
        market: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        rows = db.query_signals(
            report_date=report_date,
            strategy=strategy,
            market=market,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
        return {
            "report_date": report_date,
            "date_from": date_from,
            "date_to": date_to,
            "strategy": strategy,
            "market": market,
            "count": len(rows),
            "dates": db.signal_dates(limit=60),
            "items": rows,
        }

    def push_only(self) -> dict[str, Any]:
        return self.generate(push_feishu=True)
