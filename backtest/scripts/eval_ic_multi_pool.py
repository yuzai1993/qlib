"""跨池 IC/RankIC 评估：加载已训练模型，在多个测试池上打分并按统一口径计算 IC。

规范（backtest/EXPERIMENT_STANDARD.md）第 4/5 节的配套工具：
- 模型只在基线训练池训练一次（每种子一个 session），本脚本默认在 3 个测试集
  （csi300/csi500/csi1000）上推理评估；全A 需显式 --pools ... all；
- 评测标签固定为默认 `Ref($close, -2)/Ref($close, -1) - 1`，与训练标签无关，
  保证不同标签实验的 IC 可比；
- 全A 池（all）自动剔除上市不足 --min-listing-days 个交易日的股票；
  可选 --st-names 提供 symbol,name 映射以剔除 ST 股，未提供时输出中注明。

用法示例：
    /opt/anaconda3/envs/qlib/bin/python backtest/scripts/eval_ic_multi_pool.py \
        --config baseline/b0-m/b0_csi300_lgbm_s42.yaml \
        --sessions 20260801_xxx_base_s42:42 20260801_xxx_base_s1000:1000 \
        --pools csi300 csi500 csi1000 \
        --output backtest/result/20260801_xxx/ic_eval.json
"""

from __future__ import annotations

import argparse
import copy
import gc
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from config_loader import (  # noqa: E402
    build_handler_kwargs,
    load_config,
    load_session_model_info,
    resolve_session_dir,
)
from eval_protocol import daily_ic, summarize_ic  # noqa: E402

EVAL_LABEL_EXPR = "Ref($close, -2)/Ref($close, -1) - 1"
DEFAULT_POOLS = ("csi300", "csi500", "csi1000")  # 默认不含全A；需评估时显式 --pools ... all
POOLS_NEED_LISTING_FILTER = {"all"}


# ---------------------------------------------------------------------------
# regime-adapt 扩展（计划 v3 第 4 节，用户 2026-08-09 批准的口径扩展）。
# 旧入口 evaluate()/evaluate_rolling() 保持逐字节不变；多期限/分层采样/分风格/
# 尾部诊断全部走独立入口 evaluate_multi_horizon()，h1 与历史口径的一致性由
# 共用 _fetch_label/daily_ic/summarize_ic 保证。
# ---------------------------------------------------------------------------


def _horizon_label_expr(horizon: int) -> str:
    """h 日前视收益标签；h=1 时与 EVAL_LABEL_EXPR 完全一致。"""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    return f"Ref($close, -{horizon + 1})/Ref($close, -1) - 1"


def load_date_list(path: Path) -> pd.DatetimeIndex:
    """读取冻结测试日清单（支持 # 注释头），返回去重升序 DatetimeIndex。"""
    df = pd.read_csv(path, comment="#")
    if "date" not in df.columns:
        raise ValueError(f"date list {path} must contain a 'date' column")
    dates = pd.DatetimeIndex(pd.to_datetime(df["date"])).unique().sort_values()
    if dates.empty:
        raise ValueError(f"date list {path} is empty")
    return dates


def load_regime_monthly(path: Path) -> pd.Series:
    """读取月度风格标签 CSV（datetime,regime3），索引为月末。"""
    df = pd.read_csv(path, index_col=0, parse_dates=True, comment="#")
    col = "regime3" if "regime3" in df.columns else df.columns[0]
    s = df[col].astype(str)
    s.index = s.index.to_period("M").to_timestamp("M")
    return s


def day_regime_map(dates: pd.DatetimeIndex, monthly: pd.Series) -> pd.Series:
    """将月度标签映射到交易日。"""
    keys = dates.to_period("M").to_timestamp("M")
    return pd.Series(monthly.reindex(keys).to_numpy(), index=dates)


TRADING_DAYS_PER_YEAR = 238
# 2026-08-16 用户口径：取消北极星；主格 top5×h5；诊断网格 5/15/50 × 2/3/5/10
HEAD_K_CORE = (5, 15, 50)
HEAD_H_CORE = (2, 3, 5, 10)
PRIMARY_K = 5
PRIMARY_H = 5
NORTH_STAR_METRIC = "net_sharpe"
# 日收益量级 O(1e-2)；方差低于此即视为退化（残差恒定），IR 判为无定义
_DEGENERATE_VAR = 1e-24
# 成交额下限：qlib cn_data 的 $volume 单位是手，$amount 字段缺失，
# 故用 $volume × ($close/$factor) × 100 还原未复权成交额（元）。
AMOUNT_LOT_SIZE = 100
DEFAULT_MIN_AMOUNT = 10_000_000.0

# 单边冲击外的显性成本，与 EXPERIMENT_STANDARD 回测口径一致：
# 买入 open_cost=0.00021（含最低佣金忽略）、卖出 close_cost=0.00071。
COST_ONE_WAY_BUY = 0.00021
COST_ONE_WAY_SELL = 0.00071
COST_ROUND_TRIP = COST_ONE_WAY_BUY + COST_ONE_WAY_SELL

# 涨跌幅限制：主板 10%、创业板/科创板 20%（创业板 20% 自 2020-08-24 起）。
# 用 9.5%/19.5% 作封板判定阈值，避开除权与四舍五入噪声。
LIMIT_CAP_MAIN = 0.095
LIMIT_CAP_WIDE = 0.195
CHINEXT_WIDE_FROM = pd.Timestamp("2020-08-24")


def entry_tradable_mask(pool: str, start: str, end: str) -> pd.Series:
    """t+1 建仓可成交掩码：t+1 未涨停封板且当日有成交量。

    评测标签在 t+1 收盘建仓（`Ref($close,-1)` 起算），因此不可成交性只看 t+1：
    - 涨停封板（t+1 相对 t 的涨幅 >= 板块涨停阈值）→ 买不到；
    - t+1 成交量为 0（停牌/无撮合）→ 买不到。
    返回 (datetime, instrument) 索引的 bool Series，NaN 视为不可成交。
    """
    from qlib.data import D

    df = D.features(
        D.instruments(pool),
        ["Ref($close, -1)/$close - 1", "Ref($volume, -1)"],
        start_time=start,
        end_time=end,
    )
    df.index = df.index.set_names(["instrument", "datetime"])
    df = df.swaplevel().sort_index()
    df.columns = ["ret_next", "vol_next"]
    inst = df.index.get_level_values("instrument").str.upper()
    dts = df.index.get_level_values("datetime")
    cap = np.where(
        inst.str.startswith("SH68"),
        LIMIT_CAP_WIDE,
        np.where(
            inst.str.startswith("SZ30") & (dts >= CHINEXT_WIDE_FROM),
            LIMIT_CAP_WIDE,
            LIMIT_CAP_MAIN,
        ),
    )
    ok = (df["ret_next"].to_numpy() < cap) & (df["vol_next"].to_numpy() > 0)
    ok &= df.notna().all(axis=1).to_numpy()
    return pd.Series(ok, index=df.index)


