"""选股决策时的评估宇宙过滤：按日 ST 名单。

ST 过滤唯一入口是 `st_daily.csv`（Tushare stock_st + namechange 展开）。
禁止用「当前名字含 ST」的静态快照。成交额 / 上市天数过滤不在本模块启用
（调用方把 min_amount / min_listing_days 置 0 即跳过）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
import pandas as pd

SCRIPTS_DIR = Path(__file__).resolve().parent
EXP_ROOT = SCRIPTS_DIR.parents[1]
_TUSHARE_DIR = EXP_ROOT / "scripts" / "data_collector" / "tushare"
if str(_TUSHARE_DIR) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_DIR))
from st_calendar import load_daily  # noqa: E402

PredLike = Union[pd.Series, pd.DataFrame]


@dataclass
class UniverseFilterSpec:
    st_daily: Optional[Path]
    min_amount: float
    min_listing_days: int
    pool: str
    min_recent_trading_days: int = 0


@dataclass
class FilterStats:
    n_days: int
    n_raw: int
    n_keep: int
    keep_rate: float
    eligible_min: int
    eligible_median: float
    eligible_max: int
    eligible_mean: float
    sample_day: Optional[str]
    sample_day_eligible: Optional[int]
    sample_day_raw: Optional[int]
    pool: str
    min_amount: float
    min_listing_days: int
    st_filter: str
    n_st_symbols: int
    min_recent_trading_days: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_days": self.n_days,
            "n_raw": self.n_raw,
            "n_keep": self.n_keep,
            "keep_rate": self.keep_rate,
            "eligible_min": self.eligible_min,
            "eligible_median": self.eligible_median,
            "eligible_max": self.eligible_max,
            "eligible_mean": self.eligible_mean,
            "sample_day": self.sample_day,
            "sample_day_eligible": self.sample_day_eligible,
            "sample_day_raw": self.sample_day_raw,
            "pool": self.pool,
            "min_amount": self.min_amount,
            "min_listing_days": self.min_listing_days,
            "st_filter": self.st_filter,
            "n_st_symbols": self.n_st_symbols,
            "min_recent_trading_days": self.min_recent_trading_days,
        }

    def summary(self) -> str:
        return (
            f"pool={self.pool} keep={self.n_keep}/{self.n_raw} "
            f"({self.keep_rate:.2%}) 日可选 "
            f"min/med/max={self.eligible_min}/{self.eligible_median:.0f}/{self.eligible_max} "
            f"sample {self.sample_day} raw={self.sample_day_raw} keep={self.sample_day_eligible} "
            f"ST={self.st_filter}({self.n_st_symbols}) "
            f"amount>={self.min_amount:.0f} listing>={self.min_listing_days} "
            f"recent_traded>={self.min_recent_trading_days}"
        )


def parse_universe_filter(raw: dict, *, project_root: Optional[Path] = None) -> UniverseFilterSpec:
    root = project_root or EXP_ROOT
    st = raw.get("st_daily") or raw.get("st_names")
    st_path: Optional[Path] = None
    if raw.get("st_names") and not raw.get("st_daily"):
        raise ValueError("universe_filter.st_names 已废弃，改用 st_daily")
    if st:
        st_path = Path(st).expanduser()
        if not st_path.is_absolute():
            st_path = root / st_path
        if not st_path.is_file():
            raise FileNotFoundError(f"universe_filter.st_daily 不存在: {st_path}")
    return UniverseFilterSpec(
        st_daily=st_path,
        min_amount=float(raw.get("min_amount") or 0.0),
        min_listing_days=int(raw.get("min_listing_days") or 0),
        pool=str(raw.get("pool") or "all"),
        min_recent_trading_days=int(raw.get("min_recent_trading_days") or 0),
    )


def normalize_dt_inst_index(index: pd.Index) -> tuple[pd.MultiIndex, bool]:
    """把预测/掩码索引规范成 (datetime, instrument)，返回 (新索引, 是否交换过层级)。"""
    if not isinstance(index, pd.MultiIndex) or index.nlevels != 2:
        raise ValueError("pred/mask index must be a 2-level MultiIndex")
    names = [n or "" for n in index.names]
    if "datetime" in names and "instrument" in names:
        if names[0] == "datetime":
            return index.set_names(["datetime", "instrument"]), False
        return index.swaplevel().set_names(["datetime", "instrument"]), True
    lvl0 = index.get_level_values(0)
    if pd.api.types.is_datetime64_any_dtype(lvl0):
        return index.set_names(["datetime", "instrument"]), False
    return index.swaplevel().set_names(["datetime", "instrument"]), True


def apply_keep_mask(pred: PredLike, keep: pd.Series) -> PredLike:
    is_df = isinstance(pred, pd.DataFrame)
    scores = pred.iloc[:, 0] if is_df else pred
    pred_idx, _ = normalize_dt_inst_index(scores.index)
    keep_idx, _ = normalize_dt_inst_index(keep.index)
    keep_s = pd.Series(np.asarray(keep, dtype=bool), index=keep_idx)
    aligned = keep_s.reindex(pred_idx).fillna(False)
    values = np.asarray(scores, dtype=float).copy()
    values[~aligned.to_numpy()] = np.nan
    if is_df:
        out = pred.copy()
        out.iloc[:, 0] = values
        return out
    return pd.Series(values, index=scores.index, name=getattr(scores, "name", None))


def summarize_keep_mask(keep: pd.Series, spec: UniverseFilterSpec, n_st: int) -> FilterStats:
    idx, _ = normalize_dt_inst_index(keep.index)
    flag = pd.Series(np.asarray(keep, dtype=bool), index=idx)
    dates = idx.get_level_values("datetime")
    daily_keep = flag.groupby(dates).sum()
    daily_raw = flag.groupby(dates).size()
    sample_day = None
    sample_keep = None
    sample_raw = None
    if len(daily_keep):
        sample_ts = daily_keep.index[len(daily_keep) // 2]
        sample_day = str(pd.Timestamp(sample_ts).date())
        sample_keep = int(daily_keep.loc[sample_ts])
        sample_raw = int(daily_raw.loc[sample_ts])
    return FilterStats(
        n_days=int(len(daily_keep)),
        n_raw=int(flag.size),
        n_keep=int(flag.sum()),
        keep_rate=float(flag.mean()) if flag.size else 0.0,
        eligible_min=int(daily_keep.min()) if len(daily_keep) else 0,
        eligible_median=float(daily_keep.median()) if len(daily_keep) else 0.0,
        eligible_max=int(daily_keep.max()) if len(daily_keep) else 0,
        eligible_mean=float(daily_keep.mean()) if len(daily_keep) else 0.0,
        sample_day=sample_day,
        sample_day_eligible=sample_keep,
        sample_day_raw=sample_raw,
        pool=spec.pool,
        min_amount=spec.min_amount,
        min_listing_days=spec.min_listing_days,
        st_filter="daily" if spec.st_daily is not None else "off",
        n_st_symbols=int(n_st),
        min_recent_trading_days=spec.min_recent_trading_days,
    )


def build_keep_mask(index: pd.MultiIndex, spec: UniverseFilterSpec) -> pd.Series:
    """按评估日查日频 ST 名单，构造 (datetime, instrument) 布尔掩码。"""
    norm_idx, _ = normalize_dt_inst_index(index)
    keep = pd.Series(True, index=norm_idx)
    n_hit = 0
    if spec.st_daily is not None:
        daily = load_daily(spec.st_daily)
        dates = pd.DatetimeIndex(norm_idx.get_level_values("datetime")).strftime("%Y-%m-%d")
        inst = pd.Index(norm_idx.get_level_values("instrument")).astype(str).str.upper()
        if daily is None or daily.empty:
            raise ValueError("st_daily is empty; run st_calendar.py update")
        max_date = str(daily["date"].max())
        if len(dates) and str(dates.max()) > max_date:
            raise ValueError(
                f"st_daily covers up to {max_date}, sample needs {dates.max()}; "
                "run st_calendar.py update"
            )
        banned = set(
            zip(daily["date"].astype(str), daily["symbol"].astype(str).str.upper())
        )
        flag = pd.Series(
            [(d, i) not in banned for d, i in zip(dates, inst)], index=norm_idx
        )
        n_hit = int((~flag).sum())
        keep = keep & flag
    keep.attrs["n_st_hits"] = n_hit
    return keep.astype(bool)


def filter_pred(pred: PredLike, spec: UniverseFilterSpec) -> tuple[PredLike, FilterStats]:
    scores = pred.iloc[:, 0] if isinstance(pred, pd.DataFrame) else pred
    keep = build_keep_mask(scores.index, spec)
    stats = summarize_keep_mask(keep, spec, int(keep.attrs.get("n_st_hits", 0)))
    return apply_keep_mask(pred, keep), stats


class UniverseFilteredModel:
    """包装已有模型的 predict，使 SignalRecord / TopkDropout 看到过滤后分数。"""

    def __init__(self, model: Any, spec: UniverseFilterSpec):
        self._model = model
        self._spec = spec
        self._keep: Optional[pd.Series] = None
        self.filter_stats: Optional[dict[str, Any]] = None

    def predict(self, dataset, *args, **kwargs):
        pred = self._model.predict(dataset, *args, **kwargs)
        scores = pred.iloc[:, 0] if isinstance(pred, pd.DataFrame) else pred
        if self._keep is None:
            self._keep = build_keep_mask(scores.index, self._spec)
            stats = summarize_keep_mask(
                self._keep, self._spec, int(self._keep.attrs.get("n_st_hits", 0))
            )
            self.filter_stats = stats.as_dict()
            print(f"[universe_filter] {stats.summary()}", flush=True)
        return apply_keep_mask(pred, self._keep)

    def __getattr__(self, name: str):
        return getattr(self._model, name)


def wrap_model_predict(
    model: Any,
    raw_spec: dict,
    *,
    project_root: Optional[Path] = None,
) -> UniverseFilteredModel:
    spec = parse_universe_filter(raw_spec, project_root=project_root)
    return UniverseFilteredModel(model, spec)
