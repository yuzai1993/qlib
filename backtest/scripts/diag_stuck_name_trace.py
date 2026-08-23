"""逐日追踪某只标的在 Phase S 回测中的持有过程，判断"为什么没卖掉"。

打印每个交易日的：持仓数量/价格/权重、当日是否有行情与成交量、模型分数分位。
用法：
    python backtest/scripts/diag_stuck_name_trace.py SZ300029 [seed] [结果 JSON]
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import pandas as pd

import qlib
from qlib.data import D

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ROOT / "backtest" / "result" / "phase_s_regime" / "all_top5d1" / "m0h20.json"
CASH_KEYS = {"cash", "now_account_value", "cash_delay"}


def main() -> None:
    inst = sys.argv[1]
    seed = sys.argv[2] if len(sys.argv) > 2 else "3000"
    res_json = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_JSON

    doc = json.loads(res_json.read_text())
    session = ROOT / doc["seeds"][seed]["session_dir"]
    link = json.loads((session / "run_01" / "mlruns_link.json").read_text())
    art = ROOT / link["backtest_artifacts"] / "artifacts"
    with (art / "portfolio_analysis" / "positions_normal_1day.pkl").open("rb") as fh:
        pos = pickle.load(fh)

    held = {}
    for d, p in pos.items():
        holding = getattr(p, "position", p)
        if inst in holding:
            held[pd.Timestamp(d)] = holding[inst]
    if not held:
        print(f"{inst} 未出现在 seed {seed} 的持仓中")
        return

    lo, hi = min(held), max(held)
    qlib.init(provider_uri=str(Path.home() / ".qlib/qlib_data/cn_data"), region="cn")
    start = str((lo - pd.Timedelta(days=30)).date())
    end = str((hi + pd.Timedelta(days=30)).date())
    px = D.features([inst], ["$close", "$volume", "$factor"], start_time=start, end_time=end)
    px = px.droplevel("instrument")

    print(f"seed {seed}  标的 {inst}")
    print(f"持仓区间 {lo.date()} ~ {hi.date()}（共 {len(held)} 天）\n")
    print(f"{'日期':>12} {'持仓价':>10} {'行情收盘':>10} {'成交量':>14} {'仓位权重':>9}  状态")

    prev_close = None
    for ts in sorted(set(px.index) | set(held)):
        rec = held.get(ts)
        row = px.loc[ts] if ts in px.index else None
        close = None if row is None else row["$close"]
        vol = None if row is None else row["$volume"]
        tradable = row is not None and pd.notna(vol) and vol > 0
        flag = []
        if rec is None:
            flag.append("未持有")
        if row is None or pd.isna(close):
            flag.append("无行情")
        elif not tradable:
            flag.append("零成交")
        if prev_close is not None and close is not None and pd.notna(close):
            chg = close / prev_close - 1
            if abs(chg) > 0.05:
                flag.append(f"跳空{chg:+.1%}")
        if close is not None and pd.notna(close):
            prev_close = close
        if rec is None and not flag[1:]:
            continue
        print(
            f"{str(ts.date()):>12} "
            f"{(rec['price'] if rec else float('nan')):>10.3f} "
            f"{(close if close is not None else float('nan')):>10.3f} "
            f"{(vol if vol is not None and pd.notna(vol) else float('nan')):>14.0f} "
            f"{(rec['weight'] if rec else float('nan')):>9.1%}  "
            + " ".join(flag)
        )


if __name__ == "__main__":
    main()
