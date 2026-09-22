from __future__ import annotations

"""策略注册表：内置策略 + 聚宽 JoinQuant 策略广场热门策略（本地实现）。

说明：
- 聚宽策略广场需登录/授权才能拉取官方列表；此处内置其社区最常见、可复现的热门策略模板，
  标注来源为「JoinQuant策略广场风格·本地实现」，便于切换对比。
- 所有策略共用同一套信号输出协议：score + 买卖目标，可被回测/模拟盘调用。
"""

from typing import Any, Callable

import numpy as np
import pandas as pd

from app.strategies.base import minmax_score
from app.strategies.bottom_fishing import BottomFishingStrategy
from app.strategies.engine import STRATEGY_META as BUILTIN_META
from app.strategies.long_term_layout import LongTermLayoutStrategy
from app.strategies.short_term_hot import ShortTermHotStrategy

StrategyFn = Callable[[dict[str, pd.DataFrame], dict[str, str], int], list[dict[str, Any]]]


def _item(code: str, name: str, market: str, score: float, price: float, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "market": market,
        "score": round(float(score), 2),
        "price": round(float(price), 4),
        "reason": reason,
        "components": extra,
        **extra,
    }


def _market_of(key: str) -> tuple[str, str]:
    if key.startswith("ASHARE:"):
        return key.split(":", 1)[1], "a_share"
    return key.split(":", 1)[1], "crypto"


# ---------------- JoinQuant 风格热门策略（本地实现） ----------------

def jq_dual_momentum(frames: dict[str, pd.DataFrame], names: dict[str, str], top_n: int = 10) -> list[dict[str, Any]]:
    """双动量：绝对动量>0 且相对动量居前。JQ广场常见「动量轮动」变体。"""
    rows = []
    for key, df in frames.items():
        if df is None or len(df) < 30:
            continue
        code, market = _market_of(key)
        close = df["close"].astype(float)
        abs_mom = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 22 else 0.0
        rel_mom = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 7 else 0.0
        if abs_mom <= 0:
            continue
        score = minmax_score(pd.Series([abs_mom, abs_mom + 1, rel_mom])).iloc[0]  # placeholder
        score = 50 + abs_mom * 200 + rel_mom * 100
        rows.append(
            _item(
                code,
                names.get(key, code),
                market,
                float(np.clip(score, 0, 100)),
                float(close.iloc[-1]),
                f"20日绝对动量{abs_mom*100:.1f}%，5日相对动量{rel_mom*100:.1f}%",
                abs_mom_20d=round(abs_mom * 100, 2),
                rel_mom_5d=round(rel_mom * 100, 2),
            )
        )
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:top_n]


def jq_small_momentum_rotation(frames: dict[str, pd.DataFrame], names: dict[str, str], top_n: int = 10) -> list[dict[str, Any]]:
    """小市值+动量轮动（JQ「小市值轮动」思路）：低价/小票代理 + 动量。"""
    rows = []
    for key, df in frames.items():
        code, market = _market_of(key)
        if market != "a_share" or df is None or len(df) < 25:
            continue
        close = df["close"].astype(float)
        price = float(close.iloc[-1])
        mom = float(close.iloc[-1] / close.iloc[-11] - 1) if len(close) >= 12 else 0.0
        # 低价作为“小市值”代理（无财务市值数据时）
        size_proxy = max(0.0, 1 - price / 80.0)
        if mom < 0:
            continue
        score = 40 * size_proxy + 60 * min(mom * 5, 1.0)
        rows.append(
            _item(
                code,
                names.get(key, code),
                market,
                score * 1.2,
                price,
                f"低价代理{size_proxy:.2f} + 10日动量{mom*100:.1f}%",
                size_proxy=round(size_proxy, 3),
                mom_10d=round(mom * 100, 2),
            )
        )
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:top_n]


def jq_low_vol_quality(frames: dict[str, pd.DataFrame], names: dict[str, str], top_n: int = 10) -> list[dict[str, Any]]:
    """低波动+质量（JQ低波动/红利类）：波动低、上涨日占比高、趋势不空头。"""
    rows = []
    for key, df in frames.items():
        code, market = _market_of(key)
        if df is None or len(df) < 40:
            continue
        close = df["close"].astype(float)
        rets = close.pct_change().dropna()
        if len(rets) < 30:
            continue
        vol = float(rets.tail(40).std() * np.sqrt(252) * 100)
        win = float((rets.tail(40) > 0).mean())
        ma60 = float(close.rolling(60, min_periods=20).mean().iloc[-1])
        trend_ok = 1 if float(close.iloc[-1]) >= ma60 * 0.95 else 0
        # 低波动更好（约15-30%）
        vol_score = max(0.0, 100 - abs(vol - 22) * 2.2)
        score = vol_score * 0.45 + win * 100 * 0.35 + trend_ok * 20
        rows.append(
            _item(
                code,
                names.get(key, code),
                market,
                score,
                float(close.iloc[-1]),
                f"年化波动{vol:.1f}%，上涨日占比{win*100:.0f}%",
                annual_vol=round(vol, 2),
                win_rate=round(win, 3),
            )
        )
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:top_n]


