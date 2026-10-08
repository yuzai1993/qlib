"""Keep unattended SMB authentication in the user session and date window."""

import os
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_launch_agent_runs_existing_pipeline_in_gui_session_at_production_time():
    path = ROOT / "live_trading/launchd/com.yuxianqi.qlib-live-scheduler.plist"
    assert path.is_file(), "the scheduler needs a user LaunchAgent for keychain access"
    agent = plistlib.loads(path.read_bytes())
    assert agent["LimitLoadToSessionType"] == "Aqua"
    assert agent["StartCalendarInterval"] == [
        {"Weekday": 1, "Hour": 20, "Minute": 0},
        {"Weekday": 2, "Hour": 20, "Minute": 0},
        {"Weekday": 3, "Hour": 20, "Minute": 0},
        {"Weekday": 4, "Hour": 20, "Minute": 0},
        {"Weekday": 5, "Hour": 20, "Minute": 0},
    ]
    assert not agent.get("RunAtLoad", False)
    assert not agent.get("KeepAlive", False)
    assert agent["ProgramArguments"] == [
        "/usr/bin/caffeinate", "-i", "/bin/bash",
        "/Users/yuxianqi/Project/qlib/live_trading/run_scheduler_launchd.sh",
        "alla_v4_ladder_k1h5_postclose_real",
    ]
    assert agent["StandardOutPath"].endswith("_scheduler.stdout.log")
    assert agent["StandardErrorPath"].endswith("_scheduler.stderr.log")


@pytest.mark.parametrize("stamp,should_run", [
    ("2026-09-14 1 1959", False), ("2026-09-14 1 2000", True),
    ("2026-09-18 5 2359", True), ("2026-09-19 6 2030", False),
    ("2026-09-20 7 2030", False), ("2026-09-15 2 0010", False),
])
def test_wake_delivery_only_runs_inside_same_day_postclose_window(tmp_path, stamp, should_run):
    source = ROOT / "live_trading/run_scheduler_launchd.sh"
    assert source.is_file(), "launchd wake delivery must not publish against the wrong day"
    live = tmp_path / "repo with spaces" / "live_trading"
    live.mkdir(parents=True)
    wrapper = live / source.name
    shutil.copy2(source, wrapper)
    (live / "run_scheduler_cron.sh").write_text(
        '#!/bin/bash\nprintf "%s\\n" "$@" "$QLIB_LIVE_BUSINESS_DATE" > "$SCHEDULER_TRACE"\nexit 17\n'
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_date = bin_dir / "date"
    fake_date.write_text('#!/bin/bash\nprintf "%s\\n" "$TEST_SCHEDULE_TIME"\n')
    fake_date.chmod(0o755)
    trace = tmp_path / "trace"
    env = dict(os.environ, PATH=str(bin_dir) + ":/usr/bin:/bin",
               TEST_SCHEDULE_TIME=stamp, SCHEDULER_TRACE=str(trace))
    result = subprocess.run(
        ["/bin/bash", str(wrapper), "sample-live"], env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == (17 if should_run else 0), result.stderr
    assert trace.exists() is should_run
    if should_run:
        assert trace.read_text().splitlines() == ["sample-live", stamp.split()[0]]
    else:
        assert "skipped" in result.stdout


def test_scheduler_retains_entry_business_date_for_receipts_and_all_stages(tmp_path):
    live = tmp_path / "live_trading"
    scripts = live / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "live_trading/scripts/run_scheduler.py", scripts)
    # Exercise the real scheduler CLI with only its network calendar replaced.
    (scripts / "next_trade_date.py").write_text(
        'def is_open_date(day):\n    return day == "2026-09-14"\n'
    )
    trace = tmp_path / "dates"
    for name in ("run_postclose_cron.sh", "run_publish_cron.sh", "run_monitor_cron.sh"):
        (live / name).write_text('#!/bin/bash\necho "$QLIB_LIVE_BUSINESS_DATE" >> "$DATE_TRACE"\n')
    result = subprocess.run(
        [sys.executable, str(scripts / "run_scheduler.py"), "--config", "paper"],
        env=dict(os.environ, QLIB_LIVE_BUSINESS_DATE="2026-09-14", DATE_TRACE=str(trace)),
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == ["2026-09-14"] * 3
    assert sorted(p.name for p in (live / ".scheduler/paper/2026-09-14").glob("*.json")) == [
        "evening.json", "postclose.json", "publish.json",
    ]


@pytest.mark.parametrize("wrapper,business_date", [
    ("monitor", "2026-09-14"), ("publish", "2026-09-14"),
    ("data", "2026-09-14"), ("data", None),
])
def test_stage_commands_use_pinned_date_instead_of_current_wall_clock(tmp_path, wrapper, business_date):
    root = tmp_path / "repo"
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "commands.jsonl"
    fake_python = bin_dir / "python"
    fake_python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['COMMAND_TRACE'], 'a') as out:\n"
        "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1].endswith('next_trade_date.py'):\n"
        "    print('2026-09-15')\n"
    )
    fake_python.chmod(0o755)
    (bin_dir / "caffeinate").write_text('#!/bin/bash\nshift\nexec "$@"\n')
    (bin_dir / "caffeinate").chmod(0o755)
    (bin_dir / "date").write_text('#!/bin/bash\necho 2026-09-15\n')
    (bin_dir / "date").chmod(0o755)
    relative = {
        "monitor": "live_trading/run_monitor_cron.sh",
        "publish": "live_trading/run_publish_cron.sh",
        "data": "scripts/data_collector/tushare/run_update_to_bin.sh",
    }[wrapper]
    script = root / relative
    script.parent.mkdir(parents=True)
    script.write_text((ROOT / relative).read_text().replace(
        "/opt/anaconda3/envs/qlib/bin/python", str(fake_python),
    ))
    args = ["report", "paper"] if wrapper == "monitor" else (["paper"] if wrapper == "publish" else [])
    env = dict(os.environ, HOME=str(home), PATH=str(bin_dir) + ":/usr/bin:/bin",
               COMMAND_TRACE=str(trace))
    env.pop("QLIB_LIVE_BUSINESS_DATE", None)
    if business_date:
        env["QLIB_LIVE_BUSINESS_DATE"] = business_date
    result = subprocess.run(
        ["/bin/bash", str(script), *args],
        env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in trace.read_text().splitlines()]
    suffix, flag = {
        "monitor": ("run_monitor.py", "--date"),
        "publish": ("next_trade_date.py", "--after"),
        "data": ("collector.py", "--end_date"),
    }[wrapper]
    command = next(row for row in commands if row[0].endswith(suffix))
    assert flag in command
    assert command[command.index(flag) + 1] == ("2026-09-14" if business_date else "2026-09-15")
    if wrapper == "publish":
        publish = next(row for row in commands if row[0].endswith("run_publish_signals.py"))
        assert publish[publish.index("--trade-date") + 1] == "2026-09-15"
