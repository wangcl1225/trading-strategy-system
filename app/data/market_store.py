from __future__ import annotations

"""全市场增量行情库（SQLite）。

- 按 code+date 唯一存储日K
- 增量同步：只拉缺失日或最近 N 日刷新
- 读侧优先库，缺失再回源（新浪/腾讯/Binance）
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from app.config import ROOT, load_config
from app.data.a_share import AShareDataProvider
from app.data.crypto import CryptoDataProvider

logger = logging.getLogger(__name__)

OHLCV = ("date", "open", "high", "low", "close", "volume")


def market_db_path() -> Path:
    rel = (
        (load_config().get("market_db") or {}).get("path")
        or (load_config().get("app") or {}).get("market_db_path")
        or "data/market_bars.db"
    )
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(market_db_path()), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_market_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bars (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                updated_at TEXT,
                PRIMARY KEY (market, code, date)
            );
            CREATE INDEX IF NOT EXISTS idx_bars_code_date ON bars(code, date);
            CREATE INDEX IF NOT EXISTS idx_bars_date ON bars(date);

            CREATE TABLE IF NOT EXISTS sync_meta (
                market TEXT,
                code TEXT,
                last_date TEXT,
                last_sync TEXT,
                rows INTEGER,
                source TEXT,
                PRIMARY KEY (market, code)
            );
            """
        )


