"""回执导入：读取 outbound/fills_*.jsonl，入库并维护实盘持仓账簿。

关键规则（设计文档 §5.3/§7.2 定稿）：
- 只处理已有 ``.done`` 标记的回执文件
- ``mode=SIMULATE`` 的回执只入 fills 表，绝不更新 live 持仓
- 持仓按「已应用数量」增量更新，重复导入天然幂等
- 导入完成后回执文件移入 ``archive/``
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from live_trading.modules.signal_schema import (
    FillEvent,
    TERMINAL_FILL_STATUS,
    validate_fill,
)

logger = logging.getLogger("live_trading.fill_importer")

# 会改变持仓的终态
_POSITION_STATUS = {"FILLED", "PARTIAL"}


class LiveRecorder:
    """实盘账簿 SQLite 存储（batches / fills / positions）。"""

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
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    trade_date TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    planned_orders INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS fills (
                    client_order_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_qty INTEGER,
                    filled_qty INTEGER,
                    avg_price REAL,
                    qmt_order_id TEXT,
                    message TEXT,
                    ts TEXT,
                    applied_qty INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS positions (
                    stock_code TEXT PRIMARY KEY,
                    shares INTEGER NOT NULL,
                    avg_cost REAL NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS account_state (
                    key TEXT PRIMARY KEY,
                    value REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_fills_batch ON fills(batch_id);
            """)

    # ---------- batches ----------

    def record_batch(self, batch_id: str, trade_date: str, mode: str,
                     planned_orders: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO batches "
                "(batch_id, trade_date, mode, planned_orders) VALUES (?,?,?,?)",
                (batch_id, trade_date, mode, planned_orders),
            )

    def get_batch(self, batch_id: str):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            return dict(row) if row else None

    # ---------- fills ----------

    def get_fills(self, batch_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM fills WHERE batch_id=? ORDER BY client_order_id",
                (batch_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def apply_fill(self, fill: FillEvent) -> None:
        """upsert 回执；LIVE 终态成交按增量更新持仓（幂等）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT applied_qty FROM fills WHERE client_order_id=?",
                (fill.client_order_id,),
            ).fetchone()
            applied_qty = row["applied_qty"] if row else 0

            delta = 0
            if fill.mode == "LIVE" and fill.status in _POSITION_STATUS:
                delta = int(fill.filled_qty) - applied_qty
                if delta > 0:
                    self._apply_position_delta(conn, fill, delta)
                    self._apply_cash_delta(conn, fill, delta)
                else:
                    delta = 0

            conn.execute(
                """INSERT INTO fills (client_order_id, batch_id, mode, stock_code,
                       side, status, requested_qty, filled_qty, avg_price,
                       qmt_order_id, message, ts, applied_qty)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(client_order_id) DO UPDATE SET
                       status=excluded.status,
                       filled_qty=excluded.filled_qty,
                       avg_price=excluded.avg_price,
                       qmt_order_id=excluded.qmt_order_id,
                       message=excluded.message,
                       ts=excluded.ts,
                       applied_qty=excluded.applied_qty""",
                (fill.client_order_id, fill.batch_id, fill.mode, fill.stock_code,
                 fill.side, fill.status, fill.requested_qty, fill.filled_qty,
                 fill.avg_price, fill.qmt_order_id, fill.message, fill.ts,
                 applied_qty + delta),
            )

    @staticmethod
    def _apply_position_delta(conn, fill: FillEvent, delta: int) -> None:
        row = conn.execute(
            "SELECT shares, avg_cost FROM positions WHERE stock_code=?",
            (fill.stock_code,),
        ).fetchone()
        old_shares = row["shares"] if row else 0
        old_cost = row["avg_cost"] if row else 0.0

        if fill.side == "BUY":
            new_shares = old_shares + delta
            new_cost = (old_shares * old_cost + delta * fill.avg_price) / new_shares
        else:  # SELL
            new_shares = old_shares - delta
            new_cost = old_cost

        if new_shares > 0:
            conn.execute(
                "INSERT OR REPLACE INTO positions (stock_code, shares, avg_cost) "
                "VALUES (?,?,?)",
                (fill.stock_code, new_shares, new_cost),
            )
        else:
            if new_shares < 0:
                logger.warning(
                    "position %s went negative (%d), clamping to 0 — check fills",
                    fill.stock_code, new_shares,
                )
            conn.execute(
                "DELETE FROM positions WHERE stock_code=?", (fill.stock_code,)
            )

    @staticmethod
    def _apply_cash_delta(conn, fill: FillEvent, delta: int) -> None:
        """按成交额近似调整现金（不含手续费，盘后对账时人工校正）。"""
        amount = delta * fill.avg_price
        change = amount if fill.side == "SELL" else -amount
        conn.execute(
            "INSERT INTO account_state (key, value) VALUES ('cash', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = value + ?",
            (change, change),
        )

    # ---------- account ----------

    def set_cash(self, cash: float) -> None:
        """人工 seed / 校正现金入口。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO account_state (key, value) VALUES ('cash', ?)",
                (cash,),
            )

    def get_cash(self) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM account_state WHERE key='cash'"
            ).fetchone()
            return float(row["value"]) if row else 0.0

    def list_batches(self, limit: int = 10) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM batches ORDER BY trade_date DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- positions ----------

    def upsert_position(self, stock_code: str, shares: int, avg_cost: float) -> None:
        """人工 seed / 校正持仓入口。"""
        with self._conn() as conn:
            if shares > 0:
                conn.execute(
                    "INSERT OR REPLACE INTO positions (stock_code, shares, avg_cost) "
                    "VALUES (?,?,?)",
                    (stock_code, shares, avg_cost),
                )
            else:
                conn.execute(
                    "DELETE FROM positions WHERE stock_code=?", (stock_code,)
                )

    def get_positions(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM positions").fetchall()
            return {
                r["stock_code"]: {"shares": r["shares"], "avg_cost": r["avg_cost"]}
                for r in rows
            }


class FillImporter:
    """扫描共享目录 outbound/，导入回执并归档。"""

    def __init__(self, bridge_root, recorder: LiveRecorder):
        self.bridge_root = Path(bridge_root)
        self.outbound = self.bridge_root / "outbound"
        self.archive = self.bridge_root / "archive"
        self.recorder = recorder

    def import_fills(self) -> int:
        """导入所有已完成批次的回执，返回处理的 fill 事件数。"""
        if not self.outbound.exists():
            return 0

        count = 0
        for done_path in sorted(self.outbound.glob("fills_*.done")):
            jsonl_path = done_path.with_suffix(".jsonl")
            if not jsonl_path.exists():
                logger.warning("done without jsonl: %s", done_path)
                continue
            count += self._import_one(jsonl_path)
            self._archive(jsonl_path)
            self._archive(done_path)
        return count

    def _import_one(self, jsonl_path: Path) -> int:
        count = 0
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("type") != "fill_event":
                continue
            fill = FillEvent.from_dict(d)
            validate_fill(fill)
            self.recorder.apply_fill(fill)
            count += 1
        logger.info("imported %d fill events from %s", count, jsonl_path.name)
        return count

    def _archive(self, path: Path) -> None:
        self.archive.mkdir(parents=True, exist_ok=True)
        os.replace(path, self.archive / path.name)

    def reconcile(self, batch_id: str) -> dict:
        """对账：计划订单数 vs 已到终态回执数。"""
        batch = self.recorder.get_batch(batch_id)
        planned = batch["planned_orders"] if batch else 0
        fills = self.recorder.get_fills(batch_id)
        terminal = sum(1 for f in fills if f["status"] in TERMINAL_FILL_STATUS)
        return {
            "planned": planned,
            "terminal": terminal,
            "missing": max(planned - terminal, 0),
        }
