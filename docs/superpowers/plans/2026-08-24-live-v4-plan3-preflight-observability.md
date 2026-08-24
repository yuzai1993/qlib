# 实盘 v4 真阶梯 · 计划三：上线前置代码与可观测性

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「全A + v4 五种子 + 真阶梯 + 盘后固定价格 + 抵销」这套组合切到实盘之前，所有**可在 Mac 上测试、零实盘风险**的代码与可观测性缺口补齐，让计划四的切换手册有可依赖的观测面和回退依据。

**Architecture:** 三条独立的线。**观测线**（任务 1–4）：修掉一条会把抵销成功误报成 CRIT 的规则，为回执补两个字段把 bridge 的定价证据送到 Mac，然后在 `report` 阶段落地两项新检查——收盘价逐单对账与分侧加权成交率。**装机线**（任务 5–6）：把阻断切换的两处硬编码打开（快照白名单、运行时渲染），其中渲染 `ENABLE_LADDER_NETTING` 是让计划二全部工作在生产里真正生效的前提。**诊断线**（任务 7）：量化封板/停牌导致的名字集合偏离频率。

**Tech Stack:** Python 3（`/opt/anaconda3/envs/qlib/bin/python`）、SQLite、pytest、Qlib（`D.features`）、pandas/numpy。QMT bridge 侧代码是 Python 2/3 兼容的单文件脚本，跑在 Windows 上，**只能靠 Mac 上的单测覆盖**。

## Global Constraints

- 本计划**不做任何实盘动作**：不改 cron、不渲染装机、不动券商账户、不提交授权 marker。全部切换动作归计划四。
- 本计划**不改变回测**。`CohortLadderStrategy` 及其配置一律不动；任务 7 只读不写。
- 抵销的不变量（spec 4.4）：`netted_qty` 只落盘，绝不参与 `delta_qty` / `delta_amount` / `fee_delta`。抵销的全部意义就是「不动持仓、不动现金、不计费」。
- 失败关闭优先于可用性：新增门禁拿不到证据时报 CRIT，不得静默通过。
- QMT bridge 单文件（`live_trading/qmt_strategy/qmt_signal_bridge.py`）保持无第三方依赖、Python 2/3 兼容写法（不用 f-string、不用 `dataclass`）。
- 每个任务独立提交，提交信息用英文，遵循仓库现有风格（`fix:` / `feat:` / `docs:`）。
- 运行测试固定用 `/opt/anaconda3/envs/qlib/bin/python -m pytest`。macOS 下禁止用 heredoc/stdin 跑会触发 Qlib 并行取数的代码（见 `.cursor/rules/qlib-shell-multiprocessing.mdc`）。

## 前置事实（已核准，实施时不必重查）

| 事实 | 位置 |
|---|---|
| `_REJECT_STATUS = {"REJECTED", "ERROR"}`，`SKIPPED` **不计入**拒单率 | `live_trading/modules/pipeline_monitor.py:14` |
| `ALL_ORDERS_SKIPPED` 是 **CRIT**，条件为当日 LIVE 回执全 `SKIPPED` 且 `filled_qty == 0` | `live_trading/modules/pipeline_monitor.py:171-189` |
| `_oversold_codes` 按 `filled_qty` 累计，抵销腿恒为 0，**不受影响** | `live_trading/modules/pipeline_monitor.py:502-513` |
| `fill_ratio` 在全仓库**不存在**任何实现 | — |
| cron 顺序是 `import` → `postmarket` → **`update`（Qlib 数据更新）** → `stock_names` → `report` | `live_trading/run_postclose_cron.sh:60-83` |
| 因此 `postmarket` 阶段**拿不到** T 日权威收盘价；`report` 阶段才有 | 同上 |
| `run_report(date, calendar, recorder, store, config, notifier)` 返回的 findings 会被 `dispatch_findings` 派发 | `live_trading/scripts/run_monitor.py:511,703` |
| `fetch_close_prices(qlib_codes, date) -> dict` 取未复权收盘价 `$close/$factor` | `live_trading/scripts/run_monitor.py:83-98` |
| `netting_close` 只存在于 bridge 侧 `batch.orders` → `{BRIDGE_ROOT}/state/active_*.json`；**Mac 无任何 reader**，回执里也没有 | `qmt_signal_bridge.py:3276-3285` |
| `_write_fill` 的 `requested_qty = int(order.get("quantity", 0))` | `qmt_signal_bridge.py:782` |
| `ENABLE_LADDER_NETTING` 仓库默认 **False**，且 `render_main_source` **不渲染它** | `qmt_signal_bridge.py:54`；`live_trading/scripts/render_qmt_runtime.py:32-48` |
| `_SNAPSHOT_REQUEST_STRATEGIES` 白名单只有 csi1000 主策略与 pr49 探针 | `qmt_signal_bridge.py:1516-1519` |

### 抵销后各字段的确切取值（任务 2、4 的口径依据）

`_net_ladder_pair(due_shares, sized)` 的三种结果，以及 `_write_fill` 落到回执的 `requested_qty`：

| 情形 | `net_quantity` | `netted_qty` | 回执 `requested_qty` | 本计划定义的 `intended_qty` |
|---|---|---|---|---|
| 买 > 卖（`sized > due`） | `sized - due`（BUY 腿） | `due` | `sized - due` | `sized` |
| 买 < 卖（`sized < due`） | `due - sized`（SELL 腿） | `sized` | `due - sized` | 买腿 `sized` / 卖腿 `due` |
| 完全抵销（相等） | `0` | `due` | 买腿被显式改写为 `netted_qty`（`:3443`），卖腿保持原值 | `sized` = `due` |

**关键结论：`requested_qty` 在部分抵销时是「市场腿」的股数，在完全抵销时又被改写成全额，口径不一致，不能直接做成交率分母。** 所以任务 2 显式补一个 `intended_qty`（阶梯本意要的股数，抵销前），任务 4 的成交率统一为
`Σ(applied_qty + netted_qty) / Σ(intended_qty)`。

---

## Task 1: 抵销成功不得再触发 ALL_ORDERS_SKIPPED

一个三只票全额抵销的交易日，回执形状恰好是「全 `SKIPPED` 且 `filled_qty == 0`」——正好命中
`ALL_ORDERS_SKIPPED` 的 CRIT 条件。抵销越成功，告警越响。这条规则必须认识到
`netted_qty > 0` 意味着**股数确实动了**，只是没走市场。

**Files:**
- Modify: `live_trading/modules/pipeline_monitor.py:171-189`
- Test: `tests/live_trading/test_pipeline_monitor.py`

**Interfaces:**
- Consumes: 无（本任务是起点）
- Produces: 无新签名。`check_postmarket` 签名不变：
  `check_postmarket(trade_date, batches, reconciles, fills, prev_positions, reject_rate=0.5) -> list`

- [ ] **Step 1: 给测试的 `_fill` helper 加上 `netted_qty`**

`tests/live_trading/test_pipeline_monitor.py:23-27` 现有 helper 不带该字段。改成：

```python
def _fill(status="FILLED", side="BUY", code="600000.SH", qty=100, mode="LIVE",
          batch_id=BATCH["batch_id"], message="", netted_qty=0):
    return {"batch_id": batch_id, "mode": mode, "status": status, "side": side,
            "stock_code": code, "filled_qty": qty, "message": message,
            "netted_qty": netted_qty}
```

- [ ] **Step 2: 写失败测试**

追加到 `tests/live_trading/test_pipeline_monitor.py`：

```python
def test_a_fully_netted_ladder_day_is_not_an_all_skipped_incident():
    """三只票全额抵销：回执全 SKIPPED 且 filled_qty=0，但股数确实动了。"""
    fills = [
        _fill(status="SKIPPED", side="SELL", code="600000.SH", qty=0,
              netted_qty=300, message="netted against same-day buy"),
        _fill(status="SKIPPED", side="BUY", code="600000.SH", qty=0,
              netted_qty=300, message="netted against same-day due sell"),
    ]
    f = check_postmarket(
        "2026-07-14", [dict(BATCH, mode="LIVE")],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        fills, prev_positions={"600000.SH": 300},
    )
    assert "ALL_ORDERS_SKIPPED" not in _rules(f)


def test_all_skipped_with_nothing_netted_is_still_critical():
    """真的什么都没发生：既没成交也没抵销，仍须 CRIT。"""
    fills = [
        _fill(status="SKIPPED", side="BUY", code="600000.SH", qty=0,
              message="official close unavailable"),
        _fill(status="SKIPPED", side="BUY", code="000001.SZ", qty=0,
              message="official close unavailable"),
    ]
    f = check_postmarket(
        "2026-07-14", [dict(BATCH, mode="LIVE")],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        fills, prev_positions={},
    )
    assert "ALL_ORDERS_SKIPPED" in _rules(f)


def test_a_partially_netted_day_with_one_real_fill_is_not_an_incident():
    fills = [
        _fill(status="SKIPPED", side="SELL", code="600000.SH", qty=0,
              netted_qty=200, message="netted against same-day buy"),
        _fill(status="FILLED", side="BUY", code="600000.SH", qty=100),
    ]
    f = check_postmarket(
        "2026-07-14", [dict(BATCH, mode="LIVE")],
        {BATCH["batch_id"]: {"planned": 2, "terminal": 2, "missing": 0}},
        fills, prev_positions={"600000.SH": 200},
    )
    assert "ALL_ORDERS_SKIPPED" not in _rules(f)
```

