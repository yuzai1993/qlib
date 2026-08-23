# ST 日频名单（stock_st + namechange）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Tushare `stock_st` + `namechange` 造一张**按交易日展开的 ST 日频名单**（含退市整理期），回测 / Phase M / 实盘发布共用同一份缓存，由现有 Tushare 日调度增量更新。

**Architecture:** 构建期把两路数据都压成日频长表 `st_daily.csv`（唯一事实来源）：`stock_st(trade_date=...)` 天然日频直接落盘；`namechange` 全量区间按 qlib 交易日历展开成日频，末端用 `stock_basic.delist_date` 截断。查询期只做一件事：`st_symbols_on(daily, as_of) -> set[str]`。**回测和实盘都查这张日频缓存**，不做区间边界推断。区间日历只作为人读审计产物，不参与过滤。

**Tech Stack:** Python 3.12、Tushare Pro `stock_st` / `namechange` / `stock_basic`、pandas、pytest、现有 `run_update_to_bin.sh` / 盘后 cron。

## Global Constraints

- 过滤唯一入口是日频名单 `st_daily.csv`。**禁止**在过滤侧引入「当前名字含 ST」的静态快照逻辑。
- **禁止** `stock_st(start_date=, end_date=)` 范围查询：实测 2026-04 一个月被截成 1000 行、只剩 5 个交易日。日更只按 `trade_date` 拉（单日 68–225 行）。
- **禁止** `namechange()` 不带日期做全量：实测正好返回 10000 行，是截断。全量必须按 `ann_date` 年度分片（见下表）。
- ST 判定只看 `name`，不看 `change_reason`：`name` 大写后含 `ST`，或含 `退`。`change_reason` 描述的是「变成这个名字的原因」，`撤销ST` 对应的 `name` 是正常名，按 reason 判会反向误杀。
- 「退」的两种形态都要覆盖：沪市前缀（`退市创兴`）、深/北市后缀（`天龙退`）。`"退" in name` 同时命中。
- `namechange` 含北交所（`920305.BJ`）。代码映射必须容忍 `.BJ` 而不抛错（qlib cn_data 无北交所，映射后自然不命中）。
- 日频缓存与审计区间都不进 git（走已有 `*.csv` gitignore）；消费者读磁盘路径。
- 不把 token 写入代码或配置；cron 继续 `source ~/.qlib_live_env`。
- 修改 `backtest/EXPERIMENT_STANDARD.md` 须用户明确批准；本计划只准备文案，不得擅自改规范。
- 实盘只加 ST 日频过滤，不加成交额/上市天数过滤。
- Python：`/opt/anaconda3/envs/qlib/bin/python`。

## 实测结论（2026-08-17，当前 token）

`stock_st`：

| 调用 | 结果 |
|---|---|
| `stock_st(trade_date=20260814)` | 206 行，名称全部含 ST，0 个「退」 |
| `stock_st(trade_date=20170103)` | 68 行，接口起点可用 |
| `stock_st(trade_date=20260618)` | 225 行，**无 300029**（整理期不在本接口） |
| `stock_st(ts_code=300029.SZ)` 无 offset | 1000 行截断；`offset=0,1000` 才拿到 1389 行 |

`namechange`（本次新增实测，解决整理期缺口）：

| 调用 | 结果 |
|---|---|
| `namechange()` 无参数 | **正好 10000 行 = 截断**，5644 个代码 |
| `namechange(start_date=YYYY0101,end_date=YYYY1231)` 1999–2026 逐年 | 合计 **13243 行**、5879 个代码，**无任何一年触及 10000** |
| 年度分片 vs 无参数全量差集 | 仅漏 1 行：`689009.SH 九号公司-UWD`（`ann_date` 为空，名字不含 ST/退）→ 分片后仍与无参数全量做并集兜底 |
| 分片结果 `name` 含 `ST` | 2801 段 |
| 分片结果 `name` 含 `退` | **153 段，`change_reason` 全为「退市整理期」，`end_date` 全为空** |
| 153 个整理期段 join `stock_basic(list_status='D')` | **153/153 拿到 `delist_date`**（含正在整理期的 `920305.BJ 云创退 20260709`） |
| 整理期 `start_date` 范围 | `20140421` ~ `20260709` |

`300029.SZ` 的 `namechange` 全历史：

```
天龙光电 20091225~20200914 / ST天龙 20200915~20210425 / *ST天龙 20210426~20220612
ST天龙 20220613~20250421 / *ST天龙 20250422~20260617 / 天龙退 20260618~None（delist_date 20260710）
```

→ ST 段与 `stock_st` 对齐；**整理期 `天龙退` 由 `namechange` + `delist_date` 补齐**，原方案的「整理期缺口」关闭。附带收益：`namechange` 把 ST 历史回溯到 1999 年，突破 `stock_st` 的 2017-01-03 起点。

## 两路数据的合并规则

| 情形 | 处理 |
|---|---|
| 某 (symbol, 交易日) 两路都有 | 以 `stock_st` 为准（交易所日频权威），`source="stock_st"` |
| 只有 `namechange` 展开命中 | 收录，`source="namechange"` |
| `namechange` 段 `end_date` 为空且是整理期 | 展开到 `delist_date`（含当日） |
| `namechange` 段 `end_date` 为空且 `delist_date` 也缺 | 展开到交易日历末日（宁可多过滤，不可漏买） |
| `namechange` 段早于 qlib 交易日历起点 | 用 `max(start, calendar[0])` 截断 |

