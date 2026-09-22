from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.config import load_config
from app.strategies.base import BaseStrategy, minmax_score, weighted_score


class LongTermLayoutStrategy(BaseStrategy):
    """长线布局：趋势结构 + 波动质量 + 估值/景气代理（仅用行情可得因子）。

    说明：财务 ROE/PE 需另接财务数据源；当前用价格结构代理：
    - 趋势：收盘相对 MA60/MA120/MA250
    - 质量代理：上涨日均涨幅 vs 下跌日均跌幅、回撤修复速度
    - 估值代理：当前价在 250 日价格分位（越低分越高，偏左侧布局）
    - 恢复：距 250 日低点涨幅与距高点回撤
    """

    name = "long_term_layout"
    label = "长线布局"

    def __init__(self) -> None:
        cfg = load_config()["strategies"]["long_term_layout"]
        self.lookback = int(cfg.get("lookback_days", 250))
        self.weights = dict(cfg.get("weights") or {})
        self.default_top_n = int(cfg.get("top_n", 20))

    def score_symbol(self, code: str, df: pd.DataFrame, name: str = "") -> dict[str, Any]:
        work = df.tail(self.lookback).copy()
        close = work["close"].astype(float)
        if len(close) < 40:
            return {}

        last = float(close.iloc[-1])
        ma60 = float(close.rolling(60, min_periods=20).mean().iloc[-1])
        ma120 = float(close.rolling(120, min_periods=40).mean().iloc[-1]) if len(close) >= 40 else ma60
        ma250 = float(close.rolling(250, min_periods=60).mean().iloc[-1]) if len(close) >= 60 else ma120

        trend_raw = np.mean(
            [
                1 if last > ma60 else 0,
                1 if last > ma120 else 0,
                1 if last > ma250 else 0,
                (last / ma60 - 1) * 2 if ma60 else 0,
            ]
        )

        rets = close.pct_change().dropna()
        up = rets[rets > 0]
        down = rets[rets < 0]
        up_mean = float(up.mean()) if len(up) else 0.0
        down_mean = float(down.mean()) if len(down) else 0.0
        win_rate = float((rets > 0).mean()) if len(rets) else 0.5
        quality_proxy = win_rate * 100 + (up_mean + down_mean) * 200

        high = float(close.max())
        low = float(close.min())
        price_percentile = float((close <= last).mean() * 100)  # 0-100, 越高越贵
        valuation_score = 100 - price_percentile  # 越低越便宜，估值分越高

        vol = float(rets.std() * np.sqrt(252) * 100) if len(rets) else 30.0
        # 适度波动更好：过低没弹性，过高风险大
        if vol <= 10:
            vol_score = 40
        elif vol <= 35:
            vol_score = 100 - abs(vol - 22) * 2
        else:
            vol_score = max(10, 70 - (vol - 35))

        drawdown = (last / high - 1) * 100 if high else 0
        off_low = (last / low - 1) * 100 if low else 0
        # 恢复分：已离开底部一些，但未过热
        recovery_score = float(np.clip(50 + off_low * 0.8 + drawdown * 0.3, 0, 100))

        components = {
            "ma60": round(ma60, 4),
            "ma120": round(ma120, 4),
            "ma250": round(ma250, 4),
            "price_vs_ma60_pct": round((last / ma60 - 1) * 100, 2) if ma60 else 0,
            "price_vs_ma250_pct": round((last / ma250 - 1) * 100, 2) if ma250 else 0,
            "price_percentile_250d": round(price_percentile, 1),
            "drawdown_from_high_pct": round(drawdown, 2),
            "off_low_pct": round(off_low, 2),
            "win_rate_pct": round(win_rate * 100, 1),
            "annual_vol_pct": round(vol, 2),
        }

        parts = pd.DataFrame(
            [
                {
                    "valuation_score": valuation_score,
                    "trend_score": float(np.clip(50 + trend_raw * 40, 0, 100)),
                    "volatility_score": float(np.clip(vol_score, 0, 100)),
                    "quality_proxy": float(np.clip(quality_proxy, 0, 100)),
                    "recovery_score": recovery_score,
                }
            ]
        )
        score = float(weighted_score(parts, self.weights).iloc[0])

        reason_bits = []
        if last > ma60 > ma120:
            reason_bits.append("中期均线多头")
        if valuation_score >= 65:
            reason_bits.append(f"价格处于250日{price_percentile:.0f}%分位（偏低）")
        if win_rate >= 0.52:
            reason_bits.append(f"上涨日占比{win_rate*100:.0f}%")
        if -20 <= drawdown <= -5:
            reason_bits.append(f"距高点回撤{abs(drawdown):.0f}%")
        if off_low >= 10:
            reason_bits.append(f"已脱离低点{off_low:.0f}%")

        return {
            "code": code,
            "name": name or code,
            "score": round(score, 2),
            "price": round(last, 4),
            "change_20d_pct": round(float(close.iloc[-1] / close.iloc[-min(len(close), 20)] - 1) * 100, 2)
            if len(close) >= 2
            else 0,
            "components": components,
            "reason": "；".join(reason_bits) if reason_bits else "趋势与估值综合评分居前",
        }

    def run_pool(
        self, frames: dict[str, pd.DataFrame], names: dict[str, str] | None = None, top_n: int | None = None
    ) -> list[dict[str, Any]]:
        names = names or {}
        top_n = top_n or self.default_top_n
        items = []
        cross_rows = []
        for code, df in frames.items():
            item = self.score_symbol(code, df, name=names.get(code, code))
            if not item:
                continue
            c = item["components"]
            items.append(item)
            cross_rows.append(
                {
                    "code": code,
                    "valuation_score": 100 - c["price_percentile_250d"],
                    "trend_score": float(np.clip(50 + (c["price_vs_ma60_pct"] + c["price_vs_ma250_pct"]) / 4, 0, 100)),
                    "volatility_score": float(np.clip(100 - abs(c["annual_vol_pct"] - 22) * 1.5, 10, 100)),
                    "quality_proxy": c["win_rate_pct"] + max(0, -c["drawdown_from_high_pct"]) * 0.2,
                    "recovery_score": float(np.clip(50 + c["off_low_pct"] * 0.8 + c["drawdown_from_high_pct"] * 0.3, 0, 100)),
                }
            )
        if not cross_rows:
            return []
        cross = pd.DataFrame(cross_rows).set_index("code")
        scored = []
        for item in items:
            code = item["code"]
            row = cross.loc[[code]]
            parts = pd.DataFrame(
                {
                    col: [float(minmax_score(cross[col], higher_better=True).loc[code])]
                    for col in self.weights
                    if col in cross.columns
                }
            )
            # quality_proxy 同时用截面归一
            score = float(weighted_score(parts, self.weights).iloc[0]) if not parts.empty else item["score"]
            new_item = dict(item)
            new_item["score"] = round(score, 2)
            scored.append(new_item)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_n]
