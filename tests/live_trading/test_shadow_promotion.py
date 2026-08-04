from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from live_trading.modules import shadow_promotion
from live_trading.modules.shadow_promotion import (
    retire_claimed_shadow,
    validate_unexecuted_state,
)
from live_trading.modules.signal_schema import SchemaError
from live_trading.scripts.retire_claimed_shadow import main


BATCH_ID = "20260805_csi1000_b6m_b2s_postclose_001"
NOW = datetime(2026, 8, 5, 0, 30, tzinfo=timezone.utc)


def _valid_state():
    return {
        "batch_id": BATCH_ID,
        "phase": "SELL",
        "phase_started": 1.0,
        "trading_started": False,
        "execution_authorized": False,
        "execution_live": False,
        "submitted": [],
        "fills": {},
        "remaining_cash": None,
        "orders": [],
    }


def _write_claimed_batch(root: Path):
    processing = root / "processing"
    state_dir = root / "state"
    processing.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    header = {
        "type": "batch_header",
        "batch_id": BATCH_ID,
        "trade_date": "2026-08-05",
        "mode": "SIMULATE",
        "account_environment": "SIMULATION",
    }
    (processing / f"signal_{BATCH_ID}.jsonl").write_text(
        json.dumps(header, sort_keys=True) + "\n", encoding="utf-8",
    )
    (processing / f"signal_{BATCH_ID}.done").write_text(
        "sha256:test\n", encoding="utf-8",
    )
    (state_dir / f"active_{BATCH_ID}.json").write_text(
        json.dumps(_valid_state(), sort_keys=True) + "\n", encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("phase", "BUY"),
        ("trading_started", True),
        ("execution_authorized", True),
        ("execution_live", True),
        ("submitted", ["order-1"]),
        ("fills", {"order-1": {"status": "FILLED"}}),
    ],
)
def test_validate_unexecuted_state_rejects_each_unsafe_field(
    field, unsafe_value,
):
    payload = _valid_state()
    payload[field] = unsafe_value

    with pytest.raises(SchemaError, match=field):
        validate_unexecuted_state(payload, BATCH_ID)


def test_validate_unexecuted_state_rejects_batch_mismatch():
    with pytest.raises(SchemaError, match="batch_id"):
        validate_unexecuted_state(_valid_state(), "different")


def test_retirement_dry_run_has_no_filesystem_side_effect(tmp_path):
    _write_claimed_batch(tmp_path)

    result = retire_claimed_shadow(
        tmp_path, BATCH_ID, execute=False, now=NOW,
    )

    assert result["status"] == "dry_run"
    assert result["batch_id"] == BATCH_ID
    assert len(result["files"]) == 3
    assert all(row["sha256"].startswith("sha256:") for row in result["files"])
    assert not (tmp_path / "archive").exists()
    assert (tmp_path / "processing" / f"signal_{BATCH_ID}.jsonl").exists()


def test_retirement_archives_verified_files_before_removing_sources(tmp_path):
    _write_claimed_batch(tmp_path)

    result = retire_claimed_shadow(
        tmp_path, BATCH_ID, execute=True, now=NOW,
    )

    archive_dir = Path(result["archive_dir"])
    manifest = json.loads(
        (archive_dir / "retirement_manifest.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "retired"
    assert manifest["batch_id"] == BATCH_ID
    for row in manifest["files"]:
        assert not Path(row["source"]).exists()
        assert Path(row["archive"]).is_file()
        assert row["sha256"].startswith("sha256:")


def test_retirement_copy_failure_keeps_every_source(tmp_path, monkeypatch):
    _write_claimed_batch(tmp_path)

    def fail_copy(_source, _destination):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(shadow_promotion, "_copy_verified", fail_copy)

    with pytest.raises(OSError, match="simulated copy failure"):
        retire_claimed_shadow(tmp_path, BATCH_ID, execute=True, now=NOW)

    assert (tmp_path / "processing" / f"signal_{BATCH_ID}.jsonl").exists()
    assert (tmp_path / "processing" / f"signal_{BATCH_ID}.done").exists()
    assert (tmp_path / "state" / f"active_{BATCH_ID}.json").exists()
    assert not list((tmp_path / "archive").rglob("retirement_manifest.json"))


def test_retirement_rejects_non_shadow_header(tmp_path):
    _write_claimed_batch(tmp_path)
    jsonl = tmp_path / "processing" / f"signal_{BATCH_ID}.jsonl"
    header = json.loads(jsonl.read_text(encoding="utf-8"))
    header["mode"] = "LIVE"
    jsonl.write_text(json.dumps(header) + "\n", encoding="utf-8")

    with pytest.raises(SchemaError, match="SIMULATE"):
        retire_claimed_shadow(tmp_path, BATCH_ID, execute=False, now=NOW)


def test_retirement_cli_defaults_to_dry_run(tmp_path, capsys):
    _write_claimed_batch(tmp_path)

    assert main([
        "--bridge-root", str(tmp_path),
        "--batch-id", BATCH_ID,
    ]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "dry_run"
    assert (tmp_path / "processing" / f"signal_{BATCH_ID}.jsonl").exists()
