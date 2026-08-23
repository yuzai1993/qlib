from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import eval_ic_multi_pool as evaluator  # noqa: E402


class _BoosterFixture:
    def __init__(self, *, best_iteration: int, current_iteration: int):
        self.best_iteration = best_iteration
        self._current_iteration = current_iteration

    def current_iteration(self) -> int:
        return self._current_iteration


class _DoubleEnsembleFixture:
    def __init__(self, boosters):
        self.ensemble = boosters


def test_effective_boosting_iterations_falls_back_when_best_is_unset():
    model = _DoubleEnsembleFixture(
        [
            _BoosterFixture(best_iteration=17, current_iteration=28),
            _BoosterFixture(best_iteration=0, current_iteration=28),
            _BoosterFixture(best_iteration=9, current_iteration=28),
        ]
    )

    assert evaluator._effective_boosting_iterations(model) == [17, 28, 9]


def test_record_rolling_iterations_is_idempotent_across_pool_loads():
    diagnostics = {}
    model = _DoubleEnsembleFixture(
        [
            _BoosterFixture(best_iteration=17, current_iteration=17),
            _BoosterFixture(best_iteration=20, current_iteration=20),
            _BoosterFixture(best_iteration=14, current_iteration=14),
        ]
    )

    evaluator._record_rolling_iterations(
        diagnostics,
        seed=42,
        fold=2,
        model=model,
    )
    evaluator._record_rolling_iterations(
        diagnostics,
        seed=42,
        fold=2,
        model=model,
    )

    assert diagnostics == {"42": {"2": [17, 20, 14]}}


def test_summarize_rolling_iterations_reports_trigger_rate():
    diagnostics = {
        "42": {"1": [17, 28, 9]},
        "1000": {"1": [28, 28, 14]},
    }

    assert evaluator._summarize_rolling_iterations(
        diagnostics,
        max_rounds=28,
        early_stopping_rounds=5,
    ) == {
        "max_rounds": 28,
        "early_stopping_rounds": 5,
        "best_iterations": diagnostics,
        "booster_count": 6,
        "triggered_count": 3,
        "trigger_rate": pytest.approx(0.5),
        "mean_best_iteration": pytest.approx(20.6666666667),
        "min_best_iteration": 9,
        "max_best_iteration": 28,
    }


