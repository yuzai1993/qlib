"""对多个预测文件做每日截面 z-score 后等权集成。"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd


def _as_score_series(pred: object, path: Path) -> pd.Series:
    if isinstance(pred, pd.DataFrame):
        if pred.shape[1] != 1:
            raise ValueError(f"预测文件必须只有一列: {path}")
        pred = pred.iloc[:, 0]
    if not isinstance(pred, pd.Series):
        raise TypeError(f"预测文件必须是 pandas Series 或单列 DataFrame: {path}")
    if not isinstance(pred.index, pd.MultiIndex) or "datetime" not in pred.index.names:
        raise ValueError(f"预测索引必须是包含 datetime 的 MultiIndex: {path}")
    return pred.rename("score")


def ensemble_preds(pred_paths: Sequence[Path]) -> pd.Series:
    """读取多个预测，按交易日截面 z-score 后等权平均。"""
    if not pred_paths:
        raise ValueError("至少需要一个预测文件")

    standardized = []
    for path_like in pred_paths:
        path = Path(path_like)
        if not path.is_file():
            raise FileNotFoundError(f"预测文件不存在: {path}")
        score = _as_score_series(pd.read_pickle(path), path)
        zscore = score.groupby(level="datetime").transform(
            lambda values: (values - values.mean()) / (values.std() + 1e-12)
        )
        standardized.append(zscore)

    return pd.concat(standardized, axis=1).mean(axis=1).rename("score")


def fuse_horizons(pred_1d: pd.Series, pred_10d: pd.Series) -> pd.Series:
    """按日截面标准化 1d、10d 预测后相加。"""
    if not pred_1d.index.equals(pred_10d.index):
        raise ValueError("1d 与 10d 预测索引必须完全一致")

    standardized = []
    for score in (pred_1d, pred_10d):
        if not isinstance(score.index, pd.MultiIndex) or "datetime" not in score.index.names:
            raise ValueError("预测索引必须是包含 datetime 的 MultiIndex")
        standardized.append(
            score.groupby(level="datetime").transform(
                lambda values: (values - values.mean()) / (values.std() + 1e-12)
            )
        )
    return (standardized[0] + standardized[1]).rename("score")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每日截面 z-score 后等权集成多个 pred.pkl")
    parser.add_argument("--pred", action="append", required=True, type=Path, help="输入 pred.pkl；可重复指定")
    parser.add_argument("--output", required=True, type=Path, help="输出 ensemble_pred.pkl")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = ensemble_preds(args.pred)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_frame().to_pickle(args.output)
    print(f"已集成 {len(args.pred)} 个预测，共 {len(result)} 行: {args.output}")


if __name__ == "__main__":
    main()
