from __future__ import annotations

"""绩效分析：净值、回撤、夏普、年化、盈亏分布、月度热力图、参数敏感性。"""

from typing import Any

import numpy as np
import pandas as pd


def max_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    eq = np.array(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd = 1 - eq / np.maximum(peak, 1e-12)
    return float(np.max(dd) * 100)


def annualized_return(equity: list[float], periods_per_year: int = 242) -> float:
    if len(equity) < 2:
        return 0.0
    eq = np.array(equity, dtype=float)
    total = eq[-1] / eq[0]
    years = (len(eq) - 1) / periods_per_year
    if years <= 0 or total <= 0:
        return 0.0
    return float((total ** (1 / years) - 1) * 100)


def sharpe_ratio(returns: list[float], periods_per_year: int = 242, rf_annual: float = 0.0) -> float:
    if not returns or len(returns) < 3:
        return 0.0
    r = np.array(returns, dtype=float)
    rf = rf_annual / periods_per_year
    excess = r - rf
    std = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
    if std <= 1e-12:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(periods_per_year))


def calmar_ratio(equity: list[float], periods_per_year: int = 242) -> float:
    ann = annualized_return(equity, periods_per_year)
    dd = max_drawdown(equity)
    return float(ann / dd) if dd > 0 else 0.0


def win_rate_from_trades(trades: list[dict[str, Any]]) -> float | None:
    closed = [t for t in trades if t.get("action") == "sell" and t.get("pnl") is not None]
    if not closed:
        return None
    wins = [t for t in closed if float(t.get("pnl") or 0) > 0]
    return round(len(wins) / len(closed), 4)


def monthly_returns(dates: list[str], equity: list[float]) -> list[dict[str, Any]]:
    if len(dates) < 2 or len(equity) < 2:
        return []
    df = pd.DataFrame({"date": pd.to_datetime(dates), "equity": equity})
    df = df.sort_values("date")
    df["ym"] = df["date"].dt.strftime("%Y-%m")
    out = []
    prev_eq = None
    for ym, g in df.groupby("ym"):
        eq0 = float(prev_eq) if prev_eq is not None else float(g["equity"].iloc[0])
        eq1 = float(g["equity"].iloc[-1])
        ret = (eq1 / eq0 - 1) * 100 if eq0 else 0.0
        out.append({"month": str(ym), "return_pct": round(ret, 3)})
        prev_eq = eq1
    return out


def pnl_distribution(trades: list[dict[str, Any]], bins: int = 10) -> dict[str, Any]:
    pnls = [
        float(t.get("pnl_pct") if t.get("pnl_pct") is not None else 0)
        for t in trades
        if t.get("action") == "sell" and t.get("pnl") is not None
    ]
    if not pnls:
        return {"count": 0, "bins": [], "avg": None, "median": None}
    arr = np.array(pnls, dtype=float)
    hist, edges = np.histogram(arr, bins=bins)
    bins_out = [
        {"from": round(float(edges[i]), 2), "to": round(float(edges[i + 1]), 2), "count": int(hist[i])}
        for i in range(len(hist))
    ]
    return {
        "count": len(arr),
        "bins": bins_out,
        "avg": round(float(arr.mean()), 3),
        "median": round(float(np.median(arr)), 3),
        "min": round(float(arr.min()), 3),
        "max": round(float(arr.max()), 3),
        "win_rate": round(float((arr > 0).mean()), 4),
    }


