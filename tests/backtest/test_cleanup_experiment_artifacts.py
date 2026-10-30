from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cleanup_experiment_artifacts as cleanup  # noqa: E402

POOLS = ("csi300", "csi500", "csi1000")


def _metrics(rank_ic, rank_icir):
    return {
        pool: {
            "rank_ic_mean": rank_ic[i],
            "rank_icir": rank_icir[i],
        }
        for i, pool in enumerate(POOLS)
    }


def _row(
    exp_id,
    *,
    metrics,
    result_dirs,
    conclusion="inconclusive",
    direction="feature-test",
):
    return {
        "exp_id": exp_id,
        "direction": direction,
        "phase": "M",
        "baseline_ref": "B1 v1.0",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "conclusion": conclusion,
        "metrics_summary": metrics,
        "result_dirs": result_dirs,
    }


def _baseline():
    return _row(
        "baseline/b1-m",
        direction="baseline",
        conclusion="baseline",
        metrics=_metrics([0.02, 0.03, 0.04], [0.2, 0.3, 0.4]),
        result_dirs=["backtest/result/base-a", "backtest/result/base-b"],
    )


def test_selects_one_best_complete_phase_m_candidate():
    baseline = _baseline()
    lower_average = _row(
        "feature-test/a",
        metrics=_metrics([0.021, 0.031, 0.041], [0.21, 0.31, 0.41]),
        result_dirs=["backtest/result/a"],
    )
    higher_average = _row(
        "feature-test/b",
        metrics=_metrics([0.022, 0.032, 0.042], [0.19, 0.29, 0.39]),
        result_dirs=["backtest/result/b"],
    )
    not_all_pools = _row(
        "feature-test/c",
        metrics=_metrics([0.03, 0.03, 0.05], [0.5, 0.5, 0.5]),
        result_dirs=["backtest/result/c"],
    )

    retained = cleanup.select_retained_rows(
        [baseline, lower_average, higher_average, not_all_pools]
    )

    assert [row["exp_id"] for row in retained] == [
        "baseline/b1-m",
        "feature-test/b",
    ]


def test_rank_icir_breaks_equal_rank_ic_tie():
    baseline = _baseline()
    weaker_ir = _row(
        "feature-test/a",
        metrics=_metrics([0.021, 0.031, 0.041], [0.20, 0.30, 0.40]),
        result_dirs=["backtest/result/a"],
    )
    stronger_ir = _row(
        "feature-test/b",
        metrics=_metrics([0.021, 0.031, 0.041], [0.22, 0.32, 0.42]),
        result_dirs=["backtest/result/b"],
    )

    retained = cleanup.select_retained_rows([baseline, weaker_ir, stronger_ir])

    assert retained[-1]["exp_id"] == "feature-test/b"


def test_primary_csi1000_rank_ic_beats_larger_cross_pool_average():
    baseline = _baseline()
    stronger_primary = _row(
        "feature-test/primary",
        metrics=_metrics(
            [0.0201, 0.0301, 0.0500],
            [0.201, 0.301, 0.401],
        ),
        result_dirs=["backtest/result/primary"],
    )
    stronger_average = _row(
        "feature-test/average",
        metrics=_metrics(
            [0.0300, 0.0400, 0.0410],
            [0.202, 0.302, 0.402],
        ),
        result_dirs=["backtest/result/average"],
    )

    retained = cleanup.select_retained_rows(
        [baseline, stronger_primary, stronger_average]
    )

    assert retained[-1]["exp_id"] == "feature-test/primary"


def test_primary_csi1000_rank_icir_breaks_primary_rank_ic_tie():
    baseline = _baseline()
    stronger_primary_ir = _row(
        "feature-test/primary-ir",
        metrics=_metrics(
            [0.0201, 0.0301, 0.0450],
            [0.201, 0.301, 0.500],
        ),
        result_dirs=["backtest/result/primary-ir"],
    )
    stronger_average = _row(
        "feature-test/average",
        metrics=_metrics(
            [0.0300, 0.0400, 0.0450],
            [0.400, 0.500, 0.401],
        ),
        result_dirs=["backtest/result/average"],
    )

    retained = cleanup.select_retained_rows(
        [baseline, stronger_primary_ir, stronger_average]
    )

    assert retained[-1]["exp_id"] == "feature-test/primary-ir"


def test_keeps_only_baseline_when_no_candidate_beats_every_pool():
    baseline = _baseline()
    candidate = _row(
        "feature-test/a",
        metrics=_metrics([0.03, 0.031, 0.039], [0.5, 0.5, 0.5]),
        result_dirs=["backtest/result/a"],
    )

    assert cleanup.select_retained_rows([baseline, candidate]) == [baseline]


def test_incomplete_seed_group_is_not_a_retention_candidate():
    baseline = _baseline()
    candidate = _row(
        "feature-test/incomplete",
        metrics=_metrics([0.03, 0.04, 0.05], [0.5, 0.5, 0.5]),
        result_dirs=["backtest/result/incomplete"],
    )
    candidate["seeds"] = [42, 1000, 2000, 3000]

    assert cleanup.select_retained_rows([baseline, candidate]) == [baseline]


def test_later_phase_s_baseline_does_not_replace_phase_m_baseline():
    baseline = _baseline()
    phase_s = _baseline()
    phase_s.update({"exp_id": "baseline/b1-s", "phase": "S"})

    assert cleanup.select_retained_rows([baseline, phase_s]) == [baseline]


def test_latest_phase_m_b6_anchor_replaces_b5_and_has_no_b5_relative_candidate():
    b5 = _baseline()
    b5.update({"exp_id": "baseline/b5-m", "baseline_ref": "B5 v1.0"})
    b6 = _baseline()
    b6.update({"exp_id": "baseline/b6-m", "baseline_ref": "B6 v1.0"})
    old_winner = _row(
        "model-hyperparam/valid-rankic-search-v1",
        metrics=_metrics([0.03, 0.04, 0.05], [0.5, 0.5, 0.5]),
        result_dirs=["backtest/result/old-winner"],
    )
    old_winner["baseline_ref"] = "B5 v1.0"

    assert cleanup.select_retained_rows([b5, old_winner, b6]) == [b6]


