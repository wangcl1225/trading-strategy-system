from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from app import DISCLAIMER
from app.benchmark import PASS_RULES, BenchmarkProvider
from app.config import ROOT, load_config
from app.backtest.engine_v2 import DEFAULT_COSTS, BacktestEngineV2
from app.data.market_extra import DragonTigerProvider, FundFlowProvider, MarketExtraFactors
from app.paper_trade.engine import PaperTradeEngine
from app.risk.engine import RULES_TEXT, RiskConfig, calculate_rebalance
from app.signals.service import SignalService
from app.strategies.engine import STRATEGY_MAP, STRATEGY_META, StrategyEngine
from app.strategies.registry import list_strategies, run_strategy_by_id, STRATEGY_REGISTRY
import app.db as db

router = APIRouter(prefix="/api")

_engine = StrategyEngine()
_signals = SignalService(_engine)
_lhb_provider = DragonTigerProvider()
_flow_provider = FundFlowProvider()
_extras = MarketExtraFactors()
_paper = PaperTradeEngine()
_bt2 = BacktestEngineV2()
_bench = BenchmarkProvider()


@router.get("/health")
def health() -> dict[str, Any]:
    from app.notify import notify_status

    return {
        "status": "ok",
        "version": "0.5.0",
        "disclaimer": DISCLAIMER,
        "crypto_exchange": load_config().get("market", {}).get("crypto_exchange"),
        "db_path": str(db.db_path()),
        "strategies": list(STRATEGY_REGISTRY.keys()),
        "risk_locked": True,
        "execution_rule": "T日收盘选股，T+1开盘成交",
        "notify": notify_status(),
    }


@router.get("/notify/channels")
def notify_channels() -> dict[str, Any]:
    from app.notify import notify_status

    st = notify_status()
    cfg = load_config().get("notify") or {}
    return {
        **st,
        "hints": {
            "wecom": "企业微信群机器人（个人微信无官方群机器人，请用企业微信）",
            "dingtalk": "钉钉群机器人 Webhook",
            "telegram": "BotFather 建 bot + chat_id；国内或需 api_base 反代",
            "email": "SMTP 授权码（QQ/163 等）",
            "webhook": "任意 HTTP 接口，body 支持 {title}/{text}",
            "wechat_personal": "个人微信稳定方案：企业微信转个人微信，或公众号/第三方推送",
        },
        "configured": {
            "wecom": bool((cfg.get("wecom") or {}).get("webhook")),
            "dingtalk": bool((cfg.get("dingtalk") or {}).get("webhook")),
            "telegram": bool((cfg.get("telegram") or {}).get("bot_token")),
            "email": bool((cfg.get("email") or {}).get("smtp_host")),
            "webhook": bool((cfg.get("webhook") or {}).get("url")),
        },
    }


