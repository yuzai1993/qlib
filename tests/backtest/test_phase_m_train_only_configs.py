from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "backtest" / "experiments" / "registry.jsonl"


def test_all_registered_phase_m_configs_are_train_only():
    rows = [
        json.loads(line)
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
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
