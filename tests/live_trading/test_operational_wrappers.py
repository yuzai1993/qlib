"""Operational wrappers must default to the controlled CSI1000 deployment."""

from datetime import datetime
import json
import os
import plistlib
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


def _scheduler_fixture(tmp_path, monkeypatch, postclose_status=0):
    from live_trading.scripts.run_scheduler import run_due_stages

    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    live_dir.mkdir(parents=True)
    trace = tmp_path / "scheduler-trace.txt"
    monkeypatch.setenv("SCHEDULER_TEST_TRACE", str(trace))

    _write_executable(
        live_dir / "run_postclose_cron.sh",
        "#!/usr/bin/env bash\n"
        "printf 'postclose\\n' >> \"$SCHEDULER_TEST_TRACE\"\n"
        f"exit {postclose_status}\n",
    )
    _write_executable(
        live_dir / "run_publish_cron.sh",
        "#!/usr/bin/env bash\n"
        "printf 'publish\\n' >> \"$SCHEDULER_TEST_TRACE\"\n",
    )
    _write_executable(
        live_dir / "run_monitor_cron.sh",
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$1\" >> \"$SCHEDULER_TEST_TRACE\"\n",
    )
    config = {"schedule": {
        "import_after": "20:00",
        "report_after": "after_data_update",
        "publish_after": "21:30",
        "integrity_after": "22:30",
    }}
    return root, trace, config, run_due_stages


def _trace_lines(path):
    return path.read_text(encoding="utf-8").splitlines() \
        if path.exists() else []


def test_scheduler_runs_each_due_stage_once_in_time_order(tmp_path, monkeypatch):
    root, trace, config, run_due_stages = _scheduler_fixture(
        tmp_path, monkeypatch,
    )

    assert run_due_stages(
        config, "paper", root, datetime(2026, 8, 3, 19, 59),
    ) == 0
    assert _trace_lines(trace) == []

    assert run_due_stages(
        config, "paper", root, datetime(2026, 8, 3, 20, 0),
    ) == 0
    assert _trace_lines(trace) == ["postclose"]

    assert run_due_stages(
        config, "paper", root, datetime(2026, 8, 3, 21, 30),
    ) == 0
    assert _trace_lines(trace) == ["postclose", "publish"]

    assert run_due_stages(
        config, "paper", root, datetime(2026, 8, 3, 22, 30),
    ) == 0
    assert _trace_lines(trace) == ["postclose", "publish", "evening"]

    assert run_due_stages(
        config, "paper", root, datetime(2026, 8, 3, 23, 0),
    ) == 0
    assert _trace_lines(trace) == ["postclose", "publish", "evening"]


def test_scheduler_catches_up_all_due_stages_after_same_day_wake(
    tmp_path, monkeypatch,
):
    root, trace, config, run_due_stages = _scheduler_fixture(
        tmp_path, monkeypatch,
    )

    assert run_due_stages(
        config, "paper", root, datetime(2026, 8, 3, 22, 31),
    ) == 0

    assert _trace_lines(trace) == ["postclose", "publish", "evening"]


def test_scheduler_records_failed_stage_without_automatic_retry(
    tmp_path, monkeypatch,
):
    root, trace, config, run_due_stages = _scheduler_fixture(
        tmp_path, monkeypatch, postclose_status=2,
    )

    assert run_due_stages(
        config, "paper", root, datetime(2026, 8, 3, 20, 0),
    ) == 1
    assert run_due_stages(
        config, "paper", root, datetime(2026, 8, 3, 20, 1),
    ) == 0
    assert _trace_lines(trace) == ["postclose"]

    receipt = json.loads((
        root / "live_trading/.scheduler/paper/2026-08-03/postclose.json"
    ).read_text(encoding="utf-8"))
    assert receipt["stage"] == "postclose"
    assert receipt["scheduled_for"] == "20:00"
    assert receipt["exit_code"] == 2


