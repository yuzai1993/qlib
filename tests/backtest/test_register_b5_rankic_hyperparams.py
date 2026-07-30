from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_experiment_report as report  # noqa: E402
import register_b5_rankic_hyperparams as register  # noqa: E402


SEEDS = [42, 1000, 2000, 3000, 4000]
POOLS = ["csi1000", "csi300", "csi500"]
METRICS = ["ic_mean", "icir", "rank_ic_mean", "rank_icir"]


def _seed_metrics(rank_ic: float, seed_index: int) -> dict:
    return {
        "n_days": 100,
        "ic_mean": 0.010 + seed_index * 0.0001,
        "ic_std": 0.1,
        "icir": 0.100 + seed_index * 0.001,
        "rank_ic_mean": rank_ic + seed_index * 0.0001,
        "rank_ic_std": 0.2,
        "rank_icir": 0.200 + seed_index * 0.001,
    }


def _pool(rank_ic: float) -> dict:
    seeds = {
        str(seed): _seed_metrics(rank_ic, index)
        for index, seed in enumerate(SEEDS)
    }
    return {
        "seeds": seeds,
        "seed_mean": {
            metric: sum(row[metric] for row in seeds.values()) / len(SEEDS)
            for metric in METRICS
        },
    }


def _baseline_row(values: dict[str, float] | None = None) -> dict:
    values = values or {"csi1000": 0.05, "csi300": 0.04, "csi500": 0.045}
    return {
        "exp_id": "baseline/b5-m",
        "direction": "baseline",
        "phase": "M",
        "date": "2026-07-27",
        "hypothesis": "B5",
        "baseline_ref": "B5 v1.0",
        "seeds": SEEDS,
        "test_pools": POOLS,
        "data_version": "2026-07-30",
        "metrics_summary": {
            pool: _pool(value)["seed_mean"] for pool, value in values.items()
        },
        "conclusion": "baseline",
    }


def _baseline_result(values: dict[str, float] | None = None) -> dict:
    values = values or {"csi1000": 0.05, "csi300": 0.04, "csi500": 0.045}
    return {
        "generated_at": "2026-07-27T01:59:47",
        "eval_label": register.EVAL_LABEL_EXPR,
        "eval_label_role": "fixed_1d",
        "eval_segment_name": "test",
        "eval_segment": list(register.TEST_SEGMENT),
        "effective_eval_segment": list(register.TEST_SEGMENT),
        "test_segment": list(register.TEST_SEGMENT),
        "sessions": [
            {"session": f"baseline-s{seed}", "seed": seed} for seed in SEEDS
        ],
        "data_version": "2026-07-30",
        "pools": {pool: _pool(values[pool]) for pool in ("csi300", "csi500", "csi1000")},
    }


def _manifest() -> dict:
    candidates = {}
    config_hashes = []
    valid_hashes = {}
    for candidate_index, candidate in enumerate(register.CANDIDATES):
        sessions = [
            {"session": f"/tmp/{candidate}-s{seed}", "seed": seed}
            for seed in SEEDS
        ]
        candidates[candidate] = {
            "rank_ic_mean": 0.01 + candidate_index * 0.001,
            "rank_icir": 0.10 + candidate_index * 0.01,
            "data_version": "2026-07-30",
            "seeds": SEEDS,
            "sessions": sessions,
        }
        valid_hashes[candidate] = {
            "path": f"/tmp/{candidate}-valid.json",
            "sha256": f"valid-{candidate}",
        }
        for seed in SEEDS:
            config_hashes.append(
                {
                    "candidate": candidate,
                    "seed": seed,
                    "path": f"/tmp/{candidate}-s{seed}.yaml",
                    "sha256": f"config-{candidate}-{seed}",
                }
            )
    winner = register.CANDIDATES[-1]
    return {
        "schema_version": 1,
        "generated_at": "2026-07-31T01:00:00",
        "frozen_before_test": True,
        "test_metrics_opened": False,
        "selection_segment": "valid",
        "selection_metric": "csi1000.valid.rank_ic_mean",
        "tie_breaker": ["rank_icir", "candidate_id"],
        "eval_label": register.EVAL_LABEL_EXPR,
        "eval_label_role": "fixed_1d",
        "min_count": 20,
        "data_version": "2026-07-30",
        "official_valid_segment": list(register.VALID_SEGMENT),
        "effective_valid_segment": list(register.SAFE_VALID_SEGMENT),
        "seeds": SEEDS,
        "candidate_order": list(reversed(register.CANDIDATES)),
        "candidates": candidates,
        "selected_candidate": winner,
        "selected_seeds": SEEDS,
        "selected_sessions": candidates[winner]["sessions"],
        "valid_result_hashes": valid_hashes,
        "config_hashes": config_hashes,
    }


