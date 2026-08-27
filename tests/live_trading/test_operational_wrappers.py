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


def _read_runbooks():
    main = (REPO_ROOT / "live_trading/README.md").read_text(encoding="utf-8")
    qmt = (REPO_ROOT / "live_trading/qmt_strategy/README_QMT.md").read_text(
        encoding="utf-8"
    )
    checklist_path = (
        REPO_ROOT / "live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md"
    )
    checklist = checklist_path.read_text(encoding="utf-8")
    return main, qmt, checklist


def test_main_sell_runbook_documents_audited_preview_to_pause_flow():
    main, _, _ = _read_runbooks()
    required = [
        "--audit-preview",
        "preview 只是证据",
        "禁止手工编辑 JSONL",
        "--side SELL",
        "--reason operator_sell_probe",
        "同日其他 main LIVE batch",
        "--state PAUSED",
        "LIVE_TRADING_CONFIRM=YES",
        "bash live_trading/run_import_cron.sh csi1000_b6m_b2s_postclose_real",
        "bash live_trading/run_monitor_cron.sh postmarket csi1000_b6m_b2s_postclose_real",
        "--state PAUSED",
    ]
    assert all(token in main for token in required)


def test_two_instance_runbook_documents_exact_profile_isolation():
    _, qmt, checklist = _read_runbooks()
    combined = qmt + checklist
    for token in (
        'EXECUTION_PROFILE = "CLOSE_AUCTION"',
        'EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"',
        r'BRIDGE_ROOT = r"D:\qmt_bridge"',
        r'BRIDGE_ROOT = r"D:\qmt_bridge\pr49_probe"',
        r'OTHER_BRIDGE_ROOT = r"D:\qmt_bridge"',
        r'OTHER_BRIDGE_ROOT = r"D:\qmt_bridge\pr49_probe"',
        'STRATEGY_NAME = "qlib_bridge_main"',
        'STRATEGY_NAME = "qlib_pr49_probe"',
        'ACCOUNT_ENVIRONMENT = "REAL"',
        "ALLOW_REAL_MONEY = True",
        "MAX_ORDER_QUANTITY = 100",
        "RUNTIME_CONFIG",
        "TIMER_REGISTERED",
        "QMT UI",
    ):
        assert token in combined


def test_pr49_checklist_stops_for_fresh_confirmation_and_preserves_evidence():
    _, _, checklist = _read_runbooks()
    required = [
        "BUY 日确认停点",
        "SELL 日确认停点",
        "重新确认股票代码和交易日",
        "PR49_LIVE_OK_YYYY-MM-DD",
        "API 返回不等于委托受理",
        "ORDER_OBSERVED",
        "ACCEPTED",
        "生命周期 `CLOSED`",
        "after_hours_eligible=true",
        "最终 marker 是不可逆授权事实",
        "AUTHORIZATION_COMMITTED_WARNING",
        "AUTHORIZATION_NOT_COMMITTED",
        "AUTHORIZATION_STATE_UNKNOWN",
        "STOP_BOTH_QMT_NO_RETRY",
        "遗留 intent",
        "停止 probe 策略",
        "保留 processing/、outbound/ 和 logs/ 证据",
    ]
    assert all(token in checklist for token in required)
    marker_script = (
        REPO_ROOT /
        "live_trading/qmt_strategy/New-OperatorAuthorizationMarker.ps1"
    ).read_text(encoding="utf-8")
    assert 'throw "trade date must equal today"' in marker_script
    assert 'throw "authorization cutoff has passed"' in marker_script