def daily_topk_excess(
    pred: pd.Series,
    label: pd.Series,
    k: int,
    *,
    min_count: int = 20,
) -> pd.Series:
    """逐日 top-k（按 pred）等权标签均值 − 全池等权标签均值。"""
    return daily_head_panel(pred, label, [k], min_count=min_count)[int(k)]["excess"]


def daily_head_panel(
    pred: pd.Series,
    label: pd.Series,
    ks: Sequence[int],
    *,
    min_count: int = 20,
    tradable: Optional[pd.Series] = None,
) -> dict[int, dict[str, Any]]:
    """逐日头部面板：每个 k 返回超额序列与选中标的集合（用于换手/成本）。

    `tradable` 为 (datetime, instrument) bool Series 时，先把不可成交样本从候选池
    剔除，再取 top-k 并计算同一可成交池的等权基准，保证超额是可实现口径。
    """
    ks = [int(k) for k in ks]
    empty = {k: {"excess": pd.Series(dtype=float), "sets": {}} for k in ks}
    # qlib 标签为 float32；统一升到 float64，避免逐日累加的精度漂移
    df = pd.concat({"pred": pred, "label": label}, axis=1).dropna().astype("float64")
    if tradable is not None and not df.empty:
        keep = tradable.reindex(df.index).fillna(False).to_numpy(dtype=bool)
        df = df[keep]
    if df.empty:
        return empty
    dt_level = "datetime" if "datetime" in (df.index.names or []) else 0
    by_day = df.groupby(level=dt_level)
    universe = by_day["label"].mean()
    size = by_day.size()
    rank = by_day["pred"].rank(ascending=False, method="first").to_numpy()
    dts = df.index.get_level_values(dt_level)
    insts = df.index.get_level_values("instrument")
    out: dict[int, dict[str, Any]] = {}
    for k in ks:
        ok_days = size.index[size >= max(min_count, k)]
        sel = np.flatnonzero((rank <= k) & np.asarray(dts.isin(ok_days)))
        if sel.size == 0:
            out[k] = {"excess": pd.Series(dtype=float), "sets": {}}
            continue
        sub_dt = dts[sel]
        top_mean = pd.Series(df["label"].to_numpy()[sel], index=sub_dt).groupby(level=0).mean()
        top_mean = top_mean.sort_index()
        bench = universe.reindex(top_mean.index)
        excess = top_mean - bench
        sets: dict[Any, frozenset] = {}
        sub_inst = insts[sel]
        for dt, pos in pd.Series(np.arange(sel.size), index=sub_dt).groupby(level=0):
            sets[dt] = frozenset(sub_inst[pos.to_numpy()])
        # port/bench 供 appraisal（拟合 beta 的残差 IR）用；excess 等价于 beta 固定为 1
        out[k] = {"excess": excess, "sets": sets, "port": top_mean, "bench": bench}
    return out


def topk_turnover(
    sets: dict[Any, frozenset],
    k: int,
    lag: int,
    *,
    days: Optional[set] = None,
) -> Optional[float]:
    """相隔 lag 个评估日的单边换手率均值：1 − |S_t ∩ S_{t−lag}| / k。

    仅在评估日连续（未做日期抽样）时才是真实换手；抽样日历下结果无意义。
    `days` 用于风格切片：`sets` 须传完整日历的持仓，只对 t∈days 的调仓求均值，
    这样跨风格块边界的一对不会被当成"换掉整个组合"。
    """
    if lag < 1 or len(sets) <= lag:
        return None
    dates = sorted(sets)
    vals = [
        1.0 - len(sets[dates[i]] & sets[dates[i - lag]]) / float(k)
        for i in range(lag, len(dates))
        if days is None or dates[i] in days
    ]
    return float(np.mean(vals)) if vals else None