- [ ] **Step 3: 跑测试确认前两个新用例里第一个失败**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py -k netted -v
```

预期：`test_a_fully_netted_ladder_day_is_not_an_all_skipped_incident` FAIL
（断言失败：`ALL_ORDERS_SKIPPED` 在 rules 里）；另两个 PASS。

- [ ] **Step 4: 改规则**

`live_trading/modules/pipeline_monitor.py`，把 171-189 行的 `all(...)` 条件加上抵销判断：

```python
    if (
        planned_live > 0
        and len(live_fills) >= planned_live
        and all(
            f.get("status") == "SKIPPED"
            and int(f.get("filled_qty") or 0) == 0
            # 抵销掉的股数是真的动了，只是没走市场（spec 4.4）。
            # 少了这一条，抵销越成功这条 CRIT 就越响。
            and int(f.get("netted_qty") or 0) == 0
            for f in live_fills
        )
    ):
```

- [ ] **Step 5: 跑全套 pipeline_monitor 测试**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py -v
```

预期：全部 PASS（含既有的 `test_postmarket_*`）。

- [ ] **Step 6: 提交**

```bash
git add live_trading/modules/pipeline_monitor.py \
        tests/live_trading/test_pipeline_monitor.py
git commit -m "fix: stop reporting a fully netted ladder day as all-orders-skipped"
```

---

## Task 2: 回执补 netting_close 与 intended_qty

spec 第 8 节把「次日用权威收盘价逐单对账 `LADDER_NET` 里记录的 `C`」定为**接受旧价定量尾部
风险的前提，不得省略**。但现在这条对账**无法实现**：`netting_close` 只写进 bridge 本地的
`active_*.json`，Mac 侧没有任何 reader；回执里只有 `netted_qty`。本任务补上这条传输通道。

同时补 `intended_qty`——阶梯本意要的股数（抵销前）。理由见前置事实表：`requested_qty` 在
部分抵销与完全抵销两种情形下口径不一致，不能当成交率分母。

两个字段共用同一条链路（bridge 回执 → JSONL → `FillEvent` → `fills` 表），所以放在一个任务里。

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py:3276-3285`（写字段）、`:778-802`（`_write_fill`）
- Modify: `live_trading/modules/signal_schema.py:101-118`（`FillEvent`）、`:249+`（`validate_fill`）
- Modify: `live_trading/modules/fill_importer.py:237-255`（建表）、`:467-476`（迁移）、`:698-716`（重建）、`:1693-1717`（upsert）
- Test: `tests/live_trading/test_qmt_bridge_logic.py`、`tests/live_trading/test_signal_schema.py`、`tests/live_trading/test_fill_importer.py`

**Interfaces:**
- Consumes: Task 1 无产出依赖
- Produces:
  - `FillEvent` 新增两个带默认值的字段：`netting_close: float = 0.0`、`intended_qty: int = 0`
  - `fills` 表新增两列：`netting_close REAL NOT NULL DEFAULT 0`、`intended_qty INTEGER NOT NULL DEFAULT 0`
  - `recorder.get_fills_by_dates(trade_dates)` 返回的 dict 因此多这两个键——Task 3 与 Task 4 都消费它

- [ ] **Step 1: 写 bridge 侧失败测试**

追加到 `tests/live_trading/test_qmt_bridge_logic.py`（沿用该文件已有的 `_ladder_batch` /
`_run_after_hours` / `_ladder_ticks` 夹具，见文件内 Task 8 相关测试）：

```python
def test_receipts_carry_the_close_the_bridge_sized_on(tmp_path, monkeypatch):
    """定价证据必须随回执离开 Windows：Mac 侧的次日对账只有这一条路。"""
    batch = _ladder_batch(tmp_path, monkeypatch)
    _run_after_hours(tmp_path, monkeypatch)
    _ladder_ticks(tmp_path, monkeypatch, batch)
    receipts = _read_fills(tmp_path, batch)
    for r in receipts.values():
        assert r["netting_close"] > 0.0
        assert r["intended_qty"] > 0


def test_intended_qty_covers_every_share_the_ladder_wanted(tmp_path, monkeypatch):
    """intended_qty 是阶梯本意要的股数，抵销掉的那部分必须包含在内。

    这是成交率分母的不变量：分子是 applied_qty + netted_qty，若 intended_qty 小于
    netted_qty，比率就会 > 100%，指标当场失去意义。
    """
    batch = _ladder_batch(tmp_path, monkeypatch)
    _run_after_hours(tmp_path, monkeypatch)
    _ladder_ticks(tmp_path, monkeypatch, batch)
    receipts = _read_fills(tmp_path, batch)
    assert receipts
    for r in receipts.values():
        assert r["intended_qty"] >= r["netted_qty"]
        assert r["intended_qty"] >= r["filled_qty"]


def test_a_fully_netted_pair_reports_intent_on_both_legs(tmp_path, monkeypatch):
    """完全抵销时 requested_qty 两条腿口径不一致，intended_qty 必须一致地为全额。"""
    batch = _ladder_batch(tmp_path, monkeypatch)
    _run_after_hours(tmp_path, monkeypatch)
    _ladder_ticks(tmp_path, monkeypatch, batch)
    receipts = _read_fills(tmp_path, batch)
    netted = [r for r in receipts.values() if r["netted_qty"] > 0]
    assert netted, "夹具没有产生任何抵销腿，测试无意义"
    for r in netted:
        assert r["intended_qty"] == r["netted_qty"] + max(
            r["intended_qty"] - r["netted_qty"], 0,
        )
        assert r["intended_qty"] > 0
```

> 实施提示：`_read_fills` 若不存在，就地加一个读 `{root}/outbound/fills_{batch_id}.jsonl`
> 并按 `client_order_id` 建索引的小 helper，与该文件里既有的读取方式保持一致。若
> `_ladder_batch` 夹具当前不产生抵销腿（三只票无重叠），先把夹具的卖单股票代码改成与某个
> 买单相同，让它必然产生一对可抵销的腿——第二个测试靠这个前提。

- [ ] **Step 2: 跑测试确认失败**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_qmt_bridge_logic.py -k "sized_on or pre_netting" -v
```

预期：FAIL with `KeyError: 'netting_close'`。

- [ ] **Step 3: bridge 侧写入两个字段**

`live_trading/qmt_strategy/qmt_signal_bridge.py`，在 `_plan_ladder_netting` 的 3276-3285
区块补 `intended_qty`（`netting_close` 已有）：

```python
        buy["netting_close"] = close_price
        buy["netting_sized_shares"] = sized
        buy["netted_qty"] = transferred
        buy["net_quantity"] = quantity if side == "BUY" else 0
        # 阶梯本意要的股数，与抵销无关。requested_qty 顶不了这个位置：
        # 部分抵销时它是市场腿，完全抵销时买腿被改写成全额（见下方 BUY 分支）。
        buy["intended_qty"] = sized
        if offsetable:
            # The sell leg carries the same close: next-day reconciliation of the
            # transferred shares needs the price the decision was made at.
            sell["netting_close"] = close_price
            sell["netted_qty"] = transferred
            sell["net_quantity"] = quantity if side == "SELL" else 0
            sell["intended_qty"] = due_shares
```

然后在 `_write_fill`（782 行之后）把两者放进回执：

```python
    requested_qty = int(order.get("quantity", 0) or 0)
    if requested_qty <= 0 and order.get("side") == "BUY":
        requested_qty = 100
    # 未经抵销的批次（如 TopkDropout）没有这两个字段：intended 退化为 requested，
    # netting_close 留 0 表示「本单不是按冻结收盘价定量的」，对账端据此跳过。
    intended_qty = int(order.get("intended_qty", 0) or 0) or requested_qty
    event = {
        ...
        "netted_qty": int(order.get("netted_qty", 0) or 0),
        "netting_close": float(order.get("netting_close", 0.0) or 0.0),
        "intended_qty": intended_qty,
    }
```

`_write_fill` 末尾的去重比较（804-807 行）不需要改：这两个字段在同一批次内是冻结的，不会
单独变化。

- [ ] **Step 4: 跑 bridge 测试确认通过**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_qmt_bridge_logic.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 写 schema 侧失败测试**

追加到 `tests/live_trading/test_signal_schema.py`：