def test_scheduler_cron_wrapper_loads_env_and_forwards_config(
    tmp_path, monkeypatch,
):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    live_dir.mkdir(parents=True)
    wrapper = live_dir / "run_scheduler_cron.sh"
    shutil.copy2(REPO_ROOT / "live_trading" / wrapper.name, wrapper)

    trace = tmp_path / "wrapper-trace.txt"
    fake_python = tmp_path / "fake-python"
    _write_executable(
        fake_python,
        "#!/usr/bin/env bash\n"
        "printf '%s|%s|%s\\n' \"$SCHEDULER_ENV_TEST\" \"$1\" \"$3\" "
        "> \"$SCHEDULER_WRAPPER_TRACE\"\n",
    )
    home = tmp_path / "home"
    home.mkdir()
    (home / ".qlib_live_env").write_text(
        "export SCHEDULER_ENV_TEST='loaded'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QLIB_LIVE_PYTHON", str(fake_python))
    monkeypatch.setenv("SCHEDULER_WRAPPER_TRACE", str(trace))

    result = subprocess.run(
        ["bash", str(wrapper), "custom-paper"],
        cwd=root,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    loaded, script_path, config_id = trace.read_text(
        encoding="utf-8"
    ).strip().split("|")
    assert loaded == "loaded"
    assert script_path.endswith("live_trading/scripts/run_scheduler.py")
    assert config_id == "custom-paper"


def test_web_service_wrapper_loads_env_and_forwards_config(
    tmp_path, monkeypatch,
):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    live_dir.mkdir(parents=True)
    wrapper = live_dir / "run_web_service.sh"
    shutil.copy2(REPO_ROOT / "live_trading" / wrapper.name, wrapper)

    trace = tmp_path / "web-wrapper-trace.txt"
    fake_python = tmp_path / "fake-python"
    _write_executable(
        fake_python,
        "#!/usr/bin/env bash\n"
        "printf '%s|%s|%s\\n' \"$WEB_ENV_TEST\" \"$1\" \"$3\" "
        "> \"$WEB_WRAPPER_TRACE\"\n",
    )
    home = tmp_path / "home"
    home.mkdir()
    (home / ".qlib_live_env").write_text(
        "export WEB_ENV_TEST='loaded'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QLIB_LIVE_PYTHON", str(fake_python))
    monkeypatch.setenv("WEB_WRAPPER_TRACE", str(trace))

    result = subprocess.run(
        ["bash", str(wrapper), "custom-paper"],
        cwd=root,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    loaded, script_path, config_id = trace.read_text(
        encoding="utf-8"
    ).strip().split("|")
    assert loaded == "loaded"
    assert script_path.endswith("live_trading/scripts/run_web.py")
    assert config_id == "custom-paper"


def test_monitor_launch_agent_owns_loopback_service():
    path = (
        REPO_ROOT / "live_trading/launchd/"
        "com.yuxianqi.qlib-live-monitor.plist"
    )
    with path.open("rb") as handle:
        plist = plistlib.load(handle)

    assert plist["Label"] == "com.yuxianqi.qlib-live-monitor"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] == 10
    assert plist["ProcessType"] == "Background"
    assert plist["WorkingDirectory"] == "/Users/yuxianqi/Project/qlib"
    assert plist["ProgramArguments"] == [
        "/bin/bash",
        "/Users/yuxianqi/Project/qlib/live_trading/run_web_service.sh",
        "csi1000_b6m_b2s_postclose",
    ]
    assert plist["StandardOutPath"].endswith(
        "csi1000_b6m_b2s_postclose_web_service.stdout.log"
    )
    assert plist["StandardErrorPath"].endswith(
        "csi1000_b6m_b2s_postclose_web_service.stderr.log"
    )


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
    publish_lock=False,
):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    live_dir.mkdir(parents=True)
    source = REPO_ROOT / "live_trading" / "run_postclose_cron.sh"
    assert source.exists(), "run_postclose_cron.sh must exist"
    wrapper = live_dir / source.name
    shutil.copy2(source, wrapper)
    if publish_lock:
        (
            live_dir / ".locks" /
            "csi1000_b6m_b2s_postclose_publish.lock"
        ).mkdir(parents=True)

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
    log_path = (
        live_dir / "logs" /
        "csi1000_b6m_b2s_postclose_postclose_cron.log"
    )
    log_text = log_path.read_text(encoding="utf-8") \
        if log_path.exists() else ""
    return result, trace, log_text


