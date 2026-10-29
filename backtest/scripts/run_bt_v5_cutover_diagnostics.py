"""Frozen BT v5 300k/current-fee replay and carried-position post-July diagnostic.

No training, registry edits, live DB construction, bridge publication or broker calls.
Historical official signal is immutable; only dates after 2026-07-31 are inferred.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
BOUNDARY = pd.Timestamp("2026-07-31")
OFFICIAL_PRED_SHA = "3d6f768930a4b9ac63622ee039ded617b68addfac56f44d77002a0188572021e"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_frozen_config(cfg, *, risk_degree):
    if risk_degree not in (0.9, 1.0):
        raise ValueError("diagnostic risk_degree must be explicitly 0.9 or 1.0")
    strategy = {**cfg["strategy"], **cfg["strategy"].get("kwargs", {})}
    expected = {
        "class": "TopkDropoutStrategy",
        "topk": 5,
        "n_drop": 1,
        "hold_thresh": 1,
        "force_sell_rank": 100,
        "risk_degree": risk_degree,
        "initial_buy_count": None,
        "only_tradable": False,
        "forbid_all_trade_at_limit": False,
    }
    if any(strategy.get(key) != value for key, value in expected.items()):
        raise ValueError("diagnostic strategy differs from frozen BT v5 contract")
    exchange = cfg["backtest"]["exchange_kwargs"]
    fees = {
        "open_cost": 0.0000954,
        "close_cost": 0.0005954,
        "min_cost": 5.0,
        "trade_unit": 100,
        "deal_price": "close",
        "limit_threshold": "market_cn",
    }
    if cfg["backtest"]["account"] != 300000 or any(
        exchange.get(key) != value for key, value in fees.items()
    ):
        raise ValueError("diagnostic account/fees differ from frozen 300k contract")


def load_frozen_reuse(directory, audit):
    """Reuse saved scores and the benchmark authenticated by the prior daily report."""
    directory = Path(directory)
    prior = json.loads((directory / "summary.json").read_text())
    if prior["status"] != "completed" or any(
        prior[key] != audit[key]
        for key in (
            "requested_end",
            "source_pred_sha256",
            "st_daily_sha256",
            "model_manifest_sha256",
        )
    ):
        raise ValueError("cached diagnostic provenance mismatch")
    for filename, field in (
        ("forward_pred.pkl", "forward_pred_sha256"),
        ("portfolio_daily.csv", "report_sha256"),
    ):
        if sha(directory / filename) != prior[field]:
            raise ValueError(f"cached {filename} SHA mismatch")
    benchmark = pd.read_csv(
        directory / "bench_ew_all.csv", index_col=0, parse_dates=True
    ).iloc[:, 0]
    report = pd.read_csv(
        directory / "portfolio_daily.csv", index_col=0, parse_dates=True
    )
    if (
        benchmark.index.has_duplicates
        or not report.index.isin(benchmark.index).all()
        or not ((benchmark.reindex(report.index) - report["bench"]).abs() < 1e-12).all()
    ):
        raise ValueError("cached benchmark differs from authenticated daily report")
    return pd.read_pickle(directory / "forward_pred.pkl"), benchmark


def combine_frozen_scores(history, forward):
    if isinstance(history, pd.Series):
        history = history.to_frame("score")
    if isinstance(forward, pd.Series):
        forward = forward.to_frame("score")
    for frame in (history, forward):
        if frame.index.has_duplicates or list(frame.columns) != ["score"]:
            raise ValueError(
                "score frame must have unique datetime/instrument rows and one score column"
            )
    if BOUNDARY not in history.index.get_level_values("datetime"):
        raise ValueError("missing frozen boundary signal 2026-07-31")
    old = history.loc[history.index.get_level_values("datetime") <= BOUNDARY]
    new = forward.loc[forward.index.get_level_values("datetime") > BOUNDARY]
    if new.empty:
        raise ValueError("no forward signal after 2026-07-31")
    return pd.concat([old, new]).sort_index()


def run(args):
    import qlib
    from qlib.data import D
    from qlib.contrib.evaluate import backtest_daily
    from live_trading.modules.signal_generator import SignalGenerator, _InferenceDataset
    from ensemble_preds import blend_score_series
    from run_pred_backtest import prepare_signal_and_port_cfg
    from config_loader import load_config
    from strategy_stability_metrics import (
        load_regime_labels,
        summarize_stability,
        summarize_period,
    )

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    audit = {
        "status": "running",
        "model": "v2",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "account": 300000,
        "risk_degree": args.risk_degree,
        "hypothesis": f"固定v2五种子与top5/drop1/hold1/f100，仅以risk={args.risk_degree}复算30万元当前费率；不训练、不调参、不晋升。",
        "selection_cutoff": "2026-07-31",
        "requested_end": args.end,
        "evaluation_mode": "frozen_parameter_diagnostic_not_selection",
        "live_activation": False,
        "source_pred": str(args.pred.resolve()),
        "source_pred_sha256": sha(args.pred),
        "st_daily_sha256": sha(args.st_daily),
        "model_manifest_sha256": sha(
            ROOT / "live_trading/models/v2_t5h5es/manifest.json"
        ),
    }

    def save():
        (out / "summary.json").write_text(
            json.dumps(audit, indent=2, ensure_ascii=False) + "\n"
        )

    save()
    try:
        if audit["source_pred_sha256"] != OFFICIAL_PRED_SHA:
            raise ValueError("frozen official prediction SHA mismatch")
        live_path = ROOT / "live_trading/configs/alla_v2_top5d1f100_postclose_real.yaml"
        live = yaml.safe_load(live_path.read_text())
        config_path = args.config or ROOT / live["parity"]["backtest_config"]
        cfg = load_config(str(config_path))
        validate_frozen_config(cfg, risk_degree=args.risk_degree)
        manifest = json.loads(
            (ROOT / "live_trading/models/v2_t5h5es/manifest.json").read_text()
        )
        if [member["seed"] for member in manifest["members"]] != audit["seeds"]:
            raise ValueError("frozen five-seed manifest mismatch")
        for member in manifest["members"]:
            if sha(ROOT / member["model_path"]) != member["sha256"]:
                raise ValueError("frozen model SHA mismatch")
        cfg["data"]["handler"]["end_time"] = args.end
        cfg["segments"]["test"] = ["2020-08-03", args.end]
        cfg["backtest"]["start_time"] = "2020-08-03"
        cfg["backtest"]["end_time"] = args.end
        cfg["universe_filter"]["st_daily"] = str(args.st_daily.resolve())
        qlib.init(
            provider_uri=live["data"]["qlib_dir"],
            region="cn",
            kernels=4,
            expression_cache=None,
            dataset_cache=None,
        )
        cached_bench = None
        if args.reuse_frozen_result:
            forward, cached_bench = load_frozen_reuse(args.reuse_frozen_result, audit)
            audit["reused_frozen_result"] = str(args.reuse_frozen_result.resolve())
            audit["reused_summary_sha256"] = sha(
                args.reuse_frozen_result / "summary.json"
            )
        else:
            generator = SignalGenerator(live, ROOT)
            generator.prepare_for_dates(args.end)
            features = generator._features
            features = features.loc[
                features.index.get_level_values("datetime") >= BOUNDARY
            ].dropna(how="all")
            model_scores = [
                model.predict(_InferenceDataset(features)).rename("score")
                for model in generator._models
            ]
            if any(not s.index.equals(features.index) for s in model_scores):
                raise ValueError("ensemble member index mismatch")
            forward = blend_score_series(model_scores).to_frame("score")
        forward.to_pickle(out / "forward_pred.pkl")
        audit["forward_pred_sha256"] = sha(out / "forward_pred.pkl")
        history = pd.read_pickle(args.pred)
        combined = combine_frozen_scores(history, forward)
        dates = pd.DatetimeIndex(
            combined.index.get_level_values("datetime").unique()
        ).sort_values()
        expected = pd.DatetimeIndex(
            D.calendar(start_time="2020-08-03", end_time=args.end)
        )
        if not expected.isin(dates).all() or expected[-1] != pd.Timestamp(args.end):
            raise ValueError(
                "prediction/calendar does not cover requested diagnostic window"
            )
        # Preserve the official historical benchmark, extend with the same equal-weight recipe.
        old_bench = pd.read_csv(
            ROOT / "backtest/configs/regime-adapt/bench_ew_all.csv",
            index_col=0,
            parse_dates=True,
        ).iloc[:, 0]
        if cached_bench is not None:
            bench = cached_bench
            if not (
                (bench.loc[:BOUNDARY] - old_bench.loc[:BOUNDARY]).abs() < 1e-12
            ).all():
                raise ValueError(
                    "cached historical benchmark differs from frozen source"
                )
        else:
            instruments = D.instruments(
                "all", filter_pipe=live["handler"]["filter_pipe"]
            )
            codes = D.list_instruments(
                instruments, start_time="2026-08-03", end_time=args.end, as_list=True
            )
            returns = D.features(
                codes,
                ["$close/Ref($close,1)-1"],
                start_time="2026-08-03",
                end_time=args.end,
            )
            forward_bench = (
                returns.iloc[:, 0].groupby(level="datetime").mean().fillna(0)
            )
            bench = pd.concat([old_bench.loc[:BOUNDARY], forward_bench]).sort_index()
        if not expected.isin(bench.index).all():
            raise ValueError("equal-weight benchmark coverage missing")
        bench.rename("bench").to_csv(out / "bench_ew_all.csv", index_label="datetime")
        audit["benchmark_sha256"] = sha(out / "bench_ew_all.csv")
        cfg["data"]["benchmark"] = {"equal_weight_csv": str(out / "bench_ew_all.csv")}
        cfg["backtest"]["benchmark"] = cfg["data"]["benchmark"]
        cfg.pop("_config_path", None)
        (out / "config.yaml").write_text(
            yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
        )
        audit["config_path"] = str(out / "config.yaml")
        audit["config_sha256"] = sha(out / "config.yaml")
        save()
        port, signal, stats = prepare_signal_and_port_cfg(cfg, combined)
        (out / "filter_stats.json").write_text(json.dumps(stats, indent=2))
        report, positions = backtest_daily(
            strategy=port["strategy"], executor=port["executor"], **port["backtest"]
        )
        report.to_csv(out / "portfolio_daily.csv", index_label="datetime")
        historical = report.loc[:BOUNDARY]
        forward_report = report.loc["2026-08-03":]
        regimes = load_regime_labels(
            ROOT
            / "backtest/configs/regime-adapt/monthly_regime_labels_eval_window_v1.csv"
        )
        audit.update(
            status="completed",
            report_sha256=sha(out / "portfolio_daily.csv"),
            historical_current_cost=summarize_stability(historical, regimes),
            forward_carried_positions=summarize_period(forward_report),
            full_extended=summarize_period(report),
            forward_start=str(forward_report.index.min().date()),
            actual_end=str(report.index.max().date()),
            forward_initialization="carry holdings and cash from continuous 2020-08-03 replay, not actual live ledger",
            fee_proxy={"open_cost": 0.0000954, "close_cost": 0.0005954, "min_cost": 5},
            limitations=[
                "Historical window was used for early stopping and strategy selection; not out-of-sample.",
                "Forward dates were not used for this parameter choice; local data snapshot is current, not a historical vintage.",
                "Current-fee replay applies a fixed rate/minimum proxy; it does not simulate actual auction slippage or exact per-exchange fees.",
                "No regime assignment is invented for forward months absent from frozen labels.",
            ],
        )
        save()
        print(
            json.dumps(
                {
                    "status": audit["status"],
                    "historical": audit["historical_current_cost"]["full_period"],
                    "forward": audit["forward_carried_positions"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as exc:
        audit.update(status="failed", error=str(exc), traceback=traceback.format_exc())
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pred", required=True, type=Path)
    parser.add_argument("--st-daily", required=True, type=Path)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--risk-degree", required=True, type=float, choices=(0.9, 1.0))
    parser.add_argument(
        "--config", type=Path, help="Explicit saved config for a legacy risk0.9 replay"
    )
    parser.add_argument(
        "--reuse-frozen-result",
        type=Path,
        help="Completed result with authenticated scores and benchmark",
    )
    run(parser.parse_args())


if __name__ == "__main__":
    main()
