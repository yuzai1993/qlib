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

from live_trading.modules.fees import DEFAULT_FEES, order_total_fee
from live_trading.modules.signal_schema import (
    FillEvent,
    TERMINAL_FILL_STATUS,
    validate_fill,
)

logger = logging.getLogger("live_trading.fill_importer")

# 会改变持仓的终态
_POSITION_STATUS = {"FILLED", "PARTIAL"}

# 计入外部出入金（日收益计算时剔除）的流水类型
EXTERNAL_FLOW_TYPES = {"DEPOSIT", "WITHDRAW", "CORRECTION"}


class LiveRecorder:
    """实盘账簿 SQLite 存储（batches / fills / positions / cash_flows）。"""

    def __init__(self, db_path: str, fees: dict = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fees = dict(DEFAULT_FEES)
        if fees:
            self.fees.update(fees)
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
                    applied_qty INTEGER NOT NULL DEFAULT 0,
                    applied_fee REAL NOT NULL DEFAULT 0
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

                CREATE TABLE IF NOT EXISTS signal_orders (
                    client_order_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    instrument_qlib TEXT,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price_type TEXT,
                    limit_price REAL NOT NULL,
                    priority INTEGER,
                    reason TEXT
                );

                CREATE TABLE IF NOT EXISTS stock_names (
                    stock_code TEXT PRIMARY KEY,
                    instrument TEXT,
                    name TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS cash_flows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    flow_type TEXT NOT NULL,
                    stock_code TEXT,
                    amount REAL NOT NULL,
                    note TEXT,
                    dedup_key TEXT UNIQUE,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_cash_flows_date
                    ON cash_flows(trade_date);

                CREATE INDEX IF NOT EXISTS idx_fills_batch ON fills(batch_id);
                CREATE INDEX IF NOT EXISTS idx_orders_batch ON signal_orders(batch_id);
            """)
            # 旧库迁移：fills 补 applied_fee 列
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(fills)")}
            if "applied_fee" not in cols:
                conn.execute(
                    "ALTER TABLE fills ADD COLUMN applied_fee REAL NOT NULL DEFAULT 0"
                )

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

    # ---------- signal_orders（发布时写入，回执前可看执行计划）----------

    def record_orders(self, batch_id: str, orders: list) -> None:
        """写入批次执行计划。orders 为 SignalOrder 或同名字段 dict。

        client_order_id 按日唯一（协议不含 batch seq），同日重发会覆盖旧计划行。
        """
        rows = []
        for o in orders:
            get = o.get if isinstance(o, dict) else lambda k, d=None: getattr(o, k, d)
            rows.append((
                get("client_order_id"),
                batch_id,
                get("stock_code"),
                get("instrument_qlib"),
                get("side"),
                int(get("quantity")),
                get("price_type"),
                float(get("limit_price")),
                get("priority"),
                get("reason"),
            ))
        with self._conn() as conn:
            conn.execute("DELETE FROM signal_orders WHERE batch_id=?", (batch_id,))
            if rows:
                ids = [r[0] for r in rows]
                marks = ",".join("?" for _ in ids)
                conn.execute(
                    f"DELETE FROM signal_orders WHERE client_order_id IN ({marks})",
                    ids,
                )
            conn.executemany(
                """INSERT INTO signal_orders
                   (client_order_id, batch_id, stock_code, instrument_qlib,
                    side, quantity, price_type, limit_price, priority, reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def get_orders(self, batch_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_orders WHERE batch_id=? "
                "ORDER BY priority ASC, client_order_id ASC",
                (batch_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- stock_names ----------

    def save_stock_names(self, rows: list) -> None:
        """rows: [{stock_code, instrument, name}, ...]"""
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO stock_names "
                "(stock_code, instrument, name, updated_at) "
                "VALUES (?,?,?, datetime('now', 'localtime'))",
                [(r["stock_code"], r.get("instrument"), r["name"]) for r in rows],
            )

    def get_stock_names(self) -> dict:
        """{stock_code(QMT): name}"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT stock_code, name FROM stock_names"
            ).fetchall()
            return {r["stock_code"]: r["name"] for r in rows}

    # ---------- fills ----------

    def get_fills(self, batch_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM fills WHERE batch_id=? ORDER BY client_order_id",
                (batch_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def apply_fill(self, fill: FillEvent) -> None:
        """upsert 回执；LIVE 终态成交按增量更新持仓与现金（含费用，幂等）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT applied_qty, applied_fee FROM fills WHERE client_order_id=?",
                (fill.client_order_id,),
            ).fetchone()
            applied_qty = row["applied_qty"] if row else 0
            applied_fee = row["applied_fee"] if row else 0.0

            delta = 0
            fee_delta = 0.0
            if fill.mode == "LIVE" and fill.status in _POSITION_STATUS:
                delta = int(fill.filled_qty) - applied_qty
                if delta > 0:
                    self._apply_position_delta(conn, fill, delta)
                    self._apply_cash_delta(conn, fill, delta)
                    fee_delta = self._apply_fee_delta(conn, fill, applied_fee)
                else:
                    delta = 0

            conn.execute(
                """INSERT INTO fills (client_order_id, batch_id, mode, stock_code,
                       side, status, requested_qty, filled_qty, avg_price,
                       qmt_order_id, message, ts, applied_qty, applied_fee)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(client_order_id) DO UPDATE SET
                       status=excluded.status,
                       filled_qty=excluded.filled_qty,
                       avg_price=excluded.avg_price,
                       qmt_order_id=excluded.qmt_order_id,
                       message=excluded.message,
                       ts=excluded.ts,
                       applied_qty=excluded.applied_qty,
                       applied_fee=excluded.applied_fee""",
                (fill.client_order_id, fill.batch_id, fill.mode, fill.stock_code,
                 fill.side, fill.status, fill.requested_qty, fill.filled_qty,
                 fill.avg_price, fill.qmt_order_id, fill.message, fill.ts,
                 applied_qty + delta, applied_fee + fee_delta),
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
        """按本次成交额调整现金（费用另由 _apply_fee_delta 扣减）。"""
        amount = delta * fill.avg_price
        change = amount if fill.side == "SELL" else -amount
        conn.execute(
            "INSERT INTO account_state (key, value) VALUES ('cash', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = value + ?",
            (change, change),
        )

    def _apply_fee_delta(self, conn, fill: FillEvent, applied_fee: float) -> float:
        """按订单累计成交额计费，扣减增量部分；返回本次扣减额（幂等）。

        最低佣金对整个订单只收一次：每次回执重算「订单累计应计费用」，
        与已扣 applied_fee 的差额即本次入账额。
        """
        cum_amount = float(fill.filled_qty) * float(fill.avg_price)
        total_fee = order_total_fee(fill.side, cum_amount, self.fees)
        fee_delta = total_fee - applied_fee
        if fee_delta <= 0:
            return 0.0
        conn.execute(
            "INSERT INTO account_state (key, value) VALUES ('cash', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = value + ?",
            (-fee_delta, -fee_delta),
        )
        return fee_delta

    # ---------- cash_flows ----------

    def record_cash_flow(self, trade_date: str, flow_type: str, amount: float,
                         stock_code: str = None, note: str = "",
                         dedup_key: str = None) -> bool:
        """记录资金流水并同步调整现金。

        Args:
            flow_type: DEPOSIT / WITHDRAW / CORRECTION（外部出入金，日收益剔除）
                       DIVIDEND / DIVIDEND_TAX / BONUS_SHARES（公司行为，计入收益）
            amount: 正数入金、负数出金（BONUS_SHARES 记 0，仅留痕）
            dedup_key: 幂等键；已存在时直接返回 False，不重复入账

        Returns:
            是否实际入账。
        """
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO cash_flows "
                "(trade_date, flow_type, stock_code, amount, note, dedup_key) "
                "VALUES (?,?,?,?,?,?)",
                (trade_date, flow_type, stock_code, amount, note, dedup_key),
            )
            if cur.rowcount == 0:
                return False
            if amount:
                conn.execute(
                    "INSERT INTO account_state (key, value) VALUES ('cash', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = value + ?",
                    (amount, amount),
                )
            return True

    def get_cash_flows(self, start: str = None, end: str = None,
                       limit: int = 200) -> list:
        sql = "SELECT * FROM cash_flows"
        conds, params = [], []
        if start:
            conds.append("trade_date >= ?")
            params.append(start)
        if end:
            conds.append("trade_date <= ?")
            params.append(end)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY trade_date DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def sum_external_flows(self, trade_date: str) -> float:
        """当日外部出入金净额（快照日收益剔除用）。"""
        marks = ",".join("?" for _ in EXTERNAL_FLOW_TYPES)
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT COALESCE(SUM(amount), 0) AS s FROM cash_flows "
                f"WHERE trade_date=? AND flow_type IN ({marks})",
                [trade_date, *EXTERNAL_FLOW_TYPES],
            ).fetchone()
            return float(row["s"])

    def sum_fees_by_date(self, trade_date: str) -> float:
        """当日已扣交易费用合计（按 batches.trade_date 关联）。"""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(f.applied_fee), 0) AS s
                   FROM fills f JOIN batches b ON f.batch_id = b.batch_id
                   WHERE b.trade_date=? AND f.mode='LIVE'""",
                (trade_date,),
            ).fetchone()
            return float(row["s"])

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

    def get_batches_by_date(self, trade_date: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM batches WHERE trade_date=? ORDER BY batch_id",
                (trade_date,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_fills_by_dates(self, trade_dates: list) -> list:
        """按 batches.trade_date 关联取回执（监控用）。"""
        if not trade_dates:
            return []
        marks = ",".join("?" for _ in trade_dates)
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT f.* FROM fills f JOIN batches b ON f.batch_id = b.batch_id
                    WHERE b.trade_date IN ({marks}) ORDER BY f.client_order_id""",
                trade_dates,
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

    def apply_bonus_shares(self, stock_code: str, bonus_shares: int) -> bool:
        """送股/转增到账：股数增加、成本摊薄（总成本不变）。无持仓返回 False。"""
        if bonus_shares <= 0:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT shares, avg_cost FROM positions WHERE stock_code=?",
                (stock_code,),
            ).fetchone()
            if not row:
                return False
            new_shares = row["shares"] + bonus_shares
            new_cost = row["shares"] * row["avg_cost"] / new_shares
            conn.execute(
                "UPDATE positions SET shares=?, avg_cost=?, "
                "updated_at=datetime('now', 'localtime') WHERE stock_code=?",
                (new_shares, new_cost, stock_code),
            )
            return True

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
