from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.config import load_config
from app.strategies.engine import StrategyEngine, STRATEGY_META

LOOKBACK_NEEDED = {
    "short_term_hot": 25,
    "long_term_layout": 45,
    "bottom_fishing": 35,
}


class BacktestEngine:
    """滚动调仓近似：信号日用截至当日历史打分，持有 N 日后按双边费率结算。"""

    def __init__(self, engine: StrategyEngine | None = None) -> None:
        self.engine = engine or StrategyEngine()
        self.cfg = load_config()

    @staticmethod
    def _future_return(close: pd.Series, idx: int, holding_days: int) -> float | None:
        if idx < 0 or idx + holding_days >= len(close):
            return None
        entry = float(close.iloc[idx])
        exit_ = float(close.iloc[idx + holding_days])
        if entry <= 0:
            return None
        return exit_ / entry - 1

    @staticmethod
    def _calendar_positions(lengths: list[int], holding_days: int, lookback: int, max_points: int) -> list[int]:
        """在“还能完整持有”的历史区间内，选若干调仓下标（对齐较短样本）。"""
        if not lengths:
            return []
        usable = []
        for n in lengths:
            # 最早满足 lookback，最晚还能持有 holding_days
            lo = lookback
            hi = n - holding_days - 1
            if hi > lo:
                usable.append((lo, hi))
        if not usable:
            return []
        # 取交集过严时，用分位区间：以样本长度的 20%~80% 可持有窗口
        lo = int(np.median([u[0] for u in usable]))
        hi = int(np.percentile([u[1] for u in usable], 70))
        if hi <= lo:
            lo = max(lookback, min(u[0] for u in usable))
            hi = max(u[1] for u in usable)
        if hi <= lo:
            return []
        points = np.linspace(lo, hi, num=min(max_points, max(3, (hi - lo) // 4)))
        return sorted({int(round(p)) for p in points})

    def run(
        self,
        strategy_key: str,
        market: str | None = None,
        holding_days: int | None = None,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        if strategy_key not in STRATEGY_META:
            raise ValueError(f"unknown strategy: {strategy_key}")
        bt_cfg = self.cfg["backtest"].get(strategy_key, {})
        holding_days = int(holding_days or bt_cfg.get("holding_days", 5))
        top_k = int(top_k or bt_cfg.get("top_k", 10))
        fee = float(bt_cfg.get("fee_rate", 0.001))
        max_points = int(self.cfg["backtest"].get("max_rebalance_points", 12))
        lookback = LOOKBACK_NEEDED.get(strategy_key, 30)

        frames, names = self.engine.load_frames(market)
        if not frames:
            return {
                "strategy": strategy_key,
                "strategy_label": STRATEGY_META[strategy_key]["label"],
                "error": "行情数据为空，无法回测",
            }

        strategy = self.engine.strategies[strategy_key]
        lengths = [len(df) for df in frames.values() if len(df) >= lookback + holding_days]
        if not lengths:
            return {
                "strategy": strategy_key,
                "strategy_label": STRATEGY_META[strategy_key]["label"],
                "error": f"历史数据不足（需要至少约 {lookback + holding_days} 根K线）",
            }

        positions = self._calendar_positions(lengths, holding_days, lookback, max_points)
        if not positions:
            return {
                "strategy": strategy_key,
                "strategy_label": STRATEGY_META[strategy_key]["label"],
                "error": "有效回测调仓点不足",
            }

        trades: list[dict[str, Any]] = []
        period_returns: list[dict[str, Any]] = []

        for pos in positions:
            hist_frames: dict[str, pd.DataFrame] = {}
            for code, df in frames.items():
                if len(df) < pos + 2:
                    continue
                # 保留足够历史窗口供策略计算均线/回撤
                start = max(0, pos + 1 - max(lookback * 2, 80))
                hist_frames[code] = df.iloc[start : pos + 1].reset_index(drop=True)
            if len(hist_frames) < max(3, min(top_k, 5)):
                continue
            ranking = strategy.run_pool(hist_frames, names=names, top_n=top_k)
            if not ranking:
                continue

            slot_returns = []
            signal_date = None
            for item in ranking:
                key = item["code"]
                if key not in frames or key not in hist_frames:
                    continue
                signal_date = hist_frames[key]["date"].iloc[-1]
                raw = frames[key]
                matches = raw.index[raw["date"] == signal_date]
                if len(matches) == 0:
                    continue
                idx = int(matches[0])
                ret = self._future_return(raw["close"], idx, holding_days)
                if ret is None:
                    continue
                net = ret - fee * 2
                slot_returns.append(net)
                trades.append(
                    {
                        "signal_date": str(pd.Timestamp(signal_date).date()),
                        "code": key.split(":", 1)[-1],
                        "name": names.get(key, key),
                        "score": item.get("score"),
                        "raw_return_pct": round(ret * 100, 2),
                        "net_return_pct": round(net * 100, 2),
                    }
                )
            if slot_returns and signal_date is not None:
                period_returns.append(
                    {
                        "signal_date": str(pd.Timestamp(signal_date).date()),
                        "avg_net_return_pct": round(float(np.mean(slot_returns)) * 100, 2),
                        "win_rate": round(float(np.mean([1 if r > 0 else 0 for r in slot_returns])), 3),
                        "n": len(slot_returns),
                    }
                )

        if not period_returns:
            return {
                "strategy": strategy_key,
                "strategy_label": STRATEGY_META[strategy_key]["label"],
                "error": "有效回测调仓点不足",
            }

        nets = np.array([p["avg_net_return_pct"] for p in period_returns], dtype=float)
        wins = np.array([p["win_rate"] for p in period_returns], dtype=float)
        equity = np.cumprod(1 + nets / 100.0)
        peak = np.maximum.accumulate(equity)
        max_dd = float(np.max(1 - equity / peak) * 100) if len(equity) else 0.0

        return {
            "strategy": strategy_key,
            "strategy_label": STRATEGY_META[strategy_key]["label"],
            "market": self.engine.resolve_market(market),
            "holding_days": holding_days,
            "top_k": top_k,
            "fee_rate": fee,
            "n_rebalances": len(period_returns),
            "avg_period_net_return_pct": round(float(nets.mean()), 2),
            "median_period_net_return_pct": round(float(np.median(nets)), 2),
            "win_rate_avg": round(float(wins.mean()), 3),
            "cumulative_return_pct": round(float((equity[-1] - 1) * 100), 2),
            "max_drawdown_pct": round(max_dd, 2),
            "periods": period_returns,
            "sample_trades": trades[-50:],
            "disclaimer": (
                "回测基于历史数据与简化撮合（持有到期、双边费率），"
                "未包含涨跌停、流动性冲击与停牌，结果可能显著优于/劣于真实交易。"
            ),
        }
