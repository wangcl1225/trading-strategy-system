from __future__ import annotations

"""专业回测引擎 v2：
- T日收盘选股，T+1开盘成交
- 交易成本：佣金+印花税+滑点（A股），加密费率
- 风控内置：单票上限、止损、组合降仓
- 输出净值曲线 + 对比基准 + 绩效 + 参数敏感性
"""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.analytics.performance import parameter_sensitivity, performance_report
from app.benchmark import BENCHMARKS, PASS_RULES, BenchmarkProvider
from app.config import load_config
from app.risk.engine import RiskConfig, calculate_rebalance, compute_risk_state
from app.strategies.engine import StrategyEngine
from app.strategies.registry import STRATEGY_REGISTRY, run_strategy_by_id

# 默认交易成本（可配置）
DEFAULT_COSTS = {
    "a_share": {
        "commission_rate": 0.00015,   # 万1.5
        "stamp_tax_sell": 0.0005,     # 印花税卖出 0.05%
        "slippage_pct": 0.001,        # 滑点 0.1%
        "min_commission": 5.0,
    },
    "crypto": {
        "commission_rate": 0.001,
        "stamp_tax_sell": 0.0,
        "slippage_pct": 0.001,
        "min_commission": 0.0,
    },
}


def trade_cost(market: str, price: float, shares: float, side: str, costs: dict[str, None] | None = None) -> float:
    costs = costs or DEFAULT_COSTS
    m = costs.get(market) or costs["a_share"]
    amount = price * shares
    slip = amount * float(m.get("slippage_pct") or 0)
    commission = max(amount * float(m.get("commission_rate") or 0), float(m.get("min_commission") or 0))
    stamp = amount * float(m.get("stamp_tax_sell") or 0) if side == "sell" else 0.0
    return float(slip + commission + stamp)


