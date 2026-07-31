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


def _write_session(path: Path, experiment_id: str):
    path.mkdir(parents=True)
    (path / "meta.json").write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "status": "success",
                        "train_experiment_id": experiment_id,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


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
                    }
                    for fold, experiment_id in zip(folds, experiment_ids)
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_phase_s_session(path: Path, experiment_id: str):
    path.mkdir(parents=True)
    (path / "meta.json").write_text(
        json.dumps(
            {
                "mode": "pred_backtest",
                "runs": [{"status": "success", "backtest_recorder_id": f"rec-{experiment_id}"}],
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
        "test_results": {
            pool: [
                {"candidate_id": baseline, "result_dir": f"backtest/result/{next(iterator)}"},
                {"candidate_id": winner, "result_dir": f"backtest/result/{next(iterator)}"},
            ]
            for pool in ("csi1000", "csi300", "csi500")
        }
        if state == "test_complete"
        else {},
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
        (mlruns_root / exp_id).mkdir(parents=True)
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
        (mlruns_root / str(index)).mkdir(parents=True)
    rolling_ids = []
    for index, session in enumerate(rolling_dirs, start=201):
        fold_ids = [str(index * 10 + 1), str(index * 10 + 2)]
        rolling_ids.extend(fold_ids)
        _write_rolling_session(session, fold_ids)
        for experiment_id in fold_ids:
            (mlruns_root / experiment_id).mkdir(parents=True)

    baseline = _baseline()
    baseline["result_dirs"] = [
        f"backtest/result/base-{index}" for index in range(5)
    ]
    rolling = _row(
        "train-schedule/expanding-annual",
        metrics=_metrics([0.021, 0.031, 0.041], [0.21, 0.31, 0.41]),
        result_dirs=[
            f"backtest/result/rolling-{index}" for index in range(5)
        ],
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


def test_phase_s_retains_baseline_and_frozen_winner_for_every_test_pool(tmp_path):
    result_root = tmp_path / "backtest/result"
    mlruns_root = tmp_path / "mlruns"
    baseline = _baseline()
    baseline["result_dirs"] = []
    for index in range(5):
        name = f"phase-m-{index}"
        experiment_id = str(100 + index)
        _write_session(result_root / name, experiment_id)
        (mlruns_root / experiment_id).mkdir(parents=True)
        baseline["result_dirs"].append(f"backtest/result/{name}")

    phase_s_names = [f"phase-s-{index}" for index in range(6)]
    for index, name in enumerate(phase_s_names, start=200):
        _write_phase_s_session(result_root / name, str(index))
        (mlruns_root / str(index)).mkdir(parents=True)
    _write_phase_s_session(result_root / "loser", "999")
    (mlruns_root / "999").mkdir(parents=True)

    plan = cleanup.build_cleanup_plan(
        tmp_path,
        [baseline, _phase_s_row("b1-m", phase_s_names)],
    )

    assert plan["errors"] == []
    assert set(plan["keep_result_dirs"]) == {
        (result_root / f"phase-m-{index}").resolve() for index in range(5)
    } | {(result_root / name).resolve() for name in phase_s_names}
    assert plan["delete_result_dirs"] == [(result_root / "loser").resolve()]
    assert set(plan["keep_mlruns_dirs"]) == {
        (mlruns_root / str(index)).resolve() for index in range(100, 105)
    } | {(mlruns_root / str(index)).resolve() for index in range(200, 206)}
    assert plan["delete_mlruns_dirs"] == [(mlruns_root / "999").resolve()]


def test_incomplete_phase_s_bundle_blocks_all_deletion(tmp_path):
    result_root = tmp_path / "backtest/result"
    mlruns_root = tmp_path / "mlruns"
    baseline = _baseline()
    baseline["result_dirs"] = []
    for index in range(5):
        name = f"phase-m-{index}"
        experiment_id = str(100 + index)
        _write_session(result_root / name, experiment_id)
        (mlruns_root / experiment_id).mkdir(parents=True)
        baseline["result_dirs"].append(f"backtest/result/{name}")
    _write_phase_s_session(result_root / "loser", "999")
    (mlruns_root / "999").mkdir(parents=True)

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
    (mlruns_root / "303").mkdir(parents=True)

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
        (mlruns_root / experiment_id).mkdir(parents=True)
        baseline["result_dirs"].append(f"backtest/result/base-{i}")
    _write_session(result_root / "loser", "303")
    (mlruns_root / "303").mkdir()
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
        (mlruns_root / experiment_id).mkdir(parents=True)
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
