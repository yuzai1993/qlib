from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from backtest.scripts import compare_b5_post2020_forward as compare


SEEDS = [42, 1000, 2000, 3000, 4000]
POOLS = ["csi1000", "csi300", "csi500"]
METRICS = ["ic_mean", "icir", "rank_ic_mean", "rank_icir"]
PROTOCOL_ID = "post2020-forward-v1"
LABEL = "Ref($close, -2)/Ref($close, -1) - 1"
TEST_SEGMENT = ["2024-07-01", "2026-07-16"]


def _manifest() -> dict:
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "frozen_before_training": True,
        "data_version_at_freeze": "2026-07-31",
        "seeds": SEEDS,
        "test_pools": POOLS,
        "eval_label": LABEL,
        "groups": {
            "rankic-winner-stale": {"role": "same-window-control"},
            "rankic-winner-post2020": {"role": "treatment"},
        },
        "common_test_segment": TEST_SEGMENT,
        "conclusion_policy": {
            "improve": "all three pool RankIC means strictly improve",
            "regress": "csi1000 RankIC does not improve",
            "inconclusive": "csi1000 improves but a transfer pool does not",
        },
    }


def _seed_rows(rank_ic: float, *, expanded: bool, pool: str) -> dict:
    centered = [-0.002, -0.001, 0.0, 0.001, 0.002]
    paired_delta = [0.001, -0.002, 0.002, -0.0015, 0.0005]
    rows = {}
    for index, (seed, offset) in enumerate(zip(SEEDS, centered, strict=True)):
        delta = paired_delta[index] if expanded and pool == "csi1000" else 0.0
        value = rank_ic + offset + delta
        rows[str(seed)] = {
            "n_days": 500,
            "ic_mean": value - 0.004,
            "icir": value * 8.0,
            "rank_ic_mean": value,
            "rank_icir": value * 10.0,
            "yearly": {
                "2024": {"n_days": 125, "rank_ic_mean": value - 0.003},
                "2025": {"n_days": 242, "rank_ic_mean": value},
                "2026": {"n_days": 133, "rank_ic_mean": value + 0.003},
            },
        }
    return rows


def _result(group: str, *, expanded: bool) -> dict:
    bases = {"csi1000": 0.030, "csi300": 0.020, "csi500": 0.010}
    pools = {}
    for pool, base in bases.items():
        rank_ic = base + (0.001 if expanded else 0.0)
        rows = _seed_rows(rank_ic, expanded=expanded, pool=pool)
        pools[pool] = {
            "seeds": rows,
            "seed_mean": {
                metric: sum(row[metric] for row in rows.values()) / len(rows)
                for metric in METRICS
            },
        }
    return {
        "protocol_id": PROTOCOL_ID,
        "experiment_group": group,
        "eval_label": LABEL,
        "eval_label_role": "fixed_1d",
        "eval_segment_name": "test",
        "eval_segment": TEST_SEGMENT,
        "effective_eval_segment": TEST_SEGMENT,
        "test_segment": TEST_SEGMENT,
        "sessions": [
            {"session": f"session-{group}-{seed}", "seed": seed} for seed in SEEDS
        ],
        "data_version": "2026-07-31",
        "pools": pools,
    }


def _write_inputs(tmp_path: Path, manifest: dict, control: dict, expanded: dict):
    paths = {
        "manifest": tmp_path / "manifest.json",
        "control": tmp_path / "control.json",
        "expanded": tmp_path / "expanded.json",
        "output": tmp_path / "combined.json",
    }
    for key in ("manifest", "control", "expanded"):
        paths[key].write_text(
            json.dumps(locals()[key], allow_nan=True), encoding="utf-8"
        )
    return paths


def _compare(tmp_path: Path, manifest: dict, control: dict, expanded: dict) -> dict:
    paths = _write_inputs(tmp_path, manifest, control, expanded)
    return compare.compare_results(
        manifest_path=paths["manifest"],
        control_path=paths["control"],
        expanded_path=paths["expanded"],
        output_path=paths["output"],
    )


def _shift_rank_ic(result: dict, pool: str, delta: float) -> None:
    rows = result["pools"][pool]["seeds"]
    for row in rows.values():
        row["rank_ic_mean"] += delta
    result["pools"][pool]["seed_mean"]["rank_ic_mean"] = sum(
        row["rank_ic_mean"] for row in rows.values()
    ) / len(rows)


def test_compare_writes_combined_deltas_pairwise_yearly_and_improve(tmp_path):
    paths = _write_inputs(
        tmp_path,
        _manifest(),
        _result("rankic-winner-stale", expanded=False),
        _result("rankic-winner-post2020", expanded=True),
    )

    result = compare.compare_results(
        manifest_path=paths["manifest"],
        control_path=paths["control"],
        expanded_path=paths["expanded"],
        output_path=paths["output"],
    )

    assert result["protocol_id"] == PROTOCOL_ID
    assert result["groups"] == {
        "control": "rankic-winner-stale",
        "expanded": "rankic-winner-post2020",
    }
    assert result["conclusion"] == "improve"
    assert result["metric_deltas"]["csi1000"]["rank_ic_mean"] == pytest.approx(0.001)
    assert set(result["metric_deltas"]["csi300"]) == set(METRICS)
    assert result["csi1000_same_seed_rankic"] == {
        "n": 5,
        "wins": 3,
        "diff_mean": pytest.approx(0.001),
        "seeds": SEEDS,
        "diffs": pytest.approx([0.002, -0.001, 0.003, -0.0005, 0.0015]),
    }
    assert result["yearly_rankic_delta"]["2025"]["csi500"] == pytest.approx(0.001)
    assert result["sources"]["manifest"]["sha256"]
    assert json.loads(paths["output"].read_text(encoding="utf-8")) == result