```python
def test_fill_event_defaults_keep_legacy_receipts_loadable():
    fill = FillEvent.from_dict({
        "batch_id": "20260714_x_001", "client_order_id": "20260714001001B",
        "mode": "LIVE", "stock_code": "600000.SH", "side": "BUY",
        "status": "FILLED", "requested_qty": 100, "filled_qty": 100,
        "avg_price": 10.0, "qmt_order_id": "1", "message": "", "ts": "t",
    })
    assert fill.netting_close == 0.0
    assert fill.intended_qty == 0
    validate_fill(fill)


def test_a_negative_netting_close_is_rejected():
    fill = FillEvent(
        batch_id="20260714_x_001", client_order_id="20260714001001B",
        mode="LIVE", stock_code="600000.SH", side="BUY", status="FILLED",
        requested_qty=100, filled_qty=100, avg_price=10.0, qmt_order_id="1",
        message="", ts="t", netting_close=-1.0,
    )
    with pytest.raises(SchemaError):
        validate_fill(fill)


def test_intended_qty_must_not_be_negative():
    fill = FillEvent(
        batch_id="20260714_x_001", client_order_id="20260714001001B",
        mode="LIVE", stock_code="600000.SH", side="BUY", status="FILLED",
        requested_qty=100, filled_qty=100, avg_price=10.0, qmt_order_id="1",
        message="", ts="t", intended_qty=-100,
    )
    with pytest.raises(SchemaError):
        validate_fill(fill)
```

- [ ] **Step 6: 改 schema**

`live_trading/modules/signal_schema.py`，`FillEvent` 尾部追加（必须带默认值，否则历史回执
无法反序列化）：

```python
    netted_qty: int = 0
    # bridge 当时用于定量的收盘价。Mac 在 report 阶段拿权威收盘价与它逐单对账，
    # 把「读到 14:57 冻结价」这类静默错误转成 CRIT（spec 第 8 节，不得省略）。
    # 0 表示本单不是按冻结收盘价定量的（如 TopkDropout 批次），对账端跳过。
    netting_close: float = 0.0
    # 阶梯本意要的股数（抵销前）。成交率的分母只能用它：requested_qty 在部分抵销
    # 与完全抵销两种情形下口径不一致。0 表示退化为 requested_qty。
    intended_qty: int = 0
```

`validate_fill` 末尾（`qmt_to_qlib` 检查之前）追加：

```python
    if (
        isinstance(fill.netting_close, bool)
        or not isinstance(fill.netting_close, (int, float))
        or not math.isfinite(float(fill.netting_close))
        or fill.netting_close < 0
    ):
        raise SchemaError(
            f"netting_close must be a non-negative finite number: "
            f"{fill.netting_close!r}"
        )
    if (
        isinstance(fill.intended_qty, bool)
        or not isinstance(fill.intended_qty, int)
        or fill.intended_qty < 0
    ):
        raise SchemaError(
            f"intended_qty must be a non-negative int: {fill.intended_qty!r}"
        )
```

- [ ] **Step 7: 写 fills 表迁移的失败测试**

追加到 `tests/live_trading/test_fill_importer.py`（照抄该文件里 Plan 2 的
`test_netted_qty_column_is_added_to_a_pre_existing_database` 的结构；注意**不要**给已有
记录的库传 `opening_cash`，那会触发 `SchemaError: opening_cash cannot seed an already-used
live ledger`）：

```python
def test_pricing_evidence_columns_are_added_to_a_pre_existing_database(tmp_path):
    db = tmp_path / "live.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE fills (
               batch_id TEXT NOT NULL, client_order_id TEXT NOT NULL,
               mode TEXT NOT NULL, stock_code TEXT NOT NULL, side TEXT NOT NULL,
               status TEXT NOT NULL, requested_qty INTEGER, filled_qty INTEGER,
               avg_price REAL, qmt_order_id TEXT, message TEXT, ts TEXT,
               PRIMARY KEY (batch_id, client_order_id))"""
    )
    conn.execute(
        "INSERT INTO fills VALUES ('b','c','LIVE','600000.SH','BUY','FILLED',"
        "100,100,10.0,'1','','t')"
    )
    conn.commit()
    conn.close()

    LiveRecorder(str(db))

    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fills)")}
    conn.close()
    assert {"netting_close", "intended_qty"} <= cols
```

- [ ] **Step 8: 改 fill_importer 的四处**

`live_trading/modules/fill_importer.py`。

建表（237-255 行）与重建（698-716 行）的列定义，在 `netted_qty` 之后各加两列：

```sql
                    netted_qty INTEGER NOT NULL DEFAULT 0,
                    netting_close REAL NOT NULL DEFAULT 0,
                    intended_qty INTEGER NOT NULL DEFAULT 0,
```

迁移（467-476 行的 `cols` 判断之后，以及 740 行附近的同类判断处）各加：

```python
            if "netting_close" not in cols:
                conn.execute(
                    "ALTER TABLE fills ADD COLUMN netting_close "
                    "REAL NOT NULL DEFAULT 0"
                )
            if "intended_qty" not in cols:
                conn.execute(
                    "ALTER TABLE fills ADD COLUMN intended_qty "
                    "INTEGER NOT NULL DEFAULT 0"
                )
```

upsert（1693-1717 行）补列名、占位符与 `DO UPDATE SET`：

```python
            conn.execute(
                """INSERT INTO fills (client_order_id, batch_id, mode, stock_code,
                       side, status, requested_qty, filled_qty, avg_price,
                       qmt_order_id, message, ts, applied_qty, applied_amount,
                       applied_fee, netted_qty, netting_close, intended_qty)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id, client_order_id) DO UPDATE SET
                       status=excluded.status,
                       filled_qty=excluded.filled_qty,
                       avg_price=excluded.avg_price,
                       qmt_order_id=excluded.qmt_order_id,
                       message=excluded.message,
                       ts=excluded.ts,
                       applied_qty=excluded.applied_qty,
                       applied_amount=excluded.applied_amount,
                       applied_fee=excluded.applied_fee,
                       netted_qty=excluded.netted_qty,
                       netting_close=excluded.netting_close,
                       intended_qty=excluded.intended_qty""",
                # netted_qty / netting_close / intended_qty 都只落盘，绝不参与
                # delta_qty / delta_amount / fee_delta：抵销的全部意义就是
                # 「不动持仓、不动现金、不计费」。
                (fill.client_order_id, fill.batch_id, fill.mode, fill.stock_code,
                 fill.side, fill.status, fill.requested_qty, fill.filled_qty,
                 fill.avg_price, fill.qmt_order_id, fill.message, fill.ts,
                 applied_qty + delta_qty, applied_amount + delta_amount,
                 applied_fee + fee_delta, int(fill.netted_qty),
                 float(fill.netting_close), int(fill.intended_qty)),
            )
```

- [ ] **Step 9: 跑三个测试文件**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_signal_schema.py \
  tests/live_trading/test_fill_importer.py \
  tests/live_trading/test_qmt_bridge_logic.py -v
```

预期：全部 PASS。

- [ ] **Step 10: 提交**

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
        live_trading/modules/signal_schema.py \
        live_trading/modules/fill_importer.py \
        tests/live_trading/test_signal_schema.py \
        tests/live_trading/test_fill_importer.py \
        tests/live_trading/test_qmt_bridge_logic.py
git commit -m "feat: carry the bridge's pricing evidence through to the fills table"
```

---

## Task 3: 收盘价逐单对账 → CRIT

spec 第 8 节：「旧价定量」的兜底路径下，bridge 理论上可能读到 14:57 的冻结价并通过 `> 0`
门禁，按错价算出股数。**该对账是接受本风险的前提，不得省略。**

落在 `report` 阶段而不是 spec 写的 `postmarket`：cron 顺序是
`postmarket` → `update`（Qlib 数据更新）→ `report`，`postmarket` 时 T 日权威收盘价还没入库。
`report` 阶段已经在为快照调 `fetch_close_prices(..., date)`，是唯一拿得到权威价的位置。

**Files:**
- Modify: `live_trading/modules/pipeline_monitor.py`（新增纯函数）
- Modify: `live_trading/scripts/run_monitor.py:511-564`（`run_report` 接线）
- Test: `tests/live_trading/test_pipeline_monitor.py`

**Interfaces:**
- Consumes: Task 2 的 `fills` 行新键 `netting_close`
- Produces:
  - `check_netting_close(trade_date, fills, official_closes, rel_tol=1e-4, abs_tol=0.01) -> list`
    - `fills`：`recorder.get_fills_by_dates([date])` 的返回
    - `official_closes`：`{qmt_code: float}`，权威未复权收盘价；缺失的票不出现在 dict 里
    - 返回 `list[Finding]`

- [ ] **Step 1: 写失败测试**

追加到 `tests/live_trading/test_pipeline_monitor.py`：

