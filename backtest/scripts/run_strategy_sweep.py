"""对固定外部预测执行 TopkDropout / SoftTopk 策略扫参。"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
QLIB_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import RESULT_ROOT, resolve_config_path
from report_utils import make_session_dir

BASELINE_NAME = "topk_dropout_t10_d2_h1"
IR_KEY = "excess_with_cost_information_ratio"
MDD_KEY = "excess_with_cost_max_drawdown"


def strategy_grid() -> list[dict[str, Any]]:
    """返回任务定义的 27 个互不重复策略候选。"""
    candidates = []
    for topk in (10, 20, 30):
        for n_drop in (1, 2):
            for hold_thresh in (1, 3, 5):
                candidates.append(
                    {
                        "name": f"topk_dropout_t{topk}_d{n_drop}_h{hold_thresh}",
                        "strategy_class": "TopkDropoutStrategy",
                        "topk": topk,
                        "n_drop": n_drop,
                        "hold_thresh": hold_thresh,
                    }
                )
    for topk in (10, 20, 30):
        for trade_impact_limit in (0.05, 0.1, 1.0):
            candidates.append(
                {
                    "name": f"soft_topk_t{topk}_i{trade_impact_limit:.2f}".replace(".", ""),
                    "strategy_class": "SoftTopkStrategy",
                    "topk": topk,
                    "trade_impact_limit": trade_impact_limit,
                    "risk_degree": 0.95,
                }
            )
    return candidates


def build_sweep_config(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """从基准 YAML 复制日期和费率，仅替换为 pred-only 策略配置。"""
    config = copy.deepcopy(base)
    config["run"] = {
        "mode": "pred_backtest",
        "note": f"strategy_sweep_{candidate['name']}",
        "generate_figures": False,
    }
    if candidate["strategy_class"] == "TopkDropoutStrategy":
        config["strategy"] = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy.signal_strategy",
            "topk": candidate["topk"],
            "n_drop": candidate["n_drop"],
            "hold_thresh": candidate["hold_thresh"],
        }
    else:
        config["strategy"] = {
            "class": "SoftTopkStrategy",
            "module_path": "qlib.contrib.strategy.cost_control",
            "topk": candidate["topk"],
            "kwargs": {
                "trade_impact_limit": candidate["trade_impact_limit"],
                "risk_degree": candidate["risk_degree"],
            },
        }
    return config


def evaluate_gate(best: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """门禁：IR 至少 +0.05，或 MDD 改善至少 3pp 且 IR 不降。"""
    ir_delta = float(best[IR_KEY]) - float(baseline[IR_KEY])
    mdd_improvement = float(best[MDD_KEY]) - float(baseline[MDD_KEY])
    ir_pass = ir_delta >= 0.05 - 1e-12
    mdd_pass = mdd_improvement >= 0.03 - 1e-12 and ir_delta >= -1e-12
    return {
        "passed": ir_pass or mdd_pass,
        "ir_delta": ir_delta,
        "mdd_improvement": mdd_improvement,
        "reason": "IR 提升 ≥ 0.05" if ir_pass else ("最大回撤改善 ≥ 3pp 且 IR 不降" if mdd_pass else "未满足门禁"),
    }


def _parse_result_dir(stdout: str) -> Path:
    for line in reversed(stdout.splitlines()):
        if line.startswith("结果目录:"):
            return Path(line.split(":", 1)[1].strip())
    raise RuntimeError(f"run_pred_backtest.py 未输出结果目录:\n{stdout}")


def _params_text(row: dict[str, Any]) -> str:
    if row["strategy_class"] == "TopkDropoutStrategy":
        return f"topk={row['topk']}, n_drop={row['n_drop']}, hold_thresh={row['hold_thresh']}"
    return (
        f"topk={row['topk']}, trade_impact_limit={row['trade_impact_limit']}, "
        f"risk_degree={row['risk_degree']}"
    )


def _write_comparison(out_dir: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if row["status"] == "success"]
    if not successful:
        raise RuntimeError("27 个策略回测均失败，无法生成比较报告")
    by_name = {row["name"]: row for row in successful}
    baseline = by_name.get(BASELINE_NAME)
    if baseline is None:
        raise RuntimeError(f"基线策略 {BASELINE_NAME} 回测失败或缺失")

    for row in successful:
        row["delta_ir"] = float(row[IR_KEY]) - float(baseline[IR_KEY])
        row["mdd_improvement"] = float(row[MDD_KEY]) - float(baseline[MDD_KEY])
    ranked = sorted(successful, key=lambda row: float(row[IR_KEY]), reverse=True)
    best = ranked[0]
    gate = evaluate_gate(best, baseline)
    payload = {"baseline": baseline, "best": best, "gate": gate, "ranked": ranked, "all_rows": rows}
    (out_dir / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Task B 策略扫参对比",
        "",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- 基线: `{BASELINE_NAME}`（{_params_text(baseline)}）",
        f"- 基线 IR: {float(baseline[IR_KEY]):.4f}；最大回撤: {float(baseline[MDD_KEY]):.2%}",
        f"- 最优策略: `{best['name']}`（{_params_text(best)}）",
        f"- 最优 IR: {float(best[IR_KEY]):.4f}；ΔIR: {gate['ir_delta']:+.4f}；最大回撤: {float(best[MDD_KEY]):.2%}",
        f"- 门禁: **{'PASS' if gate['passed'] else 'FAIL'}** — {gate['reason']}",
        "",
        "## IR 排名",
        "",
        "| 排名 | 策略 | 参数 | IR | ΔIR vs 基线 | 年化超额收益 | 最大回撤 | MDD 改善 | 结果目录 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for rank, row in enumerate(ranked, start=1):
        lines.append(
            f"| {rank} | {row['strategy_class']} | {_params_text(row)} | {float(row[IR_KEY]):.4f} | "
            f"{row['delta_ir']:+.4f} | {float(row['excess_with_cost_annualized_return']):.2%} | "
            f"{float(row[MDD_KEY]):.2%} | {row['mdd_improvement']:+.2%} | `{row['result_dir']}` |"
        )
    failed = [row for row in rows if row["status"] != "success"]
    if failed:
        lines += ["", "## 失败配置", ""]
        lines.extend(f"- `{row['name']}`：{row.get('error', '未知错误')}" for row in failed)
    (out_dir / "COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="固定预测的策略参数扫参（顺序执行 27 次 pred-only 回测）")
    parser.add_argument("--pred", required=True, type=Path, help="中位种子或其他固定 pred.pkl")
    parser.add_argument(
        "--config",
        default="csi500_lgbm_bt_only_2016_from2020.yaml",
        help="含日期和费率的基准 YAML",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pred_path = args.pred.expanduser().resolve()
    if not pred_path.is_file():
        raise FileNotFoundError(f"预测文件不存在: {pred_path}")
    base_path = resolve_config_path(args.config)
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    out_dir = make_session_dir(RESULT_ROOT, note="strategy_sweep")
    configs_dir = out_dir / "configs"
    configs_dir.mkdir()
    rows: list[dict[str, Any]] = []

    for index, candidate in enumerate(strategy_grid(), start=1):
        config_path = configs_dir / f"{index:02d}_{candidate['name']}.yaml"
        config_path.write_text(
            yaml.safe_dump(build_sweep_config(base, candidate), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(SCRIPT_DIR / "run_pred_backtest.py"),
            "--pred",
            str(pred_path),
            "--config",
            str(config_path),
            "--note",
            f"strategy_sweep_{candidate['name']}",
        ]
        print(f"[{index}/27] {candidate['name']}", flush=True)
        completed = subprocess.run(command, cwd=QLIB_ROOT, text=True, capture_output=True)
        row = dict(candidate)
        row["returncode"] = completed.returncode
        try:
            result_dir = _parse_result_dir(completed.stdout)
            row["result_dir"] = str(result_dir)
            metrics_path = result_dir / "run_01" / "metrics.json"
            row.update(json.loads(metrics_path.read_text(encoding="utf-8")))
        except Exception as exc:
            row.update(status="failed", error=f"{exc}\n{completed.stderr[-2000:]}")
        if completed.returncode != 0:
            row.update(status="failed", error=completed.stderr[-2000:] or row.get("error", "子进程失败"))
        rows.append(row)

    payload = _write_comparison(out_dir, rows)
    print(f"扫参目录: {out_dir}")
    print(f"最优策略: {payload['best']['name']}")
    print(f"门禁: {'PASS' if payload['gate']['passed'] else 'FAIL'}")


if __name__ == "__main__":
    main()