## File Map

| 文件 | 职责 |
|---|---|
| `scripts/data_collector/tushare/st_calendar.py` | 拉取（两路）、展开、合并、查 as_of、CLI |
| `scripts/data_collector/tushare/st_daily.csv` | **日频名单，过滤唯一事实来源**（gitignore） |
| `scripts/data_collector/tushare/st_namechange_raw.csv` | `namechange` 原始区间快照，审计 + 免重复拉（gitignore） |
| `scripts/data_collector/tushare/st_calendar.csv` | 由日频压出的区间，**仅审计人读**（gitignore） |
| `scripts/data_collector/tushare/run_update_to_bin.sh` | 日更多一步 ST 名单 |
| `scripts/data_collector/tushare/README.md` | 文档 |
| `backtest/scripts/universe_filter.py` | 按日查日频名单，替换静态 ST 集合 |
| `backtest/scripts/eval_ic_multi_pool.py` | `--st-daily`；`--st-names` 仅保留为兼容报错 |
| `backtest/scripts/run_regime_phase_s.py` | `DEFAULT_UNIVERSE_FILTER` 改指向日频名单 |
| `live_trading/scripts/run_publish_signals.py` | 发布前按 `signal_date` 查同一份日频名单置 NaN |
| `tests/misc/test_st_calendar.py` | 展开/合并/查询/拉取契约 |
| `tests/backtest/test_universe_filter.py` | 按日 ST 掩码 |
| `tests/live_trading/test_run_publish_signals.py` | 发布侧不买当日 ST |

---

### Task 1: 日频展开、合并与 as_of 查询（无网络）

**Files:**
- Create: `scripts/data_collector/tushare/st_calendar.py`
- Test: `tests/misc/test_st_calendar.py`

**Interfaces:**
- Produces: `ts_code_to_qlib(ts_code: str) -> str`（`300029.SZ` → `SZ300029`，`920305.BJ` → `BJ920305`）
- Produces: `is_st_name(name: str) -> bool`（大写含 `ST` 或含 `退`）
- Produces: `expand_namechange(raw: pd.DataFrame, calendar: list[str], delist: dict[str, str]) -> pd.DataFrame`
- Produces: `merge_daily(*frames: pd.DataFrame) -> pd.DataFrame` 列 `symbol,date,name,source`，`stock_st` 胜出
- Produces: `st_symbols_on(daily: pd.DataFrame, as_of: str) -> set[str]`
- Produces: `compress_intervals(daily: pd.DataFrame, calendar: list[str]) -> pd.DataFrame`（审计用）
- Consumes: `raw` 需有 `ts_code,name,start_date,end_date`；`calendar` 为 `'YYYY-MM-DD'` 升序交易日；`delist` 为 `ts_code -> 'YYYYMMDD'`

- [ ] **Step 1: 写失败测试**

