"""live 配置合并加载测试。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.live_config import load_live_config


def test_load_real_live_config_is_standalone():
    import yaml

    path = REPO_ROOT / "live_trading" / "configs" / "csi300_topk10_live.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "base_config" not in raw

    cfg = load_live_config(
        path,
        project_root=REPO_ROOT,
    )
    assert cfg["strategy"]["topk"] == 10
    assert cfg["strategy"]["n_drop"] == 2
    assert cfg["strategy"]["risk_degree"] == pytest.approx(0.95)
    assert cfg["exchange"]["trade_unit"] == 100
    assert cfg["handler"]["fit_start_time"] == "2006-01-02"
    assert cfg["model"]["experiment_id"] == "265134483362085141"
    assert cfg["live"]["strategy_id"] == "csi300_topk10"
    assert cfg["live"]["default_mode"] == "LIVE"  # 2026-07-14 起实盘开关打开
    assert cfg["fees"]["stamp_duty_rate"] == 0.0005
    assert "live_trading" in cfg["storage"]["db_path"]
    assert cfg["_config_id"] == "csi300_topk10_live"


def test_load_standalone_minimal_config(tmp_path):
    p = tmp_path / "standalone.yaml"
    p.write_text("live:\n  strategy_id: s1\n", encoding="utf-8")
    cfg = load_live_config(p, project_root=tmp_path)
    assert cfg["live"]["strategy_id"] == "s1"


def _write_baseline_config(tmp_path, baseline):
    import yaml

    p = tmp_path / "baseline.yaml"
    p.write_text(
        yaml.safe_dump({"monitor": {"performance_baseline": baseline}}),
        encoding="utf-8",
    )
    return p


def test_load_valid_performance_baseline(tmp_path):
    baseline = {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": 10_000_000.0,
        "benchmark_close": 4786.78271484375,
    }
    cfg = load_live_config(
        _write_baseline_config(tmp_path, baseline), project_root=tmp_path,
    )
    assert cfg["monitor"]["performance_baseline"] == baseline


@pytest.mark.parametrize("baseline", [
    {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": 10_000_000.0,
    },
    {
        "first_snapshot_date": "20260716",
        "opening_total_value": 10_000_000.0,
        "benchmark_close": 4786.78,
    },
    {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": 0.0,
        "benchmark_close": 4786.78,
    },
    {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": 10_000_000.0,
        "benchmark_close": True,
    },
    {
        "first_snapshot_date": "2026-07-16",
        "opening_total_value": "ten million",
        "benchmark_close": 4786.78,
    },
])
def test_invalid_performance_baseline_fails_closed(tmp_path, baseline):
    with pytest.raises(ValueError, match="performance_baseline"):
        load_live_config(
            _write_baseline_config(tmp_path, baseline), project_root=tmp_path,
        )
