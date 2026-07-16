"""回执导入：读取 outbound/fills_*.jsonl，入库并维护实盘持仓账簿。

关键规则（设计文档 §5.3/§7.2 定稿）：
- 只处理已有 ``.done`` 标记的回执文件
- ``mode=SIMULATE`` 的回执只入 fills 表，绝不更新 live 持仓
- 持仓按「已应用数量」增量更新，重复导入天然幂等
- 导入完成后回执文件移入 ``archive/``
"""

import json
import logging
import math
import os
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

from live_trading.modules.fees import DEFAULT_FEES, order_total_fee, validate_fees
from live_trading.modules.signal_schema import (
    FillEvent,
    SchemaError,
    TERMINAL_FILL_STATUS,
    compute_checksum,
    validate_fill,
)

logger = logging.getLogger("live_trading.fill_importer")

# 会改变持仓的终态
_POSITION_STATUS = {"FILLED", "PARTIAL"}

# 计入外部出入金（日收益计算时剔除）的流水类型
EXTERNAL_FLOW_TYPES = {"DEPOSIT", "WITHDRAW"}


class LiveRecorder:
    """实盘账簿 SQLite 存储（batches / fills / positions / cash_flows）。"""

    def __init__(self, db_path: str, fees: dict = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fees = dict(DEFAULT_FEES)
        if fees:
            self.fees.update(fees)
        self.fees = validate_fees(self.fees)
        self._backup_legacy_db()
        self._init_db()

    def _backup_legacy_db(self) -> None:
        """首次联合主键迁移前保留一个一致的 SQLite 备份。"""
        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return
        with sqlite3.connect(str(self.db_path)) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fills'"
            ).fetchone()
            if not table:
                return
            pk = [
                row[1] for row in conn.execute("PRAGMA table_info(fills)")
                if row[5]
            ]
            if pk != ["client_order_id"]:
                return
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = self.db_path.with_name(
                f"{self.db_path.name}.pre_hardening_{stamp}.bak"
            )
            with sqlite3.connect(str(backup)) as dst:
                conn.backup(dst)
            logger.info("backed up legacy live db to %s", backup)

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
                    strategy_id TEXT,
                    signal_date TEXT,
                    account_id TEXT,
                    account_type TEXT,
                    order_checksum TEXT,
                    superseded_by TEXT,
                    superseded_at TEXT,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS fills (
                    batch_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
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
                    applied_amount REAL NOT NULL DEFAULT 0,
                    applied_fee REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (batch_id, client_order_id)
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
                    batch_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    instrument_qlib TEXT,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price_type TEXT,
                    limit_price REAL NOT NULL,
                    priority INTEGER,
                    reason TEXT,
                    PRIMARY KEY (batch_id, client_order_id)
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

                CREATE TABLE IF NOT EXISTS corporate_actions (
                    event_key TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    end_date TEXT,
                    record_date TEXT NOT NULL,
                    ex_date TEXT NOT NULL,
                    pay_date TEXT NOT NULL,
                    div_listdate TEXT NOT NULL,
                    entitled_shares INTEGER NOT NULL,
                    cash_div_tax REAL NOT NULL,
                    stk_div REAL NOT NULL,
                    gross_cash REAL NOT NULL,
                    tax_provision REAL NOT NULL,
                    bonus_shares INTEGER NOT NULL,
                    cash_settled INTEGER NOT NULL DEFAULT 0,
                    bonus_settled INTEGER NOT NULL DEFAULT 0,
                    tax_settled INTEGER NOT NULL DEFAULT 0,
                    actual_tax REAL,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_corp_pay_date
                    ON corporate_actions(pay_date, cash_settled);
                CREATE INDEX IF NOT EXISTS idx_corp_list_date
                    ON corporate_actions(div_listdate, bonus_settled);

                CREATE INDEX IF NOT EXISTS idx_fills_batch ON fills(batch_id);
                CREATE INDEX IF NOT EXISTS idx_orders_batch ON signal_orders(batch_id);
            """)
            # 旧库迁移：补批次发布语义与费用列，再迁移订单联合主键。
            batch_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(batches)")
            }
            for col in (
                "strategy_id", "signal_date", "account_id", "account_type",
                "order_checksum", "superseded_by", "superseded_at",
            ):
                if col not in batch_cols:
                    conn.execute(f"ALTER TABLE batches ADD COLUMN {col} TEXT")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(fills)")}
            if "applied_fee" not in cols:
                conn.execute(
                    "ALTER TABLE fills ADD COLUMN applied_fee REAL NOT NULL DEFAULT 0"
                )
            self._migrate_composite_keys(conn)

    @staticmethod
    def _primary_key_columns(conn, table: str) -> list:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [r["name"] for r in sorted(rows, key=lambda r: r["pk"]) if r["pk"]]

    def _migrate_composite_keys(self, conn) -> None:
        if self._primary_key_columns(conn, "fills") == ["client_order_id"]:
            conn.executescript("""
                ALTER TABLE fills RENAME TO fills_legacy;
                CREATE TABLE fills (
                    batch_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
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
                    applied_amount REAL NOT NULL DEFAULT 0,
                    applied_fee REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (batch_id, client_order_id)
                );
                INSERT INTO fills (
                    batch_id, client_order_id, mode, stock_code, side, status,
                    requested_qty, filled_qty, avg_price, qmt_order_id, message,
                    ts, applied_qty, applied_amount, applied_fee
                )
                SELECT batch_id, client_order_id, mode, stock_code, side, status,
                       requested_qty, filled_qty, avg_price, qmt_order_id, message,
                       ts, applied_qty,
                       applied_qty * COALESCE(avg_price, 0), applied_fee
                FROM fills_legacy;
                DROP TABLE fills_legacy;
            """)
        else:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(fills)")}
            if "applied_amount" not in cols:
                conn.execute(
                    "ALTER TABLE fills ADD COLUMN applied_amount "
                    "REAL NOT NULL DEFAULT 0"
                )
                conn.execute(
                    "UPDATE fills SET applied_amount = "
                    "applied_qty * COALESCE(avg_price, 0)"
                )

        if self._primary_key_columns(conn, "signal_orders") == ["client_order_id"]:
            conn.executescript("""
                ALTER TABLE signal_orders RENAME TO signal_orders_legacy;
                CREATE TABLE signal_orders (
                    batch_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    instrument_qlib TEXT,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price_type TEXT,
                    limit_price REAL NOT NULL,
                    priority INTEGER,
                    reason TEXT,
                    PRIMARY KEY (batch_id, client_order_id)
                );
                INSERT INTO signal_orders (
                    batch_id, client_order_id, stock_code, instrument_qlib,
                    side, quantity, price_type, limit_price, priority, reason
                )
                SELECT batch_id, client_order_id, stock_code, instrument_qlib,
                       side, quantity, price_type, limit_price, priority, reason
                FROM signal_orders_legacy;
                DROP TABLE signal_orders_legacy;
            """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_fills_batch ON fills(batch_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_batch ON signal_orders(batch_id)"
        )

    # ---------- batches ----------

    def record_batch(self, batch_id: str, trade_date: str, mode: str,
                     planned_orders: int) -> None:
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,),
            ).fetchone()
            if existing is not None and existing["order_checksum"]:
                if (
                    existing["trade_date"] == trade_date
                    and existing["mode"] == mode
                    and existing["planned_orders"] == planned_orders
                ):
                    return
                raise SchemaError(
                    f"batch {batch_id!r} has an immutable durable plan"
                )
            conn.execute(
                """INSERT INTO batches
                   (batch_id, trade_date, mode, planned_orders) VALUES (?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                       trade_date=excluded.trade_date,
                       mode=excluded.mode,
                       planned_orders=excluded.planned_orders""",
                (batch_id, trade_date, mode, planned_orders),
            )

    def get_batch(self, batch_id: str):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _batch_strategy_key(batch) -> str:
        """Return durable strategy id, falling back to the legacy batch id."""
        strategy_id = batch["strategy_id"]
        if strategy_id:
            return strategy_id
        parts = batch["batch_id"].split("_")
        return "_".join(parts[1:-1]) if len(parts) >= 3 else ""

    def supersede_batch(self, batch_id: str, replacement_batch_id: str) -> bool:
        """Mark one batch as historical while retaining its full audit trail.

        The relationship is deliberately restricted to the same trading
        session, mode and strategy. Replaying the same relationship is
        idempotent; redirecting it to another replacement is rejected.
        """
        if batch_id == replacement_batch_id:
            raise SchemaError("a batch cannot supersede the same batch")

        with self._conn() as conn:
            source = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,),
            ).fetchone()
            if source is None:
                raise SchemaError(f"unknown source batch: {batch_id!r}")
            replacement = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (replacement_batch_id,),
            ).fetchone()
            if replacement is None:
                raise SchemaError(
                    f"unknown replacement batch: {replacement_batch_id!r}"
                )

            if source["superseded_by"]:
                if source["superseded_by"] == replacement_batch_id:
                    return False
                raise SchemaError(
                    f"batch {batch_id!r} already superseded by "
                    f"{source['superseded_by']!r}"
                )
            if replacement["superseded_by"]:
                raise SchemaError(
                    f"replacement batch {replacement_batch_id!r} is superseded"
                )
            if source["trade_date"] != replacement["trade_date"]:
                raise SchemaError("superseding batches must share trade_date")
            if source["mode"] != replacement["mode"]:
                raise SchemaError("superseding batches must share mode")
            if self._batch_strategy_key(source) != self._batch_strategy_key(
                replacement
            ):
                raise SchemaError("superseding batches must share strategy")

            conn.execute(
                """UPDATE batches
                   SET superseded_by=?,
                       superseded_at=datetime('now', 'localtime')
                   WHERE batch_id=?""",
                (replacement_batch_id, batch_id),
            )
            return True

    # ---------- signal_orders（发布时写入，回执前可看执行计划）----------

    def record_orders(self, batch_id: str, orders: list) -> None:
        """写入批次执行计划。orders 为 SignalOrder 或同名字段 dict。

        订单身份是 ``(batch_id, client_order_id)``；不同批次互不覆盖。
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
            durable = conn.execute(
                "SELECT order_checksum FROM batches WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            if durable is not None and durable["order_checksum"]:
                raise SchemaError(
                    f"batch {batch_id!r} has an immutable durable plan"
                )
            conn.execute("DELETE FROM signal_orders WHERE batch_id=?", (batch_id,))
            conn.executemany(
                """INSERT INTO signal_orders
                   (client_order_id, batch_id, stock_code, instrument_qlib,
                    side, quantity, price_type, limit_price, priority, reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def record_publish_plan(
        self, header, orders: list,
    ) -> None:
        """Atomically persist an immutable plan before exposing it to QMT.

        A retry may reuse the exact same plan (for example after a crash between
        the database commit and shared-file publication), but it may never
        replace a plan for an existing batch id.
        """
        batch_id = header.batch_id
        order_checksum = compute_checksum([
            order.to_json_line() for order in orders
        ])
        rows = []
        for order in orders:
            get = (
                order.get if isinstance(order, dict)
                else lambda key, default=None: getattr(order, key, default)
            )
            rows.append((
                batch_id,
                get("client_order_id"),
                get("stock_code"),
                get("instrument_qlib"),
                get("side"),
                int(get("quantity")),
                get("price_type"),
                float(get("limit_price")),
                get("priority"),
                get("reason"),
            ))
        rows.sort(key=lambda row: row[1])

        with self._conn() as conn:
            existing_batch = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,),
            ).fetchone()
            if existing_batch is not None:
                batch_matches = (
                    existing_batch["trade_date"] == header.trade_date
                    and existing_batch["mode"] == header.mode
                    and existing_batch["planned_orders"] == len(rows)
                    and existing_batch["strategy_id"] == header.strategy_id
                    and existing_batch["signal_date"] == header.signal_date
                    and existing_batch["account_id"] == header.account_id
                    and existing_batch["account_type"] == header.account_type
                    and existing_batch["order_checksum"] == order_checksum
                )
                existing_rows = [tuple(row) for row in conn.execute(
                    """SELECT batch_id, client_order_id, stock_code,
                              instrument_qlib, side, quantity, price_type,
                              limit_price, priority, reason
                       FROM signal_orders WHERE batch_id=?
                       ORDER BY client_order_id""",
                    (batch_id,),
                ).fetchall()]
                if not batch_matches or existing_rows != rows:
                    raise SchemaError(
                        f"batch {batch_id!r} conflicts with durable plan"
                    )
                return

            conn.execute(
                """INSERT INTO batches
                   (batch_id, trade_date, mode, planned_orders, strategy_id,
                    signal_date, account_id, account_type, order_checksum)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    batch_id, header.trade_date, header.mode, len(rows),
                    header.strategy_id, header.signal_date, header.account_id,
                    header.account_type, order_checksum,
                ),
            )
            conn.executemany(
                """INSERT INTO signal_orders
                   (batch_id, client_order_id, stock_code, instrument_qlib,
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
            batch = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (fill.batch_id,),
            ).fetchone()
            if batch is None:
                raise SchemaError(f"unknown fill batch_id: {fill.batch_id!r}")
            if fill.mode != batch["mode"]:
                raise SchemaError(
                    f"fill mode mismatch: {fill.mode!r} != {batch['mode']!r}"
                )
            order = conn.execute(
                "SELECT * FROM signal_orders WHERE batch_id=? AND client_order_id=?",
                (fill.batch_id, fill.client_order_id),
            ).fetchone()
            if order is None:
                raise SchemaError(
                    "unknown fill order: "
                    f"{fill.batch_id!r}/{fill.client_order_id!r}"
                )
            if fill.stock_code != order["stock_code"]:
                raise SchemaError(
                    f"fill stock_code mismatch: {fill.stock_code!r} "
                    f"!= {order['stock_code']!r}"
                )
            if fill.side != order["side"]:
                raise SchemaError(
                    f"fill side mismatch: {fill.side!r} != {order['side']!r}"
                )
            if (
                fill.requested_qty <= 0
                or fill.requested_qty > order["quantity"]
                or fill.requested_qty % 100 != 0
            ):
                raise SchemaError(
                    f"fill requested_qty invalid for plan: {fill.requested_qty!r} "
                    f"> planned {order['quantity']!r} or not a whole lot"
                )

            row = conn.execute(
                "SELECT * FROM fills WHERE batch_id=? AND client_order_id=?",
                (fill.batch_id, fill.client_order_id),
            ).fetchone()
            applied_qty = row["applied_qty"] if row else 0
            applied_amount = row["applied_amount"] if row else 0.0
            applied_fee = row["applied_fee"] if row else 0.0
            cumulative_amount = float(fill.filled_qty) * float(fill.avg_price)
            if row is not None:
                for field in ("mode", "stock_code", "side", "requested_qty"):
                    if fill.__dict__[field] != row[field]:
                        raise SchemaError(
                            f"fill {field} changed: {fill.__dict__[field]!r} "
                            f"!= {row[field]!r}"
                        )
                if fill.filled_qty < row["filled_qty"]:
                    raise SchemaError("fill filled_qty cannot decrease")
                if cumulative_amount + 1e-9 < applied_amount:
                    raise SchemaError("fill cumulative amount cannot decrease")

            delta_qty = 0
            delta_amount = 0.0
            fee_delta = 0.0
            if fill.mode == "LIVE" and fill.status in _POSITION_STATUS:
                delta_qty = int(fill.filled_qty) - applied_qty
                delta_amount = cumulative_amount - applied_amount
                if delta_qty < 0 or delta_amount < -1e-9:
                    raise SchemaError("fill applied quantity/amount cannot decrease")
                if delta_qty > 0 or delta_amount > 1e-9:
                    self._apply_position_delta(
                        conn, fill, delta_qty, delta_amount,
                    )
                    self._apply_cash_delta(conn, fill, delta_amount)
                    fee_delta = self._apply_fee_delta(conn, fill, applied_fee)

            conn.execute(
                """INSERT INTO fills (client_order_id, batch_id, mode, stock_code,
                       side, status, requested_qty, filled_qty, avg_price,
                       qmt_order_id, message, ts, applied_qty, applied_amount,
                       applied_fee)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id, client_order_id) DO UPDATE SET
                       status=excluded.status,
                       filled_qty=excluded.filled_qty,
                       avg_price=excluded.avg_price,
                       qmt_order_id=excluded.qmt_order_id,
                       message=excluded.message,
                       ts=excluded.ts,
                       applied_qty=excluded.applied_qty,
                       applied_amount=excluded.applied_amount,
                       applied_fee=excluded.applied_fee""",
                (fill.client_order_id, fill.batch_id, fill.mode, fill.stock_code,
                 fill.side, fill.status, fill.requested_qty, fill.filled_qty,
                 fill.avg_price, fill.qmt_order_id, fill.message, fill.ts,
                 applied_qty + delta_qty, applied_amount + delta_amount,
                 applied_fee + fee_delta),
            )

    @staticmethod
    def _apply_position_delta(
        conn, fill: FillEvent, delta_qty: int, delta_amount: float,
    ) -> None:
        row = conn.execute(
            "SELECT shares, avg_cost FROM positions WHERE stock_code=?",
            (fill.stock_code,),
        ).fetchone()
        old_shares = row["shares"] if row else 0
        old_cost = row["avg_cost"] if row else 0.0

        if fill.side == "BUY":
            new_shares = old_shares + delta_qty
            if new_shares <= 0:
                raise SchemaError("BUY fill did not produce a positive position")
            new_cost = (old_shares * old_cost + delta_amount) / new_shares
        else:  # SELL
            if delta_qty > old_shares:
                raise SchemaError(
                    f"SELL fill quantity {delta_qty} exceeds ledger position "
                    f"{old_shares} for {fill.stock_code}"
                )
            new_shares = old_shares - delta_qty
            new_cost = old_cost

        if new_shares > 0:
            conn.execute(
                "INSERT OR REPLACE INTO positions (stock_code, shares, avg_cost) "
                "VALUES (?,?,?)",
                (fill.stock_code, new_shares, new_cost),
            )
        else:
            conn.execute(
                "DELETE FROM positions WHERE stock_code=?", (fill.stock_code,)
            )

    @staticmethod
    def _apply_cash_delta(conn, fill: FillEvent, delta_amount: float) -> None:
        """按本次成交额调整现金（费用另由 _apply_fee_delta 扣减）。"""
        change = delta_amount if fill.side == "SELL" else -delta_amount
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
            flow_type: DEPOSIT / WITHDRAW（外部出入金，日收益剔除）；
                       CORRECTION / DIVIDEND（投资相关现金变化，计入收益）。
                       DIVIDEND_TAX / BONUS_SHARES 只能走公司行为事务接口。
            amount: 正数入金、负数出金；类型另有符号约束。
            dedup_key: 幂等键；已存在时直接返回 False，不重复入账

        Returns:
            是否实际入账。
        """
        if flow_type not in {"DEPOSIT", "WITHDRAW", "CORRECTION", "DIVIDEND"}:
            raise ValueError(f"flow_type is internal or unsupported: {flow_type!r}")
        amount = float(amount)
        if not math.isfinite(amount):
            raise ValueError("cash flow amount must be finite")
        if flow_type == "DEPOSIT" and amount <= 0:
            raise ValueError("DEPOSIT amount must be positive")
        if flow_type == "WITHDRAW" and amount >= 0:
            raise ValueError("WITHDRAW amount must be negative")
        if flow_type == "CORRECTION" and not note.strip():
            raise ValueError("CORRECTION requires a note")
        if flow_type == "DIVIDEND" and amount <= 0:
            raise ValueError("DIVIDEND amount must be positive")

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

    # ---------- corporate actions ----------

    def accrue_corporate_action(
        self, event: dict, entitled_shares: int, tax_rate: float,
    ) -> bool:
        """Lock record-date entitlement without changing spendable cash."""
        entitled_shares = int(entitled_shares)
        if entitled_shares <= 0:
            return False
        event_key = str(event.get("event_key") or "")
        if not event_key:
            raise ValueError("corporate action event_key is required")
        gross = round(entitled_shares * float(event.get("cash_div_tax") or 0), 2)
        provision = round(gross * float(tax_rate), 2)
        bonus = int(entitled_shares * float(event.get("stk_div") or 0))
        if gross <= 0 and bonus <= 0:
            return False
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO corporate_actions (
                       event_key, stock_code, end_date, record_date, ex_date,
                       pay_date, div_listdate, entitled_shares, cash_div_tax,
                       stk_div, gross_cash, tax_provision, bonus_shares,
                       cash_settled, bonus_settled, tax_settled)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_key, event["stock_code"], event.get("end_date"),
                    event["record_date"], event["ex_date"], event["pay_date"],
                    event["div_listdate"], entitled_shares,
                    float(event.get("cash_div_tax") or 0),
                    float(event.get("stk_div") or 0), gross, provision, bonus,
                    1 if gross <= 0 else 0,
                    1 if bonus <= 0 else 0,
                    1 if provision <= 0 else 0,
                ),
            )
            return cur.rowcount > 0

    def settle_due_corporate_actions(self, date: str) -> list:
        """Move due receivables to cash and due bonus shares to positions."""
        applied = []
        with self._conn() as conn:
            cash_rows = conn.execute(
                """SELECT * FROM corporate_actions
                   WHERE cash_settled=0 AND pay_date<>'' AND pay_date<=?
                   ORDER BY pay_date, event_key""",
                (date,),
            ).fetchall()
            for row in cash_rows:
                amount = float(row["gross_cash"])
                conn.execute(
                    "INSERT INTO account_state (key, value) VALUES ('cash', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = value + ?",
                    (amount, amount),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO cash_flows
                       (trade_date, flow_type, stock_code, amount, note, dedup_key)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        date, "DIVIDEND", row["stock_code"], amount,
                        "record-date entitlement %d shares; gross dividend"
                        % row["entitled_shares"],
                        "DIVPAY_" + row["event_key"],
                    ),
                )
                conn.execute(
                    "UPDATE corporate_actions SET cash_settled=1 WHERE event_key=?",
                    (row["event_key"],),
                )
                applied.append(
                    "DIVIDEND %s +%.2f" % (row["stock_code"], amount)
                )

            bonus_rows = conn.execute(
                """SELECT * FROM corporate_actions
                   WHERE bonus_settled=0 AND div_listdate<>'' AND div_listdate<=?
                   ORDER BY div_listdate, event_key""",
                (date,),
            ).fetchall()
            for row in bonus_rows:
                bonus = int(row["bonus_shares"])
                self._apply_bonus_shares_conn_impl(
                    conn, row["stock_code"], bonus, create_if_missing=True,
                )
                conn.execute(
                    """INSERT OR IGNORE INTO cash_flows
                       (trade_date, flow_type, stock_code, amount, note, dedup_key)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        date, "BONUS_SHARES", row["stock_code"], 0.0,
                        "bonus/listed shares +%d" % bonus,
                        "BONUS_" + row["event_key"],
                    ),
                )
                conn.execute(
                    "UPDATE corporate_actions SET bonus_settled=1 WHERE event_key=?",
                    (row["event_key"],),
                )
                applied.append(
                    "BONUS_SHARES %s +%d股" % (row["stock_code"], bonus)
                )
        return applied

    def get_corporate_balances(self) -> dict:
        with self._conn() as conn:
            totals = conn.execute(
                """SELECT
                       COALESCE(SUM(CASE WHEN cash_settled=0 THEN gross_cash ELSE 0 END), 0)
                           AS receivables,
                       COALESCE(SUM(CASE WHEN tax_settled=0 THEN tax_provision ELSE 0 END), 0)
                           AS tax_provision
                   FROM corporate_actions"""
            ).fetchone()
            rows = conn.execute(
                """SELECT stock_code, SUM(bonus_shares) AS shares
                   FROM corporate_actions WHERE bonus_settled=0
                   GROUP BY stock_code HAVING SUM(bonus_shares)>0"""
            ).fetchall()
            return {
                "receivables": float(totals["receivables"]),
                "tax_provision": float(totals["tax_provision"]),
                "pending_shares": {r["stock_code"]: int(r["shares"]) for r in rows},
            }

    def get_corporate_actions(self, limit: int = 100) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM corporate_actions
                   ORDER BY ex_date DESC, event_key DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def settle_dividend_tax(
        self, event_key: str, date: str, actual_tax: float,
    ) -> bool:
        """Apply the broker's actual tax debit and release its provision."""
        actual_tax = float(actual_tax)
        if not math.isfinite(actual_tax) or actual_tax < 0:
            raise ValueError("actual_tax must be a finite non-negative number")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM corporate_actions WHERE event_key=?",
                (event_key,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown corporate action: {event_key!r}")
            if row["tax_settled"]:
                return False
            if actual_tax:
                conn.execute(
                    "INSERT INTO account_state (key, value) VALUES ('cash', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = value + ?",
                    (-actual_tax, -actual_tax),
                )
            conn.execute(
                """INSERT INTO cash_flows
                   (trade_date, flow_type, stock_code, amount, note, dedup_key)
                   VALUES (?,?,?,?,?,?)""",
                (
                    date, "DIVIDEND_TAX", row["stock_code"], -actual_tax,
                    "actual broker tax %.2f; released provision %.2f"
                    % (actual_tax, row["tax_provision"]),
                    "DIVTAX_" + event_key,
                ),
            )
            conn.execute(
                """UPDATE corporate_actions
                   SET tax_settled=1, actual_tax=? WHERE event_key=?""",
                (actual_tax, event_key),
            )
            return True

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

    def reprice_fees_by_date(self, trade_date: str) -> float:
        """按当前费率重算当日已入账费用，并同步修正现金。

        返回新费用减旧费用的差额；负数表示费率下降、现金应退回。
        每次均以已应用成交额重算，重复调用不会重复调整。
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT f.batch_id, f.client_order_id, f.side,
                          f.applied_amount, f.applied_fee
                   FROM fills f JOIN batches b ON f.batch_id = b.batch_id
                   WHERE b.trade_date=? AND f.mode='LIVE'
                         AND f.applied_amount > 0""",
                (trade_date,),
            ).fetchall()
            total_delta = 0.0
            for row in rows:
                target = order_total_fee(
                    row["side"], row["applied_amount"], self.fees,
                )
                delta = target - float(row["applied_fee"])
                if abs(delta) <= 1e-9:
                    continue
                conn.execute(
                    """UPDATE fills SET applied_fee=?
                       WHERE batch_id=? AND client_order_id=?""",
                    (target, row["batch_id"], row["client_order_id"]),
                )
                total_delta += delta
            if abs(total_delta) > 1e-9:
                cash = conn.execute(
                    "SELECT value FROM account_state WHERE key='cash'",
                ).fetchone()
                if cash is None:
                    raise SchemaError("cannot reprice fees before cash is initialized")
                conn.execute(
                    "UPDATE account_state SET value = value - ? WHERE key='cash'",
                    (total_delta,),
                )
            return total_delta

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
                "SELECT * FROM batches "
                "ORDER BY trade_date DESC, batch_id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_batches_by_date(self, trade_date: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM batches WHERE trade_date=? ORDER BY batch_id",
                (trade_date,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_active_batches_by_date(self, trade_date: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM batches
                   WHERE trade_date=? AND superseded_by IS NULL
                   ORDER BY batch_id""",
                (trade_date,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_active_batch(self, mode: str = None):
        query = "SELECT * FROM batches WHERE superseded_by IS NULL"
        params = []
        if mode is not None:
            query += " AND mode=?"
            params.append(mode)
        query += " ORDER BY trade_date DESC, batch_id DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

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
            return self._apply_bonus_shares_conn(conn, stock_code, bonus_shares)

    @staticmethod
    def _apply_bonus_shares_conn(conn, stock_code: str, bonus_shares: int) -> bool:
        return LiveRecorder._apply_bonus_shares_conn_impl(
            conn, stock_code, bonus_shares, create_if_missing=False,
        )

    @staticmethod
    def _apply_bonus_shares_conn_impl(
        conn, stock_code: str, bonus_shares: int, create_if_missing: bool,
    ) -> bool:
        if bonus_shares <= 0:
            return False
        row = conn.execute(
            "SELECT shares, avg_cost FROM positions WHERE stock_code=?",
            (stock_code,),
        ).fetchone()
        if row:
            new_shares = row["shares"] + bonus_shares
            new_cost = row["shares"] * row["avg_cost"] / new_shares
            conn.execute(
                "UPDATE positions SET shares=?, avg_cost=?, "
                "updated_at=datetime('now', 'localtime') WHERE stock_code=?",
                (new_shares, new_cost, stock_code),
            )
        elif create_if_missing:
            # Entitlement survives an ex-date sale. With no remaining listed
            # position, create the listed bonus shares at zero carried cost.
            conn.execute(
                "INSERT INTO positions (stock_code, shares, avg_cost) VALUES (?,?,0)",
                (stock_code, bonus_shares),
            )
        else:
            return False
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