```python
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/data_collector/tushare"))

from st_calendar import (
    compress_intervals,
    expand_namechange,
    is_st_name,
    merge_daily,
    st_symbols_on,
    ts_code_to_qlib,
)

CAL = [
    "2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18",
    "2026-06-19", "2026-06-22", "2026-06-23",
]


def test_ts_code_to_qlib_covers_bj():
    assert ts_code_to_qlib("300029.SZ") == "SZ300029"
    assert ts_code_to_qlib("600000.SH") == "SH600000"
    assert ts_code_to_qlib("920305.BJ") == "BJ920305"


def test_is_st_name_covers_both_delisting_shapes():
    assert is_st_name("*ST天龙")
    assert is_st_name("ST天龙")
    assert is_st_name("天龙退")      # 深/北市后缀
    assert is_st_name("退市创兴")    # 沪市前缀
    assert not is_st_name("天龙光电")
    assert not is_st_name("平安银行")


def test_expand_namechange_uses_delist_date_when_end_is_null():
    raw = pd.DataFrame(
        {
            "ts_code": ["300029.SZ"],
            "name": ["天龙退"],
            "start_date": ["20260618"],
            "end_date": [None],
        }
    )
    out = expand_namechange(raw, CAL, {"300029.SZ": "20260619"})
    assert sorted(out["date"]) == ["2026-06-18", "2026-06-19"]
    assert set(out["symbol"]) == {"SZ300029"}
    assert set(out["source"]) == {"namechange"}


def test_expand_namechange_falls_back_to_calendar_end_without_delist_date():
    raw = pd.DataFrame(
        {
            "ts_code": ["300029.SZ"],
            "name": ["天龙退"],
            "start_date": ["20260622"],
            "end_date": [None],
        }
    )
    out = expand_namechange(raw, CAL, {})
    assert sorted(out["date"]) == ["2026-06-22", "2026-06-23"]


def test_expand_namechange_drops_non_st_segments_and_clips_to_calendar():
    raw = pd.DataFrame(
        {
            "ts_code": ["300029.SZ", "000001.SZ"],
            "name": ["*ST天龙", "平安银行"],
            "start_date": ["20090101", "20090101"],
            "end_date": ["20260616", "20260616"],
        }
    )
    out = expand_namechange(raw, CAL, {})
    assert set(out["symbol"]) == {"SZ300029"}
    assert out["date"].min() == "2026-06-15"
    assert out["date"].max() == "2026-06-16"


def test_merge_daily_prefers_stock_st_on_conflict():
    st = pd.DataFrame(
        {"symbol": ["SZ300029"], "date": ["2026-06-17"],
         "name": ["*ST天龙"], "source": ["stock_st"]}
    )
    nc = pd.DataFrame(
        {"symbol": ["SZ300029", "SZ300029"], "date": ["2026-06-17", "2026-06-18"],
         "name": ["*ST天龙", "天龙退"], "source": ["namechange", "namechange"]}
    )
    out = merge_daily(st, nc)
    assert len(out) == 2
    row = out[out["date"] == "2026-06-17"].iloc[0]
    assert row["source"] == "stock_st"


def test_st_symbols_on_is_exact_day_lookup():
    daily = pd.DataFrame(
        {
            "symbol": ["SZ300029", "SZ300029", "SH600000"],
            "date": ["2026-06-17", "2026-06-18", "2026-06-17"],
            "name": ["*ST天龙", "天龙退", "退市浦发"],
            "source": ["stock_st", "namechange", "namechange"],
        }
    )
    assert st_symbols_on(daily, "2026-06-17") == {"SZ300029", "SH600000"}
    assert st_symbols_on(daily, "2026-06-18") == {"SZ300029"}
    assert st_symbols_on(daily, "2026-06-19") == set()


def test_compress_intervals_splits_on_missing_trade_day():
    daily = pd.DataFrame(
        {
            "symbol": ["SZ300029", "SZ300029"],
            "date": ["2026-06-15", "2026-06-17"],
            "name": ["*ST天龙", "*ST天龙"],
            "source": ["stock_st", "stock_st"],
        }
    )
    out = compress_intervals(daily, CAL)
    assert [(r.start, r.end) for r in out.itertuples()] == [
        ("2026-06-15", "2026-06-15"),
        ("2026-06-17", "2026-06-17"),
    ]
```

- [ ] **Step 2: 跑测试，确认 RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_st_calendar.py -q`

Expected: FAIL，`st_calendar` 模块不存在。

- [ ] **Step 3: 最小实现**（先不要 CLI / 网络）

```python
from __future__ import annotations

import bisect
from pathlib import Path

import pandas as pd

DAILY_COLUMNS = ("symbol", "date", "name", "source")
INTERVAL_COLUMNS = ("symbol", "start", "end", "name", "source")
_EXCHANGES = {"SH", "SZ", "BJ"}
_SOURCE_RANK = {"stock_st": 0, "namechange": 1}


def ts_code_to_qlib(ts_code: str) -> str:
    code, _, exch = ts_code.strip().upper().partition(".")
    if exch not in _EXCHANGES or len(code) != 6 or not code.isdigit():
        raise ValueError(f"unsupported ts_code: {ts_code!r}")
    return f"{exch}{code}"


def is_st_name(name: str) -> bool:
    text = str(name).strip().upper()
    return "ST" in text or "退" in text


def _norm_day(value) -> str:
    return pd.Timestamp(str(value)).strftime("%Y-%m-%d")


def expand_namechange(raw, calendar, delist) -> pd.DataFrame:
    cal = [_norm_day(d) for d in calendar]
    if raw is None or raw.empty or not cal:
        return pd.DataFrame(columns=list(DAILY_COLUMNS))
    rows = []
    for rec in raw.itertuples():
        name = str(rec.name)
        if not is_st_name(name):
            continue
        start = max(_norm_day(rec.start_date), cal[0])
        raw_end = getattr(rec, "end_date", None)
        if pd.isna(raw_end) or raw_end in (None, "", "None"):
            fallback = delist.get(str(rec.ts_code))
            end = _norm_day(fallback) if fallback else cal[-1]
        else:
            end = _norm_day(raw_end)
        end = min(end, cal[-1])
        if start > end:
            continue
        lo = bisect.bisect_left(cal, start)
        hi = bisect.bisect_right(cal, end)
        symbol = ts_code_to_qlib(str(rec.ts_code))
        for day in cal[lo:hi]:
            rows.append({"symbol": symbol, "date": day,
                         "name": name, "source": "namechange"})
    return pd.DataFrame(rows, columns=list(DAILY_COLUMNS))


def merge_daily(*frames) -> pd.DataFrame:
    parts = [f for f in frames if f is not None and not f.empty]
    if not parts:
        return pd.DataFrame(columns=list(DAILY_COLUMNS))
    both = pd.concat(parts, ignore_index=True)[list(DAILY_COLUMNS)]
    both["_rank"] = both["source"].map(_SOURCE_RANK).fillna(9)
    both = both.sort_values(["symbol", "date", "_rank"])
    both = both.drop_duplicates(["symbol", "date"], keep="first")
    return both.drop(columns="_rank").sort_values(["date", "symbol"]).reset_index(drop=True)


