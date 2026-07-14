"""监控平台存储：快照 / 流程事件 / 告警四张表的读写。

设计文档 docs/superpowers/specs/2026-07-13-live-monitor-platform-design.md §3/§4.1。
与 LiveRecorder 写同一个 db 文件，互不 import；连接风格保持一致（WAL）。
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

# daily_snapshot 的全部列（upsert 时按此顺序取值）
_SNAPSHOT_COLS = [
    "date", "cash", "market_value", "receivables",
    "pending_market_value", "tax_provision", "total_value",
    "daily_return", "cumulative_return",
    "benchmark_close", "benchmark_daily_return", "benchmark_cumulative_return",
    "excess_return", "position_count", "turnover",
    "fees", "external_flow",
]

_POSITION_COLS = [
    "stock_code", "shares", "avg_cost", "close_price",
    "market_value", "profit", "weight",
]


class MonitorStore:
    """监控表 SQLite 存储。"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS daily_snapshot (
                    date TEXT PRIMARY KEY,
                    cash REAL NOT NULL,
                    market_value REAL NOT NULL,
                    receivables REAL NOT NULL DEFAULT 0,
                    pending_market_value REAL NOT NULL DEFAULT 0,
                    tax_provision REAL NOT NULL DEFAULT 0,
                    total_value REAL NOT NULL,
                    daily_return REAL,
                    cumulative_return REAL,
                    benchmark_close REAL,
                    benchmark_daily_return REAL,
                    benchmark_cumulative_return REAL,
                    excess_return REAL,
                    position_count INTEGER NOT NULL,
                    turnover REAL,
                    fees REAL NOT NULL DEFAULT 0,
                    external_flow REAL NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS position_snapshot (
                    date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    shares INTEGER NOT NULL,
                    avg_cost REAL NOT NULL,
                    close_price REAL,
                    market_value REAL,
                    profit REAL,
                    weight REAL,
                    PRIMARY KEY (date, stock_code)
                );

                CREATE TABLE IF NOT EXISTS pipeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_pipeline_date
                    ON pipeline_events(trade_date);

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    level TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    message TEXT NOT NULL,
                    channel TEXT,
                    sent_ok INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now', 'localtime')),
                    UNIQUE (trade_date, rule)
                );
            """)
            # 旧库迁移：daily_snapshot 补新增估值与资金流列。
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_snapshot)")}
            for col in (
                "fees", "external_flow", "receivables",
                "pending_market_value", "tax_provision",
            ):
                if col not in cols:
                    conn.execute(
                        f"ALTER TABLE daily_snapshot ADD COLUMN {col} "
                        "REAL NOT NULL DEFAULT 0"
                    )

    # ---------- daily_snapshot ----------

    def upsert_daily_snapshot(self, row: dict) -> None:
        cols = ", ".join(_SNAPSHOT_COLS)
        marks = ", ".join("?" for _ in _SNAPSHOT_COLS)
        with self._conn() as conn:
            zero_default = {
                "receivables", "pending_market_value", "tax_provision",
                "fees", "external_flow",
            }
            values = [
                (row.get(c) if row.get(c) is not None else 0.0)
                if c in zero_default else row.get(c)
                for c in _SNAPSHOT_COLS
            ]
            conn.execute(
                f"INSERT OR REPLACE INTO daily_snapshot ({cols}) VALUES ({marks})",
                values,
            )

    def get_snapshots(self, start: str = None, end: str = None) -> list:
        sql = "SELECT * FROM daily_snapshot"
        conds, params = [], []
        if start:
            conds.append("date >= ?")
            params.append(start)
        if end:
            conds.append("date <= ?")
            params.append(end)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY date ASC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_latest_snapshot(self):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_snapshot ORDER BY date DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def get_first_snapshot(self):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_snapshot ORDER BY date ASC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def get_snapshot(self, date: str):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_snapshot WHERE date=?", (date,)
            ).fetchone()
            return dict(row) if row else None

    # ---------- position_snapshot ----------

    def upsert_position_snapshots(self, date: str, rows: list) -> None:
        cols = ", ".join(["date"] + _POSITION_COLS)
        marks = ", ".join("?" for _ in range(len(_POSITION_COLS) + 1))
        with self._conn() as conn:
            conn.execute("DELETE FROM position_snapshot WHERE date=?", (date,))
            conn.executemany(
                f"INSERT INTO position_snapshot ({cols}) VALUES ({marks})",
                [[date] + [r.get(c) for c in _POSITION_COLS] for r in rows],
            )

    def get_position_snapshots(self, date: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM position_snapshot WHERE date=? ORDER BY market_value DESC",
                (date,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_position_snapshot_dates(self, limit: int = 60) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM position_snapshot ORDER BY date DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [r["date"] for r in rows]

    def get_historical_position_codes(self) -> set:
        """Return every stock code ever persisted in a position snapshot."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT stock_code FROM position_snapshot"
            ).fetchall()
            return {r["stock_code"] for r in rows}

    # ---------- pipeline_events ----------

    def record_pipeline_event(self, trade_date: str, stage: str, status: str,
                              message: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pipeline_events (trade_date, stage, status, message) "
                "VALUES (?,?,?,?)",
                (trade_date, stage, status, message),
            )

    def get_pipeline_events(self, trade_date: str = None, days: int = 10) -> list:
        with self._conn() as conn:
            if trade_date:
                rows = conn.execute(
                    "SELECT * FROM pipeline_events WHERE trade_date=? ORDER BY id ASC",
                    (trade_date,),
                ).fetchall()
            else:
                dates = conn.execute(
                    "SELECT DISTINCT trade_date FROM pipeline_events "
                    "ORDER BY trade_date DESC LIMIT ?",
                    (days,),
                ).fetchall()
                if not dates:
                    return []
                keep = [d["trade_date"] for d in dates]
                marks = ",".join("?" for _ in keep)
                rows = conn.execute(
                    f"SELECT * FROM pipeline_events WHERE trade_date IN ({marks}) "
                    "ORDER BY trade_date ASC, id ASC",
                    keep,
                ).fetchall()
            return [dict(r) for r in rows]

    # ---------- alerts ----------

    def try_record_alert(self, trade_date: str, level: str, rule: str,
                         message: str) -> bool:
        """记录告警；同日同规则已存在时返回 False（推送去重依据）。"""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO alerts (trade_date, level, rule, message) "
                "VALUES (?,?,?,?)",
                (trade_date, level, rule, message),
            )
            return cur.rowcount > 0

    def mark_alert_sent(self, trade_date: str, rule: str, channel: str,
                        ok: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE alerts SET channel=?, sent_ok=? WHERE trade_date=? AND rule=?",
                (channel, 1 if ok else 0, trade_date, rule),
            )

    def get_alerts(self, limit: int = 50) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