def test_evaluate_rolling_exposes_model_iteration_diagnostics(monkeypatch):
    dates = pd.to_datetime(["2021-07-16", "2021-07-19"])
    index = pd.MultiIndex.from_product(
        [dates, ["SH600000", "SZ000001", "SZ000002"]],
        names=["datetime", "instrument"],
    )
    label = pd.Series([1.0, 2.0, 3.0, 1.5, 2.5, 3.5], index=index)

    class _CalendarFixture:
        @staticmethod
        def calendar(start_time, end_time=None):
            return dates

    class _PredictingModel(_DoubleEnsembleFixture):
        def predict(self, dataset, segment):
            assert segment == "test"
            return label

    folds = [
        {
            "fold": 1,
            "segments": {
                "train": ["2016-01-04", "2020-01-10"],
                "valid": ["2020-01-13", "2021-07-15"],
                "test": ["2021-07-16", "2021-07-19"],
            },
        }
    ]
    manifest = {
        "folds": folds,
        "sessions": [{"session": "/tmp/rolling-42", "seed": 42}],
        "step": 252,
        "rolling_type": "expanding",
    }
    model = _PredictingModel(
        [
            _BoosterFixture(best_iteration=17, current_iteration=17),
            _BoosterFixture(best_iteration=28, current_iteration=28),
            _BoosterFixture(best_iteration=9, current_iteration=9),
        ]
    )
    monkeypatch.setattr("qlib.data.D", _CalendarFixture)
    monkeypatch.setattr(evaluator, "_load_rolling_sessions", lambda *_: manifest)
    monkeypatch.setattr(evaluator, "_fetch_label", lambda *_args, **_kwargs: label)
    monkeypatch.setattr(evaluator, "_build_dataset", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(evaluator, "_load_model", lambda *_args, **_kwargs: model)
    cfg = {
        "_config_path": "/repo/es5.yaml",
        "data": {
            "handler": {
                "start_time": "2003-01-02",
                "end_time": "2021-07-19",
                "fit_start_time": "2016-01-04",
                "fit_end_time": "2020-01-10",
            }
        },
        "segments": {"test": ["2021-07-16", "2021-07-19"]},
        "model": {
            "kwargs": {
                "epochs": 28,
                "early_stopping_rounds": 5,
            }
        },
    }

    result = evaluator.evaluate_rolling(
        cfg,
        [("/tmp/rolling-42", 42)],
        ["csi1000"],
        min_count=2,
    )

    assert result["rolling"]["model_diagnostics"] == {
        "max_rounds": 28,
        "early_stopping_rounds": 5,
        "best_iterations": {"42": {"1": [17, 28, 9]}},
        "booster_count": 3,
        "triggered_count": 2,
        "trigger_rate": pytest.approx(2 / 3),
        "mean_best_iteration": pytest.approx(18.0),
        "min_best_iteration": 9,
        "max_best_iteration": 28,
    }


def test_segment_bounds_uses_requested_valid_window():
    cfg = {
        "segments": {
            "valid": ["2020-01-13", "2021-07-15"],
            "test": ["2021-07-16", "2026-07-16"],
        }
    }

    assert evaluator._segment_bounds(cfg, "valid") == (
        "2020-01-13",
        "2021-07-15",
    )


def test_cli_accepts_valid_segment():
    args = evaluator.parse_args(
        [
            "--config",
            "dummy.yaml",
            "--sessions",
            "session:42",
            "--pools",
            "csi1000",
            "--segment",
            "valid",
            "--output",
            "out.json",
        ]
    )

    assert args.segment == "valid"
    assert args.eval_label == evaluator.EVAL_LABEL_EXPR
    assert args.eval_label_role == "fixed_1d"
    assert args.eval_end is None
    assert args.st_daily == evaluator.DEFAULT_ST_DAILY


def test_cli_accepts_st_names_snapshot():
    args = evaluator.parse_args(
        [
            "--config",
            "dummy.yaml",
            "--sessions",
            "session:42",
            "--output",
            "out.json",
            "--st-names",
            "st_names.csv",
        ]
    )
    assert args.st_names is not None
    assert args.st_names.name == "st_names.csv"


def test_require_st_daily_exits_when_missing(tmp_path):
    missing = tmp_path / "st_daily.csv"
    with pytest.raises(SystemExit, match="st_daily"):
        evaluator.require_st_daily(missing)


def test_cli_requires_self_role_for_custom_evaluation_label():
    with pytest.raises(SystemExit):
        evaluator.parse_args(
            [
                "--config",
                "dummy.yaml",
                "--sessions",
                "session:42",
                "--output",
                "out.json",
                "--eval-label",
                "Ref($close, -21)/Ref($close, -1)-1",
            ]
        )


def test_cli_accepts_self_label_and_common_end():
    args = evaluator.parse_args(
        [
            "--config",
            "dummy.yaml",
            "--sessions",
            "session:42",
            "--output",
            "out.json",
            "--eval-label-role",
            "self",
            "--eval-label",
            "Ref($close, -21)/Ref($close, -1)-1",
            "--eval-end",
            "2026-04-22",
        ]
    )

    assert args.eval_label_role == "self"
    assert args.eval_label == "Ref($close, -21)/Ref($close, -1)-1"
    assert args.eval_end == "2026-04-22"


def test_effective_segment_uses_override_without_mutating_config():
    cfg = {
        "segments": {
            "test": ["2021-07-16", "2026-07-16"],
        }
    }

    assert evaluator._effective_segment(
        cfg, "test", end_override="2026-04-22"
    ) == ("2021-07-16", "2026-04-22")
    assert cfg["segments"]["test"] == ["2021-07-16", "2026-07-16"]


def test_effective_segment_rejects_override_after_official_end():
    cfg = {"segments": {"test": ["2021-07-16", "2026-07-16"]}}

    with pytest.raises(ValueError, match="official segment end"):
        evaluator._effective_segment(
            cfg, "test", end_override="2026-07-17"
        )


def test_yearly_summaries_split_daily_metrics_by_calendar_year():
    daily = pd.DataFrame(
        {
            "ic": [0.1, 0.3, 0.5],
            "rank_ic": [0.2, 0.4, 0.6],
        },
        index=pd.to_datetime(["2021-12-31", "2022-01-03", "2022-01-04"]),
    )

    yearly = evaluator._yearly_summaries(daily)

    assert set(yearly) == {"2021", "2022"}
    assert yearly["2021"]["rank_ic_mean"] == pytest.approx(0.2)
    assert yearly["2022"]["rank_ic_mean"] == pytest.approx(0.5)


def _write_rolling_session(
    path: Path,
    *,
    seed: int,
    folds: list[dict],
    statuses: list[str] | None = None,
) -> None:
    path.mkdir(parents=True)
    statuses = statuses or ["success"] * len(folds)
    runs = [
        {
            "run": fold["fold"],
            "fold": fold["fold"],
            "status": status,
            "segments": fold["segments"],
            "train_experiment_id": str(seed * 100 + fold["fold"]),
            "train_recorder_id": f"rec-{seed}-{fold['fold']}",
        }
        for fold, status in zip(folds, statuses)
    ]
    (path / "meta.json").write_text(
        json.dumps(
            {
                "mode": "rolling_train_only",
                "seed": seed,
                "step": 4,
                "rolling_type": "expanding",
                "expected_fold_count": len(folds),
                "rolling_folds": folds,
                "runs": runs,
            }
        ),
        encoding="utf-8",
    )


def _folds() -> list[dict]:
    return [
        {
            "fold": 1,
            "segments": {
                "train": ["2020-01-01", "2020-01-03"],
                "valid": ["2020-01-06", "2020-01-10"],
                "test": ["2020-01-13", "2020-01-16"],
            },
        },
        {
            "fold": 2,
            "segments": {
                "train": ["2020-01-01", "2020-01-09"],
                "valid": ["2020-01-10", "2020-01-16"],
                "test": ["2020-01-17", "2020-01-22"],
            },
        },
    ]


def test_load_rolling_sessions_accepts_identical_contiguous_folds(tmp_path):
    folds = _folds()
    sessions = []
    for seed in (42, 1000):
        path = tmp_path / f"rolling-{seed}"
        _write_rolling_session(path, seed=seed, folds=folds)
        sessions.append((str(path), seed))

    manifest = evaluator._load_rolling_sessions(
        sessions,
        pd.bdate_range("2020-01-01", "2020-01-24"),
    )

    assert manifest["folds"] == folds
    assert [(row["seed"], row["session"]) for row in manifest["sessions"]] == [
        (42, str((tmp_path / "rolling-42").resolve())),
        (1000, str((tmp_path / "rolling-1000").resolve())),
    ]
    assert manifest["step"] == 4


def test_load_rolling_sessions_rejects_prediction_gap(tmp_path):
    folds = _folds()
    folds[1]["segments"]["test"][0] = "2020-01-20"
    path = tmp_path / "rolling-42"
    _write_rolling_session(path, seed=42, folds=folds)

    with pytest.raises(ValueError, match="contiguous"):
        evaluator._load_rolling_sessions(
            [(str(path), 42)],
            pd.bdate_range("2020-01-01", "2020-01-24"),
        )


def test_load_rolling_sessions_rejects_incomplete_fold(tmp_path):
    folds = _folds()
    path = tmp_path / "rolling-42"
    _write_rolling_session(
        path,
        seed=42,
        folds=folds,
        statuses=["success", "failed"],
    )

    with pytest.raises(ValueError, match="successful folds"):
        evaluator._load_rolling_sessions(
            [(str(path), 42)],
            pd.bdate_range("2020-01-01", "2020-01-24"),
        )


def test_load_rolling_sessions_rejects_seed_manifest_mismatch(tmp_path):
    folds_a = _folds()
    folds_b = _folds()
    folds_b[1]["segments"]["train"][1] = "2020-01-10"
    path_a = tmp_path / "rolling-42"
    path_b = tmp_path / "rolling-1000"
    _write_rolling_session(path_a, seed=42, folds=folds_a)
    _write_rolling_session(path_b, seed=1000, folds=folds_b)

    with pytest.raises(ValueError, match="fold manifest"):
        evaluator._load_rolling_sessions(
            [(str(path_a), 42), (str(path_b), 1000)],
            pd.bdate_range("2020-01-01", "2020-01-24"),
        )


def test_cli_accepts_rolling_mode():
    args = evaluator.parse_args(
        [
            "--config",
            "dummy.yaml",
            "--sessions",
            "session:42",
            "--output",
            "out.json",
            "--rolling",
        ]
    )

    assert args.rolling is True


def _panel(dates, insts, values):
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(dates), insts],
        names=["datetime", "instrument"],
    )
    return pd.Series(list(values) * len(dates), index=index, dtype="float64")