def st_symbols_on(daily, as_of) -> set[str]:
    if daily is None or daily.empty:
        return set()
    return set(daily.loc[daily["date"] == _norm_day(as_of), "symbol"].astype(str))
```

`compress_intervals(daily, calendar)`：按 `symbol` 分组，用交易日历序号判断连续性（序号非 +1 即断段），`name`/`source` 取段内最后一日的值。**只用于审计输出，过滤侧不得调用。**

- [ ] **Step 4: 再跑测试，确认 GREEN**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_st_calendar.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/misc/test_st_calendar.py scripts/data_collector/tushare/st_calendar.py
git commit -m "feat(data): expand ST segments into a daily symbol index"
```

---

### Task 2: 两路拉取、落盘、CLI

**Files:**
- Modify: `scripts/data_collector/tushare/st_calendar.py`
- Test: `tests/misc/test_st_calendar.py`

**Interfaces:**
- Produces: `fetch_stock_st(pro, trade_date: str) -> pd.DataFrame`（只传 `trade_date`；`len>=1000` 抛错）
- Produces: `fetch_namechange(pro, years: range) -> pd.DataFrame`（年度分片 + 无日期全量并集去重；任一片 `len>=10000` 抛错）
- Produces: `fetch_delist_dates(pro) -> dict[str, str]`（`stock_basic(list_status='D')`）
- Produces: `load_daily(path) -> pd.DataFrame` / `load_trade_calendar(qlib_dir) -> list[str]`
- Produces: `update(*, pro, qlib_dir, dates=None, backfill=False, paths...) -> dict`
- Produces: CLI `st_calendar.py update [--backfill] [--dates ...] [--qlib-dir ...]`、`st_calendar.py audit`
- 默认路径：同目录 `st_daily.csv` / `st_namechange_raw.csv` / `st_calendar.csv`

- [ ] **Step 1: 追加失败测试（假 pro，不联网）**

```python
import pandas as pd
from st_calendar import fetch_namechange, fetch_stock_st, update


class _FakePro:
    def __init__(self, st_by_date=None, nc=None, delist=None):
        self.st_by_date = st_by_date or {}
        self.nc = nc
        self.delist = delist
        self.st_calls = []
        self.nc_calls = []

    def stock_st(self, **kw):
        assert "start_date" not in kw and "end_date" not in kw, "range query is forbidden"
        self.st_calls.append(kw)
        return self.st_by_date.get(kw["trade_date"], pd.DataFrame()).copy()

    def namechange(self, **kw):
        self.nc_calls.append(kw)
        return self.nc.copy() if self.nc is not None else pd.DataFrame()

    def stock_basic(self, **kw):
        return self.delist.copy() if self.delist is not None else pd.DataFrame()


def test_fetch_stock_st_normalises_and_rejects_page_limit():
    pro = _FakePro({
        "20260617": pd.DataFrame(
            {"ts_code": ["300029.SZ"], "name": ["*ST天龙"],
             "trade_date": ["20260617"], "type": ["ST"]}
        )
    })
    out = fetch_stock_st(pro, "2026-06-17")
    assert list(out.columns) == ["symbol", "date", "name", "source"]
    assert out.iloc[0]["symbol"] == "SZ300029"
    assert out.iloc[0]["date"] == "2026-06-17"
    assert out.iloc[0]["source"] == "stock_st"
    assert pro.st_calls == [{"trade_date": "20260617"}]

    big = pd.DataFrame({
        "ts_code": [f"{i:06d}.SZ" for i in range(1000)],
        "name": ["ST假"] * 1000,
        "trade_date": ["20260430"] * 1000,
        "type": ["ST"] * 1000,
    })
    try:
        fetch_stock_st(_FakePro({"20260430": big}), "2026-04-30")
    except ValueError as exc:
        assert "1000" in str(exc)
    else:
        raise AssertionError("expected truncation error")


def test_fetch_namechange_slices_by_year_and_rejects_10000():
    seg = pd.DataFrame({
        "ts_code": ["300029.SZ"], "name": ["天龙退"],
        "start_date": ["20260618"], "end_date": [None],
        "ann_date": ["20260610"], "change_reason": ["退市整理期"],
    })
    pro = _FakePro(nc=seg)
    out = fetch_namechange(pro, range(2025, 2027))
    # 2 个年度分片 + 1 次无日期全量兜底（ann_date 为空的行）
    assert len(pro.nc_calls) == 3
    assert any("start_date" not in c for c in pro.nc_calls)
    assert len(out) == 1

    big = pd.DataFrame({
        "ts_code": [f"{i:06d}.SZ" for i in range(10000)],
        "name": ["ST假"] * 10000, "start_date": ["20200101"] * 10000,
        "end_date": [None] * 10000, "ann_date": ["20200101"] * 10000,
        "change_reason": ["ST"] * 10000,
    })
    try:
        fetch_namechange(_FakePro(nc=big), range(2025, 2026))
    except ValueError as exc:
        assert "10000" in str(exc)
    else:
        raise AssertionError("expected truncation error")


def test_update_backfill_writes_daily_with_both_sources(tmp_path):
    qlib_dir = tmp_path / "cn_data"
    (qlib_dir / "calendars").mkdir(parents=True)
    (qlib_dir / "calendars/day.txt").write_text(
        "2026-06-17\n2026-06-18\n2026-06-19\n", encoding="utf-8"
    )
    pro = _FakePro(
        st_by_date={
            "20260617": pd.DataFrame(
                {"ts_code": ["300029.SZ"], "name": ["*ST天龙"],
                 "trade_date": ["20260617"], "type": ["ST"]}
            )
        },
        nc=pd.DataFrame({
            "ts_code": ["300029.SZ"], "name": ["天龙退"],
            "start_date": ["20260618"], "end_date": [None],
            "ann_date": ["20260610"], "change_reason": ["退市整理期"],
        }),
        delist=pd.DataFrame(
            {"ts_code": ["300029.SZ"], "name": ["天龙退"], "delist_date": ["20260619"]}
        ),
    )
    daily_path = tmp_path / "st_daily.csv"
    stats = update(
        pro=pro, qlib_dir=qlib_dir, backfill=True,
        daily_path=daily_path,
        raw_path=tmp_path / "st_namechange_raw.csv",
        interval_path=tmp_path / "st_calendar.csv",
    )
    daily = pd.read_csv(daily_path, dtype=str)
    got = {(r.date, r.symbol, r.source) for r in daily.itertuples()}
    assert ("2026-06-17", "SZ300029", "stock_st") in got
    assert ("2026-06-18", "SZ300029", "namechange") in got
    assert ("2026-06-19", "SZ300029", "namechange") in got
    assert stats["n_rows"] == 3


def test_update_without_cache_and_without_backfill_fails(tmp_path):
    qlib_dir = tmp_path / "cn_data"
    (qlib_dir / "calendars").mkdir(parents=True)
    (qlib_dir / "calendars/day.txt").write_text("2026-06-17\n", encoding="utf-8")
    try:
        update(pro=_FakePro(), qlib_dir=qlib_dir,
               daily_path=tmp_path / "missing.csv",
               raw_path=tmp_path / "raw.csv",
               interval_path=tmp_path / "cal.csv")
    except SystemExit as exc:
        assert "backfill" in str(exc)
    else:
        raise AssertionError("cron must not silently build a partial index")
```