```python
def test_netting_close_matching_the_official_close_is_silent():
    fills = [_fill(netted_qty=0)]
    fills[0]["netting_close"] = 10.00
    f = check_netting_close("2026-07-14", fills, {"600000.SH": 10.00})
    assert f == []


def test_a_stale_netting_close_is_critical():
    """14:57 的冻结价与 15:00 定盘价不同，是静默错单，必须转成 CRIT。"""
    fills = [_fill()]
    fills[0]["netting_close"] = 9.80
    f = check_netting_close("2026-07-14", fills, {"600000.SH": 10.00})
    assert "NETTING_CLOSE_MISMATCH" in _rules(f)
    assert all(x.level == "CRIT" for x in f)


def test_a_missing_official_close_is_critical_not_silent():
    """拿不到权威价就等于对不了账，不能当成对上了。"""
    fills = [_fill()]
    fills[0]["netting_close"] = 9.80
    f = check_netting_close("2026-07-14", fills, {})
    assert "NETTING_CLOSE_UNVERIFIED" in _rules(f)
    assert all(x.level == "CRIT" for x in f)


def test_orders_not_priced_by_a_frozen_close_are_skipped():
    """netting_close == 0 表示本单不是按冻结价定量的（如 TopkDropout 批次）。"""
    fills = [_fill()]
    fills[0]["netting_close"] = 0.0
    assert check_netting_close("2026-07-14", fills, {}) == []


def test_float_noise_within_a_cent_does_not_alarm():
    fills = [_fill()]
    fills[0]["netting_close"] = 10.000004
    assert check_netting_close("2026-07-14", fills, {"600000.SH": 10.0}) == []


def test_a_high_priced_name_uses_the_relative_tolerance():
    """$close/$factor 的浮点误差随价格放大，绝对一分钱不够用。"""
    fills = [_fill()]
    fills[0]["netting_close"] = 800.03
    assert check_netting_close("2026-07-14", fills, {"600000.SH": 800.0}) == []
```

在该文件顶部的 import 里加上 `check_netting_close`。

- [ ] **Step 2: 跑测试确认失败**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py -k netting_close -v
```

预期：FAIL with `ImportError: cannot import name 'check_netting_close'`。

- [ ] **Step 3: 实现纯函数**

追加到 `live_trading/modules/pipeline_monitor.py`（放在 `check_postmarket` 之后）：

```python
def check_netting_close(trade_date, fills, official_closes,
                        rel_tol=1e-4, abs_tol=0.01) -> list:
    """逐单核对 bridge 定量时读到的收盘价与权威收盘价。

    这条对账是接受「旧价定量」尾部风险的前提（spec 第 8 节）。盘后固定价格通道
    在 15:00:05 起自适应提交，若终态信号不可用而回落到 15:01 固定提交，理论上仍
    可能读到 14:57 的冻结价并通过 `> 0` 门禁——股数就按错价算出去了，而且没有任何
    即时症状。把它转成次日 CRIT 是唯一的兜底。

    netting_close == 0 的单子不是按冻结收盘价定量的（如 TopkDropout 批次），跳过。
    拿不到权威价一律 CRIT：对不了账不等于对上了。
    """
    priced = [
        f for f in fills
        if float(f.get("netting_close") or 0.0) > 0.0
    ]
    if not priced:
        return []

    findings = []
    unverified = []
    mismatched = []
    for f in priced:
        code = f["stock_code"]
        used = float(f["netting_close"])
        official = official_closes.get(code)
        if official is None or not math.isfinite(float(official)) \
                or float(official) <= 0:
            unverified.append(code)
            continue
        official = float(official)
        tol = max(abs_tol, rel_tol * official)
        if abs(used - official) > tol:
            mismatched.append(
                f"{code} 用价 {used:.4f} 权威价 {official:.4f}"
            )

    if mismatched:
        findings.append(Finding(
            "NETTING_CLOSE_MISMATCH", CRIT,
            f"{trade_date} bridge 定量用价与权威收盘价不符："
            f"{'；'.join(sorted(mismatched))}。"
            "股数可能按 14:57 冻结价算出，核对当日委托并评估是否切回 CLOSE_AUCTION"))
    if unverified:
        findings.append(Finding(
            "NETTING_CLOSE_UNVERIFIED", CRIT,
            f"{trade_date} 取不到权威收盘价，无法核对定量用价："
            f"{', '.join(sorted(set(unverified)))}。"
            "对不了账不等于对上了，请人工核对后再放行次日发布"))
    return findings
```

确认文件顶部已 `import math`（若无则补），并确认 `Finding`、`CRIT`、`WARN` 已在本模块内定义
（`pipeline_monitor.py` 顶部已有）。

- [ ] **Step 4: 跑测试确认通过**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 接进 `run_report`**

`live_trading/scripts/run_monitor.py`。顶部 import 补 `check_netting_close`。
在 `run_report` 里，`fills = recorder.get_fills_by_dates([date])`（538 行）之后插入：

```python
    fills = recorder.get_fills_by_dates([date])
    fills_amount = sum_live_fills_amount(fills)

    # 定量用价对账：必须在 postclose 的 update 阶段之后跑，postmarket 时 T 日权威
    # 收盘价还没入库。取价范围限定在真正按冻结价定量过的票，避免多拉一遍全持仓。
    netting_codes = {
        f["stock_code"] for f in fills
        if float(f.get("netting_close") or 0.0) > 0.0
    }
    if netting_codes:
        netting_qlib = {c: qmt_to_qlib(c) for c in netting_codes}
        netting_prices_qlib = fetch_close_prices(
            list(netting_qlib.values()), date,
        )
        netting_closes = {
            qmt: netting_prices_qlib[ql]
            for qmt, ql in netting_qlib.items()
            if netting_prices_qlib.get(ql) is not None
        }
        findings += check_netting_close(date, fills, netting_closes)
```

- [ ] **Step 6: 跑 run_monitor 的测试**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/ -k "monitor or report" -v
```

预期：全部 PASS。

- [ ] **Step 7: 记录一处可用性后果**

`live_trading/README.md` 的监控章节补一段（若无对应小节则新建「盘后对账的阶段依赖」）：

```markdown
### 盘后对账的阶段依赖

`report` 阶段跑在 `update`（Qlib 数据更新）之后，且 `run_postclose_cron.sh` 在 `update`
失败时会跳过 `report`。两项依赖权威收盘价的检查——`NETTING_CLOSE_MISMATCH` 与
`FILL_RATIO_*`——因此也一并跳过。数据更新失败的交易日，必须在修好数据后手工补跑
`run_monitor_cron.sh report <config_id>`，否则当日「定量用价」与「成交率」两项都没有对账
证据，不得据此放行次日发布。
```

- [ ] **Step 8: 提交**

```bash
git add live_trading/modules/pipeline_monitor.py \
        live_trading/scripts/run_monitor.py \
        live_trading/README.md \
        tests/live_trading/test_pipeline_monitor.py
git commit -m "feat: reconcile the bridge's sizing close against the official close"
```

---

## Task 4: 分侧加权成交率与回退触发

成交率是 spec 第 8 节点明的**头号风险**，而全仓库现在没有任何 `fill_ratio` 实现。回退触发
的口径来自 spec：**连续 3 个交易日买入侧加权成交率 < 80%，或任一日 < 50%**。

分母用 Task 2 的 `intended_qty`（阶梯本意要的股数），不是 `requested_qty`——理由见前置事实表。
分子是 `applied_qty + netted_qty`：抵销掉的股数确实进了仓位，只是没走市场。

同样落在 `report` 阶段：连续 3 日的判断需要交易日历，`run_report` 已经拿到 `calendar`。

**Files:**
- Modify: `live_trading/modules/pipeline_monitor.py`（新增两个纯函数 + 三个阈值默认值）
- Modify: `live_trading/scripts/run_monitor.py`（`run_report` 接线 + 日报输出）
- Modify: `live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml`（阈值）
- Test: `tests/live_trading/test_pipeline_monitor.py`

**Interfaces:**
- Consumes: Task 2 的 `fills` 行新键 `intended_qty`
- Produces:
  - `weighted_fill_ratio(fills, side) -> float | None`
    `None` 表示当日该侧没有任何意图股数（无从计算，不是 0%）
  - `check_fill_ratio(trade_date, ratios_by_date, thresholds) -> list`
    `ratios_by_date`：`{date: {"BUY": float|None, "SELL": float|None}}`，按日期升序取用；
    当日必须是最后一个键
  - `DEFAULT_THRESHOLDS` 新增 `fill_ratio_buy_floor: 0.80`、
    `fill_ratio_buy_hard_floor: 0.50`、`fill_ratio_streak_days: 3`

- [ ] **Step 1: 写失败测试**

追加到 `tests/live_trading/test_pipeline_monitor.py`：

