"""评估"退市/长期停牌导致持仓永久卖不掉"的风险，以及 ST 名单能否兜底。"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

import qlib
from qlib.data import D

ROOT = Path(__file__).resolve().parents[2]
ST_CSV = ROOT / "backtest" / "configs" / "regime-adapt" / "st_names.csv"
WIN_START, WIN_END = "2020-08-03", "2026-07-31"
CULPRITS = ["SZ300029", "SZ300391", "SH600292", "SZ300069", "SZ002066", "SH600696"]


def load_st() -> tuple[set[str], list[str]]:
    rows = list(csv.reader(ST_CSV.open()))
    header = rows[0] if rows else []
    syms = set()
    for r in rows[1:]:
        if r:
            syms.add(r[0].strip().upper())
    return syms, header


def main() -> None:
    st, header = load_st()
    print(f"st_names.csv 表头={header}  条数={len(st)}")
    print(f"肇事股是否在 ST 名单: " + ", ".join(f"{c}={c in st}" for c in CULPRITS))

    qlib.init(provider_uri=str(Path.home() / ".qlib/qlib_data/cn_data"), region="cn")

    print("\n=== 肇事股行情起止与停牌段 ===")
    for c in CULPRITS:
        df = D.features([c], ["$close", "$volume"], start_time=WIN_START, end_time=WIN_END, freq="day")
        if df.empty:
            print(f"  {c}: 窗口内无数据")
            continue
        sub = df.loc[c]
        valid = sub["$close"].dropna()
        nan_days = int(sub["$close"].isna().sum())
        print(f"  {c}: 行={len(sub)} 有效={len(valid)} NaN={nan_days} "
              f"末个有效日={valid.index.max():%Y-%m-%d} 末价={valid.iloc[-1]:.4f}")

    # 全A 池：窗口结束前就"永久消失"（摘牌）的股票有多少
    print("\n=== 全A 池退市统计 ===")
    insts = D.list_instruments(
        D.instruments("all"), start_time=WIN_START, end_time=WIN_END, as_list=False
    )
    end_ts = pd.Timestamp(WIN_END)
    delisted = []
    for sym, spans in insts.items():
        last = max(pd.Timestamp(e) for _, e in spans)
        if last < end_ts - pd.Timedelta(days=10):
            delisted.append((sym, last))
    delisted.sort(key=lambda x: x[1])
    print(f"  池内股票总数={len(insts)}   窗口内摘牌(instruments 结束早于窗口末)={len(delisted)}")
    in_st = sum(1 for s, _ in delisted if s in st)
    print(f"  其中在 ST 名单里的={in_st} ({in_st/max(len(delisted),1)*100:.1f}%)")
    print("  最近 12 只摘牌：")
    for s, d in delisted[-12:]:
        print(f"    {s}  末日={d:%Y-%m-%d}  在ST名单={s in st}")


if __name__ == "__main__":
    main()