- [ ] **Step 2: 跑测试，确认 RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_st_calendar.py -q`

Expected: FAIL，`fetch_stock_st` / `fetch_namechange` / `update` 未定义。

- [ ] **Step 3: 实现拉取与落盘**

要点（逐条都有测试对应）：

- `fetch_stock_st`：只传 `trade_date=YYYYMMDD`；空表返回空 DataFrame；`len>=1000` 抛错；输出统一成 `symbol,date,name,source="stock_st"`。
- `fetch_namechange`：
  - 逐年 `start_date=f"{y}0101", end_date=f"{y}1231"`，`fields="ts_code,name,start_date,end_date,ann_date,change_reason"`；任一年 `len>=10000` 抛错。
  - 补一次**不带日期**的调用做并集（兜住 `ann_date` 为空的行，实测 1 行）；这次调用**不检查 10000**（已知会满），只用于补差集。
  - 按 `ts_code,name,start_date` 去重后返回原始区间表，落 `st_namechange_raw.csv`。
- `fetch_delist_dates`：`stock_basic(exchange="", list_status="D", fields="ts_code,name,delist_date")` → `{ts_code: delist_date}`；缺失即缺失，由 `expand_namechange` 走「展开到日历末日」兜底。
- `load_trade_calendar(qlib_dir)`：优先 `calendars/day.txt`，回落 `calendars/day_future.txt`，取前 10 位。
- `update`：
  - `backfill=True`：`stock_st` 从 2017-01-03 拉到日历末日（约 2340 次调用，数分钟）；`namechange` 拉 `range(1999, 当年+1)`；`merge_daily` 后整表重写。
  - 增量（默认）：`st_daily.csv` 不存在 → `SystemExit("... run with --backfill first")`；存在则 `stock_st` 只补「缓存最大日的下一交易日 → 今天」，`namechange` 只拉当年与上一年（覆盖跨年公告），与旧缓存 merge。
  - 交易日返回 0 行视为异常抛错（非交易日不在 calendar 内，不会走到这里）。
  - 同时写 `st_calendar.csv`（`compress_intervals` 审计产物）并在 stats 里报 `n_rows/n_symbols/max_date/n_from_namechange`。
  - 复用 `collector.deco_retry` 或同等重试；token 只从环境读。
- CLI：`update` 子命令 + `audit` 子命令（打印指定日期的名单条数、两路来源占比、`stock_st` 与 `namechange` 在 2017 后的 ST 段一致率，不阻断）。

- [ ] **Step 4: 再跑测试，确认 GREEN**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_st_calendar.py -q`

Expected: PASS

- [ ] **Step 5: 真实 backfill 烟测（有网，不提交产物）**

```bash
/opt/anaconda3/envs/qlib/bin/python \
  scripts/data_collector/tushare/st_calendar.py update \
  --qlib-dir ~/.qlib/qlib_data/cn_data --backfill
```

Expected 断言（必须逐条肉眼确认）：

