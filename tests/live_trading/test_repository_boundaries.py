from pathlib import Path

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