def hac_ir(
    excess: pd.Series,
    horizon: int,
    *,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> Optional[float]:
    """超额 IR：h=1 用普通标准差；h>1 用 Newey-West（Bartlett，lag=h-1）。"""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    e = pd.Series(excess).dropna().astype(float)
    n = int(len(e))
    lag = horizon - 1
    min_n = max(20, 2 * lag + 10)
    if n < min_n:
        return None
    arr = e.to_numpy()
    mean = float(arr.mean())
    centered = arr - mean
    var = float(centered @ centered / n)
    for j in range(1, lag + 1):
        weight = 1.0 - j / (lag + 1)
        gamma = float(centered[j:] @ centered[:-j] / n)
        var += 2.0 * weight * gamma
    # 下限保护：残差近乎恒定时（如构造/退化数据）浮点噪声会给出天文数字 IR
    if var <= _DEGENERATE_VAR or not np.isfinite(var):
        return None
    return float(mean / np.sqrt(var) * np.sqrt(trading_days / horizon))


def hac_vol(
    excess: pd.Series,
    horizon: int,
    *,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> Optional[float]:
    """超额年化波动：与 hac_ir 同一套 Newey-West 方差，再 ×sqrt(238/h)。"""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    e = pd.Series(excess).dropna().astype(float)
    n = int(len(e))
    lag = horizon - 1
    min_n = max(20, 2 * lag + 10)
    if n < min_n:
        return None
    arr = e.to_numpy()
    centered = arr - float(arr.mean())
    var = float(centered @ centered / n)
    for j in range(1, lag + 1):
        weight = 1.0 - j / (lag + 1)
        gamma = float(centered[j:] @ centered[:-j] / n)
        var += 2.0 * weight * gamma
    if var <= _DEGENERATE_VAR or not np.isfinite(var):
        return None
    return float(np.sqrt(var) * np.sqrt(trading_days / horizon))


def appraisal(
    port: pd.Series,
    bench: pd.Series,
    horizon: int,
    *,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> dict[str, Any]:
    """拟合 beta 的残差口径：resid = port − beta×bench，返回 beta / 年化 alpha / HAC-IR。

    与 `hac_ir(port − bench)` 的区别是 beta 不再被硬钉成 1。硬减基准对真实 beta≠1 的
    组合等于过度/不足对冲，会把基准自身的波动注入"超额"序列、污染分母，
    使 IR 排序退化成 beta 排序。这里的口径与 Phase S 的 appraisal ratio 一致，
    两阶段可直接对排。
    """
    df = pd.concat({"p": port, "b": bench}, axis=1).dropna().astype("float64")
    out: dict[str, Any] = {"beta": None, "ann_alpha": None, "appraisal_ir": None}
    if df.empty:
        return out
    var = float(df["b"].var(ddof=1))
    if not np.isfinite(var) or var <= 0:
        return out
    beta = float(df["p"].cov(df["b"])) / var
    resid = df["p"] - beta * df["b"]
    out["beta"] = beta
    out["ann_alpha"] = float(resid.mean() * trading_days / horizon)
    out["appraisal_ir"] = hac_ir(resid, horizon, trading_days=trading_days)
    return out


def summarize_head_series(
    excess: pd.Series,
    horizon: int,
    *,
    sets: Optional[dict[Any, frozenset]] = None,
    k: Optional[int] = None,
    turnover_days: Optional[set] = None,
    port: Optional[pd.Series] = None,
    bench: Optional[pd.Series] = None,
) -> dict[str, Any]:
    """单格头部汇总：年化超额（×238/h）、HAC-IR、appraisal，以及换手与扣费净额。

    `turnover_period` = 相隔 h 日的单边换手率；`turnover` = 日换手 = period / h
    （h 日换掉 100% → 日换手 1/h）。年化成本 = 238 × 日换手 × 0.092%。
    """
    e = pd.Series(excess).dropna().astype(float)
    n = int(len(e))
    out: dict[str, Any] = {
        "n_days": n,
        "ann_excess": float(e.mean() * TRADING_DAYS_PER_YEAR / horizon) if n else None,
        "ir": hac_ir(e, horizon) if n else None,
    }
    if port is not None and bench is not None and n:
        out.update(appraisal(port, bench, horizon))
    if sets and k:
        period = topk_turnover(sets, int(k), int(horizon), days=turnover_days)
        out["turnover_period"] = period
        if period is not None:
            out["turnover"] = float(period) / float(horizon)
        if period is not None and out["ann_excess"] is not None:
            cost = TRADING_DAYS_PER_YEAR * (period / horizon) * COST_ROUND_TRIP
            out["ann_cost"] = float(cost)
            out["net_ann_excess"] = float(out["ann_excess"] - cost)
            if out.get("ann_alpha") is not None:
                out["net_ann_alpha"] = float(out["ann_alpha"] - cost)
            # 成本按常数从日超额里扣，HAC 波动不变；夏普用扣费净年化 / 该波动
            vol = hac_vol(e, horizon)
            out["net_ann_vol"] = vol
            if vol and vol > 0:
                out["net_sharpe"] = float(out["net_ann_excess"] / vol)
    return out


def grid_mean_ir(
    head: dict,
    ks: Sequence[int] = HEAD_K_CORE,
    hs: Sequence[int] = HEAD_H_CORE,
    *,
    metric: str = NORTH_STAR_METRIC,
) -> Optional[float]:
    """指定 k×h 子格上的等权均值；缺格则对可用格平均，全缺则 None。"""
    vals = []
    for k in ks:
        for h in hs:
            v = head.get(str(int(k)), {}).get(str(int(h)), {}).get(metric)
            if v is not None:
                vals.append(v)
    return float(np.mean(vals)) if vals else None


def _north_star_ir(head: dict) -> Optional[float]:
    """核心 9 格 appraisal-IR 等权均值；缺格则对可用格平均，全缺则 None。"""
    return grid_mean_ir(head)


def _mean_head_grid(seed_recs: Sequence[dict], key: str) -> dict:
    """对 seeds[*][key] 的 k×h 格子做算术平均。"""
    out: dict[str, dict] = {}
    ks: set[str] = set()
    hs: set[str] = set()
    for rec in seed_recs:
        grid = rec.get(key) or {}
        ks.update(grid.keys())
        for k, by_h in grid.items():
            hs.update(by_h.keys())
    for k in sorted(ks, key=lambda x: int(x)):
        out[k] = {}
        for h in sorted(hs, key=lambda x: int(x)):
            cell: dict[str, Any] = {}
            for metric in (
                "ann_excess",
                "ir",
                "beta",
                "ann_alpha",
                "appraisal_ir",
                "net_ann_alpha",
                "n_days",
                "turnover",
                "turnover_period",
                "ann_cost",
                "net_ann_excess",
                "net_ann_vol",
                "net_sharpe",
            ):
                vals = [
                    rec.get(key, {}).get(k, {}).get(h, {}).get(metric)
                    for rec in seed_recs
                ]
                vals = [v for v in vals if v is not None]
                if vals:
                    cell[metric] = float(np.mean(vals))
            if cell:
                out[k][h] = cell
    return out


def _mean_over_horizons(per_horizon: dict[str, dict]) -> dict:
    """各期限汇总指标的等权均值（北极星：rank_ic_mean）。"""
    out: dict[str, Any] = {"n_horizons": len(per_horizon)}
    for key in ("ic_mean", "icir", "rank_ic_mean", "rank_icir"):
        vals = [s[key] for s in per_horizon.values() if s.get(key) is not None]
        if vals:
            out[key] = float(np.mean(vals))
    return out


def _filter_daily_dates(daily: pd.DataFrame, dates: Optional[pd.DatetimeIndex]) -> pd.DataFrame:
    if dates is None:
        return daily
    return daily[daily.index.isin(dates)]


# all.txt 混有 SH000300 等指数代码；全A评估须只保留 A 股（沪主板/科创/深主板/创业板）
_STOCK_PREFIX_RE = r"^(SH60|SH68|SZ00|SZ30)"


def _stock_only_mask(index: pd.MultiIndex) -> pd.Series:
    inst = index.get_level_values("instrument").str.upper()
    return pd.Series(inst.str.match(_STOCK_PREFIX_RE), index=index)


def evaluate_multi_horizon(
    cfg: dict,
    sessions: Sequence[tuple[str, Any]],
    pools: Sequence[str],
    *,
    horizons: Sequence[int],
    min_listing_days: int = 60,
    st_symbols: Optional[set[str]] = None,
    min_count: int = 20,
    segment: str = "test",
    eval_end: Optional[str] = None,
    eval_dates: Optional[pd.DatetimeIndex] = None,
    regime_monthly: Optional[pd.Series] = None,
    regime_pools: Sequence[str] = ("all",),
    tail_topk: Sequence[int] = (),
    head_k: Sequence[int] = (),
    exclude_limit_up: bool = False,
    min_amount: float = 0.0,
) -> dict:
    from qlib.data import D

    if sorted(set(horizons)) != sorted(horizons):
        raise ValueError("horizons must be unique")
    eval_start, effective_end = _effective_segment(cfg, segment, end_override=eval_end)
    models = [(seed, _load_model(session)) for session, seed in sessions]

    result: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": cfg.get("_config_path"),
        "eval_mode": "multi_horizon",
        "horizons": [int(h) for h in horizons],
        "eval_labels": {f"h{h}": _horizon_label_expr(h) for h in horizons},
        "eval_segment_name": segment,
        "effective_eval_segment": [eval_start, effective_end],
        "eval_dates_count": None if eval_dates is None else int(len(eval_dates)),
        "regime_pools": list(regime_pools) if regime_monthly is not None else [],
        "tail_topk": [int(k) for k in tail_topk],
        "head_k": [int(k) for k in head_k],
        "head_core": {"k": list(HEAD_K_CORE), "h": list(HEAD_H_CORE)},
        "head_universe": (
            "t+1 可成交（剔除次日涨停封板与零成交量）" if exclude_limit_up else "全池"
        ),
        "filters": {
            "min_listing_days": int(min_listing_days),
            "st_filter": "enabled" if st_symbols is not None else "unavailable",
            "min_amount": float(min_amount),
        },
        "primary_cell": {"k": PRIMARY_K, "h": PRIMARY_H},
        "cost_round_trip": COST_ROUND_TRIP,
        "turnover_reliable": bool(eval_dates is None),
        "sessions": [{"session": s, "seed": seed} for s, seed in sessions],
        "data_version": str(pd.Timestamp(D.calendar(start_time="2020-01-01")[-1]).date()),
        "st_filter": "enabled" if st_symbols is not None else "unavailable（未剔除 ST）",
        "pools": {},
    }

    for pool in pools:
        labels: dict[int, pd.Series] = {}
        mask = None
        for h in horizons:
            label = _fetch_label(
                pool, eval_start, effective_end, expression=_horizon_label_expr(h)
            )
            if mask is None:
                mask = pd.Series(True, index=label.index)
                if pool == "all":
                    mask = mask & _stock_only_mask(label.index)
                if min_listing_days:
                    mask = mask & _listing_age_mask(
                        label.index, pool, min_listing_days, effective_end
                    )
                if st_symbols:
                    inst = label.index.get_level_values("instrument").str.upper()
                    mask = mask & ~pd.Series(inst.isin(st_symbols), index=label.index)
                if min_amount > 0:
                    amt_ok = amount_mask(pool, eval_start, effective_end, min_amount)
                    mask = mask & amt_ok.reindex(label.index).fillna(False)
                print(
                    f"[{pool}] 过滤后保留 {int(mask.mean()*10000)/100:.2f}% "
                    f"(listing>={min_listing_days}, ST={'on' if st_symbols else 'off'}, "
                    f"amount>={min_amount:.0f})",
                    flush=True,
                )
            label = label[mask]
            labels[h] = label

        head_tradable = None
        if head_k and exclude_limit_up:
            head_tradable = entry_tradable_mask(pool, eval_start, effective_end)
            print(
                f"[{pool}] t+1 可成交率={head_tradable.mean():.4f} "
                f"（剔除 {int((~head_tradable).sum())} 个样本日）",
                flush=True,
            )

        dataset = _build_dataset(cfg, pool, segment=segment, end_override=eval_end)
        want_regime = regime_monthly is not None and pool in regime_pools
        pool_out: dict[str, Any] = {"seeds": {}}
        for seed, model in models:
            pred = _normalize_prediction(model.predict(dataset, segment=segment))
            rec: dict[str, Any] = {}
            for h in horizons:
                daily = _filter_daily_dates(
                    daily_ic(pred, labels[h], min_count=min_count), eval_dates
                )
                rec[f"h{h}"] = summarize_ic(daily)
                if want_regime:
                    regs = day_regime_map(pd.DatetimeIndex(daily.index), regime_monthly)
                    for reg in sorted(regs.dropna().unique()):
                        rec.setdefault("regimes", {}).setdefault(str(reg), {})[
                            f"h{h}"
                        ] = summarize_ic(daily[(regs == reg).to_numpy()])
            rec["mean_h"] = _mean_over_horizons({f"h{h}": rec[f"h{h}"] for h in horizons})
            if want_regime:
                for reg, per_h in rec.get("regimes", {}).items():
                    per_h["mean_h"] = _mean_over_horizons(
                        {k: v for k, v in per_h.items() if k.startswith("h")}
                    )
            if tail_topk and 1 in labels:
                rec["tail"] = {}
                for k in tail_topk:
                    series = daily_topk_excess(pred, labels[1], int(k), min_count=min_count)
                    if eval_dates is not None:
                        series = series[series.index.isin(eval_dates)]
                    entry: dict[str, Any] = {
                        "n_days": int(len(series)),
                        "ann_excess": float(series.mean() * 238) if len(series) else None,
                    }
                    if want_regime and len(series):
                        regs = day_regime_map(pd.DatetimeIndex(series.index), regime_monthly)
                        entry["regimes"] = {
                            str(reg): {
                                "n_days": int((regs == reg).sum()),
                                "ann_excess": float(series[(regs == reg).to_numpy()].mean() * 238),
                            }
                            for reg in sorted(regs.dropna().unique())
                        }
                    rec["tail"][f"top{k}"] = entry
            if head_k:
                rec["head"] = {}
                rec["head_regimes"] = {}
                rec["head_years"] = {}
                for h in horizons:
                    by_k = daily_head_panel(
                        pred,
                        labels[h],
                        head_k,
                        min_count=min_count,
                        tradable=head_tradable,
                    )
                    for k in head_k:
                        excess = by_k[int(k)]["excess"]
                        sets = by_k[int(k)]["sets"]
                        port = by_k[int(k)]["port"]
                        bench = by_k[int(k)]["bench"]
                        if eval_dates is not None:
                            excess = excess[excess.index.isin(eval_dates)]
                            port = port[port.index.isin(eval_dates)]
                            bench = bench[bench.index.isin(eval_dates)]
                            sets = {d: v for d, v in sets.items() if d in set(eval_dates)}
                        rec["head"].setdefault(str(int(k)), {})[str(int(h))] = (
                            summarize_head_series(
                                excess, int(h), sets=sets, k=int(k), port=port, bench=bench
                            )
                        )
                        if want_regime and len(excess):
                            regs = day_regime_map(
                                pd.DatetimeIndex(excess.index), regime_monthly
                            )
                            for reg in sorted(regs.dropna().unique()):
                                mask_r = (regs == reg).to_numpy()
                                days_r = set(pd.DatetimeIndex(excess.index)[mask_r])
                                rec["head_regimes"].setdefault(str(reg), {}).setdefault(
                                    str(int(k)), {}
                                )[str(int(h))] = summarize_head_series(
                                    excess[mask_r],
                                    int(h),
                                    sets=sets,
                                    k=int(k),
                                    turnover_days=days_r,
                                    port=port[mask_r],
                                    bench=bench[mask_r],
                                )
                        if len(excess):
                            years = np.asarray(pd.DatetimeIndex(excess.index).year)
                            for year in sorted(set(int(y) for y in years)):
                                mask_y = years == year
                                days_y = set(pd.DatetimeIndex(excess.index)[mask_y])
                                rec["head_years"].setdefault(str(year), {}).setdefault(
                                    str(int(k)), {}
                                )[str(int(h))] = summarize_head_series(
                                    excess[mask_y],
                                    int(h),
                                    sets=sets,
                                    k=int(k),
                                    turnover_days=days_y,
                                    port=port[mask_y],
                                    bench=bench[mask_y],
                                )
                rec["primary"] = (
                    rec["head"].get(str(PRIMARY_K), {}) or {}
                ).get(str(PRIMARY_H), {})
                rec["ir_by_h"] = {
                    str(int(h)): grid_mean_ir(rec["head"], HEAD_K_CORE, [h])
                    for h in HEAD_H_CORE
                }
            pool_out["seeds"][str(seed)] = rec

        seed_recs = list(pool_out["seeds"].values())
        seed_mean: dict[str, Any] = {}
        for block in [f"h{h}" for h in horizons] + ["mean_h"]:
            for key in ("rank_ic_mean", "rank_icir", "ic_mean", "icir"):
                vals = [
                    r[block][key]
                    for r in seed_recs
                    if r.get(block, {}).get(key) is not None
                ]
                if vals:
                    seed_mean[f"{block}.{key}"] = float(np.mean(vals))
        north = [
            r["mean_h"]["rank_ic_mean"]
            for r in seed_recs
            if r.get("mean_h", {}).get("rank_ic_mean") is not None
        ]
        if len(north) > 1:
            seed_mean["mean_h.rank_ic_mean_std"] = float(np.std(north, ddof=1))
        if head_k:
            seed_mean["head"] = _mean_head_grid(seed_recs, "head")
            seed_mean["ir_by_h"] = {
                str(int(h)): grid_mean_ir(seed_mean["head"], HEAD_K_CORE, [h])
                for h in HEAD_H_CORE
            }
            prim = (seed_mean.get("head") or {}).get(str(PRIMARY_K), {}).get(str(PRIMARY_H), {})
            if prim:
                seed_mean["primary"] = prim
            if any("head_regimes" in r for r in seed_recs):
                regs = sorted(
                    {reg for r in seed_recs for reg in r.get("head_regimes", {})}
                )
                seed_mean["head_regimes"] = {}
                for reg in regs:
                    subset = [
                        {"head": r["head_regimes"][reg]}
                        for r in seed_recs
                        if reg in r.get("head_regimes", {})
                    ]
                    seed_mean["head_regimes"][reg] = _mean_head_grid(subset, "head")
            if any("head_years" in r for r in seed_recs):
                years = sorted(
                    {yr for r in seed_recs for yr in r.get("head_years", {})},
                    key=int,
                )
                seed_mean["head_years"] = {}
                for year in years:
                    subset = [
                        {"head": r["head_years"][year]}
                        for r in seed_recs
                        if year in r.get("head_years", {})
                    ]
                    seed_mean["head_years"][year] = _mean_head_grid(subset, "head")
        pool_out["seed_mean"] = seed_mean
        result["pools"][pool] = pool_out
        prim = seed_mean.get("primary") or {}
        print(
            f"[{pool}] primary k{PRIMARY_K}h{PRIMARY_H} "
            f"net_ann={prim.get('net_ann_excess')} "
            f"net_vol={prim.get('net_ann_vol')} "
            f"net_sharpe={prim.get('net_sharpe')}",
            flush=True,
        )

    return result


def _init_qlib(cfg: dict) -> None:
    import qlib

    qlib.init(provider_uri=cfg["data"]["provider_uri"], region=cfg["data"].get("region", "cn"))


def _load_model(session: str, from_run: int = 1) -> Any:
    info = load_session_model_info(resolve_session_dir(session), from_run=from_run)
    with open(info["model_path"], "rb") as fh:
        return pickle.load(fh)


def _effective_boosting_iterations(model: Any) -> list[int]:
    """Return the effective tree count for every DoubleEnsemble sub-model."""
    boosters = getattr(model, "ensemble", None)
    if not boosters:
        raise ValueError("rolling model does not contain ensemble boosters")
    iterations = []
    for booster in boosters:
        best = int(getattr(booster, "best_iteration", 0) or 0)
        current = getattr(booster, "current_iteration", None)
        effective = best if best > 0 else int(current() if callable(current) else 0)
        if effective <= 0:
            raise ValueError("rolling booster has no positive iteration count")
        iterations.append(effective)
    return iterations


def _record_rolling_iterations(
    diagnostics: dict,
    *,
    seed: Any,
    fold: int,
    model: Any,
) -> None:
    """Record one seed/fold once and reject inconsistent repeated loads."""
    seed_key = str(seed)
    fold_key = str(fold)
    iterations = _effective_boosting_iterations(model)
    existing = diagnostics.setdefault(seed_key, {}).setdefault(
        fold_key, iterations
    )
    if existing != iterations:
        raise ValueError(
            f"rolling model iterations differ across pools: seed={seed}, fold={fold}"
        )


def _summarize_rolling_iterations(
    diagnostics: dict,
    *,
    max_rounds: int,
    early_stopping_rounds: Optional[int],
) -> dict:
    """Summarize whether the configured early stopping shortened training."""
    values = [
        int(value)
        for folds in diagnostics.values()
        for iterations in folds.values()
        for value in iterations
    ]
    if not values:
        raise ValueError("no rolling model iteration diagnostics")
    triggered = sum(value < max_rounds for value in values)
    return {
        "max_rounds": int(max_rounds),
        "early_stopping_rounds": early_stopping_rounds,
        "best_iterations": diagnostics,
        "booster_count": len(values),
        "triggered_count": triggered,
        "trigger_rate": float(triggered / len(values)),
        "mean_best_iteration": float(np.mean(values)),
        "min_best_iteration": min(values),
        "max_best_iteration": max(values),
    }


def _load_rolling_sessions(
    sessions: Sequence[tuple[str, Any]],
    calendar: pd.DatetimeIndex,
) -> dict[str, Any]:
    """Load and validate identical, complete rolling manifests."""
    calendar = pd.DatetimeIndex(calendar)
    canonical_folds = None
    canonical_step = None
    loaded = []
    seen_seeds = set()
    for session, requested_seed in sessions:
        session_dir = resolve_session_dir(session)
        meta_path = session_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("mode") != "rolling_train_only":
            raise ValueError(f"not a rolling train-only session: {session_dir}")
        seed = meta.get("seed") if requested_seed is None else requested_seed
        if requested_seed is not None and int(meta.get("seed")) != int(requested_seed):
            raise ValueError(f"seed mismatch for rolling session: {session_dir}")
        if seed in seen_seeds:
            raise ValueError(f"duplicate rolling seed: {seed}")
        seen_seeds.add(seed)

        folds = meta.get("rolling_folds") or []
        expected = int(meta.get("expected_fold_count") or 0)
        successful = [
            row
            for row in (meta.get("runs") or [])
            if row.get("status") == "success"
        ]
        if expected <= 0 or len(folds) != expected or len(successful) != expected:
            raise ValueError(
                f"rolling session does not contain all successful folds: {session_dir}"
            )
        successful_by_fold = {int(row.get("fold") or row.get("run")): row for row in successful}
        if set(successful_by_fold) != set(range(1, expected + 1)):
            raise ValueError(
                f"rolling session does not contain all successful folds: {session_dir}"
            )
        for fold in folds:
            fold_no = int(fold["fold"])
            if successful_by_fold[fold_no].get("segments") != fold.get("segments"):
                raise ValueError(f"run/fold segment mismatch: {session_dir}")

        if canonical_folds is None:
            canonical_folds = folds
            canonical_step = int(meta.get("step") or 0)
        elif folds != canonical_folds or int(meta.get("step") or 0) != canonical_step:
            raise ValueError("rolling fold manifest differs between seeds")
        loaded.append(
            {
                "session": str(session_dir.resolve()),
                "seed": seed,
            }
        )

    if not canonical_folds:
        raise ValueError("no rolling folds")
    previous_end = None
    for fold in canonical_folds:
        test_start, test_end = (str(v) for v in fold["segments"]["test"])
        start_pos = int(calendar.searchsorted(pd.Timestamp(test_start)))
        end_pos = int(calendar.searchsorted(pd.Timestamp(test_end)))
        if (
            start_pos >= len(calendar)
            or end_pos >= len(calendar)
            or calendar[start_pos] != pd.Timestamp(test_start)
            or calendar[end_pos] != pd.Timestamp(test_end)
            or start_pos > end_pos
        ):
            raise ValueError(f"invalid rolling test segment: {test_start}..{test_end}")
        if previous_end is not None and start_pos != previous_end + 1:
            raise ValueError("rolling prediction folds must be contiguous")
        previous_end = end_pos
    return {
        "folds": canonical_folds,
        "sessions": loaded,
        "step": canonical_step,
        "rolling_type": "expanding",
    }


def _handler_start_for_inference(test_start: str) -> str:
    """推理只需要 test 区间前约一年历史（Alpha158 最长窗口 60 交易日）。"""
    ts = pd.Timestamp(test_start) - pd.Timedelta(days=365)
    return ts.strftime("%Y-%m-%d")


def _segment_bounds(cfg: dict, segment: str) -> tuple[str, str]:
    if segment not in cfg["segments"]:
        raise ValueError(f"config does not define segment: {segment}")
    values = cfg["segments"][segment]
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError(f"segment {segment!r} must contain [start, end]")
    return str(values[0]), str(values[1])


def _effective_segment(
    cfg: dict,
    segment: str,
    *,
    end_override: Optional[str] = None,
) -> tuple[str, str]:
    start, end = _segment_bounds(cfg, segment)
    if end_override is None:
        return start, end
    override = str(end_override)
    if pd.Timestamp(override) > pd.Timestamp(end):
        raise ValueError(
            f"eval end {override} exceeds official segment end {end}"
        )
    if pd.Timestamp(override) < pd.Timestamp(start):
        raise ValueError(
            f"eval end {override} precedes segment start {start}"
        )
    return start, override


MTS_DATASET_CLASSES = {"MTSDatasetH", "LeanMTSDatasetH"}


def _build_mts_dataset(
    cfg: dict,
    pool: str,
    segment: str = "test",
    *,
    end_override: Optional[str] = None,
):
    """时序数据集（TRA 等）：按 config 原样构建指定池的推理数据集。

    与 DatasetH 分支不同，handler 的 start/fit 区间保持训练配置不变：
    RobustZScoreNorm 等统计量必须拟合在训练期（fit 区间在 test 之前，无泄漏），
    且序列窗口/特征暖场需要训练期同样的历史起点。
    """
    from qlib.utils import init_instance_by_config

    pool_cfg = copy.deepcopy(cfg)
    pool_cfg["data"]["instruments"] = pool
    handler = pool_cfg["data"]["handler"]
    handler.pop("instruments", None)
    start, end = _effective_segment(cfg, segment, end_override=end_override)
    handler["end_time"] = end

    handler_cfg = build_handler_kwargs(pool_cfg)
    dataset_cfg = copy.deepcopy(pool_cfg["dataset"])
    dataset_cfg.setdefault("kwargs", {})
    dataset_cfg["kwargs"]["handler"] = handler_cfg
    dataset_cfg["kwargs"]["segments"] = {segment: (start, end)}
    return init_instance_by_config(dataset_cfg)


def _build_dataset(
    cfg: dict,
    pool: str,
    segment: str = "test",
    *,
    end_override: Optional[str] = None,
):
    """按 config 的 handler 设置构建指定池、指定分段的推理 DatasetH。"""
    from qlib.utils import init_instance_by_config

    if str(cfg.get("dataset", {}).get("class", "")) in MTS_DATASET_CLASSES:
        return _build_mts_dataset(cfg, pool, segment=segment, end_override=end_override)

    pool_cfg = copy.deepcopy(cfg)
    pool_cfg["data"]["instruments"] = pool
    handler = pool_cfg["data"]["handler"]
    handler.pop("instruments", None)
    start, end = _effective_segment(
        cfg, segment, end_override=end_override
    )
    handler["start_time"] = _handler_start_for_inference(start)
    handler["end_time"] = end
    # ProcessInf 等 infer processors 无需拟合统计量；fit 区间仅为满足接口
    handler["fit_start_time"] = handler["start_time"]
    handler["fit_end_time"] = start

    handler_cfg = build_handler_kwargs(pool_cfg)
    dataset_cfg = {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": handler_cfg,
            "segments": {segment: (start, end)},
        },
    }
    return init_instance_by_config(dataset_cfg)


def _fetch_label(
    pool: str,
    start: str,
    end: str,
    *,
    expression: str = EVAL_LABEL_EXPR,
) -> pd.Series:
    """固定评测标签（不做任何截面归一化；IC 对逐日仿射变换不敏感）。"""
    from qlib.data import D

    df = D.features(
        D.instruments(pool),
        [expression],
        start_time=start,
        end_time=end,
    )
    s = df.iloc[:, 0]
    s.index = s.index.set_names(["instrument", "datetime"])
    return s.swaplevel().sort_index()


def amount_mask(pool: str, start: str, end: str, min_amount: float) -> pd.Series:
    """日成交额 >= min_amount（元）的可交易掩码。

    本地 cn_data 无 $amount；volume 单位为手，用未复权收盘还原：
    amount = $volume × ($close / $factor) × 100。
    用预测日 t 的成交额（决策时已知），NaN 视为不满足。
    """
    from qlib.data import D

    df = D.features(
        D.instruments(pool),
        ["$volume", "$close", "$factor"],
        start_time=start,
        end_time=end,
    )
    df.index = df.index.set_names(["instrument", "datetime"])
    df = df.swaplevel().sort_index()
    df.columns = ["volume", "close", "factor"]
    factor = df["factor"].replace(0, np.nan)
    amount = df["volume"] * (df["close"] / factor) * AMOUNT_LOT_SIZE
    return (amount >= float(min_amount)).fillna(False)


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


def _yearly_summaries(daily: pd.DataFrame) -> dict[str, dict]:
    return {
        str(year): summarize_ic(group)
        for year, group in daily.groupby(daily.index.year)
    }


def evaluate(
    cfg: dict,
    sessions: Sequence[tuple[str, Any]],
    pools: Sequence[str],
    *,
    min_listing_days: int = 60,
    st_symbols: Optional[set[str]] = None,
    min_count: int = 20,
    segment: str = "test",
    eval_label_expr: str = EVAL_LABEL_EXPR,
    eval_label_role: str = "fixed_1d",
    eval_end: Optional[str] = None,
) -> dict:
    from qlib.data import D

    eval_start, effective_end = _effective_segment(
        cfg, segment, end_override=eval_end
    )
    if eval_label_expr != EVAL_LABEL_EXPR and eval_label_role != "self":
        raise ValueError(
            "custom evaluation labels require eval_label_role='self'"
        )
    models = [(seed, _load_model(session)) for session, seed in sessions]

    result: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": cfg.get("_config_path"),
        "eval_label": eval_label_expr,
        "eval_label_role": eval_label_role,
        "eval_segment_name": segment,
        "eval_segment": list(_segment_bounds(cfg, segment)),
        "effective_eval_segment": [eval_start, effective_end],
        "sessions": [{"session": s, "seed": seed} for s, seed in sessions],
        "data_version": str(pd.Timestamp(D.calendar(start_time="2020-01-01")[-1]).date()),
        "st_filter": "enabled" if st_symbols is not None else "unavailable（未剔除 ST）",
        "pools": {},
    }
    if segment == "test":
        result["test_segment"] = [eval_start, effective_end]

    for pool in pools:
        label = _fetch_label(
            pool,
            eval_start,
            effective_end,
            expression=eval_label_expr,
        )
        if pool in POOLS_NEED_LISTING_FILTER:
            mask = _listing_age_mask(
                label.index,
                pool,
                min_listing_days,
                effective_end,
            )
            label = label[mask]
        if st_symbols:
            keep = ~label.index.get_level_values("instrument").str.upper().isin(st_symbols)
            label = label[keep]

        dataset = _build_dataset(
            cfg,
            pool,
            segment=segment,
            end_override=eval_end,
        )
        pool_out: dict[str, Any] = {"seeds": {}}
        for seed, model in models:
            pred = model.predict(dataset, segment=segment)
            if isinstance(pred, pd.DataFrame):
                pred = pred.iloc[:, 0]
            # TRA/MTSDatasetH 返回 (instrument, datetime) 顺序，统一到 (datetime, instrument)
            if not pd.api.types.is_datetime64_any_dtype(pred.index.get_level_values(0)):
                pred = pred.swaplevel().sort_index()
            pred.index = pred.index.set_names(["datetime", "instrument"])
            daily = daily_ic(pred, label, min_count=min_count)
            summary = summarize_ic(daily)
            summary["yearly"] = _yearly_summaries(daily)
            pool_out["seeds"][str(seed)] = summary

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


