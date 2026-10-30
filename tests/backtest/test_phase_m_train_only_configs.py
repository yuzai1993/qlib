from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.scripts.experiment_registry import load_registry  # noqa: E402

REGISTRY = ROOT / "backtest" / "experiments" / "registry.jsonl"


def test_all_registered_phase_m_configs_are_train_only():
    rows = load_registry(REGISTRY)
    paths = {
        ROOT / config
        for row in rows
        if row.get("phase") == "M"
        for config in row.get("configs", [])
    }

    assert paths
    for path in sorted(paths):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert cfg["run"]["mode"] == "train_only", path
