"""诊断 Phase S 回测中的"组合冻结"缺陷。

持仓里只要有一只当日无行情（停牌）的标的，TopkDropoutStrategy 会把它选为
当日唯一的卖出目标（NaN 分数被排在最后），而该卖单因不可成交被跳过；
卖不出 → 没有释放现金 → 当日买单也不发生，整个组合零成交。

用法：
    python backtest/scripts/diag_phase_s_freeze.py [结果 JSON 路径]
默认检查 backtest/result/phase_s_regime/all_top5d1/m0h20.json。
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


def _positions(session_dir: Path) -> dict:
    link = json.loads((session_dir / "run_01" / "mlruns_link.json").read_text())
    art = ROOT / link["backtest_artifacts"] / "artifacts"
    with (art / "portfolio_analysis" / "positions_normal_1day.pkl").open("rb") as fh:
        return pickle.load(fh)


def _held(pos) -> list[str]:
    h = getattr(pos, "position", pos)
    return sorted(k for k in h if k not in CASH_KEYS)


def main() -> None:
    res_json = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSON
    doc = json.loads(res_json.read_text())
    seeds = list(doc["seeds"])
    qlib.init(provider_uri=str(Path.home() / ".qlib/qlib_data/cn_data"), region="cn")

    print(f"结果文件: {res_json}")
    print(f"策略: {doc.get('strategy')}   窗口: {doc.get('backtest_window')}\n")

    print("=== 各年零成交天数（冻结天数 / 交易日）===")
    header = f"{'年':>6} " + " ".join(f"{s:>10}" for s in seeds)
    print(header)
    reports = {}
    for s in seeds:
        d = pd.read_csv(
            ROOT / doc["seeds"][s]["session_dir"] / "run_01" / "report_normal.csv",
            parse_dates=["datetime"],
        ).set_index("datetime")
        reports[s] = d.iloc[1:]
    years = sorted({y for d in reports.values() for y in d.index.year.unique()})
    for y in years:
        cells = []
        for s in seeds:
            d = reports[s]
            sub = d[d.index.year == y]
            cells.append(f"{int((sub['turnover'] < 1e-9).sum()):3d}/{len(sub):3d}")
        print(f"{y:>6} " + " ".join(f"{c:>10}" for c in cells))

    print("\n=== 列联表：持仓含停牌标的 × 当日是否零成交 ===")
    for s in seeds:
        pos_obj = _positions(ROOT / doc["seeds"][s]["session_dir"])
        d = reports[s]
        frozen = set(d.index[d["turnover"] < 1e-9])
        held = {pd.Timestamp(dt): _held(p) for dt, p in pos_obj.items()}
        held = {k: v for k, v in held.items() if k in set(d.index)}
        insts = sorted({i for v in held.values() for i in v})
        px = D.features(
            insts, ["$close"],
            start_time=str(min(held).date()), end_time=str(max(held).date()), freq="day",
        )
        close = px["$close"].unstack(level=0)
        rows = []
        for ts in sorted(held):
            nan_names = [
                i for i in held[ts]
                if i not in close.columns or ts not in close.index or pd.isna(close.at[ts, i])
            ]
            rows.append({"frozen": ts in frozen, "halted": len(nan_names) > 0,
                         "names": ",".join(nan_names)})
        df = pd.DataFrame(rows)
        a = int((df.halted & df.frozen).sum())
        b = int((df.halted & ~df.frozen).sum())
        c = int((~df.halted & df.frozen).sum())
        e = int((~df.halted & ~df.frozen).sum())
        print(f"\n  seed {s} (n={len(df)})")
        print(f"    含停牌 & 零成交 = {a:4d}    含停牌 & 有成交 = {b:4d}")
        print(f"    无停牌 & 零成交 = {c:4d}    无停牌 & 有成交 = {e:4d}")
        vc = df[df.halted]["names"].value_counts()
        for name, cnt in vc.head(5).items():
            print(f"      锁仓标的 {name}: {cnt} 天")


if __name__ == "__main__":
    main()
