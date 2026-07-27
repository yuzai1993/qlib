"""Generate model-arch-nn/tra configs (EXPERIMENT_STANDARD v1.8, baseline B5 v1.0).

Data/feature/label identical to B5-M (Alpha158+range, cumulative H40, CSI1000,
label CSRankNorm):
- Handler start_time 2014-01-02 (causal rolling windows: feature values from 2016
  onward identical to the 2003 start used by B5; halves MTSDatasetH memory).
- NN recipe feature processors (official TRA): RobustZScoreNorm+Fillna on
  features; ProcessInf kept for parity with live config. Label processing
  (DropnaLabel + CSRankNorm) matches B5 exactly.
- drop_raw + LeanMTSDatasetH keep memory within the 16GB host (the vanilla
  MTSDatasetH run was OOM-killed).
- Purge parity with PurgedHorizonDataset(label_horizon=40): train/valid segment
  ends shifted back 41 trading days (2019-11-13 / 2021-05-18); test unchanged.
- MTSDatasetH horizon=41 masks recent memory states against H40 label leakage.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = BACKTEST_ROOT / "configs"

SEEDS = [42, 1000, 2000, 3000, 4000]

BASE = {
    "run": {
        "mode": "train_only",
        "note": None,
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
            "class": "Alpha158Technical",
            "module_path": "backtest.features.technical",
            "start_time": "2014-01-02",
            "end_time": "2026-07-16",
            "fit_start_time": "2016-01-02",
            "fit_end_time": "2020-01-10",
            "infer_processors": [
                {"class": "ProcessInf"},
                {
                    "class": "RobustZScoreNorm",
                    "kwargs": {"fields_group": "feature", "clip_outlier": True},
                },
                {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
            ],
            "learn_processors": [
                {"class": "DropnaLabel"},
                {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
            ],
            "label": [["Ref($close, -41)/Ref($close, -1)-1"], ["LABEL0"]],
            "feature_groups": ["range"],
            "drop_raw": True,
        },
    },
    # train/valid ends purged by 41 trading days (parity with PurgedHorizonDataset)
    "segments": {
        "train": ["2016-01-02", "2019-11-13"],
        "valid": ["2020-01-13", "2021-05-18"],
        "test": ["2021-07-16", "2026-07-16"],
    },
    "model": {
        "class": "TRAModelAuto",
        "module_path": "backtest.models.tra_device",
        "kwargs": {
            "model_type": "RNN",
            # official Alpha158_full backbone; epochs/steps sized for the MPS budget
            "model_config": {
                "input_size": 164,
                "hidden_size": 256,
                "num_layers": 2,
                "rnn_arch": "LSTM",
                "use_attn": True,
                "dropout": 0.2,
            },
            "tra_config": {
                "num_states": 3,
                "rnn_arch": "LSTM",
                "hidden_size": 32,
                "num_layers": 1,
                "dropout": 0.0,
                "tau": 1.0,
                "src_info": "LR_TPE",
            },
            "lr": 0.001,
            "n_epochs": 40,
            "early_stop": 10,
            "max_steps_per_epoch": 150,
            "lamb": 1.0,
            "rho": 0.99,
            "alpha": 0.5,
            "transport_method": "router",
            "memory_mode": "sample",
            "pretrain": True,
            "eval_train": False,
            "eval_test": False,
            "logdir": None,
            "seed": None,
        },
    },
    "dataset": {
        "class": "LeanMTSDatasetH",
        "module_path": "backtest.datasets.lean_mts",
        "kwargs": {
            "seq_len": 60,
            "horizon": 41,
            "num_states": 3,
            "batch_size": 1024,
            "memory_mode": "sample",
            "drop_last": True,
        },
    },
    "strategy": {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy.signal_strategy",
        "topk": 10,
        "n_drop": 2,
        "kwargs": {
            "hold_thresh": 1,
            "risk_degree": 0.95,
            "only_tradable": False,
            "forbid_all_trade_at_limit": False,
        },
    },
    "backtest": {
        "account": 1000000,
        "exchange_kwargs": {
            "freq": "day",
            "deal_price": "close",
            "limit_threshold": 0.095,
            "open_cost": 0.00021,
            "close_cost": 0.00071,
            "min_cost": 5,
            "trade_unit": 100,
        },
    },
}


def write_config(cfg: dict, rel: str) -> None:
    path = CONFIGS_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# EXPERIMENT_STANDARD v1.8 | exp: model-arch-nn/tra | baseline B5 v1.0 | seed {cfg['model']['kwargs']['seed']}\n"
        f"# /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py --config {rel}\n"
    )
    path.write_text(
        header + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"wrote {rel}")


def main() -> None:
    for seed in SEEDS:
        cfg = copy.deepcopy(BASE)
        note = f"ma_tra_s{seed}"
        cfg["run"]["note"] = note
        cfg["model"]["kwargs"]["seed"] = seed
        write_config(cfg, f"model-arch-nn/tra/{note}.yaml")

    # smoke config: 1 epoch, few steps, no pretrain — pipeline/timing check only
    smoke = copy.deepcopy(BASE)
    smoke["run"]["note"] = "ma_tra_smoke"
    smoke["model"]["kwargs"].update(
        {"seed": 42, "n_epochs": 1, "early_stop": 1, "max_steps_per_epoch": 8, "pretrain": False}
    )
    write_config(smoke, "model-arch-nn/tra/ma_tra_smoke.yaml")


if __name__ == "__main__":
    main()