```python
def _lfill(side="BUY", intended=300, applied=300, netted=0, code="600000.SH"):
    return {"batch_id": BATCH["batch_id"], "mode": "LIVE", "status": "FILLED",
            "side": side, "stock_code": code, "filled_qty": applied,
            "applied_qty": applied, "netted_qty": netted,
            "intended_qty": intended, "message": ""}


def test_a_fully_filled_day_is_one_hundred_percent():
    assert weighted_fill_ratio([_lfill()], "BUY") == 1.0


def test_netted_shares_count_as_filled():
    """抵销掉的股数确实进了仓位，只是没走市场。"""
    fills = [_lfill(applied=0, netted=300)]
    assert weighted_fill_ratio(fills, "BUY") == 1.0


def test_the_ratio_is_weighted_by_intended_shares_not_by_order_count():
    fills = [_lfill(intended=1000, applied=1000),
             _lfill(intended=100, applied=0, code="000001.SZ")]
    assert weighted_fill_ratio(fills, "BUY") == pytest.approx(1000 / 1100)


def test_a_skipped_order_drags_the_ratio_down():
    """买不成就是欠配，不是「不算」。"""
    fills = [_lfill(applied=0)]
    fills[0]["status"] = "SKIPPED"
    assert weighted_fill_ratio(fills, "BUY") == 0.0


def test_no_intent_on_a_side_is_unknown_not_zero():
    assert weighted_fill_ratio([_lfill(side="SELL")], "BUY") is None


def test_intended_qty_falls_back_to_requested_for_legacy_receipts():
    fill = _lfill()
    del fill["intended_qty"]
    fill["requested_qty"] = 300
    assert weighted_fill_ratio([fill], "BUY") == 1.0


def test_a_healthy_run_of_days_is_silent():
    ratios = {
        "2026-07-10": {"BUY": 0.95, "SELL": 1.0},
        "2026-07-13": {"BUY": 1.0, "SELL": 1.0},
        "2026-07-14": {"BUY": 0.9, "SELL": 1.0},
    }
    assert check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS) == []


def test_a_single_day_below_the_hard_floor_is_critical():
    ratios = {"2026-07-14": {"BUY": 0.4, "SELL": 1.0}}
    f = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_CRITICAL" in _rules(f)
    assert all(x.level == "CRIT" for x in f)


def test_three_consecutive_days_below_the_soft_floor_trip_the_rollback():
    ratios = {
        "2026-07-10": {"BUY": 0.75, "SELL": 1.0},
        "2026-07-13": {"BUY": 0.7, "SELL": 1.0},
        "2026-07-14": {"BUY": 0.79, "SELL": 1.0},
    }
    f = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_STREAK" in _rules(f)
    assert any(x.level == "CRIT" for x in f)


def test_a_streak_broken_by_a_good_day_does_not_trip():
    ratios = {
        "2026-07-10": {"BUY": 0.7, "SELL": 1.0},
        "2026-07-13": {"BUY": 0.95, "SELL": 1.0},
        "2026-07-14": {"BUY": 0.7, "SELL": 1.0},
    }
    f = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_STREAK" not in _rules(f)


def test_one_soft_breach_warns_without_tripping_the_rollback():
    ratios = {"2026-07-14": {"BUY": 0.7, "SELL": 1.0}}
    f = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_LOW" in _rules(f)
    assert "FILL_RATIO_STREAK" not in _rules(f)


def test_a_short_history_cannot_trip_the_streak():
    """建仓期前两天历史不足，不能凑出连续三日。"""
    ratios = {"2026-07-13": {"BUY": 0.1, "SELL": 1.0},
              "2026-07-14": {"BUY": 0.1, "SELL": 1.0}}
    f = check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS)
    assert "FILL_RATIO_STREAK" not in _rules(f)


def test_an_unknown_buy_ratio_today_is_silent():
    ratios = {"2026-07-14": {"BUY": None, "SELL": 1.0}}
    assert check_fill_ratio("2026-07-14", ratios, DEFAULT_THRESHOLDS) == []
```

顶部 import 补 `weighted_fill_ratio`、`check_fill_ratio`、`DEFAULT_THRESHOLDS`。

- [ ] **Step 2: 跑测试确认失败**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py -k "fill_ratio or intended or netted_shares" -v
```

预期：FAIL with `ImportError: cannot import name 'weighted_fill_ratio'`。

- [ ] **Step 3: 加三个阈值默认值**

`live_trading/modules/pipeline_monitor.py:17-22`：

```python
DEFAULT_THRESHOLDS = {
    "daily_loss": -0.03,
    "consecutive_loss_days": 5,
    "reject_rate": 0.5,
    "cash_tolerance": 100.0,
    # 盘后固定价格通道的成交率回退触发（spec 第 8 节）。买不成不是滑点，
    # 是系统性欠配：层会变薄且无替补。
    "fill_ratio_buy_floor": 0.80,
    "fill_ratio_buy_hard_floor": 0.50,
    "fill_ratio_streak_days": 3,
}
```

- [ ] **Step 4: 实现两个纯函数**

追加到 `live_trading/modules/pipeline_monitor.py`：

```python
def weighted_fill_ratio(fills, side):
    """按意图股数加权的单侧成交率；该侧没有意图时返回 None。

    分母用 intended_qty（阶梯抵销前本意要的股数），不用 requested_qty：后者在部分
    抵销时是市场腿、在完全抵销时被改写成全额，两种口径混在一起算不出可比的比率。
    分子把 netted_qty 计入已成交——抵销掉的股数确实进了仓位，只是没走市场。
    """
    intended = 0
    obtained = 0
    for f in fills:
        if f.get("mode") != "LIVE" or f.get("side") != side:
            continue
        want = int(f.get("intended_qty") or 0) or int(f.get("requested_qty") or 0)
        if want <= 0:
            continue
        intended += want
        got = int(f.get("applied_qty") or f.get("filled_qty") or 0)
        obtained += min(got + int(f.get("netted_qty") or 0), want)
    if intended <= 0:
        return None
    return obtained / float(intended)


def check_fill_ratio(trade_date, ratios_by_date, thresholds) -> list:
    """成交率门禁：盘后固定价格通道的头号风险（spec 第 8 节）。

    盘后固定价格是在一个独立的薄池子里按时间优先逐笔撮合，需要对手盘；而回测假设
    以收盘价成交、深度无限。成交率显著低于 100% 的后果不是滑点而是系统性欠配。
    """
    today = ratios_by_date.get(trade_date) or {}
    buy = today.get("BUY")
    if buy is None:
        return []  # 当日没有买入意图，无从判断

    hard = float(thresholds.get(
        "fill_ratio_buy_hard_floor",
        DEFAULT_THRESHOLDS["fill_ratio_buy_hard_floor"]))
    soft = float(thresholds.get(
        "fill_ratio_buy_floor", DEFAULT_THRESHOLDS["fill_ratio_buy_floor"]))
    need = int(thresholds.get(
        "fill_ratio_streak_days",
        DEFAULT_THRESHOLDS["fill_ratio_streak_days"]))

    findings = []
    if buy < hard:
        findings.append(Finding(
            "FILL_RATIO_CRITICAL", CRIT,
            f"{trade_date} 买入侧加权成交率 {buy:.1%} 低于硬下限 {hard:.0%}，"
            "暂停次日发布并评估切回 CLOSE_AUCTION"))
    elif buy < soft:
        findings.append(Finding(
            "FILL_RATIO_LOW", WARN,
            f"{trade_date} 买入侧加权成交率 {buy:.1%} 低于 {soft:.0%}，"
            "盘后对手盘可能不足，留意是否连续"))

    recent = [
        ratios_by_date[d].get("BUY")
        for d in sorted(ratios_by_date)
        if d <= trade_date
    ][-need:]
    if len(recent) == need and all(
        r is not None and r < soft for r in recent
    ):
        findings.append(Finding(
            "FILL_RATIO_STREAK", CRIT,
            f"{trade_date} 买入侧加权成交率连续 {need} 个交易日低于 {soft:.0%}"
            f"（{', '.join(f'{r:.1%}' for r in recent)}），"
            "触发回退条件：暂停发布并评估切回 CLOSE_AUCTION"))
    return findings
```

> 注意：`FILL_RATIO_STREAK` 触发时不要自动改 `execution_state`。暂停发布是人的决定
> （spec 第 8 节「暂停发布，评估切回」），监控只负责把事实喊出来。回退**不得**通过删除
> 授权 marker 实现。

- [ ] **Step 5: 跑测试确认通过**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py -v
```

预期：全部 PASS。

- [ ] **Step 6: 接进 `run_report`**

`live_trading/scripts/run_monitor.py`。顶部 import 补 `check_fill_ratio`、
`weighted_fill_ratio`。在 Task 3 插入的对账代码之后追加：

```python
    # 成交率：连续 3 日的判断要跨交易日，所以在 report 阶段做（这里有 calendar）。
    ratio_dates = [d for d in calendar if d <= date][
        -int(_thresholds(config).get("fill_ratio_streak_days", 3)):
    ]
    ratios_by_date = {}
    for d in ratio_dates:
        day_fills = fills if d == date else recorder.get_fills_by_dates([d])
        ratios_by_date[d] = {
            "BUY": weighted_fill_ratio(day_fills, "BUY"),
            "SELL": weighted_fill_ratio(day_fills, "SELL"),
        }
    findings += check_fill_ratio(date, ratios_by_date, _thresholds(config))
    today_ratios = ratios_by_date.get(date, {})
    logger.info(
        "fill_ratio %s: buy=%s sell=%s", date,
        "n/a" if today_ratios.get("BUY") is None
        else "%.1f%%" % (today_ratios["BUY"] * 100),
        "n/a" if today_ratios.get("SELL") is None
        else "%.1f%%" % (today_ratios["SELL"] * 100),
    )
```