def _test_result(values: dict[str, float] | None = None) -> dict:
    values = values or {"csi1000": 0.06, "csi300": 0.05, "csi500": 0.055}
    manifest = _manifest()
    return {
        "generated_at": "2026-07-31T02:00:00",
        "config": "/tmp/rankic-es-leaves128-s42.yaml",
        "eval_label": register.EVAL_LABEL_EXPR,
        "eval_label_role": "fixed_1d",
        "eval_segment_name": "test",
        "eval_segment": list(register.TEST_SEGMENT),
        "effective_eval_segment": list(register.TEST_SEGMENT),
        "test_segment": list(register.TEST_SEGMENT),
        "sessions": manifest["selected_sessions"],
        "data_version": "2026-07-30",
        "pools": {pool: _pool(values[pool]) for pool in POOLS},
        "min_count": 20,
        "selected_candidate": manifest["selected_candidate"],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _setup_final_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    values: dict[str, float] | None = None,
) -> tuple[Path, Path, Path, Path]:
    registry_path = tmp_path / "registry.jsonl"
    baseline_path = tmp_path / "baseline.json"
    manifest_path = tmp_path / "selection.json"
    test_path = tmp_path / "test.json"
    pending = register.build_pending_row("2026-07-30")
    registry_path.write_text(
        json.dumps(_baseline_row(), ensure_ascii=False)
        + "\n"
        + json.dumps(pending, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    manifest = _manifest()
    test_result = _test_result(values)
    _write_json(baseline_path, _baseline_result())
    _write_json(manifest_path, manifest)
    test_result["selection_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    _write_json(test_path, test_result)
    monkeypatch.setattr(register, "verify_manifest", lambda value: value)
    monkeypatch.setattr(register, "validate_test_result", lambda result, frozen: None)
    return registry_path, baseline_path, manifest_path, test_path


def _finalize(
    registry_path: Path,
    baseline_path: Path,
    manifest_path: Path,
    test_path: Path,
) -> dict:
    return register.finalize_registry(
        registry_path=registry_path,
        manifest_path=manifest_path,
        test_result_path=test_path,
        baseline_result_path=baseline_path,
    )


def test_pending_row_freezes_the_complete_valid_only_search_protocol():
    row = register.build_pending_row("2026-07-30")

    assert row["exp_id"] == "model-hyperparam/valid-rankic-search-v1"
    assert row["baseline_ref"] == "B5 v1.0"
    assert row["seeds"] == SEEDS
    assert row["train_pool"] == "csi1000"
    assert row["label_horizon"] == 40
    assert row["learn_processors"] == ["DropnaLabel", "CSRankNorm(label)"]
    assert row["selection_candidates"] == list(register.CANDIDATES)
    assert row["config_count"] == 20
    assert row["selection_segment"] == "valid"
    assert row["selection_official_segment"] == ["2020-01-13", "2021-07-15"]
    assert row["selection_effective_segment"] == ["2020-01-13", "2021-07-13"]
    assert row["selection_label_role"] == "fixed_1d"
    assert row["selection_min_count"] == 20
    assert row["selection_metric"] == "csi1000.valid.rank_ic_mean"
    assert row["selection_tie_breaker"] == ["rank_icir", "candidate_id"]
    assert row["test_pools"] == POOLS
    assert row["test_policy"] == "freeze_valid_winner_then_test_once"
    assert row["data_version"] == "2026-07-30"
    assert row["conclusion"] == "pending"
    assert "四个固定候选" in row["hypothesis"]
    assert "CSI1000 valid RankIC" in row["hypothesis"]


@pytest.mark.parametrize("value", ["", "2026-7-30", "2026-02-30", "not-a-date"])
def test_pending_rejects_missing_or_noncanonical_data_version(value: str):
    with pytest.raises(ValueError, match="data_version"):
        register.build_pending_row(value)


def test_pending_append_preserves_every_existing_byte_and_rejects_duplicate(
    tmp_path: Path,
):
    registry_path = tmp_path / "registry.jsonl"
    original = (
        b'  {"exp_id":"baseline/b5-m","conclusion":"baseline"}  \r\n'
        b"\n"
        b' { "exp_id": "bystander", "note": "\\u4fdd\\u7559" }'
    )
    registry_path.write_bytes(original)

    row = register.register_pending(registry_path, "2026-07-30")

    updated = registry_path.read_bytes()
    assert updated.startswith(original + b"\n")
    assert json.loads(updated.splitlines()[-1]) == row
    before_duplicate = updated
    with pytest.raises(ValueError, match="already exists"):
        register.register_pending(registry_path, "2026-07-30")
    assert registry_path.read_bytes() == before_duplicate


@pytest.mark.parametrize("state", ["missing", "duplicate", "already-final"])
def test_final_requires_exactly_one_pending_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
):
    registry_path, baseline_path, manifest_path, test_path = _setup_final_files(
        tmp_path, monkeypatch
    )
    rows = [
        json.loads(line)
        for line in registry_path.read_text(encoding="utf-8").splitlines()
    ]
    if state == "missing":
        rows = rows[:1]
    elif state == "duplicate":
        rows.append(deepcopy(rows[-1]))
    else:
        rows[-1]["conclusion"] = "regress"
    registry_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    original = registry_path.read_bytes()

    with pytest.raises(ValueError, match="pending"):
        _finalize(registry_path, baseline_path, manifest_path, test_path)
    assert registry_path.read_bytes() == original


def test_final_replaces_only_pending_line_and_preserves_bystander_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    registry_path, baseline_path, manifest_path, test_path = _setup_final_files(
        tmp_path, monkeypatch
    )
    baseline_raw = json.dumps(_baseline_row(), ensure_ascii=False).encode()
    pending_raw = json.dumps(
        register.build_pending_row("2026-07-30"), ensure_ascii=False
    ).encode()
    prefix = b" \t" + baseline_raw + b"  \r\n"
    suffix = b'  {"exp_id":"bystander","x":1} \n'
    registry_path.write_bytes(prefix + pending_raw + b"\r\n" + suffix)

    row = _finalize(registry_path, baseline_path, manifest_path, test_path)

    raw = registry_path.read_bytes()
    assert raw.startswith(prefix)
    assert raw.endswith(suffix)
    assert raw.count(register.EXP_ID.encode()) == 1
    assert row["conclusion"] == "improve"
    stored = next(
        json.loads(line)
        for line in raw.splitlines()
        if register.EXP_ID.encode() in line
    )
    assert stored == row


def test_final_rejects_manifest_hash_tampering_without_changing_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    registry_path, baseline_path, manifest_path, test_path = _setup_final_files(
        tmp_path, monkeypatch
    )
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
    original = registry_path.read_bytes()

    with pytest.raises(ValueError, match="manifest SHA-256"):
        _finalize(registry_path, baseline_path, manifest_path, test_path)
    assert registry_path.read_bytes() == original


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda m, t: m["config_hashes"].pop(), "20 config"),
        (lambda m, t: m["valid_result_hashes"].pop(register.CANDIDATES[0]), "four valid"),
        (lambda m, t: m["selected_sessions"].pop(), "five selected"),
        (lambda m, t: t.__setitem__("data_version", "2026-07-29"), "data_version"),
        (lambda m, t: t["sessions"].reverse(), "selected sessions"),
        (lambda m, t: t["pools"]["csi1000"]["seeds"].pop("42"), "five seeds"),
        (
            lambda m, t: t.__setitem__("generated_at", "2026-07-30T23:59:59"),
            "precede",
        ),
    ],
)
def test_final_rejects_incomplete_or_mismatched_frozen_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    message: str,
):
    registry_path, baseline_path, manifest_path, test_path = _setup_final_files(
        tmp_path, monkeypatch
    )
    manifest = json.loads(manifest_path.read_text())
    test_result = json.loads(test_path.read_text())
    mutation(manifest, test_result)
    _write_json(manifest_path, manifest)
    test_result["selection_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    _write_json(test_path, test_result)
    original = registry_path.read_bytes()

    with pytest.raises(ValueError, match=message):
        _finalize(registry_path, baseline_path, manifest_path, test_path)
    assert registry_path.read_bytes() == original


def test_final_rejects_baseline_anchor_or_pairwise_seed_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    registry_path, baseline_path, manifest_path, test_path = _setup_final_files(
        tmp_path, monkeypatch
    )
    baseline = json.loads(baseline_path.read_text())
    baseline["pools"]["csi1000"]["seed_mean"]["rank_ic_mean"] += 0.01
    _write_json(baseline_path, baseline)
    with pytest.raises(ValueError, match="B5 registry anchor"):
        _finalize(registry_path, baseline_path, manifest_path, test_path)

    _write_json(baseline_path, _baseline_result())
    baseline = json.loads(baseline_path.read_text())
    baseline["pools"]["csi1000"]["seeds"].pop("42")
    _write_json(baseline_path, baseline)
    with pytest.raises(ValueError, match="five seeds"):
        _finalize(registry_path, baseline_path, manifest_path, test_path)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"csi1000": 0.051, "csi300": 0.041, "csi500": 0.046}, "improve"),
        ({"csi1000": 0.050, "csi300": 0.050, "csi500": 0.050}, "regress"),
        ({"csi1000": 0.051, "csi300": 0.039, "csi500": 0.046}, "inconclusive"),
    ],
)
def test_final_uses_the_predeclared_three_way_conclusion_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, float],
    expected: str,
):
    registry_path, baseline_path, manifest_path, test_path = _setup_final_files(
        tmp_path, monkeypatch, values=values
    )

    row = _finalize(registry_path, baseline_path, manifest_path, test_path)

    assert row["conclusion"] == expected
    assert row["pairwise_csi1000_rankic_vs_b5"]["n"] == 5
    assert row["pairwise_csi1000_rankic_vs_b5"]["seeds"] == SEEDS