def test_non_finite_candidate_metrics_are_ineligible():
    baseline = _baseline()
    for value in (math.nan, math.inf, -math.inf):
        candidate = _row(
            f"feature-test/non-finite-{value}",
            metrics=_metrics([0.03, 0.04, 0.05], [0.5, 0.5, 0.5]),
            result_dirs=["backtest/result/non-finite"],
        )
        candidate["metrics_summary"]["csi300"]["rank_ic_mean"] = value

        assert cleanup.select_retained_rows([baseline, candidate]) == [baseline]


def test_forward_holdout_row_can_explicitly_opt_out_of_baseline_retention():
    baseline = _baseline()
    forward = _row(
        "train-recency/rankic-winner-post2020",
        metrics=_metrics([0.03, 0.04, 0.05], [0.5, 0.5, 0.5]),
        result_dirs=["backtest/result/forward"],
    )
    forward["cleanup_retention_eligible"] = False

    assert cleanup.select_retained_rows([baseline, forward]) == [baseline]


def _write_train_run(path: Path, experiment_id: str, run_index: int = 1):
    recorder_id = f"train-rec-{experiment_id}"
    run = path / f"run_{run_index:02d}"
    run.mkdir(parents=True)
    (run / "mlruns_link.json").write_text(
        json.dumps(
            {
                "train_experiment_id": experiment_id,
                "train_recorder_id": recorder_id,
                "train_artifacts": f"mlruns/{experiment_id}/{recorder_id}",
            }
        )
    )
    recorder = path.parents[2] / "mlruns" / experiment_id / recorder_id
    (recorder / "artifacts").mkdir(parents=True)
    (recorder / "artifacts/trained_model").write_bytes(b"trained-model")
    (recorder / "meta.yaml").write_text(
        f"experiment_id: '{experiment_id}'\nrun_id: {recorder_id}\nstatus: 3\nlifecycle_stage: active\n"
    )


