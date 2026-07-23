"""
特征校验脚本。

两档模式：
1. --parse-only（默认可用，无需行情数据 / 可不编译 Cython）
   - 校验表达式可被 qlib parse_field 解析
   - 检查特征名唯一、各组规模
2. 完整模式（需要 ~/.qlib/qlib_data/cn_data 且 Cython 扩展已编译）
   - 拉取少量股票的特征矩阵
   - 检查 NaN 比例、数值范围
   - 与 Alpha158 已有因子（ROC/BETA/RSQR/MA/STD）做相关性，>0.95 标记为冗余

用法：
  python backtest/scripts/validate_features.py --parse-only
  python backtest/scripts/validate_features.py --corr-threshold 0.95
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))

from backtest.features.expressions import (  # noqa: E402
    FEATURE_GROUP_NAMES,
    build_extra_features,
)
from backtest.features.qlib_stubs import install_cython_stubs  # noqa: E402

RESULT_DIR = Path(__file__).resolve().parents[1] / "result" / "feature_exp"
PROVIDER_URI_DEFAULT = "~/.qlib/qlib_data/cn_data"

BASELINE_CORR_CANDIDATES = [
    "ROC5",
    "ROC10",
    "ROC20",
    "ROC30",
    "ROC60",
    "MA5",
    "MA10",
    "MA20",
    "MA60",
    "STD5",
    "STD10",
    "STD20",
    "STD60",
    "BETA5",
    "BETA10",
    "BETA20",
    "BETA60",
    "RSQR5",
    "RSQR10",
    "RSQR20",
    "RSQR60",
]


def validate_parse(feature_groups=None) -> dict:
    """仅解析表达式，不访问行情。"""
    used_stubs = install_cython_stubs()

    from qlib.data.base import Feature  # noqa: F401
    from qlib.data.ops import Operators  # noqa: F401
    from qlib.utils import parse_field

    groups = list(feature_groups) if feature_groups else list(FEATURE_GROUP_NAMES)
    fields, names = build_extra_features(groups)

    report = {
        "groups": groups,
        "n_features": len(names),
        "names": names,
        "parse_errors": [],
        "duplicate_names": [],
        "used_cython_stubs": used_stubs,
    }

    if len(names) != len(set(names)):
        seen = set()
        for n in names:
            if n in seen:
                report["duplicate_names"].append(n)
            seen.add(n)

    for name, field in zip(names, fields):
        try:
            expr = eval(parse_field(field))  # noqa: S307 — qlib 官方表达式解析路径
            str(expr)
        except Exception as e:  # noqa: BLE001
            report["parse_errors"].append({"name": name, "field": field, "error": str(e)})

    report["group_sizes"] = {}
    for g in FEATURE_GROUP_NAMES:
        _, gnames = build_extra_features([g])
        report["group_sizes"][g] = len(gnames)

    report["ok"] = not report["parse_errors"] and not report["duplicate_names"]
    return report


def _nan_stats(df: pd.DataFrame) -> pd.DataFrame:
    total = len(df)
    rows = []
    for col in df.columns:
        s = df[col]
        nan_ratio = float(s.isna().mean()) if total else 1.0
        finite = s.replace([np.inf, -np.inf], np.nan).dropna()
        rows.append(
            {
                "feature": col,
                "nan_ratio": nan_ratio,
                "inf_ratio": float(np.isinf(s.replace(np.nan, 0)).mean()) if total else 0.0,
                "min": float(finite.min()) if len(finite) else np.nan,
                "max": float(finite.max()) if len(finite) else np.nan,
                "mean": float(finite.mean()) if len(finite) else np.nan,
                "std": float(finite.std()) if len(finite) else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("nan_ratio", ascending=False)


def _high_corr_pairs(
    feature_df: pd.DataFrame,
    extra_names: list,
    baseline_names: list,
    threshold: float,
) -> pd.DataFrame:
    available_extra = [c for c in extra_names if c in feature_df.columns]
    available_base = [c for c in baseline_names if c in feature_df.columns]
    if not available_extra or not available_base:
        return pd.DataFrame(columns=["extra", "baseline", "corr"])

    sub = feature_df[available_extra + available_base].replace([np.inf, -np.inf], np.nan)
    corr = sub.corr()
    rows = []
    for e in available_extra:
        for b in available_base:
            val = corr.loc[e, b]
            if pd.notna(val) and abs(val) >= threshold:
                rows.append({"extra": e, "baseline": b, "corr": float(val)})
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.reindex(out["corr"].abs().sort_values(ascending=False).index)
    return out.reset_index(drop=True)


def validate_with_data(
    provider_uri: str,
    market: str,
    start_time: str,
    end_time: str,
    corr_threshold: float,
    feature_groups=None,
) -> tuple:
    import qlib
    from qlib.constant import REG_CN
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.utils import exists_qlib_data

    from backtest.features.handler import Alpha158Ext

    if not exists_qlib_data(provider_uri):
        raise FileNotFoundError(
            f"Qlib 数据未找到: {provider_uri}\n"
            "可先跑 --parse-only，或准备好 cn_data 后再做完整校验。"
        )

    qlib.init(provider_uri=provider_uri, region=REG_CN)
    groups = list(feature_groups) if feature_groups else list(FEATURE_GROUP_NAMES)

    handler = Alpha158Ext(
        instruments=market,
        start_time=start_time,
        end_time=end_time,
        fit_start_time=start_time,
        fit_end_time=end_time,
        feature_groups=groups,
        learn_processors=[],
        infer_processors=[],
    )

    df = handler.fetch(col_set="feature", data_key=DataHandlerLP.DK_R)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)

    extra_names = handler.list_extra_feature_names()
    extra_df = df[extra_names]
    stats = _nan_stats(extra_df)
    corr_df = _high_corr_pairs(df, extra_names, BASELINE_CORR_CANDIDATES, corr_threshold)

    report = {
        "groups": groups,
        "n_rows": int(len(df)),
        "n_extra_features": len(extra_names),
        "high_nan_features": stats.loc[stats["nan_ratio"] > 0.3, "feature"].tolist(),
        "redundant_pairs": corr_df.to_dict(orient="records"),
        "stats_head": stats.head(20).to_dict(orient="records"),
        "ok": len(stats.loc[stats["nan_ratio"] > 0.9]) == 0,
    }
    return report, stats, corr_df


def main():
    parser = argparse.ArgumentParser(description="校验 Alpha158Ext 扩展特征")
    parser.add_argument("--parse-only", action="store_true", help="仅解析表达式，不拉行情")
    parser.add_argument(
        "--groups",
        nargs="+",
        choices=list(FEATURE_GROUP_NAMES),
        default=list(FEATURE_GROUP_NAMES),
        help="要校验的特征组",
    )
    parser.add_argument("--provider-uri", default=PROVIDER_URI_DEFAULT)
    parser.add_argument("--market", default="csi300")
    parser.add_argument("--start-time", default="2020-01-01")
    parser.add_argument("--end-time", default="2020-06-30")
    parser.add_argument("--corr-threshold", type=float, default=0.95)
    args = parser.parse_args()

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    parse_report = validate_parse(args.groups)
    parse_path = RESULT_DIR / "validate_parse.json"
    with open(parse_path, "w", encoding="utf-8") as f:
        json.dump(parse_report, f, ensure_ascii=False, indent=2)
    print(f"[parse] groups={parse_report['groups']} n={parse_report['n_features']}")
    print(f"[parse] group_sizes={parse_report['group_sizes']}")
    if parse_report["parse_errors"]:
        print(f"[parse] ERRORS: {parse_report['parse_errors']}")
    else:
        print("[parse] 全部表达式解析通过")
    print(f"[parse] 报告: {parse_path}")

    if args.parse_only:
        sys.exit(0 if parse_report["ok"] else 1)

    if not parse_report["ok"]:
        print("[data] 解析失败，跳过行情校验")
        sys.exit(1)

    try:
        data_report, stats, corr_df = validate_with_data(
            provider_uri=args.provider_uri,
            market=args.market,
            start_time=args.start_time,
            end_time=args.end_time,
            corr_threshold=args.corr_threshold,
            feature_groups=args.groups,
        )
    except FileNotFoundError as e:
        print(f"[data] {e}")
        sys.exit(2)
    except ModuleNotFoundError as e:
        print(f"[data] 缺少编译扩展或依赖: {e}")
        print("请先执行: python setup.py build_ext --inplace")
        sys.exit(3)

    stats_path = RESULT_DIR / "validate_stats.csv"
    corr_path = RESULT_DIR / "validate_corr.csv"
    data_path = RESULT_DIR / "validate_data.json"
    stats.to_csv(stats_path, index=False)
    corr_df.to_csv(corr_path, index=False)
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data_report, f, ensure_ascii=False, indent=2)

    print(f"[data] rows={data_report['n_rows']} extra_features={data_report['n_extra_features']}")
    print(f"[data] high_nan(>0.3)={data_report['high_nan_features']}")
    print(f"[data] redundant_pairs(corr>={args.corr_threshold})={len(data_report['redundant_pairs'])}")
    if data_report["redundant_pairs"]:
        for row in data_report["redundant_pairs"][:10]:
            print(f"       {row['extra']} vs {row['baseline']}: {row['corr']:.4f}")
    print(f"[data] stats: {stats_path}")
    print(f"[data] corr:  {corr_path}")
    print(f"[data] report:{data_path}")
    sys.exit(0 if data_report["ok"] else 1)


if __name__ == "__main__":
    main()
