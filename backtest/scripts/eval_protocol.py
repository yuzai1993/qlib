"""多种子回测评估：汇总、成对比较、年度 IR。"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence, Union

import pandas as pd

PathLike = Union[str, Path]

# 常用 metric 简写 → run_backtest.extract_metrics 字段名
METRIC_ALIASES: dict[str, str] = {
    "ir": "excess_with_cost_information_ratio",
    "ann": "excess_with_cost_annualized_return",
    "mdd": "excess_with_cost_max_drawdown",
}


def resolve_metric_key(metric: str) -> str:
    return METRIC_ALIASES.get(metric, metric)


def _ok_rows(rows: Sequence[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r.get("status") not in (None, "success"):
            continue
        out.append(r)
    return out


def rows_by_seed(rows: Sequence[dict], metric: str) -> dict[Any, dict]:
    """{seed: row}，仅保留 metric 有值的行。"""
    key = resolve_metric_key(metric)
    by_seed: dict[Any, dict] = {}
    for r in _ok_rows(rows):
        if "seed" not in r:
            continue
        val = r.get(key)
        if val is None:
            continue
        by_seed[r["seed"]] = r
    return by_seed


def summarize_seed_metrics(rows: Sequence[dict]) -> list[dict]:
    """按 name 分组汇总多种子 IR / ann / mdd 均值与标准差。"""
    by_name: dict[str, list[dict]] = {}
    for r in rows:
        name = r.get("name") or "default"
        by_name.setdefault(name, []).append(r)

    summaries: list[dict] = []
    for name, group in by_name.items():
        ok = [
            r
            for r in group
            if r.get("status") in (None, "success")
            and r.get("excess_with_cost_information_ratio") is not None
        ]
        s: dict[str, Any] = {
            "name": name,
            "n_success": len(ok),
            "n_total": len(group),
        }
        if not ok:
            s["status"] = "failed"
            summaries.append(s)
            continue

        irs = [float(r["excess_with_cost_information_ratio"]) for r in ok]
        anns = [float(r["excess_with_cost_annualized_return"]) for r in ok if r.get("excess_with_cost_annualized_return") is not None]
        mdds = [float(r["excess_with_cost_max_drawdown"]) for r in ok if r.get("excess_with_cost_max_drawdown") is not None]

        s.update(
            {
                "status": "success",
                "ir_mean": statistics.mean(irs),
                "ir_std": statistics.stdev(irs) if len(irs) > 1 else 0.0,
                "ir_min": min(irs),
                "ir_max": max(irs),
                "seeds": [r["seed"] for r in ok if "seed" in r],
            }
        )
        if anns:
            s["ann_mean"] = statistics.mean(anns)
            s["ann_std"] = statistics.stdev(anns) if len(anns) > 1 else 0.0
        if mdds:
            s["mdd_mean"] = statistics.mean(mdds)
        summaries.append(s)
    return summaries


def _metric_value(metrics: dict, metric: str) -> float:
    key = resolve_metric_key(metric)
    if metric in metrics:
        return float(metrics[metric])
    if key in metrics:
        return float(metrics[key])
    raise KeyError(f"metric {metric!r} not in {list(metrics.keys())}")


def pairwise_win_count(
    a_rows: Union[Sequence[dict], Mapping[Any, dict]],
    b_rows: Union[Sequence[dict], Mapping[Any, dict]],
    metric: str = "ir",
) -> dict:
    """同一 seed 下 a 优于 b 的次数。

    a_rows/b_rows 可为 row 列表，或 {seed: metrics_dict}。
    """
    def _to_by_seed(obj: Union[Sequence[dict], Mapping[Any, dict]]) -> dict[Any, dict]:
        if isinstance(obj, Mapping):
            return dict(obj)
        return rows_by_seed(obj, metric)

    a_by_seed = _to_by_seed(a_rows)
    b_by_seed = _to_by_seed(b_rows)

    diffs = []
    for s in sorted(set(a_by_seed) & set(b_by_seed)):
        diffs.append(_metric_value(a_by_seed[s], metric) - _metric_value(b_by_seed[s], metric))
    return {
        "n": len(diffs),
        "wins": sum(d > 0 for d in diffs),
        "diff_mean": sum(diffs) / len(diffs) if diffs else None,
        "diffs": diffs,
    }


def daily_ic(
    pred: pd.Series,
    label: pd.Series,
    *,
    min_count: int = 20,
) -> pd.DataFrame:
    """统一 IC 计算入口：逐日截面 Pearson IC 与 Spearman RankIC。

    pred / label 均为 MultiIndex (datetime, instrument) 的 Series；
    仅使用两者共同的非 NaN 样本；截面样本数 < min_count 的交易日被跳过。
    所有实验的 IC/RankIC 必须经此函数计算，保证口径一致。
    """
    df = pd.concat({"pred": pred, "label": label}, axis=1).dropna()
    if df.empty:
        return pd.DataFrame(columns=["ic", "rank_ic", "count"])

    dt_level = "datetime" if "datetime" in (df.index.names or []) else 0
    rows = {}
    for dt, g in df.groupby(level=dt_level):
        if len(g) < min_count:
            continue
        rows[dt] = {
            "ic": g["pred"].corr(g["label"]),
            "rank_ic": g["pred"].corr(g["label"], method="spearman"),
            "count": len(g),
        }
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def summarize_ic(daily: pd.DataFrame) -> dict:
    """将 daily_ic 输出汇总为 ic_mean/icir/rank_ic_mean/rank_icir 等。"""
    if daily.empty:
        return {"n_days": 0}
    ic = daily["ic"].dropna()
    ric = daily["rank_ic"].dropna()
    out: dict[str, Any] = {"n_days": int(len(daily))}
    if len(ic):
        out["ic_mean"] = float(ic.mean())
        out["ic_std"] = float(ic.std())
        out["icir"] = float(ic.mean() / ic.std()) if ic.std() else None
    if len(ric):
        out["rank_ic_mean"] = float(ric.mean())
        out["rank_ic_std"] = float(ric.std())
        out["rank_icir"] = float(ric.mean() / ric.std()) if ric.std() else None
    return out


def yearly_ir(report_normal_csv: PathLike) -> pd.Series:
    """按年计算日超额（return - bench）的 information_ratio。"""
    from qlib.contrib.evaluate import risk_analysis

    path = Path(report_normal_csv)
    df = pd.read_csv(path, parse_dates=["datetime"])
    df = df.dropna(subset=["return", "bench"])
    excess = df["return"] - df["bench"]
    excess.index = df["datetime"]

    out: dict[int, float] = {}
    for year, grp in excess.groupby(excess.index.year):
        if len(grp) < 2:
            continue
        ra = risk_analysis(grp, freq="day")
        out[int(year)] = float(ra.loc["information_ratio", "risk"])
    return pd.Series(out, name="information_ratio").sort_index()


CORE_METRICS = (
    "excess_with_cost_information_ratio",
    "excess_with_cost_annualized_return",
    "excess_with_cost_max_drawdown",
)


def load_metrics_json(path: PathLike) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def metrics_to_row(
    metrics: dict,
    *,
    name: str,
    seed: Any = None,
    role: str | None = None,
    label: str | None = None,
) -> dict:
    row: dict[str, Any] = {"name": name, "status": metrics.get("status", "success")}
    if seed is not None:
        row["seed"] = seed
    if role is not None:
        row["role"] = role
    if label is not None:
        row["label"] = label
    for key in CORE_METRICS:
        if key in metrics:
            row[key] = metrics[key]
    return row


def _fmt_metric(value: Any, *, pct: bool = False) -> str:
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{v * 100:.2f}%" if pct else f"{v:.4f}"


def build_seed_ensemble_comparison(
    seed_metrics: Sequence[tuple[Any, PathLike]],
    ensemble_metrics_path: PathLike,
    *,
    group_name: str = "cum_h10",
) -> tuple[list[dict], dict, dict, dict]:
    """读取多种子 metrics 与 ensemble metrics，返回明细行、均值行、ensemble 行、汇总。"""
    seed_rows = [
        metrics_to_row(
            load_metrics_json(path),
            name=group_name,
            seed=seed,
            role="seed",
            label=f"{group_name} seed {seed}",
        )
        for seed, path in seed_metrics
    ]
    summaries = summarize_seed_metrics(seed_rows)
    summary = summaries[0] if summaries else {}

    mean_row: dict[str, Any] = {
        "name": group_name,
        "role": "seed_mean",
        "label": f"{group_name} seed 均值",
        "n_success": summary.get("n_success", 0),
        "status": summary.get("status", "failed"),
    }
    if summary.get("status") == "success":
        mean_row.update(
            {
                "excess_with_cost_information_ratio": summary["ir_mean"],
                "excess_with_cost_annualized_return": summary.get("ann_mean"),
                "excess_with_cost_max_drawdown": summary.get("mdd_mean"),
                "ir_std": summary.get("ir_std"),
            }
        )

    ensemble_row = metrics_to_row(
        load_metrics_json(ensemble_metrics_path),
        name=f"{group_name}_ensemble",
        role="ensemble",
        label=f"{group_name} ensemble",
    )
    return seed_rows, mean_row, ensemble_row, summary


def write_seed_ensemble_comparison(
    out_dir: PathLike,
    seed_metrics: Sequence[tuple[Any, PathLike]],
    ensemble_metrics_path: PathLike,
    *,
    group_name: str = "cum_h10",
) -> dict[str, Path]:
    """写入单种子 vs ensemble 对比 CSV / MD 到 out_dir。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    seed_rows, mean_row, ensemble_row, summary = build_seed_ensemble_comparison(
        seed_metrics,
        ensemble_metrics_path,
        group_name=group_name,
    )

    detail_rows = seed_rows + [mean_row, ensemble_row]
    csv_path = out / "COMPARISON_SEED_ENSEMBLE.csv"
    fieldnames = [
        "label",
        "role",
        "name",
        "seed",
        "status",
        *CORE_METRICS,
        "ir_std",
        "n_success",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(detail_rows)

    md_lines = [
        f"# {group_name} 单种子 vs 集成对比",
        "",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- 种子数: {len(seed_rows)}",
        f"- ensemble metrics: `{Path(ensemble_metrics_path).resolve()}`",
        "",
        "| 对象 | IR | ann | mdd |",
        "|---|---:|---:|---:|",
    ]
    for row in (mean_row, ensemble_row):
        md_lines.append(
            f"| {row['label']} | "
            f"{_fmt_metric(row.get('excess_with_cost_information_ratio'))} | "
            f"{_fmt_metric(row.get('excess_with_cost_annualized_return'), pct=True)} | "
            f"{_fmt_metric(row.get('excess_with_cost_max_drawdown'), pct=True)} |"
        )
    if summary.get("status") == "success":
        md_lines += [
            "",
            f"种子 IR 均值 ± 标准差: {_fmt_metric(summary['ir_mean'])} ± {_fmt_metric(summary.get('ir_std'))}",
        ]
    md_lines += [
        "",
        "## 单种子明细",
        "",
        "| seed | IR | ann | mdd |",
        "|---:|---:|---:|---:|",
    ]
    for row in sorted(seed_rows, key=lambda r: r.get("seed", 0)):
        md_lines.append(
            f"| {row.get('seed')} | "
            f"{_fmt_metric(row.get('excess_with_cost_information_ratio'))} | "
            f"{_fmt_metric(row.get('excess_with_cost_annualized_return'), pct=True)} | "
            f"{_fmt_metric(row.get('excess_with_cost_max_drawdown'), pct=True)} |"
        )

    md_path = out / "COMPARISON_SEED_ENSEMBLE.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return {"csv": csv_path, "md": md_path}


def _parse_seed_metrics(raw: str) -> tuple[Any, Path]:
    seed_text, _, path_text = raw.partition(":")
    if not path_text:
        raise ValueError(f"--seed-metrics 需为 SEED:PATH，收到: {raw!r}")
    return int(seed_text) if seed_text.isdigit() else seed_text, Path(path_text)


def _cli_compare_seeds(args: argparse.Namespace) -> None:
    seed_metrics = [_parse_seed_metrics(item) for item in args.seed_metrics]
    paths = write_seed_ensemble_comparison(
        args.output_dir,
        seed_metrics,
        args.ensemble_metrics,
        group_name=args.name,
    )
    print(f"CSV: {paths['csv']}")
    print(f"MD:  {paths['md']}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多种子 vs ensemble 指标对比")
    parser.add_argument(
        "--seed-metrics",
        action="append",
        required=True,
        metavar="SEED:PATH",
        help="单种子 metrics.json，可重复指定",
    )
    parser.add_argument("--ensemble-metrics", required=True, type=Path, help="ensemble run_01/metrics.json")
    parser.add_argument("--output-dir", required=True, type=Path, help="写入对比表的目录")
    parser.add_argument("--name", default="cum_h10", help="方案名前缀")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    _cli_compare_seeds(parse_args(argv))


if __name__ == "__main__":
    main()