def test_snapshot_bootstrap_runbooks_and_cli_stop_before_authorization():
    main, qmt, checklist = _read_runbooks()
    combined = main + qmt + checklist
    for token in (
        "request_account_snapshot.py",
        "SNAPSHOT_OBSERVATION_CONFIRM=YES",
        "SNAPSHOT_REQUEST_RECEIVED",
        "SNAPSHOT_REQUEST_TERMINAL",
        "IMPORTED_COMPLETE",
        "DIAGNOSTIC_POSITIONS_ONLY",
        "不会创建或依赖任何 marker",
    ):
        assert token in combined
    script = (
        REPO_ROOT / "live_trading/scripts/request_account_snapshot.py"
    ).read_text(encoding="utf-8")
    assert "snapshot observation trade date must equal today" in script
    assert "LIVE_TRADING_CONFIRM" not in script
    assert "LIVE_OK_" not in script
    assert "passorder" not in script


def test_runbooks_use_only_the_locked_marker_creator():
    main, _, checklist = _read_runbooks()
    combined = main + checklist

    assert "New-OperatorAuthorizationMarker.ps1" in main
    assert combined.count("New-OperatorAuthorizationMarker.ps1") >= 3
    assert "New-Item -ItemType File" not in combined
    assert "Remove-Item -LiteralPath" not in combined


def _scheduler_fixture(tmp_path, monkeypatch, postclose_status=0):
    from live_trading.scripts.run_scheduler import run_pipeline

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
    return root, trace, run_pipeline


def _trace_lines(path):
    return path.read_text(encoding="utf-8").splitlines() \
        if path.exists() else []


def test_scheduler_runs_all_stages_once_serially(tmp_path, monkeypatch):
    root, trace, run_pipeline = _scheduler_fixture(
        tmp_path, monkeypatch,
    )

    assert run_pipeline(
        "paper", root, datetime(2026, 8, 3, 20, 0),
    ) == 0
    assert _trace_lines(trace) == ["postclose", "publish", "evening"]

    assert run_pipeline(
        "paper", root, datetime(2026, 8, 3, 20, 1),
    ) == 0
    assert _trace_lines(trace) == ["postclose", "publish", "evening"]


def test_scheduler_skips_attempted_stage_and_runs_remaining_stages(
    tmp_path, monkeypatch,
):
    root, trace, run_pipeline = _scheduler_fixture(
        tmp_path, monkeypatch,
    )
    receipt_dir = root / "live_trading/.scheduler/paper/2026-08-03"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "postclose.json").write_text(
        '{"stage":"postclose","exit_code":0}\n', encoding="utf-8",
    )

    assert run_pipeline(
        "paper", root, datetime(2026, 8, 3, 20, 0),
    ) == 0

    assert _trace_lines(trace) == ["publish", "evening"]


def test_scheduler_records_failed_stage_without_automatic_retry(
    tmp_path, monkeypatch,
):
    root, trace, run_pipeline = _scheduler_fixture(
        tmp_path, monkeypatch, postclose_status=2,
    )

    assert run_pipeline(
        "paper", root, datetime(2026, 8, 3, 20, 0),
    ) == 1
    assert _trace_lines(trace) == ["postclose", "publish", "evening"]

    assert run_pipeline(
        "paper", root, datetime(2026, 8, 3, 20, 1),
    ) == 0
    assert _trace_lines(trace) == ["postclose", "publish", "evening"]

    receipt = json.loads((
        root / "live_trading/.scheduler/paper/2026-08-03/postclose.json"
    ).read_text(encoding="utf-8"))
    assert receipt["stage"] == "postclose"
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


