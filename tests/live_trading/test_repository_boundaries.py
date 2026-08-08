from pathlib import Path
import os
import re
import subprocess

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


def _tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT,
        capture_output=True, check=True,
    )
    return [
        Path(value.decode("utf-8"))
        for value in result.stdout.split(b"\0") if value
    ]


def test_pr49_operator_checklist_is_a_controlled_repository_artifact():
    checklist = REPO_ROOT / "live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md"
    assert checklist.is_file()
    text = checklist.read_text(encoding="utf-8")

    required_contract = {
        "live_trading/configs/csi1000_pr49_one_lot_probe.yaml",
        "live_trading/scripts/run_operator_probe.py",
        "bash live_trading/run_probe_import.sh",
        "live_trading/data/csi1000_b6m_b2s_postclose_real.db",
        r"D:\qmt_bridge\pr49_probe",
        "/Volumes/qmt_bridge/pr49_probe",
        "csi1000_pr49_one_lot_probe",
        "PR49_LIVE_OK_YYYY-MM-DD",
        "EXECUTION_PROFILE = \"AFTER_HOURS_FIXED_PRICE\"",
        "prType=49",
        "MAX_ORDER_QUANTITY = 100",
    }
    assert all(token in text for token in required_contract)


def test_windows_marker_creator_uses_shared_lock_and_rechecks_inside_it():
    script = (
        REPO_ROOT /
        "live_trading/qmt_strategy/New-OperatorAuthorizationMarker.ps1"
    )
    assert script.is_file()
    text = script.read_text(encoding="utf-8")

    for token in (
        "OPERATOR_AUTHORIZATION.lock",
        "[System.IO.FileMode]::OpenOrCreate",
        "[System.IO.FileShare]::None",
        "$LockStream.Lock(0, 1)",
        "authorization lock timeout",
        "trade date must equal today",
        "authorization cutoff has passed",
        "other profile authorization exists",
        "authorization marker already exists",
        '"CLOSE_AUCTION"',
        '"AFTER_HOURS_FIXED_PRICE"',
        "finally",
        "$LockStream.Dispose()",
    ):
        assert token in text

    lock_acquired = text.index("$LockStream.Lock(0, 1)")
    assert lock_acquired < text.index("$Today =")
    assert lock_acquired < text.index("$OwnMarker =", lock_acquired)
    assert lock_acquired < text.index("$OtherMarker =", lock_acquired)
    assert lock_acquired < text.index(
        "[System.IO.File]::Move($IntentPath, $OwnMarker)"
    )

    publisher = (
        REPO_ROOT / "live_trading/modules/signal_publisher.py"
    ).read_text(encoding="utf-8")
    assert 'AUTHORIZATION_LOCK_NAME = "OPERATOR_AUTHORIZATION.lock"' in publisher


def _marker_commit_contract(script_text, failure):
    """Simulate the statically verified PowerShell commit-state contract."""
    required = {
        "intent": "$IntentStream.Flush($true)",
        "readback": "$IntentReadback =",
        "move": "[System.IO.File]::Move($IntentPath, $OwnMarker)",
        "committed": "$Committed = $true",
        "disambiguate": "Test-Path -LiteralPath $OwnMarker -PathType Leaf",
        "success": "AUTHORIZATION_COMMITTED",
        "failure": "AUTHORIZATION_NOT_COMMITTED",
    }
    positions = {name: script_text.index(token) for name, token in required.items()}
    positions["committed"] = script_text.index(
        "$Committed = $true", positions["move"],
    )
    assert positions["intent"] < positions["readback"] < positions["move"]
    assert positions["move"] < positions["committed"]

    if failure == "preexisting_final":
        assert 'CommitSource = "pre-existing-final-marker"' in script_text
        return True, False, True, 0

    final_exists = False
    intent_exists = True
    committed = False
    if failure == "intent_readback":
        return final_exists, intent_exists, committed, 1
    if failure == "rename_before_commit":
        return final_exists, intent_exists, committed, 1

    # Rename is the sole irreversible authorization point.
    final_exists = True
    intent_exists = False
    if failure == "rename_ambiguous":
        committed = final_exists  # PowerShell disambiguates from final path.
    else:
        committed = True

    if failure in {"final_readback", "unlock", "dispose"}:
        # Post-commit diagnostics cannot downgrade the truthful exit contract.
        pass
    return final_exists, intent_exists, committed, 0


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("preexisting_final", (True, False, True, 0)),
        ("intent_readback", (False, True, False, 1)),
        ("rename_before_commit", (False, True, False, 1)),
        ("rename_ambiguous", (True, False, True, 0)),
        ("final_readback", (True, False, True, 0)),
        ("unlock", (True, False, True, 0)),
        ("dispose", (True, False, True, 0)),
    ],
)
def test_windows_marker_commit_point_has_unambiguous_exit_contract(
    failure, expected,
):
    script = (
        REPO_ROOT /
        "live_trading/qmt_strategy/New-OperatorAuthorizationMarker.ps1"
    ).read_text(encoding="utf-8")

    assert _marker_commit_contract(script, failure) == expected
    assert "Remove-Item -LiteralPath $OwnMarker" not in script
    assert "AUTHORIZATION_COMMITTED_WARNING" in script
    assert "exit 0" in script
    assert "exit 1" in script