def _rolling_fold_config(cfg: dict, fold: dict) -> dict:
    out = copy.deepcopy(cfg)
    out["segments"] = copy.deepcopy(fold["segments"])
    handler = out["data"]["handler"]
    handler["fit_start_time"] = out["segments"]["train"][0]
    handler["fit_end_time"] = out["segments"]["train"][1]
    handler["end_time"] = out["segments"]["test"][1]
    return out


def _normalize_prediction(pred: pd.Series | pd.DataFrame) -> pd.Series:
    if isinstance(pred, pd.DataFrame):
        pred = pred.iloc[:, 0]
    if not pd.api.types.is_datetime64_any_dtype(pred.index.get_level_values(0)):
        pred = pred.swaplevel().sort_index()
    pred.index = pred.index.set_names(["datetime", "instrument"])
    return pred.sort_index()


def _period_summaries(
    daily: pd.DataFrame,
    folds: Sequence[dict],
) -> tuple[dict[str, dict], dict[str, dict]]:
    fold_stats = {}
    for fold in folds:
        start, end = fold["segments"]["test"]
        fold_stats[str(fold["fold"])] = {
            "test": [start, end],
            **summarize_ic(daily.loc[str(start):str(end)]),
        }
    yearly = _yearly_summaries(daily)
    return fold_stats, yearly


