"""live 配置合并加载测试。"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.live_config import load_live_config, _deep_merge


def test_deep_merge_overrides_and_keeps():
    base = {"a": 1, "nested": {"x": 1, "y": 2}, "list": [1, 2]}
    override = {"nested": {"y": 99, "z": 3}, "list": [9], "b": 2}
    merged = _deep_merge(base, override)
    assert merged["a"] == 1
    assert merged["b"] == 2
    assert merged["nested"] == {"x": 1, "y": 99, "z": 3}
    assert merged["list"] == [9]  # 列表整体替换
    # 原 dict 不被修改
    assert base["nested"]["y"] == 2


def test_load_real_live_config_merges_base():
    cfg = load_live_config(
        REPO_ROOT / "live_trading" / "configs" / "csi300_topk10_live.yaml",
        project_root=REPO_ROOT,
    )
    # 来自 base（paper_trading 配置）
    assert cfg["strategy"]["topk"] == 10
    assert cfg["strategy"]["n_drop"] == 2
    assert cfg["exchange"]["trade_unit"] == 100
    # 来自 live 配置
    assert cfg["live"]["strategy_id"] == "csi300_topk10"
    assert cfg["live"]["default_mode"] == "LIVE"  # 2026-07-14 起实盘开关打开
    assert cfg["fees"]["stamp_duty_rate"] == 0.0005
    # live 的 storage 覆盖 base 的 storage
    assert "live_trading" in cfg["storage"]["db_path"]
    assert cfg["_config_id"] == "csi300_topk10_live"


def test_load_without_base(tmp_path):
    p = tmp_path / "standalone.yaml"
    p.write_text("live:\n  strategy_id: s1\n", encoding="utf-8")
    cfg = load_live_config(p, project_root=tmp_path)
    assert cfg["live"]["strategy_id"] == "s1"