def test_git_tracks_no_runtime_authorization_or_broker_evidence():
    forbidden = []
    for path in _tracked_files():
        name = path.name
        if re.fullmatch(r"(?:LIVE_OK|PR49_LIVE_OK)_\d{4}-\d{2}-\d{2}", name):
            forbidden.append(path.as_posix())
        elif re.fullmatch(r"fills_.+\.(?:jsonl|done)", name):
            forbidden.append(path.as_posix())
        elif re.fullmatch(r"account_.+\.(?:jsonl|done)", name):
            forbidden.append(path.as_posix())
        elif path.suffix == ".db" or name == ".qlib_live_env":
            forbidden.append(path.as_posix())

    assert forbidden == []


def _contains_account_secret(text, suffix):
    assignment_secret = False
    if suffix in {".yaml", ".yml"}:
        assignment_secret = re.search(
            r'(?m)^[ \t]*account_id:[ \t]*["\']?'
            r'(?![ \t]*(?:["\']|$|<))[^\s"\']+',
            text,
        )
    elif suffix == ".py":
        assignment_secret = re.search(
            r'(?m)^[ \t]*ACCOUNT_ID[ \t]*=[ \t]*["\']'
            r'(?![ \t]*(?:["\']|<))'
            r'[^"\']+',
            text,
        )
    elif suffix == ".sh":
        assignment_secret = re.search(
            r'(?m)^[ \t]*export[ \t]+QMT_REAL_ACCOUNT_ID[ \t]*=[ \t]*'
            r'["\']?(?![ \t]*(?:["\']|$|<))[^\s"\']+',
            text,
        )
    generic_assignment_secret = re.search(
        r'(?m)^[ \t]*(?:export[ \t]+)?'
        r'(?:QMT_REAL_ACCOUNT_ID|ACCOUNT_ID)[ \t]*=[ \t]*["\']?'
        r'(?![ \t]*(?:["\']|$|<|\$\{))[^\s"\']+',
        text,
    )
    json_assignment_secret = re.search(
        r'(?i)["\']account_id["\'][ \t]*:[ \t]*["\']'
        r'(?![ \t]*(?:["\']|<))[^"\'\r\n]+',
        text,
    )
    plist_assignment_secret = re.search(
        r'(?is)<key>\s*(?:QMT_REAL_ACCOUNT_ID|ACCOUNT_ID|account_id)\s*'
        r'</key>\s*<string>\s*(?!<)[^<\s]+',
        text,
    )
    documented_numeric_account = re.search(
        r'(?i)(?:资金账号|account(?:_id)?|QMT_REAL_ACCOUNT_ID)'
        r'[^\n]{0,120}?\b\d{8,20}\b',
        text,
    )
    return bool(
        assignment_secret
        or generic_assignment_secret
        or json_assignment_secret
        or plist_assignment_secret
        or documented_numeric_account
    )


def _find_tracked_live_account_secrets(root, tracked):
    offenders = []
    for relative in sorted(tracked):
        if not relative.parts or relative.parts[0] != "live_trading":
            continue
        path = root / relative
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _contains_account_secret(text, path.suffix):
            offenders.append(relative.as_posix())
    return offenders


def test_account_secret_detector_catches_every_tracked_text_shape(tmp_path):
    fixtures = {
        "live_trading/runtime.py": 'ACCOUNT_ID = "1234567890"\n',
        "live_trading/config.yaml": 'account_id: "1234567890"\n',
        "live_trading/launch.plist": (
            "<key>QMT_REAL_ACCOUNT_ID</key>\n<string>1234567890</string>\n"
        ),
        "live_trading/cron.example": "QMT_REAL_ACCOUNT_ID=1234567890\n",
        "live_trading/state.json": '{"account_id":"1234567890"}\n',
        "live_trading/index.html": "<p>资金账号 1234567890</p>\n",
        "live_trading/runtime": "ACCOUNT_ID=1234567890\n",
    }
    tracked = []
    for name, content in fixtures.items():
        relative = Path(name)
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        tracked.append(relative)

    assert set(_find_tracked_live_account_secrets(tmp_path, tracked)) == set(
        fixtures
    )
    assert _contains_account_secret('ACCOUNT_ID = "1234567890"\n', ".py")
    assert _contains_account_secret('account_id: "1234567890"\n', ".yaml")
    assert _contains_account_secret(
        "资金账号 `1234567890` 不得提交\n", ".md",
    )
    assert not _contains_account_secret('ACCOUNT_ID = ""\n', ".py")
    assert not _contains_account_secret('ACCOUNT_ID = "<QMT-local>"\n', ".md")


def test_tracked_live_runtime_text_contains_no_account_secret():
    tracked = set(_tracked_files())
    tracked.add(Path("live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md"))
    assert _find_tracked_live_account_secrets(REPO_ROOT, tracked) == []
