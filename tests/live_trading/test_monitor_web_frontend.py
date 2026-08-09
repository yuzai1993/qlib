"""Run dependency-free monitor frontend behavior tests with Node."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
NODE_TEST = REPO_ROOT / "tests/live_trading/web/test_monitor_runtime.cjs"


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_monitor_frontend_runtime():
    result = subprocess.run(
        [NODE, "--test", str(NODE_TEST)], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
