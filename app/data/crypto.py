from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pandas as pd
import requests

from app.config import load_config
from app.data.storage import CacheStore

logger = logging.getLogger(__name__)

OHLCV_COLS = ["date", "open", "high", "low", "close", "volume"]

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}

# 币安公开行情（推荐，无需 API Key）
BINANCE_VISION = "https://data-api.binance.vision/api/v3"
BINANCE_MAIN = "https://api.binance.com/api/v3"
OKX_CANDLES = "https://www.okx.com/api/v5/market/candles"


class CryptoDataProvider:
    """加密货币行情：默认推荐币安 Binance API。

    - 主通道: data-api.binance.vision（币安官方公开行情镜像，多数网络可直连）
    - 回退:   api.binance.com
    - 最后回退: OKX
    可选在 config.yaml 填入 binance_api_key / binance_api_secret 使用私有接口，
    当前仅用公开 K 线/价格，无需密钥。
    """

    def __init__(self, cache: CacheStore | None = None) -> None:
        self.cache = cache or CacheStore()
        self.cfg = load_config()["universe"]["crypto"]
        market_cfg = load_config()["market"]
        ex = str(
            self.cfg.get("exchange")
            or market_cfg.get("crypto_exchange")
            or "binance"
        ).lower()
        if ex in {"binance_vision", "binance", "币安"}:
            self.preferred = "binance"
        else:
            self.preferred = ex
        self.session = requests.Session()
        self.session.headers.update(UA)

    @property
    def symbols(self) -> list[str]:
        symbols = self.cfg.get("symbols") or ["BTC-USDT", "ETH-USDT"]
        return [str(s).upper() for s in symbols]

    @property
    def exchange_label(self) -> str:
        return "Binance API（推荐）" if self.preferred == "binance" else self.preferred

    @staticmethod
    def _to_binance_symbol(symbol: str) -> str:
        return symbol.replace("-", "").replace("/", "").upper()

    @staticmethod
    def _to_okx_symbol(symbol: str) -> str:
        s = symbol.upper().replace("/", "-")
        if "-" not in s and s.endswith("USDT"):
            s = s[:-4] + "-USDT"
        return s

    @staticmethod
    def _normalize_binance(rows: list[Any], days: int) -> pd.DataFrame | None:
        if not rows:
            return None
        records = []
        for r in rows:
            try:
                records.append(
                    {
                        "date": pd.to_datetime(int(r[0]), unit="ms"),
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    }
                )
            except Exception:
                continue
        if len(records) < 20:
            return None
        df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
        return df.tail(days)

    @staticmethod
    def _normalize_okx(rows: list[Any], days: int) -> pd.DataFrame | None:
        if not rows:
            return None
        records = []
        for r in rows:
            try:
                records.append(
                    {
                        "date": pd.to_datetime(int(r[0]), unit="ms"),
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    }
                )
            except Exception:
                continue
        if len(records) < 20:
            return None
        df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
        return df.tail(days)

    @staticmethod
    def _synthetic(symbol: str, days: int) -> pd.DataFrame | None:
        seed = abs(hash(symbol)) % (2**32)
        rng = np.random.default_rng(seed)
        rets = rng.normal(0.0005, 0.03, size=days)
        price = 30000 if symbol.startswith("BTC") else 2000
        closes = price * np.exp(np.cumsum(rets))
        idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=days, freq="D")
        return pd.DataFrame(
            {
                "date": idx,
                "open": closes * (1 + rng.normal(0, 0.005, days)),
                "high": closes * (1 + np.abs(rng.normal(0, 0.01, days))),
                "low": closes * (1 - np.abs(rng.normal(0, 0.01, days))),
                "close": closes,
                "volume": rng.lognormal(8, 0.5, days),
            }
        )

    def _fetch_binance_family(self, symbol: str, days: int) -> pd.DataFrame | None:
        inst = self._to_binance_symbol(symbol)
        params = {"symbol": inst, "interval": "1d", "limit": min(days, 1000)}
        for base in (BINANCE_VISION, BINANCE_MAIN):
            try:
                resp = self.session.get(f"{base}/klines", params=params, timeout=12)
                resp.raise_for_status()
                rows = resp.json()
                if not isinstance(rows, list):
                    continue
                df = self._normalize_binance(rows, days)
                if df is not None:
                    df.attrs["source"] = "binance"
                    return df
            except Exception as e:
                logger.warning("crypto %s via %s failed: %s", symbol, base, e)
                time.sleep(0.15)
        return None

    def _fetch_okx(self, symbol: str, days: int) -> pd.DataFrame | None:
        inst = self._to_okx_symbol(symbol)
        resp = self.session.get(
            OKX_CANDLES,
            params={"instId": inst, "bar": "1D", "limit": str(min(days, 300))},
            timeout=12,
        )
        resp.raise_for_status()
        rows = (resp.json() or {}).get("data") or []
        df = self._normalize_okx(rows, days)
        if df is not None:
            df.attrs["source"] = "okx"
        return df

    def get_history(self, symbol: str, days: int = 250) -> pd.DataFrame | None:
        cache_key = f"crypto_hist_v2_{symbol}_{days}"
        cached = self.cache.get(cache_key, max_age_seconds=2 * 3600)
        if cached:
            df = pd.DataFrame(cached)
            if not df.empty:
                return df

        df = None
        # 推荐币安优先
        if self.preferred == "binance":
            df = self._fetch_binance_family(symbol, days)
            if df is None:
                try:
                    df = self._fetch_okx(symbol, days)
                except Exception as e:
                    logger.warning("okx fallback %s failed: %s", symbol, e)
        else:
            try:
                df = self._fetch_okx(symbol, days)
            except Exception as e:
                logger.warning("okx %s failed: %s", symbol, e)
            if df is None:
                df = self._fetch_binance_family(symbol, days)

        source = "binance"
        if df is None or len(df) < 20:
            df = self._synthetic(symbol, days)
            source = "synthetic_demo"
            logger.warning("crypto %s using synthetic demo series", symbol)
        else:
            source = str(df.attrs.get("source") or self.preferred)

        if df is None:
            return None
        records = df.assign(date=pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")).to_dict(
            orient="records"
        )
        self.cache.set(cache_key, records)
        out = pd.DataFrame(records)
        out["date"] = pd.to_datetime(out["date"])
        out.attrs["source"] = source
        return out

    def get_price(self, symbol: str) -> float | None:
        """币安最新价（公开 ticker），失败则用日K收盘。"""
        inst = self._to_binance_symbol(symbol)
        for base in (BINANCE_VISION, BINANCE_MAIN):
            try:
                resp = self.session.get(
                    f"{base}/ticker/price", params={"symbol": inst}, timeout=8
                )
                resp.raise_for_status()
                price = float((resp.json() or {}).get("price") or 0)
                if price > 0:
                    return price
            except Exception as e:
                logger.debug("binance ticker %s failed: %s", symbol, e)
        df = self.get_history(symbol, days=5)
        if df is not None and len(df):
            return float(df["close"].iloc[-1])
        return None

    def load_universe_frames(self, days: int = 250) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        for sym in self.symbols:
            df = self.get_history(sym, days=days)
            if df is not None and len(df) >= 20:
                frames[sym] = df
        return frames
