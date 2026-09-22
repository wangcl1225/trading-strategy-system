from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.config import load_config
from app.strategies.base import BaseStrategy, minmax_score, weighted_score


class ShortTermHotStrategy(BaseStrategy):
    """短线热门：量价异动 + 动量 + 相对强度。"""

    name = "short_term_hot"
    label = "短线热门"

    def __init__(self) -> None:
        cfg = load_config()["strategies"]["short_term_hot"]
        self.lookback = int(cfg.get("lookback_days", 20))
        self.weights = dict(cfg.get("weights") or {})
        self.default_top_n = int(cfg.get("top_n", 20))

    def score_symbol(self, code: str, df: pd.DataFrame, name: str = "") -> dict[str, Any]:
        work = df.tail(max(self.lookback * 2, 40)).copy()
        close = work["close"].astype(float)
        volume = work["volume"].astype(float)
        high = work["high"].astype(float)
        low = work["low"].astype(float)

        last_close = float(close.iloc[-1])
        if last_close <= 0:
            return {}

        vol_ma20 = float(volume.rolling(20, min_periods=5).mean().iloc[-1]) or 1.0
        volume_surge = float(volume.iloc[-1] / vol_ma20) if vol_ma20 else 1.0

        # 加密货币无换手率，用成交量相对自身流通近似
        turnover_proxy = float(volume.iloc[-1] / volume.tail(self.lookback).mean()) if volume.tail(self.lookback).mean() else 1.0

        mom_5 = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0.0
        mom_10 = float(close.iloc[-1] / close.iloc[-11] - 1) if len(close) >= 11 else 0.0
        amp = float((high.iloc[-1] - low.iloc[-1]) / last_close) if last_close else 0.0

        up_flags = (close.diff() > 0).fillna(False).astype(int)
        consec = 0
        for v in reversed(up_flags.tolist()):
            if v == 1:
                consec += 1
            else:
                break

        # 20 日收益作为相对强度原始值
        rel_strength = float(close.iloc[-1] / close.iloc[-min(len(close), self.lookback)] - 1)

        parts = pd.DataFrame(
            [
                {
                    "volume_surge": volume_surge,
                    "turnover": turnover_proxy,
                    "momentum_5d": mom_5 * 100,
                    "momentum_10d": mom_10 * 100,
                    "amplitude": amp * 100,
                    "consecutive_up": consec,
                    "rel_strength": rel_strength * 100,
                }
            ]
        )

        # 在跨标的比较前，这里先给单标的原始值；最终排序阶段 engine 会再做全池归一化。
        # 为避免量纲问题，这里输出“原始因子 + 局部启发式分数”，便于单标的查看。
        components = {
            "volume_surge": round(volume_surge, 3),
            "turnover": round(turnover_proxy, 3),
            "momentum_5d_pct": round(mom_5 * 100, 2),
            "momentum_10d_pct": round(mom_10 * 100, 2),
            "amplitude_pct": round(amp * 100, 2),
            "consecutive_up": consec,
            "rel_strength_20d_pct": round(rel_strength * 100, 2),
        }

        # 启发式分数（0-100），用于单标的显示；全池榜单请以 run_pool 为准
        heur = pd.DataFrame(
            [
                {
                    "volume_surge": minmax_score(pd.Series([1.0, max(1.0, volume_surge), 4.0])).iloc[1]
                    if volume_surge
                    else 50,
                    "turnover": float(np.clip(turnover_proxy * 35, 0, 100)),
                    "momentum_5d": float(np.clip(50 + mom_5 * 500, 0, 100)),
                    "momentum_10d": float(np.clip(50 + mom_10 * 350, 0, 100)),
                    "amplitude": float(np.clip(amp * 800, 0, 100)),
                    "consecutive_up": float(np.clip(consec * 20, 0, 100)),
                    "rel_strength": float(np.clip(50 + rel_strength * 400, 0, 100)),
                }
            ]
        )
        # 修正 volume_surge 局部分位逻辑
        heur.loc[0, "volume_surge"] = float(np.clip((volume_surge - 0.8) / (3.0 - 0.8) * 100, 0, 100))

        score = float(weighted_score(heur, self.weights).iloc[0])
        return {
            "code": code,
            "name": name or code,
            "score": round(score, 2),
            "price": round(last_close, 4),
            "change_5d_pct": round(mom_5 * 100, 2),
            "change_20d_pct": round(rel_strength * 100, 2),
            "volume_ratio": round(volume_surge, 2),
            "components": components,
            "reason": self._reason(components),
        }

    @staticmethod
    def _reason(c: dict[str, Any]) -> str:
        bits = []
        if c.get("volume_surge", 1) >= 2:
            bits.append(f"量比{c['volume_surge']}倍")
        if c.get("momentum_5d_pct", 0) > 3:
            bits.append(f"5日+{c['momentum_5d_pct']}%")
        if c.get("consecutive_up", 0) >= 3:
            bits.append(f"连阳{c['consecutive_up']}日")
        if c.get("rel_strength_20d_pct", 0) > 8:
            bits.append(f"20日强势+{c['rel_strength_20d_pct']}%")
        return "；".join(bits) if bits else "量价与动量综合评分居前"

    def run_pool(
        self, frames: dict[str, pd.DataFrame], names: dict[str, str] | None = None, top_n: int | None = None
    ) -> list[dict[str, Any]]:
        """跨标的截面归一化打分，更符合榜单语义。"""
        names = names or {}
        top_n = top_n or self.default_top_n
        rows = []
        raw_map = {}
        for code, df in frames.items():
            item = self.score_symbol(code, df, name=names.get(code, code))
            if not item:
                continue
            c = item["components"]
            raw_map[code] = item
            rows.append(
                {
                    "code": code,
                    "volume_surge": c["volume_surge"],
                    "turnover": c["turnover"],
                    "momentum_5d": c["momentum_5d_pct"],
                    "momentum_10d": c["momentum_10d_pct"],
                    "amplitude": c["amplitude_pct"],
                    "consecutive_up": c["consecutive_up"],
                    "rel_strength": c["rel_strength_20d_pct"],
                }
            )
        if not rows:
            return []
        cross = pd.DataFrame(rows).set_index("code")
        scores = {}
        for code in cross.index:
            local = cross.loc[code].to_frame().T
            # 对当前票在全池中的分位
            part_scores = {}
            for col in self.weights:
                if col not in cross.columns:
                    part_scores[col] = 50.0
                    continue
                s = minmax_score(cross[col], higher_better=True)
                part_scores[col] = float(s.loc[code])
            scores[code] = float(
                sum(part_scores[k] * float(self.weights.get(k, 0)) for k in part_scores)
                / max(sum(self.weights.values()), 1e-9)
            )

        out = []
        for code, base in raw_map.items():
            item = dict(base)
            item["score"] = round(scores.get(code, base["score"]), 2)
            out.append(item)
        out.sort(key=lambda x: x["score"], reverse=True)
        return out[:top_n]
