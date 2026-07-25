from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.scripts.generate_label_horizon_configs import (  # noqa: E402
    generate_configs,
)


def _base_config() -> dict:
    return {
        "run": {
            "mode": "train_only",
            "note": "baseline",
            "n_runs": 1,
            "from_session": None,
            "from_run": 1,
            "generate_figures": False,
        },
        "data": {
            "provider_uri": "~/.qlib/qlib_data/cn_data",
            "region": "cn",
            "instruments": "csi1000",
            "benchmark": "SH000852",
            "handler": {
                "class": "Alpha158",
                "module_path": "qlib.contrib.data.handler",
                "start_time": "2003-01-02",
                "end_time": "2026-07-16",
                "fit_start_time": "2016-01-02",
                "fit_end_time": "2020-01-10",
                "infer_processors": [{"class": "ProcessInf"}],
            },
        },
        "segments": {
            "train": ["2016-01-02", "2020-01-10"],
            "valid": ["2020-01-13", "2021-07-15"],
            "test": ["2021-07-16", "2026-07-16"],
        },
        "model": {
            "class": "LGBModel",
            "module_path": "qlib.contrib.model.gbdt",
            "kwargs": {"loss": "mse", "seed": 42},
        },
        "strategy": {"class": "TopkDropoutStrategy", "topk": 10, "n_drop": 2},
        "backtest": {"account": 1_000_000},
    }


def _manifest() -> dict:
    return {
        "baseline_ref": "B1 v1.0",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "candidates": [
            {
                "variant": "cum-h20",
                "exp_id": "label-design/cum-h20",
                "label_horizon": 20,
                "label": "Ref($close, -21)/Ref($close, -1)-1",
            }
        ],
    }


def test_generate_configs_changes_only_label_dataset_identity_and_seed(tmp_path):
    paths = generate_configs(
        _manifest(),
        base_cfg=_base_config(),
        output_root=tmp_path,
    )

    assert [path.name for path in paths] == [
        "ld_cum_h20_lgbm_s42.yaml",
        "ld_cum_h20_lgbm_s1000.yaml",
        "ld_cum_h20_lgbm_s2000.yaml",
        "ld_cum_h20_lgbm_s3000.yaml",
        "ld_cum_h20_lgbm_s4000.yaml",
    ]
    parsed = yaml.safe_load(paths[2].read_text(encoding="utf-8"))
    assert parsed["run"]["mode"] == "train_only"
    assert parsed["run"]["note"] == "ld_cum_h20_lgbm_s2000"
    assert parsed["data"]["instruments"] == "csi1000"
    assert parsed["data"]["handler"]["label"] == [
        ["Ref($close, -21)/Ref($close, -1)-1"],
        ["LABEL0"],
    ]
    assert parsed["dataset"] == {
        "class": "PurgedHorizonDataset",
        "module_path": "backtest.label_design.dataset",
        "kwargs": {"label_horizon": 20},
    }
    assert parsed["segments"] == _base_config()["segments"]
    assert parsed["model"]["kwargs"]["seed"] == 2000
    assert parsed["strategy"] == _base_config()["strategy"]


def test_generate_configs_refuses_to_overwrite_different_content(tmp_path):
    paths = generate_configs(
        _manifest(),
        base_cfg=_base_config(),
        output_root=tmp_path,
    )
    paths[0].write_text("different: true\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="differs"):
        generate_configs(
            _manifest(),
            base_cfg=_base_config(),
            output_root=tmp_path,
        )