def upsert_bars(market: str, code: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    init_market_db()
    ts = datetime.now().isoformat(timespec="seconds")
    rows = []
    work = df.copy()
    work["date"] = pd.to_datetime(work["date"]).dt.strftime("%Y-%m-%d")
    for _, r in work.iterrows():
        rows.append(
            (
                market,
                code,
                r["date"],
                float(r.get("open") or 0) or None,
                float(r.get("high") or 0) or None,
                float(r.get("low") or 0) or None,
                float(r.get("close") or 0) or None,
                float(r.get("volume") or 0) or None,
                ts,
            )
        )
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO bars (market, code, date, open, high, low, close, volume, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(market, code, date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume, updated_at=excluded.updated_at
            """,
            rows,
        )
        conn.execute(
            """
            INSERT INTO sync_meta (market, code, last_date, last_sync, rows, source)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(market, code) DO UPDATE SET
                last_date=excluded.last_date, last_sync=excluded.last_sync,
                rows=excluded.rows, source=excluded.source
            """,
            (market, code, str(work["date"].max()), ts, len(work), "live"),
        )
    return len(rows)


def load_bars(market: str, code: str, days: int = 250) -> pd.DataFrame | None:
    init_market_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM bars WHERE market=? AND code=? ORDER BY date DESC LIMIT ?",
            (market, code, int(days)),
        ).fetchall()
    if not rows:
        return None
    data = [dict(r) for r in rows][::-1]
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])
    return df


def last_bar_date(market: str, code: str) -> str | None:
    init_market_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_date FROM sync_meta WHERE market=? AND code=?",
            (market, code),
        ).fetchone()
    return row["last_date"] if row else None


def list_synced_codes(market: str | None = None) -> list[dict[str, Any]]:
    init_market_db()
    sql = "SELECT market, code, last_date, last_sync, rows FROM sync_meta"
    args: list[Any] = []
    if market:
        sql += " WHERE market=?"
        args.append(market)
    sql += " ORDER BY market, code"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


class MarketBarStore:
    """增量同步入口：全市场 A 股 + 加密，按缺口补历史。"""

    def __init__(self) -> None:
        self.ashare = AShareDataProvider()
        self.crypto = CryptoDataProvider()
        init_market_db()

    def sync_symbol(
        self,
        market: str,
        code: str,
        days: int = 250,
        refresh_tail_days: int = 5,
        name: str = "",
    ) -> dict[str, Any]:
        """增量：已有历史则只补缺失/最近尾巴，否则全量拉 days。"""
        existing = load_bars(market, code, days=max(days, 320))
        last = str(existing["date"].iloc[-1].date()) if existing is not None and len(existing) else None
        today = date.today().isoformat()

        if last is None:
            df = self._fetch(market, code, days)
            n = upsert_bars(market, code, df) if df is not None else 0
            return {
                "market": market,
                "code": code,
                "name": name,
                "mode": "full",
                "rows": n,
                "last_date": last_bar_date(market, code),
            }

        # 已到最新交易日则仅在必要时刷新尾巴
        if last >= today:
            need_tail = refresh_tail_days
        else:
            gap_days = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
            need_tail = min(days, max(refresh_tail_days, gap_days + 5))

        df = self._fetch(market, code, need_tail + 5)
        n = 0
        if df is not None and len(df):
            # 只 upsert last-refresh_tail_days 起的数据（增量）
            if refresh_tail_days and last:
                cutoff = (
                    datetime.strptime(last, "%Y-%m-%d") - timedelta(days=refresh_tail_days)
                ).strftime("%Y-%m-%d")
                df = df[pd.to_datetime(df["date"]) >= cutoff]
            n = upsert_bars(market, code, df)
        return {
            "market": market,
            "code": code,
            "name": name,
            "mode": "incremental",
            "rows": n,
            "last_date": last_bar_date(market, code),
            "prev_last": last,
        }

    def _fetch(self, market: str, code: str, days: int) -> pd.DataFrame | None:
        if market == "crypto":
            return self.crypto.get_history(code, days=days)
        return self.ashare.get_history(code, days=days)

    def sync_universe(
        self,
        market: str = "all",
        days: int = 250,
        max_codes: int | None = None,
        codes: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """全市场/指定池增量同步。"""
        started = datetime.now().isoformat(timespec="seconds")
        tasks: list[tuple[str, str, str]] = []  # market, code, name
        if market in ("a_share", "all"):
            stocks = self.ashare.get_stock_list()
            limit = max_codes or int((load_config().get("universe") or {}).get("a_share", {}).get("max_count") or 300)
            if codes:
                want = {str(c).zfill(6) for c in codes}
                stocks = stocks[stocks["code"].astype(str).str.zfill(6).isin(want)]
            for _, r in stocks.head(limit if not codes else len(stocks)).iterrows():
                tasks.append(("a_share", str(r["code"]).zfill(6), str(r["name"])))
        if market in ("crypto", "all"):
            for sym in self.crypto.symbols:
                if codes and sym not in {str(c).upper() for c in codes}:
                    continue
                tasks.append(("crypto", sym, sym))

        results = []
        full_n = inc_n = 0
        for mkt, code, name in tasks:
            try:
                info = self.sync_symbol(mkt, code, days=days, name=name)
                results.append(info)
                if info.get("mode") == "full":
                    full_n += 1
                else:
                    inc_n += 1
            except Exception as e:
                results.append({"market": mkt, "code": code, "name": name, "error": str(e)})
                logger.warning("sync %s %s failed: %s", mkt, code, e)

        with get_conn() as conn:
            total_rows = conn.execute("SELECT COUNT(*) c FROM bars").fetchone()["c"]
            n_codes = conn.execute("SELECT COUNT(*) c FROM sync_meta").fetchone()["c"]

        return {
            "started_at": started,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "market": market,
            "requested": len(tasks),
            "full_synced": full_n,
            "incremental_synced": inc_n,
            "errors": [r for r in results if r.get("error")],
            "db_path": str(market_db_path()),
            "db_bars": int(total_rows),
            "db_codes": int(n_codes),
            "sample": results[:15],
        }

    def get_history(self, market: str, code: str, days: int = 250) -> pd.DataFrame | None:
        """读优先库，缺失则回源并写入（增量）。"""
        df = load_bars(market, code, days=days)
        if df is not None and len(df) >= min(20, days):
            return df.tail(days).reset_index(drop=True)
        try:
            self.sync_symbol(market, code, days=days, name=code)
        except Exception as e:
            logger.warning("history fallback sync failed %s %s: %s", market, code, e)
        return load_bars(market, code, days=days)
