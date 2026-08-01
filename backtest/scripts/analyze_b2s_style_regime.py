"""B2-S 组合的坏日子/事件簇诊断，以及冻结 B6-M 分数的风格暴露画像。

回答三个问题：

1. B2-S（TopkDropout t30/d2/h20）的扣费超额收益最差的日子，是否聚集成少数
   事件簇。若 valid 段的独立事件簇只有个位数，风格择时缺乏可用样本，应转向
   约束型方案而非预测型 overlay。
2. 冻结的 B6-M seed4000 分数的风格暴露画像是否仍是"低成交额 + 低波动 +
   低动量"，以及逐日残差化之后还剩多少 RankIC / RankICIR。
3. 坏日子当天的风格溢价是否显著偏离全样本，即坏日子是否就是风格反转日。

红线：valid 段（2020-01-13 ~ 2021-07-15）是唯一允许用于后续信号/参数设计的
窗口；test 段数字仅作描述性确认，不得用于选型。输出 JSON 按段分开存放，并
显式标注该约束。

用法：
    /opt/anaconda3/envs/qlib/bin/python backtest/scripts/analyze_b2s_style_regime.py \
        --report backtest/result/<session>/run_01/report_normal.csv \
        --pred backtest/experiments/strategy-stability/20260801_full_period/predictions/b6-m/csi1000_full.pkl \
        --config strategy-stability/b6-m/topk-t30-d2-h20_csi1000_full.yaml \
        --output backtest/experiments/ic/b2s_style_regime.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

POOL = "csi1000"
VALID_SEGMENT = ("2020-01-13", "2021-07-15")
TEST_SEGMENT = ("2021-07-16", "2026-07-31")

STYLE_EXPRS = {
    "size_liq": "Log(Mean($volume*$vwap, 20)+1)",
    "vol20": "Std($close/Ref($close, 1)-1, 20)",
    "mom20": "$close/Ref($close, 20)-1",
    "rev5": "$close/Ref($close, 5)-1",
    "price_raw": "$close/$factor",
}
LABEL_1D = "Ref($close, -2)/Ref($close, -1) - 1"
MIN_COUNT = 20
BAD_DAY_QUANTILE = 0.10
CLUSTER_MAX_GAP = 5

# 事件簇数量的可用性判据：择时信号需要足够多的独立事件才可能被验证。
CLUSTER_MARGINAL = 10
CLUSTER_USABLE = 25


def daily_excess_with_cost(report: pd.DataFrame) -> pd.Series:
    """qlib 口径：excess_return_with_cost = return - bench - cost。"""
    missing = [col for col in ("return", "bench", "cost") if col not in report.columns]
    if missing:
        raise ValueError(f"report_normal 缺少列: {missing}")
    excess = report["return"] - report["bench"] - report["cost"]
    return excess.rename("excess_with_cost")


def select_bad_days(excess: pd.Series, quantile: float = BAD_DAY_QUANTILE) -> pd.DatetimeIndex:
    """取最差分位的交易日，按收益升序返回（最差在前）。"""
    if not 0 < quantile < 1:
        raise ValueError("quantile 必须落在 (0, 1)")
    clean = excess.dropna()
    if clean.empty:
        return pd.DatetimeIndex([])
    n = max(1, int(round(len(clean) * quantile)))
    return pd.DatetimeIndex(clean.nsmallest(n).index)


def cluster_events(
    bad_days: pd.DatetimeIndex,
    calendar: Sequence[pd.Timestamp],
    max_gap: int = CLUSTER_MAX_GAP,
) -> list[dict]:
    """把间隔不超过 max_gap 个交易日的坏日子归并为一个事件簇。"""
    if len(bad_days) == 0:
        return []
    positions = {ts: i for i, ts in enumerate(pd.DatetimeIndex(calendar))}
    ordered = sorted(bad_days)
    unknown = [ts for ts in ordered if ts not in positions]
    if unknown:
        raise ValueError(f"坏日子不在交易日历中: {unknown[:3]}")

    clusters: list[list[pd.Timestamp]] = [[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:]):
        if positions[current] - positions[previous] <= max_gap:
            clusters[-1].append(current)
        else:
            clusters.append([current])
    return [
        {
            "start": str(group[0].date()),
            "end": str(group[-1].date()),
            "n_bad_days": len(group),
            "span_trading_days": positions[group[-1]] - positions[group[0]] + 1,
            "_days": group,
        }
        for group in clusters
    ]


def summarize_clusters(excess: pd.Series, clusters: Sequence[dict]) -> dict:
    """给出簇数、坏日损失集中度，用于判断择时信号的有效样本量。"""
    enriched = []
    for cluster in clusters:
        days = cluster.get("_days") or []
        total = float(excess.reindex(days).sum())
        entry = {key: value for key, value in cluster.items() if key != "_days"}
        entry["excess_sum"] = total
        enriched.append(entry)
    enriched.sort(key=lambda item: item["excess_sum"])

    bad_day_loss = float(sum(item["excess_sum"] for item in enriched))
    worst = enriched[0]["excess_sum"] if enriched else 0.0
    return {
        "n_clusters": len(enriched),
        "n_bad_days": int(sum(item["n_bad_days"] for item in enriched)),
        "bad_day_excess_sum": bad_day_loss,
        "worst_cluster_excess_sum": worst,
        "worst_cluster_share_of_bad_day_loss": (
            float(worst / bad_day_loss) if bad_day_loss else None
        ),
        "effective_sample_verdict": effective_sample_verdict(len(enriched)),
        "clusters": enriched,
    }


def effective_sample_verdict(n_clusters: int) -> str:
    if n_clusters < CLUSTER_MARGINAL:
        return "insufficient"
    if n_clusters < CLUSTER_USABLE:
        return "marginal"
    return "usable"


def summarize_series(daily: pd.Series) -> dict:
    clean = daily.dropna()
    if clean.empty:
        return {"n_days": 0}
    std = float(clean.std(ddof=1))
    return {
        "mean": float(clean.mean()),
        "std": std,
        "ir": float(clean.mean() / std) if std > 0 else None,
        "n_days": int(len(clean)),
        "pct_days_neg": float((clean < 0).mean()),
    }


def daily_spearman(ranked: pd.DataFrame, a: str, b: str) -> pd.Series:
    grouped = ranked[[a, b]].dropna().groupby(level="datetime")
    corr = grouped.apply(lambda x: x[a].corr(x[b]) if len(x) >= MIN_COUNT else np.nan)
    return corr.dropna()


def residualize_daily(ranked: pd.DataFrame, target: str, factors: list[str]) -> pd.Series:
    def _one(group: pd.DataFrame) -> pd.Series:
        sub = group.dropna(subset=[target] + factors)
        if len(sub) < MIN_COUNT:
            return pd.Series(np.nan, index=group.index)
        design = np.column_stack([np.ones(len(sub))] + [sub[f].values for f in factors])
        beta, *_ = np.linalg.lstsq(design, sub[target].values, rcond=None)
        resid = pd.Series(sub[target].values - design @ beta, index=sub.index)
        return resid.reindex(group.index)

    return ranked.groupby(level="datetime", group_keys=False).apply(_one)


def _load_pred(path: Path) -> pd.Series:
    pred = pd.read_pickle(path)
    if isinstance(pred, pd.DataFrame):
        pred = pred.iloc[:, 0]
    pred.index = pred.index.set_names(["datetime", "instrument"])
    return pred.rename("score").sort_index()


def _fetch_styles(start: str, end: str) -> pd.DataFrame:
    from qlib.data import D

    exprs = list(STYLE_EXPRS.values()) + [LABEL_1D]
    frame = D.features(D.instruments(POOL), exprs, start_time=start, end_time=end)
    frame.columns = list(STYLE_EXPRS) + ["label_1d"]
    frame.index = frame.index.set_names(["instrument", "datetime"])
    return frame.swaplevel().sort_index()


def _segment_slice(frame: pd.DataFrame, segment: tuple[str, str]) -> pd.DataFrame:
    dates = frame.index.get_level_values("datetime")
    mask = (dates >= pd.Timestamp(segment[0])) & (dates <= pd.Timestamp(segment[1]))
    return frame[mask]


def style_profile(data: pd.DataFrame) -> dict:
    """暴露画像、残差化后 RankIC、以及风格自身溢价。"""
    ranked = data.groupby(level="datetime").rank(pct=True)
    profile: dict = {"exposure": {}, "rank_ic": {}, "style_premium": {}}
    for style in STYLE_EXPRS:
        profile["exposure"][style] = summarize_series(daily_spearman(ranked, "score", style))
        profile["style_premium"][style] = summarize_series(
            daily_spearman(ranked, style, "label_1d")
        )
    profile["rank_ic"]["raw"] = summarize_series(daily_spearman(ranked, "score", "label_1d"))

    residual = ranked.copy()
    residual["score_ex_size"] = residualize_daily(ranked, "score", ["size_liq"])
    residual["score_ex_vol"] = residualize_daily(ranked, "score", ["vol20"])
    residual["score_ex_all"] = residualize_daily(ranked, "score", list(STYLE_EXPRS))
    for key, column in (
        ("ex_size", "score_ex_size"),
        ("ex_vol", "score_ex_vol"),
        ("ex_all_styles", "score_ex_all"),
    ):
        profile["rank_ic"][key] = summarize_series(daily_spearman(residual, column, "label_1d"))
    return profile


def bad_day_style_premium(data: pd.DataFrame, bad_days: pd.DatetimeIndex) -> dict:
    """坏日子当天的风格溢价 vs 全样本，检验坏日子是否即风格反转日。"""
    ranked = data.groupby(level="datetime").rank(pct=True)
    out: dict = {}
    for style in STYLE_EXPRS:
        daily = daily_spearman(ranked, style, "label_1d")
        on_bad = daily.reindex(bad_days).dropna()
        others = daily.drop(index=on_bad.index, errors="ignore")
        out[style] = {
            "bad_day_mean": float(on_bad.mean()) if not on_bad.empty else None,
            "other_day_mean": float(others.mean()) if not others.empty else None,
            "difference": (
                float(on_bad.mean() - others.mean())
                if not on_bad.empty and not others.empty
                else None
            ),
            "n_bad_days_matched": int(len(on_bad)),
        }
    return out


def analyze_segment(
    excess: pd.Series,
    data: pd.DataFrame,
    segment: tuple[str, str],
    *,
    quantile: float,
    max_gap: int,
    with_style: bool,
) -> dict:
    window = excess.loc[segment[0] : segment[1]]
    calendar = pd.DatetimeIndex(window.index)
    bad_days = select_bad_days(window, quantile=quantile)
    clusters = cluster_events(bad_days, calendar, max_gap=max_gap)
    result = {
        "segment": list(segment),
        "excess_with_cost": summarize_series(window),
        "bad_day_quantile": quantile,
        "cluster_max_gap_trading_days": max_gap,
        "cluster_summary": summarize_clusters(window, clusters),
    }
    if with_style:
        segment_data = _segment_slice(data, segment)
        result["style"] = style_profile(segment_data)
        result["bad_day_style_premium"] = bad_day_style_premium(segment_data, bad_days)
    return result


DIRECTION = "signal-style-diagnostic"
BASELINE_MANIFEST = "backtest/models/baselines/b6-m/manifest.json"
SELECTION_CONSTRAINT = (
    "只有 valid 段可用于后续信号与参数设计；test 段数字仅作描述性确认，"
    "不得参与任何选型"
)


def sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def build_registry_row(
    payload: dict,
    output: Path,
    *,
    result_dir: Optional[str],
) -> dict:
    """诊断行：不选型、不占清理额度、不给可比策略指标。"""
    manifest_path = QLIB_ROOT / BASELINE_MANIFEST
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    findings = {}
    for name, block in payload.get("segments", {}).items():
        cluster = block.get("cluster_summary") or {}
        rank_ic = ((block.get("style") or {}).get("rank_ic")) or {}
        findings[name] = {
            "segment": block.get("segment"),
            "n_clusters": cluster.get("n_clusters"),
            "n_bad_days": cluster.get("n_bad_days"),
            "worst_cluster_share_of_bad_day_loss": cluster.get(
                "worst_cluster_share_of_bad_day_loss"
            ),
            "effective_sample_verdict": cluster.get("effective_sample_verdict"),
            "rank_ic_raw": (rank_ic.get("raw") or {}).get("mean"),
            "rank_icir_raw": (rank_ic.get("raw") or {}).get("ir"),
            "rank_ic_ex_size": (rank_ic.get("ex_size") or {}).get("mean"),
            "rank_icir_ex_size": (rank_ic.get("ex_size") or {}).get("ir"),
        }
    return {
        "exp_id": f"{DIRECTION}/b2s-on-b6-m",
        "direction": DIRECTION,
        "phase": "S",
        "date": str(pd.Timestamp.today().date()),
        "state": "complete",
        "conclusion": "diagnostic_no_selection",
        "hypothesis": (
            "检验 B2-S 的坏日子是否聚集成少数事件簇、是否由已知风格溢价反转驱动，"
            "并复核冻结 B6-M 分数的风格暴露与残差化后的信号质量；不用于选型。"
        ),
        "baseline_ref": "B2-S v1.0",
        "frozen_model_ref": "B6 v1.0",
        "model_ref": "b6-m",
        "model_manifest": BASELINE_MANIFEST,
        "model_path": (manifest.get("model") or {}).get("path"),
        "model_sha256": (manifest.get("model") or {}).get("sha256"),
        "pool": POOL,
        "benchmark": "SH000852",
        "strategy": {
            "candidate_id": "topk-t30-d2-h20",
            "strategy_class": "TopkDropoutStrategy",
            "topk": 30,
            "n_drop": 2,
            "hold_thresh": 20,
        },
        "segments": {
            "valid": list(VALID_SEGMENT),
            "test": list(TEST_SEGMENT),
        },
        "selection_constraint": SELECTION_CONSTRAINT,
        "metric_basis": "after_cost_excess_return_daily",
        "prediction": payload.get("prediction"),
        "prediction_sha256": payload.get("prediction_sha256"),
        "metrics_summary": {},
        "diagnostic_result_path": str(output),
        "diagnostic_result_sha256": sha256_of(output),
        "diagnostic_findings": findings,
        "result_dirs": [result_dir] if result_dir else [],
        "cleanup_retention_eligible": False,
        "note": (
            "坏日子/事件簇与风格暴露诊断；指标口径为日频扣费超额，与各方向的年化"
            "策略指标不可比，故 metrics_summary 留空。"
        ),
    }


def upsert_registry(registry: Path, row: dict) -> None:
    lines = registry.read_text(encoding="utf-8").splitlines(keepends=True) if registry.is_file() else []
    parsed = [
        (i, json.loads(line))
        for i, line in enumerate(lines)
        if line.strip()
    ]
    matches = [i for i, item in parsed if item.get("exp_id") == row["exp_id"]]
    if len(matches) > 1:
        raise ValueError(f"duplicate registry exp_id: {row['exp_id']}")
    serialized = json.dumps(row, ensure_ascii=False) + "\n"
    if matches:
        lines[matches[0]] = serialized
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(serialized)
    registry.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry.with_name(registry.name + ".tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    temporary.replace(registry)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path, help="report_normal.csv")
    parser.add_argument("--pred", required=True, type=Path, help="冻结预测 pkl")
    parser.add_argument("--config", required=True, help="用于 qlib init 的配置")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--bad-day-quantile", type=float, default=BAD_DAY_QUANTILE)
    parser.add_argument("--cluster-max-gap", type=int, default=CLUSTER_MAX_GAP)
    parser.add_argument(
        "--skip-style",
        action="store_true",
        help="只做坏日子/事件簇统计，跳过需要取数的风格画像",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="提供时把诊断登记为 registry 行（conclusion=diagnostic_no_selection）",
    )
    parser.add_argument("--result-dir", default=None, help="产生 report 的回测 session")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    report = pd.read_csv(args.report, index_col=0, parse_dates=True)
    excess = daily_excess_with_cost(report)

    data = pd.DataFrame()
    pred_sha = None
    if not args.skip_style:
        from config_loader import load_config
        from eval_ic_multi_pool import _init_qlib
        from phase_s_protocol import sha256_file

        cfg = load_config(args.config)
        _init_qlib(cfg)
        pred_sha = sha256_file(args.pred)
        pred = _load_pred(args.pred)
        dates = pred.index.get_level_values("datetime")
        styles = _fetch_styles(str(dates.min().date()), str(dates.max().date()))
        data = styles.join(pred, how="inner")

    payload = {
        "schema_version": 1,
        "diagnostic": "b2s_style_regime",
        "pool": POOL,
        "frozen_model_ref": "B6 v1.0",
        "strategy_ref": "B2-S v1.0",
        "report": str(args.report),
        "prediction": str(args.pred),
        "prediction_sha256": pred_sha,
        "selection_constraint": (
            "只有 valid 段可用于后续信号与参数设计；test 段数字仅作描述性确认，"
            "不得参与任何选型"
        ),
        "segments": {},
    }
    for name, segment in (("valid", VALID_SEGMENT), ("test", TEST_SEGMENT)):
        payload["segments"][name] = analyze_segment(
            excess,
            data,
            segment,
            quantile=args.bad_day_quantile,
            max_gap=args.cluster_max_gap,
            with_style=not args.skip_style,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"written: {args.output}")
    if args.registry is not None:
        row = build_registry_row(payload, args.output, result_dir=args.result_dir)
        upsert_registry(args.registry, row)
        print(f"registered: {row['exp_id']}")
    for name, block in payload["segments"].items():
        summary = block["cluster_summary"]
        print(
            f"{name}: excess IR={block['excess_with_cost'].get('ir'):.3f} "
            f"bad_days={summary['n_bad_days']} clusters={summary['n_clusters']} "
            f"verdict={summary['effective_sample_verdict']} "
            f"worst_cluster_share={summary['worst_cluster_share_of_bad_day_loss']:.3f}"
        )


if __name__ == "__main__":
    main()