def test_postclose_continues_to_update_after_import_failure(tmp_path):
    result, trace, _log = _run_postclose_fixture(tmp_path, import_status=1)

    assert result.returncode != 0
    assert trace == ["import", "postmarket", "update", "report"]


def test_postclose_skips_report_when_update_fails(tmp_path):
    result, trace, log = _run_postclose_fixture(tmp_path, update_status=1)

    assert result.returncode != 0
    assert trace == ["import", "postmarket", "update"]
    assert "report skipped: market data update failed" in log


def test_postclose_success_is_serial_and_zero(tmp_path):
    result, trace, log = _run_postclose_fixture(tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""
    assert trace == ["import", "postmarket", "update", "report"]
    assert "postclose summary: import=0 postmarket=0 update=0 report=0" in log


def test_postclose_refuses_active_publish(tmp_path):
    result, trace, _log = _run_postclose_fixture(
        tmp_path, publish_lock=True,
    )

    assert result.returncode == 75
    assert trace == []
    assert "publish job holds" in result.stderr


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


def test_publish_rechecks_postclose_after_taking_publish_lock(tmp_path):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    scripts_dir = live_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    wrapper = live_dir / "run_publish_cron.sh"
    shutil.copy2(REPO_ROOT / "live_trading" / wrapper.name, wrapper)
    postclose_lock = (
        live_dir / ".locks" /
        "csi1000_b6m_b2s_postclose_postclose.lock"
    )
    next_date_script = scripts_dir / "next_trade_date.py"
    next_date_script.write_text(
        "from pathlib import Path\n"
        f"Path({str(postclose_lock)!r}).mkdir(parents=True)\n"
        "print('2026-08-04')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp_path / "home"),
        "QMT_SIM_ACCOUNT_ID": "test-account",
        "LIVE_RUN_MODE": "SIMULATE",
    })

    result = subprocess.run(
        ["bash", str(wrapper), "csi1000_b6m_b2s_postclose"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 75, (result.stdout, result.stderr)
    assert "postclose pipeline holds" in result.stderr


def test_publish_wrappers_load_run_mode_from_cron_env_file(tmp_path):
    for name in ("run_publish_cron.sh", "run_publish_catchup_cron.sh"):
        root = tmp_path / name / "repo"
        live_dir = root / "live_trading"
        live_dir.mkdir(parents=True)
        wrapper = live_dir / name
        shutil.copy2(REPO_ROOT / "live_trading" / name, wrapper)

        home = tmp_path / name / "home"
        home.mkdir()
        (home / ".qlib_live_env").write_text(
            "export QMT_SIM_ACCOUNT_ID='paper-account'\n"
            "export LIVE_RUN_MODE='LIVE'\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["HOME"] = str(home)
        for key in (
            "QMT_SIM_ACCOUNT_ID", "LIVE_RUN_MODE", "LIVE_TRADING_CONFIRM",
        ):
            env.pop(key, None)

        result = subprocess.run(
            ["bash", str(wrapper), "csi1000_b6m_b2s_postclose"],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 1, (name, result.stdout, result.stderr)
        assert "LIVE_TRADING_CONFIRM=YES" in result.stderr


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


def test_crontab_uses_one_durable_scheduler_entry():
    text = (
        REPO_ROOT / "live_trading" / "crontab.csi1000_postclose.example"
    ).read_text(encoding="utf-8")
    commands = [
        line.strip() for line in text.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and not line.startswith(("SHELL=", "PATH="))
    ]

    assert commands == [
        "* * * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/"
        "run_scheduler_cron.sh csi1000_b6m_b2s_postclose"
    ]
    assert "run_publish_catchup_cron.sh" not in text


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
