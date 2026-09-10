"""Mount the real configured bridge, without mkdir privileges or login UI."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from live_trading.scripts import ensure_bridge_mount as mounting


def _mount_result(status=0):
    return subprocess.CompletedProcess([], 0, json.dumps({"status": status}), "")


def test_missing_volumes_directory_is_created_by_netfs_not_mkdir(monkeypatch):
    root = Path("/Volumes/qmt_bridge")
    ready = [False]
    monkeypatch.setattr(mounting, "_bridge_ready", lambda path: ready[0])
    monkeypatch.setattr(mounting.os.path, "ismount", lambda path: False)
    monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: pytest.fail("ordinary cron cannot mkdir /Volumes"))

    def run(argv, **kwargs):
        assert argv[:3] == ["/usr/bin/osascript", "-l", "JavaScript"]
        assert kwargs["env"]["QLIB_MOUNT_URL"] == "smb://user:secret@server/qmt_bridge"
        assert kwargs["env"]["QLIB_MOUNT_ROOT"] == "/Volumes/qmt_bridge"
        assert "secret" not in " ".join(argv)
        assert kwargs["timeout"] == 45
        ready[0] = True
        return _mount_result()

    monkeypatch.setattr(mounting.subprocess, "run", run)
    assert mounting.ensure_bridge_mount(root, "//user:secret@server/qmt_bridge") == 0


def test_existing_writable_share_is_reused_without_remount(tmp_path, monkeypatch):
    root = tmp_path / "bridge"
    (root / "inbox").mkdir(parents=True)
    monkeypatch.setattr(mounting.os.path, "ismount", lambda path: Path(path) == root)
    monkeypatch.setattr(mounting.subprocess, "run", lambda *a, **kw: pytest.fail("unnecessary remount"))
    assert mounting.ensure_bridge_mount(root, "//server/share") == 0


def test_plain_local_inbox_is_not_a_connected_share(tmp_path, monkeypatch):
    (tmp_path / "inbox").mkdir()
    monkeypatch.setattr(mounting.os.path, "ismount", lambda path: False)
    assert mounting._bridge_ready(tmp_path) is False


def test_custom_mount_point_is_created_before_connecting(tmp_path, monkeypatch):
    root = tmp_path / "custom" / "bridge"
    mounted = [False]
    monkeypatch.setattr(mounting.os.path, "ismount", lambda path: mounted[0])

    def run(argv, **kwargs):
        assert root.is_dir()
        assert kwargs["env"]["QLIB_MOUNT_ROOT"] == str(root)
        (root / "inbox").mkdir()
        mounted[0] = True
        return _mount_result()

    monkeypatch.setattr(mounting.subprocess, "run", run)
    assert mounting.ensure_bridge_mount(root, "smb://server/share") == 0


@pytest.mark.parametrize("kind", ["network", "helper", "timeout", "wrong_directory"])
def test_failed_mount_is_explicit_and_does_not_log_credentials(tmp_path, monkeypatch, capsys, kind):
    root = tmp_path / "bridge"
    monkeypatch.setattr(mounting.os.path, "ismount", lambda path: False)

    def run(argv, **kwargs):
        if kind == "timeout":
            raise subprocess.TimeoutExpired(argv, 45, output="user:secret@server")
        if kind == "helper":
            return subprocess.CompletedProcess(argv, 1, "", "user:secret@server")
        return _mount_result(60 if kind == "network" else 0)

    monkeypatch.setattr(mounting.subprocess, "run", run)
    assert mounting.ensure_bridge_mount(root, "smb://user:secret@server/share") != 0
    output = capsys.readouterr()
    assert "secret" not in output.out + output.err
    assert output.err


def test_busy_stale_mount_is_not_forcibly_unmounted(tmp_path, monkeypatch):
    monkeypatch.setattr(mounting, "_bridge_ready", lambda root: False)
    monkeypatch.setattr(mounting.os.path, "ismount", lambda root: True)
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "busy")

    monkeypatch.setattr(mounting.subprocess, "run", run)
    assert mounting.ensure_bridge_mount(tmp_path, "smb://server/share") != 0
    assert commands == [["/sbin/umount", str(tmp_path)]]


def test_successfully_unmounted_stale_share_is_reconnected(tmp_path, monkeypatch):
    mounted = [True]
    ready = [False]
    commands = []
    monkeypatch.setattr(mounting.os.path, "ismount", lambda root: mounted[0])
    monkeypatch.setattr(mounting, "_bridge_ready", lambda root: ready[0])

    def run(argv, **kwargs):
        commands.append(argv)
        if argv[0] == "/sbin/umount":
            mounted[0] = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        ready[0] = mounted[0] = True
        return _mount_result()

    monkeypatch.setattr(mounting.subprocess, "run", run)
    assert mounting.ensure_bridge_mount(tmp_path, "//server/share") == 0
    assert [command[0] for command in commands] == ["/sbin/umount", "/usr/bin/osascript"]


@pytest.mark.parametrize("url", ["https://server/share", "smb://server", "smb:///share"])
def test_invalid_share_url_is_rejected_before_connecting(tmp_path, monkeypatch, url):
    monkeypatch.setattr(mounting.subprocess, "run", lambda *a, **kw: pytest.fail("invalid URL must not connect"))
    assert mounting.ensure_bridge_mount(tmp_path, url) != 0


def test_default_volume_name_mismatch_does_not_mount_a_different_directory(monkeypatch):
    monkeypatch.setattr(mounting.subprocess, "run", lambda *a, **kw: pytest.fail("wrong mountpoint"))
    assert mounting.ensure_bridge_mount(Path("/Volumes/wrong_name"), "smb://server/qmt_bridge") != 0


@pytest.mark.parametrize("mount_status", [0, 1])
def test_scheduler_entry_runs_only_after_mount_helper_success(tmp_path, mount_status):
    repo = Path(__file__).resolve().parents[2]
    live = tmp_path / "repo" / "live_trading"
    scripts = live / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(repo / "live_trading/run_scheduler_cron.sh", live)
    shutil.copy2(repo / "live_trading/scripts/ensure_bridge_mount.sh", scripts)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".qlib_live_env").write_text("export MOUNT_ENV_CHECK=loaded\n")
    trace = tmp_path / "trace"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        '#!/bin/bash\n'
        'test "$MOUNT_ENV_CHECK" = loaded || exit 90\n'
        'case "$1" in\n'
        '  */ensure_bridge_mount.py)\n'
        '    test "$2" = /Volumes/qmt_bridge || exit 91\n'
        '    printf "mount\\n" >> "$MOUNT_TRACE"\n'
        f'    exit {mount_status};;\n'
        '  live_trading/scripts/run_scheduler.py)\n'
        '    test "$2" = --config && test "$3" = sample-live || exit 92\n'
        '    printf "scheduler\\n" >> "$MOUNT_TRACE";;\n'
        '  *) exit 93;;\n'
        'esac\n'
    )
    fake_python.chmod(0o755)
    env = dict(os.environ, HOME=str(home), QLIB_LIVE_PYTHON=str(fake_python),
               QLIB_BRIDGE_ROOT="/Volumes/qmt_bridge", MOUNT_TRACE=str(trace))
    result = subprocess.run(
        ["/bin/bash", str(live / "run_scheduler_cron.sh"), "sample-live"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == mount_status, (result.stdout, result.stderr)
    assert trace.read_text().splitlines() == (["mount", "scheduler"] if mount_status == 0 else ["mount"])
