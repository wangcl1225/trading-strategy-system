from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT, load_config


def db_path() -> Path:
    cfg = load_config()
    rel = (cfg.get("paper_trade") or {}).get("database") or (cfg.get("app") or {}).get(
        "db_path"
    ) or "data/tradelab.db"
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str = "TEXT") -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    except Exception:
        pass


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                strategy_id TEXT,
                strategy_name TEXT,
                strategy_source TEXT,
                initial_cash REAL,
                status TEXT DEFAULT 'active',
                note TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                generated_at TEXT,
                market TEXT,
                strategy TEXT,
                code TEXT,
                name TEXT,
                score REAL,
                price REAL,
                reason TEXT,
                components_json TEXT,
                UNIQUE(report_date, market, strategy, code)
            );

            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                account_id TEXT,
                strategy TEXT,
                code TEXT,
                name TEXT,
                market TEXT,
                shares REAL,
                buy_price REAL,
                buy_date TEXT,
                sell_after_trading_days INTEGER,
                status TEXT,
                sell_price REAL,
                sell_date TEXT,
                pnl REAL,
                pnl_pct REAL,
                fee_buy REAL,
                fee_sell REAL,
                signal_score REAL,
                note TEXT,
                buy_seq INTEGER,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT,
                trade_ts TEXT,
                trade_date TEXT,
                action TEXT,
                strategy TEXT,
                code TEXT,
                name TEXT,
                market TEXT,
                shares REAL,
                price REAL,
                amount REAL,
                cost_price REAL,
                pnl REAL,
                pnl_pct REAL,
                hold_days INTEGER,
                position_id TEXT,
                seq_in_strategy INTEGER,
                total_trades INTEGER,
                extra_json TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT,
                run_date TEXT,
                run_ts TEXT,
                kind TEXT,
                signal_source TEXT,
                buys_json TEXT,
                sells_json TEXT,
                portfolio_json TEXT
            );

            CREATE TABLE IF NOT EXISTS push_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                push_ts TEXT,
                push_date TEXT,
                push_type TEXT,
                account_id TEXT,
                title TEXT,
                content TEXT,
                ok INTEGER,
                response TEXT
            );
            """
        )
        # 旧库补列（索引必须在列存在后创建）
        _ensure_column(conn, "positions", "account_id", "TEXT")
        _ensure_column(conn, "trades", "account_id", "TEXT")
        _ensure_column(conn, "daily_runs", "account_id", "TEXT")
        _ensure_column(conn, "push_logs", "account_id", "TEXT")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(report_date);
            CREATE INDEX IF NOT EXISTS idx_pos_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_pos_account ON positions(account_id);
            CREATE INDEX IF NOT EXISTS idx_pos_date ON positions(buy_date);
            CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
            CREATE INDEX IF NOT EXISTS idx_trades_account ON trades(account_id);
            """
        )


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------- accounts ----------

def ensure_account(
    account_id: str,
    strategy_id: str | None = None,
    strategy_name: str | None = None,
    strategy_source: str | None = None,
    initial_cash: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    init_db()
    if initial_cash is None:
        initial_cash = float((load_config().get("paper_trade") or {}).get("initial_cash") or 1_000_000)
    ts = now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE accounts SET strategy_id=?, strategy_name=?, strategy_source=?,
                    note=?, updated_at=? WHERE account_id=?
                """,
                (
                    strategy_id or row["strategy_id"],
                    strategy_name or row["strategy_name"],
                    strategy_source or row["strategy_source"],
                    note or row["note"],
                    ts,
                    account_id,
                ),
            )
            created = False
        else:
            conn.execute(
                """
                INSERT INTO accounts (account_id, strategy_id, strategy_name, strategy_source,
                    initial_cash, status, note, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    account_id,
                    strategy_id or account_id,
                    strategy_name or account_id,
                    strategy_source or "",
                    float(initial_cash),
                    "active",
                    note,
                    ts,
                    ts,
                ),
            )
            created = True
        row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    return {"created": created, **dict(row)}