def _write_session(path: Path, experiment_id: str):
    path.mkdir(parents=True)
    (path / "meta.json").write_text(
        json.dumps(
            {
                "mode": "train_only",
                "runs": [
                    {
                        "run": 1,
                        "status": "success",
                        "train_experiment_id": experiment_id,
                        "train_recorder_id": f"train-rec-{experiment_id}",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_train_run(path, experiment_id)


def _write_rolling_session(path: Path, experiment_ids: list[str]):
    path.mkdir(parents=True)
    folds = [
        {
            "fold": index,
            "segments": {
                "train": ["2016-01-02", f"202{index}-01-01"],
                "valid": [f"202{index}-01-02", f"202{index}-06-30"],
                "test": [f"202{index}-07-01", f"202{index + 1}-06-30"],
            },
        }
        for index in range(1, len(experiment_ids) + 1)
    ]
    (path / "meta.json").write_text(
        json.dumps(
            {
                "mode": "rolling_train_only",
                "expected_fold_count": len(folds),
                "rolling_folds": folds,
                "runs": [
                    {
                        "run": fold["fold"],
                        "fold": fold["fold"],
                        "segments": fold["segments"],
                        "status": "success",
                        "train_experiment_id": experiment_id,
                        "train_recorder_id": f"train-rec-{experiment_id}",
                    }
                    for fold, experiment_id in zip(folds, experiment_ids)
                ],
            }
        ),
        encoding="utf-8",
    )
    for run_index, experiment_id in enumerate(experiment_ids, start=1):
        _write_train_run(path, experiment_id, run_index)


def _write_phase_s_session(path: Path, experiment_id: str):
    path.mkdir(parents=True)
    (path / "meta.json").write_text(
        json.dumps(
            {
                "mode": "pred_backtest",
                "runs": [
                    {
                        "status": "success",
                        "backtest_recorder_id": f"rec-{experiment_id}",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run = path / "run_01"
    run.mkdir()
    (run / "mlruns_link.json").write_text(
        json.dumps(
            {
                "backtest_experiment_id": experiment_id,
                "backtest_recorder_id": f"rec-{experiment_id}",
            }
        ),
        encoding="utf-8",
    )
    recorder = path.parents[2] / "mlruns" / experiment_id / f"rec-{experiment_id}"
    recorder.mkdir(parents=True, exist_ok=True)
    (recorder / "meta.yaml").write_text(
        f"experiment_id: '{experiment_id}'\nrun_id: rec-{experiment_id}\nstatus: 3\nlifecycle_stage: active\n"
    )


def _bind_phase_s_baseline_metadata(path: Path, pool: str, row: dict, root: Path):
    meta_path = path / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        config_path=str((root / f"backtest/configs/{pool}.yaml").resolve()),
        source_pred=str((root / f"backtest/predictions/{pool}.pkl").resolve()),
        source_pred_sha256=f"{pool}-sha",
        backtest={
            "account": 500000,
            "start_time": "2021-07-16",
            "end_time": "2026-07-31",
            "benchmark": row["benchmarks"][pool],
            "exchange_kwargs": row["fees"],
        },
        strategy={
            "class": "TopkDropoutStrategy",
            "risk_degree": 0.95,
            "topk": 30,
            "n_drop": 2,
            "hold_thresh": 20,
        },
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def _phase_s_row(model_ref: str, result_names: list[str], state="test_complete"):
    baseline = "topk-t10-d2-h1"
    winner = "topk-t20-d2-h10"
    iterator = iter(result_names)
    return {
        "exp_id": f"strategy-sweep/{model_ref}",
        "phase": "S",
        "state": state,
        "model_ref": model_ref,
        "selected_candidate_id": winner,
        "cleanup_retention_eligible": state == "test_complete",
        "test_results": (
            {
                pool: [
                    {
                        "candidate_id": baseline,
                        "result_dir": f"backtest/result/{next(iterator)}",
                    },
                    {
                        "candidate_id": winner,
                        "result_dir": f"backtest/result/{next(iterator)}",
                    },
                ]
                for pool in ("csi1000", "csi300", "csi500")
            }
            if state == "test_complete"
            else {}
        ),
    }


def _phase_s_baseline_row(result_names: list[str]):
    benchmarks = {"csi1000": "SH000852", "csi300": "SH000300", "csi500": "SH000905"}
    return {
        "exp_id": "baseline/b2-s-on-b6-m",
        "direction": "baseline-strategy",
        "phase": "S",
        "state": "baseline",
        "conclusion": "baseline",
        "baseline_ref": "B2-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "strategy": {
            "candidate_id": "topk-t30-d2-h20",
            "strategy_class": "TopkDropoutStrategy",
            "topk": 30,
            "n_drop": 2,
            "hold_thresh": 20,
        },
        "test_segment": ["2021-07-16", "2026-07-31"],
        "account": 500000,
        "risk_degree": 0.95,
        "fees": {"open_cost": 0.00021, "close_cost": 0.00071},
        "benchmarks": benchmarks,
        "cleanup_retention_eligible": True,
        "test_results": {
            pool: {
                "candidate_id": "topk-t30-d2-h20",
                "result_dir": f"backtest/result/{name}",
                "config": f"backtest/configs/{pool}.yaml",
                "source_pred": f"backtest/predictions/{pool}.pkl",
                "source_pred_sha256": f"{pool}-sha",
            }
            for pool, name in zip(("csi1000", "csi300", "csi500"), result_names)
        },
    }


def test_cleanup_plan_is_direct_child_safe_and_dry_run_is_non_destructive(tmp_path):
    result_root = tmp_path / "backtest" / "result"
    mlruns_root = tmp_path / "mlruns"
    base_dirs = [result_root / f"base-{i}" for i in range(5)]
    candidate_dirs = [result_root / f"candidate-{i}" for i in range(5)]
    loser = result_root / "loser-a"
    for i, session in enumerate(base_dirs, start=101):
        _write_session(session, str(i))
    for i, session in enumerate(candidate_dirs, start=201):
        _write_session(session, str(i))
    _write_session(loser, "303")
    for exp_id in (
        *(str(i) for i in range(101, 106)),
        *(str(i) for i in range(201, 206)),
        "303",
    ):
        (mlruns_root / exp_id).mkdir(parents=True, exist_ok=True)
    (mlruns_root / ".trash").mkdir()

    baseline = _baseline()
    baseline["result_dirs"] = [
        *(f"backtest/result/base-{i}" for i in range(5)),
    ]
    winner = _row(
        "feature-test/winner",
        metrics=_metrics([0.021, 0.031, 0.041], [0.21, 0.31, 0.41]),
        result_dirs=[f"backtest/result/candidate-{i}" for i in range(5)],
    )

    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, winner])

    assert plan["keep_result_dirs"] == sorted(
        [path.resolve() for path in base_dirs + candidate_dirs]
    )
    assert plan["delete_result_dirs"] == [loser.resolve()]
    assert plan["keep_mlruns_dirs"] == sorted(
        [(mlruns_root / str(i)).resolve() for i in (*range(101, 106), *range(201, 206))]
    )
    assert plan["delete_mlruns_dirs"] == [
        (mlruns_root / ".trash").resolve(),
        (mlruns_root / "303").resolve(),
    ]
    assert plan["errors"] == []

    cleanup.apply_cleanup(plan, apply=False)

    assert loser.exists()
    assert (mlruns_root / "303").exists()

    cleanup.apply_cleanup(plan, apply=True)

    assert all(path.exists() for path in base_dirs + candidate_dirs)
    assert not loser.exists()
    assert (mlruns_root / "101").exists()
    assert (mlruns_root / "202").exists()
    assert not (mlruns_root / "303").exists()
    assert not (mlruns_root / ".trash").exists()


def test_cleanup_retains_every_fold_experiment_for_five_rolling_sessions(
    tmp_path,
):
    result_root = tmp_path / "backtest" / "result"
    mlruns_root = tmp_path / "mlruns"
    base_dirs = [result_root / f"base-{index}" for index in range(5)]
    rolling_dirs = [result_root / f"rolling-{index}" for index in range(5)]

    for index, session in enumerate(base_dirs, start=101):
        _write_session(session, str(index))
        (mlruns_root / str(index)).mkdir(parents=True, exist_ok=True)
    rolling_ids = []
    for index, session in enumerate(rolling_dirs, start=201):
        fold_ids = [str(index * 10 + 1), str(index * 10 + 2)]
        rolling_ids.extend(fold_ids)
        _write_rolling_session(session, fold_ids)
        for experiment_id in fold_ids:
            (mlruns_root / experiment_id).mkdir(parents=True, exist_ok=True)

    baseline = _baseline()
    baseline["result_dirs"] = [f"backtest/result/base-{index}" for index in range(5)]
    rolling = _row(
        "train-schedule/expanding-annual",
        metrics=_metrics([0.021, 0.031, 0.041], [0.21, 0.31, 0.41]),
        result_dirs=[f"backtest/result/rolling-{index}" for index in range(5)],
    )

    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, rolling])

    assert plan["errors"] == []
    assert plan["candidate_exp_id"] == "train-schedule/expanding-annual"
    assert plan["keep_result_dirs"] == sorted(
        [path.resolve() for path in base_dirs + rolling_dirs]
    )
    assert plan["keep_mlruns_dirs"] == sorted(
        [
            *(mlruns_root / str(index) for index in range(101, 106)),
            *(mlruns_root / experiment_id for experiment_id in rolling_ids),
        ]
    )


def test_cli_writes_machine_readable_plan_to_stdout(tmp_path, capsys):
    registry = tmp_path / "registry.jsonl"
    registry.write_text(json.dumps(_baseline()) + "\n", encoding="utf-8")

    cleanup.main(
        [
            "--repo-root",
            str(tmp_path),
            "--registry",
            str(registry),
        ]
    )

    captured = capsys.readouterr()
    plan = json.loads(captured.out)
    assert plan["baseline_exp_id"] == "baseline/b1-m"
    assert "dry-run only" in captured.err


def test_phase_s_retains_only_latest_registered_strategy_baseline(tmp_path):
    result_root = tmp_path / "backtest/result"
    mlruns_root = tmp_path / "mlruns"
    baseline = _baseline()
    baseline["result_dirs"] = []
    for index in range(5):
        name = f"phase-m-{index}"
        experiment_id = str(100 + index)
        _write_session(result_root / name, experiment_id)
        (mlruns_root / experiment_id).mkdir(parents=True, exist_ok=True)
        baseline["result_dirs"].append(f"backtest/result/{name}")

    legacy_names = [f"legacy-phase-s-{index}" for index in range(6)]
    baseline_names = [f"phase-s-baseline-{index}" for index in range(3)]
    for index, name in enumerate(legacy_names, start=200):
        _write_phase_s_session(result_root / name, str(index))
        (mlruns_root / str(index)).mkdir(parents=True, exist_ok=True)
    phase_s_baseline = _phase_s_baseline_row(baseline_names)
    for index, (pool, name) in enumerate(
        zip(("csi1000", "csi300", "csi500"), baseline_names), start=206
    ):
        session = result_root / name
        _write_phase_s_session(session, str(index))
        _bind_phase_s_baseline_metadata(session, pool, phase_s_baseline, tmp_path)
        (mlruns_root / str(index)).mkdir(parents=True, exist_ok=True)
    _write_phase_s_session(result_root / "loser", "999")
    (mlruns_root / "999").mkdir(parents=True, exist_ok=True)

    plan = cleanup.build_cleanup_plan(
        tmp_path,
        [
            baseline,
            _phase_s_row("b1-m", legacy_names),
            phase_s_baseline,
        ],
    )

    assert plan["errors"] == []
    assert set(plan["keep_result_dirs"]) == {
        (result_root / f"phase-m-{index}").resolve() for index in range(5)
    } | {(result_root / name).resolve() for name in baseline_names}
    assert set(plan["delete_result_dirs"]) == {
        (result_root / name).resolve() for name in legacy_names
    } | {(result_root / "loser").resolve()}
    assert set(plan["keep_mlruns_dirs"]) == {
        (mlruns_root / str(index)).resolve() for index in range(100, 105)
    } | {(mlruns_root / str(index)).resolve() for index in range(206, 209)}
    assert set(plan["delete_mlruns_dirs"]) == {
        (mlruns_root / str(index)).resolve() for index in range(200, 206)
    } | {(mlruns_root / "999").resolve()}


def test_phase_s_baseline_requires_three_distinct_test_sessions():
    row = _phase_s_baseline_row(["same", "same", "same"])

    with pytest.raises(ValueError, match="distinct test sessions"):
        cleanup.select_phase_s_retained_result_paths([row])


def test_phase_s_baseline_retains_optional_interactive_full_period_result():
    row = _phase_s_baseline_row(["csi1000", "csi300", "csi500"])
    row["interactive_full_period_result"] = {
        "result_dir": "backtest/result/baseline-full-interactive",
        "config": "backtest/configs/baseline-full.yaml",
        "source_pred": "backtest/predictions/csi1000-full.pkl",
        "source_pred_sha256": "full-sha",
        "period": ["2020-01-13", "2026-07-31"],
    }

    assert cleanup.select_phase_s_retained_result_paths([row]) == {
        "backtest/result/csi1000",
        "backtest/result/csi300",
        "backtest/result/csi500",
        "backtest/result/baseline-full-interactive",
    }


def test_phase_s_baseline_session_metadata_is_bound_to_expected_pool(tmp_path):
    names = ["csi1000-session", "csi300-session", "csi500-session"]
    row = _phase_s_baseline_row(names)
    for pool, name in zip(("csi1000", "csi300", "csi500"), names):
        session = tmp_path / "backtest/result" / name
        _write_phase_s_session(session, str(300 + len(pool)))
        _bind_phase_s_baseline_metadata(session, pool, row, tmp_path)

    errors = []
    cleanup._validate_phase_s_baseline_sessions(tmp_path, row, errors)
    assert errors == []

    row["test_results"]["csi300"]["result_dir"] = row["test_results"]["csi1000"][
        "result_dir"
    ]
    errors = []
    cleanup._validate_phase_s_baseline_sessions(tmp_path, row, errors)
    assert any("csi300" in error and "metadata mismatch" in error for error in errors)


def test_phase_s_completed_non_selecting_diagnostic_is_not_retained():
    diagnostic = {
        "exp_id": "strategy-stability-full-period/b1-m",
        "direction": "strategy-stability-full-period",
        "phase": "S",
        "state": "complete",
        "conclusion": "diagnostic_no_selection",
        "cleanup_retention_eligible": False,
        "result_dirs": ["backtest/result/diagnostic-a"],
    }

    assert cleanup.select_phase_s_retained_result_paths([diagnostic]) == set()


def test_phase_s_incomplete_non_selecting_diagnostic_still_blocks_cleanup():
    diagnostic = {
        "exp_id": "strategy-stability-full-period/b1-m",
        "phase": "S",
        "state": "preregistered",
        "conclusion": "preregistered",
        "cleanup_retention_eligible": False,
    }

    with pytest.raises(ValueError, match="diagnostic row is malformed"):
        cleanup.select_phase_s_retained_result_paths([diagnostic])


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("state", "preregistered"),
        ("direction", "strategy-sweep-b1-m"),
        ("exp_id", "strategy-sweep/b1-m"),
    ],
)
def test_phase_s_partially_matching_diagnostic_blocks_cleanup(mutation, value):
    diagnostic = {
        "exp_id": "strategy-stability-full-period/b1-m",
        "direction": "strategy-stability-full-period",
        "phase": "S",
        "state": "complete",
        "conclusion": "diagnostic_no_selection",
        "cleanup_retention_eligible": False,
    }
    diagnostic[mutation] = value

    with pytest.raises(ValueError, match="diagnostic row is malformed"):
        cleanup.select_phase_s_retained_result_paths([diagnostic])


def test_phase_s_retention_accepts_absolute_direct_child_inside_result_root(tmp_path):
    session = tmp_path / "backtest/result/final-test"
    session.mkdir(parents=True)
    errors = []

    retained = cleanup._retained_phase_s_dirs(
        tmp_path, [str(session.resolve())], errors
    )

    assert retained == [session.resolve()]
    assert errors == []


def test_incomplete_phase_s_bundle_blocks_all_deletion(tmp_path):
    result_root = tmp_path / "backtest/result"
    mlruns_root = tmp_path / "mlruns"
    baseline = _baseline()
    baseline["result_dirs"] = []
    for index in range(5):
        name = f"phase-m-{index}"
        experiment_id = str(100 + index)
        _write_session(result_root / name, experiment_id)
        (mlruns_root / experiment_id).mkdir(parents=True, exist_ok=True)
        baseline["result_dirs"].append(f"backtest/result/{name}")
    _write_phase_s_session(result_root / "loser", "999")
    (mlruns_root / "999").mkdir(parents=True, exist_ok=True)

    plan = cleanup.build_cleanup_plan(
        tmp_path,
        [baseline, _phase_s_row("b1-m", [], state="valid_complete")],
    )

    assert any("Phase S" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []


def test_missing_retained_metadata_blocks_all_deletion(tmp_path):
    result_root = tmp_path / "backtest" / "result"
    mlruns_root = tmp_path / "mlruns"
    (result_root / "base-a").mkdir(parents=True)
    _write_session(result_root / "loser", "303")
    (mlruns_root / "303").mkdir(parents=True, exist_ok=True)

    baseline = _baseline()
    baseline["result_dirs"] = ["backtest/result/base-a"]
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline])

    assert plan["errors"]
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []


def test_non_finite_baseline_metric_blocks_all_deletion(tmp_path):
    baseline = _baseline()
    baseline["metrics_summary"]["csi300"]["rank_ic_mean"] = math.nan

    plan = cleanup.build_cleanup_plan(tmp_path, [baseline])

    assert any("baseline metric" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []


@pytest.mark.parametrize(
    "seeds",
    [
        [],
        [42, 1000, 2000, 3000],
        [42, 1000, 2000, 3000, 3000],
        [42, 1000, 2000, 3000, 9999],
    ],
)
def test_invalid_baseline_seed_group_blocks_all_deletion(tmp_path, seeds):
    baseline = _baseline()
    baseline["seeds"] = seeds

    plan = cleanup.build_cleanup_plan(tmp_path, [baseline])

    assert any("baseline seeds" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []


def test_result_symlink_blocks_all_deletion_before_apply(tmp_path):
    result_root = tmp_path / "backtest" / "result"
    mlruns_root = tmp_path / "mlruns"
    baseline = _baseline()
    baseline["result_dirs"] = []
    for i in range(5):
        session = result_root / f"base-{i}"
        experiment_id = str(100 + i)
        _write_session(session, experiment_id)
        (mlruns_root / experiment_id).mkdir(parents=True, exist_ok=True)
        baseline["result_dirs"].append(f"backtest/result/base-{i}")
    _write_session(result_root / "loser", "303")
    (mlruns_root / "303").mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (result_root / "unsafe-link").symlink_to(outside, target_is_directory=True)

    plan = cleanup.build_cleanup_plan(tmp_path, [baseline])

    assert any("symlink" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []
    cleanup.apply_cleanup(plan, apply=False)
    assert (result_root / "loser").exists()


def test_mlruns_symlink_blocks_all_deletion_before_apply(tmp_path):
    result_root = tmp_path / "backtest" / "result"
    mlruns_root = tmp_path / "mlruns"
    baseline = _baseline()
    baseline["result_dirs"] = []
    for i in range(5):
        session = result_root / f"base-{i}"
        experiment_id = str(100 + i)
        _write_session(session, experiment_id)
        (mlruns_root / experiment_id).mkdir(parents=True, exist_ok=True)
        baseline["result_dirs"].append(f"backtest/result/base-{i}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (mlruns_root / "999").symlink_to(outside, target_is_directory=True)

    plan = cleanup.build_cleanup_plan(tmp_path, [baseline])

    assert any("symlink" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []


@pytest.mark.parametrize("artifact_root", ["backtest/result", "mlruns"])
def test_artifact_root_symlink_blocks_all_deletion(tmp_path, artifact_root):
    external = tmp_path / "external"
    external.mkdir()
    root = tmp_path / artifact_root
    root.parent.mkdir(parents=True, exist_ok=True)
    root.symlink_to(external, target_is_directory=True)

    plan = cleanup.build_cleanup_plan(tmp_path, [_baseline()])

    assert any("artifact root" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []


def test_apply_preflights_every_target_before_first_deletion(tmp_path):
    result_root = tmp_path / "backtest" / "result"
    mlruns_root = tmp_path / "mlruns"
    loser = result_root / "loser"
    outside = tmp_path / "outside"
    loser.mkdir(parents=True)
    outside.mkdir()
    mlruns_root.mkdir()
    plan = {
        "result_root": result_root,
        "mlruns_root": mlruns_root,
        "delete_result_dirs": [loser.resolve(), outside.resolve()],
        "delete_mlruns_dirs": [],
        "errors": [],
    }

    with pytest.raises(ValueError, match="non-child"):
        cleanup.apply_cleanup(plan, apply=True)

    assert loser.exists()
    assert outside.exists()


def _complete_baseline(root):
    row = _baseline()
    row["result_dirs"] = []
    for index in range(5):
        session = root / f"backtest/result/base-{index}"
        _write_session(session, str(100 + index))
        (root / "mlruns" / str(100 + index)).mkdir(parents=True, exist_ok=True)
        row["result_dirs"].append(f"backtest/result/base-{index}")
    return row


def _local_phase_m_baseline(root, version, suffix):
    row = {
        "exp_id": f"regime-adapt/{suffix}",
        "phase": "M",
        "phase_m_protocol": "v1",
        "state": "completed",
        "baseline_version": version,
        "baseline_ref": "self",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "result_dirs": [],
    }
    for seed in row["seeds"]:
        relative = f"backtest/result/{suffix}_s{seed}"
        session = root / relative
        artifacts = session / "run_01/artifacts_root/artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "trained_model").write_bytes(b"model")
        (session / "meta.json").write_text(
            json.dumps(
                {
                    "mode": "train_only",
                    "n_runs": 1,
                    "regime_adapt": {"seed": seed},
                }
            )
        )
        (session / "run_01/train_summary.json").write_text(json.dumps({"seed": seed}))
        (session / "run_01/mlruns_link.json").write_text(
            json.dumps(
                {
                    "train_artifacts": f"{relative}/run_01/artifacts_root",
                }
            )
        )
        row["result_dirs"].append(relative)
    return row


def test_current_full_period_phase_s_retains_one_formal_session():
    row = _phase_s_baseline_row(["old-a", "old-b", "old-c"])
    row.update(
        evaluation_mode="full_history_in_sample",
        selection_pool="csi1000",
        result_dirs=["backtest/result/b4-full"],
    )
    row.pop("test_results")
    assert cleanup.select_phase_s_retained_result_paths([row]) == {
        "backtest/result/b4-full"
    }


@pytest.mark.parametrize("paths", [[], ["backtest/result/a", "backtest/result/b"]])
def test_full_period_phase_s_rejects_missing_or_multiple_formal_sessions(paths):
    row = _phase_s_baseline_row(["a", "b", "c"])
    row.update(
        evaluation_mode="full_history_in_sample",
        selection_pool="csi1000",
        result_dirs=paths,
    )
    with pytest.raises(ValueError, match="one.*full-period"):
        cleanup.select_phase_s_retained_result_paths([row])


def test_phase_m_v1_baseline_groups_and_current_bt_dependency_are_retained(tmp_path):
    baseline = _complete_baseline(tmp_path)
    v2 = _local_phase_m_baseline(tmp_path, "v2", "m0-es")
    v3 = _local_phase_m_baseline(tmp_path, "v3", "m0-top3")
    v4 = _local_phase_m_baseline(tmp_path, "v4", "m0-rankic")
    bt_session = tmp_path / "backtest/result/current-bt"
    _write_phase_s_session(bt_session, "800")
    (tmp_path / "mlruns/800").mkdir(exist_ok=True)
    bt = {
        "exp_id": "baseline/phase-m-v1-bt-v5",
        "phase": "S",
        "report_kind": "phase_m_v1_bt_baseline",
        "current_bt": True,
        "seeds": [42, 1000, 2000, 3000, 4000],
        "model_ref": v2["exp_id"],
        "ensemble_session": "backtest/result/current-bt",
    }
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, v2, v3, v4, bt])
    assert plan["errors"] == []
    assert bt_session in plan["keep_result_dirs"]
    assert tmp_path / "mlruns/800" in plan["keep_mlruns_dirs"]
    assert all(
        tmp_path / raw in plan["keep_result_dirs"]
        for row in (v2, v3, v4)
        for raw in row["result_dirs"]
    )


@pytest.mark.parametrize("breakage", ["seed", "model", "symlink", "link_escape"])
def test_incomplete_local_phase_m_v1_baseline_blocks_all_deletion(tmp_path, breakage):
    baseline = _complete_baseline(tmp_path)
    v4 = _local_phase_m_baseline(tmp_path, "v4", "rankic")
    session = tmp_path / v4["result_dirs"][0]
    if breakage == "seed":
        v4["seeds"] = [42, 1000, 2000, 3000]
    elif breakage in ("model", "symlink"):
        model = session / "run_01/artifacts_root/artifacts/trained_model"
        model.unlink()
        if breakage == "symlink":
            outside = tmp_path / "external-model"
            outside.write_bytes(b"model")
            model.symlink_to(outside)
    else:
        (session / "run_01/mlruns_link.json").write_text(
            json.dumps({"train_artifacts": "../outside"})
        )
    _write_session(tmp_path / "backtest/result/loser", "999")
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, v4])
    assert plan["errors"]
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []


def test_result_audit_directories_are_preserved(tmp_path):
    baseline = _complete_baseline(tmp_path)
    for name in ("eval_regime_ablation", "phase_s_regime", "unknown-audit"):
        (tmp_path / "backtest/result" / name).mkdir()
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline])
    assert plan["errors"] == []
    assert plan["delete_result_dirs"] == []


def test_missing_baseline_does_not_fall_back_to_sibling_research_root(tmp_path):
    production = tmp_path / "production"
    research = tmp_path / "research"
    baseline = _complete_baseline(research)
    production.mkdir()
    plan = cleanup.build_cleanup_plan(production, [baseline])
    assert plan["errors"]
    assert any("--repo-root" in warning for warning in plan["warnings"])
    assert plan["keep_result_dirs"] == []
    assert plan["delete_result_dirs"] == []


def test_absolute_phase_m_sessions_inside_explicit_root_are_retained(tmp_path):
    baseline = _complete_baseline(tmp_path)
    baseline["result_dirs"] = [str(tmp_path / raw) for raw in baseline["result_dirs"]]
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline])
    assert plan["errors"] == []
    assert len(plan["keep_result_dirs"]) == 5