@dataclass
class Account:
    initial_cash: float
    cash: float
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    peak_equity: float = 0.0
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    underperform_months: int = 0

    def equity(self, prices: dict[str, float]) -> float:
        mv = 0.0
        for code, p in self.positions.items():
            px = float(prices.get(code) or p.get("last_price") or p.get("avg_price") or 0)
            mv += float(p["shares"]) * px
        return self.cash + mv

    def position_views(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        eq = self.equity(prices)
        out = []
        for code, p in self.positions.items():
            last = float(prices.get(code) or p.get("last_price") or p.get("avg_price") or 0)
            shares = float(p["shares"])
            cost = float(p.get("avg_price") or 0)
            mv = shares * last
            out.append(
                {
                    **p,
                    "code": code,
                    "last_price": last,
                    "market_value": mv,
                    "weight_pct": round(mv / eq * 100, 2) if eq > 0 else 0,
                    "unrealized_pnl_pct": round((last - cost) / cost * 100, 2) if cost else 0,
                }
            )
        return out


def execute_buy(acct: Account, code: str, name: str, market: str, strategy: str, price: float, shares: float, date: str, note: str = "") -> bool:
    if shares <= 0 or price <= 0:
        return False
    fee = trade_cost(market, price, shares, "buy")
    need = price * shares + fee
    if need > acct.cash + 1e-6:
        return False
    acct.cash -= need
    pos = acct.positions.get(code)
    if not pos:
        acct.positions[code] = {
            "code": code,
            "name": name,
            "market": market,
            "strategy": strategy,
            "shares": float(shares),
            "avg_price": float(price),
            "buy_date": date,
            "fees": fee,
        }
    else:
        old_sh = float(pos["shares"])
        old_avg = float(pos["avg_price"])
        new_sh = old_sh + float(shares)
        pos["avg_price"] = (old_sh * old_avg + float(shares) * price) / new_sh if new_sh else price
        pos["shares"] = new_sh
        pos["fees"] = float(pos.get("fees") or 0) + fee
    acct.trades.append(
        {
            "date": date,
            "action": "buy",
            "code": code,
            "name": name,
            "market": market,
            "strategy": strategy,
            "price": price,
            "shares": float(shares),
            "fee": round(fee, 4),
            "amount": round(price * shares, 2),
            "note": note,
        }
    )
    return True


def execute_sell(acct: Account, code: str, price: float, date: str, note: str = "") -> None:
    pos = acct.positions.get(code)
    if not pos:
        return
    shares = float(pos["shares"])
    market = str(pos.get("market") or "a_share")
    fee = trade_cost(market, price, shares, "sell")
    proceeds = price * shares - fee
    cost = float(pos.get("avg_price") or 0) * shares + float(pos.get("fees") or 0)
    pnl = proceeds - cost
    pnl_pct = pnl / cost * 100 if cost else 0
    acct.cash += proceeds
    acct.trades.append(
        {
            "date": date,
            "action": "sell",
            "code": code,
            "name": pos.get("name"),
            "market": market,
            "strategy": pos.get("strategy"),
            "price": price,
            "shares": shares,
            "fee": round(fee, 4),
            "amount": round(proceeds, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "cost_price": pos.get("avg_price"),
            "note": note,
        }
    )
    del acct.positions[code]


class BacktestEngineV2:
    def __init__(self) -> None:
        self.engine = StrategyEngine()
        self.bench = BenchmarkProvider()
        self.cfg = load_config()
        self.costs = (self.cfg.get("costs") or DEFAULT_COSTS)
        self.risk = RiskConfig.load()

    def _hist_matrix(self, frames: dict[str, pd.DataFrame], days: int | None = None) -> pd.DataFrame:
        """对齐各标的收盘价矩阵。"""
        series = {}
        for key, df in frames.items():
            if df is None or df.empty:
                continue
            s = df.set_index("date")["close"].astype(float)
            series[key] = s
        if not series:
            return pd.DataFrame()
        mat = pd.DataFrame(series)
        mat = mat.sort_index()
        if days:
            mat = mat.tail(days)
        return mat

    def _open_matrix(self, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        series = {}
        for key, df in frames.items():
            if df is None or df.empty:
                continue
            series[key] = df.set_index("date")["open"].astype(float)
        if not series:
            return pd.DataFrame()
        return pd.DataFrame(series).sort_index()

    def run(
        self,
        strategy_id: str,
        market: str = "a_share",
        top_n: int = 5,
        holding_days: int | None = None,
        initial_cash: float | None = None,
        benchmark_id: str | None = None,
        lookback: int = 30,
        max_days: int = 90,
        run_sensitivity: bool = True,
        frames: dict[str, pd.DataFrame] | None = None,
        names: dict[str, str] | None = None,
        universe_limit: int = 60,
    ) -> dict[str, Any]:
        if strategy_id not in STRATEGY_REGISTRY:
            return {"error": f"未知策略 {strategy_id}"}
        meta = STRATEGY_REGISTRY[strategy_id]
        holding_days = int(holding_days or meta.get("default_holding_days") or 5)
        initial_cash = float(initial_cash or (self.cfg.get("paper_trade") or {}).get("initial_cash") or 1_000_000)
        benchmark_id = benchmark_id or self.cfg.get("benchmark", {}).get("default") or (
            "btc" if market == "crypto" else "hs300"
        )

        cache_key = f"{market}|{universe_limit}"
        if frames is None:
            if not hasattr(self, "_frame_cache"):
                self._frame_cache = {}
            if cache_key not in self._frame_cache:
                all_frames, all_names = self.engine.load_frames(market if market != "all" else "all")
                keys = list(all_frames.keys())
                if len(keys) > universe_limit:
                    step = max(1, len(keys) // universe_limit)
                    keys = keys[::step][:universe_limit]
                frames = {k: all_frames[k] for k in keys}
                names = {k: all_names.get(k, k) for k in keys}
                self._frame_cache[cache_key] = (frames, names)
            frames, names = self._frame_cache[cache_key]
        names = names or {}
        if not frames:
            return {"error": "无行情数据"}
        close_mat = self._hist_matrix(frames)
        open_mat = self._open_matrix(frames)
        if close_mat.empty or len(close_mat) < lookback + holding_days + 5:
            return {"error": f"历史长度不足，至少需要 {lookback + holding_days + 5} 根K线"}
        # 限制回测窗口长度，加快计算
        if len(close_mat) > max_days + lookback + holding_days + 5:
            tail_n = max_days + lookback + holding_days + 5
            close_mat = close_mat.tail(tail_n)
            open_mat = open_mat.tail(tail_n)
            # 同步裁剪 frames
            idx_min = close_mat.index[0]
            frames = {
                k: df[df["date"] >= idx_min].reset_index(drop=True) if "date" in df.columns else df.tail(tail_n)
                for k, df in frames.items()
            }

        dates = [str(pd.Timestamp(d).date()) for d in close_mat.index]
        n = len(dates)
        start = min(lookback, max(5, n // 4))
        end = n - 2

        acct = Account(initial_cash=initial_cash, cash=initial_cash, peak_equity=initial_cash)
        pending_sells: dict[str, str] = {}  # code -> sell_date
        next_rebalance_idx = start
        last_hold_end = start

        for i in range(start, end):
            date = dates[i]
            row = close_mat.iloc[i]
            prices = {k: float(row[k]) for k in close_mat.columns if pd.notna(row[k])}

            # T+1 开盘执行：昨日收盘生成的订单在今日开盘成交
            # 简化：在 i 日开盘用 open 价执行“到持有期的卖出”与“调仓单”
            open_row = open_mat.iloc[i] if i < len(open_mat) else row
            open_prices = {k: float(open_row[k]) for k in open_mat.columns if pd.notna(open_row[k])}

            # 1) 到期卖出（T+1 开盘价）
            for code, sell_date in list(pending_sells.items()):
                if sell_date == date:
                    px = open_prices.get(code) or prices.get(code)
                    if px:
                        execute_sell(acct, code, px, date, note="持有期到期 T+1开盘卖出")
                    pending_sells.pop(code, None)

            # 2) 止损检查（用收盘价判断，次日开盘执行）
            views = acct.position_views(prices)
            risk_state = compute_risk_state(
                acct.equity(prices), acct.cash, views, acct.peak_equity, acct.underperform_months, self.risk
            )
            for flag in risk_state.stop_loss_flags:
                code = str(flag.get("code"))
                if code in acct.positions and code not in pending_sells:
                    px = open_prices.get(code) or prices.get(code)
                    if px:
                        execute_sell(acct, code, px, date, note=f"风控止损 {flag.get('pnl_pct')}%")

            # 3) 调仓日：T日收盘选股 → 次日开盘成交
            if i >= next_rebalance_idx:
                # 用截至 T 收盘的数据选股
                hist_frames = {}
                for key, df in frames.items():
                    if len(df) > i:
                        hist_frames[key] = df.iloc[: i + 1]
                    else:
                        hist_frames[key] = df
                picks = run_strategy_by_id(strategy_id, hist_frames, names, top_n=top_n)
                targets = []
                for p in picks:
                    key = p.get("symbol_key") or (
                        f"ASHARE:{p['code']}" if p.get("market") == "a_share" else f"CRYPTO:{p['code']}"
                    )
                    # 映射回矩阵列
                    col = None
                    if key in close_mat.columns:
                        col = key
                    else:
                        for c in close_mat.columns:
                            if str(c).endswith(str(p["code"])) or str(p["code"]) in str(c):
                                col = c
                                break
                    if not col:
                        continue
                    targets.append(
                        {
                            "code": col,
                            "name": p.get("name"),
                            "market": p.get("market") or "a_share",
                            "strategy": strategy_id,
                            "weight_pct": 100.0 / max(len(picks), 1),
                        }
                    )

                # 次日索引
                j = i + 1
                if j < end:
                    j_date = dates[j]
                    j_open = open_mat.iloc[j] if j < len(open_mat) else close_mat.iloc[j]
                    j_prices = {k: float(j_open[k]) for k in open_mat.columns if pd.notna(j_open[k])}
                    pos_list = acct.position_views(prices)
                    reb = calculate_rebalance(
                        target_codes=targets,
                        positions=pos_list,
                        prices={**prices, **j_prices},
                        equity=acct.equity(prices),
                        cash=acct.cash,
                        risk=self.risk,
                        peak_equity=acct.peak_equity,
                        cost_rate=0.002,
                    )
                    # 先卖后买
                    for order in reb["orders"]:
                        code = order["code"]
                        if order["action"] == "sell":
                            px = j_prices.get(code) or prices.get(code)
                            if px and code in acct.positions:
                                execute_sell(acct, code, px, j_date, note=order.get("reason") or "调仓卖出")
                        elif order["action"] == "buy":
                            px = j_prices.get(code) or prices.get(code)
                            if not px:
                                continue
                            shares = order["target_shares"] if order["current_shares"] == 0 else max(
                                0.0, order["target_shares"] - order["current_shares"]
                            )
                            if order["current_shares"] == 0:
                                shares = order["target_shares"]
                            else:
                                shares = max(0.0, order["delta_shares"])
                            ok = execute_buy(
                                acct,
                                code,
                                order.get("name") or code,
                                order.get("market") or "a_share",
                                strategy_id,
                                px,
                                float(shares),
                                j_date,
                                note=order.get("reason") or "调仓买入",
                            )
                            if ok:
                                pending_sells[code] = dates[min(j + holding_days, n - 1)]
                    next_rebalance_idx = i + holding_days

            # 4) 每日盯市
            eq = acct.equity(prices)
            acct.peak_equity = max(acct.peak_equity, eq)
            acct.equity_curve.append({"date": date, "equity": round(eq, 2), "cash": round(acct.cash, 2)})

        # 绩效 + 基准
        eq_dates = [x["date"] for x in acct.equity_curve]
        eq_vals = [x["equity"] for x in acct.equity_curve]
        bench_series = self.bench.get_series(benchmark_id, days=n + 5)
        bench_map = {}
        if bench_series is not None and not bench_series.empty:
            for _, r in bench_series.iterrows():
                bench_map[str(pd.Timestamp(r["date"]).date())] = float(r["close"])
        bench_eq = []
        base_px = None
        for d in eq_dates:
            px = bench_map.get(d)
            if px is None:
                px = base_px
            else:
                if base_px is None:
                    base_px = px
            if px is None or base_px is None:
                bench_eq.append(None)
            else:
                bench_eq.append(px / base_px)
        # 填充 None
        last = 1.0
        bench_filled = []
        for v in bench_eq:
            if v is None:
                bench_filled.append(last)
            else:
                last = v
                bench_filled.append(v)
        # 对齐 equity 绝对金额
        bench_eq_money = [initial_cash * x for x in bench_filled]

        perf = performance_report(
            dates=eq_dates,
            equity=eq_vals,
            trades=acct.trades,
            benchmark_equity=bench_eq_money,
            benchmark_name=BENCHMARKS.get(benchmark_id, {}).get("name", benchmark_id),
        )
        perf["pass_rule_detail"] = PASS_RULES
        perf["benchmark_meta"] = self.bench.get_meta(benchmark_id)
        perf["benchmark_equity"] = [round(float(x), 2) for x in bench_eq_money]
        perf["costs"] = self.costs
        perf["risk"] = self.risk.to_dict()
        perf["strategy"] = {
            "id": strategy_id,
            "name": meta["name"],
            "source": meta["source_label"],
            "desc": meta["desc"],
        }
        perf["account"] = {
            "initial_cash": initial_cash,
            "final_equity": eq_vals[-1] if eq_vals else initial_cash,
            "cash": acct.cash,
            "positions": acct.position_views(
                {k: float(close_mat.iloc[-1][k]) for k in close_mat.columns if pd.notna(close_mat.iloc[-1][k])}
            )
            if len(close_mat)
            else [],
            "trade_count": len(acct.trades),
            "buy_count": len([t for t in acct.trades if t["action"] == "buy"]),
            "sell_count": len([t for t in acct.trades if t["action"] == "sell"]),
        }
        perf["execution_rule"] = "T日收盘选股，T+1开盘价成交；费用=佣金万1.5+印花税卖出0.05%+滑点0.1%"
        perf["underperform_streak_months"] = perf.get("underperform_streak_months") or 0

        # 参数敏感性
        sens = None
        if run_sensitivity:
            variants = []
            base = {"param": "holding_days", "value": holding_days, "total_return_pct": perf["total_return_pct"], "max_drawdown_pct": perf["max_drawdown_pct"]}
            for hd in sorted({max(2, holding_days - 2), holding_days + 2, holding_days + 5}):
                if hd == holding_days:
                    continue
                try:
                    sub = self.run(
                        strategy_id=strategy_id,
                        market=market,
                        top_n=top_n,
                        holding_days=hd,
                        initial_cash=initial_cash,
                        benchmark_id=benchmark_id,
                        lookback=lookback,
                        max_days=max_days,
                        run_sensitivity=False,
                        frames=frames,
                        names=names,
                        universe_limit=universe_limit,
                    )
                    variants.append(
                        {
                            "param": "holding_days",
                            "value": hd,
                            "total_return_pct": sub.get("total_return_pct"),
                            "max_drawdown_pct": sub.get("max_drawdown_pct"),
                        }
                    )
                except Exception as e:
                    variants.append({"param": "holding_days", "value": hd, "error": str(e)})
            for tn in sorted({max(3, top_n - 2), top_n + 2}):
                if tn == top_n:
                    continue
                try:
                    sub = self.run(
                        strategy_id=strategy_id,
                        market=market,
                        top_n=tn,
                        holding_days=holding_days,
                        initial_cash=initial_cash,
                        benchmark_id=benchmark_id,
                        lookback=lookback,
                        max_days=max_days,
                        run_sensitivity=False,
                        frames=frames,
                        names=names,
                        universe_limit=universe_limit,
                    )
                    variants.append(
                        {
                            "param": "top_n",
                            "value": tn,
                            "total_return_pct": sub.get("total_return_pct"),
                            "max_drawdown_pct": sub.get("max_drawdown_pct"),
                        }
                    )
                except Exception as e:
                    variants.append({"param": "top_n", "value": tn, "error": str(e)})
            sens = parameter_sensitivity(base, variants)
        perf["sensitivity"] = sens
        perf["rules"] = self.risk.to_dict()
        return perf