def get_account(account_id: str) -> dict[str, Any] | None:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    return dict(row) if row else None


def list_accounts() -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def account_cash(account_id: str) -> float:
    """账户可用现金 = 初始资金 - 买入成本 + 卖出回款。"""
    acc = get_account(account_id)
    if not acc:
        return 0.0
    cash = float(acc.get("initial_cash") or 0)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE account_id=?", (account_id,)
        ).fetchall()
    for r in rows:
        shares = float(r["shares"] or 0)
        price = float(r["buy_price"] or 0)
        fee_buy = float(r["fee_buy"] or 0)
        cash -= shares * price + fee_buy
        if r["status"] == "closed" and r["sell_price"] is not None:
            cash += float(r["sell_price"]) * shares - float(r["fee_sell"] or 0)
    return cash


# ---------- signals ----------

def insert_signals(report: dict[str, Any]) -> int:
    init_db()
    report_date = report.get("report_date") or today()
    generated_at = report.get("generated_at") or now_iso()
    market = str(report.get("market") or "")
    count = 0
    with get_conn() as conn:
        for strategy, items in (report.get("signals") or {}).items():
            for it in items or []:
                try:
                    conn.execute(
                        """
                        INSERT INTO signals
                        (report_date, generated_at, market, strategy, code, name, score, price, reason, components_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(report_date, market, strategy, code) DO UPDATE SET
                            generated_at=excluded.generated_at, name=excluded.name,
                            score=excluded.score, price=excluded.price,
                            reason=excluded.reason, components_json=excluded.components_json
                        """,
                        (
                            report_date, generated_at, str(it.get("market") or market), strategy,
                            str(it.get("code") or ""), str(it.get("name") or ""),
                            float(it.get("score") or 0),
                            float(it.get("price") or 0) if it.get("price") is not None else None,
                            str(it.get("reason") or ""),
                            json.dumps(it.get("components") or {}, ensure_ascii=False),
                        ),
                    )
                    count += 1
                except Exception:
                    continue
    return count


def query_signals(
    report_date: str | None = None,
    strategy: str | None = None,
    market: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM signals WHERE 1=1"
    args: list[Any] = []
    if report_date:
        sql += " AND report_date=?"; args.append(report_date)
    if date_from:
        sql += " AND report_date>=?"; args.append(date_from)
    if date_to:
        sql += " AND report_date<=?"; args.append(date_to)
    if strategy:
        sql += " AND strategy=?"; args.append(strategy)
    if market:
        sql += " AND market LIKE ?"; args.append(f"%{market}%")
    sql += " ORDER BY report_date DESC, score DESC LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["components"] = json.loads(d.get("components_json") or "{}")
        except Exception:
            d["components"] = {}
        out.append(d)
    return out


def signal_dates(limit: int = 60) -> list[str]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT report_date FROM signals ORDER BY report_date DESC LIMIT ?", (limit,)
        ).fetchall()
    return [r["report_date"] for r in rows]


# ---------- positions / trades ----------

