"""跨池 IC/RankIC 评估：加载已训练模型，在多个测试池上打分并按统一口径计算 IC。

规范（backtest/EXPERIMENT_STANDARD.md）第 4/5 节的配套工具：
- 模型只在基线训练池训练一次（每种子一个 session），本脚本负责在 4 个测试集上推理评估；
- 评测标签固定为默认 `Ref($close, -2)/Ref($close, -1) - 1`，与训练标签无关，
  保证不同标签实验的 IC 可比；
- 全A 池（all）自动剔除上市不足 --min-listing-days 个交易日的股票；
  可选 --st-names 提供 symbol,name 映射以剔除 ST 股，未提供时输出中注明。

用法示例：
    /opt/anaconda3/envs/qlib/bin/python backtest/scripts/eval_ic_multi_pool.py \
        --config csi300_lgbm_base_s42.yaml \
        --sessions 20260801_xxx_base_s42:42 20260801_xxx_base_s1000:1000 \
        --pools csi300 csi500 csi1000 all \
        --output backtest/result/20260801_xxx/ic_eval.json
"""

from __future__ import annotations

import argparse
import copy
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from config_loader import (  # noqa: E402
    build_handler_kwargs,
    load_config,
    load_session_model_info,
    resolve_session_dir,
)
from eval_protocol import daily_ic, summarize_ic  # noqa: E402

EVAL_LABEL_EXPR = "Ref($close, -2)/Ref($close, -1) - 1"
DEFAULT_POOLS = ("csi300", "csi500", "csi1000", "all")
POOLS_NEED_LISTING_FILTER = {"all"}


def _init_qlib(cfg: dict) -> None:
    import qlib

    qlib.init(provider_uri=cfg["data"]["provider_uri"], region=cfg["data"].get("region", "cn"))


def _load_model(session: str, from_run: int = 1) -> Any:
    info = load_session_model_info(resolve_session_dir(session), from_run=from_run)
    with open(info["model_path"], "rb") as fh:
        return pickle.load(fh)


def _handler_start_for_inference(test_start: str) -> str:
    """推理只需要 test 区间前约一年历史（Alpha158 最长窗口 60 交易日）。"""
    ts = pd.Timestamp(test_start) - pd.Timedelta(days=365)
    return ts.strftime("%Y-%m-%d")


def _build_dataset(cfg: dict, pool: str):
    """按 config 的 handler 设置构建指定池的推理 DatasetH（仅 test 段被使用）。"""
    from qlib.utils import init_instance_by_config

    pool_cfg = copy.deepcopy(cfg)
    pool_cfg["data"]["instruments"] = pool
    handler = pool_cfg["data"]["handler"]
    handler.pop("instruments", None)
    test = list(cfg["segments"]["test"])
    handler["start_time"] = _handler_start_for_inference(str(test[0]))
    handler["end_time"] = str(test[1])
    # ProcessInf 等 infer processors 无需拟合统计量；fit 区间仅为满足接口
    handler["fit_start_time"] = handler["start_time"]
    handler["fit_end_time"] = str(test[0])

    handler_cfg = build_handler_kwargs(pool_cfg)
    dataset_cfg = {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": handler_cfg,
            "segments": {"test": tuple(test)},
        },
    }
    return init_instance_by_config(dataset_cfg)


def _fetch_label(pool: str, start: str, end: str) -> pd.Series:
    """固定评测标签（不做任何截面归一化；IC 对逐日仿射变换不敏感）。"""
    from qlib.data import D

    df = D.features(D.instruments(pool), [EVAL_LABEL_EXPR], start_time=start, end_time=end)
    s = df.iloc[:, 0]
    s.index = s.index.set_names(["instrument", "datetime"])
    return s.swaplevel().sort_index()


def _listing_age_mask(index: pd.MultiIndex, pool: str, min_days: int, end: str) -> pd.Series:
    """保留“评估日距该股数据起始 >= min_days 个交易日”的样本。"""
    from qlib.data import D

    cal = pd.DatetimeIndex(D.calendar(start_time="2000-01-01", end_time=end))
    inst_spans = D.list_instruments(
        D.instruments(pool), start_time="2000-01-01", end_time=end, as_list=False
    )
    first_pos: dict[str, int] = {}
    for code, spans in inst_spans.items():
        starts = [pd.Timestamp(s) for s, _ in spans]
        first_pos[code] = int(cal.searchsorted(min(starts)))

    dt_pos = pd.Series(cal.searchsorted(index.get_level_values("datetime")), index=index)
    inst_first = pd.Series(
        [first_pos.get(i, 10**9) for i in index.get_level_values("instrument")], index=index
    )
    return (dt_pos - inst_first) >= min_days