- [ ] **Step 7: 把成交率写进日报**

`live_trading/scripts/run_monitor.py` 的 `_daily_report_md`，加一个参数并在正文里输出。
先看该函数当前签名，把新参数加在末尾并给默认值 `None`，再在费用/成交那一段之后插入：

```python
    if fill_ratios:
        def _pct(v):
            return "n/a" if v is None else "%.1f%%" % (v * 100)
        lines.append(
            "- 加权成交率：买 %s / 卖 %s"
            % (_pct(fill_ratios.get("BUY")), _pct(fill_ratios.get("SELL")))
        )
```

调用处（559-563 行）改为传入 `today_ratios`：

```python
        body = _daily_report_md(date, daily_row, fills, findings, corp_applied,
                                fill_ratios=today_ratios)
```

> `lines` 是 `_daily_report_md` 内部拼接列表的名字——实施时按该函数实际的变量名与
> 拼接风格调整，不要硬套。

- [ ] **Step 8: 配置里显式写出阈值**

`live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml` 的 `monitor.thresholds`
补三项（与默认值相同，显式写出便于运行时核对）：

```yaml
  thresholds:
    daily_loss: -0.03
    consecutive_loss_days: 5
    reject_rate: 0.5
    cash_tolerance: 100.0
    fill_ratio_buy_floor: 0.80
    fill_ratio_buy_hard_floor: 0.50
    fill_ratio_streak_days: 3
```

- [ ] **Step 9: 跑相关测试**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_pipeline_monitor.py \
  tests/live_trading/test_live_config.py -v
```

预期：全部 PASS。

- [ ] **Step 10: 提交**

```bash
git add live_trading/modules/pipeline_monitor.py \
        live_trading/scripts/run_monitor.py \
        live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml \
        tests/live_trading/test_pipeline_monitor.py
git commit -m "feat: track weighted per-side fill ratio and trip the rollback trigger"
```

---

## Task 5: 快照白名单容纳阶梯策略

账户快照观测请求的 `requested_for_strategy_id` 有两道白名单，Mac 与 bridge 各一份，都还只
认 csi1000 主策略和 pr49 探针。新策略 id 不在里面，切换后快照观测会被拒。两份必须保持镜像。

**Files:**
- Modify: `live_trading/modules/operator_probe.py:43-44`
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py:1516-1519`
- Test: `tests/live_trading/test_operator_probe.py`、`tests/live_trading/test_qmt_bridge_logic.py`

**Interfaces:**
- Consumes: 无
- Produces: `operator_probe.SNAPSHOT_REQUEST_STRATEGIES` 与
  `qmt_signal_bridge._SNAPSHOT_REQUEST_STRATEGIES` 两个集合的内容（须逐项相同）

- [ ] **Step 1: 写失败测试**

追加到 `tests/live_trading/test_operator_probe.py`：

```python
LADDER_STRATEGY_ID = "alla_v4_ladder_k3h5_postclose_real"


def test_the_ladder_strategy_may_request_an_account_snapshot():
    assert LADDER_STRATEGY_ID in SNAPSHOT_REQUEST_STRATEGIES


def test_the_two_snapshot_whitelists_are_mirrored():
    """Mac 与 bridge 各有一份，任一侧漏改都会让快照观测在切换当天被拒。"""
    bridge_src = (
        Path(__file__).resolve().parents[2]
        / "live_trading" / "qmt_strategy" / "qmt_signal_bridge.py"
    ).read_text(encoding="utf-8")
    for sid in SNAPSHOT_REQUEST_STRATEGIES:
        assert f'"{sid}"' in bridge_src, sid
```

追加到 `tests/live_trading/test_qmt_bridge_logic.py`：

```python
def test_bridge_snapshot_whitelist_includes_the_ladder_strategy():
    assert "alla_v4_ladder_k3h5_postclose_real" \
        in bridge._SNAPSHOT_REQUEST_STRATEGIES
```

按各文件已有风格补 import（`SNAPSHOT_REQUEST_STRATEGIES`、`Path`）。

- [ ] **Step 2: 跑测试确认失败**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_operator_probe.py \
  tests/live_trading/test_qmt_bridge_logic.py -k snapshot -v
```

预期：两个新用例 FAIL（AssertionError）。

- [ ] **Step 3: 两侧各加一项**

`live_trading/modules/operator_probe.py:43-44`：

```python
MAIN_STRATEGY_ID = "csi1000_b6m_b2s_postclose_real"
# 全A 真阶梯（BT v4）。切换期间与 csi1000 主策略并存于白名单：白名单只管
# 「谁可以请求账户快照」，不决定谁在被调度。
LADDER_STRATEGY_ID = "alla_v4_ladder_k3h5_postclose_real"
SNAPSHOT_REQUEST_STRATEGIES = {
    MAIN_STRATEGY_ID, PROBE_STRATEGY_ID, LADDER_STRATEGY_ID,
}
```

`live_trading/qmt_strategy/qmt_signal_bridge.py:1516-1519`：

```python
_SNAPSHOT_REQUEST_STRATEGIES = (
    "csi1000_b6m_b2s_postclose_real",
    "csi1000_pr49_one_lot_probe",
    "alla_v4_ladder_k3h5_postclose_real",
)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_operator_probe.py \
  tests/live_trading/test_qmt_bridge_logic.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add live_trading/modules/operator_probe.py \
        live_trading/qmt_strategy/qmt_signal_bridge.py \
        tests/live_trading/test_operator_probe.py \
        tests/live_trading/test_qmt_bridge_logic.py
git commit -m "feat: allow the ladder strategy to request account snapshots"
```

---

## Task 6: 渲染执行通道、抵销开关与摘掉一手闸

`render_main_source` 现在只渲染 6 个账户类常量。**`ENABLE_LADDER_NETTING` 仓库默认 `False`
且不被渲染——照现在装机，计划二的全部抵销工作在生产里一行都不会执行。** 同理
`EXECUTION_PROFILE` 不渲染就还是收盘集合竞价通道，`MAX_ORDER_QUANTITY` 不渲染就还卡在一手。

做法是**渲染**而不是改仓库默认值：仓库模板保持最保守（不抵销、一手上限、旧通道），生产形态
由渲染显式产生。这样模板被误当成生产脚本直接跑也不会造成损失。

**Files:**
- Modify: `live_trading/scripts/render_qmt_runtime.py:32-48`
- Modify: `live_trading/qmt_strategy/README_QMT.md:40,54,174`
- Modify: `live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md:14,36`
- Test: `tests/live_trading/test_render_qmt_runtime.py`

**Interfaces:**
- Consumes: 无
- Produces: `render_main_source(source, account_id, expected_cash, *, execution_profile="AFTER_HOURS_FIXED_PRICE", enable_ladder_netting=True, max_order_quantity=0) -> str`
  （新增三个关键字参数，默认值即生产形态）

- [ ] **Step 1: 写失败测试**

`tests/live_trading/test_render_qmt_runtime.py` 里**没有**共享的模板变量，每个用例都自己
拼一段内联源码。而 `_replace_setting`（`render_qmt_runtime.py:13-22`）在目标设置不存在或
出现多次时会 `raise ValueError`——所以新测试必须用一段包含全部 9 个设置的源码。在文件顶部
加一个共享夹具，再追加用例：

```python
import pytest

# _replace_setting 要求每个设置在源码里恰好出现一次，所以夹具必须列全。
# 这里的值刻意与仓库模板一致（保守形态），断言才能证明渲染真的改变了它们。
MAIN_TEMPLATE = (
    'ACCOUNT_ID = ""\n'
    'STRATEGY_NAME = "qlib_bridge"\n'
    'ACCOUNT_ENVIRONMENT = "SIMULATION"\n'
    'ALLOW_REAL_MONEY = False\n'
    'REAL_EXPECTED_INITIAL_CASH = 1000000.0\n'
    'REAL_REQUIRE_EMPTY_POSITIONS = True\n'
    'EXECUTION_PROFILE = "CLOSE_AUCTION"\n'
    'ENABLE_LADDER_NETTING = False\n'
    'MAX_ORDER_QUANTITY = 100\n'
)


def test_rendered_runtime_turns_netting_on():
    """默认 False 且不渲染的话，计划二的抵销在生产里一行都不会跑。"""
    rendered = render_main_source(MAIN_TEMPLATE, "12345678", 100000.0)
    assert "ENABLE_LADDER_NETTING = True" in rendered
    assert "ENABLE_LADDER_NETTING = False" not in rendered


def test_rendered_runtime_selects_the_after_hours_channel():
    rendered = render_main_source(MAIN_TEMPLATE, "12345678", 100000.0)
    assert 'EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"' in rendered


def test_rendered_runtime_lifts_the_one_lot_cap():
    rendered = render_main_source(MAIN_TEMPLATE, "12345678", 100000.0)
    assert "MAX_ORDER_QUANTITY = 0" in rendered
    assert "MAX_ORDER_QUANTITY = 100" not in rendered