def test_phase_m_v1_bt_missing_model_ref_blocks_deletion(tmp_path):
    baseline = _complete_baseline(tmp_path)
    bt = {
        "exp_id": "baseline/bt",
        "phase": "S",
        "current_bt": True,
        "report_kind": "phase_m_v1_bt_baseline",
        "model_ref": "missing",
    }
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, bt])
    assert any("model_ref" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []


def test_legacy_script_export_refuses_incomplete_retention(tmp_path, monkeypatch):
    import plan_artifact_cleanup as legacy

    (tmp_path / "backtest/experiments").mkdir(parents=True)
    (tmp_path / "backtest/experiments/registry.jsonl").write_text(
        json.dumps(_baseline()) + "\n"
    )
    script = tmp_path / "delete.sh"
    monkeypatch.setattr(legacy, "EXP_ROOT", tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["plan_artifact_cleanup.py", "--emit-script", str(script)]
    )
    with pytest.raises(ValueError, match="retention errors"):
        legacy.main()
    assert not script.exists()


def test_absolute_phase_m_path_outside_selected_root_blocks_cleanup(tmp_path):
    baseline = _complete_baseline(tmp_path / "research")
    baseline["result_dirs"] = [
        str(tmp_path / "research" / raw) for raw in baseline["result_dirs"]
    ]
    production = tmp_path / "production"
    production.mkdir()
    plan = cleanup.build_cleanup_plan(production, [baseline])
    assert any("unsafe result path" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []


def test_missing_retained_mlflow_experiment_blocks_cleanup(tmp_path):
    baseline = _complete_baseline(tmp_path)
    (tmp_path / "mlruns/100").rename(tmp_path / "removed-100")
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline])
    assert any("MLflow experiment missing" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []


def test_historical_versioned_model_keeps_its_original_baseline_ref(tmp_path):
    baseline = _complete_baseline(tmp_path)
    historical = _local_phase_m_baseline(tmp_path, "v1", "historical")
    historical["baseline_ref"] = "regime-adapt/later-baseline"
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, historical])
    assert plan["errors"] == []
    assert len(plan["keep_result_dirs"]) == 10


