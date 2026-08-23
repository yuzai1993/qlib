"""实盘 artifact 精简：清训练态属性，且预测逐点严格相等。"""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from live_trading.scripts.export_live_model import export, slim_model

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRAIN_ROOT = PROJECT_ROOT / "backtest/result"
SEEDS = (42, 1000, 2000, 3000, 4000)


def _src(seed: int) -> Path:
    return (
        TRAIN_ROOT
        / f"regimeadaptfast_m0h20_rankices_s{seed}"
        / "run_01/artifacts_root/artifacts/trained_model"
    )


class _Fake:
    """替身：只验证 slim_model 清哪些属性、保留哪些。"""

    def __init__(self):
        self.model = "booster"
        self.tradable_mask = pd.Series([True, False])
        self.day_weights = pd.Series([1.0])
        self.rankic_evals_result = [{"x": 1}]
        self.protocol_id = "regime-adapt-v1"
        self.es_metric = "daily_rank_ic"
        self.params = {"lr": 0.2}


def test_slim_model_clears_training_only_attributes():
    fake = _Fake()

    cleared = slim_model(fake)

    assert set(cleared) == {"tradable_mask", "day_weights", "rankic_evals_result"}
    assert fake.tradable_mask is None
    assert fake.day_weights is None
    assert fake.rankic_evals_result is None


def test_slim_model_keeps_everything_inference_needs():
    fake = _Fake()

    slim_model(fake)

    assert fake.model == "booster"
    assert fake.protocol_id == "regime-adapt-v1"
    assert fake.es_metric == "daily_rank_ic"
    assert fake.params == {"lr": 0.2}


def test_slim_model_is_idempotent():
    fake = _Fake()

    slim_model(fake)
    cleared_again = slim_model(fake)

    assert cleared_again == []


@pytest.mark.parametrize("seed", SEEDS)
def test_source_artifact_exists(seed):
    assert _src(seed).is_file(), f"training artifact missing for seed {seed}"


@pytest.mark.parametrize("seed", SEEDS)
def test_export_shrinks_artifact_by_at_least_ten_times(tmp_path, seed):
    dst = tmp_path / f"s{seed}" / "trained_model"

    info = export(_src(seed), dst)

    assert dst.is_file()
    assert info["dst_bytes"] * 10 < info["src_bytes"], info
    assert len(info["sha256"]) == 64
    assert "tradable_mask" in info["cleared"]


@pytest.mark.parametrize("seed", SEEDS)
def test_exported_model_predicts_identically(tmp_path, seed):
    """精简是纯体积优化：同一批特征的预测必须逐点严格相等。"""
    from qlib.data.dataset.handler import DataHandlerLP

    dst = tmp_path / f"s{seed}" / "trained_model"
    export(_src(seed), dst)

    with open(_src(seed), "rb") as fh:
        original = pickle.load(fh)
    with open(dst, "rb") as fh:
        slimmed = pickle.load(fh)

    n_features = original.model.num_feature()
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        rng.standard_normal((64, n_features)),
        index=pd.MultiIndex.from_product(
            [[pd.Timestamp("2026-08-20")], [f"SH{600000 + i}" for i in range(64)]],
            names=["datetime", "instrument"],
        ),
    )

    class _DS:
        def prepare(self, segment, *, col_set, data_key):
            assert col_set == "feature" and data_key == DataHandlerLP.DK_I
            return frame

    before = original.predict(_DS(), segment="test")
    after = slimmed.predict(_DS(), segment="test")

    pd.testing.assert_series_equal(before, after, check_exact=True)
