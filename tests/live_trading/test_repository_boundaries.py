from pathlib import Path
import os

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_obsolete_paper_trading_application_is_removed():
    assert not (REPO_ROOT / "paper_trading").exists()
    assert not (REPO_ROOT / "tests/paper_trading").exists()


def test_live_runtime_has_no_paper_trading_reference():
    live_root = REPO_ROOT / "live_trading"
    offenders = []
    for pattern in ("*.py", "*.yaml", "*.sh"):
        for path in live_root.rglob(pattern):
            if "paper_trading" in path.read_text(
                encoding="utf-8", errors="ignore",
            ):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())

    assert offenders == []


@pytest.mark.parametrize(
    "wrapper",
    [
        "run_import_cron.sh",
        "run_monitor_cron.sh",
        "run_publish_cron.sh",
        "run_publish_catchup_cron.sh",
    ],
)
def test_cron_wrappers_are_executable(wrapper):
    path = REPO_ROOT / "live_trading" / wrapper
    assert os.access(path, os.X_OK), f"cron cannot execute {path}"