def upsert_position(pos: dict[str, Any]) -> None:
    init_db()
    ts = now_iso()
    account_id = pos.get("account_id") or pos.get("strategy") or "default"
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO positions (
                id, account_id, strategy, code, name, market, shares, buy_price, buy_date,
                sell_after_trading_days, status, sell_price, sell_date, pnl, pnl_pct,
                fee_buy, fee_sell, signal_score, note, buy_seq, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                account_id=excluded.account_id, strategy=excluded.strategy, code=excluded.code,
                name=excluded.name, market=excluded.market, shares=excluded.shares,
                buy_price=excluded.buy_price, buy_date=excluded.buy_date,
                sell_after_trading_days=excluded.sell_after_trading_days, status=excluded.status,
                sell_price=excluded.sell_price, sell_date=excluded.sell_date, pnl=excluded.pnl,
                pnl_pct=excluded.pnl_pct, fee_buy=excluded.fee_buy, fee_sell=excluded.fee_sell,
                signal_score=excluded.signal_score, note=excluded.note, buy_seq=excluded.buy_seq,
                updated_at=excluded.updated_at
            """,
            (
                pos.get("id"), account_id, pos.get("strategy"), pos.get("code"), pos.get("name"),
                pos.get("market"), pos.get("shares"), pos.get("buy_price"), pos.get("buy_date"),
                pos.get("sell_after_trading_days"), pos.get("status"), pos.get("sell_price"),
                pos.get("sell_date"), pos.get("pnl"), pos.get("pnl_pct"), pos.get("fee_buy"),
                pos.get("fee_sell"), pos.get("signal_score"), pos.get("note"), pos.get("buy_seq"),
                pos.get("created_at") or ts, ts,
            ),
        )


def list_positions(
    account_id: str | None = None,
    status: str | None = None,
    strategy: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    code: str | None = None,
) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM positions WHERE 1=1"
    args: list[Any] = []
    if account_id:
        sql += " AND account_id=?"; args.append(account_id)
    if status:
        sql += " AND status=?"; args.append(status)
    if strategy:
        sql += " AND strategy=?"; args.append(strategy)
    if code:
        sql += " AND code=?"; args.append(code)
    if date_from:
        sql += " AND buy_date>=?"; args.append(date_from)
    if date_to:
        sql += " AND buy_date<=?"; args.append(date_to)
    sql += " ORDER BY buy_date DESC, created_at DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def next_trade_seq(strategy: str | None = None, action: str = "buy", account_id: str | None = None) -> int:
    init_db()
    sql = "SELECT COUNT(*) AS c FROM trades WHERE action=?"
    args: list[Any] = [action]
    if strategy:
        sql += " AND strategy=?"; args.append(strategy)
    if account_id:
        sql += " AND account_id=?"; args.append(account_id)
    with get_conn() as conn:
        row = conn.execute(sql, args).fetchone()
    return int((row or {"c": 0})["c"] or 0) + 1


def total_trades(action: str | None = None, account_id: str | None = None) -> int:
    init_db()
    sql = "SELECT COUNT(*) AS c FROM trades WHERE 1=1"
    args: list[Any] = []
    if action:
        sql += " AND action=?"; args.append(action)
    if account_id:
        sql += " AND account_id=?"; args.append(account_id)
    with get_conn() as conn:
        row = conn.execute(sql, args).fetchone()
    return int((row or {"c": 0})["c"] or 0)


def insert_trade(trade: dict[str, Any]) -> int:
    init_db()
    account_id = trade.get("account_id") or trade.get("strategy") or "default"
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO trades (
                account_id, trade_ts, trade_date, action, strategy, code, name, market,
                shares, price, amount, cost_price, pnl, pnl_pct, hold_days,
                position_id, seq_in_strategy, total_trades, extra_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                account_id,
                trade.get("time") or trade.get("trade_ts") or now_iso(),
                trade.get("trade_date") or today(),
                trade.get("action"), trade.get("strategy"), trade.get("code"), trade.get("name"),
                trade.get("market"), trade.get("shares"), trade.get("price"), trade.get("amount"),
                trade.get("cost_price") or trade.get("buy_price"), trade.get("pnl"),
                trade.get("pnl_pct"), trade.get("hold_days") or trade.get("hold_days_elapsed"),
                trade.get("position_id") or trade.get("id"), trade.get("seq_in_strategy"),
                trade.get("total_trades"),
                json.dumps(trade.get("extra") or {}, ensure_ascii=False),
            ),
        )
        return int(cur.lastrowid or 0)


def query_trades(
    account_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    action: str | None = None,
    strategy: str | None = None,
    code: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM trades WHERE 1=1"
    args: list[Any] = []
    if account_id:
        sql += " AND account_id=?"; args.append(account_id)
    if date_from:
        sql += " AND trade_date>=?"; args.append(date_from)
    if date_to:
        sql += " AND trade_date<=?"; args.append(date_to)
    if action:
        sql += " AND action=?"; args.append(action)
    if strategy:
        sql += " AND strategy=?"; args.append(strategy)
    if code:
        sql += " AND code=?"; args.append(code)
    sql += " ORDER BY trade_date DESC, id DESC LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def insert_daily_run(
    kind: str,
    signal_source: str | None,
    buys: list | None,
    sells: list | None,
    portfolio: dict | None,
    account_id: str | None = None,
) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_runs (account_id, run_date, run_ts, kind, signal_source, buys_json, sells_json, portfolio_json)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                account_id, today(), now_iso(), kind, signal_source,
                json.dumps(buys or [], ensure_ascii=False),
                json.dumps(sells or [], ensure_ascii=False),
                json.dumps(portfolio or {}, ensure_ascii=False),
            ),
        )


def insert_watch_snapshot(slot: str, payload: dict[str, Any]) -> None:
    """关注列表快照，便于按日期回查。kind=watch_<slot>"""
    init_db()
    day = str(payload.get("as_of_date") or payload.get("date") or today())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_runs (account_id, run_date, run_ts, kind, signal_source, buys_json, sells_json, portfolio_json)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                None,
                day,
                now_iso(),
                f"watch_{slot}",
                str(payload.get("slot") or slot),
                "[]",
                "[]",
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )


def query_watch_snapshot(slot: str, report_date: str | None = None) -> dict[str, Any] | None:
    init_db()
    kind = f"watch_{slot}"
    sql = "SELECT * FROM daily_runs WHERE kind=?"
    args: list[Any] = [kind]
    if report_date:
        sql += " AND run_date=?"
        args.append(report_date)
    sql += " ORDER BY run_date DESC, id DESC LIMIT 1"
    with get_conn() as conn:
        row = conn.execute(sql, args).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        payload = json.loads(d.get("portfolio_json") or "{}")
    except Exception:
        payload = {}
    payload["_run_date"] = d.get("run_date")
    payload["_run_ts"] = d.get("run_ts")
    return payload


def watch_snapshot_dates(slot: str | None = None, limit: int = 30) -> list[str]:
    init_db()
    if slot:
        sql = "SELECT DISTINCT run_date FROM daily_runs WHERE kind=? ORDER BY run_date DESC LIMIT ?"
        args: list[Any] = [f"watch_{slot}", limit]
    else:
        sql = "SELECT DISTINCT run_date FROM daily_runs WHERE kind LIKE 'watch_%' ORDER BY run_date DESC LIMIT ?"
        args = [limit]
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [r["run_date"] for r in rows]


def first_signal_map(strategy_id: str, lookback_days: int = 30) -> dict[str, dict[str, Any]]:
    """同一策略下各 code 的首次信号（按 report_date 最早），用于连续上榜时算信号价后涨幅。"""
    init_db()
    from datetime import datetime, timedelta

    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    rows = query_signals(strategy=strategy_id, date_from=start, limit=2000)
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        code = str(r.get("code") or "")
        if not code:
            continue
        d = str(r.get("report_date") or "")
        price = r.get("price")
        cur = out.get(code)
        if cur is None:
            out[code] = {
                "code": code,
                "name": r.get("name"),
                "first_date": d,
                "first_price": price,
                "latest_date": d,
                "latest_price": price,
                "appear_days": 1,
            }
        else:
            cur["appear_days"] += 1
            if d < str(cur.get("first_date") or "9999"):
                cur["first_date"] = d
                cur["first_price"] = price
            if d >= str(cur.get("latest_date") or ""):
                cur["latest_date"] = d
                cur["latest_price"] = price
    return out


def query_daily_runs(
    account_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    kind: str | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM daily_runs WHERE 1=1"
    args: list[Any] = []
    if account_id:
        sql += " AND account_id=?"; args.append(account_id)
    if date_from:
        sql += " AND run_date>=?"; args.append(date_from)
    if date_to:
        sql += " AND run_date<=?"; args.append(date_to)
    if kind:
        sql += " AND kind=?"; args.append(kind)
    sql += " ORDER BY run_date DESC, id DESC LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        for key in ("buys_json", "sells_json", "portfolio_json"):
            try:
                r[key.replace("_json", "")] = json.loads(r.get(key) or ("[]" if key != "portfolio_json" else "{}"))
            except Exception:
                r[key.replace("_json", "")] = [] if key != "portfolio_json" else {}
    return rows


def insert_push_log(
    push_type: str, title: str, content: str, ok: bool, response: Any = None, account_id: str | None = None
) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO push_logs (push_ts, push_date, push_type, account_id, title, content, ok, response)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                now_iso(), today(), push_type, account_id, title, content[:4000], 1 if ok else 0,
                json.dumps(response, ensure_ascii=False, default=str)[:2000] if response is not None else None,
            ),
        )


def query_push_logs(
    account_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    push_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM push_logs WHERE 1=1"
    args: list[Any] = []
    if account_id:
        sql += " AND (account_id=? OR account_id IS NULL)"; args.append(account_id)
    if date_from:
        sql += " AND push_date>=?"; args.append(date_from)
    if date_to:
        sql += " AND push_date<=?"; args.append(date_to)
    if push_type:
        sql += " AND push_type=?"; args.append(push_type)
    sql += " ORDER BY push_date DESC, id DESC LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def portfolio_stats_from_db(account_id: str | None = None) -> dict[str, Any]:
    init_db()
    where = " WHERE 1=1"
    args: list[Any] = []
    if account_id:
        where += " AND account_id=?"
        args.append(account_id)
    with get_conn() as conn:
        buys = conn.execute(f"SELECT COUNT(*) c FROM trades{where} AND action='buy'", args).fetchone()
        sells = conn.execute(f"SELECT COUNT(*) c FROM trades{where} AND action='sell'", args).fetchone()
        pnl_row = conn.execute(
            f"SELECT COALESCE(SUM(pnl),0) s, COUNT(*) c FROM trades{where} AND action='sell' AND pnl IS NOT NULL",
            args,
        ).fetchone()
        win_row = conn.execute(
            f"SELECT COUNT(*) c FROM trades{where} AND action='sell' AND pnl > 0", args
        ).fetchone()
        if account_id:
            open_row = conn.execute(
                "SELECT COUNT(*) c FROM positions WHERE status='open' AND account_id=?", (account_id,)
            ).fetchone()
        else:
            open_row = conn.execute("SELECT COUNT(*) c FROM positions WHERE status='open'").fetchone()
    sell_cnt = int(sells["c"] or 0)
    win_cnt = int(win_row["c"] or 0)
    return {
        "buy_count": int(buys["c"] or 0),
        "sell_count": sell_cnt,
        "realized_pnl": float(pnl_row["s"] or 0),
        "realized_trades": int(pnl_row["c"] or 0),
        "win_rate": round(win_cnt / sell_cnt, 3) if sell_cnt else None,
        "open_count": int(open_row["c"] or 0),
        "account_id": account_id,
    }


def summarize_accounts() -> list[dict[str, Any]]:
    """各策略账户概览，便于对比隔离效果。"""
    init_db()
    accounts = list_accounts()
    out = []
    for acc in accounts:
        aid = acc["account_id"]
        cash = account_cash(aid)
        opens = list_positions(account_id=aid, status="open")
        stats = portfolio_stats_from_db(account_id=aid)
        out.append(
            {
                **acc,
                "cash": round(cash, 2),
                "open_count": len(opens),
                "buy_count": stats.get("buy_count"),
                "sell_count": stats.get("sell_count"),
                "realized_pnl": stats.get("realized_pnl"),
            }
        )
    return out
