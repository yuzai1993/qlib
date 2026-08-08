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

from filelock import FileLock

from live_trading.modules.fees import DEFAULT_FEES, order_total_fee, validate_fees
from live_trading.modules.execution_state import (
    get_execution_state as _get_execution_state,
    set_execution_state as _set_execution_state,
)
from live_trading.modules.signal_schema import (
    FillEvent,
    SchemaError,
    TERMINAL_FILL_STATUS,
    VALID_ACCOUNT_ENVIRONMENTS,
    compute_checksum,
    validate_fill,
)

logger = logging.getLogger("live_trading.fill_importer")

# 会改变持仓的终态
_POSITION_STATUS = {"FILLED", "PARTIAL"}

# 计入外部出入金（日收益计算时剔除）的流水类型
EXTERNAL_FLOW_TYPES = {"DEPOSIT", "WITHDRAW"}

OPERATOR_PROBE_STRATEGY_ID = "csi1000_pr49_one_lot_probe"


def _mask_account_id(account_id: str | None) -> str:
    value = str(account_id or "")
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


class LiveRecorder:
    """实盘账簿 SQLite 存储（batches / fills / positions / cash_flows）。"""

    def __init__(
        self,
        db_path: str,
        fees: dict = None,
        opening_cash: float | None = None,
        opening_value_adjustment: float | None = None,
        read_only: bool = False,
    ):
        self.db_path = Path(db_path)
        self.read_only = read_only
        if self.read_only:
            if not self.db_path.is_file():
                raise SchemaError(
                    f"live ledger unavailable for read-only access: {self.db_path}"
                )
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fees = dict(DEFAULT_FEES)
        if fees:
            self.fees.update(fees)
        self.fees = validate_fees(self.fees)
        if not self.read_only:
            self._backup_legacy_db()
            self._init_db()
            self._seed_opening_cash(opening_cash)
            self._seed_opening_value_adjustment(opening_value_adjustment)

    def _seed_opening_cash(self, opening_cash: float | None) -> None:
        if opening_cash is None:
            return
        if (
            isinstance(opening_cash, bool)
            or not isinstance(opening_cash, (int, float))
            or not math.isfinite(opening_cash)
            or opening_cash <= 0
        ):
            raise ValueError("opening_cash must be a positive finite number")
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT value FROM account_state WHERE key='cash'"
            ).fetchone()
            if existing is not None:
                return
            used = sum(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("batches", "fills", "positions")
            )
            if used:
                raise SchemaError(
                    "opening_cash cannot seed an already-used live ledger"
                )
            conn.execute(
                "INSERT INTO account_state (key, value) VALUES ('cash', ?)",
                (float(opening_cash),),
            )

    def _seed_opening_value_adjustment(
        self, opening_value_adjustment: float | None,
    ) -> None:
        if opening_value_adjustment is None:
            return
        if (
            isinstance(opening_value_adjustment, bool)
            or not isinstance(opening_value_adjustment, (int, float))
            or not math.isfinite(opening_value_adjustment)
        ):
            raise ValueError(
                "opening_value_adjustment must be a finite number"
            )
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT value FROM account_state WHERE key='value_adjustment'"
            ).fetchone()
            if existing is not None:
                return
            used = sum(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("batches", "fills", "positions")
            )
            if used:
                raise SchemaError(
                    "opening_value_adjustment cannot seed an already-used live ledger"
                )
            conn.execute(
                "INSERT INTO account_state (key, value) "
                "VALUES ('value_adjustment', ?)",
                (float(opening_value_adjustment),),
            )

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
        if self.read_only:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True,
            )
        else:
            conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        if not self.read_only:
            conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def probe_snapshot_gate(self):
        """Serialize probe authorization/publication with snapshot imports."""
        lock_path = self.db_path.with_name(
            f"{self.db_path.name}.probe_snapshot.lock"
        )
        with FileLock(str(lock_path)):
            yield

    @contextmanager
    def operator_publish_gate(self):
        """Serialize main operator preflight, durable record, and publication."""
        lock_path = self.db_path.with_name(
            f"{self.db_path.name}.operator_publish.lock"
        )
        with FileLock(str(lock_path)):
            yield

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
                    account_environment TEXT NOT NULL DEFAULT 'SIMULATION',
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
                    opened_trade_date TEXT,
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
                    max_quantity INTEGER NOT NULL DEFAULT 0,
                    target_value REAL NOT NULL DEFAULT 0,
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

                CREATE TABLE IF NOT EXISTS predictions (
                    date TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    score REAL NOT NULL,
                    rank INTEGER NOT NULL,
                    PRIMARY KEY (date, instrument)
                );

                CREATE TABLE IF NOT EXISTS broker_account_snapshot (
                    batch_id TEXT PRIMARY KEY,
                    trade_date TEXT NOT NULL,
                    account_id TEXT,
                    available_cash REAL,
                    total_asset REAL,
                    market_value REAL,
                    frozen_cash REAL,
                    ts TEXT,
                    created_at TEXT DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS broker_position_snapshot (
                    batch_id TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    shares INTEGER NOT NULL,
                    can_use_volume INTEGER,
                    avg_cost REAL,
                    market_value REAL,
                    PRIMARY KEY (batch_id, stock_code)
                );

                CREATE TABLE IF NOT EXISTS broker_snapshot_imports (
                    batch_id TEXT PRIMARY KEY,
                    trade_date TEXT NOT NULL,
                    import_sequence INTEGER NOT NULL UNIQUE,
                    imported_at TEXT NOT NULL,
                    lifecycle_evidence INTEGER NOT NULL DEFAULT 0,
                    evidence_strategy_id TEXT,
                    evidence_purpose TEXT,
                    source_kind TEXT,
                    ordering_trusted INTEGER NOT NULL DEFAULT 0,
                    is_fresh INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS account_snapshot_requests (
                    request_id TEXT PRIMARY KEY,
                    trade_date TEXT NOT NULL,
                    collector_execution_profile TEXT NOT NULL,
                    collector_bridge_root TEXT NOT NULL,
                    requested_for_strategy_id TEXT NOT NULL,
                    evidence_purpose TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    account_type TEXT NOT NULL,
                    account_environment TEXT NOT NULL,
                    account_id_masked TEXT NOT NULL,
                    account_fingerprint TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    request_checksum TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_checksum TEXT,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    imported_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_account_snapshot_request_date
                    ON account_snapshot_requests(trade_date, imported_at);
                CREATE INDEX IF NOT EXISTS idx_broker_snapshot_import_order
                    ON broker_snapshot_imports(
                        trade_date, import_sequence DESC, batch_id DESC
                    );

                CREATE TABLE IF NOT EXISTS execution_state (
                    strategy_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operator_probe_lifecycle (
                    strategy_id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    buy_batch_id TEXT NOT NULL,
                    buy_trade_date TEXT NOT NULL,
                    sell_batch_id TEXT,
                    sell_trade_date TEXT,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'BUY_PLANNED', 'BUY_FILLED', 'SELL_PLANNED',
                            'CLOSED', 'FAILED'
                        )
                    ),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_fills_batch ON fills(batch_id);
                CREATE INDEX IF NOT EXISTS idx_orders_batch ON signal_orders(batch_id);
                CREATE INDEX IF NOT EXISTS idx_broker_acct_date
                    ON broker_account_snapshot(trade_date);
                CREATE INDEX IF NOT EXISTS idx_broker_pos_date
                    ON broker_position_snapshot(trade_date);
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
            if "account_environment" not in batch_cols:
                conn.execute(
                    "ALTER TABLE batches ADD COLUMN account_environment "
                    "TEXT NOT NULL DEFAULT 'SIMULATION'"
                )
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(fills)")}
            if "applied_fee" not in cols:
                conn.execute(
                    "ALTER TABLE fills ADD COLUMN applied_fee REAL NOT NULL DEFAULT 0"
                )
            position_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(positions)")
            }
            if "opened_trade_date" not in position_cols:
                conn.execute(
                    "ALTER TABLE positions ADD COLUMN opened_trade_date TEXT"
                )
            order_cols = {
                r["name"] for r in conn.execute("PRAGMA table_info(signal_orders)")
            }
            if "target_value" not in order_cols:
                conn.execute(
                    "ALTER TABLE signal_orders ADD COLUMN target_value "
                    "REAL NOT NULL DEFAULT 0"
                )
            if "max_quantity" not in order_cols:
                conn.execute(
                    "ALTER TABLE signal_orders ADD COLUMN max_quantity "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            snapshot_import_cols = {
                r["name"] for r in conn.execute(
                    "PRAGMA table_info(broker_snapshot_imports)"
                )
            }
            if "lifecycle_evidence" not in snapshot_import_cols:
                conn.execute(
                    "ALTER TABLE broker_snapshot_imports ADD COLUMN "
                    "lifecycle_evidence INTEGER NOT NULL DEFAULT 0"
                )
            if "ordering_trusted" not in snapshot_import_cols:
                conn.execute(
                    "ALTER TABLE broker_snapshot_imports ADD COLUMN "
                    "ordering_trusted INTEGER NOT NULL DEFAULT 0"
                )
            if "is_fresh" not in snapshot_import_cols:
                conn.execute(
                    "ALTER TABLE broker_snapshot_imports ADD COLUMN "
                    "is_fresh INTEGER NOT NULL DEFAULT 0"
                )
            for column in (
                "evidence_strategy_id", "evidence_purpose", "source_kind",
            ):
                if column not in snapshot_import_cols:
                    conn.execute(
                        f"ALTER TABLE broker_snapshot_imports ADD COLUMN "
                        f"{column} TEXT"
                    )
            # Repeat this classification on every startup so an interrupted
            # three-column DDL upgrade can resume. Successful legacy rows are
            # normalized with a ``legacy:`` prefix below and cannot be
            # mistaken for Round-1 fresh-import timestamps on the next run.
            candidates = conn.execute(
                """SELECT batch_id, imported_at
                     FROM broker_snapshot_imports
                    WHERE is_fresh=0 AND ordering_trusted=0
                      AND imported_at GLOB
                          '????-??-??T??:??:??.???'"""
            ).fetchall()
            for candidate in candidates:
                try:
                    datetime.strptime(
                        candidate["imported_at"],
                        "%Y-%m-%dT%H:%M:%S.%f",
                    )
                except ValueError:
                    continue
                conn.execute(
                    """UPDATE broker_snapshot_imports
                          SET is_fresh=1, ordering_trusted=1
                        WHERE batch_id=?""",
                    (candidate["batch_id"],),
                )
            self._backfill_broker_snapshot_imports(conn)
            self._migrate_composite_keys(conn)

    @staticmethod
    def _backfill_broker_snapshot_imports(conn) -> None:
        """Recover only chronology supported by legacy observation times."""
        sequence = conn.execute(
            "SELECT COALESCE(MAX(import_sequence), 0) AS value "
            "FROM broker_snapshot_imports"
        ).fetchone()["value"]
        rows = conn.execute(
            """SELECT batch_id, trade_date FROM (
                   SELECT batch_id, trade_date FROM broker_account_snapshot
                   UNION
                   SELECT batch_id, trade_date FROM broker_position_snapshot
               ) AS snapshots
               WHERE NOT EXISTS (
                   SELECT 1 FROM broker_snapshot_imports imports
                   WHERE imports.batch_id=snapshots.batch_id
               )"""
        ).fetchall()
        for row in rows:
            sequence += 1
            conn.execute(
                """INSERT INTO broker_snapshot_imports
                   (batch_id, trade_date, import_sequence, imported_at,
                    lifecycle_evidence, ordering_trusted, is_fresh)
                   VALUES (?,?,?,'',0,0,0)""",
                (row["batch_id"], row["trade_date"], sequence),
            )

        markers = conn.execute(
            """SELECT i.batch_id, i.trade_date, i.import_sequence,
                      i.imported_at, i.is_fresh,
                      a.account_id, a.ts, a.created_at,
                      b.account_id AS durable_account_id,
                      b.account_environment
                 FROM broker_snapshot_imports i
                 LEFT JOIN broker_account_snapshot a
                        ON a.batch_id=i.batch_id
                 LEFT JOIN batches b ON b.batch_id=i.batch_id"""
        ).fetchall()

        legacy = []
        fresh = []
        for ordinal, row in enumerate(markers):
            item = {
                "row": row,
                "ordinal": ordinal,
                "timestamp": None,
                "timestamp_text": "",
                "ordering_trusted": False,
            }
            if row["is_fresh"]:
                fresh.append(item)
                continue
            for value in (row["ts"], row["created_at"]):
                if not isinstance(value, str) or not value.strip():
                    continue
                try:
                    parsed = datetime.fromisoformat(
                        value.strip().replace("Z", "+00:00")
                    )
                    timestamp = parsed.timestamp()
                except (ValueError, OverflowError, OSError):
                    continue
                if parsed.date().isoformat() != row["trade_date"]:
                    continue
                item["timestamp"] = timestamp
                item["timestamp_text"] = parsed.isoformat()
                break
            legacy.append(item)

        counts = {}
        for item in legacy:
            timestamp = item["timestamp"]
            if timestamp is not None:
                key = (item["row"]["trade_date"], timestamp)
                counts[key] = counts.get(key, 0) + 1
        for item in legacy:
            timestamp = item["timestamp"]
            if timestamp is not None:
                item["ordering_trusted"] = counts[
                    (item["row"]["trade_date"], timestamp)
                ] == 1

        ordered_legacy = sorted(
            legacy,
            key=lambda item: (
                item["row"]["trade_date"],
                item["timestamp"] is None,
                item["timestamp"] if item["timestamp"] is not None else 0,
                item["row"]["import_sequence"],
                item["ordinal"],
            ),
        )
        ordered_fresh = sorted(
            fresh, key=lambda item: item["row"]["import_sequence"],
        )
        if markers:
            offset = max(abs(int(row["import_sequence"])) for row in markers) + 1
            conn.execute(
                "UPDATE broker_snapshot_imports "
                "SET import_sequence=-(import_sequence + ?)",
                (offset,),
            )
        for new_sequence, item in enumerate(
            ordered_legacy + ordered_fresh, start=1,
        ):
            row = item["row"]
            durable_account = str(row["durable_account_id"] or "")
            stored_account = str(row["account_id"] or "")
            lifecycle_evidence = int(
                row["account_environment"] == "REAL"
                and bool(durable_account)
                and stored_account in {
                    durable_account,
                    _mask_account_id(durable_account),
                }
            )
            imported_at = (
                row["imported_at"]
                if row["is_fresh"]
                else f"legacy:{item['timestamp_text'] or 'ambiguous'}"
            )
            conn.execute(
                """UPDATE broker_snapshot_imports
                      SET import_sequence=?, imported_at=?,
                          lifecycle_evidence=?, ordering_trusted=?
                    WHERE batch_id=?""",
                (
                    new_sequence,
                    imported_at,
                    lifecycle_evidence,
                    int(row["is_fresh"] or item["ordering_trusted"]),
                    row["batch_id"],
                ),
            )

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
                    max_quantity INTEGER NOT NULL DEFAULT 0,
                    target_value REAL NOT NULL DEFAULT 0,
                    price_type TEXT,
                    limit_price REAL NOT NULL,
                    priority INTEGER,
                    reason TEXT,
                    PRIMARY KEY (batch_id, client_order_id)
                );
                INSERT INTO signal_orders (
                    batch_id, client_order_id, stock_code, instrument_qlib,
                    side, quantity, max_quantity, target_value, price_type, limit_price,
                    priority, reason
                )
                SELECT batch_id, client_order_id, stock_code, instrument_qlib,
                       side, quantity, 0, 0, price_type, limit_price, priority, reason
                FROM signal_orders_legacy;
                DROP TABLE signal_orders_legacy;
            """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_fills_batch ON fills(batch_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_batch ON signal_orders(batch_id)"
        )

    # ---------- batches ----------

    def record_batch(
        self,
        batch_id: str,
        trade_date: str,
        mode: str,
        planned_orders: int,
        account_environment: str = "SIMULATION",
    ) -> None:
        if account_environment not in VALID_ACCOUNT_ENVIRONMENTS:
            raise SchemaError(
                "account_environment must be SIMULATION or REAL"
            )
        if account_environment == "REAL" and mode != "LIVE":
            raise SchemaError("REAL account_environment requires LIVE mode")
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,),
            ).fetchone()
            if existing is not None and existing["order_checksum"]:
                if (
                    existing["trade_date"] == trade_date
                    and existing["mode"] == mode
                    and existing["planned_orders"] == planned_orders
                    and existing["account_environment"] == account_environment
                ):
                    return
                raise SchemaError(
                    f"batch {batch_id!r} has an immutable durable plan"
                )
            conn.execute(
                """INSERT INTO batches
                   (batch_id, trade_date, mode, planned_orders,
                    account_environment) VALUES (?,?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                       trade_date=excluded.trade_date,
                       mode=excluded.mode,
                       planned_orders=excluded.planned_orders,
                       account_environment=excluded.account_environment""",
                (
                    batch_id, trade_date, mode, planned_orders,
                    account_environment,
                ),
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

    def promote_shadow_batch(
        self, source_batch_id: str, replacement_batch_id: str,
    ) -> bool:
        """Replace one unexecuted SIMULATE plan with a same-session LIVE plan.

        This is intentionally narrower than :meth:`supersede_batch`.  It is
        only for the controlled one-lot promotion path and never accepts a
        source batch that has produced any fill event, including SKIPPED.
        """
        if source_batch_id == replacement_batch_id:
            raise SchemaError("a batch cannot promote to the same batch")

        with self._conn() as conn:
            source = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (source_batch_id,),
            ).fetchone()
            if source is None:
                raise SchemaError(
                    f"unknown source batch: {source_batch_id!r}"
                )
            replacement = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?",
                (replacement_batch_id,),
            ).fetchone()
            if replacement is None:
                raise SchemaError(
                    f"unknown replacement batch: {replacement_batch_id!r}"
                )

            if source["superseded_by"]:
                if source["superseded_by"] == replacement_batch_id:
                    return False
                raise SchemaError(
                    f"batch {source_batch_id!r} already superseded by "
                    f"{source['superseded_by']!r}"
                )
            if replacement["superseded_by"]:
                raise SchemaError(
                    f"replacement batch {replacement_batch_id!r} is superseded"
                )

            fill_count = conn.execute(
                "SELECT COUNT(*) FROM fills WHERE batch_id=?",
                (source_batch_id,),
            ).fetchone()[0]
            if source["mode"] != "SIMULATE" or fill_count:
                raise SchemaError(
                    "promotion source must be an unexecuted SIMULATE batch"
                )
            if replacement["mode"] != "LIVE":
                raise SchemaError("promotion replacement must be LIVE")
            if source["trade_date"] != replacement["trade_date"]:
                raise SchemaError("promoted batches must share trade_date")
            if self._batch_strategy_key(source) != self._batch_strategy_key(
                replacement
            ):
                raise SchemaError("promoted batches must share strategy")

            conn.execute(
                """UPDATE batches
                   SET superseded_by=?,
                       superseded_at=datetime('now', 'localtime')
                   WHERE batch_id=?""",
                (replacement_batch_id, source_batch_id),
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
                int(get("max_quantity", 0) or 0),
                float(get("target_value", 0.0)),
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
                    side, quantity, max_quantity, target_value, price_type, limit_price,
                    priority, reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    @staticmethod
    def _publish_plan_values(header, orders: list) -> tuple[str, list]:
        if header.account_environment not in VALID_ACCOUNT_ENVIRONMENTS:
            raise SchemaError(
                "account_environment must be SIMULATION or REAL"
            )
        if header.account_environment == "REAL" and header.mode != "LIVE":
            raise SchemaError("REAL account_environment requires LIVE mode")
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
                int(get("max_quantity", 0) or 0),
                float(get("target_value", 0.0)),
                get("price_type"),
                float(get("limit_price")),
                get("priority"),
                get("reason"),
            ))
        rows.sort(key=lambda row: row[1])
        return order_checksum, rows

    @staticmethod
    def _require_exact_publish_plan_conn(
        conn, header, order_checksum: str, rows: list,
    ) -> bool:
        existing_batch = conn.execute(
            "SELECT * FROM batches WHERE batch_id=?", (header.batch_id,),
        ).fetchone()
        if existing_batch is None:
            return False
        batch_matches = (
            existing_batch["trade_date"] == header.trade_date
            and existing_batch["mode"] == header.mode
            and existing_batch["planned_orders"] == len(rows)
            and existing_batch["strategy_id"] == header.strategy_id
            and existing_batch["signal_date"] == header.signal_date
            and existing_batch["account_id"] == header.account_id
            and existing_batch["account_type"] == header.account_type
            and existing_batch["account_environment"]
            == header.account_environment
            and existing_batch["order_checksum"] == order_checksum
        )
        existing_rows = [tuple(row) for row in conn.execute(
            """SELECT batch_id, client_order_id, stock_code,
                      instrument_qlib, side, quantity, max_quantity,
                      target_value, price_type, limit_price, priority, reason
               FROM signal_orders WHERE batch_id=?
               ORDER BY client_order_id""",
            (header.batch_id,),
        ).fetchall()]
        if not batch_matches or existing_rows != rows:
            raise SchemaError(
                f"batch {header.batch_id!r} conflicts with durable plan"
            )
        return True

    def record_publish_plan(
        self,
        header,
        orders: list,
        probe_transition: dict | None = None,
        required_execution_state: str | None = None,
        exclusive_same_day_live: bool = False,
    ) -> None:
        """Atomically persist an immutable plan before exposing it to QMT.

        A retry may reuse the exact same plan (for example after a crash between
        the database commit and shared-file publication), but it may never
        replace a plan for an existing batch id.
        """
        order_checksum, rows = self._publish_plan_values(header, orders)
        batch_id = header.batch_id

        with self._conn() as conn:
            if required_execution_state is not None or exclusive_same_day_live:
                # Serialize the final state/date gate with every competing
                # writer. A publisher that passed an earlier read-only gate
                # cannot record after an operator pauses the strategy.
                conn.execute("BEGIN IMMEDIATE")
            if required_execution_state is not None:
                current_state = _get_execution_state(
                    conn, header.strategy_id,
                )["state"]
                if current_state != required_execution_state:
                    raise SchemaError(
                        "publish execution state changed: required "
                        f"{required_execution_state}, found {current_state}"
                    )
            if exclusive_same_day_live:
                conflict = conn.execute(
                    """SELECT batch_id FROM batches
                         WHERE trade_date=? AND strategy_id=? AND mode='LIVE'
                           AND superseded_by IS NULL AND batch_id<>?
                         ORDER BY batch_id LIMIT 1""",
                    (header.trade_date, header.strategy_id, header.batch_id),
                ).fetchone()
                if conflict is not None:
                    raise SchemaError(
                        "same-day LIVE batch blocks exclusive main SELL: "
                        + conflict["batch_id"]
                    )
            if self._require_exact_publish_plan_conn(
                conn, header, order_checksum, rows,
            ):
                if probe_transition is not None:
                    self._record_operator_probe_plan_conn(
                        conn, header, rows, probe_transition,
                        durable_retry=True,
                    )
                return

            conn.execute(
                """INSERT INTO batches
                   (batch_id, trade_date, mode, planned_orders, strategy_id,
                    signal_date, account_id, account_type, account_environment,
                    order_checksum)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    batch_id, header.trade_date, header.mode, len(rows),
                    header.strategy_id, header.signal_date, header.account_id,
                    header.account_type, header.account_environment,
                    order_checksum,
                ),
            )
            conn.executemany(
                """INSERT INTO signal_orders
                   (batch_id, client_order_id, stock_code, instrument_qlib,
                    side, quantity, max_quantity, target_value, price_type, limit_price,
                    priority, reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            if probe_transition is not None:
                self._record_operator_probe_plan_conn(
                    conn, header, rows, probe_transition,
                    durable_retry=False,
                )

    def publish_recorded_operator_probe(
        self, header, orders: list, probe_transition: dict, publish_callback,
    ):
        """Publish while holding the writer gate shared with probe imports."""
        order_checksum, rows = self._publish_plan_values(header, orders)
        with self._conn() as conn:
            # SQLite's RESERVED writer lock serializes receipt/snapshot commits
            # with the last lifecycle check and the filesystem publication.
            conn.execute("BEGIN IMMEDIATE")
            if not self._require_exact_publish_plan_conn(
                conn, header, order_checksum, rows,
            ):
                raise SchemaError(
                    f"batch {header.batch_id!r} has no durable publish plan"
                )
            self._require_latest_broker_snapshot_lifecycle_evidence_conn(
                conn, header.trade_date,
            )
            self._record_operator_probe_plan_conn(
                conn, header, rows, probe_transition, durable_retry=True,
            )
            return publish_callback()

    @staticmethod
    def _record_operator_probe_plan_conn(
        conn, header, rows: list, transition: dict, *, durable_retry: bool,
    ) -> None:
        """Persist the probe plan state in the same transaction as its plan."""
        if header.strategy_id != OPERATOR_PROBE_STRATEGY_ID:
            raise SchemaError("probe lifecycle requires the probe strategy")
        if len(rows) != 1:
            raise SchemaError("probe lifecycle requires exactly one order")
        side = rows[0][4]
        stock_code = rows[0][2]
        if transition != {"side": side, "stock_code": stock_code}:
            raise SchemaError("probe lifecycle transition does not match plan")
        lifecycle = conn.execute(
            "SELECT * FROM operator_probe_lifecycle WHERE strategy_id=?",
            (header.strategy_id,),
        ).fetchone()

        if durable_retry:
            lifecycle_batch_id = None
            expected_state = None
            if side == "BUY":
                lifecycle_batch_id = (
                    lifecycle["buy_batch_id"] if lifecycle else None
                )
                expected_state = "BUY_PLANNED"
            elif side == "SELL":
                lifecycle_batch_id = (
                    lifecycle["sell_batch_id"] if lifecycle else None
                )
                expected_state = "SELL_PLANNED"
            if (
                lifecycle_batch_id != header.batch_id
                or lifecycle["state"] != expected_state
            ):
                raise SchemaError(
                    "durable probe retry requires its exact planned lifecycle"
                )
            statuses = sorted(TERMINAL_FILL_STATUS)
            marks = ",".join("?" for _ in statuses)
            terminal = conn.execute(
                f"""SELECT 1 FROM fills
                      WHERE batch_id=? AND status IN ({marks}) LIMIT 1""",
                (header.batch_id, *statuses),
            ).fetchone()
            snapshot = conn.execute(
                "SELECT 1 FROM broker_snapshot_imports WHERE batch_id=?",
                (header.batch_id,),
            ).fetchone()
            if terminal is not None or snapshot is not None:
                raise SchemaError(
                    "durable probe retry rejected after terminal evidence"
                )
            return

        if side == "BUY":
            if lifecycle is not None and lifecycle["state"] not in {"CLOSED", "FAILED"}:
                raise SchemaError(
                    "an operator probe lifecycle is already unresolved"
                )
            conn.execute(
                """INSERT INTO operator_probe_lifecycle
                   (strategy_id, stock_code, buy_batch_id, buy_trade_date,
                    sell_batch_id, sell_trade_date, state, updated_at)
                   VALUES (?,?,?,?,NULL,NULL,'BUY_PLANNED',datetime('now','localtime'))
                   ON CONFLICT(strategy_id) DO UPDATE SET
                       stock_code=excluded.stock_code,
                       buy_batch_id=excluded.buy_batch_id,
                       buy_trade_date=excluded.buy_trade_date,
                       sell_batch_id=NULL,
                       sell_trade_date=NULL,
                       state='BUY_PLANNED',
                       updated_at=datetime('now','localtime')""",
                (
                    header.strategy_id, stock_code, header.batch_id,
                    header.trade_date,
                ),
            )
            return

        if side != "SELL":
            raise SchemaError(f"invalid probe lifecycle side: {side!r}")
        if lifecycle is None or lifecycle["state"] != "BUY_FILLED":
            raise SchemaError("SELL requires a BUY_FILLED probe lifecycle")
        if lifecycle["stock_code"] != stock_code:
            raise SchemaError("SELL probe symbol does not match BUY lifecycle")
        conn.execute(
            """UPDATE operator_probe_lifecycle
                  SET sell_batch_id=?, sell_trade_date=?, state='SELL_PLANNED',
                      updated_at=datetime('now','localtime')
                WHERE strategy_id=?""",
            (header.batch_id, header.trade_date, header.strategy_id),
        )

    def get_operator_probe_lifecycle(
        self, strategy_id: str = OPERATOR_PROBE_STRATEGY_ID,
    ) -> dict | None:
        with self._conn() as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='operator_probe_lifecycle'"
            ).fetchone()
            if table is None:
                return None
            row = conn.execute(
                "SELECT * FROM operator_probe_lifecycle WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_operator_probe_applied_quantity(
        self, batch_id: str, side: str, stock_code: str,
    ) -> int:
        """Return actual applied shares, scoped to one durable probe plan."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(f.applied_qty), 0) AS quantity
                     FROM fills f
                     JOIN batches b ON b.batch_id=f.batch_id
                     JOIN signal_orders o
                       ON o.batch_id=f.batch_id
                      AND o.client_order_id=f.client_order_id
                    WHERE b.strategy_id=? AND f.batch_id=?
                      AND f.side=? AND f.stock_code=?
                      AND o.side=f.side AND o.stock_code=f.stock_code""",
                (
                    OPERATOR_PROBE_STRATEGY_ID, batch_id, side, stock_code,
                ),
            ).fetchone()
            return int(row["quantity"])

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

    # ---------- predictions ----------

    def save_predictions(self, date_str: str, scores) -> int:
        """保存某个 signal_date 的全市场预测分数（覆盖式，幂等）。

        Args:
            scores: {instrument(qlib): score} 映射（dict / pd.Series 均可）。
                    rank 按分数降序计算（1 = 最高分）。

        Returns:
            写入条数。
        """
        items = [
            (str(inst), float(score))
            for inst, score in scores.items()
            if score is not None and math.isfinite(float(score))
        ]
        items.sort(key=lambda kv: (-kv[1], kv[0]))
        rows = [
            (date_str, inst, score, rank)
            for rank, (inst, score) in enumerate(items, start=1)
        ]
        with self._conn() as conn:
            conn.execute("DELETE FROM predictions WHERE date=?", (date_str,))
            conn.executemany(
                "INSERT INTO predictions (date, instrument, score, rank) "
                "VALUES (?,?,?,?)",
                rows,
            )
        return len(rows)

    def get_prediction_dates(self) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT date FROM predictions ORDER BY date DESC"
            ).fetchall()
            return [r["date"] for r in rows]

    def get_predictions_by_date(self, date_str: str) -> dict:
        """{instrument(qlib): {"score": float, "rank": int}}"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT instrument, score, rank FROM predictions WHERE date=?",
                (date_str,),
            ).fetchall()
            return {
                r["instrument"]: {"score": r["score"], "rank": r["rank"]}
                for r in rows
            }

    def get_predictions_search(self, date_str: str = None,
                               instrument: str = None, name: str = None,
                               sort_by: str = "rank", sort_order: str = "asc",
                               limit: int = 50, offset: int = 0):
        """按日期/代码/名称检索预测，返回 (records, total)。"""
        where = []
        params = []
        if date_str:
            where.append("p.date=?")
            params.append(date_str)
        else:
            where.append("p.date=(SELECT MAX(date) FROM predictions)")
        if instrument:
            where.append("p.instrument LIKE ?")
            params.append(f"%{instrument.upper()}%")
        if name:
            where.append("s.name LIKE ?")
            params.append(f"%{name}%")
        where_clause = " AND ".join(where)

        allowed_sort = {
            "rank": "p.rank", "score": "p.score", "instrument": "p.instrument",
        }
        order_col = allowed_sort.get(sort_by, "p.rank")
        order_dir = "DESC" if sort_order.lower() == "desc" else "ASC"

        with self._conn() as conn:
            total = conn.execute(
                f"""SELECT COUNT(*) AS cnt FROM predictions p
                    LEFT JOIN stock_names s ON p.instrument = s.instrument
                    WHERE {where_clause}""",
                params,
            ).fetchone()["cnt"]
            rows = conn.execute(
                f"""SELECT p.date, p.instrument, p.score, p.rank,
                           s.stock_code, s.name
                    FROM predictions p
                    LEFT JOIN stock_names s ON p.instrument = s.instrument
                    WHERE {where_clause}
                    ORDER BY {order_col} {order_dir}
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()
            return [dict(r) for r in rows], total

    def get_prediction_daily_mean(self, instruments: list = None) -> list:
        """每日预测分数均值，可选按标的过滤。"""
        if instruments:
            marks = ",".join("?" for _ in instruments)
            sql = (
                "SELECT date, AVG(score) AS mean_score, COUNT(*) AS count "
                f"FROM predictions WHERE instrument IN ({marks}) "
                "GROUP BY date ORDER BY date"
            )
            params = list(instruments)
        else:
            sql = (
                "SELECT date, AVG(score) AS mean_score, COUNT(*) AS count "
                "FROM predictions GROUP BY date ORDER BY date"
            )
            params = []
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_prediction_extremes(self, date_str: str, n: int = 3) -> dict:
        """某日 top N / bottom N 预测标的（含名称）。"""
        with self._conn() as conn:
            top = conn.execute(
                """SELECT p.instrument, p.score, p.rank, s.stock_code, s.name
                   FROM predictions p
                   LEFT JOIN stock_names s ON p.instrument = s.instrument
                   WHERE p.date=? ORDER BY p.rank ASC LIMIT ?""",
                (date_str, n),
            ).fetchall()
            bottom = conn.execute(
                """SELECT p.instrument, p.score, p.rank, s.stock_code, s.name
                   FROM predictions p
                   LEFT JOIN stock_names s ON p.instrument = s.instrument
                   WHERE p.date=? ORDER BY p.rank DESC LIMIT ?""",
                (date_str, n),
            ).fetchall()
            return {
                "top": [dict(r) for r in top],
                "bottom": [dict(r) for r in reversed(bottom)],
            }

    def get_prediction_instrument_list(self) -> list:
        """有预测数据的全部标的（供前端联想），含 QMT 代码与名称。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT p.instrument, s.stock_code, s.name
                   FROM predictions p
                   LEFT JOIN stock_names s ON p.instrument = s.instrument
                   ORDER BY p.instrument""",
            ).fetchall()
            return [dict(r) for r in rows]

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
            if fill.requested_qty <= 0 or fill.requested_qty % 100 != 0:
                raise SchemaError(
                    f"fill requested_qty invalid for plan: {fill.requested_qty!r} "
                    "must be a positive whole lot"
                )
            if fill.side == "SELL" and fill.requested_qty > order["quantity"]:
                raise SchemaError(
                    f"SELL requested_qty {fill.requested_qty!r} exceeds "
                    f"planned {order['quantity']!r}"
                )
            if fill.side == "BUY":
                if order["quantity"] != 0 or order["target_value"] <= 0:
                    raise SchemaError("BUY plan must use a positive target_value")
                if (
                    order["max_quantity"] > 0
                    and fill.requested_qty > order["max_quantity"]
                ):
                    raise SchemaError(
                        f"BUY requested_qty {fill.requested_qty!r} exceeds "
                        f"authorized max {order['max_quantity']!r}"
                    )
                fill_gross = float(fill.filled_qty) * float(fill.avg_price)
                if fill_gross > float(order["target_value"]) + 1e-6:
                    raise SchemaError(
                        f"BUY fill gross {fill_gross:.6f} exceeds target_value "
                        f"{order['target_value']:.6f}"
                    )

            row = conn.execute(
                "SELECT * FROM fills WHERE batch_id=? AND client_order_id=?",
                (fill.batch_id, fill.client_order_id),
            ).fetchone()
            if (
                row is not None
                and batch["strategy_id"] == OPERATOR_PROBE_STRATEGY_ID
                and row["status"] in TERMINAL_FILL_STATUS
            ):
                receipt_fields = (
                    "batch_id", "client_order_id", "mode", "stock_code",
                    "side", "status", "requested_qty", "filled_qty",
                    "avg_price", "qmt_order_id", "message", "ts",
                )
                exact_receipt = all(
                    getattr(fill, field) == row[field]
                    for field in receipt_fields
                )
                if exact_receipt:
                    return
                raise SchemaError("terminal probe fill is immutable")
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
                        conn,
                        fill,
                        delta_qty,
                        delta_amount,
                        trade_date=batch["trade_date"],
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
            self._refresh_operator_probe_lifecycle_conn(conn)

    @staticmethod
    def _refresh_operator_probe_lifecycle_conn(conn) -> None:
        """Advance only when terminal applied fills and its snapshot agree."""
        lifecycle = conn.execute(
            "SELECT * FROM operator_probe_lifecycle WHERE strategy_id=?",
            (OPERATOR_PROBE_STRATEGY_ID,),
        ).fetchone()
        if lifecycle is None:
            return

        if lifecycle["state"] == "BUY_PLANNED":
            batch_id = lifecycle["buy_batch_id"]
            side = "BUY"
            success_state = "BUY_FILLED"
            expected_broker_shares = None
        elif lifecycle["state"] == "SELL_PLANNED":
            batch_id = lifecycle["sell_batch_id"]
            side = "SELL"
            success_state = "CLOSED"
            expected_broker_shares = None
        else:
            return

        marks = ",".join("?" for _ in TERMINAL_FILL_STATUS)
        fill = conn.execute(
            f"""SELECT f.applied_qty
                   FROM fills f
                   JOIN batches b ON b.batch_id=f.batch_id
                  WHERE b.strategy_id=? AND f.batch_id=?
                    AND f.side=? AND f.stock_code=?
                    AND f.status IN ({marks})""",
            (
                OPERATOR_PROBE_STRATEGY_ID, batch_id, side,
                lifecycle["stock_code"], *sorted(TERMINAL_FILL_STATUS),
            ),
        ).fetchone()
        if fill is None:
            return
        snapshot = conn.execute(
            """SELECT 1 FROM broker_snapshot_imports
                WHERE batch_id=? AND lifecycle_evidence=1""",
            (batch_id,),
        ).fetchone()
        if snapshot is None:
            return
        broker = conn.execute(
            """SELECT shares FROM broker_position_snapshot
                WHERE batch_id=? AND stock_code=?""",
            (batch_id, lifecycle["stock_code"]),
        ).fetchone()
        broker_shares = int(broker["shares"]) if broker else 0
        applied_qty = int(fill["applied_qty"])
        if side == "BUY":
            expected_broker_shares = applied_qty
        else:
            expected_broker_shares = 100 - applied_qty
        state = (
            success_state
            if applied_qty == 100 and broker_shares == expected_broker_shares
            else "FAILED"
        )
        conn.execute(
            """UPDATE operator_probe_lifecycle
                  SET state=?, updated_at=datetime('now','localtime')
                WHERE strategy_id=?""",
            (state, OPERATOR_PROBE_STRATEGY_ID),
        )

    @staticmethod
    def _apply_position_delta(
        conn,
        fill: FillEvent,
        delta_qty: int,
        delta_amount: float,
        *,
        trade_date: str,
    ) -> None:
        row = conn.execute(
            "SELECT shares, avg_cost, opened_trade_date FROM positions "
            "WHERE stock_code=?",
            (fill.stock_code,),
        ).fetchone()
        old_shares = row["shares"] if row else 0
        old_cost = row["avg_cost"] if row else 0.0
        opened_trade_date = row["opened_trade_date"] if row else None

        if fill.side == "BUY":
            new_shares = old_shares + delta_qty
            if new_shares <= 0:
                raise SchemaError("BUY fill did not produce a positive position")
            new_cost = (old_shares * old_cost + delta_amount) / new_shares
            if old_shares == 0:
                opened_trade_date = trade_date
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
                "INSERT OR REPLACE INTO positions "
                "(stock_code, shares, avg_cost, opened_trade_date) "
                "VALUES (?,?,?,?)",
                (
                    fill.stock_code,
                    new_shares,
                    new_cost,
                    opened_trade_date,
                ),
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
                          f.status, f.applied_amount, f.applied_fee,
                          b.strategy_id
                   FROM fills f JOIN batches b ON f.batch_id = b.batch_id
                   WHERE b.trade_date=? AND f.mode='LIVE'
                         AND f.applied_amount > 0""",
                (trade_date,),
            ).fetchall()
            total_delta = 0.0
            for row in rows:
                if (
                    row["strategy_id"] == OPERATOR_PROBE_STRATEGY_ID
                    and row["status"] in TERMINAL_FILL_STATUS
                ):
                    continue
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

    # ---------- execution state ----------

    def get_execution_state(self, strategy_id: str) -> dict:
        """Return a durable strategy state, defaulting to non-persisted ACTIVE."""
        with self._conn() as conn:
            return _get_execution_state(conn, strategy_id)

    def set_execution_state(
        self,
        strategy_id: str,
        state: str,
        reason: str,
        changed_at: str | None = None,
    ) -> dict:
        """Persist the explicit execution state used by publishers and monitors."""
        if self.read_only:
            raise SchemaError("cannot set execution state through a read-only ledger")
        with self._conn() as conn:
            return _set_execution_state(
                conn, strategy_id, state, reason, changed_at,
            )

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

    def get_value_adjustment(self) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM account_state WHERE key='value_adjustment'"
            ).fetchone()
            return float(row["value"]) if row else 0.0

    def list_batches(
        self, limit: int = 10, strategy_id: str | None = None,
    ) -> list:
        clauses = []
        params = []
        if strategy_id is not None:
            clauses.append("strategy_id=?")
            params.append(strategy_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM batches" + where
                + " ORDER BY trade_date DESC, batch_id DESC LIMIT ?", params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_batches_by_date(
        self, trade_date: str, strategy_id: str | None = None,
    ) -> list:
        clauses = ["trade_date=?"]
        params = [trade_date]
        if strategy_id is not None:
            clauses.append("strategy_id=?")
            params.append(strategy_id)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM batches WHERE "
                + " AND ".join(clauses)
                + " ORDER BY batch_id",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_active_batches_by_date(
        self, trade_date: str, strategy_id: str | None = None,
    ) -> list:
        clauses = ["trade_date=?", "superseded_by IS NULL"]
        params = [trade_date]
        if strategy_id is not None:
            clauses.append("strategy_id=?")
            params.append(strategy_id)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM batches WHERE "
                + " AND ".join(clauses)
                + " ORDER BY batch_id",
                params,
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

    def get_unreconciled_active_live_batches_before(
        self, trade_date: str, strategy_id: str | None = None,
    ) -> list:
        """Return earlier active LIVE batches lacking terminal fill events."""
        statuses = sorted(TERMINAL_FILL_STATUS)
        marks = ",".join("?" for _ in statuses)
        clauses = [
            "b.mode='LIVE'",
            "b.trade_date < ?",
            "b.superseded_by IS NULL",
        ]
        params = [*statuses, trade_date]
        if strategy_id is not None:
            clauses.append("b.strategy_id=?")
            params.append(strategy_id)
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT b.*,
                            COUNT(f.client_order_id) AS terminal_orders
                     FROM batches b
                     LEFT JOIN fills f
                       ON f.batch_id=b.batch_id AND f.status IN ({marks})
                     WHERE {' AND '.join(clauses)}
                     GROUP BY b.batch_id
                     HAVING terminal_orders < b.planned_orders
                     ORDER BY b.trade_date, b.batch_id""",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_failed_live_sells_before(self, trade_date: str) -> list:
        """Return prior LIVE sell intents whose latest terminal result did not fill."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT b.trade_date, o.batch_id, o.client_order_id,
                          o.stock_code, f.status, f.filled_qty, f.message
                     FROM signal_orders o
                     JOIN batches b ON b.batch_id=o.batch_id
                     JOIN fills f ON f.batch_id=o.batch_id
                                 AND f.client_order_id=o.client_order_id
                    WHERE b.mode='LIVE' AND b.trade_date < ?
                      AND b.superseded_by IS NULL AND o.side='SELL'
                      AND f.status IN ('REJECTED','SKIPPED','EXPIRED','ERROR')
                    ORDER BY b.trade_date, o.client_order_id""",
                (trade_date,),
            ).fetchall()
            return [dict(row) for row in rows]

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

    def upsert_position(
        self,
        stock_code: str,
        shares: int,
        avg_cost: float,
        opened_trade_date: str | None = None,
    ) -> None:
        """人工 seed / 校正持仓入口。"""
        with self._conn() as conn:
            if shares > 0:
                conn.execute(
                    """INSERT INTO positions
                       (stock_code, shares, avg_cost, opened_trade_date)
                       VALUES (?,?,?,?)
                       ON CONFLICT(stock_code) DO UPDATE SET
                           shares=excluded.shares,
                           avg_cost=excluded.avg_cost,
                           opened_trade_date=COALESCE(
                               excluded.opened_trade_date,
                               positions.opened_trade_date
                           ),
                           updated_at=datetime('now', 'localtime')""",
                    (stock_code, shares, avg_cost, opened_trade_date),
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
            positions = {}
            for row in rows:
                value = {
                    "shares": row["shares"],
                    "avg_cost": row["avg_cost"],
                }
                if row["opened_trade_date"]:
                    value["opened_trade_date"] = row["opened_trade_date"]
                positions[row["stock_code"]] = value
            return positions

    # ---------- 券商快照（二道对账）----------

    def record_account_snapshot_request(
        self, payload: dict, account_id: str,
    ) -> None:
        """Persist one immutable observation request without a LIVE batch."""
        from live_trading.modules.operator_probe import (
            SNAPSHOT_EVIDENCE_PURPOSE,
            SNAPSHOT_REQUEST_SCHEMA_VERSION,
            account_identity_fingerprint,
            snapshot_artifact_checksum,
        )

        if payload.get("type") != "account_snapshot_request":
            raise SchemaError("invalid account snapshot request type")
        if payload.get("schema_version") != SNAPSHOT_REQUEST_SCHEMA_VERSION:
            raise SchemaError("invalid account snapshot request schema")
        if payload.get("evidence_purpose") != SNAPSHOT_EVIDENCE_PURPOSE:
            raise SchemaError("invalid account snapshot evidence purpose")
        checksum = snapshot_artifact_checksum(payload)
        if payload.get("checksum") != checksum:
            raise SchemaError("account snapshot request checksum mismatch")
        expected_fingerprint = account_identity_fingerprint(
            account_id,
            payload.get("account_type", ""),
            payload.get("account_environment", ""),
        )
        if payload.get("account_fingerprint") != expected_fingerprint:
            raise SchemaError("account snapshot request identity mismatch")
        if payload.get("account_id_masked") != _mask_account_id(account_id):
            raise SchemaError("account snapshot request masked identity mismatch")
        request_json = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        )
        values = (
            payload["request_id"], payload["trade_date"],
            payload["collector_execution_profile"],
            payload["collector_bridge_root"],
            payload["requested_for_strategy_id"], payload["evidence_purpose"],
            account_id, payload["account_type"],
            payload["account_environment"], payload["account_id_masked"],
            payload["account_fingerprint"], payload["schema_version"],
            checksum, request_json, "PREPARED", payload["created_at"],
        )
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM account_snapshot_requests WHERE request_id=?",
                (payload["request_id"],),
            ).fetchone()
            if existing is not None:
                if (
                    existing["request_checksum"] == checksum
                    and existing["request_json"] == request_json
                    and existing["account_id"] == account_id
                ):
                    return
                raise SchemaError("conflicting durable account snapshot request")
            conn.execute(
                """INSERT INTO account_snapshot_requests
                   (request_id, trade_date, collector_execution_profile,
                    collector_bridge_root, requested_for_strategy_id,
                    evidence_purpose, account_id, account_type,
                    account_environment, account_id_masked,
                    account_fingerprint, schema_version, request_checksum,
                    request_json, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )

    def get_account_snapshot_request(self, request_id: str):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM account_snapshot_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def mark_account_snapshot_request_published(
        self, request_id: str, request_checksum: str,
    ) -> None:
        """Commit the durable exposure intent before QMT-visible files."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM account_snapshot_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise SchemaError("unknown prepared account snapshot request")
            if row["request_checksum"] != request_checksum:
                raise SchemaError("prepared account snapshot checksum changed")
            if row["status"] == "REQUESTED":
                return
            if row["status"] != "PREPARED":
                raise SchemaError("account snapshot request is already terminal")
            conn.execute(
                "UPDATE account_snapshot_requests SET status='REQUESTED' "
                "WHERE request_id=?",
                (request_id,),
            )

    def save_account_snapshot_response(
        self, response: dict, *, before_commit=None,
    ) -> bool:
        """Import a bound terminal response; exact replay is a no-op."""
        from live_trading.modules.operator_probe import (
            SNAPSHOT_EVIDENCE_PURPOSE,
            SNAPSHOT_REQUEST_SCHEMA_VERSION,
            account_identity_fingerprint,
            snapshot_artifact_checksum,
        )

        if response.get("type") != "account_snapshot_response":
            raise SchemaError("invalid account snapshot response type")
        if response.get("schema_version") != SNAPSHOT_REQUEST_SCHEMA_VERSION:
            raise SchemaError("invalid account snapshot response schema")
        response_checksum = snapshot_artifact_checksum(response)
        if response.get("checksum") != response_checksum:
            raise SchemaError("account snapshot response checksum mismatch")
        response_json = json.dumps(
            response, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"),
        )
        request_id = response.get("request_id", "")
        with (
            self.probe_snapshot_gate(),
            self.operator_publish_gate(),
            self._conn() as conn,
        ):
            request = conn.execute(
                "SELECT * FROM account_snapshot_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if request is None:
                raise SchemaError("unknown account snapshot request_id")
            if request["response_checksum"] is not None:
                if (
                    request["response_checksum"] == response_checksum
                    and request["response_json"] == response_json
                ):
                    return False
                raise SchemaError("terminal account snapshot response changed")
            if request["status"] != "REQUESTED":
                raise SchemaError("account snapshot response has no published request")
            bindings = {
                "trade_date": request["trade_date"],
                "collector_execution_profile": request[
                    "collector_execution_profile"
                ],
                "collector_bridge_root": request["collector_bridge_root"],
                "requested_for_strategy_id": request[
                    "requested_for_strategy_id"
                ],
                "evidence_purpose": SNAPSHOT_EVIDENCE_PURPOSE,
                "account_type": request["account_type"],
                "account_environment": request["account_environment"],
                "account_id_masked": request["account_id_masked"],
                "account_fingerprint": request["account_fingerprint"],
                "request_checksum": request["request_checksum"],
            }
            for field, expected in bindings.items():
                if response.get(field) != expected:
                    raise SchemaError(
                        f"account snapshot response {field} mismatch"
                    )
            if request["schema_version"] != SNAPSHOT_REQUEST_SCHEMA_VERSION:
                raise SchemaError("durable account snapshot request schema mismatch")
            status = response.get("status")
            if status not in {"COMPLETE", "DIAGNOSTIC_POSITIONS_ONLY", "ERROR"}:
                raise SchemaError("invalid account snapshot response status")
            account = response.get("account")
            positions = response.get("positions")
            if not isinstance(positions, list) or any(
                not isinstance(row, dict) for row in positions
            ):
                raise SchemaError("account snapshot response positions invalid")
            if status == "COMPLETE":
                if not isinstance(account, dict):
                    raise SchemaError("complete account snapshot requires ACCOUNT row")
                if account.get("request_id") != request_id:
                    raise SchemaError("ACCOUNT row request_id mismatch")
                if account.get("account_id_masked") != request["account_id_masked"]:
                    raise SchemaError("ACCOUNT row identity mismatch")
                if account.get("account_fingerprint") != request[
                    "account_fingerprint"
                ]:
                    raise SchemaError("ACCOUNT row fingerprint mismatch")
            elif account is not None:
                raise SchemaError("non-complete response cannot carry ACCOUNT row")
            if status == "ERROR" and positions:
                raise SchemaError("error response cannot carry positions")
            for row in positions:
                if row.get("request_id") != request_id:
                    raise SchemaError("position row request_id mismatch")
                if row.get("trade_date") != request["trade_date"]:
                    raise SchemaError("position response trade_date mismatch")
            source_id = request_id
            conn.execute(
                "DELETE FROM broker_account_snapshot WHERE batch_id=?",
                (source_id,),
            )
            conn.execute(
                "DELETE FROM broker_position_snapshot WHERE batch_id=?",
                (source_id,),
            )
            if status == "COMPLETE":
                conn.execute(
                    """INSERT INTO broker_account_snapshot
                       (batch_id, trade_date, account_id, available_cash,
                        total_asset, market_value, frozen_cash, ts)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        source_id, request["trade_date"], request["account_id"],
                        account.get("available_cash"), account.get("total_asset"),
                        account.get("market_value"), account.get("frozen_cash"),
                        account.get("ts"),
                    ),
                )
            conn.executemany(
                """INSERT INTO broker_position_snapshot
                   (batch_id, trade_date, stock_code, shares, can_use_volume,
                    avg_cost, market_value)
                   VALUES (?,?,?,?,?,?,?)""",
                [(
                    source_id, request["trade_date"], row["stock_code"],
                    int(row["shares"]), row.get("can_use_volume"),
                    row.get("avg_cost"), row.get("market_value"),
                ) for row in positions],
            )
            sequence = conn.execute(
                "SELECT COALESCE(MAX(import_sequence), 0) + 1 AS value "
                "FROM broker_snapshot_imports"
            ).fetchone()["value"]
            conn.execute(
                """INSERT INTO broker_snapshot_imports
                   (batch_id, trade_date, import_sequence, imported_at,
                    lifecycle_evidence, evidence_strategy_id,
                    evidence_purpose, source_kind,
                    ordering_trusted, is_fresh)
                   VALUES (?,?,?,strftime('%Y-%m-%dT%H:%M:%f','now','localtime'),
                           ?,?,?,?,1,1)""",
                (
                    source_id, request["trade_date"], sequence,
                    int(status == "COMPLETE"),
                    request["requested_for_strategy_id"],
                    request["evidence_purpose"],
                    "SNAPSHOT_REQUEST",
                ),
            )
            conn.execute(
                """UPDATE account_snapshot_requests
                      SET status=?, response_checksum=?, response_json=?,
                          imported_at=strftime('%Y-%m-%dT%H:%M:%f','now','localtime')
                    WHERE request_id=?""",
                ("IMPORTED_" + status, response_checksum, response_json, request_id),
            )
            self._refresh_operator_probe_lifecycle_conn(conn)
            if before_commit is not None:
                before_commit()
        return True

    def save_broker_snapshot(self, batch_id: str, account: dict,
                            positions: list) -> None:
        """存券商 ACCOUNT/POSITION 快照（覆盖式，重复导入幂等）。

        Args:
            account: 账户行 dict，或 None（ACCOUNT 查询为空时）
            positions: 持仓行 dict 列表
        """
        with self.probe_snapshot_gate(), self.operator_publish_gate():
            self._save_broker_snapshot_locked(batch_id, account, positions)

    def _save_broker_snapshot_locked(self, batch_id: str, account: dict,
                                     positions: list) -> None:
        with self._conn() as conn:
            batch = conn.execute(
                "SELECT * FROM batches WHERE batch_id=?", (batch_id,),
            ).fetchone()
            if batch is None:
                raise SchemaError(f"unknown snapshot batch_id: {batch_id!r}")
            trade_date = batch["trade_date"]
            for row in ([account] if account is not None else []) + list(positions):
                row_batch_id = row.get("batch_id")
                if row_batch_id is not None and row_batch_id != batch_id:
                    raise SchemaError("snapshot batch_id does not match durable batch")
                row_trade_date = row.get("trade_date")
                if row_trade_date is not None and row_trade_date != trade_date:
                    raise SchemaError(
                        "snapshot trade_date does not match durable batch"
                    )
            normalized_account = None if account is None else dict(account)
            if account is not None and batch["account_environment"] == "REAL":
                durable_account = str(batch["account_id"] or "")
                if not durable_account:
                    raise SchemaError(
                        "REAL snapshot batch requires a durable account_id"
                    )
                full_account = account.get("account_id")
                masked_account = account.get("account_id_masked")
                if full_account is not None and full_account != durable_account:
                    raise SchemaError(
                        "snapshot account_id does not match durable REAL batch"
                    )
                if (
                    masked_account is not None
                    and masked_account != _mask_account_id(durable_account)
                ):
                    raise SchemaError(
                        "masked snapshot account does not match durable REAL batch"
                    )
                if full_account is None and masked_account is None:
                    raise SchemaError(
                        "REAL account snapshot requires trusted account binding"
                    )
                normalized_account["account_id"] = durable_account

            conn.execute(
                "DELETE FROM broker_account_snapshot WHERE batch_id=?",
                (batch_id,),
            )
            if normalized_account is not None:
                conn.execute(
                    """INSERT INTO broker_account_snapshot
                       (batch_id, trade_date, account_id, available_cash,
                        total_asset, market_value, frozen_cash, ts)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(batch_id) DO UPDATE SET
                           account_id=excluded.account_id,
                           available_cash=excluded.available_cash,
                           total_asset=excluded.total_asset,
                           market_value=excluded.market_value,
                           frozen_cash=excluded.frozen_cash,
                           ts=excluded.ts""",
                    (batch_id, trade_date, normalized_account.get("account_id"),
                     normalized_account.get("available_cash"),
                     normalized_account.get("total_asset"),
                     normalized_account.get("market_value"),
                     normalized_account.get("frozen_cash"),
                     normalized_account.get("ts")),
                )

            conn.execute(
                "DELETE FROM broker_position_snapshot WHERE batch_id=?", (batch_id,)
            )
            conn.executemany(
                """INSERT INTO broker_position_snapshot
                   (batch_id, trade_date, stock_code, shares, can_use_volume,
                    avg_cost, market_value)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    (batch_id, trade_date, p["stock_code"], int(p["shares"]),
                     p.get("can_use_volume"), p.get("avg_cost"),
                     p.get("market_value"))
                    for p in positions
                ],
            )
            sequence = conn.execute(
                "SELECT COALESCE(MAX(import_sequence), 0) + 1 AS value "
                "FROM broker_snapshot_imports"
            ).fetchone()["value"]
            conn.execute(
                """INSERT INTO broker_snapshot_imports
                   (batch_id, trade_date, import_sequence, imported_at,
                    lifecycle_evidence, evidence_strategy_id,
                    evidence_purpose, source_kind,
                    ordering_trusted, is_fresh)
                   VALUES (?,?,?,strftime('%Y-%m-%dT%H:%M:%f','now','localtime'),
                           ?,?,?,?,1,1)
                   ON CONFLICT(batch_id) DO UPDATE SET
                       trade_date=excluded.trade_date,
                       import_sequence=excluded.import_sequence,
                       imported_at=excluded.imported_at,
                       lifecycle_evidence=excluded.lifecycle_evidence,
                       evidence_strategy_id=excluded.evidence_strategy_id,
                       evidence_purpose=excluded.evidence_purpose,
                       source_kind=excluded.source_kind,
                       ordering_trusted=1,
                       is_fresh=1""",
                (
                    batch_id,
                    trade_date,
                    sequence,
                    int(
                        normalized_account is not None
                        and batch["account_environment"] == "REAL"
                    ),
                    batch["strategy_id"],
                    "BATCH_RECONCILIATION",
                    "TRADING_BATCH",
                ),
            )
            self._refresh_operator_probe_lifecycle_conn(conn)

    def get_broker_account_snapshot(self, trade_date: str):
        """当日最新批次的券商账户快照；无则 None。"""
        with self._conn() as conn:
            batch_id = self._latest_broker_snapshot_batch_id(conn, trade_date)
            if batch_id is None:
                return None
            row = conn.execute(
                "SELECT * FROM broker_account_snapshot WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _latest_broker_snapshot_batch_id(conn, trade_date: str):
        columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(broker_snapshot_imports)"
            ).fetchall()
        }
        if not {"is_fresh", "ordering_trusted"}.issubset(columns):
            raise SchemaError(
                "broker snapshot metadata requires writable migration"
            )
        row = conn.execute(
            """SELECT batch_id FROM broker_snapshot_imports
                WHERE trade_date=? AND is_fresh=1 AND ordering_trusted=1
                ORDER BY import_sequence DESC LIMIT 1""",
            (trade_date,),
        ).fetchone()
        if row is not None:
            return row["batch_id"]
        ambiguous = conn.execute(
            """SELECT 1 FROM broker_snapshot_imports
                WHERE trade_date=? AND ordering_trusted=0 LIMIT 1""",
            (trade_date,),
        ).fetchone()
        if ambiguous is not None:
            return None
        row = conn.execute(
            """SELECT batch_id FROM broker_snapshot_imports
                WHERE trade_date=? AND ordering_trusted=1
                ORDER BY import_sequence DESC LIMIT 1""",
            (trade_date,),
        ).fetchone()
        return row["batch_id"] if row is not None else None

    @classmethod
    def _require_latest_broker_snapshot_lifecycle_evidence_conn(
        cls, conn, trade_date: str, evidence_strategy_id: str | None = None,
    ) -> str:
        batch_id = cls._latest_broker_snapshot_batch_id(conn, trade_date)
        if batch_id is None:
            raise SchemaError(
                f"broker position snapshot missing for {trade_date}"
            )
        evidence = conn.execute(
            """SELECT lifecycle_evidence, evidence_strategy_id
                 FROM broker_snapshot_imports WHERE batch_id=?""",
            (batch_id,),
        ).fetchone()
        if evidence is None or evidence["lifecycle_evidence"] != 1:
            raise SchemaError(
                "latest broker snapshot lacks matched REAL "
                f"ACCOUNT evidence for {trade_date}"
            )
        if (
            evidence_strategy_id is not None
            and evidence["evidence_strategy_id"] != evidence_strategy_id
        ):
            raise SchemaError(
                "latest broker snapshot ACCOUNT evidence belongs to another "
                f"strategy for {trade_date}"
            )
        return batch_id

    def get_broker_positions(self, trade_date: str) -> dict:
        """当日最新批次的券商持仓 {stock_code: shares}；无快照则空 dict。"""
        with self._conn() as conn:
            batch_id = self._latest_broker_snapshot_batch_id(conn, trade_date)
            if batch_id is None:
                return {}
            rows = conn.execute(
                "SELECT stock_code, shares FROM broker_position_snapshot "
                "WHERE batch_id=?",
                (batch_id,),
            ).fetchall()
            return {r["stock_code"]: r["shares"] for r in rows}

    def get_broker_position_details(
        self,
        trade_date: str,
        *,
        require_lifecycle_evidence: bool = False,
        evidence_strategy_id: str | None = None,
    ) -> dict[str, dict]:
        """Return the complete latest broker position snapshot for a date.

        An absent snapshot is an operational data failure.  Returning an empty
        mapping would make a stale or failed QMT account query look like an
        account with no positions, which is unsafe for operator-created orders.
        An empty *present* snapshot remains a valid empty mapping.

        Probe authorization can additionally require the latest snapshot's
        durable REAL ACCOUNT binding without changing diagnostic consumers.
        """
        with self._conn() as conn:
            if require_lifecycle_evidence:
                batch_id = (
                    self._require_latest_broker_snapshot_lifecycle_evidence_conn(
                        conn, trade_date, evidence_strategy_id,
                    )
                )
            else:
                batch_id = self._latest_broker_snapshot_batch_id(
                    conn, trade_date,
                )
            if batch_id is None:
                raise SchemaError(
                    f"broker position snapshot missing for {trade_date}"
                )
            rows = conn.execute(
                """SELECT stock_code, shares, can_use_volume, avg_cost,
                          market_value
                   FROM broker_position_snapshot WHERE batch_id=?""",
                (batch_id,),
            ).fetchall()
            return {
                row["stock_code"]: {
                    "shares": row["shares"],
                    "can_use_volume": row["can_use_volume"],
                    "avg_cost": row["avg_cost"],
                    "market_value": row["market_value"],
                }
                for row in rows
            }

    def get_broker_position_market_values(self, trade_date: str) -> dict:
        """当日最新券商快照的逐仓市值；缺失值保留为 None。"""
        with self._conn() as conn:
            batch_id = self._latest_broker_snapshot_batch_id(conn, trade_date)
            if batch_id is None:
                return {}
            rows = conn.execute(
                "SELECT stock_code, market_value FROM broker_position_snapshot "
                "WHERE batch_id=?",
                (batch_id,),
            ).fetchall()
            return {r["stock_code"]: r["market_value"] for r in rows}


class FillImporter:
    """扫描共享目录 outbound/，导入回执并归档。"""

    def __init__(self, bridge_root, recorder: LiveRecorder):
        self.bridge_root = Path(bridge_root)
        self.outbound = self.bridge_root / "outbound"
        self.archive = self.bridge_root / "archive"
        self.snapshot_request_root = self.bridge_root / "snapshot_requests"
        self.snapshot_responses = self.snapshot_request_root / "responses"
        self.snapshot_archive = self.snapshot_request_root / "archive"
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

    def import_broker_snapshots(self) -> int:
        """导入券商 ACCOUNT/POSITION 快照，返回处理的批次数。"""
        if not self.outbound.exists():
            return 0

        count = 0
        for done_path in sorted(self.outbound.glob("account_*.done")):
            jsonl_path = done_path.with_suffix(".jsonl")
            if not jsonl_path.exists():
                logger.warning("done without jsonl: %s", done_path)
                continue
            if self._import_snapshot(jsonl_path):
                count += 1
            self._archive(jsonl_path)
            self._archive(done_path)
        return count

    def import_account_snapshot_responses(self) -> int:
        """Serialize response import/archive across importer processes."""
        self.snapshot_request_root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.snapshot_request_root / "response_import.lock"))
        with lock:
            return self._import_account_snapshot_responses_locked()

    def _import_account_snapshot_responses_locked(self) -> int:
        """Import terminal snapshot-only responses, never trading receipts."""
        request_ids = set()
        for root in (self.snapshot_responses, self.snapshot_archive):
            if not root.exists():
                continue
            for path in root.glob("response_snapshot_*.*"):
                name = path.name
                if name.endswith(".json") or name.endswith(".done"):
                    request_ids.add(name[len("response_"):].rsplit(".", 1)[0])
        count = 0
        for request_id in sorted(request_ids):
            json_path = self._snapshot_response_file(request_id, ".json")
            done_path = self._snapshot_response_file(request_id, ".done")
            if json_path is None or done_path is None:
                logger.warning(
                    "partial snapshot response retained for recovery: %s",
                    request_id,
                )
                continue
            try:
                response_bytes = json_path.read_bytes()
                done_bytes = done_path.read_bytes()
                payload = json.loads(response_bytes.decode("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SchemaError("invalid account snapshot response JSON") from exc
            if not isinstance(payload, dict):
                raise SchemaError("account snapshot response must be an object")
            payload_request_id = payload.get("request_id", "")
            expected_name = f"response_{request_id}.json"
            if json_path.name != expected_name or payload_request_id != request_id:
                raise SchemaError("account snapshot response filename mismatch")
            done_checksum = done_bytes.decode("utf-8").strip()
            if done_checksum != payload.get("checksum"):
                raise SchemaError("account snapshot response done checksum mismatch")
            def require_unchanged():
                if (
                    json_path.read_bytes() != response_bytes
                    or done_path.read_bytes() != done_bytes
                ):
                    raise SchemaError(
                        "account snapshot response changed during import"
                    )

            require_unchanged()
            if self.recorder.save_account_snapshot_response(
                payload, before_commit=require_unchanged,
            ):
                count += 1
            if json_path.parent != self.snapshot_archive:
                self._archive_snapshot_response(json_path)
            if done_path.parent != self.snapshot_archive:
                self._archive_snapshot_response(done_path)
        return count

    def _snapshot_response_file(self, request_id: str, suffix: str):
        name = f"response_{request_id}{suffix}"
        candidates = [
            root / name for root in (
                self.snapshot_responses, self.snapshot_archive,
            ) if (root / name).is_file()
        ]
        if len(candidates) > 1:
            if candidates[0].read_bytes() != candidates[1].read_bytes():
                raise SchemaError("conflicting duplicate snapshot response file")
            # Prefer the response copy so normal archival removes the duplicate.
            return candidates[0]
        return candidates[0] if candidates else None

    def _import_snapshot(self, jsonl_path: Path) -> bool:
        account = None
        positions = []
        name = jsonl_path.name
        prefix = "account_"
        suffix = ".jsonl"
        if not name.startswith(prefix) or not name.endswith(suffix):
            raise SchemaError(f"invalid snapshot filename: {name!r}")
        batch_id = name[len(prefix):-len(suffix)]
        if not batch_id:
            raise SchemaError("snapshot filename requires a batch_id")
        row_count = 0
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if not isinstance(d, dict):
                raise SchemaError("snapshot row must be an object")
            row_count += 1
            if d.get("batch_id") != batch_id:
                raise SchemaError(
                    "snapshot batch_id must match filename for every row"
                )
            if d.get("type") == "account_snapshot":
                if account is not None:
                    raise SchemaError("snapshot contains multiple account rows")
                account = d
            elif d.get("type") == "broker_position":
                positions.append(d)
            else:
                raise SchemaError(f"unknown snapshot row type: {d.get('type')!r}")
        if row_count == 0:
            logger.warning("empty broker snapshot: %s", jsonl_path.name)
            return False
        batch = self.recorder.get_batch(batch_id)
        if batch is None:
            raise SchemaError(f"unknown snapshot batch_id: {batch_id!r}")
        if any(
            row.get("trade_date") != batch["trade_date"]
            for row in ([account] if account is not None else []) + positions
        ):
            raise SchemaError("snapshot trade_date does not match durable batch")
        self.recorder.save_broker_snapshot(batch_id, account, positions)
        logger.info(
            "imported broker snapshot %s: cash=%s positions=%d",
            batch_id,
            None if account is None else account.get("available_cash"),
            len(positions),
        )
        return True

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

    def _archive_snapshot_response(self, path: Path) -> None:
        self.snapshot_archive.mkdir(parents=True, exist_ok=True)
        target = self.snapshot_archive / path.name
        if target.exists():
            if target.read_bytes() == path.read_bytes():
                path.unlink()
                return
            raise SchemaError("account snapshot response archive conflict")
        os.replace(path, target)

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