| 检查 | 期望 |
|---|---|
| `st_symbols_on(daily, "2026-04-24")` | 含 `SZ300029`（`*ST天龙`，来源 `stock_st`） |
| `st_symbols_on(daily, "2026-06-18")` | **含** `SZ300029`（`天龙退`，来源 `namechange`）← 这是本次改动的核心验收点 |
| `st_symbols_on(daily, "2026-07-10")` 之后 | 不再含 `SZ300029`（`delist_date` 之后截断） |
| `st_symbols_on(daily, "2015-06-30")` | 非空（`namechange` 补齐 2017 前历史） |
| `namechange` 来源行数 | > 0 且整理期段覆盖 153 个代码 |

- [ ] **Step 6: Commit（不要 add 任何 csv）**

```bash
git add scripts/data_collector/tushare/st_calendar.py tests/misc/test_st_calendar.py
git commit -m "feat(data): build ST daily index from stock_st and namechange"
```

---

### Task 3: 回测 / Phase M 按日过滤

**Files:**
- Modify: `backtest/scripts/universe_filter.py`
- Modify: `backtest/scripts/eval_ic_multi_pool.py`
- Modify: `backtest/scripts/run_regime_phase_s.py`
- Modify: `tests/backtest/test_universe_filter.py`
- Modify: `backtest/configs/regime-adapt/phase-s/*.yaml` 里的 `universe_filter.st_names`

**Interfaces:**
- Consumes: `load_daily` / `st_symbols_on`
- Produces: `UniverseFilterSpec.st_daily: Optional[Path]`（取代用于过滤的 `st_names`）
- Produces: `build_keep_mask` 按行日期查日频名单，不再 `inst.isin(静态集合)`
- `eval_ic_multi_pool.py`：新增 `--st-daily`；传 `--st-names` 直接 `p.error("--st-names 已废弃，改用 --st-daily")`
- 默认路径：`scripts/data_collector/tushare/st_daily.csv`
- 样本日期超出缓存 `max_date` → 抛错，禁止静默不过滤

- [ ] **Step 1: 改 `test_universe_filter.py`**

把 `test_parse_universe_filter_resolves_st_names` 改成解析 `st_daily`，并新增按日断言（含整理期日）：

```python
def test_build_keep_mask_is_date_aware(tmp_path):
    daily = tmp_path / "st_daily.csv"
    daily.write_text(
        "symbol,date,name,source\n"
        "SZ300029,2026-04-24,*ST天龙,stock_st\n"
        "SZ300029,2026-06-18,天龙退,namechange\n",
        encoding="utf-8",
    )
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2026-04-24", "2026-06-18"]), ["SZ300029", "SZ000001"]],
        names=["datetime", "instrument"],
    )
    spec = parse_universe_filter({"st_daily": str(daily), "pool": "csi300"},
                                 project_root=tmp_path)
    spec.min_amount = 0          # 避开取数，只测 ST 维度
    spec.min_listing_days = 0
    keep = build_keep_mask(idx, spec)
    assert bool(keep.loc[pd.Timestamp("2026-04-24"), "SZ300029"]) is False
    assert bool(keep.loc[pd.Timestamp("2026-06-18"), "SZ300029"]) is False
    assert bool(keep.loc[pd.Timestamp("2026-04-24"), "SZ000001"]) is True


def test_build_keep_mask_rejects_dates_beyond_cache(tmp_path):
    daily = tmp_path / "st_daily.csv"
    daily.write_text(
        "symbol,date,name,source\nSZ300029,2026-04-24,*ST天龙,stock_st\n",
        encoding="utf-8",
    )
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-08-14"), "SZ000001")], names=["datetime", "instrument"]
    )
    spec = parse_universe_filter({"st_daily": str(daily), "pool": "csi300"},
                                 project_root=tmp_path)
    spec.min_amount = 0
    spec.min_listing_days = 0
    with pytest.raises(ValueError, match="st_daily"):
        build_keep_mask(idx, spec)
```

- [ ] **Step 2: 跑测试，确认 RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_universe_filter.py -q`

Expected: FAIL（仍是静态 `st_names` 名称匹配）。

- [ ] **Step 3: 改过滤实现**

`UniverseFilterSpec` 增加 `st_daily`；`parse_universe_filter` 解析（相对仓库根）。为避免路径脏，查询函数留在 `st_calendar.py`，`universe_filter.py` 这样 import：

```python
_TUSHARE_DIR = EXP_ROOT / "scripts" / "data_collector" / "tushare"
if str(_TUSHARE_DIR) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_DIR))
from st_calendar import load_daily
```

掩码（按日 map，避免逐行 Python 循环）：

```python
daily = load_daily(spec.st_daily) if spec.st_daily else None
if daily is not None:
    dates = pd.DatetimeIndex(norm_idx.get_level_values("datetime")).strftime("%Y-%m-%d")
    inst = pd.Index(norm_idx.get_level_values("instrument")).astype(str).str.upper()
    max_date = daily["date"].max()
    if len(dates) and dates.max() > max_date:
        raise ValueError(
            f"st_daily covers up to {max_date}, sample needs {dates.max()}; "
            "run st_calendar.py update"
        )
    banned = set(zip(daily["date"].astype(str), daily["symbol"].astype(str)))
    flag = pd.Series(
        [(d, i) not in banned for d, i in zip(dates, inst)], index=norm_idx
    )
    keep = keep & flag
