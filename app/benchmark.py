from __future__ import annotations

"""基准定义：收益没有对比就没有意义。

选择理由写入代码与页面，避免“拍脑袋定基准”。
"""

from typing import Any

import pandas as pd
import requests

from app.config import load_config
from app.data.crypto import CryptoDataProvider
from app.data.storage import CacheStore

UA = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn",
}
SINA_KLINE = (
    "https://money.finance.sinance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
# 上面域名可能写错，用正确域名
SINA_KLINE = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)

BENCHMARKS: dict[str, dict[str, Any]] = {
    "hs300": {
        "id": "hs300",
        "name": "沪深300",
        "symbol": "sh000300",
        "market": "a_share",
        "reason": (
            "覆盖A股市值最大、流动性最好的300只股票，是机构最常用的主动权益业绩基准；"
            "能代表“随便买大盘”的机会成本，适合衡量短线/长线策略是否创造超额。"
        ),
    },
    "csi500": {
        "id": "csi500",
        "name": "中证500",
        "symbol": "sh000905",
        "market": "a_share",
        "reason": (
            "中盘成长风格，更贴近龙虎榜/题材/中小市值活跃股；"
            "若策略偏中小盘热点，用沪深300可能低估基准，用中证500更公平。"
        ),
    },
    "chinext": {
        "id": "chinext",
        "name": "创业板指",
        "symbol": "sz399006",
        "market": "a_share",
        "reason": "成长/科技弹性大，适合评估偏题材、高波动策略是否跑赢同风格市场。",
    },
    "btc": {
        "id": "btc",
        "name": "BTC 买入持有",
        "symbol": "BTC-USDT",
        "market": "crypto",
        "reason": (
            "加密市场大盘锚。策略若连BTC持有都跑不赢，说明交易没有创造价值。"
        ),
    },
    "blend_ashare_btc": {
        "id": "blend_ashare_btc",
        "name": "混合基准 70%沪深300 + 30%BTC",
        "symbol": "blend",
        "market": "all",
        "reason": (
            "当同时交易A股与加密时，单一指数会失真；按常见跨市场配置加权作为机会成本参考。"
            "权重可在 config.yaml -> benchmark.blend_weights 修改。"
        ),
    },
}

# 合格线（内置，页面展示）
PASS_RULES = {
    "min_excess_return_pct": 5.0,
    "rule_text": (
        "合格条件（同时满足）："
        "①策略累计收益显著跑赢基准（超出基准 ≥ 5 个百分点）；"
        "②策略最大回撤 < 基准最大回撤。"
        "仅收益高但回撤更大 → 不合格；仅回撤小但跑输 → 不合格。"
    ),
}


class BenchmarkProvider:
    def __init__(self) -> None:
        self.cache = CacheStore()
        self.crypto = CryptoDataProvider()
        self.cfg = (load_config().get("benchmark") or {})
        self.default_id = str(self.cfg.get("default") or "hs300")

    @staticmethod
    def list_meta() -> list[dict[str, Any]]:
        return [
            {**v, "pass_rules": PASS_RULES} for v in BENCHMARKS.values()
        ]

    def get_meta(self, bench_id: str) -> dict[str, Any]:
        return dict(BENCHMARKS.get(bench_id) or BENCHMARKS["hs300"])

    def _index_hist(self, symbol: str, days: int = 260) -> pd.DataFrame | None:
        cache_key = f"bench_index_{symbol}_{days}"
        cached = self.cache.get(cache_key, max_age_seconds=12 * 3600)
        if cached:
            df = pd.DataFrame(cached)
            if not df.empty:
                return df
        try:
            resp = requests.get(
                SINA_KLINE,
                params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": min(days, 320)},
                headers=UA,
                timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        if not isinstance(data, list) or not data:
            return None
        rows = [
            {
                "date": item.get("day"),
                "open": float(item.get("open")),
                "high": float(item.get("high")),
                "low": float(item.get("low")),
                "close": float(item.get("close")),
                "volume": float(item.get("volume") or 0),
            }
            for item in data
        ]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        records = df.assign(date=df["date"].dt.strftime("%Y-%m-%d")).to_dict(orient="records")
        self.cache.set(cache_key, records)
        return df

    def get_series(self, bench_id: str, days: int = 260) -> pd.DataFrame | None:
        meta = self.get_meta(bench_id)
        if bench_id == "btc" or meta.get("market") == "crypto":
            return self.crypto.get_history("BTC-USDT", days=days)
        if bench_id == "blend_ashare_btc" or meta.get("symbol") == "blend":
            w = self.cfg.get("blend_weights") or {"hs300": 0.7, "btc": 0.3}
            a = self.get_series("hs300", days=days)
            b = self.get_series("btc", days=days)
            if a is None and b is None:
                return None
            if a is None:
                return b
            if b is None:
                return a
            merged = pd.merge(
                a[["date", "close"]].rename(columns={"close": "a"}),
                b[["date", "close"]].rename(columns={"close": "b"}),
                on="date",
                how="inner",
            )
            if merged.empty:
                return a
            # 归一化加权
            a_n = merged["a"] / float(merged["a"].iloc[0])
            b_n = merged["b"] / float(merged["b"].iloc[0])
            blended = a_n * float(w.get("hs300", 0.7)) + b_n * float(w.get("btc", 0.3))
            out = pd.DataFrame(
                {
                    "date": merged["date"],
                    "open": blended,
                    "high": blended,
                    "low": blended,
                    "close": blended,
                    "volume": 0.0,
                }
            )
            return out
        return self._index_hist(str(meta.get("symbol")), days=days)

    def equity_from_series(self, df: pd.DataFrame | None) -> list[dict[str, Any]]:
        if df is None or df.empty:
            return []
        close = df["close"].astype(float)
        base = float(close.iloc[0]) or 1.0
        out = []
        for i, row in df.iterrows():
            out.append(
                {
                    "date": str(pd.Timestamp(row["date"]).date()),
                    "close": float(row["close"]),
                    "equity": float(row["close"]) / base,
                }
            )
        return out