def test_the_channel_can_be_overridden_for_a_rollback_render():
    rendered = render_main_source(
        MAIN_TEMPLATE, "12345678", 100000.0,
        execution_profile="CLOSE_AUCTION", enable_ladder_netting=False,
    )
    assert 'EXECUTION_PROFILE = "CLOSE_AUCTION"' in rendered
    assert "ENABLE_LADDER_NETTING = False" in rendered


def test_an_unknown_execution_profile_is_rejected():
    with pytest.raises(ValueError):
        render_main_source(MAIN_TEMPLATE, "12345678", 100000.0,
                           execution_profile="MARKET_ON_OPEN")


def test_a_negative_order_cap_is_rejected():
    with pytest.raises(ValueError):
        render_main_source(MAIN_TEMPLATE, "12345678", 100000.0,
                           max_order_quantity=-1)


def test_the_real_bridge_template_is_renderable_and_conservative():
    """夹具会漂移，真模板不会。直接拿仓库里的 bridge 源码渲染一遍。

    _replace_setting 要求每个设置恰好出现一次——这条测试同时守住了「模板里没有重复
    的模块级赋值」这个前提，那是渲染在装机当天唯一会硬失败的地方。
    """
    bridge = (
        Path(__file__).resolve().parents[2]
        / "live_trading" / "qmt_strategy" / "qmt_signal_bridge.py"
    ).read_text(encoding="utf-8")
    assert 'EXECUTION_PROFILE = "CLOSE_AUCTION"' in bridge
    assert "ENABLE_LADDER_NETTING = False" in bridge
    assert "MAX_ORDER_QUANTITY = 100" in bridge

    rendered = render_main_source(bridge, "12345678", 100000.0)
    assert 'EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"' in rendered
    assert "ENABLE_LADDER_NETTING = True" in rendered
    assert "MAX_ORDER_QUANTITY = 0" in rendered
```

> 已核准：`EXECUTION_PROFILE`、`ENABLE_LADDER_NETTING`、`MAX_ORDER_QUANTITY` 在
> `qmt_signal_bridge.py` 里各自恰好有一处模块级赋值，渲染不会撞上「found 2」。最后那条
> 测试就是这个前提的回归守卫。

- [ ] **Step 2: 跑测试确认失败**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_render_qmt_runtime.py -v
```

预期：新用例 FAIL（`TypeError: unexpected keyword argument 'execution_profile'`，
以及渲染结果里没有那几行）。

**同时既有的 `test_main_runtime_render_binds_real_account_without_mutating_template`
会 FAIL 两次**，都要改：
1. 它的内联模板（12-19 行）不含 `EXECUTION_PROFILE` / `ENABLE_LADDER_NETTING`，渲染会
   `ValueError: expected exactly one EXECUTION_PROFILE setting, found 0`。把该模板换成
   上面的 `MAIN_TEMPLATE`。
2. 它的 `assert "MAX_ORDER_QUANTITY = 100" in rendered`（33 行）表达的是旧的一手阶段
   约束，现在渲染产物里应当是 `0`。改成 `assert "MAX_ORDER_QUANTITY = 0" in rendered`；
   「模板本身保持保守」这层语义由新的
   `test_the_real_bridge_template_is_renderable_and_conservative` 承接。

- [ ] **Step 3: 改渲染函数**

`live_trading/scripts/render_qmt_runtime.py`：

```python
_VALID_EXECUTION_PROFILES = ("CLOSE_AUCTION", "AFTER_HOURS_FIXED_PRICE")


def render_main_source(source: str, account_id: str, expected_cash: float, *,
                       execution_profile: str = "AFTER_HOURS_FIXED_PRICE",
                       enable_ladder_netting: bool = True,
                       max_order_quantity: int = 0) -> str:
    """把仓库模板渲染成生产运行时。

    渲染而不是改仓库默认值：模板保持最保守形态（旧通道、不抵销、一手上限），
    生产形态只在渲染产物里出现。默认参数就是生产形态——漏传参数会得到正确结果，
    而不是静默退回一手探针。
    """
    account_id = _validate_account_id(account_id)
    cash = float(expected_cash)
    if not math.isfinite(cash) or cash < 0:
        raise ValueError("expected cash must be a finite non-negative number")
    if execution_profile not in _VALID_EXECUTION_PROFILES:
        raise ValueError(
            "unknown execution profile: %r" % (execution_profile,)
        )
    cap = int(max_order_quantity)
    if cap < 0:
        raise ValueError("max order quantity must not be negative")
    settings = (
        ("ACCOUNT_ID", f'"{account_id}"'),
        ("STRATEGY_NAME", '"qlib_bridge_main"'),
        ("ACCOUNT_ENVIRONMENT", '"REAL"'),
        ("ALLOW_REAL_MONEY", "True"),
        ("REAL_EXPECTED_INITIAL_CASH", f"{cash:.2f}"),
        ("REAL_REQUIRE_EMPTY_POSITIONS", "False"),
        ("EXECUTION_PROFILE", f'"{execution_profile}"'),
        ("ENABLE_LADDER_NETTING", "True" if enable_ladder_netting else "False"),
        # 0 = 无上限。真阶梯单笔约 6 万元，一手闸会把每层砍成 100 股。
        ("MAX_ORDER_QUANTITY", str(cap)),
    )
    rendered = source
    for name, value in settings:
        rendered = _replace_setting(rendered, name, value)
    return rendered
```

- [ ] **Step 4: 跑测试确认通过**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_render_qmt_runtime.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 跑边界与包装脚本测试**

`tests/live_trading/test_repository_boundaries.py:70` 与
`tests/live_trading/test_operational_wrappers.py:69` 把
`"MAX_ORDER_QUANTITY = 100"` 当清单 token。模板值没变，这两处应当仍然通过：

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_operational_wrappers.py -v
```

预期：全部 PASS。若有失败，是清单里连渲染产物一起校验了——按实际报错调整清单，不要动模板值。

- [ ] **Step 6: 更新两份文档**

`live_trading/qmt_strategy/README_QMT.md`：**不要动** 31-41 与 45-55 行那两段「本地副本」
示例——它们描述的是部署状态，改了就等于宣称已经切换，那是计划四完成后才成立的事实
（`BRIDGE_ROOT` / `OTHER_BRIDGE_ROOT` 也不由渲染决定，是 Windows 上手工设定的）。
本任务只把 174 行「保持 `MAX_ORDER_QUANTITY = 100`」改写为渲染契约的说明：

```markdown
仓库模板里 `MAX_ORDER_QUANTITY = 100`、`ENABLE_LADDER_NETTING = False`、
`EXECUTION_PROFILE = "CLOSE_AUCTION"` 是**故意保守**的：模板被误当生产脚本直接跑也不会
造成损失。生产形态一律由 `live_trading/scripts/render_qmt_runtime.py` 渲染产生
（盘后固定价格通道、开启抵销、`MAX_ORDER_QUANTITY = 0` 即无上限）。
**不要手工编辑本地副本里的这三个常量**——渲染产物是唯一的生产事实来源。
```

`live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md`：14、36 行所在小节顶部加一行说明：

```markdown
> 说明：本清单描述的是**一手探针阶段**的约束。`MAX_ORDER_QUANTITY = 100` 是探针的刻意
> 限制，不是主策略的生产设置；主策略的上限由渲染决定。探针的退役状态见计划四。
```

- [ ] **Step 7: 提交**

```bash
git add live_trading/scripts/render_qmt_runtime.py \
        live_trading/qmt_strategy/README_QMT.md \
        live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md \
        tests/live_trading/test_render_qmt_runtime.py