def test_final_records_complete_audit_and_renders_b5_first_with_12_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    registry_path, baseline_path, manifest_path, test_path = _setup_final_files(
        tmp_path, monkeypatch
    )

    row = _finalize(registry_path, baseline_path, manifest_path, test_path)

    pending = register.build_pending_row("2026-07-30")
    for key, value in pending.items():
        if key != "conclusion":
            assert row[key] == value, f"finalization changed pre-registered {key}"
    assert row["selected_candidate"] == register.CANDIDATES[-1]
    assert set(row["candidate_valid_summary"]) == set(register.CANDIDATES)
    assert len(row["configs"]) == 20
    assert len(row["winner_result_dirs"]) == 5
    assert row["selection_manifest_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    assert row["test_result_sha256"] == hashlib.sha256(
        test_path.read_bytes()
    ).hexdigest()
    assert row["audit_artifacts"]["selection_manifest"].endswith("selection.json")
    for pool in POOLS:
        assert set(METRICS) <= set(row["metrics_summary"][pool])

    html = report.build_html([_baseline_row(), row])
    section = html.split("id='direction-model-hyperparam'", 1)[1]
    assert section.index("baseline/b5-m") < section.index(register.EXP_ID)
    final_html_row = section.split(register.EXP_ID, 1)[1].split("</tr>", 1)[0]
    assert final_html_row.count('<td class="num') == 12
    assert '<span class="empty">—</span>' not in final_html_row


def test_cli_requires_data_version_only_for_pending():
    with pytest.raises(SystemExit):
        register.parse_args(["--stage", "pending"])
    args = register.parse_args(["--stage", "pending", "--data-version", "2026-07-30"])
    assert args.data_version == "2026-07-30"
    args = register.parse_args(["--stage", "final"])
    assert args.data_version is None
