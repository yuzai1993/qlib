"""Exercise offline backup against real SQLite files, including a live WAL."""

import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


def prepare(*args, **kwargs):
    return importlib.import_module(
        "live_trading.scripts.prepare_topk_cutover"
    ).prepare_topk_cutover(*args, **kwargs)


@pytest.fixture
def ledger(tmp_path):
    source = tmp_path / "source.db"
    conn = sqlite3.connect(source)
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA wal_autocheckpoint=0;
        CREATE TABLE account_state (key TEXT PRIMARY KEY, value REAL NOT NULL);
        CREATE TABLE positions (stock_code TEXT PRIMARY KEY, shares INTEGER,
                                avg_cost REAL, opened_trade_date TEXT);
        CREATE TABLE batches (batch_id TEXT PRIMARY KEY, trade_date TEXT,
                              mode TEXT, planned_orders INTEGER,
                              strategy_id TEXT, superseded_by TEXT);
        CREATE TABLE fills (batch_id TEXT, client_order_id TEXT, mode TEXT,
                            stock_code TEXT, side TEXT, status TEXT,
                            requested_qty INTEGER, filled_qty INTEGER,
                            applied_qty INTEGER, netted_qty INTEGER,
                            PRIMARY KEY (batch_id, client_order_id));
        CREATE TABLE signal_orders (batch_id TEXT, client_order_id TEXT,
                                    stock_code TEXT, side TEXT, quantity INTEGER,
                                    PRIMARY KEY (batch_id, client_order_id));
        CREATE TABLE cohort_layers (buy_trade_date TEXT, stock_code TEXT, shares INTEGER);
        CREATE TABLE cohort_layer_dates (buy_trade_date TEXT PRIMARY KEY, seq INTEGER UNIQUE);
        CREATE TABLE cohort_pending (stock_code TEXT PRIMARY KEY, shares INTEGER);
        CREATE TABLE cash_flows (id INTEGER PRIMARY KEY AUTOINCREMENT, amount REAL);
        CREATE TABLE private_metadata (value BLOB);
        INSERT INTO account_state VALUES ('cash', 87654.321), ('opening_cash', 300000);
        INSERT INTO positions VALUES ('600000.SH', 205, 12.34, '2026-10-27');
        INSERT INTO cohort_layer_dates VALUES ('2026-10-27', 7);
        INSERT INTO cohort_layers VALUES ('2026-10-27', 'SH600000', 205);
        INSERT INTO cash_flows (amount) VALUES (300000), (-12.5);
        INSERT INTO private_metadata VALUES (X'00ff04');
        INSERT INTO batches VALUES ('20261027_old_001', '2026-10-27', 'LIVE', 1, 'old', NULL);
        INSERT INTO signal_orders VALUES ('20261027_old_001', '20261027001001B', '600000.SH', 'BUY', 205);
        INSERT INTO fills VALUES ('20261027_old_001', '20261027001001B', 'LIVE',
                                  '600000.SH', 'BUY', 'FILLED', 205, 205, 205, 0);
    """
    )
    conn.commit()
    yield source, conn
    conn.close()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def all_rows(conn):
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {
        name: conn.execute(f'SELECT * FROM "{name}"').fetchall() for (name,) in tables
    }


def test_wal_snapshot_preserves_every_table_cash_cohort_history_and_source(
    ledger, tmp_path
):
    source, conn = ledger
    before_rows = all_rows(conn)
    before_files = {p: file_hash(p) for p in (source, Path(str(source) + "-wal"))}
    destination = tmp_path / "offline" / "prepared.db"
    manifest_path = tmp_path / "offline" / "manifest.json"

    result = prepare(source, destination, manifest_path=manifest_path)

    assert {p: file_hash(p) for p in before_files} == before_files
    assert all_rows(conn) == before_rows
    with sqlite3.connect(destination) as copied:
        assert all_rows(copied) == before_rows
        assert copied.execute(
            "SELECT value FROM account_state WHERE key='cash'"
        ).fetchone() == (87654.321,)
        assert copied.execute("SELECT seq FROM cohort_layer_dates").fetchone() == (7,)
        assert copied.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='cash_flows'"
        ).fetchone() == (2,)
    assert result["snapshot"]["sha256"] == file_hash(destination)
    assert not Path(str(destination) + "-wal").exists()
    assert not Path(str(destination) + "-shm").exists()
    assert result["preservation"]["all_tables_equal"] is True
    assert result["preservation"]["cash_equal"] is True
    assert result["preservation"]["positions_equal"] is True
    assert result["account"]["cash"] == 87654.321
    assert result["account"]["position_count"] == 1
    assert result["target"]["opening_cash"] == 300000
    assert result["target"]["risk_degree"] == 0.90
    assert result["target"]["strategy_id"] == "alla_v2_top5d1f100_postclose_real"
    assert result["source"]["strategy_ids"] == ["old"]
    assert result["cohort"]["preserved_as_historical"] is True
    assert result["ready_for_activation"] is False
    assert json.loads(manifest_path.read_text()) == result
    assert "00ff04" not in manifest_path.read_text()


def test_risk1_metadata_preserves_real_cash_and_every_table(ledger, tmp_path):
    source, conn = ledger
    before = all_rows(conn)
    manifest = tmp_path / "risk1.json"
    result = prepare(
        source, tmp_path / "risk1.db", manifest_path=manifest, target_risk_degree=1.0
    )
    assert result["target"]["risk_degree"] == 1.0
    assert result["account"]["cash"] == 87654.321
    assert result["ready_for_activation"] is False
    assert result["activation_authorized"] is False
    assert all_rows(conn) == before
    assert json.loads(manifest.read_text())["target"]["risk_degree"] == 1.0


@pytest.mark.parametrize("risk", [0, -0.1, 1.01, float("nan"), float("inf"), True, "1.0", None])
def test_invalid_target_risk_creates_no_outputs(ledger, tmp_path, risk):
    source, conn = ledger
    before = all_rows(conn)
    output_dir = tmp_path / "invalid-risk"
    with pytest.raises(ValueError, match="risk"):
        prepare(
            source,
            output_dir / "snapshot.db",
            manifest_path=output_dir / "audit.json",
            target_risk_degree=risk,
        )
    assert not output_dir.exists()
    assert all_rows(conn) == before


def test_cli_risk1_is_recorded_without_activating(ledger, tmp_path):
    source, _ = ledger
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "live_trading/scripts/prepare_topk_cutover.py"),
            "--source", str(source), "--destination", str(tmp_path / "cli-risk1.db"),
            "--risk-degree", "1.0",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    audit = json.loads(result.stdout)
    assert audit["target"]["risk_degree"] == 1.0
    assert audit["activation_authorized"] is False
    assert audit["account"]["cash"] == 87654.321


@pytest.mark.parametrize(
    "kind", ["same_path", "existing", "symlink", "bridge", "production"]
)
def test_refuses_overwrite_alias_and_live_locations_without_touching_source(
    ledger, tmp_path, kind
):
    source, conn = ledger
    before = all_rows(conn)
    if kind == "same_path":
        destination = source
    elif kind == "existing":
        destination = tmp_path / "exists.db"
        destination.write_bytes(b"do not replace")
    elif kind == "symlink":
        destination = tmp_path / "alias.db"
        destination.symlink_to(source)
    elif kind == "bridge":
        destination = tmp_path / "qmt_bridge" / "prepared.db"
    else:
        destination = tmp_path / "live_trading" / "data" / "prepared.db"
    with pytest.raises((ValueError, FileExistsError)):
        prepare(source, destination)
    assert all_rows(conn) == before
    if kind == "existing":
        assert destination.read_bytes() == b"do not replace"


@pytest.mark.parametrize("kind", ["missing", "corrupt", "wrong_schema"])
def test_bad_source_fails_closed_and_leaves_no_output(tmp_path, kind):
    source = tmp_path / "bad.db"
    destination = tmp_path / "prepared.db"
    if kind == "corrupt":
        source.write_bytes(b"this is not sqlite")
    elif kind == "wrong_schema":
        with sqlite3.connect(source) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
    with pytest.raises((ValueError, FileNotFoundError, sqlite3.DatabaseError)):
        prepare(source, destination)
    assert not destination.exists()
    assert not Path(str(destination) + "-wal").exists()
    if kind == "missing":
        assert not source.exists()


@pytest.mark.parametrize("status", [None, "ACCEPTED", "PARTIAL", "UNKNOWN"])
def test_pending_and_partial_live_batches_are_blockers_and_never_superseded(
    ledger, tmp_path, status
):
    source, conn = ledger
    conn.execute(
        "INSERT INTO batches VALUES ('20261028_old_002', '2026-10-28', 'LIVE', 1, 'old', NULL)"
    )
    conn.execute(
        "INSERT INTO signal_orders VALUES ('20261028_old_002', '20261028002001S', '600000.SH', 'SELL', 205)"
    )
    if status is not None:
        conn.execute(
            "INSERT INTO fills VALUES ('20261028_old_002', '20261028002001S', 'LIVE', '600000.SH', 'SELL', ?, 205, 100, 100, 0)",
            (status,),
        )
    conn.commit()
    before = all_rows(conn)
    result = prepare(source, tmp_path / "prepared.db")
    assert result["ready_for_activation"] is False
    assert "unresolved_live_orders" in result["blockers"]
    assert result["orders"]["blocking_batches"][0]["batch_id"] == "20261028_old_002"
    assert all_rows(conn) == before
    with sqlite3.connect(tmp_path / "prepared.db") as copied:
        assert all_rows(copied) == before


def test_superseded_but_observed_open_order_still_blocks(ledger, tmp_path):
    source, conn = ledger
    conn.execute("UPDATE batches SET superseded_by='replacement'")
    conn.execute("UPDATE fills SET status='ACCEPTED'")
    conn.commit()
    result = prepare(source, tmp_path / "prepared.db")
    assert "unresolved_live_orders" in result["blockers"]


@pytest.mark.parametrize("quantity", [-10, 205.5, 100000000])
def test_unusual_cohort_quantities_block_without_repair(ledger, tmp_path, quantity):
    source, conn = ledger
    conn.execute("UPDATE cohort_layers SET shares=?", (quantity,))
    conn.commit()
    result = prepare(source, tmp_path / "prepared.db")
    assert "cohort_requires_review" in result["blockers"]
    assert result["cohort"]["anomaly_count"] > 0
    assert conn.execute("SELECT shares FROM cohort_layers").fetchone()[0] == quantity


def test_collision_audit_reserves_sequence_across_strategy_names(ledger, tmp_path):
    source, _ = ledger
    result = prepare(source, tmp_path / "prepared.db")
    assert result["batch_identity"]["by_trade_date"]["2026-10-27"] == {
        "used_sequences": [1],
        "next_unused_sequence": 2,
    }
    assert result["batch_identity"]["client_order_ids_include_strategy"] is False
    assert result["batch_identity"]["history_preserved"] is True


def test_existing_manifest_is_never_overwritten_and_no_db_is_created(ledger, tmp_path):
    source, _ = ledger
    manifest = tmp_path / "manifest.json"
    manifest.write_text("keep")
    with pytest.raises(FileExistsError):
        prepare(source, tmp_path / "prepared.db", manifest_path=manifest)
    assert manifest.read_text() == "keep"
    assert not (tmp_path / "prepared.db").exists()


def test_custom_bridge_root_is_forbidden_even_without_bridge_in_its_name(
    ledger, tmp_path
):
    source, _ = ledger
    bridge = tmp_path / "mounted_share"
    with pytest.raises(ValueError, match="bridge"):
        prepare(source, bridge / "copy.db", bridge_roots=[bridge])
    assert not bridge.exists()


def test_manifest_cannot_be_a_source_sqlite_sidecar(ledger, tmp_path):
    source, _ = ledger
    with pytest.raises(ValueError):
        prepare(
            source, tmp_path / "copy.db", manifest_path=Path(str(source) + "-journal")
        )
    assert not (tmp_path / "copy.db").exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_manifest_cannot_overlap_destination_sqlite_sidecars(ledger, tmp_path, suffix):
    source, _ = ledger
    destination = tmp_path / "copy.db"
    with pytest.raises(ValueError):
        prepare(source, destination, manifest_path=Path(str(destination) + suffix))
    assert not destination.exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_existing_destination_sidecars_are_never_modified(ledger, tmp_path, suffix):
    source, _ = ledger
    destination = tmp_path / "copy.db"
    sidecar = Path(str(destination) + suffix)
    sidecar.write_bytes(b"keep existing sidecar")
    with pytest.raises(FileExistsError):
        prepare(source, destination)
    assert sidecar.read_bytes() == b"keep existing sidecar"
    assert not destination.exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_failure_cleanup_preserves_preexisting_manifest_companions(
    ledger, tmp_path, suffix
):
    source, conn = ledger
    conn.execute("UPDATE positions SET shares='nonnumeric'")
    conn.commit()
    before = all_rows(conn)
    destination = tmp_path / "copy.db"
    manifest = tmp_path / "manifest.json"
    sidecar = Path(str(manifest) + suffix)
    sidecar.write_bytes(b"unrelated existing file")

    with pytest.raises((TypeError, ValueError)):
        prepare(source, destination, manifest_path=manifest)

    assert sidecar.read_bytes() == b"unrelated existing file"
    assert not destination.exists()
    assert not manifest.exists()
    assert all_rows(conn) == before


def test_duplicate_client_order_identity_across_strategies_blocks(ledger, tmp_path):
    source, conn = ledger
    conn.execute(
        "INSERT INTO batches VALUES ('20261027_other_001', '2026-10-27', 'LIVE', 1, 'other', NULL)"
    )
    conn.execute(
        "INSERT INTO signal_orders SELECT '20261027_other_001', client_order_id, stock_code, side, quantity FROM signal_orders"
    )
    conn.execute(
        "INSERT INTO fills SELECT '20261027_other_001', client_order_id, mode, stock_code, side, status, requested_qty, filled_qty, applied_qty, netted_qty FROM fills"
    )
    conn.commit()
    result = prepare(source, tmp_path / "copy.db")
    assert "batch_identity_requires_review" in result["blockers"]
    assert result["batch_identity"]["duplicate_client_order_id_count"] == 1


def test_actual_client_order_sequence_is_reserved_when_batch_sequence_disagrees(
    ledger, tmp_path
):
    source, conn = ledger
    for table in ("signal_orders", "fills"):
        conn.execute(f"UPDATE {table} SET client_order_id='20261027002001B'")
    conn.commit()

    result = prepare(source, tmp_path / "copy.db")

    assert "batch_identity_requires_review" in result["blockers"]
    assert result["batch_identity"]["by_trade_date"]["2026-10-27"] == {
        "used_sequences": [1, 2],
        "next_unused_sequence": 3,
    }
    issues = result["batch_identity"]["client_order_identity_issues"]
    assert len(issues) == 2
    assert all("batch_sequence_mismatch" in issue["reasons"] for issue in issues)


@pytest.mark.parametrize(
    "client_order_id,reason,next_sequence",
    [
        ("bad", "malformed_client_order_id", None),
        ("20261027000001B", "batch_sequence_out_of_range", None),
        ("20261027001000B", "order_sequence_out_of_range", None),
        ("20261327001001B", "invalid_trade_date", None),
        ("20261027001001S", "side_mismatch", 2),
        ("20261028001001B", "trade_date_mismatch", 2),
    ],
)
def test_client_order_format_date_sequence_and_side_are_validated(
    ledger, tmp_path, client_order_id, reason, next_sequence
):
    source, conn = ledger
    for table in ("signal_orders", "fills"):
        conn.execute(f"UPDATE {table} SET client_order_id=?", (client_order_id,))
    conn.commit()

    result = prepare(source, tmp_path / "copy.db")

    assert "batch_identity_requires_review" in result["blockers"]
    issues = result["batch_identity"]["client_order_identity_issues"]
    assert len(issues) == 2
    assert all(reason in issue["reasons"] for issue in issues)
    assert (
        result["batch_identity"]["by_trade_date"]["2026-10-27"]["next_unused_sequence"]
        == next_sequence
    )
    if reason == "trade_date_mismatch":
        assert result["batch_identity"]["by_trade_date"]["2026-10-28"] == {
            "used_sequences": [1],
            "next_unused_sequence": 2,
        }
    if next_sequence is None:
        assert "batch_sequence_exhausted" not in result["blockers"]


@pytest.mark.parametrize("table", ["signal_orders", "fills"])
def test_orphan_client_order_reserves_actual_sequence_and_blocks(
    ledger, tmp_path, table
):
    source, conn = ledger
    if table == "signal_orders":
        conn.execute(
            "INSERT INTO signal_orders VALUES ('orphan', '20261027003001B', '600000.SH', 'BUY', 205)"
        )
    else:
        conn.execute(
            "INSERT INTO fills VALUES ('orphan', '20261027003001B', 'LIVE', '600000.SH', 'BUY', 'FILLED', 205, 205, 205, 0)"
        )
    conn.commit()

    result = prepare(source, tmp_path / "copy.db")

    assert "batch_identity_requires_review" in result["blockers"]
    assert "unresolved_live_orders" in result["blockers"]
    assert result["batch_identity"]["by_trade_date"]["2026-10-27"] == {
        "used_sequences": [1, 3],
        "next_unused_sequence": 4,
    }
    assert result["batch_identity"]["client_order_identity_issues"] == [
        {
            "table": table,
            "batch_id": "orphan",
            "client_order_id": "20261027003001B",
            "reasons": ["missing_batch"],
        }
    ]


def test_source_snapshot_hash_changes_when_only_wal_data_changes(ledger, tmp_path):
    source, conn = ledger
    first = prepare(source, tmp_path / "first.db")
    main_hash = file_hash(source)
    conn.execute("UPDATE account_state SET value=12345.67 WHERE key='cash'")
    conn.commit()
    second = prepare(source, tmp_path / "second.db")
    assert file_hash(source) == main_hash
    assert first["source"]["data_sha256"] != second["source"]["data_sha256"]
    assert first["snapshot"]["sha256"] != second["snapshot"]["sha256"]
    assert first["account"]["cash"] == 87654.321
    assert second["account"]["cash"] == 12345.67


def test_cli_writes_offline_manifest_and_returns_nonzero_for_activation_blockers(
    ledger, tmp_path
):
    source, _ = ledger
    script = (
        Path(__file__).resolve().parents[2]
        / "live_trading/scripts/prepare_topk_cutover.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(source),
            "--destination",
            str(tmp_path / "cli.db"),
            "--manifest",
            str(tmp_path / "cli.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["ready_for_activation"] is False
    assert (tmp_path / "cli.json").is_file()