```

`FilterStats`：`st_filter="daily"`，`n_st_symbols` 改为「样本窗口内被命中的 (日, 标的) 数」。

`eval_ic_multi_pool.py` 里按日掩码同样改掉「全窗口一个 set」。`DEFAULT_UNIVERSE_FILTER` 与 phase-s yaml：`st_daily: scripts/data_collector/tushare/st_daily.csv`，删除 `st_names`。

`backtest/configs/regime-adapt/st_names.csv` 与 `backtest/scripts/dump_st_names.py` 保留但在文件头加一行注释标记「已废弃，仅供历史对照」。

- [ ] **Step 4: 跑测试，确认 GREEN**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_universe_filter.py tests/misc/test_st_calendar.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/scripts/universe_filter.py backtest/scripts/eval_ic_multi_pool.py \
  backtest/scripts/run_regime_phase_s.py backtest/configs/regime-adapt \
  backtest/scripts/dump_st_names.py tests/backtest/test_universe_filter.py
git commit -m "feat(backtest): filter ST by daily index instead of static names"
```

---

### Task 4: 实盘发布查同一份日频缓存

**Files:**
- Modify: `live_trading/scripts/run_publish_signals.py`
- Test: `tests/live_trading/test_run_publish_signals.py`

**Interfaces:**
- Consumes: `load_daily(path)` + `st_symbols_on(daily, signal_date)` — **与回测同一份 `st_daily.csv`、同一个查询函数**，不查 `stock_basic` 当前名字、不查区间
- Produces: `apply_st_daily(scores: pd.Series, daily: pd.DataFrame, as_of: str) -> pd.Series`（命中行置 NaN；持仓股同样置 NaN 以便 dropout 换出）
- 默认路径同回测；可用环境变量 `QLIB_ST_DAILY` 覆盖
- 缓存文件缺失 → `SystemExit`；缓存 `max_date < signal_date` → `SystemExit`（提示先跑 `st_calendar.py update`）。不得静默跳过
- 落库 predictions 用过滤后的分数，监控与下单一致
- 只改分数，不加成交额/上市天数过滤

- [ ] **Step 1: 写失败测试**

```python
import numpy as np
import pandas as pd
import pytest

from live_trading.scripts.run_publish_signals import apply_st_daily

DAILY = pd.DataFrame(
    {
        "symbol": ["SZ300029", "SZ300029"],
        "date": ["2026-04-24", "2026-06-18"],
        "name": ["*ST天龙", "天龙退"],
        "source": ["stock_st", "namechange"],
    }
)


def test_publish_nans_st_symbols_on_signal_date():
    scores = pd.Series([1.0, 2.0, 3.0], index=["SZ000001", "SZ300029", "SH600000"])
    out = apply_st_daily(scores, DAILY, "2026-04-24")
    assert np.isnan(out["SZ300029"])
    assert out["SZ000001"] == 1.0
    # 整理期同样必须屏蔽（namechange 来源）
    assert np.isnan(apply_st_daily(scores, DAILY, "2026-06-18")["SZ300029"])


def test_publish_refuses_stale_cache():
    scores = pd.Series([1.0], index=["SZ000001"])
    with pytest.raises(SystemExit, match="st_daily"):
        apply_st_daily(scores, DAILY, "2026-08-14")
```

- [ ] **Step 2: 跑测试，确认 RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_run_publish_signals.py -q`

Expected: FAIL，`apply_st_daily` 不存在。

- [ ] **Step 3: 实现并接到 `main()`**

在 `get_signal_date_and_scores` 之后、`save_predictions` 之前调用：

```python
def apply_st_daily(scores, daily, as_of):
    as_of = pd.Timestamp(as_of).strftime("%Y-%m-%d")
    if daily is None or daily.empty or daily["date"].max() < as_of:
        raise SystemExit(
            f"st_daily covers up to {'<empty>' if daily is None or daily.empty else daily['date'].max()}, "
            f"signal_date={as_of}; run st_calendar.py update"
        )
    banned = st_symbols_on(daily, as_of)
    out = scores.astype(float).copy()
    out.loc[out.index.astype(str).str.upper().isin(banned)] = np.nan
    return out
```

`main()` 里解析路径：`Path(os.environ.get("QLIB_ST_DAILY", "scripts/data_collector/tushare/st_daily.csv"))`，不存在则 `SystemExit("ST daily index missing; run st_calendar.py update")`。日志打印 `signal_date`、命中数量、命中中属于当前持仓的代码（便于人工核对为什么换股）。

- [ ] **Step 4: 跑测试，确认 GREEN**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_run_publish_signals.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add live_trading/scripts/run_publish_signals.py tests/live_trading/test_run_publish_signals.py
git commit -m "feat(live): block ST and delisting names using the daily ST index"
```

---

### Task 5: 挂进 Tushare 日调度与文档

**Files:**
- Modify: `scripts/data_collector/tushare/run_update_to_bin.sh`
- Modify: `scripts/data_collector/tushare/README.md`
- Modify: `live_trading/README.md`（发布依赖日频名单；盘后 update 必须先成功）
- 不改 `run_postclose_cron.sh` 阶段顺序：它已先跑 `run_update_to_bin.sh` 再 `stock_names` 再 report，名单会在 publish 之前更新