def _load_st_symbols(st_names: Optional[Path]) -> Optional[set[str]]:
    """读取 symbol,name 两列 CSV，返回名称含 ST 的代码集合。"""
    if st_names is None:
        return None
    df = pd.read_csv(st_names)
    sym_col, name_col = df.columns[0], df.columns[1]
    mask = df[name_col].astype(str).str.upper().str.contains("ST")
    return set(df.loc[mask, sym_col].astype(str).str.upper())


def evaluate(
    cfg: dict,
    sessions: Sequence[tuple[str, Any]],
    pools: Sequence[str],
    *,
    min_listing_days: int = 60,
    st_symbols: Optional[set[str]] = None,
    min_count: int = 20,
) -> dict:
    from qlib.data import D

    test_start, test_end = (str(x) for x in cfg["segments"]["test"])
    models = [(seed, _load_model(session)) for session, seed in sessions]

    result: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": cfg.get("_config_path"),
        "eval_label": EVAL_LABEL_EXPR,
        "test_segment": [test_start, test_end],
        "sessions": [{"session": s, "seed": seed} for s, seed in sessions],
        "data_version": str(pd.Timestamp(D.calendar(start_time="2020-01-01")[-1]).date()),
        "st_filter": "enabled" if st_symbols is not None else "unavailable（未剔除 ST）",
        "pools": {},
    }

    for pool in pools:
        label = _fetch_label(pool, test_start, test_end)
        if pool in POOLS_NEED_LISTING_FILTER:
            mask = _listing_age_mask(label.index, pool, min_listing_days, test_end)
            label = label[mask]
        if st_symbols:
            keep = ~label.index.get_level_values("instrument").str.upper().isin(st_symbols)
            label = label[keep]

        dataset = _build_dataset(cfg, pool)
        pool_out: dict[str, Any] = {"seeds": {}}
        for seed, model in models:
            pred = model.predict(dataset, segment="test")
            if isinstance(pred, pd.DataFrame):
                pred = pred.iloc[:, 0]
            pred.index = pred.index.set_names(["datetime", "instrument"])
            daily = daily_ic(pred, label, min_count=min_count)
            pool_out["seeds"][str(seed)] = summarize_ic(daily)

        seed_stats = [v for v in pool_out["seeds"].values() if v.get("n_days")]
        if seed_stats:
            pool_out["seed_mean"] = {
                k: float(np.mean([s[k] for s in seed_stats if s.get(k) is not None]))
                for k in ("ic_mean", "icir", "rank_ic_mean", "rank_icir")
                if any(s.get(k) is not None for s in seed_stats)
            }
            rics = [s["rank_ic_mean"] for s in seed_stats if s.get("rank_ic_mean") is not None]
            if len(rics) > 1:
                pool_out["seed_mean"]["rank_ic_mean_std"] = float(np.std(rics, ddof=1))
        result["pools"][pool] = pool_out
        print(f"[{pool}] {pool_out.get('seed_mean', {})}")

    return result


def _parse_session(raw: str) -> tuple[str, Any]:
    session, _, seed = raw.rpartition(":")
    if not session:
        return raw, None
    return session, (int(seed) if seed.isdigit() else seed)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="跨池 IC/RankIC 评估（统一口径）")
    p.add_argument("--config", required=True, help="基准 YAML（提供 handler/segments 设置）")
    p.add_argument(
        "--sessions",
        nargs="+",
        required=True,
        metavar="SESSION[:SEED]",
        help="训练结果 session 目录（backtest/result/ 下），冒号后跟种子号",
    )
    p.add_argument("--pools", nargs="+", default=list(DEFAULT_POOLS))
    p.add_argument("--output", required=True, type=Path, help="输出 JSON 路径")
    p.add_argument("--min-listing-days", type=int, default=60, help="全A 池最短上市交易日数")
    p.add_argument("--st-names", type=Path, default=None, help="可选 symbol,name CSV 用于剔除 ST")
    p.add_argument("--min-count", type=int, default=20, help="单日截面最少样本数")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    cfg = load_config(args.config)
    _init_qlib(cfg)

    sessions = [_parse_session(s) for s in args.sessions]
    st_symbols = _load_st_symbols(args.st_names)
    result = evaluate(
        cfg,
        sessions,
        args.pools,
        min_listing_days=args.min_listing_days,
        st_symbols=st_symbols,
        min_count=args.min_count,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
