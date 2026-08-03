"""Operational wrappers must default to the controlled CSI1000 deployment."""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from live_trading.scripts.batch_status import find_active_batch_id


REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPERS = [
    "run_postclose_cron.sh",
    "run_publish_cron.sh",
    "run_publish_catchup_cron.sh",
    "run_import_cron.sh",
    "run_monitor_cron.sh",
]


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _run_postclose_fixture(
    tmp_path,
    *,
    import_status=0,
    postmarket_status=0,
    update_status=0,
    report_status=0,
):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    live_dir.mkdir(parents=True)
    source = REPO_ROOT / "live_trading" / "run_postclose_cron.sh"
    assert source.exists(), "run_postclose_cron.sh must exist"
    wrapper = live_dir / source.name
    shutil.copy2(source, wrapper)

    _write_executable(
        live_dir / "run_import_cron.sh",
        "#!/usr/bin/env bash\n"
        "printf 'import\\n' >> \"$POSTCLOSE_TEST_TRACE\"\n"
        "exit \"${FAKE_IMPORT_STATUS:-0}\"\n",
    )
    _write_executable(
        live_dir / "run_monitor_cron.sh",
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"postmarket\" ]]; then\n"
        "  printf 'postmarket\\n' >> \"$POSTCLOSE_TEST_TRACE\"\n"
        "  exit \"${FAKE_POSTMARKET_STATUS:-0}\"\n"
        "fi\n"
        "printf 'report\\n' >> \"$POSTCLOSE_TEST_TRACE\"\n"
        "exit \"${FAKE_REPORT_STATUS:-0}\"\n",
    )
    _write_executable(
        root / "scripts/data_collector/tushare/run_update_to_bin.sh",
        "#!/usr/bin/env bash\n"
        "printf 'update\\n' >> \"$POSTCLOSE_TEST_TRACE\"\n"
        "exit \"${FAKE_UPDATE_STATUS:-0}\"\n",
    )

    trace_path = tmp_path / "trace.txt"
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp_path / "home"),
        "POSTCLOSE_TEST_TRACE": str(trace_path),
        "FAKE_IMPORT_STATUS": str(import_status),
        "FAKE_POSTMARKET_STATUS": str(postmarket_status),
        "FAKE_UPDATE_STATUS": str(update_status),
        "FAKE_REPORT_STATUS": str(report_status),
    })
    result = subprocess.run(
        ["bash", str(wrapper), "csi1000_b6m_b2s_postclose"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    trace = trace_path.read_text(encoding="utf-8").splitlines() \
        if trace_path.exists() else []
    return result, trace


def test_postclose_continues_to_update_after_import_failure(tmp_path):
    result, trace = _run_postclose_fixture(tmp_path, import_status=1)

    assert result.returncode != 0
    assert trace == ["import", "postmarket", "update", "report"]


def test_postclose_skips_report_when_update_fails(tmp_path):
    result, trace = _run_postclose_fixture(tmp_path, update_status=1)

    assert result.returncode != 0
    assert trace == ["import", "postmarket", "update"]
    assert "report skipped: market data update failed" in result.stdout


def test_postclose_success_is_serial_and_zero(tmp_path):
    result, trace = _run_postclose_fixture(tmp_path)

    assert result.returncode == 0
    assert trace == ["import", "postmarket", "update", "report"]


def test_publish_wrappers_refuse_postclose_overlap(tmp_path):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    lock_dir = (
        live_dir / ".locks" /
        "csi1000_b6m_b2s_postclose_postclose.lock"
    )
    lock_dir.mkdir(parents=True)
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp_path / "home"),
        "QMT_SIM_ACCOUNT_ID": "test-account",
        "LIVE_RUN_MODE": "SIMULATE",
    })

    for name in ("run_publish_cron.sh", "run_publish_catchup_cron.sh"):
        source = REPO_ROOT / "live_trading" / name
        wrapper = live_dir / name
        shutil.copy2(source, wrapper)

        result = subprocess.run(
            ["bash", str(wrapper), "csi1000_b6m_b2s_postclose"],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 75, (name, result.stdout, result.stderr)
        assert "postclose pipeline holds" in result.stderr


def test_wrappers_are_configurable_and_default_to_new_simulation_system():
    for name in WRAPPERS:
        text = (REPO_ROOT / "live_trading" / name).read_text(encoding="utf-8")
        assert "csi1000_b6m_b2s_postclose" in text
        assert "LIVE_CONFIG_ID" in text
        assert "QLIB_LIVE_CONFIG_ID" in text
        assert "LOCK_DIR" in text
        assert 'CONFIG_ID="csi300_topk10_live"' not in text


def test_wrappers_do_not_swallow_monitor_failures_or_embed_python_stdin():
    combined = "\n".join(
        (REPO_ROOT / "live_trading" / name).read_text(encoding="utf-8")
        for name in WRAPPERS
    )
    assert "|| true" not in combined
    assert "<<'PY'" not in combined
    assert "batch_status.py" in combined


def test_wrappers_log_the_real_exit_status_while_releasing_locks():
    for name in WRAPPERS:
        text = (REPO_ROOT / "live_trading" / name).read_text(encoding="utf-8")
        assert "finish_job()" in text
        assert "job_status=$?" in text
        assert "trap finish_job EXIT" in text
        assert 'exit "$job_status"' in text


def test_batch_status_finds_latest_active_batch(tmp_path):
    db = tmp_path / "live.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE batches (batch_id TEXT PRIMARY KEY, trade_date TEXT, "
            "superseded_by TEXT)"
        )
        conn.executemany(
            "INSERT INTO batches VALUES (?,?,?)",
            [
                ("20260803_s_001", "2026-08-03", "20260803_s_002"),
                ("20260803_s_002", "2026-08-03", None),
            ],
        )

    assert find_active_batch_id(db, "2026-08-03") == "20260803_s_002"
    assert find_active_batch_id(db, "2026-08-04") == ""
