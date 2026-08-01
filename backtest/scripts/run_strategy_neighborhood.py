"""Run the preregistered B2-S local neighborhood on frozen B6-M predictions."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import RESULT_ROOT, load_config  # noqa: E402
from eval_protocol import yearly_ir  # noqa: E402
from phase_s_protocol import POOL_BENCHMARKS, TEST_SEGMENT, VALID_SEGMENT, sha256_file  # noqa: E402
from run_strategy_sweep import (  # noqa: E402
    _parse_result_dir,
    build_backtest_command,
    build_sweep_config,
    classify_strategy_outcome,
    verify_prediction_contract,
)
from strategy_neighborhood_protocol import (  # noqa: E402
    BASELINE_CANDIDATE_ID,
    score_valid_candidates,
    strategy_neighborhood_grid,
)

DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT
    / "backtest/experiments/strategy-neighborhood/20260802_b2s_local"
)
DEFAULT_CONFIGS_DIR = REPO_ROOT / "backtest/configs/strategy-neighborhood/b2-s-local"
DEFAULT_BASE_CONFIG = (
    REPO_ROOT
    / "backtest/configs/baseline-strategy/b2-s/topk-t30-d2-h20_csi1000_full.yaml"
)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def pending_candidates(
    grid: Sequence[dict[str, Any]], checkpoint: Optional[dict[str, Any]]
) -> list[dict[str, Any]]:
    completed = {
        str(row.get("candidate_id"))
        for row in (checkpoint or {}).get("all_rows") or []
        if row.get("status") == "success"
    }
    return [copy.deepcopy(row) for row in grid if row["candidate_id"] not in completed]


def upsert_result(
    rows: Sequence[dict[str, Any]],
    result: dict[str, Any],
    grid: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(row["candidate_id"]): copy.deepcopy(row) for row in rows}
    candidate_id = str(result["candidate_id"])
    if candidate_id in by_id:
        replacement = copy.deepcopy(result)
        attempts = copy.deepcopy(by_id[candidate_id].get("previous_attempts") or [])
        attempts.append(
            {
                key: copy.deepcopy(value)
                for key, value in by_id[candidate_id].items()
                if key in {"status", "error", "result_dir", "returncode", "config"}
            }
        )
        replacement["previous_attempts"] = attempts
        by_id[candidate_id] = replacement
    else:
        by_id[candidate_id] = copy.deepcopy(result)
    order = {str(candidate["candidate_id"]): index for index, candidate in enumerate(grid)}
    return sorted(by_id.values(), key=lambda row: order[str(row["candidate_id"])])


def build_test_plan(
    winner: dict[str, Any], prediction_manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    entries = prediction_manifest.get("predictions") or []
    tasks = []
    for pool in POOL_BENCHMARKS:
        matches = [
            entry
            for entry in entries
            if (entry.get("model_ref"), entry.get("pool"), entry.get("segment"))
            == ("b6-m", pool, "test")
        ]
        if len(matches) != 1:
            raise ValueError(f"prediction manifest requires one b6-m/{pool}/test artifact")
        tasks.append(
            {
                "pool": pool,
                "segment": "test",
                "candidate": copy.deepcopy(winner),
                "prediction": copy.deepcopy(matches[0]),
            }
        )
    return tasks


def _protocol_payload(grid: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "exp_id": "strategy-neighborhood/b2-s-local-v1",
        "direction": "strategy-neighborhood-b2-s",
        "phase": "S",
        "baseline_ref": "B2-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "model_ref": "b6-m",
        "account": 500000,
        "selection_pool": "csi1000",
        "selection_segment": list(VALID_SEGMENT),
        "test_pools": list(POOL_BENCHMARKS),
        "test_segment": list(TEST_SEGMENT),
        "strategy_grid": list(grid),
        "selection_rule": [
            "axial_neighbor_ir_p25 desc",
            "own_ir desc",
            "annualized_return desc",
            "max_drawdown desc",
            "annualized_one_way_turnover asc",
            "candidate_id asc",
        ],
        "test_policy": "freeze one valid winner, then run winner once per test pool; reuse registered B2-S baseline test",
    }


def _prediction_path(entry: dict[str, Any]) -> Path:
    path = Path(str(entry["path"])).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def _run_candidate(
    base: dict[str, Any],
    candidate: dict[str, Any],
    *,
    pool: str,
    segment: str,
    pred_path: Path,
    prediction_manifest_path: Path,
    configs_dir: Path,
) -> dict[str, Any]:
    prediction = verify_prediction_contract(
        pred_path,
        prediction_manifest_path,
        model_ref="b6-m",
        pool=pool,
        segment=segment,
    )
    config_path = configs_dir / f"{candidate['candidate_id']}_{pool}_{segment}.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(
            build_sweep_config(base, candidate, pool=pool, segment=segment),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    note = f"strategy_neighborhood_{candidate['candidate_id']}_{pool}_{segment}"
    command = build_backtest_command(
        Path(sys.executable),
        SCRIPT_DIR / "run_pred_backtest.py",
        pred_path,
        config_path,
        note,
    )
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    row = {
        **candidate,
        "source_pred": str(pred_path),
        "source_pred_sha256": prediction["prediction_sha256"],
        "returncode": completed.returncode,
        "config": str(config_path),
    }
    try:
        result_dir = _parse_result_dir(completed.stdout)
        row["result_dir"] = str(result_dir)
        row.update(
            json.loads(
                (result_dir / "run_01/metrics.json").read_text(encoding="utf-8")
            )
        )
        classify_strategy_outcome(row)
        if row.get("status") == "success":
            annual = yearly_ir(result_dir / "run_01/report_normal.csv")
            row["yearly_ir"] = {
                str(year): float(value) for year, value in annual.items()
            }
    except Exception as exc:
        row.update(status="failed", error=f"{exc}\n{completed.stderr[-2000:]}")
    if completed.returncode != 0:
        row.update(
            status="failed",
            error=completed.stderr[-2000:]
            or row.get("error", "backtest subprocess failed"),
        )
    return row


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction-manifest", required=True, type=Path)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_BASE_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--configs-dir", type=Path, default=DEFAULT_CONFIGS_DIR)
    parser.add_argument("--max-runtime-hours", type=float, default=4.5)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1 or args.workers > 4:
        parser.error("--workers must be between 1 and 4")
    return args


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    output_root = args.output_root.resolve()
    configs_dir = args.configs_dir.resolve()
    manifest_path = args.prediction_manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    grid = strategy_neighborhood_grid()
    protocol_path = output_root / "protocol.json"
    protocol = _protocol_payload(grid)
    if protocol_path.exists():
        if json.loads(protocol_path.read_text(encoding="utf-8")) != protocol:
            raise ValueError("existing protocol differs from preregistered protocol")
    else:
        write_json_atomic(protocol_path, protocol)
    if args.prepare_only:
        print(f"protocol: {protocol_path}")
        return
    protocol_sha = sha256_file(protocol_path)
    valid_entry = next(
        (
            entry
            for entry in manifest.get("predictions") or []
            if (entry.get("model_ref"), entry.get("pool"), entry.get("segment"))
            == ("b6-m", "csi1000", "valid")
        ),
        None,
    )
    if valid_entry is None:
        raise ValueError("prediction manifest lacks b6-m/csi1000/valid")
    base = load_config(str(args.base_config))
    valid_path = output_root / "valid_results.json"
    checkpoint = (
        json.loads(valid_path.read_text(encoding="utf-8")) if valid_path.exists() else {}
    )
    if checkpoint and checkpoint.get("protocol_sha256") != protocol_sha:
        raise ValueError("valid checkpoint protocol SHA differs from preregistration")
    rows = list(checkpoint.get("all_rows") or [])
    pending = pending_candidates(grid, checkpoint)
    started = time.monotonic()
    def run_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        return _run_candidate(
            base,
            candidate,
            pool="csi1000",
            segment="valid",
            pred_path=_prediction_path(valid_entry),
            prediction_manifest_path=manifest_path,
            configs_dir=configs_dir,
        )

    for offset in range(0, len(pending), args.workers):
        if (time.monotonic() - started) / 3600 >= args.max_runtime_hours:
            raise RuntimeError("runtime budget reached before valid grid completed")
        batch = pending[offset : offset + args.workers]
        for index, candidate in enumerate(batch, offset + 1):
            print(
                f"[{index}/{len(pending)}] {candidate['candidate_id']} "
                f"elapsed={(time.monotonic() - started) / 3600:.2f}h",
                flush=True,
            )
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(run_candidate, candidate) for candidate in batch]
            for future in futures:
                row = future.result()
                rows = upsert_result(rows, row, grid)
                write_json_atomic(
                    valid_path,
                    {
                        "schema_version": 1,
                        "state": "running",
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                        "protocol_sha256": protocol_sha,
                        "model_ref": "b6-m",
                        "pool": "csi1000",
                        "segment": "valid",
                        "all_rows": rows,
                    },
                )
    scored, winner = score_valid_candidates(rows, grid)
    write_json_atomic(
        valid_path,
        {
            "schema_version": 1,
            "state": "valid_complete",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "protocol_sha256": protocol_sha,
            "model_ref": "b6-m",
            "pool": "csi1000",
            "segment": "valid",
            "all_rows": scored,
            "winner": winner,
        },
    )
    print(f"valid winner: {winner['candidate_id']}", flush=True)
    test_path = output_root / "test_results.json"
    test_payload = (
        json.loads(test_path.read_text(encoding="utf-8"))
        if test_path.exists()
        else {"schema_version": 1, "state": "running", "winner": winner, "pools": {}}
    )
    if test_payload.get("winner", {}).get("candidate_id") != winner["candidate_id"]:
        raise ValueError("test checkpoint winner differs from frozen valid winner")
    for task in build_test_plan(winner, manifest):
        pool = task["pool"]
        if test_payload.get("pools", {}).get(pool, {}).get("status") == "success":
            continue
        row = _run_candidate(
            base,
            winner,
            pool=pool,
            segment="test",
            pred_path=_prediction_path(task["prediction"]),
            prediction_manifest_path=manifest_path,
            configs_dir=configs_dir,
        )
        test_payload.setdefault("pools", {})[pool] = row
        write_json_atomic(test_path, test_payload)
        if row.get("status") != "success":
            raise RuntimeError(f"frozen winner test failed for {pool}: {row.get('error')}")
    test_payload["state"] = "test_complete"
    test_payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json_atomic(test_path, test_payload)
    print(f"results: {output_root}")


if __name__ == "__main__":
    main()