@pytest.mark.parametrize(
    ("csi1000_shift", "transfer_shift", "expected"),
    [
        (-0.002, 0.0, "regress"),
        (0.0, -0.002, "inconclusive"),
    ],
)
def test_conclusion_uses_preregistered_strict_boundaries(
    tmp_path, csi1000_shift, transfer_shift, expected
):
    control = _result("rankic-winner-stale", expanded=False)
    expanded = _result("rankic-winner-post2020", expanded=True)
    _shift_rank_ic(expanded, "csi1000", csi1000_shift)
    _shift_rank_ic(expanded, "csi300", transfer_shift)

    result = _compare(tmp_path, _manifest(), control, expanded)

    assert result["conclusion"] == expected


def test_session_order_does_not_change_the_fixed_five_seed_protocol(tmp_path):
    control = _result("rankic-winner-stale", expanded=False)
    expanded = _result("rankic-winner-post2020", expanded=True)
    control["sessions"].reverse()
    expanded["sessions"] = expanded["sessions"][2:] + expanded["sessions"][:2]

    result = _compare(tmp_path, _manifest(), control, expanded)

    assert result["seeds"] == SEEDS


@pytest.mark.parametrize(
    ("target", "mutation", "match"),
    [
        ("manifest", lambda d: d.update(frozen_before_training=False), "frozen"),
        ("control", lambda d: d.update(protocol_id="other"), "protocol"),
        ("expanded", lambda d: d.update(eval_segment_name="valid"), "test"),
        (
            "expanded",
            lambda d: d.update(eval_segment=["2024-07-02", "2026-07-16"]),
            "segment",
        ),
        (
            "expanded",
            lambda d: d.update(effective_eval_segment=["2024-07-01", "2026-07-15"]),
            "segment",
        ),
        (
            "expanded",
            lambda d: d.update(test_segment=["2024-07-01", "2026-07-15"]),
            "segment",
        ),
        ("expanded", lambda d: d.update(eval_label_role="self"), "label"),
        ("expanded", lambda d: d.update(eval_label="other"), "label"),
        ("expanded", lambda d: d.update(data_version="2026-07-30"), "data_version"),
        ("expanded", lambda d: d["pools"].pop("csi500"), "pool"),
        ("expanded", lambda d: d["sessions"].pop(), "seed"),
        (
            "expanded",
            lambda d: d["pools"]["csi1000"]["seeds"].pop("42"),
            "seed",
        ),
        (
            "expanded",
            lambda d: d["pools"]["csi1000"]["seed_mean"].update(
                rank_ic_mean=float("nan")
            ),
            "finite",
        ),
        (
            "expanded",
            lambda d: d["pools"]["csi1000"]["seed_mean"].update(rank_ic_mean=999.0),
            "seed_mean",
        ),
        (
            "expanded",
            lambda d: d["pools"]["csi1000"]["seeds"]["42"]["yearly"].pop("2026"),
            "year",
        ),
    ],
)
def test_compare_fails_closed_on_protocol_or_aggregate_drift(
    tmp_path, target, mutation, match
):
    documents = {
        "manifest": _manifest(),
        "control": _result("rankic-winner-stale", expanded=False),
        "expanded": _result("rankic-winner-post2020", expanded=True),
    }
    mutation(documents[target])

    with pytest.raises(ValueError, match=match):
        _compare(tmp_path, **documents)


def test_protocol_can_be_proven_by_frozen_config_when_result_has_no_field(tmp_path):
    import hashlib

    manifest = _manifest()
    documents = {
        "control": _result("rankic-winner-stale", expanded=False),
        "expanded": _result("rankic-winner-post2020", expanded=True),
    }
    manifest["config_hashes"] = {}
    for role, group in (
        ("control", "rankic-winner-stale"),
        ("expanded", "rankic-winner-post2020"),
    ):
        config_dir = tmp_path / group
        config_dir.mkdir()
        config = config_dir / f"tr_{group}_s42.yaml"
        config.write_text(
            "model:\n  kwargs:\n    seed: 42\n" f"    protocol_id: {PROTOCOL_ID}\n",
            encoding="utf-8",
        )
        manifest["config_hashes"][group] = {
            "42": hashlib.sha256(config.read_bytes()).hexdigest()
        }
        documents[role].pop("protocol_id")
        documents[role].pop("experiment_group")
        documents[role]["config"] = str(config)

    result = _compare(tmp_path, manifest, **documents)

    assert result["protocol_id"] == PROTOCOL_ID


def test_existing_output_is_rejected_before_reading_inputs(tmp_path):
    output = tmp_path / "combined.json"
    output.write_text("sentinel", encoding="utf-8")

    with pytest.raises(FileExistsError, match="overwrite"):
        compare.compare_results(
            manifest_path=tmp_path / "missing-manifest.json",
            control_path=tmp_path / "missing-control.json",
            expanded_path=tmp_path / "missing-expanded.json",
            output_path=output,
        )

    assert output.read_text(encoding="utf-8") == "sentinel"


def test_cli_writes_finite_json(tmp_path):
    paths = _write_inputs(
        tmp_path,
        _manifest(),
        _result("rankic-winner-stale", expanded=False),
        _result("rankic-winner-post2020", expanded=True),
    )

    compare.main(
        [
            "--manifest",
            str(paths["manifest"]),
            "--control",
            str(paths["control"]),
            "--expanded",
            str(paths["expanded"]),
            "--output",
            str(paths["output"]),
        ]
    )

    result = json.loads(paths["output"].read_text(encoding="utf-8"))
    assert result["conclusion"] == "improve"
    assert all(
        math.isfinite(value)
        for metrics in result["metric_deltas"].values()
        for value in metrics.values()
    )
