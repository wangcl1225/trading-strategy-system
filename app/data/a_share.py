from __future__ import annotations

import logging
import time
from typing import Any

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

SINA_HQ_NODE = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
SINA_KLINE = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
EM_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def code_to_sina_symbol(code: str) -> str:
    c = str(code).zfill(6)
    if c[0] in {"6", "9", "5"}:
        return f"sh{c}"
    if c[0] in {"0", "2", "3"}:
        return f"sz{c}"
    if c[0] in {"4", "8"}:
        return f"bj{c}"
    return f"sh{c}"


def code_to_em_secid(code: str) -> str:
    c = str(code).zfill(6)
    if c[0] in {"6", "9", "5"}:
        return f"1.{c}"
    return f"0.{c}"


class AShareDataProvider:
    """A股数据层：列表优先新浪成交额榜，K线优先新浪，回退腾讯/东财。

    东方财富接口在部分网络下不稳定，故不作为主通道。
    """

    def __init__(self, cache: CacheStore | None = None) -> None:
        self.cache = cache or CacheStore()
        self.cfg = load_config()["universe"]["a_share"]
        self.session = requests.Session()
        self.session.headers.update(UA)

    def _get_json(self, url: str, params: dict[str, Any] | None = None, timeout: int = 12) -> Any:
        resp = self.session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def get_stock_list(self) -> pd.DataFrame:
        cache_key = "ashare_stock_list_v3"
        cached = self.cache.get(cache_key, max_age_seconds=6 * 3600)
        if cached:
            return pd.DataFrame(cached)

        max_count = int(self.cfg.get("max_count", 300))
        max_pages = int(self.cfg.get("list_pages", 8))
        rows: list[dict[str, Any]] = []

        # 按成交额排序取活跃股：短线/资金流/龙虎榜都更关注流动性，
        # 不是从5000家里随机抽样；冷门票即使便宜也难以成交与跟踪。
        page = 1
        page_size = 100
        while len(rows) < max_count and page <= max_pages:
            try:
                data = self._get_json(
                    SINA_HQ_NODE,
                    params={
                        "page": page,
                        "num": page_size,
                        "sort": "amount",
                        "asc": 0,
                        "node": "hs_a",
                        "symbol": "",
                        "_s_r_a": "page",
                    },
                )
            except Exception as e:
                logger.warning("sina hq node page %s failed: %s", page, e)
                break
            if not data:
                break
            for item in data:
                code = str(item.get("code", "")).zfill(6)
                name = str(item.get("name", code))
                if not code or code == "000000":
                    continue
                if self.cfg.get("exclude_st", True) and ("ST" in name or "退" in name):
                    continue
                rows.append({"code": code, "name": name})
            page += 1
            time.sleep(0.15)

        # 若新浪失败，用流动性较好的常见样本保证系统可演示
        if not rows:
            fallback = [
                {"code": "600519", "name": "贵州茅台"},
                {"code": "000858", "name": "五粮液"},
                {"code": "601318", "name": "中国平安"},
                {"code": "600036", "name": "招商银行"},
                {"code": "000001", "name": "平安银行"},
                {"code": "600900", "name": "长江电力"},
                {"code": "601899", "name": "紫金矿业"},
                {"code": "300750", "name": "宁德时代"},
                {"code": "002594", "name": "比亚迪"},
                {"code": "600276", "name": "恒瑞医药"},
                {"code": "601012", "name": "隆基绿能"},
                {"code": "000002", "name": "万  科Ａ"},
                {"code": "601166", "name": "兴业银行"},
                {"code": "600030", "name": "中信证券"},
                {"code": "002415", "name": "海康威视"},
                {"code": "600887", "name": "伊利股份"},
                {"code": "601888", "name": "中国中免"},
                {"code": "300059", "name": "东方财富"},
                {"code": "002230", "name": "科大讯飞"},
                {"code": "603288", "name": "海天味业"},
            ]
            rows = fallback

        custom = self.cfg.get("custom_codes") or []
        if self.cfg.get("source") == "custom" and custom:
            custom_set = {str(c).zfill(6) for c in custom}
            rows = [r for r in rows if r["code"] in custom_set] or [
                {"code": str(c).zfill(6), "name": str(c).zfill(6)} for c in custom
            ]

        # 去重并截断
        seen = set()
        uniq = []
        for r in rows:
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            uniq.append(r)
        uniq = uniq[:max_count]
        self.cache.set(cache_key, uniq)
        return pd.DataFrame(uniq)

    @staticmethod
    def _normalize_df(records: list[dict[str, Any]]) -> pd.DataFrame | None:
        if not records:
            return None
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
        return df if len(df) >= 20 else None

    def _hist_sina(self, code: str, days: int) -> pd.DataFrame | None:
        symbol = code_to_sina_symbol(code)
        data = self._get_json(
            SINA_KLINE,
            params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": min(days, 260)},
        )
        if not isinstance(data, list):
            return None
        records = [
            {
                "date": item.get("day"),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": item.get("volume"),
            }
            for item in data
        ]
        return self._normalize_df(records)

    def _hist_tencent(self, code: str, days: int) -> pd.DataFrame | None:
        symbol = code_to_sina_symbol(code)
        data = self._get_json(
            TENCENT_KLINE,
            params={"param": f"{symbol},day,,,{min(days, 400)},qfq"},
        )
        payload = (data or {}).get("data", {}).get(symbol, {})
        series = payload.get("qfqday") or payload.get("day")
        if not series:
            return None
        records = []
        for row in series:
            # 腾讯: date, open, close, high, low, volume
            if len(row) < 6:
                continue
            records.append(
                {
                    "date": row[0],
                    "open": row[1],
                    "close": row[2],
                    "high": row[3],
                    "low": row[4],
                    "volume": row[5],
                }
            )
        return self._normalize_df(records)

    def _hist_eastmoney(self, code: str, days: int) -> pd.DataFrame | None:
        secid = code_to_em_secid(code)
        data = self._get_json(
            EM_KLINE,
            params={
                "secid": secid,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "klt": 101,
                "fqt": 1,
                "beg": "0",
                "end": "20500101",
                "lmt": min(days, 400),
            },
        )
        klines = ((data or {}).get("data") or {}).get("klines") or []
        records = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 6:
                continue
            records.append(
                {
                    "date": parts[0],
                    "open": parts[1],
                    "close": parts[2],
                    "high": parts[3],
                    "low": parts[4],
                    "volume": parts[5],
                }
            )
        return self._normalize_df(records)

    def get_history(self, code: str, days: int = 250) -> pd.DataFrame | None:
        cache_key = f"ashare_hist_v2_{code}_{days}"
        cached = self.cache.get(cache_key, max_age_seconds=12 * 3600)
        if cached:
            df = pd.DataFrame(cached)
            if not df.empty:
                return df

        fetchers = [
            ("tencent", self._hist_tencent, 2),
            ("sina", self._hist_sina, 1),
            ("eastmoney", self._hist_eastmoney, 1),
        ]
        for name, fn, tries in fetchers:
            for attempt in range(tries):
                try:
                    df = fn(code, days)
                    if df is not None and len(df) >= 20:
                        df = df.tail(int(days)).reset_index(drop=True)
                        records = df.assign(date=df["date"].dt.strftime("%Y-%m-%d")).to_dict(
                            orient="records"
                        )
                        self.cache.set(cache_key, records)
                        return df
                except Exception as e:
                    logger.warning("history %s via %s#%s failed: %s", code, name, attempt, e)
                    time.sleep(0.15 * (attempt + 1))
        return None

    def load_universe_frames(
        self, days: int = 250, limit: int | None = None
    ) -> dict[str, pd.DataFrame]:
        stocks = self.get_stock_list()
        if limit:
            stocks = stocks.head(limit)
        frames: dict[str, pd.DataFrame] = {}
        for _, row in stocks.iterrows():
            df = self.get_history(row["code"], days=days)
            if df is not None and len(df) >= 20:
                frames[str(row["code"])] = df
        return frames
