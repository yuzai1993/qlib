"""regime-adapt 臂的 Phase S 回测驱动：B4-S 实盘策略 + 图 + 逐年 alpha/beta。

为每个 (臂, 种子) 生成 `mode: backtest_only` 配置，调 run_backtest.py 从 session
加载模型做推理 + TopkDropout 回测，然后用 strategy_stability_metrics 汇总全周期与
逐年指标（含 CAPM alpha/beta）。

**回测窗**：2020-08-03 ~ 2026-07-31。不用规范 B4-S 的 full 窗（2020-01-13 起），
因为 regime 臂的训练样本截至 2020-07-31，2020-01-13~07-31 落在训练集内。
b6m 参考臂训练集为 2016-01-02~2020-01-10，同窗回测对它也是样本外，可直接对比。

用法：
    python backtest/scripts/run_regime_phase_s.py --arms m0h5 --pool all --strategy b4s
    python backtest/scripts/run_regime_phase_s.py --arms m0h5 --pool all --strategy daily_topk
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd
import yaml

EXP_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXP_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from strategy_stability_metrics import summarize_stability  # noqa: E402

PYTHON = "/opt/anaconda3/envs/qlib/bin/python"
CONFIG_DIR = EXP_ROOT / "backtest" / "configs" / "regime-adapt" / "phase-s"
OUT_DIR = EXP_ROOT / "backtest" / "result" / "phase_s_regime"

BT_START, BT_END = "2020-08-03", "2026-07-31"
WARMUP_START = "2020-02-03"

SEEDS = (42, 1000, 2000, 3000, 4000)

DEFAULT_UNIVERSE_FILTER = {
    "st_daily": "scripts/data_collector/tushare/st_daily.csv",
}

# 全A 与训练池一致：剔除指数 / B 股 / 北交所
ALL_A_INSTRUMENTS = {
    "market": "all",
    "filter_pipe": [
        {
            "filter_type": "NameDFilter",
            "name_rule_re": "^(SH60|SH68|SZ00|SZ30)",
            "filter_start_time": None,
            "filter_end_time": None,
        }
    ],
}

# 全A 池基准：本地无中证全指 SH000985，原用 SH000300 占位，但组合从全A 选股（偏小盘），
# 沪深300 张不开其因子暴露 → CAPM 残差里混进未对冲的小盘因子，alpha 虚高且低 beta 臂占便宜。
# 改用自建等权全A（build_equal_weight_benchmark.py），同时与 Phase M 头部口径基准对齐。
EW_ALL_BENCH = {"equal_weight_csv": "backtest/configs/regime-adapt/bench_ew_all.csv"}

POOLS = {
    "csi1000": {
        "instruments": "csi1000",
        "handler_instruments": "csi1000",
        "benchmark": "SH000852",
        "benchmark_note": "中证1000",
    },
    "all": {
        "instruments": "all",
        "handler_instruments": ALL_A_INSTRUMENTS,
        "benchmark": EW_ALL_BENCH,
        "benchmark_note": "等权全A（自建，与 Phase M 头部基准同口径）",
    },
}

STRATEGIES = {
    "b4s": {
        "class": "TopkDropoutStrategy",
        "desc": "B4-S topk=22 n_drop=2 hold_thresh=2 risk_degree=0.90",
        "topk": 22,
        "n_drop": 2,
        "hold_thresh": 2,
    },
    "daily_topk": {
        "class": "DailyTopkStrategy",
        "desc": "每日换仓 topk=22 hold_thresh=1（无 n_drop 缓冲）",
        "topk": 22,
        "n_drop": 22,
        "hold_thresh": 1,
    },
}

# arm -> {sessions: {seed: session}, handler, module, label_horizon, model, extra}
ARMS: dict[str, dict[str, Any]] = {
    "m0h5": {
        "desc": "M0 H5（全A 长窗单 LGBM，训练标签 H5）",
        "sessions": {s: f"regimeadaptfast_m0h5_s{s}" for s in SEEDS},
        "handler": "Alpha158Technical",
        "module": "backtest.features.technical",
        "label_horizon": 5,
        "model": ("RegimeSingleLGBMModel", "backtest.models.regime_adapt"),
    },
    "m0h10": {
        "desc": "M0 H10（同配方，训练标签 H10）",
        "sessions": {s: f"regimeadaptfast_m0h10_s{s}" for s in SEEDS},
        "handler": "Alpha158Technical",
        "module": "backtest.features.technical",
        "label_horizon": 10,
        "model": ("RegimeSingleLGBMModel", "backtest.models.regime_adapt"),
    },
    "m0h40": {
        "desc": "M0 H40（同配方，训练标签 H40；标签对照）",
        "sessions": {
            42: "20260812_222756_regimeadaptfast_m0_s42",
            1000: "20260812_223202_regimeadaptfast_m0_s1000",
            2000: "20260812_223531_regimeadaptfast_m0_s2000",
            3000: "20260812_224058_regimeadaptfast_m0_s3000",
            4000: "20260812_224512_regimeadaptfast_m0_s4000",
        },
        "handler": "Alpha158Technical",
        "module": "backtest.features.technical",
        "label_horizon": 40,
        "model": ("RegimeSingleLGBMModel", "backtest.models.regime_adapt"),
    },
    "b6m": {
        "desc": "B6-M 现役实盘模型（csi1000 2016-2020 DoubleEnsemble H40）",
        "sessions": {
            42: "20260731_030732_mh_rankic_es_lr010_s42",
            1000: "20260731_032149_mh_rankic_es_lr010_s1000",
            2000: "20260731_033541_mh_rankic_es_lr010_s2000",
            3000: "20260731_034838_mh_rankic_es_lr010_s3000",
            4000: "20260731_040103_mh_rankic_es_lr010_s4000",
        },
        "handler": "Alpha158Technical",
        "module": "backtest.features.technical",
        "label_horizon": 40,
        "model": ("RankICEarlyStoppingDEnsembleModel", "backtest.models.rankic_early_stop"),
    },
    "m3h5": {
        "desc": "M3 H5（regime 特征 + 风格平衡权重，训练标签 H5）",
        "sessions": {s: f"regimeadaptfast_m3h5_s{s}" for s in SEEDS},
        "handler": "Alpha158RegimeTechnical",
        "module": "backtest.features.regime",
        "label_horizon": 5,
        "model": ("RegimeSingleLGBMModel", "backtest.models.regime_adapt"),
        "regime_csv": str(
            EXP_ROOT / "backtest" / "configs" / "regime-adapt" / "regime_features_v1.csv"
        ),
    },
}


def label_expr(horizon: int) -> str:
    return f"Ref($close, -{horizon + 1})/Ref($close, -1)-1"


def build_config(
    arm: str,
    seed: int,
    *,
    pool: str,
    strategy: str,
    generate_figures: bool,
) -> Path:
    spec = ARMS[arm]
    pool_spec = POOLS[pool]
    strat = STRATEGIES[strategy]
    handler: dict[str, Any] = {
        "class": spec["handler"],
        "module_path": spec["module"],
        "instruments": pool_spec["handler_instruments"],
        "start_time": WARMUP_START,
        "end_time": BT_END,
        "fit_start_time": WARMUP_START,
        "fit_end_time": BT_START,
        "feature_groups": ["range"],
        "infer_processors": [{"class": "ProcessInf"}],
        "label": [[label_expr(spec["label_horizon"])], ["LABEL0"]],
        "learn_processors": [{"class": "DropnaLabel"}],
    }
    if spec.get("regime_csv"):
        handler["regime_csv"] = spec["regime_csv"]
    model_cls, model_mod = spec["model"]
    tag = f"{arm}_{pool}_{strategy}_s{seed}"
    cfg = {
        "run": {
            "mode": "backtest_only",
            "note": f"phase_s_{tag}",
            "n_runs": 1,
            "from_session": spec["sessions"][seed],
            "from_run": 1,
            "generate_figures": generate_figures,
        },
        "data": {
            "provider_uri": "~/.qlib/qlib_data/cn_data",
            "region": "cn",
            "instruments": pool_spec["instruments"],
            "benchmark": pool_spec["benchmark"],
            "handler": handler,
        },
        "segments": {
            "train": [WARMUP_START, "2020-07-31"],
            "valid": [BT_START, BT_END],
            "test": [BT_START, BT_END],
        },
        "model": {"class": model_cls, "module_path": model_mod},
        "strategy": {
            "class": strat["class"],
            "module_path": "qlib.contrib.strategy.signal_strategy",
            "topk": strat["topk"],
            "n_drop": strat["n_drop"],
            "hold_thresh": strat["hold_thresh"],
            "kwargs": {
                "risk_degree": 0.90,
                "only_tradable": False,
                "forbid_all_trade_at_limit": False,
            },
        },
        "backtest": {
            "account": 10000000,
            "exchange_kwargs": {
                "freq": "day",
                "deal_price": "close",
                "limit_threshold": 0.095,
                "open_cost": 0.00021,
                "close_cost": 0.00071,
                "min_cost": 5.0,
                "trade_unit": 100,
            },
        },
        "universe_filter": dict(DEFAULT_UNIVERSE_FILTER, pool=pool),
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / f"bt_{tag}.yaml"
    path.write_text(
        "# 由 run_regime_phase_s.py 生成，勿手改\n"
        + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def run_one(
    arm: str,
    seed: int,
    *,
    pool: str,
    strategy: str,
    generate_figures: bool,
) -> Optional[Path]:
    """跑一次回测，返回 session 目录。"""
    cfg_path = build_config(
        arm, seed, pool=pool, strategy=strategy, generate_figures=generate_figures
    )
    proc = subprocess.run(
        [PYTHON, str(SCRIPTS_DIR / "run_backtest.py"), "--config", str(cfg_path)],
        cwd=EXP_ROOT,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(proc.stdout[-4000:])
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        print(f"[FAIL] {arm} s{seed} rc={proc.returncode}", flush=True)
        return None
    m = re.search(r"结果目录:\s*(\S+)", proc.stdout)
    if not m:
        print(f"[FAIL] {arm} s{seed} 未能解析结果目录", flush=True)
        return None
    return Path(m.group(1))


def collect(session_dir: Path) -> Optional[dict]:
    report_csv = session_dir / "run_01" / "report_normal.csv"
    if not report_csv.is_file():
        return None
    df = pd.read_csv(report_csv, index_col=0, parse_dates=True)
    stab = summarize_stability(df)
    metrics_path = session_dir / "run_01" / "metrics.json"
    figures_manifest = session_dir / "run_01" / "figures_manifest.json"
    return {
        "session": session_dir.name,
        "session_dir": str(session_dir.relative_to(EXP_ROOT)),
        "full_period": stab["full_period"],
        "years": stab["years"],
        "run_metrics": (
            json.loads(metrics_path.read_text()) if metrics_path.is_file() else None
        ),
        "figures": (
            json.loads(figures_manifest.read_text())
            if figures_manifest.is_file()
            else None
        ),
    }


def main(argv: Optional[Sequence[str]] = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--arms", nargs="+", required=True, choices=sorted(ARMS))
    p.add_argument("--seeds", nargs="+", type=int, default=list(SEEDS))
    p.add_argument("--pool", choices=sorted(POOLS), default="all")
    p.add_argument("--strategy", choices=sorted(STRATEGIES), default="b4s")
    p.add_argument("--skip-existing", action="store_true", default=True)
    p.add_argument(
        "--generate-figures",
        action="store_true",
        default=False,
        help="全A 截面大，默认关图；需要时显式打开",
    )
    args = p.parse_args(argv)

    pool_spec = POOLS[args.pool]
    strat = STRATEGIES[args.strategy]
    out_dir = OUT_DIR / f"{args.pool}_{args.strategy}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for arm in args.arms:
        out_json = out_dir / f"{arm}.json"
        done: dict[str, Any] = {}
        if out_json.is_file():
            done = json.loads(out_json.read_text())
        seeds_out = done.setdefault("seeds", {})
        done["arm"] = arm
        done["desc"] = ARMS[arm]["desc"]
        done["backtest_window"] = [BT_START, BT_END]
        done["pool"] = args.pool
        done["benchmark"] = pool_spec["benchmark"]
        done["benchmark_note"] = pool_spec["benchmark_note"]
        done["strategy"] = strat["desc"]
        done["strategy_id"] = args.strategy
        done["fees"] = {
            "open_cost": 0.00021,
            "close_cost": 0.00071,
            "min_cost": 5.0,
            "trade_unit": 100,
            "note": "QMT 2026-07-16 校准：买 0.021% + 卖 0.071%，往返 0.092%，最低 5 元",
        }
        for seed in args.seeds:
            key = str(seed)
            if key in seeds_out and args.skip_existing:
                print(f"[SKIP] {arm} {args.pool}/{args.strategy} s{seed} 已有结果", flush=True)
                continue
            print(f"[RUN ] {arm} {args.pool}/{args.strategy} s{seed}", flush=True)
            session_dir = run_one(
                arm,
                seed,
                pool=args.pool,
                strategy=args.strategy,
                generate_figures=args.generate_figures,
            )
            if session_dir is None:
                continue
            rec = collect(session_dir)
            if rec is None:
                print(f"[FAIL] {arm} s{seed} 无 report_normal.csv", flush=True)
                continue
            seeds_out[key] = rec
            fp = rec["full_period"]
            print(
                f"[DONE] {arm} {args.pool}/{args.strategy} s{seed} "
                f"年化={fp['annualized_return']:.2%} "
                f"夏普={fp['sharpe_ratio']:.2f} alpha={fp['alpha']:.2%} "
                f"beta={fp['beta']:.2f} 回撤={fp['max_drawdown']:.2%}",
                flush=True,
            )
            out_json.write_text(
                json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        out_json.write_text(
            json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"written: {out_json}", flush=True)


if __name__ == "__main__":
    main()