def test_blend_score_series_is_daily_zscore_then_mean():
    dates = ["2021-01-04"]
    insts = ["A", "B", "C"]
    left = _panel(dates, insts, [3.0, 2.0, 1.0])
    right = _panel(dates, insts, [1.0, 2.0, 3.0])

    blended = evaluator.blend_score_series([left, right])

    # [1,2,3] 的样本标准差为 1，z 分别为 ±1 / 0；等权后全 0。
    assert blended.to_numpy() == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)


def test_official_head_includes_h5_rank_ic_on_blended_signal():
    """官方合成信号要带全宇宙 h5 RankIC，不能只靠 seed_mean 冒充。"""
    dates = pd.bdate_range("2021-01-04", periods=25)
    insts = ["A", "B", "C"]
    seed_a = _panel(dates, insts, [3.0, 2.0, 1.0])
    seed_b = _panel(dates, insts, [2.5, 1.5, 0.5])
    labels = {5: _panel(dates, insts, [0.05, 0.02, -0.01])}

    official = evaluator.official_head_from_preds(
        [seed_a, seed_b],
        labels,
        horizons=[5],
        head_k=[3],
        min_count=1,
    )

    assert official["h5"]["rank_ic_mean"] == pytest.approx(1.0)
    assert official["mean_h"]["rank_ic_mean"] == pytest.approx(1.0)


