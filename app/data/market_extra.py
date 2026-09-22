from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from app.config import load_config
from app.data.a_share import code_to_sina_symbol
from app.data.storage import CacheStore

logger = logging.getLogger(__name__)

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}

EM_LHB = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SINA_MONEYFLOW = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssl_qsfx_zjlrqs"
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


class DragonTigerProvider:
    """龙虎榜：东财数据中心，单次拉取近 N 日上榜明细。"""

    def __init__(self, cache: CacheStore | None = None) -> None:
        self.cache = cache or CacheStore()
        self.session = requests.Session()
        self.session.headers.update(UA)

    def get_recent_board(self, days: int = 5, page_size: int = 300) -> pd.DataFrame:
        cache_key = f"lhb_recent_{days}"
        cached = self.cache.get(cache_key, max_age_seconds=3 * 3600)
        if cached is not None:
            return pd.DataFrame(cached)

        start = (datetime.now() - timedelta(days=days + 5)).strftime("%Y-%m-%d")
        rows: list[dict[str, Any]] = []
        page = 1
        pages = 1
        while page <= pages and page <= 6:
            try:
                resp = self.session.get(
                    EM_LHB,
                    params={
                        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                        "columns": "ALL",
                        "source": "WEB",
                        "client": "WEB",
                        "pageNumber": page,
                        "pageSize": page_size,
                        "sortColumns": "TRADE_DATE",
                        "sortTypes": "-1",
                        "filter": f"(TRADE_DATE>='{start}')",
                    },
                    timeout=12,
                )
                resp.raise_for_status()
                payload = resp.json() or {}
            except Exception as e:
                logger.warning("lhb page %s failed: %s", page, e)
                break
            result = payload.get("result") or {}
            pages = int(result.get("pages") or 1)
            data = result.get("data") or []
            if not data:
                break
            for item in data:
                code = str(item.get("SECURITY_CODE") or "").zfill(6)
                if not code or code == "000000":
                    continue
                trade_date = str(item.get("TRADE_DATE") or "")[:10]
                rows.append(
                    {
                        "code": code,
                        "name": item.get("SECURITY_NAME_ABBR") or code,
                        "trade_date": trade_date,
                        "close_price": _f(item.get("CLOSE_PRICE")),
                        "change_rate": _f(item.get("CHANGE_RATE")),
                        "billboard_net_amt": _f(item.get("BILLBOARD_NET_AMT")),
                        "billboard_buy_amt": _f(item.get("BILLBOARD_BUY_AMT")),
                        "billboard_sell_amt": _f(item.get("BILLBOARD_SELL_AMT")),
                        "deal_amount_ratio": _f(item.get("DEAL_AMOUNT_RATIO")),
                        "turnover": _f(item.get("TURNOVERRATE")),
                        "reason": item.get("EXPLANATION") or "",
                        "explain": item.get("EXPLAIN") or "",
                    }
                )
            page += 1
            time.sleep(0.12)

        if rows:
            self.cache.set(cache_key, rows)
        return pd.DataFrame(rows)

    @staticmethod
    def build_code_map(board: pd.DataFrame) -> dict[str, dict[str, Any]]:
        """同一代码多日上榜时，累加净买并保留最近原因。"""
        out: dict[str, dict[str, Any]] = {}
        if board is None or board.empty:
            return out
        df = board.sort_values("trade_date")
        for _, row in df.iterrows():
            code = str(row["code"])
            cur = out.get(
                code,
                {
                    "code": code,
                    "name": row.get("name") or code,
                    "appear_days": 0,
                    "net_amt_sum": 0.0,
                    "last_date": "",
                    "reason": "",
                    "explain": "",
                    "change_rate": 0.0,
                },
            )
            cur["appear_days"] = int(cur.get("appear_days", 0)) + 1
            cur["net_amt_sum"] = float(cur.get("net_amt_sum", 0.0)) + float(
                row.get("billboard_net_amt") or 0.0
            )
            cur["last_date"] = str(row.get("trade_date") or cur.get("last_date") or "")
            cur["reason"] = str(row.get("reason") or cur.get("reason") or "")
            cur["explain"] = str(row.get("explain") or cur.get("explain") or "")
            cur["change_rate"] = float(row.get("change_rate") or 0.0)
            cur["name"] = row.get("name") or cur.get("name")
            out[code] = cur
        return out


