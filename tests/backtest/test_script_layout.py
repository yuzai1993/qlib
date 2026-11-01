"""Classified research commands remain importable and expose safe CLI help."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest/scripts"
CATEGORIES = {"prepare", "train", "evaluate", "report", "register", "maintenance"}
DIRECT_COMMANDS = [
    "train/train_regime_arm.py",
    "prepare/build_regime_alt_labels.py",
    "evaluate/eval_ic_multi_pool.py",
    "report/build_phase_m_v1_report.py",
    "register/register_phase_s_experiment.py",
    "maintenance/cleanup_experiment_artifacts.py",
]


def _environment():
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _run_help(arguments, cwd):
    result = subprocess.run(
        [sys.executable, *arguments, "--help"], cwd=cwd, env=_environment(),
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout.lower(), result.stdout
    assert "--help" in result.stdout


def test_operational_scripts_have_one_category_and_no_flat_wrappers():
    assert {path.name for path in SCRIPTS.iterdir() if path.is_dir() and path.name != "__pycache__"} == CATEGORIES
    assert [path.name for path in SCRIPTS.iterdir() if path.is_file() and path.suffix in {".py", ".sh"}] == ["__init__.py"]
    commands = [path for path in SCRIPTS.rglob("*") if path.is_file() and path.suffix in {".py", ".sh"}
                and path.name != "__init__.py"]
    assert commands
    assert all(path.parent.name in CATEGORIES and path.parent.parent == SCRIPTS for path in commands)
    # A second implementation/wrapper with the same command name would make
    # canonical import targets and CLI instructions ambiguous.
    assert len({path.name for path in commands}) == len(commands)


def test_canonical_cross_category_imports_keep_the_repository_roots():
    from backtest.models import regime_adapt
    from backtest.scripts.evaluate import eval_ic_multi_pool
    from backtest.scripts.maintenance import cleanup_experiment_artifacts, config_loader
    from backtest.scripts.prepare import build_regime_alt_labels, prepare_regime_train_chunks
    from backtest.scripts.register import experiment_registry, register_phase_s_experiment
    from backtest.scripts.report import build_phase_m_v1_report
    from backtest.scripts.train import train_regime_arm

    assert train_regime_arm.EXP_ROOT == ROOT
    assert build_regime_alt_labels.EXP_ROOT == ROOT
    assert eval_ic_multi_pool.QLIB_ROOT == ROOT
    assert build_phase_m_v1_report.BACKTEST_ROOT == ROOT / "backtest"
    assert register_phase_s_experiment.REPO_ROOT == ROOT
    assert train_regime_arm.listing_first_pos is prepare_regime_train_chunks.listing_first_pos
    assert eval_ic_multi_pool.load_config is config_loader.load_config
    assert build_phase_m_v1_report._load_registry_rows is experiment_registry.load_registry
    assert cleanup_experiment_artifacts._load_registry_rows is experiment_registry.load_registry
    assert regime_adapt.daily_head_panel is eval_ic_multi_pool.daily_head_panel


@pytest.mark.parametrize("relative", DIRECT_COMMANDS)
def test_absolute_command_help_starts_from_an_unrelated_working_directory(tmp_path, relative):
    _run_help([str(SCRIPTS / relative)], tmp_path)
    # Help must not create training results, forecasts or registration files.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("module", [
    "backtest.scripts.report.build_phase_m_v1_report",
    "backtest.scripts.maintenance.cleanup_experiment_artifacts",
])
def test_package_command_help_starts_without_per_category_path_setup(module):
    _run_help(["-m", module], ROOT)
