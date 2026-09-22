from __future__ import annotations

"""模拟账户：每个策略独立账户，资金/持仓/成交完全隔离。"""

import logging
from datetime import datetime
from typing import Any

from app.config import ROOT, load_config
from app.data.a_share import AShareDataProvider, code_to_sina_symbol
from app.risk.engine import RiskConfig, calculate_rebalance, compute_risk_state
from app.signals.service import SignalService
from app.strategies.engine import StrategyEngine
from app.strategies.registry import STRATEGY_REGISTRY, list_strategies, run_strategy_by_id
import app.db as db
from app.backtest.engine_v2 import DEFAULT_COSTS, trade_cost

logger = logging.getLogger(__name__)

STRATEGY_LABEL = {
    "short_term_hot": "短线热门",
    "long_term_layout": "长线布局",
    "bottom_fishing": "抄底潜伏",
    "jq_dual_momentum": "双动量轮动",
    "jq_small_mom_rotation": "小市值动量",
    "jq_low_vol_quality": "低波动质量",
    "jq_turtle_breakout": "海龟突破",
    "jq_reversal_oversold": "超跌反转",
    "jq_grid_mean_rev": "网格均值回归",
}

DEFAULT_ACCOUNT = "default"


def pd_to_date_str(date: str) -> str:
    return str(date)[:10]


def normalize_symbol(code: str, market: str | None = None) -> tuple[str, str]:
    """去掉 ASHARE:/CRYPTO: 前缀，返回 (纯净代码, market)。"""
    raw = str(code or "")
    m = (market or "").lower()
    if raw.startswith("ASHARE:"):
        raw = raw.split(":", 1)[1]
        m = m or "a_share"
    elif raw.startswith("CRYPTO:"):
        raw = raw.split(":", 1)[1]
        m = m or "crypto"
    if not m:
        m = "crypto" if ("-USDT" in raw.upper() or raw.upper().endswith("USDT")) else "a_share"
    if m == "crypto":
        return raw.upper().replace("_", "-"), "crypto"
    if raw and raw[0].isdigit():
        return raw.zfill(6), "a_share"
    return raw, m or "a_share"


def _weekdays_between(start: str, end: str) -> int:
    from datetime import timedelta
    s = datetime.strptime(start[:10], "%Y-%m-%d").date()
    e = datetime.strptime(end[:10], "%Y-%m-%d").date()
    if e <= s:
        return 0
    days, cur = 0, s
    while cur < e:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


