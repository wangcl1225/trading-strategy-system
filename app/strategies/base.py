from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import pandas as pd


def safe_series(values: Any) -> pd.Series:
    s = pd.Series(values)
    return pd.to_numeric(s, errors="coerce")


def minmax_score(series: pd.Series, higher_better: bool = True) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    lo, hi = s.min(), s.max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return pd.Series(np.full(len(s), 50.0), index=s.index)
    norm = (s - lo) / (hi - lo) * 100.0
    return norm if higher_better else 100.0 - norm


def weighted_score(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    total = 0.0
    acc = pd.Series(np.zeros(len(frame)), index=frame.index, dtype=float)
    for col, w in weights.items():
        if col not in frame.columns:
            continue
        acc = acc + frame[col].fillna(0) * float(w)
        total += float(w)
    if total <= 0:
        return pd.Series(np.zeros(len(frame)), index=frame.index)
    return acc / total


def rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = 100 - (100 / (1 + rs))
    last = val.iloc[-1]
    return float(last) if pd.notna(last) else 50.0


class BaseStrategy(ABC):
    name: str = "base"
    label: str = "基础策略"

    @abstractmethod
    def score_symbol(self, code: str, df: pd.DataFrame, name: str = "") -> dict[str, Any]:
        """对单个标的打分，返回含 score 与 components 的 dict。"""

    def run(
        self, frames: dict[str, pd.DataFrame], names: dict[str, str] | None = None, top_n: int = 20
    ) -> list[dict[str, Any]]:
        names = names or {}
        results: list[dict[str, Any]] = []
        for code, df in frames.items():
            if df is None or len(df) < 15:
                continue
            try:
                item = self.score_symbol(code, df, name=names.get(code, code))
                if item and item.get("score") is not None:
                    results.append(item)
            except Exception:
                continue
        results.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
        return results[: max(1, int(top_n))]