def jq_turtle_breakout(frames: dict[str, pd.DataFrame], names: dict[str, str], top_n: int = 10) -> list[dict[str, Any]]:
    """海龟突破（唐奇安通道）：价格接近/突破20日高。"""
    rows = []
    for key, df in frames.items():
        code, market = _market_of(key)
        if df is None or len(df) < 25:
            continue
        close = df["close"].astype(float)
        high_n = float(close.tail(21).max())
        low_n = float(close.tail(21).min())
        price = float(close.iloc[-1])
        if high_n <= low_n:
            continue
        pos = (price - low_n) / (high_n - low_n)
        if pos < 0.75:
            continue
        vol = float(df["volume"].astype(float).tail(5).mean() or 1)
        vol_ma = float(df["volume"].astype(float).tail(20).mean() or 1)
        vol_ratio = vol / vol_ma if vol_ma else 1
        score = pos * 70 + min(vol_ratio, 3) * 10
        rows.append(
            _item(
                code,
                names.get(key, code),
                market,
                score,
                price,
                f"唐奇安位置{pos*100:.0f}%（20日），量比{vol_ratio:.2f}",
                channel_pos=round(pos, 3),
                vol_ratio=round(vol_ratio, 2),
            )
        )
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:top_n]


def jq_reversal_oversold(frames: dict[str, pd.DataFrame], names: dict[str, str], top_n: int = 10) -> list[dict[str, Any]]:
    """超跌反转（JQ反转类）：短期跌幅大但开始止跌。"""
    rows = []
    for key, df in frames.items():
        code, market = _market_of(key)
        if df is None or len(df) < 15:
            continue
        close = df["close"].astype(float)
        mom5 = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0
        mom10 = float(close.iloc[-1] / close.iloc[-11] - 1) if len(close) >= 11 else 0
        rets = close.pct_change().dropna()
        rsi = 50.0
        if len(rets) >= 15:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
            loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
            rs = gain / loss if loss else np.nan
            rsi = float(100 - 100 / (1 + rs)) if pd.notna(rs) else 50.0
        if mom10 > -0.05 and rsi > 40:
            continue
        rebound = float(close.iloc[-1] / close.iloc[-2] - 1) if len(close) >= 3 else 0
        score = max(0, (-mom10) * 200) + max(0, (35 - rsi)) * 1.2 + max(0, rebound) * 100
        rows.append(
            _item(
                code,
                names.get(key, code),
                market,
                float(np.clip(score, 0, 100)),
                float(close.iloc[-1]),
                f"10日{mom10*100:.1f}%，RSI={rsi:.0f}，近1日{rebound*100:.1f}%",
                mom_10d=round(mom10 * 100, 2),
                rsi=round(rsi, 1),
            )
        )
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:top_n]


def jq_grid_like_mean_rev(frames: dict[str, pd.DataFrame], names: dict[str, str], top_n: int = 10) -> list[dict[str, Any]]:
    """网格/均值回归代理：价格处于布林下轨附近且波动适中。"""
    rows = []
    for key, df in frames.items():
        code, market = _market_of(key)
        if df is None or len(df) < 22:
            continue
        close = df["close"].astype(float)
        ma = float(close.rolling(20).mean().iloc[-1])
        sd = float(close.rolling(20).std().iloc[-1])
        price = float(close.iloc[-1])
        if not sd or sd <= 0:
            continue
        z = (price - ma) / sd
        if z > -0.8:
            continue
        score = float(np.clip((-z) * 35 + 40, 0, 100))
        rows.append(
            _item(
                code,
                names.get(key, code),
                market,
                score,
                price,
                f"布林z={z:.2f}（接近下轨），适合网格/均值回归",
                boll_z=round(z, 2),
            )
        )
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:top_n]


def _run_builtin(cls, frames, names, top_n):
    strat = cls()
    raw = strat.run_pool(frames, names=names, top_n=top_n)
    out = []
    for it in raw:
        code = it["code"]
        market = "crypto" if "-USDT" in str(code) or str(code).startswith("CRYPTO") else "a_share"
        if str(code).startswith("ASHARE:"):
            code = str(code).split(":", 1)[1]
            market = "a_share"
        out.append(
            {
                **it,
                "code": code,
                "market": market,
                "name": it.get("name") or code,
            }
        )
    return out


STRATEGY_REGISTRY: dict[str, dict[str, Any]] = {
    "short_term_hot": {
        "id": "short_term_hot",
        "name": "短线热门（内置）",
        "source": "builtin",
        "source_label": "内置·量价+龙虎榜+资金流",
        "desc": "量比/动量/连阳 + 龙虎榜净买 + 主力资金流",
        "risk_profile": "高波动短线",
        "default_holding_days": 3,
        "fn": lambda f, n, t: _run_builtin(ShortTermHotStrategy, f, n, t),
        "builtin_key": "short_term_hot",
    },
    "long_term_layout": {
        "id": "long_term_layout",
        "name": "长线布局（内置）",
        "source": "builtin",
        "source_label": "内置·趋势与估值分位",
        "desc": "均线结构、波动质量、价格分位",
        "risk_profile": "中低频",
        "default_holding_days": 42,
        "fn": lambda f, n, t: _run_builtin(LongTermLayoutStrategy, f, n, t),
        "builtin_key": "long_term_layout",
    },
    "bottom_fishing": {
        "id": "bottom_fishing",
        "name": "抄底潜伏（内置）",
        "source": "builtin",
        "source_label": "内置·超跌均值回归",
        "desc": "回撤/RSI/缩量/支撑/企稳",
        "risk_profile": "左侧高风险",
        "default_holding_days": 60,
        "fn": lambda f, n, t: _run_builtin(BottomFishingStrategy, f, n, t),
        "builtin_key": "bottom_fishing",
    },
    "jq_dual_momentum": {
        "id": "jq_dual_momentum",
        "name": "双动量轮动",
        "source": "joinquant",
        "source_label": "JoinQuant策略广场风格·本地实现",
        "desc": "绝对动量过滤 + 相对动量排序（聚宽动量轮动类热门模板）",
        "risk_profile": "趋势中频",
        "default_holding_days": 5,
        "fn": jq_dual_momentum,
    },
    "jq_small_mom_rotation": {
        "id": "jq_small_mom_rotation",
        "name": "小市值动量轮动",
        "source": "joinquant",
        "source_label": "JoinQuant策略广场风格·本地实现",
        "desc": "低价小票代理 + 动量（聚宽小市值轮动类）",
        "risk_profile": "中小盘高波动",
        "default_holding_days": 5,
        "fn": jq_small_momentum_rotation,
    },
    "jq_low_vol_quality": {
        "id": "jq_low_vol_quality",
        "name": "低波动质量",
        "source": "joinquant",
        "source_label": "JoinQuant策略广场风格·本地实现",
        "desc": "低波动 + 高胜率 + 趋势不空头",
        "risk_profile": "稳健偏多",
        "default_holding_days": 20,
        "fn": jq_low_vol_quality,
    },
    "jq_turtle_breakout": {
        "id": "jq_turtle_breakout",
        "name": "海龟突破",
        "source": "joinquant",
        "source_label": "JoinQuant策略广场风格·本地实现",
        "desc": "唐奇安20日通道突破 + 量能确认",
        "risk_profile": "趋势突破",
        "default_holding_days": 10,
        "fn": jq_turtle_breakout,
    },
    "jq_reversal_oversold": {
        "id": "jq_reversal_oversold",
        "name": "超跌反转",
        "source": "joinquant",
        "source_label": "JoinQuant策略广场风格·本地实现",
        "desc": "短期超跌 + RSI偏低 + 出现止跌",
        "risk_profile": "反转短线",
        "default_holding_days": 5,
        "fn": jq_reversal_oversold,
    },
    "jq_grid_mean_rev": {
        "id": "jq_grid_mean_rev",
        "name": "网格/均值回归",
        "source": "joinquant",
        "source_label": "JoinQuant策略广场风格·本地实现",
        "desc": "布林下轨附近的均值回归（网格类思路的选股代理）",
        "risk_profile": "震荡市",
        "default_holding_days": 5,
        "fn": jq_grid_like_mean_rev,
    },
}


def list_strategies() -> list[dict[str, Any]]:
    out = []
    for sid, meta in STRATEGY_REGISTRY.items():
        out.append(
            {
                "id": sid,
                "name": meta["name"],
                "source": meta["source"],
                "source_label": meta["source_label"],
                "desc": meta["desc"],
                "risk_profile": meta["risk_profile"],
                "default_holding_days": meta["default_holding_days"],
            }
        )
    return out


def run_strategy_by_id(
    strategy_id: str,
    frames: dict[str, pd.DataFrame],
    names: dict[str, str],
    top_n: int = 10,
) -> list[dict[str, Any]]:
    meta = STRATEGY_REGISTRY.get(strategy_id) or STRATEGY_REGISTRY["short_term_hot"]
    items = meta["fn"](frames, names, top_n)
    for it in items:
        it["strategy"] = strategy_id
        it["strategy_name"] = meta["name"]
        it["strategy_source"] = meta["source_label"]
    return items