def test_official_head_uses_blended_signal_not_seed_metric_mean():
    dates = pd.bdate_range("2021-01-04", periods=25)
    insts = ["A", "B", "C"]
    seed_a = _panel(dates, insts, [3.0, 1.0, 0.0])
    seed_b = _panel(dates, insts, [1.0, 9.0, 0.0])
    labels = {1: _panel(dates, insts, [0.02, -0.01, 0.00])}

    official = evaluator.official_head_from_preds(
        [seed_a, seed_b],
        labels,
        horizons=[1],
        head_k=[1],
        min_count=1,
    )
    seed_recs = [
        evaluator.compute_head_blocks(seed_a, labels, [1], [1], min_count=1),
        evaluator.compute_head_blocks(seed_b, labels, [1], [1], min_count=1),
    ]
    metric_mean = evaluator._mean_head_grid(seed_recs, "head")

    ens_ann = official["head"]["1"]["1"]["ann_excess"]
    mean_ann = metric_mean["1"]["1"]["ann_excess"]
    assert ens_ann is not None
    assert mean_ann is not None
    assert ens_ann != pytest.approx(mean_ann, abs=1e-9)


def test_label_window_cutoff_drops_days_whose_exit_leaves_the_window():
    # 标签 Ref($close,-(h+1))/Ref($close,-1)-1 在日 t 记入 t+1→t+h+1 的收益，
    # 故最后 h+1 天的平仓日落在窗口外；h=5 时 20 天日历只有前 14 天可用。
    calendar = pd.bdate_range("2021-01-04", periods=20)

    cutoff = evaluator.label_window_cutoff(calendar, 5)

    assert cutoff == calendar[13]
    assert int((calendar > cutoff).sum()) == 6


def test_label_window_cutoff_scales_with_horizon():
    calendar = pd.bdate_range("2021-01-04", periods=20)

    assert int((calendar > evaluator.label_window_cutoff(calendar, 1)).sum()) == 2
    assert int((calendar > evaluator.label_window_cutoff(calendar, 10)).sum()) == 11


def test_label_window_cutoff_is_none_when_calendar_cannot_hold_one_window():
    calendar = pd.bdate_range("2021-01-04", periods=6)

    assert evaluator.label_window_cutoff(calendar, 5) is None


def test_official_pool_block_prefers_ensemble():
    doc = {
        "pools": {
            "all": {
                "seed_mean": {"head": {"5": {"5": {"net_ann_excess": 0.20}}}},
                "ensemble": {"head": {"5": {"5": {"net_ann_excess": 0.11}}}},
            }
        }
    }

    block = evaluator.official_pool_block(doc)
    assert block["head"]["5"]["5"]["net_ann_excess"] == 0.11
