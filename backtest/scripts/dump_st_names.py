"""已废弃：静态 ST 名称表，仅供历史对照。

过滤请改用 scripts/data_collector/tushare/st_daily.csv（--st-daily）。
本脚本导出的是当前名字快照，不能覆盖退市整理期，也没有交易日维度。
"""

优先 AkShare（无需 token）；失败再尝试环境变量 TUSHARE_TOKEN。
写出 CSV：symbol,name（qlib 代码，如 SZ000001）。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

OUT_DEFAULT = Path("backtest/configs/regime-adapt/st_names.csv")


def _to_qlib(code: str) -> str | None:
    raw = str(code).strip().upper()
    if "." in raw:
        num, exch = raw.split(".", 1)
        if exch in ("SH", "SZ") and num.isdigit():
            return f"{exch}{num}"
    if raw.startswith(("SH", "SZ")) and raw[2:].isdigit():
        return raw
    if raw.isdigit() and len(raw) == 6:
        if raw.startswith(("6", "9")):
            return f"SH{raw}"
        if raw.startswith(("0", "3")):
            return f"SZ{raw}"
    return None


def from_akshare() -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_info_a_code_name()
    cols = {c.lower(): c for c in df.columns}
    code_col = cols.get("code") or cols.get("代码") or df.columns[0]
    name_col = cols.get("name") or cols.get("名称") or df.columns[1]
    rows = []
    for code, name in zip(df[code_col], df[name_col]):
        inst = _to_qlib(str(code))
        if inst:
            rows.append({"symbol": inst, "name": str(name)})
    return pd.DataFrame(rows)


def from_tushare() -> pd.DataFrame:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN empty")
    import tushare as ts

    pro = ts.pro_api(token)
    frames = []
    for status in ("L", "D", "P"):
        part = pro.stock_basic(exchange="", list_status=status, fields="ts_code,name")
        if part is not None and not part.empty:
            frames.append(part)
    if not frames:
        raise RuntimeError("tushare stock_basic empty")
    df = pd.concat(frames, ignore_index=True)
    rows = []
    for code, name in zip(df["ts_code"], df["name"]):
        inst = _to_qlib(str(code))
        if inst:
            rows.append({"symbol": inst, "name": str(name)})
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(OUT_DEFAULT))
    args = p.parse_args()
    errors = []
    df = None
    for name, fn in (("akshare", from_akshare), ("tushare", from_tushare)):
        try:
            df = fn()
            print(f"source={name} rows={len(df)}")
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    if df is None or df.empty:
        raise SystemExit("无法获取股票名称：" + " | ".join(errors))
    st = int(df["name"].astype(str).str.upper().str.contains("ST").sum())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values("symbol").to_csv(out, index=False)
    print(f"written {out}  total={len(df)}  ST={st}")


if __name__ == "__main__":
    main()
