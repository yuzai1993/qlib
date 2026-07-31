from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import register_b5_followups as register  # noqa: E402


def test_upsert_registry_row_appends_then_replaces_without_duplicate(tmp_path):
    registry = tmp_path / "registry.jsonl"
    registry.write_text(
        json.dumps({"exp_id": "baseline/b5-m", "value": 1}) + "\n",
        encoding="utf-8",
    )

    register.upsert_registry_row(
        registry,
        {"exp_id": "train-schedule/expanding-annual", "value": 2},
    )
    register.upsert_registry_row(
        registry,
        {"exp_id": "train-schedule/expanding-annual", "value": 3},
    )

    rows = [
        json.loads(line)
        for line in registry.read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [
        {"exp_id": "baseline/b5-m", "value": 1},
        {"exp_id": "train-schedule/expanding-annual", "value": 3},
    ]


def test_pending_rows_lock_b5_seed_pool_and_treatment():
    rows = {row["exp_id"]: row for row in register.pending_rows()}

    vol = rows["label-risk-adjustment/vol20-scaled"]
    assert vol["baseline_ref"] == "B5 v1.0"
    assert vol["seeds"] == [42, 1000, 2000, 3000, 4000]
    assert vol["test_pools"] == ["csi1000", "csi300", "csi500"]
    assert vol["volatility_window"] == 20
    assert vol["volatility_floor"] == 0.005
    assert vol["conclusion"] == "pending"

    rolling = rows["train-schedule/expanding-annual"]
    assert rolling["baseline_ref"] == "B5 v1.0"
    assert rolling["rolling_type"] == "expanding"
    assert rolling["rolling_step_trading_days"] == 252
    assert rolling["purge_trading_days"] == 41
    assert rolling["conclusion"] == "pending"

    rolling_es5 = rows["train-schedule/expanding-annual-es5"]
    assert rolling_es5["baseline_ref"] == "B5 v1.0"
    assert rolling_es5["control_ref"] == "train-schedule/expanding-annual"
    assert rolling_es5["rolling_type"] == "expanding"
    assert rolling_es5["rolling_step_trading_days"] == 252
    assert rolling_es5["max_boosting_rounds"] == 28
    assert rolling_es5["early_stopping_rounds"] == 5
    assert rolling_es5["conclusion"] == "pending"


def test_early_stopping_assessment_requires_mean_and_seed_consistency():
    control = {
        "pools": {
            "csi1000": {
                "seeds": {
                    "42": {"rank_ic_mean": 0.040},
                    "1000": {"rank_ic_mean": 0.050},
                    "2000": {"rank_ic_mean": 0.060},
                    "3000": {"rank_ic_mean": 0.070},
                    "4000": {"rank_ic_mean": 0.080},
                },
                "seed_mean": {"rank_ic_mean": 0.060},
            }
        }
    }

    def candidate(values, triggered_count):
        return {
            "pools": {
                "csi1000": {
                    "seeds": {
                        str(seed): {"rank_ic_mean": value}
                        for seed, value in zip(register.SEEDS, values)
                    },
                    "seed_mean": {
                        "rank_ic_mean": sum(values) / len(values),
                    },
                }
            },
            "rolling": {
                "model_diagnostics": {
                    "triggered_count": triggered_count,
                    "booster_count": 75,
                }
            },
        }

    improved = candidate([0.041, 0.051, 0.061, 0.069, 0.079], 25)
    regressed = candidate([0.039, 0.049, 0.059, 0.069, 0.079], 25)
    never_triggered = candidate([0.040, 0.050, 0.060, 0.070, 0.080], 0)

    assert register._early_stopping_assessment(improved, control)["verdict"] == (
        "improve"
    )
    assert register._early_stopping_assessment(regressed, control)["verdict"] == (
        "not_beneficial"
    )
    assert register._early_stopping_assessment(
        never_triggered, control
    )["verdict"] == "redundant_at_current_round_cap"


def test_finalize_es5_records_direct_control_and_model_diagnostics(
    tmp_path, monkeypatch
):
    def evaluation(rank_ic, *, diagnostics=None):
        pools = {}
        for pool in register.POOLS:
            pools[pool] = {
                "seeds": {
                    str(seed): {
                        "rank_ic_mean": rank_ic + index * 0.0001,
                    }
                    for index, seed in enumerate(register.SEEDS)
                },
                "seed_mean": {
                    "ic_mean": 0.01,
                    "icir": 0.10,
                    "rank_ic_mean": rank_ic,
                    "rank_icir": 0.30,
                },
            }
        output = {
            "data_version": "2026-07-27",
            "pools": pools,
        }
        if diagnostics is not None:
            output["rolling"] = {
                "folds": [{"fold": 1}],
                "fold_count": 1,
                "model_diagnostics": diagnostics,
            }
        return output

    candidate = evaluation(
        0.049,
        diagnostics={
            "max_rounds": 28,
            "early_stopping_rounds": 5,
            "best_iterations": {"42": {"1": [17, 28, 9]}},
            "booster_count": 75,
            "triggered_count": 20,
            "trigger_rate": 20 / 75,
            "mean_best_iteration": 23.0,
            "min_best_iteration": 9,
            "max_best_iteration": 28,
        },
    )
    baseline = evaluation(0.050)
    control = evaluation(0.048)
    ic_dir = tmp_path / "ic"
    ic_dir.mkdir()
    (ic_dir / "ts_expanding_annual_es5_test_1d.json").write_text(
        json.dumps(candidate),
        encoding="utf-8",
    )
    baseline_path = ic_dir / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    (ic_dir / "ts_expanding_annual_test_1d.json").write_text(
        json.dumps(control),
        encoding="utf-8",
    )
    monkeypatch.setattr(register, "IC_DIR", ic_dir)
    monkeypatch.setattr(register, "BASELINE_IC", baseline_path)
    monkeypatch.setattr(
        register,
        "BASELINE_YEARLY_IC",
        ic_dir / "missing-yearly.json",
    )
    monkeypatch.setattr(
        register,
        "_result_sessions",
        lambda _prefix: ["backtest/result/es5"],
    )
    pending = {
        row["exp_id"]: row for row in register.pending_rows()
    }["train-schedule/expanding-annual-es5"]

    row = register.finalize_row(
        pending,
        register.EXPERIMENTS["rolling_es5"],
    )

    assert row["early_stopping_assessment"]["verdict"] == "improve"
    assert row["early_stopping_assessment"]["csi1000_rank_ic_delta"] == (
        pytest.approx(0.001)
    )
    assert row["model_diagnostics"] == candidate["rolling"][
        "model_diagnostics"
    ]
    assert row["control_ref"] == "train-schedule/expanding-annual"


def test_yearly_rank_ic_delta_averages_seed_level_years():
    def doc(values):
        return {
            "pools": {
                pool: {
                    "seeds": {
                        "42": {
                            "yearly": {
                                "2025": {"rank_ic_mean": values[pool][0]},
                            }
                        },
                        "1000": {
                            "yearly": {
                                "2025": {"rank_ic_mean": values[pool][1]},
                            }
                        },
                    }
                }
                for pool in register.POOLS
            }
        }

    baseline = doc(
        {
            "csi1000": [0.04, 0.06],
            "csi300": [0.02, 0.04],
            "csi500": [0.03, 0.05],
        }
    )
    candidate = doc(
        {
            "csi1000": [0.05, 0.07],
            "csi300": [0.01, 0.03],
            "csi500": [0.05, 0.07],
        }
    )

    assert register._yearly_rank_ic_delta(candidate, baseline) == {
        "2025": {
            "csi1000": pytest.approx(0.01),
            "csi300": pytest.approx(-0.01),
            "csi500": pytest.approx(0.02),
        }
    }