def _full_period_fixture(root, monkeypatch):
    import pandas as pd
    import yaml
    import phase_s_prediction_validation as prediction_validation
    from phase_s_protocol import sha256_file

    relative_config = "backtest/configs/b4-full.yaml"
    config_path = root / relative_config
    config_path.parent.mkdir(parents=True)
    config = yaml.safe_load(
        (
            ROOT
            / "backtest/configs/baseline-strategy/b4-s/topk-t22-d2-h2_csi1000_full.yaml"
        ).read_text()
    )
    config_path.write_text(yaml.safe_dump(config))
    model = root / "backtest/models/baselines/b6-m/trained_model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"frozen-model")
    (model.parent / "manifest.json").write_text(
        json.dumps(
            {
                "baseline_exp_id": "baseline/b6-m",
                "retained_model": {
                    "path": str(model.relative_to(root)),
                    "size_bytes": 12,
                    "sha256": sha256_file(model),
                },
                "config": {"path": relative_config},
            }
        )
    )
    frame = pd.DataFrame(
        {"score": [0.1, 0.2, 0.3]},
        index=pd.MultiIndex.from_arrays(
            [
                pd.to_datetime(["2020-01-13", "2020-01-14", "2026-07-31"]),
                ["SH600000"] * 3,
            ],
            names=["datetime", "instrument"],
        ),
    )
    coverage = prediction_validation.prediction_coverage(frame)
    monkeypatch.setattr(prediction_validation, "FULL_PREDICTION_COVERAGE", coverage)
    pred = root / "prediction.pkl"
    frame.to_pickle(pred)
    manifest = (
        root
        / "backtest/experiments/strategy-stability/20260801_full_period/prediction_manifest.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "data_version": "2026-07-31",
                "predictions": [
                    {
                        "model_ref": "b6-m",
                        "pool": "csi1000",
                        "segment": "full",
                        "data_version": "2026-07-31",
                        "model_path": str(model),
                        "model_sha256": sha256_file(model),
                        "path": str(pred),
                        "prediction_sha256": sha256_file(pred),
                        "coverage": coverage,
                    }
                ],
            }
        )
    )
    session = root / "backtest/result/b4-full"
    _write_phase_s_session(session, "800")
    (root / "mlruns/800").mkdir(parents=True, exist_ok=True)
    meta = json.loads((session / "meta.json").read_text())
    meta.update(
        config_path=str(config_path),
        source_pred=str(pred),
        source_pred_sha256=sha256_file(pred),
        backtest={
            "account": 10000000,
            "start_time": "2020-01-13",
            "end_time": "2026-07-31",
            "benchmark": "SH000852",
            "exchange_kwargs": config["backtest"]["exchange_kwargs"],
        },
        strategy={
            "class": "TopkDropoutStrategy",
            "risk_degree": 0.9,
            "topk": 22,
            "n_drop": 2,
            "hold_thresh": 2,
            "only_tradable": False,
            "forbid_all_trade_at_limit": False,
        },
    )
    (session / "meta.json").write_text(json.dumps(meta))
    row = {
        "exp_id": "baseline/b4-s-on-b6-m",
        "phase": "S",
        "direction": "baseline-strategy",
        "state": "baseline",
        "conclusion": "baseline",
        "cleanup_retention_eligible": True,
        "evaluation_mode": "full_history_in_sample",
        "selection_pool": "csi1000",
        "model_ref": "b6-m",
        "frozen_model_ref": "B6 v1.0",
        "selection_segment": ["2020-01-13", "2026-07-31"],
        "test_segment": ["2020-01-13", "2026-07-31"],
        "account": 10000000,
        "configs": [relative_config],
        "config_sha256": sha256_file(config_path),
        "result_dirs": ["backtest/result/b4-full"],
        "strategy": {
            "candidate_id": "topk-t22-d2-h2",
            "strategy_class": "TopkDropoutStrategy",
            "risk_degree": 0.9,
            "topk": 22,
            "n_drop": 2,
            "hold_thresh": 2,
        },
    }
    return row, session