git commit -m "feat: render the production execution channel, netting switch and order cap"
```

---

## Task 7: 顺延频率诊断脚本

spec 第 8 节：「名字集合与回测不同——封板票实盘尝试买入而回测顺延跳过，停牌票实盘让层变薄
而回测顺延。**频率待诊断脚本量化；量化前无法预估其对收益的影响方向。**」

回测里这些 skip 是**静默**的（`select_ladder_buys` 里 `continue`，无 log 无计数），所以只能
另写脚本重算。脚本只读，不碰回测代码。

**本任务不阻塞计划四**：它量化的是一个已知的语义偏离，产出是决策信息而非上线前置条件。
若要赶切换窗口，可以先跳过本任务。

**Files:**
- Create: `live_trading/scripts/diagnose_ladder_skip_rate.py`
- Test: 无单测（一次性诊断脚本，与 `diagnose_ladder_gap.py`、`probe_topk_margin.py` 同类）

**Interfaces:**
- Consumes: BT v4 的 `external_pred.pkl`（路径见 `verify_ladder_dry_run.py` 里的
  `BT_V4_PRED` 常量）；Qlib 行情
- Produces: stdout 报表，无模块级 API

- [ ] **Step 1: 写脚本**

创建 `live_trading/scripts/diagnose_ladder_skip_rate.py`：

```python
#!/usr/bin/env python3
"""量化「封板/停牌导致的名字集合偏离」在测试期出现的频率。

回测的 select_ladder_buys 遇到不可买的候选会顺延取下一名，而实盘按 spec 4.7 不顺延
（封板票照样尝试买入，停牌票让层变薄）。两边的名字集合因此会分叉。回测里这些 skip
是静默的——没有 log、没有计数——所以只能在这里用同一帧预测重算一遍。

口径说明（与 exchange 的近似）：本脚本用 $volume == 0 或 $close 缺失判定停牌，用
cn_limit.limit_cap_array 的板块阈值判定封板。这是对 Exchange.is_stock_tradable 的
近似，不是逐位复刻——本脚本要回答的是「频率有多高」，不是「哪一天差一分钱」。
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

BT_V4_PRED = (
    PROJECT_ROOT / "backtest" / "result"
    / "20260822_233132_phase_s_m0h20rankices_all_ladder_k3h5_ensemble"
    / "external_pred.pkl"
)
TEST_START = "2021-07-16"
TEST_END = "2026-07-16"


def _load_scores(path, start, end):
    df = pd.read_pickle(path)
    scores = df.iloc[:, 0] if isinstance(df, pd.DataFrame) else df
    scores = scores.dropna()
    dates = scores.index.get_level_values("datetime")
    return scores[(dates >= start) & (dates <= end)]


def _load_quotes(instruments, start, end):
    from qlib.data import D

    fields = ["$close/$factor", "Ref($close/$factor, 1)", "$volume"]
    quotes = D.features(
        sorted(instruments), fields, start_time=start, end_time=end,
    )
    quotes.columns = ["close", "prev_close", "volume"]
    return quotes


def _buyable_flags(quotes):
    """返回与 quotes 同索引的布尔 Series：True = 可买。"""
    from qlib.backtest.cn_limit import limit_cap_array

    instruments = quotes.index.get_level_values("instrument")
    dates = quotes.index.get_level_values("datetime")
    cap = limit_cap_array(instruments, dates)

    close = quotes["close"].to_numpy(dtype=float)
    prev = quotes["prev_close"].to_numpy(dtype=float)
    volume = quotes["volume"].to_numpy(dtype=float)

    suspended = ~np.isfinite(close) | (volume <= 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        limit_up = close >= prev * (1.0 + cap) - 1e-9
    return pd.Series(~(suspended | limit_up), index=quotes.index)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pred", default=str(BT_V4_PRED))
    parser.add_argument("--start", default=TEST_START)
    parser.add_argument("--end", default=TEST_END)
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument(
        "--provider-uri",
        default=str(Path.home() / ".qlib" / "qlib_data" / "cn_data"),
    )
    args = parser.parse_args()

    import qlib

    qlib.init(provider_uri=args.provider_uri, region="cn")

    scores = _load_scores(args.pred, args.start, args.end)
    instruments = sorted(set(scores.index.get_level_values("instrument")))
    quotes = _load_quotes(instruments, args.start, args.end)
    buyable = _buyable_flags(quotes)

    days = 0
    days_with_skip = 0
    skipped_slots = 0
    total_slots = 0
    thin_days = 0
    reasons = {"limit_up_or_suspended": 0}

    for date, day_scores in scores.groupby(level="datetime"):
        ranked = day_scores.sort_values(ascending=False)
        codes = [c for _, c in ranked.index]
        picked = []
        skipped_here = 0
        for code in codes:
            if len(picked) >= args.topk:
                break
            ok = buyable.get((date, code))
            if ok is None:
                continue  # 当天没有行情，回测也取不到，不计入
            if not ok:
                skipped_here += 1
                continue
            picked.append(code)
        days += 1
        total_slots += args.topk
        skipped_slots += skipped_here
        if skipped_here:
            days_with_skip += 1
            reasons["limit_up_or_suspended"] += skipped_here
        if len(picked) < args.topk:
            thin_days += 1

    print("=" * 66)
    print("阶梯顺延频率诊断  %s ~ %s  topk=%d" % (args.start, args.end, args.topk))
    print("=" * 66)
    print("交易日数                        : %d" % days)
    print("发生过顺延的交易日              : %d (%.1f%%)"
          % (days_with_skip, 100.0 * days_with_skip / max(days, 1)))
    print("被顺延掉的候选次数              : %d" % skipped_slots)
    print("平均每日顺延次数                : %.3f"
          % (skipped_slots / float(max(days, 1))))
    print("凑不满 topk 的交易日（层变薄）  : %d (%.1f%%)"
          % (thin_days, 100.0 * thin_days / max(days, 1)))
    print()
    print("读法：实盘不顺延。上面「发生过顺延的交易日」占比就是实盘与回测名字集合")
    print("可能分叉的日子占比上界；「凑不满 topk」的日子实盘会直接让层变薄。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑脚本**

```bash
cd /Users/yuxianqi/Project/qlib_exp && \
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/diagnose_ladder_skip_rate.py
```

预期：打印上述报表，交易日数约 1200（测试期 2021-07-16 ~ 2026-07-16）。
不得抛异常。若 `external_pred.pkl` 路径已被实验清理策略删掉，用 `--pred` 指向现存的
BT v4 产物；找不到就在提交信息里注明该前提。

> macOS 注意：本脚本会触发 Qlib 并行取数，**必须**以文件形式运行，不能用
> heredoc/stdin（见 `.cursor/rules/qlib-shell-multiprocessing.mdc`）。

- [ ] **Step 3: 把实测数字写回 spec**

`docs/superpowers/specs/2026-08-23-live-v4-cohort-ladder-netting-design.md` 第 8 节
「名字集合与回测不同」那一段，把「频率待诊断脚本量化」替换为实测结论，格式：

```markdown
**名字集合与回测不同**（4.7）：封板票实盘尝试买入而回测顺延跳过，停牌票实盘让层变薄而
回测顺延。`live_trading/scripts/diagnose_ladder_skip_rate.py` 实测（测试期 N 个交易日，
topk=3）：发生顺延的交易日 X 个（Y%），平均每日顺延 Z 次，凑不满 top3 的交易日 W 个（V%）。
```

- [ ] **Step 4: 提交**

```bash
git add live_trading/scripts/diagnose_ladder_skip_rate.py \
        docs/superpowers/specs/2026-08-23-live-v4-cohort-ladder-netting-design.md
git commit -m "feat: quantify how often the ladder's backtest defers an unbuyable pick"
```

---

## 完成标准

- [ ] 全套实盘单测通过：
      `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -v`
- [ ] 一个三只票全额抵销的交易日不再产生 `ALL_ORDERS_SKIPPED`，而一个真的什么都没发生的
      交易日仍然 CRIT
- [ ] `fills` 表有 `netting_close` 与 `intended_qty` 两列，且历史库能就地迁移出来
- [ ] `report` 阶段会因「定量用价与权威收盘价不符」或「取不到权威价」报 CRIT
- [ ] `report` 阶段输出买卖两侧加权成交率，并在单日 < 50% 或连续 3 日 < 80% 时报 CRIT
- [ ] `render_main_source()` 不传任何可选参数时，产出的运行时是
      `AFTER_HOURS_FIXED_PRICE` + `ENABLE_LADDER_NETTING = True` + `MAX_ORDER_QUANTITY = 0`；
      而仓库模板本身仍是保守的三个值
- [ ] 新策略 id 同时在 Mac 与 bridge 两份快照白名单里
- [ ] 顺延频率的实测数字已回写 spec 第 8 节（若跳过 Task 7，在此注明）

## 不在本计划内（归计划四：切换手册）

- 探测生产账户的 `get_instrument_detail` 盘后资格字段（需真实 QMT 会话）
- 探测收盘价终态信号 `timetag` 是否真实可用（需真实 QMT 会话；spec 4.7.1）
- 用旧配置清空零星一手持仓
- 退役 pr49 探针实例、渲染并装机新运行时
- 新账本以券商现金快照起账
- 切 cron 到新 config id、launchd plist 与 crontab 示例的策略 id
- 三处 `OPERATOR_PROBE` 主策略 id 常量（`operator_probe.MAIN_STRATEGY_ID`、
  `web/api.MAIN_REAL_STRATEGY_ID`、`live_config._OPERATOR_PROBE_MAIN_STRATEGY_ID`）——
  这三个只在 `kind: OPERATOR_PROBE` 下生效，探针退役后即成惰性代码，随退役一起处理
- `live_trading/README.md` 的活动系统/固定契约表/受控晋级三章改写（切换完成后才是事实）
- `README_QMT.md` 里「主策略本地副本 / 探针本地副本」两段示例的更新，以及
  `BRIDGE_ROOT` / `OTHER_BRIDGE_ROOT` 在探针退役后的取值——都描述部署状态，随装机一起改
- `AGENTS.md` 第 1 条、`backtest/EXPERIMENT_STANDARD.md` 第 1.4 节、
  `backtest/experiments/LESSONS.md` 的补记
- 建仓期前 5 个交易日的人工核对
