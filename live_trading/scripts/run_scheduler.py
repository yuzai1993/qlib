#!/usr/bin/env python3
"""Run due CSI1000 post-close stages once per date.

The cron entry invokes this dispatcher every minute. Each attempted stage gets
an atomic receipt, including failures, so automatic retries never loop.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from live_trading.modules.live_config import load_live_config

CONFIGS_DIR = PROJECT_ROOT / "live_trading" / "configs"


def _scheduled_minutes(raw: object, key: str) -> int:
    if not isinstance(raw, str) or not re.fullmatch(r"\d{2}:\d{2}", raw):
        raise ValueError(f"schedule.{key} must be HH:MM")
    hour, minute = (int(part) for part in raw.split(":"))
    if hour > 23 or minute > 59:
        raise ValueError(f"schedule.{key} must be a valid HH:MM time")
    return hour * 60 + minute


def _stage_definitions(config: dict, project_root: Path, config_id: str):
    schedule = config.get("schedule") or {}
    live_dir = project_root / "live_trading"
    return [
        (
            "postclose",
            "import_after",
            schedule.get("import_after"),
            [str(live_dir / "run_postclose_cron.sh"), config_id],
        ),
        (
            "publish",
            "publish_after",
            schedule.get("publish_after"),
            [str(live_dir / "run_publish_cron.sh"), config_id],
        ),
        (
            "evening",
            "integrity_after",
            schedule.get("integrity_after"),
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


def run_due_stages(
    config: dict,
    config_id: str,
    project_root: Path,
    now: datetime,
) -> int:
    """Run all due, not-yet-attempted stages and return 1 on any failure."""
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
        current_minutes = now.hour * 60 + now.minute
        receipt_dir = live_dir / ".scheduler" / config_id / date_key
        receipt_dir.mkdir(parents=True, exist_ok=True)
        overall_status = 0

        for stage, schedule_key, scheduled_for, argv in _stage_definitions(
            config, project_root, config_id,
        ):
            due_minutes = _scheduled_minutes(scheduled_for, schedule_key)
            receipt_path = receipt_dir / f"{stage}.json"
            if current_minutes < due_minutes or receipt_path.exists():
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
                "scheduled_for": scheduled_for,
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

    config = load_live_config(
        CONFIGS_DIR / f"{args.config}.yaml", PROJECT_ROOT,
    )
    return run_due_stages(
        config, args.config, PROJECT_ROOT, datetime.now().astimezone(),
    )


if __name__ == "__main__":
    sys.exit(main())
