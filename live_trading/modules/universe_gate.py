"""实盘选股宇宙过滤：与回测共用 backtest/scripts/universe_filter.py 的同一实现。

``build_keep_mask`` 内部是 ``from eval_ic_multi_pool import ...`` 这样的裸模块导入，
所以必须把 backtest/scripts 目录本身插进 sys.path，仅靠命名空间包导入
``backtest.scripts.universe_filter`` 会在运行时才炸。

单日调用是安全的：``recent_trading_mask`` 自己用 D.calendar 向前扩 window-1 个交易日
再滚动，``_listing_age_mask`` 用日历位置差，``amount_mask`` 只用当日。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_BACKTEST_SCRIPTS = Path(__file__).resolve().parents[2] / "backtest" / "scripts"
if str(_BACKTEST_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKTEST_SCRIPTS))
from universe_filter import (  # noqa: E402
    build_keep_mask,
    parse_universe_filter,
)

logger = logging.getLogger("live_trading.universe")

REQUIRED_FILTER_KEYS = (
    "st_daily",
    "min_amount",
    "min_listing_days",
    "min_recent_trading_days",
    "pool",
)


def filter_scores(
    scores: pd.Series,
    *,
    signal_date: str,
    raw_spec: dict,
    project_root: Path,
) -> tuple[pd.Series, dict[str, Any]]:
    """把回测那套四重宇宙过滤应用到单日分数上，被剔除的置 NaN。

    入参与返回都是单层 instrument 索引，与发布脚本下游约定一致。
    """
    if scores.empty:
        raise ValueError("cannot filter an empty score series")
    missing = [key for key in REQUIRED_FILTER_KEYS if key not in raw_spec]
    if missing:
        raise ValueError(
            f"universe_filter is missing required items: {', '.join(missing)}"
        )

    spec = parse_universe_filter(raw_spec, project_root=project_root)
    stamp = pd.Timestamp(signal_date)
    index = pd.MultiIndex.from_arrays(
        [[stamp] * len(scores), scores.index],
        names=["datetime", "instrument"],
    )
    # n_st_hits 挂在 Series 的 .attrs 上，转 numpy 会丢，所以先取再转
    keep_series = build_keep_mask(index, spec)
    n_st_hits = int(keep_series.attrs.get("n_st_hits", 0))
    keep = keep_series.to_numpy(dtype=bool)

    out = scores.astype(float).copy()
    out[~keep] = np.nan
    stats = {
        "signal_date": signal_date,
        "n_raw": int(len(scores)),
        "n_keep": int(keep.sum()),
        "n_st_hits": n_st_hits,
        "pool": spec.pool,
        "min_amount": spec.min_amount,
        "min_listing_days": spec.min_listing_days,
        "min_recent_trading_days": spec.min_recent_trading_days,
    }
    logger.info(
        "universe filter %s: kept %d / %d (pool=%s, ST hits %d)",
        signal_date, stats["n_keep"], stats["n_raw"], spec.pool, n_st_hits,
    )
    return out, stats
