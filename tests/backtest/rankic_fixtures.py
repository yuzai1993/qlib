"""Synthetic inputs for RankIC tool tests, independent of research archives.

No model, historical result, or provider data is needed: the production generator
writes candidate YAMLs and the calling tests synthesize their own metric/session
evidence. Keep this protocol fixture explicit so contract drift is still tested.
"""

from pathlib import Path

from backtest.scripts.generate_b5_rankic_hyperparams import generate_configs


def base_config() -> dict:
    return {
        "run": {"mode": "train_only", "note": "synthetic-b5", "n_runs": 1,
                "from_session": None, "from_run": 1, "generate_figures": False},
        "data": {
            "provider_uri": "~/.qlib/qlib_data/cn_data", "region": "cn",
            "instruments": "csi1000", "benchmark": "SH000852",
            "handler": {
                "class": "Alpha158Technical", "module_path": "backtest.features.technical",
                "start_time": "2003-01-02", "end_time": "2026-07-16",
                "fit_start_time": "2016-01-02", "fit_end_time": "2020-01-10",
                "infer_processors": [{"class": "ProcessInf"}],
                "label": [["Ref($close, -41)/Ref($close, -1)-1"], ["LABEL0"]],
                "feature_groups": ["range"],
                "learn_processors": [{"class": "DropnaLabel"},
                                     {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}}],
            },
        },
        "segments": {"train": ["2016-01-02", "2020-01-10"],
                     "valid": ["2020-01-13", "2021-07-15"],
                     "test": ["2021-07-16", "2026-07-16"]},
        "model": {
            "class": "DEnsembleModel", "module_path": "qlib.contrib.model.double_ensemble",
            "kwargs": {
                "base_model": "gbm", "loss": "mse", "num_models": 3,
                "enable_sr": True, "enable_fs": True, "alpha1": 1, "alpha2": 1,
                "bins_sr": 10, "bins_fs": 5, "decay": .5,
                "sample_ratios": [.8, .7, .6, .5, .4], "sub_weights": [1, 1, 1],
                "epochs": 200, "colsample_bytree": .8879, "learning_rate": .2,
                "subsample": .8789, "lambda_l1": 205.6999, "lambda_l2": 580.9768,
                "max_depth": 8, "num_leaves": 210, "num_threads": 8, "seed": 42,
            },
        },
        "strategy": {
            "class": "TopkDropoutStrategy", "module_path": "qlib.contrib.strategy.signal_strategy",
            "topk": 10, "n_drop": 2,
            "kwargs": {"hold_thresh": 1, "risk_degree": .95,
                       "only_tradable": False, "forbid_all_trade_at_limit": False},
        },
        "backtest": {
            "account": 1_000_000,
            "exchange_kwargs": {"freq": "day", "deal_price": "close", "limit_threshold": .095,
                                "open_cost": .00021, "close_cost": .00071, "min_cost": 5, "trade_unit": 100},
        },
        "dataset": {"class": "PurgedHorizonDataset", "module_path": "backtest.label_design.dataset",
                    "kwargs": {"label_horizon": 40}},
    }


def write_candidate_configs(output_root: Path) -> dict[str, list[Path]]:
    paths: dict[str, list[Path]] = {}
    for path in generate_configs(base_cfg=base_config(), output_root=output_root):
        paths.setdefault(path.parent.name, []).append(path)
    return paths
