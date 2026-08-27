#!/usr/bin/env python3
"""Run the CSI1000 post-close pipeline once per date.

The cron entry invokes this dispatcher once at 23:00. Each attempted stage gets
an atomic receipt, including failures, so manual reruns never duplicate work.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

def _stage_definitions(project_root: Path, config_id: str):
    live_dir = project_root / "live_trading"
    return [
        (
            "postclose",
            [str(live_dir / "run_postclose_cron.sh"), config_id],
        ),
        (
            "publish",
            [str(live_dir / "run_publish_cron.sh"), config_id],
        ),
        (
            "evening",
            [str(live_dir / "run_monitor_cron.sh"), "evening", config_id],
        ),
    ]


def _write_receipt(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run_pipeline(
    config_id: str,
    project_root: Path,
    now: datetime,
) -> int:
    """Run all not-yet-attempted stages serially; return 1 on any failure."""
    project_root = Path(project_root)
    live_dir = project_root / "live_trading"
    lock_root = live_dir / ".locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_dir = lock_root / f"{config_id}_scheduler.lock"
    try:
        lock_dir.mkdir()
    except FileExistsError:
        print(f"another scheduler holds {lock_dir}", file=sys.stderr)
        return 75

    try:
        date_key = now.strftime("%Y-%m-%d")
        receipt_dir = live_dir / ".scheduler" / config_id / date_key
        receipt_dir.mkdir(parents=True, exist_ok=True)
        overall_status = 0

        for stage, argv in _stage_definitions(project_root, config_id):
            receipt_path = receipt_dir / f"{stage}.json"
            if receipt_path.exists():
                continue

            started_at = datetime.now().astimezone().isoformat(timespec="seconds")
            result = subprocess.run(
                ["/bin/bash", *argv],
                cwd=project_root,
                check=False,
            )
            finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
            _write_receipt(receipt_path, {
                "stage": stage,
                "started_at": started_at,
                "finished_at": finished_at,
                "exit_code": int(result.returncode),
            })
            print(
                f"scheduler {date_key} stage={stage} "
                f"exit_status={result.returncode}"
            )
            if result.returncode != 0:
                overall_status = 1

        return overall_status
    finally:
        lock_dir.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="live config id")
    args = parser.parse_args()

    return run_pipeline(
        args.config, PROJECT_ROOT, datetime.now().astimezone(),
    )


if __name__ == "__main__":
    sys.exit(main())