**Interfaces:**
- 在复权巡检之后、汇总之前增加一步：
  `"$PYTHON" scripts/data_collector/tushare/st_calendar.py update --qlib-dir ~/.qlib/qlib_data/cn_data`
- 失败走现有 `alert_fail "ST日频名单日更"`，不阻断后续步骤，最终非 0
- 首次部署须先手工 `--backfill`；缓存缺失时 CLI 必须非 0 退出（Task 2 已有测试兜住）

- [ ] **Step 1: 改 `run_update_to_bin.sh`**

```bash
log "===== ST daily index update ====="
if "$PYTHON" scripts/data_collector/tushare/st_calendar.py update \
  --qlib-dir ~/.qlib/qlib_data/cn_data
then
  log "st_calendar update OK"
else
  status=$?
  alert_fail "ST日频名单日更" \
    "st_calendar.py update 退出码：${status}

最近日志：
----------------------------------------
$(tail -n 80 "$logfile" || echo "无法读取日志")"
fi
```

- [ ] **Step 2: README** 写明：两路来源与优先级、`stock_st` 只按 `trade_date`（1000 行拒收）、`namechange` 必须年度分片（10000 行拒收）、整理期靠 `namechange` + `delist_date`、首次 `--backfill`、产物路径、回测与实盘查同一份 `st_daily.csv`、盘后顺序依赖。
- [ ] **Step 3: 语法与回归**

Run: `bash -n scripts/data_collector/tushare/run_update_to_bin.sh`

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_st_calendar.py tests/backtest/test_universe_filter.py tests/live_trading/test_run_publish_signals.py tests/live_trading/test_operational_wrappers.py -q`

Expected: PASS；wrapper 测试不应因 update 脚本多一段而失败（它们 stub 整个 `run_update_to_bin.sh`）。

- [ ] **Step 4: Commit**

```bash
git add scripts/data_collector/tushare/run_update_to_bin.sh \
  scripts/data_collector/tushare/README.md live_trading/README.md
git commit -m "feat(data): refresh ST daily index in the Tushare daily job"
```

---

### Task 6: 规范文案（先批准再改）

**Files:**
- Modify: `backtest/EXPERIMENT_STANDARD.md`（仅在用户明确批准后）

准备替换第 5.1.2 节过滤第 1 条：

旧：`ST 名单：backtest/configs/regime-adapt/st_names.csv（--st-names）`

新：`ST 日频名单：scripts/data_collector/tushare/st_daily.csv（--st-daily）。来源 Tushare stock_st（按交易日，2017-01-03 起）+ namechange（区间展开，回溯至 1999，并用 stock_basic.delist_date 覆盖退市整理期）；同名含「退」的整理期股票一并剔除。回测与实盘发布查同一份缓存。`

- [ ] **Step 1: 把上述文案给用户看，等明确批准。**
- [ ] **Step 2: 批准后再改文件并 commit：`docs: point ST filter at the daily ST index`。**

---

## 部署顺序（实施完成后）

1. 手工 `--backfill` 一次，逐条确认 Task 2 Step 5 的验收表，尤其 `2026-06-18` 命中 `SZ300029`。
2. 确认 `run_update_to_bin.sh` 日更只追加新交易日 + 刷新当年 `namechange`。
3. 下一次盘后发布前确认 `st_daily.csv` 的 `max_date` ≥ 最新 `signal_date`。
4. 重跑受影响的 Phase S 回测（2026 冻结问题的验证）后再谈结论；旧结果不可与新过滤混表。
5. 不要把 `st_daily.csv` / `st_namechange_raw.csv` / `st_calendar.csv` 提交进 git。

## Self-Review

1. **Spec coverage:** 用户两点均落地 —— 实盘走 `load_daily` + `st_symbols_on`（Task 4，与回测同函数同文件）；`namechange` 补充且「退」计入 ST（`is_st_name` 含 `退`，Task 1 测试直接断言 `天龙退` / `退市创兴` 两种形态，Task 2 验收表把 `2026-06-18` 命中设为核心验收点）。
2. **原缺口关闭:** 旧方案的「整理期不覆盖」已由实测的 `namechange` 153 段 + `stock_basic` 153/153 `delist_date` 解决，Global Constraints 里不再保留该缺口。
3. **截断防线:** `stock_st` 1000 行拒收、`namechange` 年度分片 10000 行拒收、无日期全量只做差集兜底 —— 三条都有测试。
4. **Placeholder scan:** 无 TBD；空缓存、过期缓存、缺 `delist_date`、日历外区间都有明确行为。
5. **Types:** `st_symbols_on(daily, as_of) -> set[str]`；`symbol` 一律 qlib 形式（`SZ300029` / `BJ920305`），与发布/回测 index 一致。
6. **剩余风险:** `namechange` 的 ST 区间按公告生效自然日给出，2017 前无 `stock_st` 可交叉校验；Task 2 的 `audit` 子命令输出 2017 后两路一致率作为间接质量信号，但不阻断。