def evaluate_rolling(
    cfg: dict,
    sessions: Sequence[tuple[str, Any]],
    pools: Sequence[str],
    *,
    min_listing_days: int = 60,
    st_symbols: Optional[set[str]] = None,
    min_count: int = 20,
) -> dict:
    """Evaluate stitched walk-forward predictions through the canonical IC path."""
    from qlib.data import D

    official_start, official_end = _segment_bounds(cfg, "test")
    calendar = pd.DatetimeIndex(
        D.calendar(start_time=official_start, end_time=official_end)
    )
    manifest = _load_rolling_sessions(sessions, calendar)
    folds = manifest["folds"]
    first_start = str(folds[0]["segments"]["test"][0])
    last_end = str(folds[-1]["segments"]["test"][1])
    if (first_start, last_end) != (official_start, official_end):
        raise ValueError(
            "rolling folds must cover the complete official test segment"
        )

    result: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": cfg.get("_config_path"),
        "eval_label": EVAL_LABEL_EXPR,
        "eval_label_role": "fixed_1d",
        "eval_segment_name": "test",
        "eval_segment": [official_start, official_end],
        "effective_eval_segment": [official_start, official_end],
        "test_segment": [official_start, official_end],
        "sessions": manifest["sessions"],
        "rolling": {
            "type": manifest["rolling_type"],
            "step": manifest["step"],
            "fold_count": len(folds),
            "folds": folds,
        },
        "data_version": str(
            pd.Timestamp(D.calendar(start_time="2020-01-01")[-1]).date()
        ),
        "st_filter": (
            "enabled" if st_symbols is not None else "unavailable（未剔除 ST）"
        ),
        "pools": {},
    }

    expected_dates = set(calendar)
    iteration_diagnostics: dict[str, dict[str, list[int]]] = {}
    for pool in pools:
        label = _fetch_label(pool, official_start, official_end)
        if pool in POOLS_NEED_LISTING_FILTER:
            mask = _listing_age_mask(
                label.index,
                pool,
                min_listing_days,
                official_end,
            )
            label = label[mask]
        if st_symbols:
            keep = ~label.index.get_level_values("instrument").str.upper().isin(
                st_symbols
            )
            label = label[keep]

        predictions: dict[Any, list[pd.Series]] = {
            row["seed"]: [] for row in manifest["sessions"]
        }
        for fold in folds:
            fold_cfg = _rolling_fold_config(cfg, fold)
            dataset = _build_dataset(fold_cfg, pool, segment="test")
            for session_row in manifest["sessions"]:
                model = _load_model(
                    session_row["session"],
                    from_run=int(fold["fold"]),
                )
                _record_rolling_iterations(
                    iteration_diagnostics,
                    seed=session_row["seed"],
                    fold=int(fold["fold"]),
                    model=model,
                )
                pred = _normalize_prediction(
                    model.predict(dataset, segment="test")
                )
                predictions[session_row["seed"]].append(pred)
                del model
            del dataset
            gc.collect()

        pool_out: dict[str, Any] = {"seeds": {}}
        for seed, pieces in predictions.items():
            pred = pd.concat(pieces).sort_index()
            if pred.index.has_duplicates:
                raise ValueError(
                    f"duplicate rolling predictions for pool={pool}, seed={seed}"
                )
            predicted_dates = set(
                pd.DatetimeIndex(
                    pred.index.get_level_values("datetime").unique()
                )
            )
            if predicted_dates != expected_dates:
                missing = sorted(expected_dates - predicted_dates)
                extra = sorted(predicted_dates - expected_dates)
                raise ValueError(
                    f"rolling prediction date coverage mismatch for {pool}/{seed}: "
                    f"missing={missing[:3]}, extra={extra[:3]}"
                )
            daily = daily_ic(pred, label, min_count=min_count)
            summary = summarize_ic(daily)
            fold_stats, yearly = _period_summaries(daily, folds)
            summary["folds"] = fold_stats
            summary["yearly"] = yearly
            pool_out["seeds"][str(seed)] = summary

        seed_stats = [v for v in pool_out["seeds"].values() if v.get("n_days")]
        if seed_stats:
            pool_out["seed_mean"] = {
                key: float(
                    np.mean(
                        [row[key] for row in seed_stats if row.get(key) is not None]
                    )
                )
                for key in ("ic_mean", "icir", "rank_ic_mean", "rank_icir")
                if any(row.get(key) is not None for row in seed_stats)
            }
            rank_means = [
                row["rank_ic_mean"]
                for row in seed_stats
                if row.get("rank_ic_mean") is not None
            ]
            if len(rank_means) > 1:
                pool_out["seed_mean"]["rank_ic_mean_std"] = float(
                    np.std(rank_means, ddof=1)
                )
        result["pools"][pool] = pool_out
        print(f"[rolling/{pool}] {pool_out.get('seed_mean', {})}")
    model_kwargs = cfg["model"]["kwargs"]
    result["rolling"]["model_diagnostics"] = _summarize_rolling_iterations(
        iteration_diagnostics,
        max_rounds=int(model_kwargs["epochs"]),
        early_stopping_rounds=model_kwargs.get("early_stopping_rounds"),
    )
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
    p.add_argument(
        "--segment",
        choices=("train", "valid", "test"),
        default="test",
        help="评测分段；模型选择只能使用 valid",
    )
    p.add_argument("--output", required=True, type=Path, help="输出 JSON 路径")
    p.add_argument("--min-listing-days", type=int, default=60, help="全A 池最短上市交易日数")
    p.add_argument("--st-names", type=Path, default=None, help="可选 symbol,name CSV 用于剔除 ST")
    p.add_argument("--min-count", type=int, default=20, help="单日截面最少样本数")
    p.add_argument(
        "--eval-label",
        default=EVAL_LABEL_EXPR,
        help="评测标签；自标签诊断时必须同时指定 --eval-label-role self",
    )
    p.add_argument(
        "--eval-label-role",
        choices=("fixed_1d", "self"),
        default="fixed_1d",
    )
    p.add_argument(
        "--eval-end",
        default=None,
        help="可选有效评测截止日，不得晚于规范分段截止日",
    )
    p.add_argument(
        "--rolling",
        action="store_true",
        help="sessions are rolling parent sessions; stitch fold predictions",
    )
    p.add_argument(
        "--horizons",
        nargs="+",
        type=int,
        default=None,
        help="多期限评估（如 1 5 10 20 40）；启用 regime-adapt 扩展入口",
    )
    p.add_argument(
        "--date-list",
        type=Path,
        default=None,
        help="冻结测试日清单 CSV（date 列）；仅多期限入口支持",
    )
    p.add_argument(
        "--regime-labels",
        type=Path,
        default=None,
        help="月度风格标签 CSV（datetime,regime3）；仅多期限入口支持",
    )
    p.add_argument(
        "--regime-pools",
        nargs="+",
        default=["all"],
        help="做分风格切片的池（默认仅全A）",
    )
    p.add_argument(
        "--tail-topk",
        nargs="+",
        type=int,
        default=[],
        help="尾部诊断 top-k 列表（如 22 50），基于 h1 标签",
    )
    p.add_argument(
        "--head-k",
        nargs="+",
        type=int,
        default=[],
        help="头部网格 k 列表（如 10 22 50）；与 --horizons 组成 k×h 超额 IR / 年化 / 换手",
    )
    p.add_argument(
        "--exclude-limit-up",
        action="store_true",
        help="头部指标剔除 t+1 涨停封板/零成交量样本（top-k 候选池与等权基准同口径）",
    )
    p.add_argument(
        "--min-amount",
        type=float,
        default=0.0,
        help="评估日成交额下限（元）；0=不启用。1000 万传入 10000000",
    )
    args = p.parse_args(argv)
    if (
        args.eval_label != EVAL_LABEL_EXPR
        and args.eval_label_role != "self"
    ):
        p.error("custom --eval-label requires --eval-label-role self")
    if args.rolling and (
        args.segment != "test"
        or args.eval_label != EVAL_LABEL_EXPR
        or args.eval_end is not None
    ):
        p.error("--rolling requires full test segment and fixed 1-day label")
    if args.horizons is None and (
        args.date_list is not None
        or args.regime_labels is not None
        or args.tail_topk
        or args.head_k
        or args.exclude_limit_up
    ):
        p.error(
            "--date-list/--regime-labels/--tail-topk/--head-k/--exclude-limit-up "
            "require --horizons"
        )
    if args.exclude_limit_up and not args.head_k:
        p.error("--exclude-limit-up only affects head metrics; pass --head-k")
    if args.horizons is not None and (
        args.rolling or args.eval_label != EVAL_LABEL_EXPR
    ):
        p.error("--horizons is incompatible with --rolling / custom --eval-label")
    return args


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    cfg = load_config(args.config)
    _init_qlib(cfg)

    sessions = [_parse_session(s) for s in args.sessions]
    st_symbols = _load_st_symbols(args.st_names)
    if args.horizons is not None:
        result = evaluate_multi_horizon(
            cfg,
            sessions,
            args.pools,
            horizons=args.horizons,
            min_listing_days=args.min_listing_days,
            st_symbols=st_symbols,
            min_count=args.min_count,
            segment=args.segment,
            eval_end=args.eval_end,
            eval_dates=(
                load_date_list(args.date_list) if args.date_list is not None else None
            ),
            regime_monthly=(
                load_regime_monthly(args.regime_labels)
                if args.regime_labels is not None
                else None
            ),
            regime_pools=args.regime_pools,
            tail_topk=args.tail_topk,
            head_k=args.head_k,
            exclude_limit_up=args.exclude_limit_up,
            min_amount=args.min_amount,
        )
    elif args.rolling:
        result = evaluate_rolling(
            cfg,
            sessions,
            args.pools,
            min_listing_days=args.min_listing_days,
            st_symbols=st_symbols,
            min_count=args.min_count,
        )
    else:
        result = evaluate(
            cfg,
            sessions,
            args.pools,
            min_listing_days=args.min_listing_days,
            st_symbols=st_symbols,
            min_count=args.min_count,
            segment=args.segment,
            eval_label_expr=args.eval_label,
            eval_label_role=args.eval_label_role,
            eval_end=args.eval_end,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"written: {args.output}")


if __name__ == "__main__":
    main()
