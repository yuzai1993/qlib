"""SQLite persistence layer for paper trading."""

import sqlite3
import json
import logging
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, date
from typing import Optional

import pandas as pd

logger = logging.getLogger("paper_trading.recorder")


class Recorder:
    """Manages all SQLite read/write operations."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
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
                CREATE TABLE IF NOT EXISTS account_summary (
                    date TEXT PRIMARY KEY,
                    cash REAL NOT NULL,
                    total_value REAL NOT NULL,
                    market_value REAL NOT NULL,
                    daily_return REAL,
                    cumulative_return REAL,
                    benchmark_return REAL,
                    benchmark_cumulative_return REAL,
                    excess_return REAL,
                    position_count INTEGER,
                    turnover REAL
                );

                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    shares REAL NOT NULL,
                    cost_price REAL NOT NULL,
                    current_price REAL,
                    market_value REAL,
                    profit REAL,
                    profit_rate REAL,
                    weight REAL,
                    holding_days INTEGER DEFAULT 1,
                    UNIQUE(date, instrument)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    target_shares REAL,
                    filled_shares REAL,
                    price REAL,
                    amount REAL,
                    commission REAL,
                    status TEXT NOT NULL,
                    reject_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    score REAL NOT NULL,
                    rank INTEGER,
                    UNIQUE(date, instrument)
                );

                CREATE TABLE IF NOT EXISTS trade_summary (
                    date TEXT PRIMARY KEY,
                    buy_count INTEGER DEFAULT 0,
                    sell_count INTEGER DEFAULT 0,
                    buy_amount REAL DEFAULT 0,
                    sell_amount REAL DEFAULT 0,
                    total_commission REAL DEFAULT 0,
                    net_inflow REAL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS stock_names (
                    instrument TEXT PRIMARY KEY,
                    ts_code TEXT,
                    name TEXT NOT NULL,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS system_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    module TEXT,
                    message TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_positions_date ON positions(date);
                CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(date);
                CREATE INDEX IF NOT EXISTS idx_predictions_date ON predictions(date);
                CREATE INDEX IF NOT EXISTS idx_system_log_timestamp ON system_log(timestamp);
            """)

    # ==================== Account Summary ====================

    @staticmethod
    def _to_float(val):
        if val is None:
            return None
        return float(val)

    def save_account_summary(self, data: dict):
        f = self._to_float
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO account_summary
                (date, cash, total_value, market_value, daily_return,
                 cumulative_return, benchmark_return, benchmark_cumulative_return,
                 excess_return, position_count, turnover)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["date"], f(data["cash"]), f(data["total_value"]),
                f(data["market_value"]), f(data.get("daily_return")),
                f(data.get("cumulative_return")), f(data.get("benchmark_return")),
                f(data.get("benchmark_cumulative_return")), f(data.get("excess_return")),
                data.get("position_count"), f(data.get("turnover")),
            ))

    def get_account_summary(self, start: str = None, end: str = None) -> pd.DataFrame:
        query = "SELECT * FROM account_summary"
        params = []
        clauses = ["date != 'init'"]
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY date"
        with self._conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def get_latest_account_summary(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM account_summary WHERE date != 'init' ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if row:
                return dict(row)
            row = conn.execute(
                "SELECT * FROM account_summary WHERE date = 'init'"
            ).fetchone()
            return dict(row) if row else None

    def has_date_executed(self, dt: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM account_summary WHERE date = ?", (dt,)
            ).fetchone()
            return row is not None

    # ==================== Positions ====================

    def save_positions(self, date_str: str, positions: list[dict]):
        with self._conn() as conn:
            conn.execute("DELETE FROM positions WHERE date = ?", (date_str,))
            for pos in positions:
                conn.execute("""
                    INSERT INTO positions
                    (date, instrument, shares, cost_price, current_price,
                     market_value, profit, profit_rate, weight, holding_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    date_str, pos["instrument"], pos["shares"],
                    pos["cost_price"], pos.get("current_price"),
                    pos.get("market_value"), pos.get("profit"),
                    pos.get("profit_rate"), pos.get("weight"),
                    pos.get("holding_days", 1),
                ))

    def get_positions(self, date_str: str = None) -> pd.DataFrame:
        if date_str is None:
            query = """
                SELECT p.*, s.name FROM positions p
                LEFT JOIN stock_names s ON p.instrument = s.instrument
                WHERE p.date = (SELECT MAX(date) FROM positions)
                ORDER BY p.weight DESC
            """
            params = []
        else:
            query = """
                SELECT p.*, s.name FROM positions p
                LEFT JOIN stock_names s ON p.instrument = s.instrument
                WHERE p.date = ? ORDER BY p.weight DESC
            """
            params = [date_str]
        with self._conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def get_latest_positions_as_dict(self) -> dict:
        """Return latest positions as {instrument: {shares, cost_price, holding_days}}."""
        df = self.get_positions()
        if df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            result[row["instrument"]] = {
                "shares": row["shares"],
                "cost_price": row["cost_price"],
                "holding_days": row.get("holding_days", 1),
            }
        return result

    # ==================== Orders ====================

    def save_orders(self, orders: list[dict]):
        with self._conn() as conn:
            for order in orders:
                conn.execute("""
                    INSERT OR REPLACE INTO orders
                    (order_id, date, instrument, direction, target_shares,
                     filled_shares, price, amount, commission, status, reject_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order["order_id"], order["date"], order["instrument"],
                    order["direction"], order.get("target_shares"),
                    order.get("filled_shares"), order.get("price"),
                    order.get("amount"), order.get("commission"),
                    order["status"], order.get("reject_reason"),
                ))

    def get_orders(self, start: str = None, end: str = None,
                   direction: str = None) -> pd.DataFrame:
        query = """
            SELECT o.*, s.name FROM orders o
            LEFT JOIN stock_names s ON o.instrument = s.instrument
        """
        params = []
        clauses = []
        if start:
            clauses.append("o.date >= ?")
            params.append(start)
        if end:
            clauses.append("o.date <= ?")
            params.append(end)
        if direction:
            clauses.append("o.direction = ?")
            params.append(direction.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY o.date DESC, o.order_id"
        with self._conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # ==================== Trade Summary ====================

    def save_trade_summary(self, data: dict):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trade_summary
                (date, buy_count, sell_count, buy_amount, sell_amount,
                 total_commission, net_inflow)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                data["date"], data.get("buy_count", 0),
                data.get("sell_count", 0), data.get("buy_amount", 0),
                data.get("sell_amount", 0), data.get("total_commission", 0),
                data.get("net_inflow", 0),
            ))

    # ==================== Predictions ====================

    def save_predictions(self, date_str: str, predictions: pd.Series):
        """Save prediction scores. predictions: Series with instrument index."""
        ranked = predictions.rank(ascending=False).astype(int)
        with self._conn() as conn:
            conn.execute("DELETE FROM predictions WHERE date = ?", (date_str,))
            for instrument, score in predictions.items():
                conn.execute("""
                    INSERT INTO predictions (date, instrument, score, rank)
                    VALUES (?, ?, ?, ?)
                """, (date_str, instrument, float(score), int(ranked[instrument])))

    def get_predictions(self, date_str: str = None) -> pd.DataFrame:
        if date_str is None:
            query = """
                SELECT p.*, s.name FROM predictions p
                LEFT JOIN stock_names s ON p.instrument = s.instrument
                WHERE p.date = (SELECT MAX(date) FROM predictions)
                ORDER BY p.rank
            """
            params = []
        else:
            query = """
                SELECT p.*, s.name FROM predictions p
                LEFT JOIN stock_names s ON p.instrument = s.instrument
                WHERE p.date = ? ORDER BY p.rank
            """
            params = [date_str]
        with self._conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def get_latest_prediction_scores(self) -> Optional[pd.Series]:
        """Return the latest prediction as Series {instrument: score}."""
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(date) as d FROM predictions").fetchone()
            if not row or not row["d"]:
                return None
            date_str = row["d"]
            df = pd.read_sql_query(
                "SELECT instrument, score FROM predictions WHERE date = ?",
                conn, params=[date_str],
            )
            if df.empty:
                return None
            return df.set_index("instrument")["score"]

    def get_prediction_date(self) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(date) as d FROM predictions").fetchone()
            return row["d"] if row else None

    # ==================== Stock Names ====================

    def save_stock_names(self, names_df: pd.DataFrame):
        """Save stock name mapping. Expects columns: instrument, ts_code, name."""
        now = datetime.now().isoformat()
        with self._conn() as conn:
            for _, row in names_df.iterrows():
                conn.execute("""
                    INSERT OR REPLACE INTO stock_names (instrument, ts_code, name, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (row["instrument"], row.get("ts_code", ""), row["name"], now))

    def get_stock_names(self) -> dict:
        """Return {instrument: name} mapping."""
        with self._conn() as conn:
            rows = conn.execute("SELECT instrument, name FROM stock_names").fetchall()
            return {r["instrument"]: r["name"] for r in rows}

    def get_stock_names_updated_at(self) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(updated_at) as t FROM stock_names").fetchone()
            return row["t"] if row else None

    # ==================== System Log ====================

    def save_system_log(self, level: str, module: str, message: str):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO system_log (timestamp, level, module, message)
                VALUES (?, ?, ?, ?)
            """, (datetime.now().isoformat(), level, module, message))

    def get_system_logs(self, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM system_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ==================== Utility ====================

    def delete_date_data(self, date_str: str):
        """Remove all data for a specific date (for rollback)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM account_summary WHERE date = ?", (date_str,))
            conn.execute("DELETE FROM positions WHERE date = ?", (date_str,))
            conn.execute("DELETE FROM orders WHERE date = ?", (date_str,))
            conn.execute("DELETE FROM trade_summary WHERE date = ?", (date_str,))

    def export_table(self, table: str, output_path: str):
        with self._conn() as conn:
            df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
            df.to_csv(output_path, index=False)
            logger.info("Exported %s to %s (%d rows)", table, output_path, len(df))
