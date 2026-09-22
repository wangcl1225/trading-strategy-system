from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.config import load_config
from app.strategies.base import BaseStrategy, minmax_score, rsi, weighted_score


class BottomFishingStrategy(BaseStrategy):
    """抄底潜伏：超跌 + 缩量 + 接近支撑 + 出现企稳迹象。

    这是左侧/半左侧信号，胜率天然不稳定，必须配合回测与仓位管理。
    """

    name = "bottom_fishing"
    label = "抄底潜伏"

    def __init__(self) -> None:
        cfg = load_config()["strategies"]["bottom_fishing"]
        self.lookback = int(cfg.get("lookback_days", 120))
        self.weights = dict(cfg.get("weights") or {})
        self.default_top_n = int(cfg.get("top_n", 20))
        self.min_price = float(cfg.get("min_price", 2.0))
        self.max_drawdown_pct = float(cfg.get("max_drawdown_pct", 35))

    def score_symbol(self, code: str, df: pd.DataFrame, name: str = "") -> dict[str, Any]:
        work = df.tail(self.lookback).copy()
        close = work["close"].astype(float)
        high = work["high"].astype(float)
        low = work["low"].astype(float)
        volume = work["volume"].astype(float)
        if len(close) < 30:
            return {}

        last = float(close.iloc[-1])
        if last < self.min_price:
            return {}

        period_high = float(close.max())
        drawdown = (last / period_high - 1) * 100 if period_high else 0
        # 候选条件：回撤足够深，或处于低位分位
        price_pct = float((close <= last).mean() * 100)
        if drawdown > -15 and price_pct > 40:
            return {}

        vol_ma20 = float(volume.rolling(20, min_periods=5).mean().iloc[-1]) or 1.0
        vol_ma5 = float(volume.rolling(5, min_periods=2).mean().iloc[-1]) or 1.0
        volume_dry = vol_ma5 / vol_ma20 if vol_ma20 else 1.0

        recent_low = float(low.tail(20).min())
        support_dist = (last / recent_low - 1) * 100 if recent_low else 0

        rsi14 = rsi(close, 14)
        rets = close.pct_change().dropna()
        last5 = rets.tail(5)
        # 企稳：近 5 日不再加速下跌，且收盘站上 5 日线
        ma5 = float(close.rolling(5, min_periods=2).mean().iloc[-1])
        stab_raw = 0.0
        if last >= ma5:
            stab_raw += 35
        if len(last5) and float(last5.mean()) > -0.005:
            stab_raw += 35
        if len(rets) >= 10:
            prev_drop = float(rets.iloc[-10:-5].mean()) if len(rets) >= 10 else 0
            recent = float(rets.iloc[-5:].mean())
            if recent > prev_drop:
                stab_raw += 30
        stabilization_score = float(np.clip(stab_raw, 0, 100))

        # 各因子 0-100，越高越像“可潜伏”
        drawdown_score = float(np.clip((-drawdown - 10) / max(self.max_drawdown_pct - 10, 1) * 100, 0, 100))
        rsi_score = float(np.clip((45 - rsi14) / 25 * 100, 0, 100))  # 越低分越高，但不过度追 20 以下极端
        if rsi14 < 20:
            rsi_score = 70  # 极端超跌可能继续钝化
        volume_dry_score = float(np.clip((1.2 - volume_dry) / 0.7 * 100, 0, 100))
        support_score = float(np.clip(100 - abs(support_dist) * 8, 0, 100))
        if support_dist > 25:
            support_score = max(0, support_score - 40)

        components = {
            "drawdown_from_high_pct": round(drawdown, 2),
            "price_percentile": round(price_pct, 1),
            "rsi_14": round(rsi14, 1),
            "volume_dry_ratio": round(volume_dry, 3),
            "support_distance_pct": round(support_dist, 2),
            "ma5": round(ma5, 4),
            "recent_low_20d": round(recent_low, 4),
        }

        parts = pd.DataFrame(
            [
                {
                    "drawdown_score": drawdown_score,
                    "rsi_score": rsi_score,
                    "volume_dry_score": volume_dry_score,
                    "support_score": support_score,
                    "stabilization_score": stabilization_score,
                }
            ]
        )
        score = float(weighted_score(parts, self.weights).iloc[0])

        reason_bits = []
        if drawdown <= -30:
            reason_bits.append(f"自区间高点回撤{abs(drawdown):.0f}%")
        if rsi14 <= 35:
            reason_bits.append(f"RSI={rsi14:.0f}偏超卖")
        if volume_dry <= 0.85:
            reason_bits.append(f"近5日量能萎缩至20日{volume_dry*100:.0f}%")
        if support_dist <= 8:
            reason_bits.append(f"距20日低点仅{support_dist:.1f}%")
        if stabilization_score >= 70:
            reason_bits.append("出现止跌/企稳迹象")

        return {
            "code": code,
            "name": name or code,
            "score": round(score, 2),
            "price": round(last, 4),
            "change_20d_pct": round(float(close.iloc[-1] / close.iloc[-min(len(close), 20)] - 1) * 100, 2)
            if len(close) >= 2
            else 0,
            "components": components,
            "reason": "；".join(reason_bits) if reason_bits else "超跌与缩量结构综合评分居前",
        }

    def run_pool(
        self, frames: dict[str, pd.DataFrame], names: dict[str, str] | None = None, top_n: int | None = None
    ) -> list[dict[str, Any]]:
        names = names or {}
        top_n = top_n or self.default_top_n
        items = []
        for code, df in frames.items():
            item = self.score_symbol(code, df, name=names.get(code, code))
            if item:
                items.append(item)
        if not items:
            return []

        cross = pd.DataFrame(
            [
                {
                    "code": it["code"],
                    "drawdown_score": float(np.clip((-it["components"]["drawdown_from_high_pct"] - 10) / max(self.max_drawdown_pct - 10, 1) * 100, 0, 100)),
                    "rsi_score": float(np.clip((45 - it["components"]["rsi_14"]) / 25 * 100, 0, 100))
                    if it["components"]["rsi_14"] >= 20
                    else 70,
                    "volume_dry_score": float(np.clip((1.2 - it["components"]["volume_dry_ratio"]) / 0.7 * 100, 0, 100)),
                    "support_score": float(np.clip(100 - abs(it["components"]["support_distance_pct"]) * 8, 0, 100)),
                    "stabilization_score": it.get("_stab", 0),
                }
                for it in items
            ]
        ).set_index("code")

        # stabilization 用 reason 粗估不稳，这里直接用原始 score 分量重算：保持启发式即可
        scored = []
        for it in items:
            # 在抄底池内做相对排序
            local = {}
            for col in self.weights:
                if col == "stabilization_score":
                    # 从 reason 提示近似
                    local[col] = 80 if "企稳" in it.get("reason", "") else 45
                    continue
                if col not in cross.columns:
                    local[col] = 50
                    continue
                local[col] = float(minmax_score(cross[col], higher_better=True).loc[it["code"]])
            score = float(
                sum(local[k] * float(self.weights.get(k, 0)) for k in local)
                / max(sum(self.weights.values()), 1e-9)
            )
            new_item = dict(it)
            new_item["score"] = round(score, 2)
            scored.append(new_item)
        scored.sort(key=lambda x: x["score"], reverse=True)
        if not scored:
            fallback = sorted(items, key=lambda x: x.get("score", 0), reverse=True)
            return fallback[:top_n]
        return scored[:top_n]