class FundFlowProvider:
    """个股资金流：新浪 MoneyFlow 近日净流入/主力净额。"""

    def __init__(self, cache: CacheStore | None = None) -> None:
        self.cache = cache or CacheStore()
        self.session = requests.Session()
        self.session.headers.update(
            {**UA, "Referer": "https://finance.sina.com.cn"}
        )

    def get_stock_flow(self, code: str, days: int = 5) -> dict[str, Any] | None:
        cache_key = f"fundflow_sina_{code}_{days}"
        cached = self.cache.get(cache_key, max_age_seconds=6 * 3600)
        if cached is not None:
            return cached

        symbol = code_to_sina_symbol(code)
        try:
            resp = self.session.get(
                SINA_MONEYFLOW,
                params={"daima": symbol, "num": max(days, 5)},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("fundflow %s failed: %s", code, e)
            return None
        if not isinstance(data, list) or not data:
            return None

        # 接口按日期升序返回时取尾部；若倒序则取头部最近几天
        dated = []
        for item in data:
            date = str(item.get("opendate") or "")
            if not date:
                continue
            dated.append((date, item))
        dated.sort(key=lambda x: x[0])
        recent = dated[-max(days, 1) :]

        net_sum = sum(_f(item.get("netamount")) for _, item in recent)
        main_sum = sum(
            _f(item.get("r0_net")) + _f(item.get("r1_net")) for _, item in recent
        )
        # ratioamount 为净流入占成交额比，取最近一日
        last = recent[-1][1] if recent else {}
        last_net = _f(last.get("netamount"))
        last_ratio = _f(last.get("ratioamount"))
        last_main = _f(last.get("r0_net")) + _f(last.get("r1_net"))

        result = {
            "code": str(code).zfill(6),
            "window_days": len(recent),
            "net_inflow_sum": net_sum,
            "main_inflow_sum": main_sum,
            "last_net_inflow": last_net,
            "last_net_ratio": last_ratio,
            "last_main_inflow": last_main,
            "last_date": str(last.get("opendate") or ""),
            "last_close": _f(last.get("trade")),
        }
        self.cache.set(cache_key, result)
        return result


class MarketExtraFactors:
    """统一对外：龙虎榜映射 + 个股资金流查询。"""

    def __init__(self, cache: CacheStore | None = None) -> None:
        self.cache = cache or CacheStore()
        self.lhb = DragonTigerProvider(self.cache)
        self.flow = FundFlowProvider(self.cache)
        self.cfg = load_config().get("factors") or {}

    def lhb_map(self, days: int | None = None) -> dict[str, dict[str, Any]]:
        days = int(days or (self.cfg.get("lhb") or {}).get("lookback_days", 5))
        board = self.lhb.get_recent_board(days=days)
        return self.lhb.build_code_map(board)

    def fund_flow(self, code: str, days: int | None = None) -> dict[str, Any] | None:
        days = int(days or (self.cfg.get("fund_flow") or {}).get("lookback_days", 5))
        return self.flow.get_stock_flow(code, days=days)

    @staticmethod
    def lhb_score(entry: dict[str, Any] | None) -> float:
        """0-100：上榜且净买为正加分，净买规模/上榜次数加成。"""
        if not entry:
            return 0.0
        net = float(entry.get("net_amt_sum") or 0.0)
        days = int(entry.get("appear_days") or 0)
        if net <= 0:
            base = 35.0  # 上榜但净卖，低分
        else:
            # 1亿净买约 60 分量级，5亿以上接近满分
            base = 40.0 + min(net / 1e8, 5.0) * 12.0
        base += min(days - 1, 3) * 8.0
        return float(max(0.0, min(100.0, base)))

    @staticmethod
    def fund_flow_score(flow: dict[str, Any] | None) -> float:
        """0-100：近日主力/全单净流入强度。"""
        if not flow:
            return 50.0
        main = float(flow.get("main_inflow_sum") or 0.0)
        net = float(flow.get("net_inflow_sum") or 0.0)
        ratio = float(flow.get("last_net_ratio") or 0.0)
        # 主力净流入 2 亿约 +30；净流入占比正向
        score = 50.0 + main / 2e8 * 25.0 + net / 5e8 * 10.0 + ratio * 200.0
        return float(max(0.0, min(100.0, score)))
