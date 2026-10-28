#!/usr/bin/env python3
"""Prepare an inactive, exact SQLite ledger snapshot and cutover audit.

No recorder, broker, scheduler or bridge is constructed. All source access uses
SQLite mode=ro; a read transaction pins the WAL-aware snapshot for backup and
logical verification. The output is audit evidence, never an activation command.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys


TARGET_STRATEGY = "alla_v2_top5d1f100_postclose_real"
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
# Match the recorder protocol: PARTIAL is a terminal receipt, but still needs
# explicit cutover review because the planned position transition is incomplete.
TERMINAL = {"FILLED", "PARTIAL", "REJECTED", "SKIPPED", "EXPIRED", "ERROR"}
REQUIRED = {
    "account_state": {"key", "value"},
    "positions": {"stock_code", "shares", "avg_cost"},
    "batches": {
        "batch_id",
        "trade_date",
        "mode",
        "planned_orders",
        "strategy_id",
        "superseded_by",
    },
    "fills": {
        "batch_id",
        "client_order_id",
        "mode",
        "stock_code",
        "side",
        "status",
        "filled_qty",
    },
    "signal_orders": {"batch_id", "client_order_id", "stock_code", "side", "quantity"},
    "cohort_layers": {"buy_trade_date", "stock_code", "shares"},
    "cohort_layer_dates": {"buy_trade_date", "seq"},
    "cohort_pending": {"stock_code", "shares"},
}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readonly(path):
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _fingerprint(conn):
    schema = [
        list(row)
        for row in conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
    ]
    tables = {}
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        name = row[0]
        columns = [col[1] for col in conn.execute(f"PRAGMA table_info({_quote(name)})")]
        digest = hashlib.sha256()
        count = 0
        order = ",".join(_quote(column) for column in columns)
        for values in conn.execute(f"SELECT * FROM {_quote(name)} ORDER BY {order}"):
            # Typed values preserve SQLite BLOBs without exposing their contents.
            record = [
                [
                    type(value).__name__,
                    value.hex() if isinstance(value, bytes) else value,
                ]
                for value in values
            ]
            digest.update((_json(record) + "\n").encode())
            count += 1
        tables[name] = {"count": count, "sha256": digest.hexdigest()}
    return {
        "schema_sha256": hashlib.sha256(_json(schema).encode()).hexdigest(),
        "tables": tables,
        "data_sha256": hashlib.sha256(_json(tables).encode()).hexdigest(),
    }


def _validate_schema(conn):
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("source SQLite integrity check failed")
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, required in REQUIRED.items():
        if table not in tables:
            raise ValueError(f"missing recorder table: {table}")
        columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")
        }
        if not required <= columns:
            raise ValueError(
                f"missing recorder columns in {table}: {sorted(required - columns)}"
            )


def _offline_output(path, source, bridge_roots):
    raw = Path(path).expanduser().absolute()
    resolved = raw.resolve()
    if resolved in {
        source,
        *(Path(str(source) + suffix) for suffix in SQLITE_SIDECARS),
    }:
        raise ValueError("output must be distinct from source and its SQLite sidecars")
    if raw.exists() or raw.is_symlink():
        raise FileExistsError(f"refusing existing output: {raw}")
    parts = [part.lower() for part in resolved.parts]
    if any("bridge" in part for part in parts) or any(
        resolved.is_relative_to(root) for root in bridge_roots
    ):
        raise ValueError("output must be outside every live bridge")
    if any(
        parts[index : index + 2] == ["live_trading", "data"]
        for index in range(len(parts) - 1)
    ):
        raise ValueError("output must be outside production ledger directories")
    return resolved


def _reserve(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)


def _code(code):
    value = str(code).upper()
    if "." in value:
        number, exchange = value.split(".", 1)
        return exchange + number
    return value


def _valid_shares(value, *, positive=False):
    return (
        isinstance(value, (int, float))
        and math.isfinite(value)
        and value == int(value)
        and value >= int(positive)
    )


def _audit(conn, fingerprints):
    def rows(table):
        return [dict(row) for row in conn.execute(f"SELECT * FROM {_quote(table)}")]

    blockers = ["manual_activation_review_required"]
    positions = rows("positions")
    cash_rows = conn.execute(
        "SELECT value FROM account_state WHERE key='cash'"
    ).fetchall()
    cash = cash_rows[0][0] if len(cash_rows) == 1 else None
    if not isinstance(cash, (int, float)) or not math.isfinite(cash) or cash < 0:
        blockers.append("invalid_cash_state")
    if any(not _valid_shares(row["shares"]) for row in positions):
        blockers.append("invalid_position_quantities")
    batches = rows("batches")
    fills = rows("fills")
    orders = rows("signal_orders")
    batch_by_id = {row["batch_id"]: row for row in batches}
    fills_by_batch = defaultdict(list)
    orders_by_batch = defaultdict(list)
    for row in fills:
        fills_by_batch[row["batch_id"]].append(row)
    for row in orders:
        orders_by_batch[row["batch_id"]].append(row)
    blocking = []
    failed_sells = 0
    for batch in sorted(batches, key=lambda row: row["batch_id"]):
        if batch["mode"] != "LIVE":
            continue
        receipts = fills_by_batch[batch["batch_id"]]
        planned = orders_by_batch[batch["batch_id"]]
        received = {row["client_order_id"]: row for row in receipts}
        terminal = sum(row["status"] in TERMINAL for row in receipts)
        nonterminal = sum(row["status"] not in TERMINAL for row in receipts)
        partial = sum(row["status"] == "PARTIAL" for row in receipts)
        missing = sum(row["client_order_id"] not in received for row in planned)
        active = not batch["superseded_by"]
        expected = batch["planned_orders"]
        invalid_plan = not _valid_shares(expected) or expected != len(planned)
        unresolved = nonterminal or (
            active and (invalid_plan or missing or terminal < expected or partial)
        )
        if unresolved:
            blocking.append(
                {
                    "batch_id": batch["batch_id"],
                    "trade_date": batch["trade_date"],
                    "strategy_id": batch["strategy_id"],
                    "superseded": not active,
                    "planned_orders": expected,
                    "durable_orders": len(planned),
                    "terminal_receipts": terminal,
                    "nonterminal_receipts": nonterminal,
                    "partial_receipts": partial,
                    "missing_receipts": missing,
                    "plan_count_mismatch": invalid_plan,
                }
            )
        if active:
            failed_sells += sum(
                row["side"] == "SELL"
                and row["status"] in {"REJECTED", "SKIPPED", "EXPIRED", "ERROR"}
                for row in receipts
            )
    orphan_fills = sum(row["batch_id"] not in batch_by_id for row in fills)
    orphan_orders = sum(row["batch_id"] not in batch_by_id for row in orders)
    if blocking or orphan_fills or orphan_orders:
        blockers.append("unresolved_live_orders")
    if failed_sells:
        blockers.append("failed_live_sells_require_review")

    layers = rows("cohort_layers")
    pending = rows("cohort_pending")
    dates = rows("cohort_layer_dates")
    registered = {row["buy_trade_date"] for row in dates}
    anomaly_count = sum(
        not _valid_shares(row["shares"], positive=True) for row in layers + pending
    )
    anomaly_count += sum(row["buy_trade_date"] not in registered for row in layers)
    quantities = defaultdict(float)
    for row in layers + pending:
        if _valid_shares(row["shares"]):
            quantities[_code(row["stock_code"])] += row["shares"]
    actual = {_code(row["stock_code"]): row["shares"] for row in positions}
    mismatched = sum(
        quantities.get(code, 0) != actual.get(code, 0)
        for code in quantities.keys() | actual.keys()
    )
    anomaly_count += mismatched
    if anomaly_count:
        blockers.append("cohort_requires_review")
    if pending:
        blockers.append("cohort_pending_requires_review")

    used = defaultdict(set)
    malformed = []
    target_existing = []
    for batch in batches:
        match = re.fullmatch(r"(\d{8})_(.+)_(\d{3})", str(batch["batch_id"]))
        if (
            not match
            or match[1] != str(batch["trade_date"]).replace("-", "")
            or not 1 <= int(match[3]) <= 999
        ):
            malformed.append(batch["batch_id"])
        else:
            used[batch["trade_date"]].add(int(match[3]))
        if batch["strategy_id"] == TARGET_STRATEGY or (
            match and match[2] == TARGET_STRATEGY
        ):
            target_existing.append(batch["batch_id"])
    identities = defaultdict(set)
    for row in orders + fills:
        identities[row["client_order_id"]].add(row["batch_id"])
    duplicate_ids = sorted(
        identity for identity, owners in identities.items() if len(owners) > 1
    )
    if malformed or target_existing or duplicate_ids:
        blockers.append("batch_identity_requires_review")
    sequences = {
        day: {
            "used_sequences": sorted(numbers),
            "next_unused_sequence": max(numbers) + 1 if max(numbers) < 999 else None,
        }
        for day, numbers in sorted(used.items())
    }
    if any(row["next_unused_sequence"] is None for row in sequences.values()):
        blockers.append("batch_sequence_exhausted")
    return {
        "blockers": blockers,
        "account": {
            "cash": cash,
            "position_count": len(positions),
            "total_position_shares": sum(row["shares"] for row in positions),
        },
        "source_strategy_ids": sorted(
            {row["strategy_id"] for row in batches if row["strategy_id"]}
        ),
        "orders": {
            "blocking_batches": blocking,
            "unsuperseded_live_batch_count": sum(
                row["mode"] == "LIVE" and not row["superseded_by"] for row in batches
            ),
            "fill_status_counts": dict(
                sorted(Counter(row["status"] for row in fills).items())
            ),
            "orphan_fill_count": orphan_fills,
            "orphan_order_count": orphan_orders,
            "failed_live_sell_count": failed_sells,
        },
        "cohort": {
            "preserved_as_historical": True,
            "historical_marker_location": "manifest_only",
            "layer_count": len(dates),
            "layer_rows": len(layers),
            "pending_rows": len(pending),
            "layer_dates_and_sequences": sorted(dates, key=lambda row: row["seq"]),
            "anomaly_count": anomaly_count,
            "position_mismatch_count": mismatched,
            "odd_lot_rows": sum(
                _valid_shares(row["shares"]) and row["shares"] % 100 != 0
                for row in layers + pending
            ),
            "table_fingerprints": {
                name: fingerprints["tables"][name]
                for name in ("cohort_layers", "cohort_layer_dates", "cohort_pending")
            },
        },
        "batch_identity": {
            "history_preserved": True,
            "client_order_ids_include_strategy": False,
            "by_trade_date": sequences,
            "malformed_batch_ids": malformed,
            "existing_target_batch_ids": target_existing,
            "duplicate_client_order_id_count": len(duplicate_ids),
            "duplicate_client_order_ids": duplicate_ids,
            "collision_risk": "Client order IDs omit strategy. Never reuse a date/sequence already used by any strategy; recheck live history at activation.",
        },
    }


def prepare_topk_cutover(source, destination, *, manifest_path=None, bridge_roots=()):
    """Copy the complete ledger, preserving all history, and return its audit.

    Existing outputs (including aliases) are refused. Snapshot equality is
    checked against the pinned source transaction, not the moving live DB file.
    Configured nonstandard bridge roots must be passed via ``bridge_roots``.
    """
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("source must be an existing regular SQLite file")
    forbidden = [
        Path("/Volumes/qmt_bridge"),
        *(Path(root).expanduser().resolve() for root in bridge_roots),
    ]
    destination = _offline_output(destination, source, forbidden)
    manifest_path = (
        _offline_output(manifest_path, source, forbidden)
        if manifest_path is not None
        else None
    )
    companions = {Path(str(destination) + suffix) for suffix in SQLITE_SIDECARS}
    if manifest_path in {destination, *companions}:
        raise ValueError("manifest must be distinct from database and its sidecars")
    for companion in companions:
        if companion.exists() or companion.is_symlink():
            raise FileExistsError(f"refusing existing SQLite sidecar: {companion}")
    created = []
    started = datetime.now(timezone.utc).isoformat()
    try:
        with closing(_readonly(source)) as original:
            original.execute("BEGIN")
            _validate_schema(original)
            source_fingerprint = _fingerprint(original)
            for path in (destination, manifest_path):
                if path is not None:
                    _reserve(path)
                    created.append(path)
            with closing(sqlite3.connect(destination)) as copied:
                original.backup(copied)
                # Normalize only the offline copy to a self-contained DB. The
                # copied source may use WAL; audit delivery must not depend on
                # external sidecars or create them on a later read-only open.
                copied.execute("PRAGMA journal_mode=DELETE")
            with closing(_readonly(destination)) as copied:
                _validate_schema(copied)
                copied_fingerprint = _fingerprint(copied)
                if copied_fingerprint != source_fingerprint:
                    raise ValueError("backup differs from pinned source snapshot")
                audit = _audit(copied, copied_fingerprint)
            original.rollback()
        result = {
            "audit_schema_version": 1,
            "prepared_at_utc": started,
            "source": {
                "path": str(source),
                "readonly": True,
                "strategy_ids": audit.pop("source_strategy_ids"),
                **source_fingerprint,
            },
            "snapshot": {
                "path": str(destination),
                "sha256": _sha(destination),
                **copied_fingerprint,
            },
            "target": {
                "strategy_id": TARGET_STRATEGY,
                "strategy_class": "TopkDropoutStrategy",
                "topk": 5,
                "n_drop": 1,
                "hold_thresh": 1,
                "force_sell_rank": 100,
                "risk_degree": 0.90,
                "opening_cash": 300000,
                "opening_cash_is_metadata_only": True,
            },
            "preservation": {
                "all_tables_equal": True,
                "cash_equal": True,
                "positions_equal": True,
                "fills_equal": True,
                "batch_history_equal": True,
                "source_write_connection_opened": False,
            },
            "ready_for_activation": False,
            "activation_authorized": False,
            "snapshot_method": "SQLite backup from mode=ro source within pinned read transaction; snapshot SHA identifies WAL-inclusive data",
            **audit,
        }
        if manifest_path is not None:
            manifest_path.write_text(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
            )
        return result
    except BaseException:
        # Remove only paths exclusively reserved by this invocation.
        for path in reversed(created):
            path.unlink(missing_ok=True)
            for suffix in SQLITE_SIDECARS:
                Path(str(path) + suffix).unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--bridge-root",
        type=Path,
        action="append",
        default=[],
        help="additional forbidden bridge root (repeatable)",
    )
    args = parser.parse_args(argv)
    try:
        result = prepare_topk_cutover(
            args.source,
            args.destination,
            manifest_path=args.manifest,
            bridge_roots=args.bridge_root,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"offline cutover audit refused: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
    )
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