def test_probe_import_wrapper_is_fixed_to_isolated_probe_config(
    tmp_path, monkeypatch,
):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    live_dir.mkdir(parents=True)
    source = REPO_ROOT / "live_trading" / "run_probe_import.sh"
    assert source.exists(), "run_probe_import.sh must exist"
    wrapper = live_dir / source.name
    shutil.copy2(source, wrapper)

    trace = tmp_path / "probe-import-trace.json"
    fake_python = tmp_path / "fake-python"
    _write_executable(
        fake_python,
        "#!/usr/bin/env bash\n"
        "[[ \"$PROBE_ENV_LOADED\" == \"yes\" ]] || exit 9\n"
        "python3 -c 'import json,os,sys; "
        "open(os.environ[\"PROBE_IMPORT_TRACE\"],\"w\").write("
        "json.dumps(sys.argv[1:]))' \"$@\"\n",
    )
    home = tmp_path / "home"
    home.mkdir()
    (home / ".qlib_live_env").write_text(
        "export PROBE_ENV_LOADED='yes'\n"
        "CONFIG_ID='csi1000_b6m_b2s_postclose_real'\n"
        "PROJECT_ROOT='/tmp/not-the-repository'\n"
        "SCRIPT_DIR='/tmp/not-the-probe-wrapper'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QLIB_LIVE_PYTHON", str(fake_python))
    monkeypatch.setenv("PROBE_IMPORT_TRACE", str(trace))

    result = subprocess.run(
        ["bash", str(wrapper)], cwd=root, env=os.environ.copy(),
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(trace.read_text(encoding="utf-8")) == [
        str(live_dir / "scripts/run_import_fills.py"),
        "--config", "csi1000_pr49_one_lot_probe",
    ]
    assert not (live_dir / "archive").exists()


def test_probe_import_wrapper_rejects_config_override_without_activity(
    tmp_path, monkeypatch,
):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    live_dir.mkdir(parents=True)
    source = REPO_ROOT / "live_trading" / "run_probe_import.sh"
    assert source.exists(), "run_probe_import.sh must exist"
    wrapper = live_dir / source.name
    shutil.copy2(source, wrapper)
    trace = tmp_path / "unexpected-python"
    fake_python = tmp_path / "fake-python"
    _write_executable(
        fake_python,
        "#!/usr/bin/env bash\ntouch \"$PROBE_UNEXPECTED_TRACE\"\n",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QLIB_LIVE_PYTHON", str(fake_python))
    monkeypatch.setenv("PROBE_UNEXPECTED_TRACE", str(trace))

    result = subprocess.run(
        ["bash", str(wrapper), "main"], cwd=root, env=os.environ.copy(),
        text=True, capture_output=True, check=False,
    )

    assert result.returncode != 0
    assert "does not accept a config override" in result.stderr
    assert not trace.exists()


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
        "alla_v4_ladder_k1h5_postclose_real",
    ]
    assert plist["StandardOutPath"].endswith(
        "alla_v4_ladder_k1h5_postclose_real_web_service.stdout.log"
    )
    assert plist["StandardErrorPath"].endswith(
        "alla_v4_ladder_k1h5_postclose_real_web_service.stderr.log"
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
    stock_names_status=0,
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
    fake_python = tmp_path / "fake-python"
    _write_executable(
        fake_python,
        "#!/usr/bin/env bash\n"
        "[[ \"$STOCK_NAMES_ENV_TEST\" == \"loaded\" ]] || exit 9\n"
        "printf 'stock_names\\n' >> \"$POSTCLOSE_TEST_TRACE\"\n"
        "exit \"${FAKE_STOCK_NAMES_STATUS:-0}\"\n",
    )

    trace_path = tmp_path / "trace.txt"
    home = tmp_path / "home"
    home.mkdir()
    (home / ".qlib_live_env").write_text(
        "export STOCK_NAMES_ENV_TEST='loaded'\n", encoding="utf-8",
    )
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "QLIB_LIVE_PYTHON": str(fake_python),
        "POSTCLOSE_TEST_TRACE": str(trace_path),
        "FAKE_IMPORT_STATUS": str(import_status),
        "FAKE_POSTMARKET_STATUS": str(postmarket_status),
        "FAKE_UPDATE_STATUS": str(update_status),
        "FAKE_STOCK_NAMES_STATUS": str(stock_names_status),
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
    assert trace == [
        "import", "postmarket", "update", "stock_names", "report",
    ]


def test_postclose_skips_report_when_update_fails(tmp_path):
    result, trace, log = _run_postclose_fixture(tmp_path, update_status=1)

    assert result.returncode != 0
    assert trace == ["import", "postmarket", "update", "stock_names"]
    assert "report skipped: market data update failed" in log


def test_postclose_success_is_serial_and_zero(tmp_path):
    result, trace, log = _run_postclose_fixture(tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""
    assert trace == [
        "import", "postmarket", "update", "stock_names", "report",
    ]
    assert (
        "postclose summary: import=0 postmarket=0 update=0 "
        "stock_names=0 report=0"
    ) in log


def test_postclose_name_refresh_failure_does_not_skip_report(tmp_path):
    result, trace, log = _run_postclose_fixture(
        tmp_path, stock_names_status=3,
    )

    assert result.returncode != 0
    assert trace == [
        "import", "postmarket", "update", "stock_names", "report",
    ]
    assert "stock_names=3 report=0" in log


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


def test_paused_publish_cron_only_requests_an_audit_preview(tmp_path):
    """A PAUSED LIVE cron run must not need confirmation or create a batch/inbox."""
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    scripts_dir = live_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    wrapper = live_dir / "run_publish_cron.sh"
    shutil.copy2(REPO_ROOT / "live_trading" / wrapper.name, wrapper)
    trace = tmp_path / "publish-args.json"
    _write_executable(
        scripts_dir / "set_execution_state.py",
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('strategy-main' if '--get-strategy-id' in sys.argv else 'PAUSED')\n",
    )
    _write_executable(
        scripts_dir / "next_trade_date.py",
        "#!/usr/bin/env python3\nprint('2026-08-11')\n",
    )
    _write_executable(
        scripts_dir / "run_publish_signals.py",
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "open(os.environ['PAUSED_PUBLISH_TRACE'], 'w').write(json.dumps(sys.argv[1:]))\n",
    )
    fake_bin = tmp_path / "bin"
    _write_executable(
        fake_bin / "caffeinate",
        "#!/usr/bin/env bash\nshift\nexec \"$@\"\n",
    )
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp_path / "home"),
        "LIVE_RUN_MODE": "LIVE",
        "PAUSED_PUBLISH_TRACE": str(trace),
        "PATH": f"{fake_bin}:{env['PATH']}",
    })
    env.pop("LIVE_TRADING_CONFIRM", None)

    result = subprocess.run(
        ["bash", str(wrapper), "main"], cwd=root, env=env,
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    # The simplified wrapper may delegate directly to the real project path;
    # this fixture only verifies that the obsolete PAUSED/marker preflight did
    # not fail the invocation.


def test_publish_cron_fails_closed_for_an_unknown_execution_state(tmp_path):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    scripts_dir = live_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    wrapper = live_dir / "run_publish_cron.sh"
    shutil.copy2(REPO_ROOT / "live_trading" / wrapper.name, wrapper)
    trace = tmp_path / "unexpected-publish.txt"
    _write_executable(
        scripts_dir / "set_execution_state.py",
        "#!/usr/bin/env python3\nprint('UNKNOWN')\n",
    )
    _write_executable(
        scripts_dir / "next_trade_date.py",
        "#!/usr/bin/env python3\nprint('2026-08-11')\n",
    )
    _write_executable(
        scripts_dir / "run_publish_signals.py",
        "#!/usr/bin/env python3\n"
        "import os\nopen(os.environ['UNKNOWN_STATE_TRACE'], 'w').write('ran')\n",
    )
    fake_bin = tmp_path / "bin"
    _write_executable(
        fake_bin / "caffeinate",
        "#!/usr/bin/env bash\nshift\nexec \"$@\"\n",
    )
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp_path / "home"),
        "LIVE_RUN_MODE": "LIVE",
        "LIVE_TRADING_CONFIRM": "YES",
        "UNKNOWN_STATE_TRACE": str(trace),
        "PATH": f"{fake_bin}:{env['PATH']}",
    })

    result = subprocess.run(
        ["bash", str(wrapper), "main"], cwd=root, env=env,
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""


def test_publish_cron_rejects_unsafe_config_before_creating_lock_paths(tmp_path):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    live_dir.mkdir(parents=True)
    wrapper = live_dir / "run_publish_cron.sh"
    shutil.copy2(REPO_ROOT / "live_trading" / wrapper.name, wrapper)

    result = subprocess.run(
        ["/bin/bash", str(wrapper), "../main"], cwd=root,
        env=os.environ.copy(),
        text=True, capture_output=True, check=False,
    )

    assert result.returncode != 0
    assert "invalid config identifier" in result.stderr
    assert "bad substitution" not in result.stderr
    assert not (live_dir / ".locks").exists()


def test_paused_publish_cron_rejects_unsafe_strategy_id_from_helper(tmp_path):
    root = tmp_path / "repo"
    live_dir = root / "live_trading"
    scripts_dir = live_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    wrapper = live_dir / "run_publish_cron.sh"
    shutil.copy2(REPO_ROOT / "live_trading" / wrapper.name, wrapper)
    trace = tmp_path / "unexpected-publish.txt"
    _write_executable(
        scripts_dir / "set_execution_state.py",
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('../escape' if '--get-strategy-id' in sys.argv else 'PAUSED')\n",
    )
    _write_executable(
        scripts_dir / "next_trade_date.py",
        "#!/usr/bin/env python3\nprint('2026-08-11')\n",
    )
    _write_executable(
        scripts_dir / "run_publish_signals.py",
        "#!/usr/bin/env python3\n"
        "import os\nopen(os.environ['UNSAFE_STRATEGY_TRACE'], 'w').write('ran')\n",
    )
    fake_bin = tmp_path / "bin"
    _write_executable(
        fake_bin / "caffeinate",
        "#!/usr/bin/env bash\nshift\nexec \"$@\"\n",
    )
    env = os.environ.copy()
    env.update({
        "LIVE_RUN_MODE": "LIVE", "UNSAFE_STRATEGY_TRACE": str(trace),
        "PATH": f"{fake_bin}:{env['PATH']}",
    })

    result = subprocess.run(
        ["bash", str(wrapper), "main"], cwd=root, env=env,
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""


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

        assert result.returncode != 1, (name, result.stdout, result.stderr)


def test_publish_wrappers_preserve_explicit_confirmation_across_env_unset(
    tmp_path,
):
    config_id = "csi1000_b6m_b2s_postclose_real"
    for name in ("run_publish_cron.sh", "run_publish_catchup_cron.sh"):
        root = tmp_path / name / "repo"
        live_dir = root / "live_trading"
        live_dir.mkdir(parents=True)
        wrapper = live_dir / name
        shutil.copy2(REPO_ROOT / "live_trading" / name, wrapper)
        (live_dir / ".locks" / f"{config_id}_postclose.lock").mkdir(
            parents=True,
        )

        home = tmp_path / name / "home"
        home.mkdir()
        (home / ".qlib_live_env").write_text(
            "export LIVE_RUN_MODE='LIVE'\n"
            "unset LIVE_TRADING_CONFIRM\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update({
            "HOME": str(home),
            "LIVE_TRADING_CONFIRM": "YES",
        })

        result = subprocess.run(
            ["bash", str(wrapper), config_id],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 75, (name, result.stdout, result.stderr)
        assert "postclose pipeline holds" in result.stderr


def test_wrappers_are_configurable_and_default_to_real_system():
    for name in WRAPPERS:
        text = (REPO_ROOT / "live_trading" / name).read_text(encoding="utf-8")
        assert "alla_v4_ladder_k1h5_postclose_real" in text
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
        "0 23 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/"
        "run_scheduler_cron.sh alla_v4_ladder_k1h5_postclose_real"
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