class PaperTradeEngine:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.pt_cfg = self.cfg.get("paper_trade") or {}
        self.engine = StrategyEngine()
        self.signals = SignalService(self.engine)
        self.ashare = AShareDataProvider()
        self.risk = RiskConfig.load()
        self.costs = self.cfg.get("costs") or DEFAULT_COSTS
        self.data_dir = ROOT / str(self.pt_cfg.get("data_dir") or "data/paper_trade")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db.init_db()
        self.quote_cache: dict[str, float] = {}

    @property
    def default_initial_cash(self) -> float:
        return float(self.pt_cfg.get("initial_cash") or 1_000_000)

    @property
    def active_strategies(self) -> list[str]:
        ids = self.pt_cfg.get("strategy_ids") or ["short_term_hot"]
        return [str(x) for x in ids]

    @staticmethod
    def account_id_for(strategy_id: str) -> str:
        """策略账户 ID 与策略一一对应，隔离资金与持仓。"""
        return f"acc_{strategy_id}"

    def _holding_days(self, strategy: str) -> int:
        m = self.pt_cfg.get("holding_days") or {}
        if strategy in m:
            return int(m[strategy])
        meta = STRATEGY_REGISTRY.get(strategy)
        return int(meta.get("default_holding_days", 5)) if meta else 5

    def _push(self, push_type: str, title: str, text: str, account_id: str | None = None) -> dict[str, Any]:
        from app.notify import MultiNotifier
        notifier = MultiNotifier()
        if not notifier.enabled_channels:
            from app.signals.feishu import FeishuPusher
            pusher = FeishuPusher()
            if not pusher.is_ready():
                db.insert_push_log(push_type, title, text, False, {"error": "无可用通知渠道"}, account_id)
                return {"ok": False, "error": "无可用通知渠道"}
            result = pusher.push_text(f"【{title}】\n{text}")
            db.insert_push_log(push_type, title, text, bool(result.get("ok")), result, account_id)
            return result
        result = notifier.push_text(title, text)
        db.insert_push_log(push_type, title, text, bool(result.get("ok")), result, account_id)
        return result

    def _push_table(
        self,
        push_type: str,
        title: str,
        headers: list[str],
        rows: list[list[Any]],
        subtitle: str = "",
        footer: str = "",
        account_id: str | None = None,
        template: str = "blue",
    ) -> dict[str, Any]:
        from app.signals.feishu import FeishuPusher
        from app.notify import MultiNotifier

        md_table = FeishuPusher.format_md_table(headers, rows)
        text_table = FeishuPusher.format_text_table(headers, rows)
        markdown = subtitle + "\n\n" + md_table + ("\n\n" + footer if footer else "")
        plain = subtitle + "\n\n" + text_table + ("\n\n" + footer if footer else "")

        notifier = MultiNotifier()
        result = notifier.push_card(title, markdown)
        # 若 markdown 渠道失败，回退纯文本
        if not result.get("ok"):
            result = notifier.push_text(title, plain)
            result["fallback_text"] = True
        db.insert_push_log(push_type, title, markdown, bool(result.get("ok")), result, account_id)
        result["markdown"] = markdown
        result["table_text"] = text_table
        return result

    def get_ohlcv_on_date(self, code: str, market: str, date: str) -> dict[str, Any] | None:
        """取指定日期的日K（收盘价/前收盘/涨跌幅）。date: YYYY-MM-DD"""
        code, market = normalize_symbol(code, market)
        market = (market or "a_share").lower()
        try:
            if market == "crypto":
                df = self.engine.crypto.get_history(str(code).upper(), days=60)
            else:
                df = self.ashare.get_history(str(code).zfill(6), days=60)
            if df is None or df.empty:
                return None
            d = pd_to_date_str(date)
            work = df.copy()
            work["date"] = work["date"].astype(str).str.slice(0, 10)
            matched = work[work["date"] == d]
            if matched.empty:
                return None
            idx = matched.index[-1]
            close = float(work.loc[idx, "close"])
            prev_close = None
            if idx > 0:
                prev_close = float(work.loc[idx - 1, "close"])
            change_pct = None
            if prev_close:
                change_pct = (close / prev_close - 1) * 100
            return {
                "date": d,
                "close": close,
                "prev_close": prev_close,
                "market_change_pct": round(change_pct, 2) if change_pct is not None else None,
                "open": float(work.loc[idx, "open"]) if "open" in work.columns else None,
                "high": float(work.loc[idx, "high"]) if "high" in work.columns else None,
                "low": float(work.loc[idx, "low"]) if "low" in work.columns else None,
            }
        except Exception as e:
            logger.debug("ohlcv %s %s %s failed: %s", code, market, date, e)
            return None

    @staticmethod
    def _pos_mark_rows(
        positions: list[dict[str, Any]],
        as_of_date: str,
        price_lookup,
    ) -> tuple[list[list[Any]], list[dict[str, Any]]]:
        """持仓按指定日收盘价计算浮动收益/涨幅 + 行情涨幅，生成表格行。"""
        rows = []
        details = []
        for p in positions:
            code = str(p.get("code") or "")
            market = str(p.get("market") or "a_share")
            buy_price = float(p.get("buy_price") or 0)
            shares = float(p.get("shares") or 0)
            buy_date = str(p.get("buy_date") or "")[:10]
            ohlcv = price_lookup(code, market, as_of_date)
            close = float(ohlcv.get("close")) if ohlcv and ohlcv.get("close") is not None else None
            if close is None:
                close = float(p.get("last_price") or buy_price or 0)
            mkt_chg = ohlcv.get("market_change_pct") if ohlcv else None
            hold_pnl = (close - buy_price) * shares if buy_price and shares else 0.0
            hold_chg = (close / buy_price - 1) * 100 if buy_price else 0.0
            rows.append(
                [
                    code,
                    p.get("name") or "",
                    f"{buy_price:.4f}".rstrip("0").rstrip("."),
                    buy_date,
                    f"{close:.4f}".rstrip("0").rstrip("."),
                    f"{hold_pnl:+.2f}",
                    f"{hold_chg:+.2f}%",
                    f"{mkt_chg:+.2f}%" if mkt_chg is not None else "-",
                ]
            )
            details.append(
                {
                    "code": code,
                    "name": p.get("name"),
                    "buy_price": buy_price,
                    "buy_date": buy_date,
                    "close_price": close,
                    "close_date": as_of_date,
                    "hold_pnl": round(hold_pnl, 2),
                    "hold_change_pct": round(hold_chg, 2),
                    "market_change_pct": mkt_chg,
                    "shares": shares,
                }
            )
        return rows, details

    def get_price(self, code: str, market: str = "a_share") -> float | None:
        code, market = normalize_symbol(code, market)
        market = (market or "a_share").lower()
        if market == "crypto":
            sym = str(code).upper()
            price = self.engine.crypto.get_price(sym)
            if price:
                return price
            df = self.engine.crypto.get_history(sym, days=5)
            return float(df["close"].iloc[-1]) if df is not None and len(df) else None
        key = str(code).zfill(6)
        if key in self.quote_cache:
            return self.quote_cache[key]
        try:
            import requests
            resp = requests.get(
                f"https://hq.sinajs.cn/list={code_to_sina_symbol(key)}",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"},
                timeout=6,
            )
            text = resp.text
            if "=" in text:
                parts = text.split("=", 1)[1].strip().strip('"').split(",")
                if len(parts) >= 4:
                    price = float(parts[3])
                    if price > 0:
                        self.quote_cache[key] = price
                        return price
        except Exception:
            pass
        df = self.ashare.get_history(key, days=8)
        if df is not None and len(df):
            price = float(df["close"].iloc[-1])
            self.quote_cache[key] = price
            return price
        return None

    def get_open_price(self, code: str, market: str = "a_share") -> float | None:
        market = (market or "a_share").lower()
        if market == "crypto":
            df = self.engine.crypto.get_history(str(code).upper(), days=3)
            if df is not None and len(df):
                return float(df["open"].iloc[-1])
            return self.get_price(code, market)
        key = str(code).zfill(6)
        df = self.ashare.get_history(key, days=5)
        if df is not None and len(df):
            return float(df["open"].iloc[-1])
        return self.get_price(code, market)

    def _signals_for_strategy(self, strategy_id: str, market: str | None = None, allow_live: bool = True) -> list[dict[str, Any]]:
        """优先库/报告信号；若为空则实时扫描该策略，保证各策略都能建仓。"""
        dates = db.signal_dates(limit=5)
        for d in dates:
            rows = db.query_signals(report_date=d, strategy=strategy_id, limit=50)
            if rows:
                return [
                    {
                        "code": r["code"],
                        "name": r["name"],
                        "market": "crypto" if "-USDT" in str(r["code"]) else "a_share",
                        "score": r["score"],
                        "price": r["price"],
                        "reason": r["reason"],
                    }
                    for r in rows
                ]
        import json
        reports = ROOT / str(self.cfg["app"].get("reports_dir") or "reports")
        for path in sorted(reports.glob("signals_*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items = (data.get("signals") or {}).get(strategy_id) or []
                if items:
                    return items
            except Exception:
                continue

        if not allow_live or strategy_id not in STRATEGY_REGISTRY:
            return []
        # 实时扫描并入库，避免“只有短线有信号/有持仓”
        try:
            market = market or str((self.cfg.get("signals") or {}).get("market") or "all")
            top_n = int((self.cfg.get("signals") or {}).get("top_n") or 20)
            frames, names = self.engine.load_frames(market)
            items = run_strategy_by_id(strategy_id, frames, names, top_n=top_n)
            today = datetime.now().strftime("%Y-%m-%d")
            if items:
                db.insert_signals(
                    {
                        "report_date": today,
                        "generated_at": datetime.now().isoformat(timespec="seconds"),
                        "market": market,
                        "signals": {strategy_id: items},
                    }
                )
            return items
        except Exception as e:
            logger.warning("live signal %s failed: %s", strategy_id, e)
            return []

    def apply_strategy(self, strategy_id: str, create_if_missing: bool = True) -> dict[str, Any]:
        """应用策略到独立模拟账户。返回明确成功/失败原因。"""
        if strategy_id not in STRATEGY_REGISTRY:
            return {
                "ok": False,
                "code": "STRATEGY_NOT_FOUND",
                "reason": f"策略不存在或已失效：{strategy_id}",
                "available": [s["id"] for s in list_strategies()],
            }
        meta = STRATEGY_REGISTRY[strategy_id]
        account_id = self.account_id_for(strategy_id)
        try:
            existing = db.get_account(account_id)
            if existing is None and not create_if_missing:
                return {
                    "ok": False,
                    "code": "ACCOUNT_MISSING",
                    "reason": f"策略账户不存在：{account_id}，且未允许自动创建",
                }
            acc = db.ensure_account(
                account_id=account_id,
                strategy_id=strategy_id,
                strategy_name=meta["name"],
                strategy_source=meta["source_label"],
                initial_cash=self.default_initial_cash,
                note=f"独立账户 · {meta['desc']}",
            )
            cash = db.account_cash(account_id)
            opens = db.list_positions(account_id=account_id, status="open")
            stats = db.portfolio_stats_from_db(account_id=account_id)
            # 更新“当前活跃策略”
            self.pt_cfg["strategy_ids"] = [strategy_id]
            self.cfg.setdefault("paper_trade", {})["strategy_ids"] = [strategy_id]

            warnings = []
            if cash <= 0:
                warnings.append("可用现金不足，请检查该账户是否已满仓或资金耗尽")
            exposure = 0.0
            equity = cash
            mv = 0.0
            for p in opens:
                last = self.get_price(str(p.get("code")), str(p.get("market") or "a_share")) or float(p.get("buy_price") or 0)
                mv += float(p.get("shares") or 0) * last
            equity = cash + mv
            if equity > 0:
                exposure = mv / equity * 100
            if exposure >= 99:
                warnings.append("该策略账户仓位已接近满仓（≥99%），新信号可能无法买入")

            return {
                "ok": True,
                "code": "OK",
                "reason": (
                    f"已{'创建并' if acc.get('created') else ''}绑定独立模拟账户"
                    if not warnings
                    else f"已绑定独立账户，但请注意：{'；'.join(warnings)}"
                ),
                "active": [strategy_id],
                "account": {
                    "account_id": account_id,
                    "strategy_id": strategy_id,
                    "strategy_name": meta["name"],
                    "strategy_source": meta["source_label"],
                    "desc": meta["desc"],
                    "initial_cash": acc.get("initial_cash"),
                    "cash": round(cash, 2),
                    "open_positions": len(opens),
                    "buy_count": stats.get("buy_count"),
                    "sell_count": stats.get("sell_count"),
                    "exposure_pct": round(exposure, 2),
                    "created": bool(acc.get("created")),
                    "holding_days": self._holding_days(strategy_id),
                },
                "warnings": warnings,
                "isolation": "每个策略独立账户：资金/持仓/成交互不影响",
            }
        except Exception as e:
            logger.exception("apply strategy failed")
            return {
                "ok": False,
                "code": "APPLY_FAILED",
                "reason": f"应用失败：{type(e).__name__}: {e}",
            }

    def switch_strategies(self, strategy_ids: list[str]) -> dict[str, Any]:
        """兼容旧接口：批量应用，为每个策略确保独立账户。"""
        results = []
        ok_ids = []
        last_err = None
        for sid in strategy_ids:
            r = self.apply_strategy(sid, create_if_missing=True)
            results.append(r)
            if r.get("ok"):
                ok_ids.append(sid)
            else:
                last_err = r.get("reason")
        if not ok_ids:
            return {"ok": False, "reason": last_err or "没有可用策略", "results": results}
        return {"ok": True, "active": ok_ids, "results": results}

    def mark_to_market(self, account_id: str | None = None) -> dict[str, Any]:
        account_id = account_id or DEFAULT_ACCOUNT
        acc = db.get_account(account_id)
        if acc is None:
            # 兼容旧数据：无账户时用 strategy 字段当账户
            acc = {
                "account_id": account_id,
                "strategy_id": account_id.replace("acc_", "") if account_id.startswith("acc_") else account_id,
                "strategy_name": STRATEGY_LABEL.get(account_id.replace("acc_", ""), account_id),
                "initial_cash": self.default_initial_cash,
            }
            db.ensure_account(
                account_id=account_id,
                strategy_id=str(acc.get("strategy_id")),
                strategy_name=acc.get("strategy_name"),
                initial_cash=self.default_initial_cash,
            )
        cash = db.account_cash(account_id)
        opens = db.list_positions(account_id=account_id, status="open")
        market_value = 0.0
        rows = []
        today = datetime.now().strftime("%Y-%m-%d")
        for p in opens:
            market = str(p.get("market") or "a_share")
            price = self.get_price(str(p.get("code")), market)
            buy_price = float(p.get("buy_price") or 0)
            shares = float(p.get("shares") or 0)
            mv = (price or buy_price) * shares
            market_value += mv
            elapsed = _weekdays_between(str(p.get("buy_date")), today)
            rows.append(
                {
                    **p,
                    "last_price": price,
                    "market_value": round(mv, 2),
                    "unrealized_pnl": round(((price or buy_price) - buy_price) * shares, 2),
                    "unrealized_pnl_pct": round(((price or buy_price) - buy_price) / buy_price * 100, 2) if buy_price else 0,
                    "hold_change_pct": round(((price or buy_price) - buy_price) / buy_price * 100, 2) if buy_price else 0,
                    "hold_days": elapsed,
                    "hold_days_elapsed": elapsed,
                    "days_to_sell": max(0, int(p.get("sell_after_trading_days") or 0) - elapsed),
                    "strategy_label": STRATEGY_LABEL.get(p.get("strategy"), p.get("strategy")),
                    "account_id": account_id,
                    "market_type": "crypto" if str(p.get("market") or "") == "crypto" else "a_share",
                }
            )
        initial = float(acc.get("initial_cash") or self.default_initial_cash)
        equity = cash + market_value
        peak = max(initial, equity)
        for r in db.query_daily_runs(account_id=account_id, limit=40):
            eq = float((r.get("portfolio") or {}).get("equity") or 0)
            if eq:
                peak = max(peak, eq)
        risk_state = compute_risk_state(equity, cash, rows, peak, 0, self.risk)
        stats = db.portfolio_stats_from_db(account_id=account_id)
        sid = acc.get("strategy_id") or account_id.replace("acc_", "")
        return {
            "account_id": account_id,
            "strategy_id": sid,
            "strategy_name": acc.get("strategy_name") or STRATEGY_LABEL.get(sid, sid),
            "strategy_source": acc.get("strategy_source") or "",
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "equity": round(equity, 2),
            "initial_cash": initial,
            "total_pnl": round(equity - initial, 2),
            "total_pnl_pct": round((equity - initial) / initial * 100, 2) if initial else 0,
            "open_positions": rows,
            "db_stats": stats,
            "risk_state": {
                "total_exposure_pct": risk_state.total_exposure_pct,
                "portfolio_drawdown_pct": risk_state.portfolio_drawdown_pct,
                "de_risk_active": risk_state.de_risk_active,
                "max_total_position_allowed_pct": risk_state.max_total_position_allowed_pct,
                "max_single_position_pct": self.risk.max_single_position_pct,
                "stop_loss_flags": risk_state.stop_loss_flags,
                "alerts": risk_state.alerts,
                "need_reevaluate": risk_state.need_reevaluate,
            },
        }

    def _sell_due(self, account_id: str, strategy_id: str, today: str) -> list[dict[str, Any]]:
        closed = []
        for p in list(db.list_positions(account_id=account_id, status="open")):
            elapsed = _weekdays_between(str(p.get("buy_date")), today)
            market = str(p.get("market") or "a_share")
            cost = float(p.get("buy_price") or 0)
            last = self.get_price(str(p.get("code")), market)
            pnl_pct = ((last - cost) / cost * 100) if cost and last else 0.0
            due = elapsed >= int(p.get("sell_after_trading_days") or 0)
            stop = pnl_pct <= -self.risk.single_stop_loss_pct
            if not (due or stop):
                continue
            price = self.get_open_price(str(p.get("code")), market) or last
            if not price or price <= 0:
                continue
            shares = float(p.get("shares") or 0)
            fee_sell = trade_cost(market, price, shares, "sell", self.costs)
            amount = price * shares - fee_sell
            cost_total = cost * shares + float(p.get("fee_buy") or 0)
            pnl = amount - cost_total
            pnl_pct = pnl / cost_total * 100 if cost_total else 0
            p.update(
                {
                    "account_id": account_id,
                    "status": "closed",
                    "sell_price": round(price, 4),
                    "sell_date": today,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "fee_sell": round(fee_sell, 2),
                }
            )
            db.upsert_position(p)
            total_sell = db.total_trades("sell", account_id=account_id) + 1
            record = {
                "account_id": account_id,
                "time": datetime.now().isoformat(timespec="seconds"),
                "trade_date": today,
                "action": "sell",
                "strategy": p.get("strategy") or strategy_id,
                "code": p.get("code"),
                "name": p.get("name"),
                "market": market,
                "shares": shares,
                "price": round(price, 4),
                "amount": round(amount, 2),
                "cost_price": p.get("buy_price"),
                "pnl": p["pnl"],
                "pnl_pct": p["pnl_pct"],
                "hold_days": elapsed,
                "position_id": p.get("id"),
                "total_trades": total_sell,
                "note": "止损" if stop and not due else "到期",
            }
            db.insert_trade(record)
            # 卖出提醒：表格（成本/卖出价/持有/收益 + 当日收盘与行情涨幅）
            ohlcv = self.get_ohlcv_on_date(str(p.get("code")), market, today)
            close = float(ohlcv["close"]) if ohlcv and ohlcv.get("close") is not None else price
            mkt_chg = ohlcv.get("market_change_pct") if ohlcv else None
            hold_pnl_close = (close - float(p.get("buy_price") or 0)) * shares
            hold_chg_close = (close / float(p.get("buy_price") or 1) - 1) * 100 if p.get("buy_price") else 0
            self._push_table(
                "sell",
                f"卖出提醒 {today} · {STRATEGY_LABEL.get(strategy_id, strategy_id)}",
                headers=[
                    "代码", "名称", "买入价", "卖出价", "当日收盘",
                    "持仓收益(收盘)", "持仓涨幅(收盘)", "行情收盘涨幅",
                    "卖出收益", "卖出收益率", "持有天数", "累计卖出次数",
                ],
                rows=[[
                    p.get("code"),
                    p.get("name"),
                    f"{float(p.get('buy_price') or 0):.4f}".rstrip("0").rstrip("."),
                    f"{price:.4f}".rstrip("0").rstrip("."),
                    f"{close:.4f}".rstrip("0").rstrip("."),
                    f"{hold_pnl_close:+.2f}",
                    f"{hold_chg_close:+.2f}%",
                    f"{mkt_chg:+.2f}%" if mkt_chg is not None else "-",
                    f"{p['pnl']:+.2f}",
                    f"{p['pnl_pct']:+.2f}%",
                    elapsed,
                    total_sell,
                ]],
                subtitle=(
                    f"账户: {account_id} | 策略: {STRATEGY_LABEL.get(strategy_id, strategy_id)} | "
                    f"结算日: {today} | 原因: {'止损' if stop and not due else '持有期到期'}\n"
                    f"说明: 「持仓收益/涨幅」按当日收盘价相对买入价计算；「行情收盘涨幅」为该标的当日收盘涨跌幅；"
                    f"「卖出收益」按实际卖出成交价计算。"
                ),
                footer="T+1开盘成交 · 模拟盘 · 不构成投资建议",
                account_id=account_id,
                template="red" if p["pnl"] < 0 else "green",
            )
            closed.append(record)
        return closed

    def _buy_for_account(
        self, account_id: str, strategy_id: str, items: list[dict[str, Any]], today: str
    ) -> list[dict[str, Any]]:
        buys: list[dict[str, Any]] = []
        shares_n = float(self.pt_cfg.get("shares_per_trade", 500))
        crypto_notional = float(self.pt_cfg.get("crypto_notional_per_trade", 5000))
        top_n = int(self.pt_cfg.get("top_n_per_strategy", 5))
        cash = db.account_cash(account_id)
        if cash <= 0:
            buys.append(
                {
                    "action": "skip_account_cash",
                    "account_id": account_id,
                    "reason": "该策略账户可用现金不足",
                    "cash": round(cash, 2),
                }
            )
            return buys

        open_keys = {
            str(p.get("code"))
            for p in db.list_positions(account_id=account_id, status="open")
        }
        mtm = self.mark_to_market(account_id)
        risk_state = mtm.get("risk_state") or {}
        max_total = float(risk_state.get("max_total_position_allowed_pct") or 100)
        max_single = self.risk.max_single_position_pct
        equity = float(mtm.get("equity") or cash)
        current_mv = float(mtm.get("market_value") or 0)

        picked = 0
        for item in items:
            if picked >= top_n:
                break
            code = str(item.get("code") or "")
            market = str(item.get("market") or "a_share")
            code, market = normalize_symbol(code, market)
            if market != "crypto":
                market = "a_share"
                if not code or not code[0].isdigit():
                    continue
                code = code.zfill(6)
            else:
                code = code.upper()
            if code in open_keys:
                continue
            price = self.get_open_price(code, market) or self.get_price(code, market)
            if not price or price <= 0:
                continue
            shares = round(crypto_notional / price, 6) if market == "crypto" else shares_n
            if shares <= 0:
                continue
            amount = price * shares
            if equity > 0 and amount / equity * 100 > max_single:
                shares = max(0.0, (equity * max_single / 100) / price)
                if market != "crypto":
                    shares = float(int(shares // 100) * 100)
                if shares <= 0:
                    continue
                amount = price * shares
            if equity > 0 and (current_mv + amount) / equity * 100 > max_total:
                buys.append(
                    {
                        "action": "skip_position_full",
                        "account_id": account_id,
                        "code": code,
                        "reason": f"账户总仓位已达风控上限 {max_total}%",
                        "exposure_pct": round(current_mv / equity * 100, 2),
                    }
                )
                continue
            fee = trade_cost(market, price, shares, "buy", self.costs)
            need = amount + fee
            if need > cash:
                buys.append(
                    {
                        "action": "skip_cash",
                        "account_id": account_id,
                        "code": code,
                        "need": round(need, 2),
                        "cash": round(cash, 2),
                        "reason": "该策略账户可用现金不足",
                    }
                )
                continue

            seq = db.next_trade_seq(strategy_id, "buy", account_id=account_id)
            total_buy = db.total_trades("buy", account_id=account_id) + 1
            pos_id = f"{account_id}-{strategy_id}-{code}-{today}-{seq}"
            pos = {
                "id": pos_id,
                "account_id": account_id,
                "strategy": strategy_id,
                "code": code,
                "name": str(item.get("name") or code),
                "market": market,
                "shares": shares,
                "buy_price": round(price, 4),
                "buy_date": today,
                "sell_after_trading_days": self._holding_days(strategy_id),
                "status": "open",
                "fee_buy": round(fee, 2),
                "fee_sell": 0.0,
                "signal_score": item.get("score"),
                "note": str(item.get("reason") or "")[:160],
                "buy_seq": total_buy,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            db.upsert_position(pos)
            cash -= need
            current_mv += amount
            open_keys.add(code)
            picked += 1
            record = {
                "account_id": account_id,
                "time": datetime.now().isoformat(timespec="seconds"),
                "trade_date": today,
                "action": "buy",
                "strategy": strategy_id,
                "code": code,
                "name": pos["name"],
                "market": market,
                "shares": shares,
                "price": pos["buy_price"],
                "amount": round(amount, 2),
                "cost_price": pos["buy_price"],
                "position_id": pos_id,
                "seq_in_strategy": seq,
                "total_trades": total_buy,
                "extra": {"fee": fee, "score": item.get("score")},
            }
            db.insert_trade(record)
            # 买入明细写入 record，表格推送在 _buy_for_account 返回后由 run_daily 汇总
            ohlcv = self.get_ohlcv_on_date(code, market, today)
            close = float(ohlcv["close"]) if ohlcv and ohlcv.get("close") is not None else price
            mkt_chg = ohlcv.get("market_change_pct") if ohlcv else None
            hold_pnl = (close - price) * shares
            hold_chg = (close / price - 1) * 100 if price else 0
            record["close_date"] = today
            record["close_price"] = close
            record["market_change_pct"] = mkt_chg
            record["hold_pnl_close"] = round(hold_pnl, 2)
            record["hold_change_close_pct"] = round(hold_chg, 2)
            db.insert_trade(
                {
                    **record,
                    "extra": {
                        **(record.get("extra") or {}),
                        "close_date": today,
                        "close_price": close,
                        "market_change_pct": mkt_chg,
                        "hold_pnl_close": round(hold_pnl, 2),
                        "hold_change_close_pct": round(hold_chg, 2),
                    },
                }
            )
            buys.append(record)
        return buys

    def _push_buy_table(
        self, account_id: str, strategy_id: str, today: str, buys: list[dict[str, Any]], cash_left: float
    ) -> dict[str, Any]:
        if not buys:
            return {"ok": True, "skipped": True}
        rows = []
        for b in buys:
            close = b.get("close_price")
            rows.append([
                b.get("code"),
                b.get("name"),
                f"{float(b.get('price') or 0):.4f}".rstrip("0").rstrip("."),
                f"{float(b.get('shares') or 0):g}",
                b.get("trade_date") or today,
                f"{float(close):.4f}".rstrip("0").rstrip(".") if close is not None else "-",
                f"{float(b.get('hold_pnl_close') or 0):+.2f}",
                f"{float(b.get('hold_change_close_pct') or 0):+.2f}%",
                f"{float(b.get('market_change_pct')):+.2f}%" if b.get("market_change_pct") is not None else "-",
            ])
        return self._push_table(
            "buy",
            f"买入成交提醒 {today} · {STRATEGY_LABEL.get(strategy_id, strategy_id)}",
            headers=[
                "代码", "名称", "买入成交价", "数量", "成交日",
                "当日收盘价", "按收盘收益", "按收盘涨幅", "行情收盘涨幅",
            ],
            rows=rows,
            subtitle=(
                f"账户: {account_id} | 策略: {STRATEGY_LABEL.get(strategy_id, strategy_id)}（隔离账户）\n"
                f"成交日: {today} | 买入成交价=T+1开盘 | 当日收盘价已标注具体日期\n"
                f"「按收盘收益/涨幅」= 相对买入成交价；「行情收盘涨幅」= 标的当日收盘涨跌幅\n"
                f"剩余现金: {cash_left:.2f}"
            ),
            footer="模拟盘 · 含费用 · 不构成投资建议",
            account_id=account_id,
            template="blue",
        )

    def run_daily(self, force_refresh_signals: bool = False, account_id: str | None = None, strategy_id: str | None = None) -> dict[str, Any]:
        self.quote_cache.clear()
        today = datetime.now().strftime("%Y-%m-%d")

        if strategy_id is None and account_id and account_id.startswith("acc_"):
            strategy_id = account_id[4:]
        if strategy_id is None:
            strategy_id = self.active_strategies[0] if self.active_strategies else "short_term_hot"
        if account_id is None:
            account_id = self.account_id_for(strategy_id)

        db.ensure_account(
            account_id=account_id,
            strategy_id=strategy_id,
            strategy_name=STRATEGY_REGISTRY.get(strategy_id, {}).get("name", strategy_id),
            strategy_source=STRATEGY_REGISTRY.get(strategy_id, {}).get("source_label", ""),
            initial_cash=self.default_initial_cash,
        )

        if force_refresh_signals:
            try:
                self.signals.generate(market=str(self.pt_cfg.get("market") or "all"), top_n=20, push_feishu=False)
            except Exception as e:
                logger.warning("refresh signals failed: %s", e)

        items = self._signals_for_strategy(strategy_id, market=str(self.pt_cfg.get("market") or "all"), allow_live=True)
        if not items:
            try:
                rep = self.signals.generate(market=str(self.pt_cfg.get("market") or "all"), top_n=20, push_feishu=False)
                items = (rep.get("signals") or {}).get(strategy_id) or []
            except Exception as e:
                logger.warning("generate signals failed: %s", e)
        # 纸面交易不强制 min_score：取该策略 TopN，否则长线/聚宽策略长期建不了仓
        if items:
            items = items[: int(self.pt_cfg.get("top_n_per_strategy", 5))]

        sells = self._sell_due(account_id, strategy_id, today)
        buys = self._buy_for_account(account_id, strategy_id, items, today)
        mtm = self.mark_to_market(account_id)

        preview = None
        try:
            targets = []
            for it in items[: int(self.pt_cfg.get("top_n_per_strategy", 5))]:
                code = str(it.get("code") or "")
                market = str(it.get("market") or "a_share")
                if market != "crypto":
                    code = code.zfill(6)
                targets.append({"code": code, "name": it.get("name"), "market": market, "strategy": strategy_id, "weight_pct": 100})
            prices = {p["code"]: p.get("last_price") or p.get("buy_price") for p in mtm["open_positions"]}
            for it in items:
                c = str(it.get("code") or "")
                if str(it.get("market")) != "crypto":
                    c = c.zfill(6)
                prices[c] = float(it.get("price") or prices.get(c) or 0)
            preview = calculate_rebalance(
                target_codes=targets,
                positions=mtm["open_positions"],
                prices={k: float(v or 0) for k, v in prices.items()},
                equity=mtm["equity"],
                cash=mtm["cash"],
                risk=self.risk,
            )
        except Exception as e:
            preview = {"error": str(e)}

        real_buys = [b for b in buys if b.get("action") == "buy"]
        skips = [b for b in buys if b.get("action") != "buy"]

        # 买入成交表
        buy_push = self._push_buy_table(
            account_id, strategy_id, today, real_buys, cash_left=float(mtm.get("cash") or 0)
        )

        # 每日简报：持仓按当日收盘价盯市表
        holdings = mtm.get("open_positions") or []
        hold_rows, hold_details = self._pos_mark_rows(
            holdings, today, self.get_ohlcv_on_date
        )
        brief_subtitle = (
            f"账户: {account_id} | 策略: {mtm.get('strategy_name') or strategy_id}\n"
            f"简报日期: {today}\n"
            f"今日买入 {len(real_buys)} 笔 / 卖出 {len(sells)} 笔 / 跳过 {len(skips)} 笔\n"
            f"总资产 {mtm['equity']} | 可用现金 {mtm['cash']} | 仓位 {mtm['risk_state']['total_exposure_pct']}%\n"
            f"来源: {mtm.get('strategy_source') or strategy_id}\n"
            f"下表持仓收益/涨幅均按「{today}」收盘价相对买入价计算；行情收盘涨幅为该日标的涨跌幅。"
        )
        if hold_rows:
            brief_push = self._push_table(
                "paper_run",
                f"每日模拟盘简报 {today} · {STRATEGY_LABEL.get(strategy_id, strategy_id)}",
                headers=[
                    "代码", "名称", "买入成交价", "买入日",
                    f"收盘价({today})", "按收盘收益", "按收盘涨幅", "行情收盘涨幅",
                ],
                rows=hold_rows,
                subtitle=brief_subtitle,
                footer=(
                    f"买入明细见「买入成交提醒」| 执行规则: T日收盘选股，T+1开盘成交 | "
                    f"策略来源: {mtm.get('strategy_source') or strategy_id} | 不构成投资建议"
                ),
                account_id=account_id,
                template="green",
            )
        else:
            brief_push = self._push(
                "paper_run",
                f"每日模拟盘简报 {today} · {STRATEGY_LABEL.get(strategy_id, strategy_id)}",
                brief_subtitle + "\n（当前无持仓）",
                account_id,
            )

        # 同步写入 daily_runs 便于回溯
        mtm["mark_table"] = hold_details
        mtm["buy_table"] = [
            {
                "code": b.get("code"),
                "name": b.get("name"),
                "buy_price": b.get("price"),
                "buy_date": b.get("trade_date"),
                "close_date": b.get("close_date"),
                "close_price": b.get("close_price"),
                "hold_pnl_close": b.get("hold_pnl_close"),
                "hold_change_close_pct": b.get("hold_change_close_pct"),
                "market_change_pct": b.get("market_change_pct"),
            }
            for b in real_buys
        ]
        db.insert_daily_run("paper", f"{strategy_id}|{account_id}", buys, sells, mtm, account_id)

        return {
            "date": today,
            "run_at": datetime.now().isoformat(timespec="seconds"),
            "account_id": account_id,
            "strategy_id": strategy_id,
            "strategy_name": mtm.get("strategy_name"),
            "strategy_source": mtm.get("strategy_source"),
            "execution_rule": "T日收盘选股，T+1开盘成交；成本含佣金/印花税/滑点",
            "signal_count": len(items),
            "rebalance_preview": preview,
            "buys": real_buys,
            "skips": skips,
            "sells": sells,
            "portfolio": mtm,
            "mark_table": mtm.get("mark_table"),
            "buy_table": mtm.get("buy_table"),
            "push_buy": buy_push,
            "push_brief": brief_push,
        }

    def snapshot(
        self,
        account_id: str | None = None,
        strategy_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        strategy: str | None = None,
        action: str | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        if account_id is None and strategy_id:
            account_id = self.account_id_for(strategy_id)
        if account_id is None:
            # 默认取第一个已创建账户，否则 default
            accounts = db.list_accounts()
            account_id = accounts[0]["account_id"] if accounts else DEFAULT_ACCOUNT
            if not accounts:
                sid = self.active_strategies[0] if self.active_strategies else "short_term_hot"
                account_id = self.account_id_for(sid)
                db.ensure_account(
                    account_id=account_id,
                    strategy_id=sid,
                    strategy_name=STRATEGY_REGISTRY.get(sid, {}).get("name", sid),
                    strategy_source=STRATEGY_REGISTRY.get(sid, {}).get("source_label", ""),
                    initial_cash=self.default_initial_cash,
                )

        mtm = self.mark_to_market(account_id)
        accounts = db.summarize_accounts()
        return {
            "config": {
                "initial_cash": self.pt_cfg.get("initial_cash"),
                "shares_per_trade": self.pt_cfg.get("shares_per_trade"),
                "crypto_notional_per_trade": self.pt_cfg.get("crypto_notional_per_trade"),
                "top_n_per_strategy": self.pt_cfg.get("top_n_per_strategy"),
                "active_strategies": self.active_strategies,
                "available_strategies": list_strategies(),
                "holding_days": self.pt_cfg.get("holding_days"),
                "market": self.pt_cfg.get("market"),
                "costs": self.costs,
                "execution_rule": "T日收盘选股，T+1开盘成交",
                "account_isolation": True,
                "crypto_exchange": self.cfg.get("market", {}).get("crypto_exchange"),
            },
            "risk": self.risk.to_dict(),
            "accounts": accounts,
            "account_id": account_id,
            "portfolio": mtm,
            "db_stats": mtm.get("db_stats"),
            "positions": db.list_positions(
                account_id=account_id, strategy=strategy, date_from=date_from, date_to=date_to, code=code
            ),
            "trades": db.query_trades(
                account_id=account_id, date_from=date_from, date_to=date_to, action=action, strategy=strategy, code=code, limit=300
            ),
            "push_logs": db.query_push_logs(account_id=account_id, date_from=date_from, date_to=date_to, limit=50),
            "daily_runs": db.query_daily_runs(account_id=account_id, date_from=date_from, date_to=date_to, limit=20),
            "signal_dates": db.signal_dates(limit=30),
            "strategies": list_strategies(),
            "disclaimer": "模拟含手续费与滑点，T+1成交；策略账户相互隔离。不构成投资建议。",
        }

    def morning_watchlist(self, market: str | None = None, account_id: str | None = None) -> dict[str, Any]:
        strategy_id = (account_id or "").replace("acc_", "") or (
            self.active_strategies[0] if self.active_strategies else "short_term_hot"
        )
        account_id = account_id or self.account_id_for(strategy_id)
        mtm = self.mark_to_market(account_id)
        items = self._signals_for_strategy(strategy_id)
        risk = mtm.get("risk_state") or {}
        today = datetime.now().strftime("%Y-%m-%d")
        # 早盘用最近一个有收盘价的交易日
        as_of = today
        holdings = mtm.get("open_positions") or []
        hold_rows, hold_details = self._pos_mark_rows(holdings, as_of, self.get_ohlcv_on_date)
        # 若当日尚无收盘，尝试回退到买入日/前一日
        if hold_rows and all(r[4] == "-" or float(str(r[4]).replace("-", "0") or 0) == 0 for r in hold_rows):
            pass
        watch_rows = []
        for it in items[:8]:
            mkt = "加密" if it.get("market") == "crypto" else "A股"
            code = it.get("code")
            mkt_i = str(it.get("market") or "a_share")
            ohlcv = self.get_ohlcv_on_date(str(code), mkt_i, as_of)
            close = ohlcv.get("close") if ohlcv else it.get("price")
            chg = ohlcv.get("market_change_pct") if ohlcv else None
            watch_rows.append([
                code,
                it.get("name"),
                mkt,
                it.get("score"),
                it.get("price"),
                f"{float(close):.4f}".rstrip("0").rstrip(".") if close is not None else "-",
                f"{chg:+.2f}%" if chg is not None else "-",
            ])

        subtitle = (
            f"账户: {account_id} | 策略: {mtm.get('strategy_name') or strategy_id}\n"
            f"来源: {mtm.get('strategy_source') or '-'}\n"
            f"提醒时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 行情日期: {as_of}\n"
            f"总资产 {mtm['equity']} · 现金 {mtm['cash']} · 仓位 {risk.get('total_exposure_pct')}%\n"
            f"持仓表收益/涨幅按「{as_of}」收盘价相对买入价计算；行情收盘涨幅为该日标的涨跌幅。"
        )

        sections_md = [subtitle, "", "▎今日需关注"]
        if watch_rows:
            from app.signals.feishu import FeishuPusher
            sections_md.append(
                FeishuPusher.format_md_table(
                    ["代码", "名称", "市场", "得分", "信号价", f"收盘价({as_of})", "行情收盘涨幅"],
                    watch_rows,
                )
            )
        else:
            sections_md.append("（暂无该策略达标信号，可先生成信号）")
        sections_md.append("")
        sections_md.append("▎持仓盯市表")
        if hold_rows:
            from app.signals.feishu import FeishuPusher
            sections_md.append(
                FeishuPusher.format_md_table(
                    ["代码", "名称", "买入成交价", "买入日", f"收盘价({as_of})", "按收盘收益", "按收盘涨幅", "行情收盘涨幅"],
                    hold_rows,
                )
            )
            sections_md.append("")
            sections_md.append(
                "下表口径：按收盘收益/涨幅 = 收盘价相对买入成交价；行情收盘涨幅 = 标的当日涨跌幅。"
            )
        else:
            sections_md.append("（无持仓）")
        sections_md.append("")
        sections_md.append("今日计划：14:45 按前一日信号自动模拟买入。模拟验证，不构成投资建议。")
        md = "\n".join(sections_md)

        from app.signals.feishu import FeishuPusher
        pusher = FeishuPusher()
        if pusher.is_ready():
            push_res = pusher.push_card(f"早盘关注 {today} · {strategy_id}", md, template="blue")
        else:
            push_res = {"ok": False, "error": "webhook未配置"}

        mtm["mark_table"] = hold_details
        mtm["watch_table"] = [
            {
                "code": r[0], "name": r[1], "market": r[2], "score": r[3],
                "signal_price": r[4], "close_price": r[5], "market_change_pct": r[6],
                "as_of_date": as_of,
            }
            for r in watch_rows
        ]
        db.insert_push_log("morning", f"早盘关注 {today}", md, bool(push_res.get("ok")), push_res, account_id)
        db.insert_daily_run("morning", strategy_id, [], [], mtm, account_id)
        return {
            "time": datetime.now().isoformat(timespec="seconds"),
            "account_id": account_id,
            "as_of_date": as_of,
            "watchlist_text": md,
            "watch_table": mtm.get("watch_table"),
            "mark_table": mtm.get("mark_table"),
            "portfolio": mtm,
            "push": push_res,
        }

    def _watch_strategies(self) -> list[str]:
        sig = self.cfg.get("signals") or {}
        ids = sig.get("watch_strategies") or list(STRATEGY_REGISTRY.keys())
        return [str(x) for x in ids if x in STRATEGY_REGISTRY]

    @staticmethod
    def _held_cell(code: str, name: str, held: bool) -> str:
        """已持仓橙色并标注，未持仓黑色。"""
        label = f"{code} {name or ''}".strip()
        if held:
            return f"<font color='orange'>**{label}**</font> [持仓]"
        return f"<font color='black'>{label}</font>"

    @staticmethod
    def _chg_cell(pct) -> str:
        """买入后涨幅：>0 红，<0 绿，=0 黑（A股习惯红涨绿跌）。"""
        if pct is None:
            return "<font color='black'>-</font>"
        v = float(pct)
        if v > 0:
            return f"<font color='red'>{v:+.2f}%</font>"
        if v < 0:
            return f"<font color='green'>{v:+.2f}%</font>"
        return f"<font color='black'>{v:+.2f}%</font>"

    @staticmethod
    def _sort_watch_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """排序：A股在前、加密在后；同市场内未持仓在前、已持仓在后。"""
        def key(r):
            mkt = 0 if str(r.get("market")) in ("A股", "a_share") else 1
            held = 1 if r.get("held") else 0
            # 分数降序作次级排序
            score = -float(r.get("score") or 0)
            return (mkt, held, score, str(r.get("code") or ""))

        return sorted(rows, key=key)

    def watch_all_strategies(
        self, slot: str = "morning", market: str | None = None, push: bool = True
    ) -> dict[str, Any]:
        """早/中/晚时段：全策略关注表 + 持仓橙/未持仓黑，评分置后。"""
        from app.signals.feishu import FeishuPusher
        from app.signals.feishu import FeishuPusher as FP

        sig_cfg = self.cfg.get("signals") or {}
        slots = sig_cfg.get("push_slots") or {"morning": "09:20", "midday": "12:30", "evening": "17:30"}
        slot_label = {"morning": "早盘", "midday": "午盘", "evening": "收盘后"}.get(slot, slot)
        slot_time = slots.get(slot, "")
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        as_of = today
        strategy_ids = self._watch_strategies()
        market = market or str(sig_cfg.get("market") or "all")

        # 行情只加载一次，供全部策略复用
        frames, names = None, None
        try:
            frames, names = self.engine.load_frames(market)
        except Exception:
            frames, names = {}, {}

        strategy_sections: list[dict[str, Any]] = []
        md_parts = [
            f"**TradeLab 每日关注 · {slot_label}**",
            f"时段: {today} {slot_time or now.strftime('%H:%M')} | 观察日: {as_of}",
            f"覆盖策略: {len(strategy_ids)} 个（各策略独立账户）",
            "",
            "颜色说明：<font color='orange'>**橙色 = 已持仓**</font> · <font color='black'>黑色 = 未持仓</font>",
            "字段：代码/名称 · 信号价 · 收盘价 · 行情涨幅 · **信号价后涨幅** · 持有天数 · 持股数量 · 持股市值 · 市场 · 评分",
            "口径：**信号价** = 策略生成信号当日使用的最新收盘价（入库后按信号日固定）；**信号价后涨幅** = 观察日收盘价 / 信号价 − 1；**行情收盘涨幅** = 标的当日涨跌幅。",
            "",
        ]

        for sid in strategy_ids:
            meta = STRATEGY_REGISTRY.get(sid) or {}
            account_id = self.account_id_for(sid)
            db.ensure_account(
                account_id=account_id,
                strategy_id=sid,
                strategy_name=meta.get("name", sid),
                strategy_source=meta.get("source_label", ""),
                initial_cash=self.default_initial_cash,
            )
            mtm = self.mark_to_market(account_id)
            held_codes = set()
            for p in (mtm.get("open_positions") or []):
                c, _m = normalize_symbol(p.get("code"), p.get("market"))
                held_codes.add(c)
            items = self._signals_for_strategy(sid)
            if not items and frames:
                try:
                    items = run_strategy_by_id(sid, frames, names or {}, top_n=int(sig_cfg.get("top_n") or 20))
                except Exception:
                    items = []

            # 连续上榜：用「首次信号价」作为涨幅基准
            baseline_map = db.first_signal_map(sid, lookback_days=30)

            watch_rows = []
            for it in items[:15]:
                raw_code = it.get("code")
                code_key, mkt_raw = normalize_symbol(raw_code, it.get("market"))
                mkt = "加密" if mkt_raw == "crypto" else "A股"
                held = code_key in held_codes
                ohlcv = self.get_ohlcv_on_date(code_key, mkt_raw, as_of)
                close = ohlcv.get("close") if ohlcv and ohlcv.get("close") is not None else it.get("price")
                chg = ohlcv.get("market_change_pct") if ohlcv else None
                hold_info = next(
                    (
                        p
                        for p in (mtm.get("open_positions") or [])
                        if normalize_symbol(p.get("code"), p.get("market"))[0] == code_key
                    ),
                    None,
                )
                latest_price = it.get("price")
                base = baseline_map.get(code_key) or {}
                # 若库里无历史，则以今日信号价为首次价
                first_price = base.get("first_price")
                if first_price in (None, ""):
                    first_price = latest_price
                first_date = base.get("first_date") or as_of
                latest_date = base.get("latest_date") or as_of
                appear_days = int(base.get("appear_days") or 1)
                sig_chg = None
                try:
                    if first_price not in (None, "") and close not in (None, ""):
                        fp = float(first_price)
                        cp = float(close)
                        if fp > 0:
                            sig_chg = round((cp / fp - 1) * 100, 2)
                except Exception:
                    sig_chg = None
                watch_rows.append(
                    {
                        "code": code_key,
                        "name": it.get("name"),
                        "market": mkt,
                        "signal_price": latest_price,
                        "first_signal_price": first_price,
                        "first_signal_date": first_date,
                        "latest_signal_date": latest_date,
                        "signal_appear_days": appear_days,
                        "close_price": close,
                        "close_date": as_of,
                        "market_change_pct": chg,
                        "score": it.get("score"),
                        "held": held,
                        "signal_change_pct": sig_chg,
                        "signal_baseline": "first_signal" if appear_days > 1 else "latest_signal",
                        "buy_price": hold_info.get("buy_price") if hold_info else None,
                        "hold_change_pct": hold_info.get("hold_change_pct") if hold_info else None,
                        "hold_days": hold_info.get("hold_days") if hold_info else None,
                        "shares": hold_info.get("shares") if hold_info else None,
                        "market_value": hold_info.get("market_value") if hold_info else None,
                        "account_id": account_id,
                        "strategy_id": sid,
                        "strategy_name": meta.get("name", sid),
                    }
                )
            watch_rows = self._sort_watch_rows(watch_rows)

            md_rows = []
            for r in watch_rows:
                cell = self._held_cell(r["code"], r["name"] or "", r["held"])
                chg_sig = self._chg_cell(r.get("signal_change_pct"))
                hold_days = r.get("hold_days") if r.get("held") else None
                sh = r.get("shares")
                mv = r.get("market_value")
                first_p = r.get("first_signal_price")
                latest_p = r.get("signal_price")
                base_txt = (
                    f"{first_p}({r.get('first_signal_date')})"
                    if r.get("signal_appear_days", 1) > 1
                    else (f"{latest_p}" if latest_p not in (None, "") else "-")
                )
                md_rows.append(
                    [
                        cell,
                        f"{float(latest_p):.4f}".rstrip("0").rstrip(".") if latest_p not in (None, "") else "-",
                        base_txt,
                        f"{float(r['close_price']):.4f}".rstrip("0").rstrip(".") if r.get("close_price") not in (None, "") else "-",
                        f"{float(r['market_change_pct']):+.2f}%" if r.get("market_change_pct") is not None else "-",
                        chg_sig,
                        hold_days if hold_days is not None else "-",
                        f"{float(sh):g}" if sh not in (None, "") else "-",
                        f"{float(mv):.2f}" if mv not in (None, "") else "-",
                        r.get("market") or "-",
                        r.get("score") if r.get("score") is not None else "-",
                    ]
                )
            table_md = (
                FP.format_md_table(
                    [
                        "代码/名称",
                        "最新信号价",
                        "涨幅基准(首次信号价)",
                        f"收盘价({as_of})",
                        "行情收盘涨幅",
                        "信号价后涨幅",
                        "持有天数",
                        "持股数量",
                        "持股市值",
                        "市场",
                        "评分",
                    ],
                    md_rows,
                )
                if md_rows
                else "_（本策略暂无达标关注标的）_"
            )
            hold_rows_md = []
            for p in (mtm.get("open_positions") or []):
                c, mm = normalize_symbol(p.get("code"), p.get("market"))
                sh = float(p.get("shares") or 0)
                mv = float(p.get("market_value") or 0)
                hold_rows_md.append(
                    [
                        f"<font color='orange'>{c} {p.get('name') or ''}</font>",
                        f"{float(p.get('buy_price') or 0):.4f}".rstrip("0").rstrip("."),
                        str(p.get("buy_date") or "")[:10],
                        f"{float(p.get('last_price') or 0):.4f}".rstrip("0").rstrip("."),
                        self._chg_cell(p.get("hold_change_pct") if p.get("hold_change_pct") is not None else p.get("unrealized_pnl_pct")),
                        p.get("hold_days") if p.get("hold_days") is not None else p.get("hold_days_elapsed"),
                        f"{sh:g}",
                        f"{mv:.2f}",
                        "加密" if mm == "crypto" else "A股",
                        f"{float(p.get('unrealized_pnl') or 0):+.2f}",
                    ]
                )
            hold_table = (
                FP.format_md_table(
                    [
                        "代码/名称",
                        "买入成交价",
                        "买入日",
                        "现价/收盘",
                        "买入后涨幅",
                        "持有天数",
                        "持股数量",
                        "持股市值",
                        "市场",
                        "浮动盈亏",
                    ],
                    hold_rows_md,
                )
                if hold_rows_md
                else "_（无持仓）_"
            )
            held_n = sum(1 for r in watch_rows if r.get("held"))
            md_parts.append(f"### {meta.get('name', sid)}")
            md_parts.append(
                f"账户 `{account_id}` | 持仓 {len(held_codes)} 只 | 关注中已持仓 **{held_n}** | "
                f"资产 {mtm.get('equity')} / 现金 {mtm.get('cash')}"
            )
            md_parts.append("**关注列表**（A股在前 · 未持仓在前 · 评分最后）")
            md_parts.append(table_md)
            md_parts.append("**持仓明细**（涨幅：>0红 / <0绿 / =0黑）")
            md_parts.append(hold_table)
            md_parts.append("")
            strategy_sections.append(
                {
                    "strategy_id": sid,
                    "strategy_name": meta.get("name", sid),
                    "account_id": account_id,
                    "held_count": held_n,
                    "watch": watch_rows,
                    "holdings": mtm.get("open_positions") or [],
                    "portfolio": {
                        "equity": mtm.get("equity"),
                        "cash": mtm.get("cash"),
                        "exposure_pct": (mtm.get("risk_state") or {}).get("total_exposure_pct"),
                    },
                }
            )

        md_parts.append("---")
        md_parts.append(
            f"{slot_label}推送共覆盖 **{len(strategy_ids)}** 个策略账户；14:45 按各账户策略分别模拟买入。不构成投资建议。"
        )
        md_parts.append(
            "涨幅口径：**信号价后涨幅** = 观察日收盘价 / **首次信号价** − 1；"
            "若标的连续多日上榜，以库里**最早一次信号日的信号价**为基准，而不是每天刷新的最新信号价。"
        )
        md = "\n".join(md_parts)

        pusher = FeishuPusher()
        title = f"{slot_label}关注 {today} · 全策略"
        if push and pusher.is_ready():
            push_res = pusher.push_card(title, md, template="orange" if slot != "morning" else "blue")
            if not push_res.get("ok"):
                plain = (
                    md.replace("<font color='orange'>**", "[持仓] ")
                    .replace("**</font>", "")
                    .replace("<font color='black'>", "")
                    .replace("</font>", "")
                )
                push_res = pusher.push_text(f"【{title}】\n{plain}")
                push_res["fallback_text"] = True
        else:
            push_res = {"ok": False, "error": "未推送或 webhook 未配置"}

        result = {
            "slot": slot,
            "slot_label": slot_label,
            "slot_time": slot_time,
            "as_of_date": as_of,
            "date": today,
            "strategies": strategy_ids,
            "sections": strategy_sections,
            "markdown": md,
            "push": push_res,
            "legend": {
                "held": "orange",
                "not_held": "black",
                "score_field": "last",
                "sort": "A股优先，市场内未持仓优先",
                "signal_change_baseline": "first_signal_price（连续上榜用首次信号价）",
                "hold_change_color": {"positive": "red", "negative": "green", "zero": "black"},
            },
        }
        try:
            db.insert_watch_snapshot(slot, result)
            result["snapshot_saved"] = True
            result["available_dates"] = db.watch_snapshot_dates(slot, limit=30)
        except Exception as e:
            result["snapshot_saved"] = False
            result["snapshot_error"] = str(e)
        return result

    def run_all_strategy_accounts(self, force_refresh_signals: bool = False) -> list[dict[str, Any]]:
        """对每个策略独立账户分别执行模拟盘（补齐非短线策略的成交提醒）。"""
        results = []
        market = str((self.cfg.get("signals") or {}).get("market") or "all")
        top_n = int((self.cfg.get("signals") or {}).get("top_n") or 20)
        frames, names = {}, {}
        try:
            frames, names = self.engine.load_frames(market)
        except Exception:
            pass
        today = datetime.now().strftime("%Y-%m-%d")
        for sid in self._watch_strategies():
            try:
                if frames:
                    try:
                        items = run_strategy_by_id(sid, frames, names or {}, top_n=top_n)
                        if items:
                            db.insert_signals(
                                {
                                    "report_date": today,
                                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                                    "market": market,
                                    "signals": {sid: items},
                                }
                            )
                    except Exception as e:
                        logger.warning("signal %s failed: %s", sid, e)
                results.append(self.run_daily(force_refresh_signals=False, strategy_id=sid))
            except Exception as e:
                results.append({"strategy_id": sid, "error": str(e)})
        return results
