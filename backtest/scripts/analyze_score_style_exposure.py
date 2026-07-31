"""B5 预测分数的风格暴露诊断（size/波动/动量/反转/价格）。

数据无市值与行业字段：size 用 20 日均成交额（对数）代理（A股与流通市值
秩相关很高），价格用未复权价 $close/$factor。

输出：
1. 逐日截面 Spearman(score, style) 的均值/标准差 —— 暴露画像；
2. 逐日把 score 对 size（及全部风格）做秩回归取残差后的 RankIC —— 对比
   原始 RankIC，回答“RankIC 有多少来自风格押注”；
3. 风格自身对次日收益的 RankIC —— 测试期风格溢价是否兑现。

用法：
    /opt/anaconda3/envs/qlib/bin/python backtest/scripts/analyze_score_style_exposure.py \
        --output backtest/experiments/ic/b5_style_exposure.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from config_loader import load_config  # noqa: E402
from eval_ic_multi_pool import _build_dataset, _init_qlib, _load_model  # noqa: E402

B5_CONFIG = "loss-design/cs-rank-norm/ls_rank_norm_s42.yaml"
SEEDS = [42, 1000, 2000, 3000, 4000]
POOL = "csi1000"

STYLE_EXPRS = {
    "size_liq": "Log(Mean($volume*$vwap, 20)+1)",
    "vol20": "Std($close/Ref($close, 1)-1, 20)",
    "mom20": "$close/Ref($close, 20)-1",
    "rev5": "$close/Ref($close, 5)-1",
    "price_raw": "$close/$factor",
}
LABEL_1D = "Ref($close, -2)/Ref($close, -1) - 1"
MIN_COUNT = 20


def find_sessions() -> list[tuple[str, int]]:
    result_root = QLIB_ROOT / "backtest" / "result"
    out = []
    for seed in SEEDS:
        matches = sorted(result_root.glob(f"*_ls_rank_norm_s{seed}"))
        if not matches:
            raise FileNotFoundError(f"missing session for ls_rank_norm_s{seed}")
        out.append((matches[-1].name, seed))
    return out


def fetch_frame(cfg: dict) -> pd.DataFrame:
    from qlib.data import D

    start, end = (str(x) for x in cfg["segments"]["test"])
    exprs = list(STYLE_EXPRS.values()) + [LABEL_1D]
    df = D.features(D.instruments(POOL), exprs, start_time=start, end_time=end)
    df.columns = list(STYLE_EXPRS) + ["label_1d"]
    df.index = df.index.set_names(["instrument", "datetime"])
    return df.swaplevel().sort_index()


def daily_spearman(ranked: pd.DataFrame, a: str, b: str) -> pd.Series:
    """ranked: 已按日转秩（pct）的 DataFrame；返回逐日 Pearson(rank_a, rank_b)。"""
    g = ranked[[a, b]].dropna().groupby(level="datetime")
    corr = g.apply(lambda x: x[a].corr(x[b]) if len(x) >= MIN_COUNT else np.nan)
    return corr.dropna()


def residualize_daily(ranked: pd.DataFrame, target: str, factors: list[str]) -> pd.Series:
    """逐日把 target 秩对 factors 秩做 OLS，返回残差。"""

    def _one(g: pd.DataFrame) -> pd.Series:
        sub = g.dropna(subset=[target] + factors)
        if len(sub) < MIN_COUNT:
            return pd.Series(np.nan, index=g.index)
        X = np.column_stack([np.ones(len(sub))] + [sub[f].values for f in factors])
        y = sub[target].values
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = pd.Series(y - X @ beta, index=sub.index)
        return resid.reindex(g.index)

    return ranked.groupby(level="datetime", group_keys=False).apply(_one)


def summarize(daily: pd.Series) -> dict:
    d = daily.dropna()
    return {
        "mean": float(d.mean()),
        "std": float(d.std(ddof=1)),
        "ir": float(d.mean() / d.std(ddof=1)) if d.std(ddof=1) > 0 else None,
        "n_days": int(len(d)),
        "pct_days_neg": float((d < 0).mean()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cfg = load_config(B5_CONFIG)
    _init_qlib(cfg)

    frame = fetch_frame(cfg)
    dataset = _build_dataset(cfg, POOL, segment="test")

    result: dict = {"pool": POOL, "config": B5_CONFIG, "seeds": {}, "style_premium": {}}

    # 风格自身对次日收益的逐日 RankIC（测试期风格溢价）
    ranked_styles = frame.groupby(level="datetime").rank(pct=True)
    for style in STYLE_EXPRS:
        result["style_premium"][style] = summarize(
            daily_spearman(ranked_styles, style, "label_1d")
        )

    for session, seed in find_sessions():
        model = _load_model(session)
        pred = model.predict(dataset, segment="test")
        if isinstance(pred, pd.DataFrame):
            pred = pred.iloc[:, 0]
        pred.index = pred.index.set_names(["datetime", "instrument"])
        data = frame.join(pred.rename("score"), how="inner")
        ranked = data.groupby(level="datetime").rank(pct=True)

        row: dict = {"exposure": {}, "rank_ic": {}}
        for style in STYLE_EXPRS:
            row["exposure"][style] = summarize(daily_spearman(ranked, "score", style))

        row["rank_ic"]["raw"] = summarize(daily_spearman(ranked, "score", "label_1d"))

        ranked_r = ranked.copy()
        ranked_r["score_ex_size"] = residualize_daily(ranked, "score", ["size_liq"])
        ranked_r["score_ex_all"] = residualize_daily(ranked, "score", list(STYLE_EXPRS))
        row["rank_ic"]["ex_size"] = summarize(
            daily_spearman(ranked_r, "score_ex_size", "label_1d")
        )
        row["rank_ic"]["ex_all_styles"] = summarize(
            daily_spearman(ranked_r, "score_ex_all", "label_1d")
        )

        result["seeds"][str(seed)] = row
        print(
            f"seed {seed}: size_exp={row['exposure']['size_liq']['mean']:.3f} "
            f"rank_ic raw={row['rank_ic']['raw']['mean']:.5f} "
            f"ex_size={row['rank_ic']['ex_size']['mean']:.5f} "
            f"ex_all={row['rank_ic']['ex_all_styles']['mean']:.5f}"
        )

    # 种子均值
    def _seed_mean(path: list[str]) -> dict:
        vals = {}
        for k in ("mean", "std", "ir"):
            xs = []
            for s in result["seeds"].values():
                node = s
                for p in path:
                    node = node[p]
                if node.get(k) is not None:
                    xs.append(node[k])
            if xs:
                vals[k] = float(np.mean(xs))
        return vals

    result["seed_mean"] = {
        "exposure": {s: _seed_mean(["exposure", s]) for s in STYLE_EXPRS},
        "rank_ic": {
            k: _seed_mean(["rank_ic", k]) for k in ("raw", "ex_size", "ex_all_styles")
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {args.output}")
    print(json.dumps(result["seed_mean"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