def performance_report(
    dates: list[str],
    equity: list[float],
    trades: list[dict[str, Any]] | None = None,
    benchmark_equity: list[float] | None = None,
    benchmark_name: str = "",
    periods_per_year: int = 242,
) -> dict[str, Any]:
    if not equity:
        return {"error": "无净值序列"}
    rets = []
    for i in range(1, len(equity)):
        prev, cur = equity[i - 1], equity[i]
        rets.append((cur / prev - 1) if prev else 0.0)
    total_ret = (equity[-1] / equity[0] - 1) * 100 if equity[0] else 0.0
    mdd = max_drawdown(equity)
    ann = annualized_return(equity, periods_per_year)
    sharpe = sharpe_ratio(rets, periods_per_year)
    months = monthly_returns(dates, equity)

    bench = None
    excess = None
    passed = None
    pass_reason = ""
    under_months = 0
    if benchmark_equity and len(benchmark_equity) >= 2:
        b_total = (benchmark_equity[-1] / benchmark_equity[0] - 1) * 100
        b_mdd = max_drawdown(benchmark_equity)
        b_rets = [
            (benchmark_equity[i] / benchmark_equity[i - 1] - 1) if benchmark_equity[i - 1] else 0
            for i in range(1, len(benchmark_equity))
        ]
        b_ann = annualized_return(benchmark_equity, periods_per_year)
        bench = {
            "name": benchmark_name,
            "total_return_pct": round(b_total, 2),
            "max_drawdown_pct": round(b_mdd, 2),
            "annualized_pct": round(b_ann, 2),
            "sharpe": round(sharpe_ratio(b_rets, periods_per_year), 3),
        }
        excess = round(total_ret - bench["total_return_pct"], 2)
        beat = excess >= 5.0
        lower_dd = mdd < bench["max_drawdown_pct"]
        passed = bool(beat and lower_dd)
        pass_reason = (
            f"超额 {excess}pp（要求≥5）{'达标' if beat else '未达标'}；"
            f"回撤 {mdd:.2f}% vs 基准 {bench['max_drawdown_pct']}% "
            f"{'更小·达标' if lower_dd else '不小于基准·未达标'}"
        )
        # 连续跑输月
        b_months = monthly_returns(dates[: len(benchmark_equity)], benchmark_equity)
        b_map = {m["month"]: m["return_pct"] for m in b_months}
        streak = 0
        for m in months:
            b = b_map.get(m["month"])
            if b is None:
                continue
            if m["return_pct"] < b:
                streak += 1
                under_months = max(under_months, streak)
            else:
                streak = 0

    return {
        "dates": dates,
        "equity": [round(float(x), 6) for x in equity],
        "total_return_pct": round(total_ret, 2),
        "annualized_pct": round(ann, 2),
        "max_drawdown_pct": round(mdd, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar_ratio(equity, periods_per_year), 3),
        "volatility_annual_pct": round(float(np.std(rets) * np.sqrt(periods_per_year) * 100), 2) if rets else 0.0,
        "win_rate_trades": win_rate_from_trades(trades or []),
        "monthly_returns": months,
        "pnl_distribution": pnl_distribution(trades or []),
        "benchmark": bench,
        "excess_return_pct": excess,
        "underperform_streak_months": under_months,
        "qualified": passed,
        "qualified_reason": pass_reason,
        "pass_rule": "合格：策略累计收益 ≥ 基准+5pp，且策略最大回撤 < 基准最大回撤",
    }


def parameter_sensitivity(base_result: dict[str, Any], variant_results: list[dict[str, Any]]) -> dict[str, Any]:
    base_ret = float(base_result.get("total_return_pct") or 0)
    base_dd = float(base_result.get("max_drawdown_pct") or 0)
    rows = []
    collapse = False
    for v in variant_results:
        ret = float(v.get("total_return_pct") or 0)
        dd = float(v.get("max_drawdown_pct") or 0)
        delta_ret = ret - base_ret
        ret_collapse = (base_ret > 5 and ret < base_ret * 0.5) or (delta_ret <= -15)
        if ret_collapse:
            collapse = True
        rows.append(
            {
                "param": v.get("param"),
                "value": v.get("value"),
                "total_return_pct": round(ret, 2),
                "max_drawdown_pct": round(dd, 2),
                "delta_return_pp": round(delta_ret, 2),
                "collapse": ret_collapse,
            }
        )
    stable = (not collapse) and (len(rows) > 0)
    return {
        "base": {"param": base_result.get("param", "default"), "total_return_pct": round(base_ret, 2), "max_drawdown_pct": round(base_dd, 2)},
        "variants": rows,
        "stable": stable,
        "overfit_warning": (
            "参数微调后收益大幅崩塌，存在过拟合风险，不建议实盘使用。"
            if collapse
            else "参数小幅扰动下收益未出现断崖式下跌，稳健性相对可接受（仍需样本外验证）。"
        ),
        "conclusion": "稳健" if stable else "敏感/可能过拟合",
    }