@router.post("/notify/test")
def notify_test(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from app.notify import MultiNotifier

    channel = str(payload.get("channel") or "feishu")
    title = str(payload.get("title") or "TradeLab 推送测试")
    text = str(payload.get("text") or "测试消息：该渠道已接通。信号仅供研究，不构成投资建议。")
    return MultiNotifier().push_text(title, text, channels=[channel]) | {"channel": channel}


@router.get("/config")
def get_config() -> dict[str, Any]:
    cfg = load_config()
    return {
        "market": cfg.get("market"),
        "benchmark": cfg.get("benchmark"),
        "costs": cfg.get("costs") or DEFAULT_COSTS,
        "risk": RiskConfig.load().to_dict(),
        "signals": cfg.get("signals"),
        "paper_trade": {k: v for k, v in (cfg.get("paper_trade") or {}).items() if k != "database"},
        "pass_rule": PASS_RULES,
        "disclaimer": DISCLAIMER,
    }


@router.get("/strategies")
def strategies() -> dict[str, Any]:
    return {
        "items": list_strategies(),
        "active_paper_strategies": _paper.active_strategies,
        "accounts": db.summarize_accounts(),
        "note": "每个策略对应独立模拟账户（acc_<strategy_id>），资金/持仓/成交隔离。JoinQuant为广场热门策略本地实现。",
    }


@router.post("/strategies/active")
def set_active_strategies(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """应用策略到独立模拟账户，返回明确成功/失败原因。"""
    try:
        # 兼容两种入参：{strategy_ids:[...]} 或 {strategy_id:"..."}
        strategy_ids = payload.get("strategy_ids")
        if not strategy_ids and payload.get("strategy_id"):
            strategy_ids = [payload.get("strategy_id")]
        if not strategy_ids:
            return {
                "ok": False,
                "code": "EMPTY",
                "reason": "未指定策略 strategy_id / strategy_ids",
            }
        # 单策略：返回详细账户信息
        if len(strategy_ids) == 1:
            return _paper.apply_strategy(str(strategy_ids[0]), create_if_missing=True)
        result = _paper.switch_strategies([str(x) for x in strategy_ids])
        return result
    except Exception as e:
        return {"ok": False, "code": "ERROR", "reason": f"应用失败：{e}"}


@router.get("/paper/accounts")
def paper_accounts() -> dict[str, Any]:
    """账户总览：含浮动盈亏（持仓成本 vs 最新价汇总）。"""
    base = db.summarize_accounts()
    enriched = []
    for a in base:
        aid = a.get("account_id")
        unreal = 0.0
        mv = 0.0
        opens_n = 0
        try:
            mtm = _paper.mark_to_market(aid)
            for p in mtm.get("open_positions") or []:
                opens_n += 1
                unreal += float(p.get("unrealized_pnl") or 0)
                mv += float(p.get("market_value") or 0)
            a["equity"] = mtm.get("equity")
            a["strategy_name"] = mtm.get("strategy_name") or a.get("strategy_name")
            a["strategy_source"] = mtm.get("strategy_source") or a.get("strategy_source")
        except Exception:
            pass
        a["unrealized_pnl"] = round(unreal, 2)
        a["market_value"] = round(mv, 2)
        a["open_count"] = a.get("open_count") or opens_n
        enriched.append(a)
    return {
        "accounts": enriched,
        "available_strategies": list_strategies(),
        "isolation": "每策略独立账户，便于分别验证",
        "columns_note": "浮动盈亏=Σ(最新价-成本价)×持股数量；已实现盈亏=已平仓合计",
    }


@router.post("/paper/accounts/create")
def create_account(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    strategy_id = str(payload.get("strategy_id") or "")
    return _paper.apply_strategy(strategy_id, create_if_missing=True)


@router.get("/scan/strategy")
def scan_strategy(
    strategy_id: str = Query(...),
    market: str = Query("all"),
    top_n: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    try:
        if strategy_id not in STRATEGY_REGISTRY:
            raise HTTPException(status_code=400, detail="未知策略")
        frames, names = _engine.load_frames(market)
        items = run_strategy_by_id(strategy_id, frames, names, top_n=top_n)
        meta = STRATEGY_REGISTRY[strategy_id]
        return {
            "strategy": {k: meta[k] for k in ["id", "name", "source", "source_label", "desc", "risk_profile", "default_holding_days"]},
            "market": market,
            "items": items,
            "disclaimer": DISCLAIMER,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/scan/strategy")
def scan_strategy_post(
    strategy_id: str = Query(...),
    market: str = Query("all"),
    top_n: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    """兼容 POST 调用。"""
    return scan_strategy(strategy_id=strategy_id, market=market, top_n=top_n)


@router.get("/risk/config")
def risk_config() -> dict[str, Any]:
    return RiskConfig.load().to_dict()


@router.post("/risk/rebalance")
def risk_rebalance(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """调仓计算：传入目标标的与账户，返回买卖数量（含风控）。"""
    try:
        targets = payload.get("targets") or []
        positions = payload.get("positions") or []
        prices = payload.get("prices") or {}
        equity = float(payload.get("equity") or 0)
        cash = float(payload.get("cash") or 0)
        return calculate_rebalance(
            target_codes=targets,
            positions=positions,
            prices=prices,
            equity=equity,
            cash=cash,
            risk=RiskConfig.load(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/benchmarks")
def benchmarks() -> dict[str, Any]:
    return {"items": _bench.list_meta(), "default": _bench.default_id, "pass_rules": PASS_RULES}


@router.get("/backtest/v2")
def backtest_v2(
    strategy: str = Query("short_term_hot"),
    market: str = Query("a_share"),
    top_n: int = Query(5, ge=1, le=20),
    holding_days: int | None = Query(None, ge=1, le=250),
    benchmark: str | None = Query(None),
    sensitivity: bool = Query(True),
) -> dict[str, Any]:
    try:
        return _bt2.run(
            strategy_id=strategy,
            market=market,
            top_n=top_n,
            holding_days=holding_days,
            benchmark_id=benchmark,
            run_sensitivity=sensitivity,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/lhb")
def dragon_tiger(
    days: int = Query(5, ge=1, le=30),
    top_n: int = Query(20),
    date: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
) -> dict[str, Any]:
    try:
        board = _lhb_provider.get_recent_board(days=max(days, 15 if date_from else days))
        if board is not None and not board.empty:
            if date:
                board = board[board["trade_date"] <= date]
            if date_from:
                board = board[board["trade_date"] >= date_from]
            if date_to:
                board = board[board["trade_date"] <= date_to]
        else:
            board = board
        code_map = _lhb_provider.build_code_map(board) if board is not None else {}
        rows = []
        for code, entry in code_map.items():
            row = dict(entry)
            row["score"] = _extras.lhb_score(entry)
            rows.append(row)
        rows.sort(key=lambda x: float(x.get("net_amt_sum") or 0), reverse=True)
        return {"count": len(rows), "items": rows[:top_n], "date_from": date_from, "date_to": date_to}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/scan")
def scan(
    market: str = Query("all"),
    top_n: int = Query(20, ge=1, le=100),
    strategy: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    report_date: str | None = Query(None),
) -> dict[str, Any]:
    try:
        if strategy and strategy in STRATEGY_REGISTRY:
            frames, names = _engine.load_frames(market)
            items = run_strategy_by_id(strategy, frames, names, top_n=top_n)
            return {
                "mode": "live_registry",
                "strategy": strategy,
                "market": market,
                "items": items,
                "strategies": {
                    strategy: {
                        **{k: STRATEGY_REGISTRY[strategy][k] for k in ["name", "desc", "source_label"]},
                        "items": items,
                    }
                },
                "disclaimer": DISCLAIMER,
            }
        if report_date or (date_from and date_to and date_from == date_to):
            hist = _signals.query_history(
                report_date=report_date or date_from,
                strategy=strategy if strategy in STRATEGY_MAP else None,
                date_from=date_from,
                date_to=date_to,
                limit=top_n * 20,
            )
            by_strat: dict[str, list] = {}
            for row in hist["items"]:
                by_strat.setdefault(row["strategy"], []).append(
                    {
                        "code": row["code"],
                        "name": row["name"],
                        "market": "crypto" if "-USDT" in str(row["code"]) else "a_share",
                        "score": row["score"],
                        "price": row["price"],
                        "reason": row["reason"],
                    }
                )
            strategies_payload = {}
            for key in list(STRATEGY_MAP) + ["dragon_tiger"]:
                strategies_payload[key] = {
                    **STRATEGY_META.get(key, {"name": key, "desc": key}),
                    "items": (by_strat.get(key) or [])[:top_n],
                }
            return {
                "mode": "history_db",
                "report_date": report_date or date_from,
                "strategies": strategies_payload,
                "available_dates": hist.get("dates") or [],
                "disclaimer": DISCLAIMER,
            }
        result = _engine.run_all(market=market, top_n=top_n)
        result["mode"] = "live"
        result["available_dates"] = db.signal_dates(limit=30)
        result["disclaimer"] = DISCLAIMER
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/signals/history")
def signals_history(
    report_date: str | None = Query(None),
    strategy: str | None = Query(None),
    market: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(200),
) -> dict[str, Any]:
    return _signals.query_history(
        report_date=report_date,
        strategy=strategy,
        market=market,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@router.post("/signals/generate")
def generate_signals(market: str = Query("all"), top_n: int = Query(20), push: bool = Query(False)) -> dict[str, Any]:
    return _signals.generate(market=market, top_n=top_n, push_feishu=True if push else None)


@router.post("/signals/push")
def push_signals(market: str = Query("all"), top_n: int = Query(20)) -> dict[str, Any]:
    return _signals.generate(market=market, top_n=top_n, push_feishu=True)


@router.post("/signals/morning")
def morning_push(
    market: str = Query("all"),
    account_id: str | None = Query(None),
    slot: str = Query("morning"),
    all_strategies: bool = Query(True),
) -> dict[str, Any]:
    """时段关注推送。默认全策略；slot=morning|midday|evening。"""
    if all_strategies:
        return _paper.watch_all_strategies(slot=slot, market=market, push=True)
    return _paper.morning_watchlist(market=market, account_id=account_id)


@router.get("/signals/watch")
def watch_slot(
    slot: str = Query("morning"),
    market: str = Query("all"),
    push: bool = Query(False),
) -> dict[str, Any]:
    try:
        return _paper.watch_all_strategies(slot=slot, market=market, push=push)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/watch/page-data")
def watch_page_data(
    slot: str = Query("morning"),
    report_date: str | None = Query(None, description="YYYY-MM-DD，查看历史关注快照；不传则实时计算"),
) -> dict[str, Any]:
    """页面同步数据：全策略关注+持仓。可按日期查历史快照。"""
    try:
        if report_date:
            snap = db.query_watch_snapshot(slot=slot, report_date=report_date)
            if snap:
                snap["mode"] = "history_db"
                snap["report_date"] = report_date
                snap["available_dates"] = db.watch_snapshot_dates(slot, limit=30)
                snap["push"] = {"ok": False, "skipped": True, "error": "历史快照仅查看，未推送"}
                return snap
            return {
                "slot": slot,
                "mode": "history_db",
                "report_date": report_date,
                "sections": [],
                "available_dates": db.watch_snapshot_dates(slot, limit=30),
                "error": f"{report_date} 无 {slot} 关注快照（需当日跑过关注/模拟任务）",
            }
        data = _paper.watch_all_strategies(slot=slot, market="all", push=False)
        data["mode"] = "live"
        data["available_dates"] = db.watch_snapshot_dates(slot, limit=30)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/watch/dates")
def watch_dates(slot: str = Query("morning")) -> dict[str, Any]:
    return {"slot": slot, "dates": db.watch_snapshot_dates(slot, limit=60)}


@router.post("/paper/run-all")
def paper_run_all(refresh_signals: bool = Query(True)) -> dict[str, Any]:
    """全部策略独立账户分别执行模拟盘。"""
    try:
        results = _paper.run_all_strategy_accounts(force_refresh_signals=refresh_signals)
        return {"ok": True, "count": len(results), "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/paper/state")
def paper_state(
    account_id: str | None = Query(None),
    strategy_id: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    strategy: str | None = Query(None),
    action: str | None = Query(None),
    code: str | None = Query(None),
) -> dict[str, Any]:
    return _paper.snapshot(
        account_id=account_id,
        strategy_id=strategy_id,
        date_from=date_from,
        date_to=date_to,
        strategy=strategy,
        action=action,
        code=code,
    )


@router.post("/paper/run")
def paper_run(
    refresh_signals: bool = Query(False),
    account_id: str | None = Query(None),
    strategy_id: str | None = Query(None),
) -> dict[str, Any]:
    run_log = _paper.run_daily(
        force_refresh_signals=refresh_signals,
        account_id=account_id,
        strategy_id=strategy_id,
    )
    snap = _paper.snapshot(account_id=run_log.get("account_id"))
    return {"run": run_log, "snapshot": snap}


@router.get("/paper/trades")
def paper_trades(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    action: str | None = Query(None),
    strategy: str | None = Query(None),
    code: str | None = Query(None),
    limit: int = Query(200),
) -> dict[str, Any]:
    return {
        "items": db.query_trades(date_from=date_from, date_to=date_to, action=action, strategy=strategy, code=code, limit=limit),
        "stats": db.portfolio_stats_from_db(),
    }


@router.get("/stocks")
def list_stocks(market: str = Query("a_share")) -> dict[str, Any]:
    market = _engine.resolve_market(market)
    out = []
    if market in {"a_share", "all"}:
        stocks = _engine.ashare.get_stock_list()
        for _, r in stocks.iterrows():
            out.append({"code": str(r["code"]), "name": str(r["name"]), "market": "a_share"})
    if market in {"crypto", "all"}:
        for sym in _engine.crypto.symbols:
            out.append({"code": sym, "name": sym, "market": "crypto"})
    return {"market": market, "count": len(out), "items": out[:300]}


@router.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
