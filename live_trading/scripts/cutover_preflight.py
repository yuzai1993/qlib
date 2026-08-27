#!/usr/bin/env python3
"""Read-only cutover checks. Must never construct LiveRecorder."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

PLACEHOLDER_CASH = 1_000_000.0
CONFIG_REL = Path("live_trading/configs/alla_v4_ladder_k1h5_postclose_real.yaml")
CRONTAB_REL = Path("live_trading/crontab.csi1000_postclose.example")
OLD_CONFIG_ID = "csi1000_b6m_b2s_postclose_real"
NEW_CONFIG_ID = "alla_v4_ladder_k1h5_postclose_real"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha_ok(project_root: Path, config: dict) -> bool:
    members = config.get("model", {}).get("members") or []
    if not members:
        return False
    for member in members:
        artifact = project_root / member["model_path"]
        expected = str(member.get("sha256") or "").strip().lower()
        if not artifact.is_file() or not expected:
            return False
        if _file_sha256(artifact) != expected:
            return False
    return True


def _parity_ok(project_root: Path) -> bool:
    script = project_root / "live_trading" / "scripts" / "check_backtest_parity.py"
    python = sys.executable
    result = subprocess.run(
        [python, str(script), "--config", NEW_CONFIG_ID],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def cutover_preflight(
    project_root: Path,
    skip_parity: bool = False,
    skip_sha: bool = False,
) -> dict:
    """Inspect the ladder config without opening the new ledger."""
    project_root = Path(project_root)
    config = yaml.safe_load(
        (project_root / CONFIG_REL).read_text(encoding="utf-8")
    )
    db_path = project_root / config["storage"]["db_path"]
    opening_cash = float(config.get("account", {}).get("opening_cash"))
    crontab_path = project_root / CRONTAB_REL
    crontab = (
        crontab_path.read_text(encoding="utf-8") if crontab_path.is_file() else ""
    )
    result = {
        "new_ledger_exists": db_path.exists(),
        "opening_cash_is_placeholder": opening_cash == PLACEHOLDER_CASH,
        "old_cron_token": OLD_CONFIG_ID in crontab,
        "parity_ok": None if skip_parity else _parity_ok(project_root),
        "sha_ok": None if skip_sha else _sha_ok(project_root, config),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-parity", action="store_true")
    parser.add_argument("--skip-sha", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    result = cutover_preflight(
        root, skip_parity=args.skip_parity, skip_sha=args.skip_sha,
    )
    for key in sorted(result):
        print("%s: %s" % (key, result[key]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
