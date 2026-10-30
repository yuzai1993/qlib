"""Keep the real research archive navigable after future repository edits."""
import hashlib
import json
from pathlib import Path

from backtest.scripts.experiment_registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "backtest/experiments"


def test_registered_experiments_have_local_records_and_asset_manifests():
    rows = load_registry(ARCHIVE / "registry.jsonl")
    entries = [json.loads(line) for line in (ARCHIVE / "registry.jsonl").read_text().splitlines() if line.strip()]
    assert rows
    for row, entry in zip(rows, entries):
        expected_parent = Path(row["exp_id"]) / "records"
        assert Path(entry["detail_path"]).parent == expected_parent
        metadata = json.loads((ARCHIVE / row["exp_id"] / "metadata.json").read_text())
        assert metadata["exp_id"] == row["exp_id"]
        assert metadata["registry_authority"] == "backtest/experiments/registry.jsonl"


def test_archived_assets_and_frozen_config_hashes_are_present():
    ids = {row["exp_id"] for row in load_registry(ARCHIVE / "registry.jsonl")}
    for exp_id in ids:
        metadata = json.loads((ARCHIVE / exp_id / "metadata.json").read_text())
        for key in ("reports", "docs", "data", "shared_assets", "records"):
            for relative in metadata.get(key, []):
                path = ROOT / relative
                assert path.is_file() and path.resolve().is_relative_to(ROOT), (exp_id, key, relative)
        for config in metadata.get("configs", []):
            path = ROOT / config["path"]
            assert path.is_file(), (exp_id, config["path"])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == config["sha256"], config["path"]


def test_summary_reports_stay_in_the_shared_report_directory():
    for name in ("report.html", "phase_m_v1_report.html", "phase_m_v1_bt_report.html", "strategy_stability_report.html"):
        assert (ARCHIVE / "reports" / name).is_file()
        assert not (ARCHIVE / name).exists()