def test_full_period_cleanup_binds_config_model_prediction_and_backtest(
    tmp_path, monkeypatch
):
    baseline = _complete_baseline(tmp_path)
    row, session = _full_period_fixture(tmp_path, monkeypatch)
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, row])
    assert plan["errors"] == []
    assert session in plan["keep_result_dirs"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_pred_sha256", "wrong-sha"),
        ("start_time", "2021-07-16"),
        ("risk_degree", 0.95),
    ],
)
def test_full_period_cleanup_blocks_wrong_prediction_period_or_strategy(
    tmp_path, monkeypatch, field, value
):
    baseline = _complete_baseline(tmp_path)
    row, session = _full_period_fixture(tmp_path, monkeypatch)
    meta = json.loads((session / "meta.json").read_text())
    target = (
        meta["backtest"]
        if field == "start_time"
        else meta["strategy"] if field == "risk_degree" else meta
    )
    target[field] = value
    (session / "meta.json").write_text(json.dumps(meta))
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, row])
    assert any("metadata mismatch" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []


@pytest.mark.parametrize(
    "breakage",
    ["missing", "mismatch", "symlink", "traversal", "failed", "wrong_experiment"],
)
def test_current_bt_recorder_missing_or_unbound_blocks_all_deletion(tmp_path, breakage):
    baseline = _complete_baseline(tmp_path)
    model = _local_phase_m_baseline(tmp_path, "v2", "bt-model")
    session = tmp_path / "backtest/result/current-bt"
    _write_phase_s_session(session, "800")
    recorder = tmp_path / "mlruns/800/rec-800"
    link_path = session / "run_01/mlruns_link.json"
    link = json.loads(link_path.read_text())
    if breakage == "missing":
        (recorder / "meta.yaml").unlink()
        recorder.rmdir()
    elif breakage == "symlink":
        outside = tmp_path / "external-recorder"
        recorder.rename(outside)
        recorder.symlink_to(outside, target_is_directory=True)
    elif breakage in ("mismatch", "traversal"):
        link["backtest_recorder_id"] = (
            "other" if breakage == "mismatch" else "../../outside"
        )
        link_path.write_text(json.dumps(link))
    else:
        (recorder / "meta.yaml").write_text(
            "experiment_id: '999'\nrun_id: rec-800\nstatus: 3\n"
            if breakage == "wrong_experiment"
            else "experiment_id: '800'\nrun_id: rec-800\nstatus: 4\n"
        )
    bt = {
        "exp_id": "baseline/bt",
        "phase": "S",
        "report_kind": "phase_m_v1_bt_baseline",
        "current_bt": True,
        "model_ref": model["exp_id"],
        "seeds": [42, 1000, 2000, 3000, 4000],
        "ensemble_session": "backtest/result/current-bt",
    }
    _write_session(tmp_path / "backtest/result/loser", "999")
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, model, bt])
    assert any("recorder" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []


@pytest.mark.parametrize(
    "breakage",
    [
        "missing_recorder",
        "recorder_mismatch",
        "experiment_mismatch",
        "recorder_symlink",
        "missing_model",
        "empty_model",
        "model_symlink",
        "failed_status",
    ],
)
def test_train_recorder_or_model_missing_or_unbound_blocks_cleanup(tmp_path, breakage):
    baseline = _complete_baseline(tmp_path)
    session = tmp_path / baseline["result_dirs"][0]
    recorder = tmp_path / "mlruns/100/train-rec-100"
    model = recorder / "artifacts/trained_model"
    link_path = session / "run_01/mlruns_link.json"
    if breakage in ("missing_recorder", "recorder_symlink"):
        outside = tmp_path / "removed-recorder"
        recorder.rename(outside)
        if breakage == "recorder_symlink":
            recorder.symlink_to(outside, target_is_directory=True)
    elif breakage in ("recorder_mismatch", "experiment_mismatch"):
        link = json.loads(link_path.read_text())
        link[
            (
                "train_recorder_id"
                if breakage == "recorder_mismatch"
                else "train_experiment_id"
            )
        ] = "other"
        link_path.write_text(json.dumps(link))
    elif breakage == "failed_status":
        (recorder / "meta.yaml").write_text(
            "experiment_id: '100'\nrun_id: train-rec-100\nstatus: 4\nlifecycle_stage: active\n"
        )
    elif breakage == "empty_model":
        model.write_bytes(b"")
    else:
        model.unlink()
        if breakage == "model_symlink":
            outside = tmp_path / "external-model"
            outside.write_bytes(b"model")
            model.symlink_to(outside)
    _write_session(tmp_path / "backtest/result/loser", "999")
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline])
    assert any("train" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []


def test_missing_second_rolling_fold_model_blocks_cleanup(tmp_path):
    baseline = _complete_baseline(tmp_path)
    rolling = _row(
        "train-schedule/rolling",
        metrics=_metrics([0.03, 0.04, 0.05], [0.3, 0.4, 0.5]),
        result_dirs=[],
    )
    for index in range(5):
        relative = f"backtest/result/rolling-{index}"
        _write_rolling_session(
            tmp_path / relative, [str(200 + index * 2), str(201 + index * 2)]
        )
        rolling["result_dirs"].append(relative)
    (tmp_path / "mlruns/201/train-rec-201/artifacts/trained_model").unlink()
    plan = cleanup.build_cleanup_plan(tmp_path, [baseline, rolling])
    assert any("train" in error for error in plan["errors"])
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []
