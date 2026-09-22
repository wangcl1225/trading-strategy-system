from __future__ import annotations

from typing import Any

import pandas as pd

from app.config import load_config
from app.data.a_share import AShareDataProvider
from app.data.crypto import CryptoDataProvider
from app.data.market_extra import MarketExtraFactors
from app.strategies.bottom_fishing import BottomFishingStrategy
from app.strategies.extra_factors import build_lhb_highlight, enrich_with_extra_factors
from app.strategies.long_term_layout import LongTermLayoutStrategy
from app.strategies.short_term_hot import ShortTermHotStrategy

STRATEGY_MAP = {
    "short_term_hot": ShortTermHotStrategy,
    "long_term_layout": LongTermLayoutStrategy,
    "bottom_fishing": BottomFishingStrategy,
}

STRATEGY_META = {
    "short_term_hot": {
        "label": "短线热门",
        "desc": "量价异动 + 龙虎榜净买 + 主力资金流综合打分",
    },
    "long_term_layout": {"label": "长线布局", "desc": "趋势结构、波动质量与估值分位，偏中期配置"},
    "bottom_fishing": {"label": "抄底潜伏", "desc": "超跌、缩量、近支撑与企稳迹象的左侧信号"},
    "dragon_tiger": {"label": "龙虎榜", "desc": "近几日龙虎榜净买入靠前的活跃席位标的"},
}


class StrategyEngine:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.ashare = AShareDataProvider()
        self.crypto = CryptoDataProvider()
        self.extras = MarketExtraFactors()
        self.strategies = {k: cls() for k, cls in STRATEGY_MAP.items()}

    def resolve_market(self, market: str | None) -> str:
        m = (market or self.cfg["market"].get("default") or "a_share").lower()
        if m not in {"a_share", "crypto", "all"}:
            m = "a_share"
        return m

    def load_frames(self, market: str | None = None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        market = self.resolve_market(market)
        frames: dict[str, pd.DataFrame] = {}
        names: dict[str, str] = {}

        need_days = {
            "short_term_hot": self.cfg["strategies"]["short_term_hot"]["lookback_days"] * 2,
            "long_term_layout": self.cfg["strategies"]["long_term_layout"]["lookback_days"],
            "bottom_fishing": self.cfg["strategies"]["bottom_fishing"]["lookback_days"],
        }
        days = max(max(need_days.values()), 260)

        if market in {"a_share", "all"}:
            stocks = self.ashare.get_stock_list()
            name_map = {str(r["code"]): str(r["name"]) for _, r in stocks.iterrows()}
            limit = int(self.cfg["universe"]["a_share"].get("max_count", 300))
            for code, name in list(name_map.items())[:limit]:
                df = self.ashare.get_history(code, days=min(days, 320))
                if df is not None and len(df) >= 20:
                    key = f"ASHARE:{code}"
                    frames[key] = df
                    names[key] = name

        if market in {"crypto", "all"}:
            crypto_frames = self.crypto.load_universe_frames(days=min(days, 250))
            for sym, df in crypto_frames.items():
                key = f"CRYPTO:{sym}"
                frames[key] = df
                names[key] = sym

        return frames, names

    @staticmethod
    def _prefix_meta(code_key: str, name: str) -> dict[str, str]:
        if code_key.startswith("ASHARE:"):
            return {
                "code": code_key.split(":", 1)[1],
                "name": name,
                "market": "a_share",
                "symbol_key": code_key,
            }
        return {
            "code": code_key.split(":", 1)[1],
            "name": name,
            "market": "crypto",
            "symbol_key": code_key,
        }

    def _attach_meta(self, items: list[dict[str, Any]], strategy_key: str) -> list[dict[str, Any]]:
        out = []
        for item in items:
            meta = self._prefix_meta(item["code"], item.get("name") or item["code"])
            merged = {**meta, **item}
            merged["code"] = meta["code"]
            merged["name"] = meta["name"]
            merged["strategy"] = strategy_key
            merged["strategy_label"] = STRATEGY_META[strategy_key]["label"]
            out.append(merged)
        return out

    def _factor_weights(self) -> tuple[float, float, float]:
        fac = (self.cfg.get("factors") or {}).get("short_term_blend") or {}
        return (
            float(fac.get("base", 0.70)),
            float(fac.get("lhb", 0.15)),
            float(fac.get("fund_flow", 0.15)),
        )

    def run_strategy(
        self,
        strategy_key: str,
        market: str | None = None,
        top_n: int | None = None,
        frames: dict[str, pd.DataFrame] | None = None,
        names: dict[str, str] | None = None,
        use_extra: bool = True,
    ) -> list[dict[str, Any]]:
        if strategy_key not in self.strategies:
            raise ValueError(f"unknown strategy: {strategy_key}")
        if frames is None or names is None:
            frames, names = self.load_frames(market)
        strategy = self.strategies[strategy_key]
        top_n = top_n or strategy.default_top_n

        candidate_n = top_n
        if strategy_key == "short_term_hot" and use_extra:
            candidate_n = max(top_n * 2, 30)

        raw = strategy.run_pool(frames, names=names, top_n=candidate_n)
        items = self._attach_meta(raw, strategy_key)

        if strategy_key == "short_term_hot" and use_extra:
            try:
                lhb_map = self.extras.lhb_map()
                w_base, w_lhb, w_flow = self._factor_weights()
                items = enrich_with_extra_factors(
                    items,
                    self.extras,
                    lhb_map,
                    weight_lhb=w_lhb,
                    weight_flow=w_flow,
                    weight_base=w_base,
                )
                items = self._attach_meta(items, strategy_key)
            except Exception:
                # 扩展因子失败时回退纯量价得分
                pass
        return items[:top_n]

    def lhb_board(self, top_n: int = 20) -> list[dict[str, Any]]:
        _, names = {}, {}
        try:
            stocks = self.ashare.get_stock_list()
            names = {str(r["code"]): str(r["name"]) for _, r in stocks.iterrows()}
        except Exception:
            names = {}
        return build_lhb_highlight(self.extras, names=names, top_n=top_n)

    def run_all(
        self, market: str | None = None, top_n: int | None = None
    ) -> dict[str, Any]:
        market = self.resolve_market(market)
        frames, names = self.load_frames(market)
        result = {
            "market": market,
            "universe_size": len(frames),
            "strategies": {},
            "meta": STRATEGY_META,
        }
        for key in STRATEGY_MAP:
            items = self.run_strategy(
                key, market=market, top_n=top_n, frames=frames, names=names
            )
            result["strategies"][key] = {
                **STRATEGY_META[key],
                "items": items,
            }

        if market in {"a_share", "all"}:
            try:
                result["strategies"]["dragon_tiger"] = {
                    **STRATEGY_META["dragon_tiger"],
                    "items": self.lhb_board(top_n=top_n or 20),
                }
            except Exception:
                result["strategies"]["dragon_tiger"] = {
                    **STRATEGY_META["dragon_tiger"],
                    "items": [],
                }
        return result
