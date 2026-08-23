# 实盘 BT v4 真阶梯 · 计划二：解锁 dry-run 与 bridge 执行层

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先把计划一遗留的三处 fail-closed 障碍清掉、把全A dry-run 真正跑通并与 BT v4 的官方预测帧逐点对齐，再在 QMT bridge 里实现提交时刻的精确定量、同名买卖抵销、卖单终态即触发买单、以及收盘价终态门禁下的自适应提交。

**Architecture:** 分两部分。**Part A（Task 1–4）**解锁：扩 parity 门禁认识五种子 / 真阶梯 / 宇宙过滤 / 执行通道，修掉 `filter_pipe` 的运行时 `KeyError` 与买单口径偏离，然后跑通 dry-run 并用 BT v4 的 `external_pred.pkl` 做逐点对账。**Part B（Task 5–10）**执行层：抵销与定量实现为**不依赖 `ContextInfo` 的纯函数**（收盘价与股票代码作为入参），在批次的首个交易 pass 上一次性算定并**冻结进 `batch.orders`**（`_save_active_state` 已持久化该字段），SELL / BUY 两个阶段只消费冻结结果。抵销的账本语义靠回执新增字段 `netted_qty` 传回 Mac，而不靠伪造成交额——伪造会被 `apply_fill` 重新计费，正好抹掉抵销省下的手续费。

**Tech Stack:** Python 3.11（Mac 侧，`/opt/anaconda3/envs/qlib/bin/python`）；Python 3.6 ASCII-only（QMT 内置 bridge，文件声明 gbk）；pytest；SQLite；PyYAML；pandas。

## Global Constraints

- 实验规范单一事实来源：`backtest/EXPERIMENT_STANDARD.md`。执行层回测基线为 **BT v4 · v4 RankIC ES 真阶梯 k3×h5**（`baseline/phase-m-v1-bt-v4`），本计划**不改回测侧任何数字**。
- 设计单一事实来源：`docs/superpowers/specs/2026-08-23-live-v4-cohort-ladder-netting-design.md`（下称 spec）。
- 本计划全程**不动任何调度**：不改 `crontab`、不改 `run_*_cron.sh` 的 config id、不 render QMT 运行时源码、不创建授权 marker。唯一的执行动作是 `--dry-run`。
- **不改回测侧的 `CohortLadderStrategy`**，不引入 `force_sell_rank` / `refill_force_sell`，不改 CSI1000 研究轨道（B6-M / B4-S）的任何定义。
- 存量 `TopkDropoutStrategy` 配置（`csi1000_b6m_b2s_postclose`、`csi1000_b6m_b2s_postclose_real`、`csi300_topk10_live`）的行为**不得改变**；`tests/live_trading/` 全套必须继续通过。
- `MAX_ORDER_QUANTITY = 100` 在本计划内**保持不动**（理由见「不在本计划内」一节）。写 bridge 代码时按它可能为 0 也可能为 100 来设计，测试里显式改成 0。
- bridge 文件（`live_trading/qmt_strategy/qmt_signal_bridge.py`）运行在 QMT 内置 Python 3.6：**不得使用** f-string 之外的新语法（no walrus、no `dataclass`、no type hints、no `math.isqrt`）、**不得出现非 ASCII 字符**（文件头声明 gbk，ASCII 是其子集；中文注释会破坏两种编码下的一致性）。该文件里的注释一律写英文。
- macOS 下禁止用 heredoc / stdin 运行会触发 Qlib 并行取数的代码（见 `.cursor/rules/qlib-shell-multiprocessing.mdc`）。Task 4 的 dry-run 与核对脚本必须落成真实 `.py` 文件再 `python <file>` 执行。
- 固定 5 种子 `[42, 1000, 2000, 3000, 4000]`；时间划分 valid `2020-01-13~2021-07-15`、test `2021-07-16~2026-07-16`，禁止用 test 调参（本计划不训练模型，此约束仅约束不要拿 dry-run 结果去反向改参数）。

---

## 前置事实（已在本仓库核实，不要重新推断）

**计划一交付了什么。** Mac 侧信号链路（五种子合成 → 全A 四重宇宙过滤 → 阶梯下单意图）与分层账本（三张表 + `reconciled_state` / `advanced_state` + 回执导入后推进）已实现并有单测；`live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml` 与五个精简 artifact（`live_trading/models/v4_rankices/s{42,1000,2000,3000,4000}/trained_model`，合计 8.1 MB，SHA-256 已核对一致）已就位。**但全A dry-run 一次都没跑过**，被 parity 门禁挡在 `ParityError: parity backtest config not found`。

**Task 1 要修的 parity 现状。** `live_trading/modules/backtest_parity.py` 的 `validate_backtest_parity` 是一张扁平的 `comparisons` 列表（第 58–136 行），写死了单模型（`model.model_path` / `model.sha256`）与 TopkDropout 字段（`n_drop` / `hold_thresh` / `initial_buy_count`），完全没有比对宇宙过滤和执行通道。对照回测配置 `backtest/configs/alla_v4_ladder_k3h5_parity.yaml` 不存在。

**存量 parity 配置的通道字段缺口。** 三个存量 parity 配置（`csi1000_b6m_b2s_postclose_parity.yaml`、`csi1000_b6m_b2s_postclose_real_parity.yaml`、`csi300_live_parity.yaml`）都**没有** `parity.execution_session` / `parity.signal_price_type`。而 live 侧 `csi1000_b6m_b2s_postclose*.yaml` 有 `live.execution_session: "CLOSE_AUCTION"`，`csi300_topk10_live.yaml` **完全没有** `live.execution_session` 字段。所以通道比对必须能表达「两侧都没有 → 放行」，否则会打断正在运行的 CSI1000 实盘。

**Task 2 要修的是一个真实崩溃，不是洁癖。** `qlib/data/data.py:718` 用 `getattr(F, filter_config["filter_type"]).from_config(filter_config)` 构造过滤器，而 `qlib/data/filter.py:296-301` 的 `NameDFilter.from_config` 用 **直接下标** 取 `config["filter_start_time"]` / `config["filter_end_time"]`。计划一写进 live 配置的 `filter_pipe` 条目只有 `filter_type` 与 `name_rule_re` 两个键。已实测确认：

```
$ python -c "from qlib.data.filter import NameDFilter; NameDFilter.from_config(
    {'filter_type': 'NameDFilter', 'name_rule_re': '^(SH60|SH68|SZ00|SZ30)'})"
KeyError: 'filter_start_time'
```

BT v4 回测配置 `bt_m0h20rankices_all_ladder_k3h5_ensemble.yaml:18-22` 的同一条目带着 `filter_start_time: null` / `filter_end_time: null`，所以回测跑得通。计划一的单测只断言了「kwarg 被透传」，没有真正构造过滤器，因此漏掉了这个必崩路径。

**Task 3 要修的两处买单口径偏离。** 回测 `qlib/contrib/strategy/cohort_ladder.py` 的 `_orders_for_names`（第 534–569 行）：`value = budget / len(names)`，`names` 是 `select_ladder_buys` 在**完整截面**上取的 top-k，取不到价的票在**除完之后**才被 `continue` 跳过。而计划一的 `live_trading/modules/cohort_order_manager.py:99-112` 反过来：先用 `close_prices` 预筛截面再选 top-k（等于替补了下一名，**违反 spec 4.7 已批准的「实盘不顺延」**），再用 `budget / self.topk`（`len(buys) < topk` 时与回测不等）。另外买单腿在 Mac 侧**根本不需要价格**——BUY 只带 `target_value`，`close_prices` 只被卖出所得预估用到，所以这个预筛既错又无用。

**Task 5 要锁死的精度事实。** 回测的买入股数是 `qlib/backtest/exchange.py:769-792`：

```python
return (deal_amount * factor + 0.1) // self.trade_unit * self.trade_unit / factor
```

调用点 `_orders_for_names` 传的是 `round_amount_by_trade_unit(value / buy_price, factor)`，其中 `buy_price` 是**复权**收盘价。真实股数 = 返回值 × factor = `(V / raw_close + 0.1) // 100 * 100`（因为 `raw_close = adj_close / factor`）。所以 **factor 会完全约掉**，实盘用未复权收盘价 `C` 的等价公式是：

```
B = int((V / C + 0.1) // lot) * lot
```

而 bridge 现有的 `_target_requested_quantity`（第 2569–2572 行）是 `int(V / C / 100.0) * 100`，**丢了那个 `+0.1`**。差异不是零点几股而是**整整一手**：`V/C = 299.95` 时回测得 300 股、bridge 得 200 股。窗口是 `V/C mod 100 ∈ [99.9, 100)`，约 0.1%/单，每年 750 单里出现不到一次——但出现时是该名义仓位的 33% 偏差。这条就是 spec 第 6 节要求的「精确性回归」的实际内容。

**`+0.1` 的副作用。** 因为 `B` 可能比 `V/C` 多出至多 0.1 股，`B × C` 可以**超过** `target_value` 至多 `0.1 × C` 元。`live_trading/modules/fill_importer.py:1612-1617` 的守卫是 `fill_gross > order["target_value"] + 1e-6` → 抛 `SchemaError`。所以 Task 5 必须把容差改成 `+ 0.1 * avg_price + 1e-6`，否则那 0.1% 的单子会在回执导入时硬失败。

**Task 6 要放开的两处整百假设。** 计划一只放开了 `signal_schema.validate_order`。还剩两处会拒收含零股的到期层（零股来自 `absorb_broker_excess` 吸收的送股）：

- `live_trading/qmt_strategy/qmt_signal_bridge.py:1385-1387`：`quantity % 100 != 0` → `reject("SELL quantity must be a positive whole lot")`，整批拒收
- `live_trading/modules/fill_importer.py:1591-1595`：`fill.requested_qty % 100 != 0` → `SchemaError`，回执导入失败

**Task 7 为什么不能靠伪造成交额传递抵销。** `apply_fill`（`fill_importer.py:1659-1673`）对 `mode == "LIVE"` 且 `status in {"FILLED", "PARTIAL"}` 的回执**自己算费用**（`_apply_fee_delta`）。若把被抵销的两腿写成正常成交回执，Mac 会给这两腿重新计一遍佣金 + 印花税——正好等于抵销本该省下的钱。所以抵销必须走一个**不计入持仓、不计费**的通道：新增回执字段 `netted_qty`，状态用 `SKIPPED`（已在 `TERMINAL_FILL_STATUS` 里、不在 `_POSITION_STATUS = {"FILLED", "PARTIAL"}` 里）。`signal_schema._from_dict`（第 40–42 行）会丢弃未知键，所以加字段对旧回执文件前后兼容。

**Task 7 为什么 Mac 一定要拿到「转记股数」。** `advanced_state(state, horizon, trade_date, sold, filled)` 先 `settle(sold)` 再 `add(filled)`。抵销后正确的账本动作是：到期层退掉 `S`、今日层记入 `B`。设实际成交 `f`：

| 情形 | 提交 | `sold` 应为 | `filled` 应为 |
|---|---|---|---|
| `B > S`（净买） | BUY `B−S` 股，成交 `f` | `S` | `S + f` |
| `B < S`（净卖） | SELL `S−B` 股，成交 `g` | `B + g` | `B` |
| `B == S` | 无单 | `S` | `B` |

三行都要用到转记股数 `T = min(S, B)`，而 `B` 只在 bridge 的提交时刻才确定。所以 `T` 必须随回执回传，Mac 无从推算（`B` 还会被现金封顶削减）。统一表达为 `sold = applied_qty + netted_qty`、`filled = applied_qty + netted_qty`，上表三行全部自动成立。

**Task 7 为什么只在 `S` 是整百倍数时抵销。** `S` 可以是零股（`absorb_broker_excess` 吸收送股后整层一次性卖出是合规的）。若 `S = 120`、`B = 300`，`net = 180` 股——**买入不允许非整百**。拆成「买 100 + 转记 120」会让该层少 80 股，拆成「买 200 + 卖 20」的 20 股零股卖单又不合规。所以 `S % lot != 0` 时该票整体走不抵销的老路（两腿都正常下单），代价是那一次的往返费。这种票每年最多出现几只。

**Task 8/9 要改的 profile 与时点。** bridge 顶部 `_EXECUTION_PROFILES`（第 63–88 行）与 Mac 侧 `live_trading/modules/execution_profile.py` 是**两份**必须同步的 profile 表。`_activate_profile_settings`（第 512–525 行）把 profile 值刷进模块级常量，`_process_batch` 第 3122 行用 `now < TRADE_START` 门禁。`live_config.py:96-104` 逐项校验 live 配置的四个时点等于 Mac profile 的同名字段，所以**改 profile 必须同步改所有引用该 profile 的 live 配置**，否则那些配置会 fail-closed。当前引用 `AFTER_HOURS_FIXED_PRICE` 的有两个：`alla_v4_ladder_k3h5_postclose_real.yaml` 与 `csi1000_pr49_one_lot_probe.yaml`。这两个配置里的 `submit_after` 等字段是**纯校验字段**（Mac 侧不用它调度任何东西，实际时点由 QMT-local 编译副本里的常量决定），所以改它们对正在跑的探针没有功能影响——探针的编译副本要到 Plan 3 重新 render 时才会变。

**Task 9 的探测无法在 Mac 上完成。** spec 4.7.1 要求先探明 tick 的 `timetag` 是否存在。那需要真实 QMT 会话，本计划做不到。因此 Task 9 只实现**机制**：纯函数 `_close_is_final` 在拿不到终态信号时返回 `None`，调用方把 `None` 当作「未终态」处理，于是行为自动退化成 spec 规定的「固定 15:01 兜底」。探测结论由 Plan 3 的操作步骤填。

**spec 4.1 的一处笔误（不要照着找）。** spec 说要改「`qmt_signal_bridge.py` 第 1488 行附近的允许 strategy_id 列表」。第 1487–1490 行是 `_SNAPSHOT_REQUEST_STRATEGIES`，它只在第 1548 行门禁**快照观测请求**，与订单批次无关；bridge 对批次的 `header.strategy_id` 只做日志记录，没有任何允许列表。所以「bridge 会拒收批次」的说法不成立，这条属于 Plan 3 的快照工作流范围。

**Task 4 的对账锚点。** BT v4 基线 run 的目录 `backtest/result/20260822_233132_phase_s_m0h20rankices_all_ladder_k3h5_ensemble/` 下有 `external_pred.pkl`：`DataFrame`，索引 `['datetime', 'instrument']`，单列 `score`，6,914,485 行，日期范围 `2020-08-03 ~ 2026-07-31`（1454 个交易日）。**这就是回测实际消费的合成预测帧**，是比对 Mac 侧五种子合成结果的权威锚点。因此 Task 4 用 `trade_date = 2026-07-31`（signal_date 自动落在 2026-07-30）。

**测试夹具现状。** bridge 测试 `tests/live_trading/test_qmt_bridge_logic.py` 用 `importlib.util.spec_from_file_location` 加载模块（第 30–39 行 `bridge` fixture），`_activate_profile(bridge, profile, root, other_root)`（第 54–59 行）切 profile，`_order()`（第 1243 行）造订单，`_write_batch()`（第 1256 行）写批次，`_read_fills()` / `_read_events()` 读产物，`_TickCtx`（第 2192 行）是假 `ContextInfo`（`get_full_tick` 返回 `lastPrice` / `askPrice` / `bidPrice`，`get_instrumentdetail` 返回 `UpStopPrice` / `DownStopPrice` / 盘后资格字段）。新测试一律复用这些，不要另造一套。

---

# Part A — 解锁全A dry-run

### Task 1: parity 门禁认识五种子、真阶梯、宇宙过滤与执行通道

**Files:**
- Modify: `live_trading/modules/backtest_parity.py:40-146`
- Create: `backtest/configs/alla_v4_ladder_k3h5_parity.yaml`
- Modify: `backtest/configs/csi1000_b6m_b2s_postclose_parity.yaml:10-19`
- Modify: `backtest/configs/csi1000_b6m_b2s_postclose_real_parity.yaml:10-19`
- Modify: `backtest/configs/csi300_live_parity.yaml:10-17`
- Test: `tests/live_trading/test_backtest_parity.py`

**Interfaces:**
- Consumes: 计划一的 `live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml`（含 `model.members` 五条、`universe_filter` 四项、`strategy.{netting,absorb_broker_excess,no_buyable_substitution}: "live_only"`）；`live_trading/modules/execution_profile.get_execution_profile(name) -> ExecutionProfile`。
- Produces: `validate_backtest_parity(live: dict, backtest: dict) -> None`（签名不变，行为扩展）；`validate_configured_backtest(live: dict, project_root: Path) -> Path`（签名不变）。Task 4 依赖它对新配置放行。

- [ ] **Step 1: 写失败测试——真阶梯 / 五种子 / 宇宙 / 通道四组分支**

在 `tests/live_trading/test_backtest_parity.py` 末尾追加。文件顶部已有 `import pytest`、`import yaml`、`from pathlib import Path`、`REPO_ROOT`（第 13 行）以及从 `backtest_parity` 的导入；只需确认 `validate_configured_backtest` 在导入清单里，**不要重复定义 `REPO_ROOT`**。

```python
LADDER_LIVE_PATH = (
    REPO_ROOT / "live_trading" / "configs"
    / "alla_v4_ladder_k3h5_postclose_real.yaml"
)
LADDER_PARITY_PATH = (
    REPO_ROOT / "backtest" / "configs" / "alla_v4_ladder_k3h5_parity.yaml"
)


def _ladder_pair():
    with open(LADDER_LIVE_PATH, encoding="utf-8") as handle:
        live = yaml.safe_load(handle)
    with open(LADDER_PARITY_PATH, encoding="utf-8") as handle:
        backtest = yaml.safe_load(handle)
    return live, backtest


def test_ladder_pair_passes_parity_as_shipped():
    live, backtest = _ladder_pair()
    validate_backtest_parity(live, backtest)


def test_ladder_config_resolves_its_parity_backtest_from_disk():
    with open(LADDER_LIVE_PATH, encoding="utf-8") as handle:
        live = yaml.safe_load(handle)
    assert validate_configured_backtest(live, REPO_ROOT) == LADDER_PARITY_PATH


def test_ladder_horizon_mismatch_is_caught():
    live, backtest = _ladder_pair()
    backtest["strategy"]["horizon"] = 4
    with pytest.raises(ParityError, match="strategy.horizon"):
        validate_backtest_parity(live, backtest)


def test_ladder_never_compares_topk_dropout_only_fields():
    """真阶梯没有 n_drop / hold_thresh；比对它们只会拿 <missing> 和 <missing> 相等，
    看似通过实则什么都没查，还会掩盖配置里真的写错了这些字段的情况。"""
    live, backtest = _ladder_pair()
    live["strategy"]["n_drop"] = 99
    validate_backtest_parity(live, backtest)


def test_unknown_strategy_class_is_rejected_not_silently_passed():
    live, backtest = _ladder_pair()
    live["strategy"]["class"] = "SomeFutureStrategy"
    backtest["strategy"]["class"] = "SomeFutureStrategy"
    with pytest.raises(ParityError, match="unknown strategy class"):
        validate_backtest_parity(live, backtest)


def test_member_set_is_order_insensitive_but_content_sensitive():
    live, backtest = _ladder_pair()
    backtest["parity"]["model_members"] = list(
        reversed(backtest["parity"]["model_members"])
    )
    validate_backtest_parity(live, backtest)

    backtest["parity"]["model_members"][0]["sha256"] = "0" * 64
    with pytest.raises(ParityError, match="model.members"):
        validate_backtest_parity(live, backtest)


def test_member_count_mismatch_is_caught():
    live, backtest = _ladder_pair()
    backtest["parity"]["model_members"] = backtest["parity"]["model_members"][:4]
    with pytest.raises(ParityError, match="model.member_count"):
        validate_backtest_parity(live, backtest)


def test_ensemble_live_against_single_model_backtest_fails_closed():
    live, backtest = _ladder_pair()
    del backtest["parity"]["model_members"]
    backtest["parity"]["model_path"] = "whatever"
    with pytest.raises(ParityError, match="model.members"):
        validate_backtest_parity(live, backtest)


@pytest.mark.parametrize(
    "key,bad",
    [
        ("st_daily", "scripts/other.csv"),
        ("min_amount", 5_000_000),
        ("min_listing_days", 30),
        ("min_recent_trading_days", 30),
        ("pool", "csi1000"),
    ],
)
def test_each_universe_filter_key_is_compared(key, bad):
    live, backtest = _ladder_pair()
    backtest["universe_filter"][key] = bad
    with pytest.raises(ParityError, match="universe_filter." + key):
        validate_backtest_parity(live, backtest)


def test_universe_filter_missing_on_one_side_only_fails_closed():
    live, backtest = _ladder_pair()
    del backtest["universe_filter"]
    with pytest.raises(ParityError, match="universe_filter"):
        validate_backtest_parity(live, backtest)


def test_after_hours_channel_requires_close_deal_price():
    """盘后固定价恒以收盘价撮合。回测改 vwap 则通道语义不再对应，必须 fail。"""
    live, backtest = _ladder_pair()
    backtest["backtest"]["exchange_kwargs"]["deal_price"] = "vwap"
    with pytest.raises(ParityError, match="deal_price"):
        validate_backtest_parity(live, backtest)


def test_execution_session_mismatch_is_caught():
    live, backtest = _ladder_pair()
    backtest["parity"]["execution_session"] = "CLOSE_AUCTION"
    with pytest.raises(ParityError, match="execution_session"):
        validate_backtest_parity(live, backtest)


def test_signal_price_type_is_derived_from_the_profile_not_trusted():
    """live 配置里没有 signal_price_type 字段；它必须由 profile 推出来再比，
    否则改了 execution_session 却忘了改 parity 配置就查不出来。"""
    live, backtest = _ladder_pair()
    backtest["parity"]["signal_price_type"] = "CLOSE_AUCTION_LIMIT"
    with pytest.raises(ParityError, match="signal_price_type"):
        validate_backtest_parity(live, backtest)


@pytest.mark.parametrize(
    "marker",
    ["netting", "absorb_broker_excess", "no_buyable_substitution"],
)
def test_live_only_deviation_missing_on_either_side_fails_closed(marker):
    live, backtest = _ladder_pair()
    del live["strategy"][marker]
    with pytest.raises(ParityError, match=marker):
        validate_backtest_parity(live, backtest)

    live, backtest = _ladder_pair()
    del backtest["parity"][marker]
    with pytest.raises(ParityError, match=marker):
        validate_backtest_parity(live, backtest)
```

- [ ] **Step 2: 写失败测试——三个存量配置不受影响**

```python
_LEGACY_PAIRS = [
    (
        "live_trading/configs/csi1000_b6m_b2s_postclose.yaml",
        "backtest/configs/csi1000_b6m_b2s_postclose_parity.yaml",
    ),
    (
        "live_trading/configs/csi1000_b6m_b2s_postclose_real.yaml",
        "backtest/configs/csi1000_b6m_b2s_postclose_real_parity.yaml",
    ),
    (
        "live_trading/configs/csi300_topk10_live.yaml",
        "backtest/configs/csi300_live_parity.yaml",
    ),
]


@pytest.mark.parametrize("live_rel,backtest_rel", _LEGACY_PAIRS)
def test_shipped_topk_dropout_pairs_still_pass(live_rel, backtest_rel):
    with open(REPO_ROOT / live_rel, encoding="utf-8") as handle:
        live = yaml.safe_load(handle)
    with open(REPO_ROOT / backtest_rel, encoding="utf-8") as handle:
        backtest = yaml.safe_load(handle)
    validate_backtest_parity(live, backtest)


def test_topk_dropout_still_compares_its_own_fields():
    live_rel, backtest_rel = _LEGACY_PAIRS[1]
    with open(REPO_ROOT / live_rel, encoding="utf-8") as handle:
        live = yaml.safe_load(handle)
    with open(REPO_ROOT / backtest_rel, encoding="utf-8") as handle:
        backtest = yaml.safe_load(handle)
    backtest["strategy"]["kwargs"]["hold_thresh"] = 99
    with pytest.raises(ParityError, match="strategy.hold_thresh"):
        validate_backtest_parity(live, backtest)


def test_live_config_without_execution_session_still_passes():
    """csi300_topk10_live 没有 live.execution_session；两侧都没有才放行。"""
    live_rel, backtest_rel = _LEGACY_PAIRS[2]
    with open(REPO_ROOT / live_rel, encoding="utf-8") as handle:
        live = yaml.safe_load(handle)
    with open(REPO_ROOT / backtest_rel, encoding="utf-8") as handle:
        backtest = yaml.safe_load(handle)
    assert "execution_session" not in live["live"]
    validate_backtest_parity(live, backtest)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_backtest_parity.py -q`
Expected: FAIL —— 新增用例大量报 `FileNotFoundError`（`alla_v4_ladder_k3h5_parity.yaml` 不存在）。

- [ ] **Step 4: 建对照回测配置**

创建 `backtest/configs/alla_v4_ladder_k3h5_parity.yaml`。内容镜像 `backtest/configs/regime-adapt/phase-s/bt_m0h20rankices_all_ladder_k3h5_ensemble.yaml` 并补 `parity.*`。注意 `filter_start_time` / `filter_end_time` 两个 null 键必须保留（Task 2 会让 live 侧也带上它们并逐字比对）。

```yaml
# Deployment-parity config for alla_v4_ladder_k3h5_postclose_real.
# Mirrors backtest/configs/regime-adapt/phase-s/bt_m0h20rankices_all_ladder_k3h5_ensemble.yaml
# (BT v4 baseline: baseline/phase-m-v1-bt-v4) plus the parity.* fields.
# Do not hand-tune: any drift here means the live gate stops matching the baseline.

run:
  mode: backtest_only
  note: "alla_v4_ladder_k3h5_parity"
  n_runs: 1
  generate_figures: false

parity:
  live_config: "live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml"
  model_experiment_name: "regime-adapt/m0-h20-rankices-v1"
  model_members:
    - model_path: "live_trading/models/v4_rankices/s42/trained_model"
      sha256: "3c75ee96fb8868d1200145b97141529b8852a204c6f4615d321a93da71368ecd"
    - model_path: "live_trading/models/v4_rankices/s1000/trained_model"
      sha256: "38f9dea415b528916105a6f472adf9b1ff3032fca92250c4d66db0323f5acdbc"
    - model_path: "live_trading/models/v4_rankices/s2000/trained_model"
      sha256: "ff605722c4beece2cfa38324c0e3480c0fbb96fcfc35774a2838f39c4e131729"
    - model_path: "live_trading/models/v4_rankices/s3000/trained_model"
      sha256: "75d8f5a38ce13683a7f75760b220e678ecb9c055d93be2bb83df4edcc636b260"
    - model_path: "live_trading/models/v4_rankices/s4000/trained_model"
      sha256: "ff795a6fe4500e0e2affc9c8aa14be603173290a93ee3b04fc91907796ff5525"
  execution_price_proxy: "close"
  broker_environment: "REAL"
  close_auction_price_type: 49
  execution_session: "AFTER_HOURS_FIXED_PRICE"
  signal_price_type: "AFTER_HOURS_CLOSE"
  # Live-only, backtest-absent deviations. Registered explicitly so a missing
  # field is a ParityError rather than a silent pass. See spec 4.4 / 4.3.1 / 4.7.
  netting: "live_only"
  absorb_broker_excess: "live_only"
  no_buyable_substitution: "live_only"

data:
  provider_uri: "~/.qlib/qlib_data/cn_data"
  region: "cn"
  instruments: "all"
  benchmark: "SH000985"
  handler:
    class: "Alpha158Technical"
    module_path: "backtest.features.technical"
    start_time: "2020-02-03"
    end_time: "2026-07-31"
    fit_start_time: "2020-02-03"
    fit_end_time: "2020-08-03"
    infer_processors:
      - class: "ProcessInf"
    feature_groups:
      - "range"
    instruments:
      market: "all"
      filter_pipe:
        - filter_type: "NameDFilter"
          name_rule_re: "^(SH60|SH68|SZ00|SZ30)"
          filter_start_time: null
          filter_end_time: null

segments:
  train: ["2020-02-03", "2020-07-31"]
  valid: ["2020-08-03", "2026-07-31"]
  test: ["2020-08-03", "2026-07-31"]

model:
  class: "RegimeSingleLGBMModel"
  module_path: "backtest.models.regime_adapt"

strategy:
  class: "CohortLadderStrategy"
  module_path: "qlib.contrib.strategy.signal_strategy"
  topk: 3
  horizon: 5
  kwargs:
    risk_degree: 0.9
    only_tradable: false
    forbid_all_trade_at_limit: false

backtest:
  account: 1000000.0
  exchange_kwargs:
    freq: "day"
    deal_price: "close"
    limit_threshold: "market_cn"
    open_cost: 0.00021
    close_cost: 0.00071
    min_cost: 5.0
    trade_unit: 100

universe_filter:
  st_daily: "scripts/data_collector/tushare/st_daily.csv"
  min_amount: 10000000
  min_listing_days: 60
  min_recent_trading_days: 60
  pool: "all"
```

- [ ] **Step 5: 给三个存量 parity 配置补通道字段**

在每个文件的 `parity:` 段里加两行。这不是为了新配置，而是**给存量系统也把通道纳入门禁**——原先 `execution_session` 完全没有比对。

`csi1000_b6m_b2s_postclose_parity.yaml` 与 `csi1000_b6m_b2s_postclose_real_parity.yaml` 各加：

```yaml
  execution_session: "CLOSE_AUCTION"
  signal_price_type: "CLOSE_AUCTION_LIMIT"
```

`csi300_live_parity.yaml` **不加**：其 live 对照 `csi300_topk10_live.yaml` 没有 `live.execution_session`，两侧都缺才是一致状态。

- [ ] **Step 6: 重构 `backtest_parity.py` 为分组比对**

把 `validate_backtest_parity` 里的扁平列表拆成五个返回 `list` 的私有函数，主函数只做拼接与报错。在 `import` 区加 `from live_trading.modules.execution_profile import get_execution_profile`。

```python
_LADDER_CLASS = "CohortLadderStrategy"
_TOPK_DROPOUT_CLASS = "TopkDropoutStrategy"
_LIVE_ONLY_DEVIATIONS = ("netting", "absorb_broker_excess", "no_buyable_substitution")
_UNIVERSE_KEYS = (
    "st_daily", "min_amount", "min_listing_days",
    "min_recent_trading_days", "pool",
)
# 通道与成交价是绑定关系：盘后固定价恒以收盘价撮合。
_SESSION_DEAL_PRICE = {"AFTER_HOURS_FIXED_PRICE": "close"}


def _model_members(config: dict, path: str):
    """把成员列表规整成排序后的 (model_path, sha256) 序列；缺该段返回 None。"""
    members = _optional(config, path)
    if members is None:
        return None
    if not isinstance(members, list) or not members:
        raise ParityError(f"{path} must be a non-empty list")
    normalized = []
    for member in members:
        if not isinstance(member, dict):
            raise ParityError(f"{path} entries must be mappings")
        for key in ("model_path", "sha256"):
            if not member.get(key):
                raise ParityError(f"{path} entries require a non-empty {key}")
        normalized.append((str(member["model_path"]), str(member["sha256"])))
    # 顺序无关、集合必须相等：种子的书写顺序不承载语义。
    return sorted(normalized)


def _model_comparisons(live: dict, backtest: dict) -> list:
    comparisons = [
        ("model.experiment_name", _get(live, "model.experiment_name"),
         _get(backtest, "parity.model_experiment_name")),
    ]
    live_members = _model_members(live, "model.members")
    backtest_members = _model_members(backtest, "parity.model_members")
    if live_members is None and backtest_members is None:
        return comparisons + [
            ("model.experiment_id", _get(live, "model.experiment_id"),
             _get(backtest, "parity.model_experiment_id")),
            ("model.recorder_id", _get(live, "model.recorder_id"),
             _get(backtest, "parity.model_recorder_id")),
            ("model.model_path", _get(live, "model.model_path"),
             _get(backtest, "parity.model_path")),
            ("model.sha256", _get(live, "model.sha256"),
             _get(backtest, "parity.model_sha256")),
        ]
    return comparisons + [
        ("model.member_count",
         -1 if live_members is None else len(live_members),
         -1 if backtest_members is None else len(backtest_members)),
        ("model.members", live_members, backtest_members),
    ]


def _strategy_comparisons(live: dict, backtest: dict) -> list:
    live_class = _get(live, "strategy.class")
    backtest_class = _get(backtest, "strategy.class")
    shared = [
        ("strategy.class", live_class, backtest_class),
        ("strategy.topk", _get(live, "strategy.topk"),
         _get(backtest, "strategy.topk")),
        ("strategy.risk_degree", _get(live, "strategy.risk_degree"),
         _get(backtest, "strategy.kwargs.risk_degree")),
        ("strategy.only_tradable", _get(live, "strategy.only_tradable"),
         _get(backtest, "strategy.kwargs.only_tradable")),
        ("strategy.forbid_all_trade_at_limit",
         _get(live, "strategy.forbid_all_trade_at_limit"),
         _get(backtest, "strategy.kwargs.forbid_all_trade_at_limit")),
    ]
    if live_class != backtest_class:
        # 类都不一样，再比只有一侧存在的字段只会刷出噪音掩盖真正的错。
        return shared
    if live_class == _LADDER_CLASS:
        return shared + [
            ("strategy.horizon", _get(live, "strategy.horizon"),
             _get(backtest, "strategy.horizon")),
        ]
    if live_class == _TOPK_DROPOUT_CLASS:
        return shared + [
            ("strategy.n_drop", _get(live, "strategy.n_drop"),
             _get(backtest, "strategy.n_drop")),
            ("strategy.hold_thresh", _get(live, "strategy.hold_thresh"),
             _get(backtest, "strategy.kwargs.hold_thresh")),
            ("strategy.initial_buy_count",
             _optional(live, "strategy.initial_buy_count"),
             _optional(backtest, "strategy.kwargs.initial_buy_count")),
        ]
    raise ParityError(f"unknown strategy class for parity: {live_class!r}")


def _universe_comparisons(live: dict, backtest: dict) -> list:
    live_section = _optional(live, "universe_filter")
    backtest_section = _optional(backtest, "universe_filter")
    if live_section is None and backtest_section is None:
        return []      # 存量 CSI1000 / CSI300 两侧都没有这一段
    if not isinstance(live_section, dict) or not isinstance(backtest_section, dict):
        raise ParityError(
            "universe_filter must be present on both sides once either declares it: "
            f"live={type(live_section).__name__}, "
            f"backtest={type(backtest_section).__name__}"
        )
    return [
        (f"universe_filter.{key}",
         _get(live, f"universe_filter.{key}"),
         _get(backtest, f"universe_filter.{key}"))
        for key in _UNIVERSE_KEYS
    ]


def _channel_comparisons(live: dict, backtest: dict) -> list:
    session = _optional(live, "live.execution_session")
    if session is None:
        return [("live.execution_session", None,
                 _optional(backtest, "parity.execution_session"))]
    comparisons = [
        ("live.execution_session", session,
         _get(backtest, "parity.execution_session")),
        # signal_price_type 不在 live 配置里，必须由 profile 推出来，
        # 否则改了 execution_session 忘改 parity 配置就查不出来。
        ("live.signal_price_type",
         get_execution_profile(session).signal_price_type,
         _get(backtest, "parity.signal_price_type")),
    ]
    bound = _SESSION_DEAL_PRICE.get(session)
    if bound is not None:
        comparisons.append((
            f"exchange.deal_price bound to {session}", bound,
            _get(backtest, "backtest.exchange_kwargs.deal_price"),
        ))
    return comparisons


def _deviation_comparisons(live: dict, backtest: dict) -> list:
    return [
        (f"live_only deviation {name}",
         _get(live, f"strategy.{name}"), _get(backtest, f"parity.{name}"))
        for name in _LIVE_ONLY_DEVIATIONS
    ]
```

`validate_backtest_parity` 主体：保留开头的费用/账户推导，把原来的 `comparisons = [...]` 大列表拆成「通用项 + 五组」。通用项即原列表**去掉** model 四项、strategy 八项、`live.close_auction_price_type` 保留不动，其余（`data.*`、`handler.*`、`backtest.*`、`exchange.*`、`live.broker_environment`）原样保留，并追加 `handler.filter_pipe` 一项（Task 2 会让两侧对齐）：

```python
    comparisons = _common_comparisons(live, backtest, opening_account,
                                      buy_cost, sell_cost)
    for group in (_model_comparisons, _strategy_comparisons,
                  _universe_comparisons, _channel_comparisons,
                  _deviation_comparisons):
        comparisons.extend(group(live, backtest))

    mismatches = [
        f"{path}: live={left!r}, backtest={right!r}"
        for path, left, right in comparisons
        if not _equal(left, right)
    ]
    if mismatches:
        raise ParityError(
            "Live/Backtest parity mismatch:\n- " + "\n- ".join(mismatches)
        )
```

其中 `_common_comparisons(live, backtest, opening_account, buy_cost, sell_cost) -> list` 就是把原第 69–89 行与第 108–135 行的条目搬进一个函数，**再加一条**：

```python
        ("handler.filter_pipe", _optional(live, "handler.filter_pipe"),
         _optional(backtest, "data.handler.instruments.filter_pipe")),
```

- [ ] **Step 7: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_backtest_parity.py -q`
Expected: PASS 全绿。

- [ ] **Step 8: 跑门禁脚本核对四个配置**

Run:
```bash
for c in alla_v4_ladder_k3h5_postclose_real csi1000_b6m_b2s_postclose \
         csi1000_b6m_b2s_postclose_real csi300_topk10_live; do
  echo "== $c"
  /opt/anaconda3/envs/qlib/bin/python live_trading/scripts/check_backtest_parity.py --config "$c"
done
```
Expected: 四个都通过（`alla_v4_ladder_k3h5_postclose_real` 这一个会在 Task 2 之前就通过，因为 Task 2 只改 `filter_pipe` 的两个 null 键，两侧同时改）。若 `alla_v4...` 报 `handler.filter_pipe` 不等，那是预期的——Task 2 修。

- [ ] **Step 9: 提交**

```bash
git add live_trading/modules/backtest_parity.py \
        backtest/configs/alla_v4_ladder_k3h5_parity.yaml \
        backtest/configs/csi1000_b6m_b2s_postclose_parity.yaml \
        backtest/configs/csi1000_b6m_b2s_postclose_real_parity.yaml \
        tests/live_trading/test_backtest_parity.py
git commit -m "feat(live): teach the parity gate about ensembles, the cohort ladder, universe filters and the execution channel"
```

---

### Task 2: 修 `filter_pipe` 的运行时 KeyError

**Files:**
- Modify: `live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml:51-53`
- Test: `tests/live_trading/test_live_config.py`

**Interfaces:**
- Consumes: `qlib.data.filter.NameDFilter.from_config(config: dict) -> NameDFilter`（用 `config["filter_start_time"]` / `config["filter_end_time"]` 直接下标）。
- Produces: live 配置的 `handler.filter_pipe` 与 BT v4 回测配置的 `data.handler.instruments.filter_pipe` **逐字相等**，Task 1 的 `handler.filter_pipe` 比对因此通过。

- [ ] **Step 1: 写失败测试——用真的 Qlib 构造器验证每一条 filter_pipe**

在 `tests/live_trading/test_live_config.py` 追加。不要用 mock：这个缺陷的全部要害就是「假 handler 收下了 kwarg，真 Qlib 会崩」。

```python
def test_live_filter_pipe_entries_build_real_qlib_filters():
    """计划一的 filter_pipe 少了两个键，qlib 构造过滤器时会 KeyError。
    单测必须真的构造一次，只断言 kwarg 被透传是查不出来的。"""
    from qlib.data import filter as qlib_filter

    config = _ladder_config()
    entries = config["handler"]["filter_pipe"]
    assert entries, "ladder config must declare a filter_pipe"
    for entry in entries:
        builder = getattr(qlib_filter, entry["filter_type"])
        assert builder.from_config(entry) is not None


def test_live_filter_pipe_matches_the_bt_v4_backtest_verbatim():
    import yaml

    config = _ladder_config()
    baseline = (
        REPO_ROOT / "backtest" / "configs" / "regime-adapt" / "phase-s"
        / "bt_m0h20rankices_all_ladder_k3h5_ensemble.yaml"
    )
    with open(baseline, encoding="utf-8") as handle:
        backtest = yaml.safe_load(handle)
    assert (
        config["handler"]["filter_pipe"]
        == backtest["data"]["handler"]["instruments"]["filter_pipe"]
    )
```

若文件里还没有 `REPO_ROOT` / `_ladder_config`，沿用计划一 Task 10 引入的那两个（`_ladder_config()` 读 `LADDER_LIVE_PATH`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_live_config.py -k filter_pipe -q`
Expected: FAIL —— `KeyError: 'filter_start_time'`，以及第二个用例的 `assert` 不等。

- [ ] **Step 3: 给 live 配置补两个 null 键**

`live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml` 的 `handler.filter_pipe` 改成：

```yaml
  # 两个 null 键不是冗余：NameDFilter.from_config 用直接下标取它们，缺键即 KeyError。
  # 同时与 BT v4 回测配置逐字相等，parity 才能整段比对。
  filter_pipe:
    - filter_type: "NameDFilter"
      name_rule_re: "^(SH60|SH68|SZ00|SZ30)"
      filter_start_time: null
      filter_end_time: null
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_live_config.py tests/live_trading/test_backtest_parity.py -q
```
Expected: PASS 全绿（Task 1 Step 8 里那条 `handler.filter_pipe` 不等此时消失）。

- [ ] **Step 5: 提交**

```bash
git add live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml \
        tests/live_trading/test_live_config.py
git commit -m "fix(live): give the ladder filter_pipe the two keys NameDFilter.from_config indexes directly"
```

---

### Task 3: 修买单口径与回测的两处偏离

**Files:**
- Modify: `live_trading/modules/cohort_order_manager.py:97-122`
- Test: `tests/live_trading/test_cohort_order_manager.py`

**Interfaces:**
- Consumes: `select_ladder_buys(scores: pd.Series, *, k: int, is_buyable=None) -> tuple[str, ...]`；`cohort_budget(*, total_value, cash, risk_degree, horizon) -> float`。
- Produces: `CohortOrderManager.generate_orders(...) -> list[dict]` 签名不变；BUY 意图的 `target_value` 改为 `budget / len(buys)`，选股不再受 `close_prices` 影响。

- [ ] **Step 1: 写失败测试——不顺延、按实选只数均分**

在 `tests/live_trading/test_cohort_order_manager.py` 追加。沿用文件里已有的 `CONFIG`（topk=3, horizon=5, risk_degree=0.90）与 `_scores(mapping)`。

```python
_BUDGET = 1_000_000.0 * 0.90 / 5      # cohort_budget 的稳态目标


def test_a_name_without_a_close_price_is_still_bought():
    """spec 4.7 已批准「实盘不顺延」。按价格预筛截面等于替补了下一名，
    那是回测才做的顺延，实盘不能做——买入腿在 Mac 侧根本不需要价格。"""
    orders = CohortOrderManager(CONFIG).generate_orders(
        scores=_scores(
            {"SH600001": 3.0, "SH600002": 2.0, "SH600003": 1.0, "SH600004": 0.5}
        ),
        cohort_state=CohortState(),
        broker_positions={},
        cash=1_000_000.0,
        close_prices={"SH600001": 10.0, "SH600003": 10.0},  # 600002 无价
        total_value=1_000_000.0,
    )
    bought = [o["stock_code"] for o in orders if o["side"] == "BUY"]
    assert bought == ["SH600001", "SH600002", "SH600003"]


def test_budget_is_split_by_the_number_of_names_actually_selected():
    """回测 _orders_for_names 用 budget / len(names)。截面不足 topk 时
    除以 topk 会让每只都少买，与回测不等。"""
    orders = CohortOrderManager(CONFIG).generate_orders(
        scores=_scores({"SH600001": 2.0, "SH600002": 1.0}),
        cohort_state=CohortState(),
        broker_positions={},
        cash=1_000_000.0,
        close_prices={"SH600001": 10.0, "SH600002": 10.0},
        total_value=1_000_000.0,
    )
    buys = [o for o in orders if o["side"] == "BUY"]
    assert len(buys) == 2
    assert all(o["target_value"] == pytest.approx(_BUDGET / 2) for o in buys)


def test_full_cross_section_still_splits_by_topk():
    orders = CohortOrderManager(CONFIG).generate_orders(
        scores=_scores(
            {"SH600001": 3.0, "SH600002": 2.0, "SH600003": 1.0, "SH600004": 0.5}
        ),
        cohort_state=CohortState(),
        broker_positions={},
        cash=1_000_000.0,
        close_prices={},
        total_value=1_000_000.0,
    )
    buys = [o for o in orders if o["side"] == "BUY"]
    assert len(buys) == 3
    assert all(o["target_value"] == pytest.approx(_BUDGET / 3) for o in buys)
```

同时检查文件里现有的 `test_names_missing_a_close_price_are_not_buyable`：它编码的正是被废弃的行为，**删掉它**，并在删除处留一行注释指向 `test_a_name_without_a_close_price_is_still_bought`。

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_order_manager.py -q`
Expected: FAIL —— `bought == ["SH600001", "SH600003", "SH600004"]`（顺延了）、`target_value` 是 `budget/3` 而非 `budget/2`。

- [ ] **Step 3: 改实现**

`live_trading/modules/cohort_order_manager.py` 把第 97–122 行替换为：

```python
        # 发布期不做任何可买过滤：T 日 16:00 无从判断 T+1 的封板/停牌，
        # 且 spec 4.7 已决定不顺延。买入腿只带 target_value，不需要价格，
        # 所以也不能按 close_prices 预筛截面——那等于替补了下一名。
        buys = select_ladder_buys(scores, k=self.topk, is_buyable=None)

        budget = cohort_budget(
            total_value=float(total_value),
            cash=float(cash) + self._estimated_proceeds(sells, close_prices),
            risk_degree=self.risk_degree,
            horizon=self.horizon,
        )
        # 与回测 _orders_for_names 的 budget / len(names) 一致：
        # 截面不足 topk 时除以 topk 会让每只都少买。
        per_name = budget / len(buys) if buys else 0.0
        for code in buys:
            if per_name <= 0:
                continue
            orders.append({
                "side": "BUY",
                "stock_code": code,
                "quantity": 0,
                "target_value": per_name,
                "reason": "cohort_layer",
            })
```

同时更新模块 docstring 里「只剔掉没有收盘价的票」那句描述（现在不剔了）。

- [ ] **Step 4: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_order_manager.py tests/live_trading/test_run_publish_signals.py -q`
Expected: PASS 全绿。

- [ ] **Step 5: 提交**

```bash
git add live_trading/modules/cohort_order_manager.py \
        tests/live_trading/test_cohort_order_manager.py
git commit -m "fix(live): stop substituting unpriced names and split the layer budget by names actually selected"
```

---

### Task 4: 跑通全A dry-run 并与 BT v4 官方预测帧对账

**Files:**
- Create: `live_trading/scripts/verify_ladder_dry_run.py`
- Modify: `docs/superpowers/specs/2026-08-23-live-v4-cohort-ladder-netting-design.md`（4.2 节「成本提示」补实测数字）
- Test: 无新单测——本任务的交付物是一次实测与一份对账结论

**Interfaces:**
- Consumes: `live_trading/scripts/run_publish_signals.py --config <id> --trade-date <YYYY-MM-DD> --dry-run`；`backtest/result/20260822_233132_phase_s_m0h20rankices_all_ladder_k3h5_ensemble/external_pred.pkl`。
- Produces: `verify_ladder_dry_run.py` 打印 `top3 match: True/False` 与最大分数偏差；实测墙钟秒数与峰值 RSS。

- [ ] **Step 1: 跑 dry-run，同时量墙钟与峰值内存**

`trade_date` 取 `2026-07-31`（`external_pred.pkl` 覆盖到该日；signal_date 会落在前一交易日 2026-07-30）。用 `/usr/bin/time -l` 拿峰值 RSS（macOS 输出 `maximum resident set size`，单位字节）。

Run:
```bash
/usr/bin/time -l /opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_publish_signals.py \
  --config alla_v4_ladder_k3h5_postclose_real \
  --trade-date 2026-07-31 --dry-run 2>&1 | tee /tmp/ladder_dry_run.log
```
Expected: 打印 3 个 BUY（`reason=cohort_layer`，各带 `target_value`，`quantity=0`）与 0 个 SELL（账本是空的，首日无到期层），末尾无 traceback。

**若报 `ParityError`**：回到 Task 1/2，别绕过门禁。**若报数据缺失或 ST 缓存落后**：说明 `scripts/data_collector/tushare/st_daily.csv` 没覆盖 signal_date，先更新缓存；`build_keep_mask` 的 fail-closed 是对的，不要放宽它。

- [ ] **Step 2: 写对账脚本**

创建 `live_trading/scripts/verify_ladder_dry_run.py`。**必须是真实文件再执行**（macOS 下 heredoc 会撞 Qlib 并行取数）。

```python
#!/usr/bin/env python3
"""把 dry-run 的合成分数与 BT v4 回测实际消费的预测帧逐点对账。

对账锚点选 external_pred.pkl 而不是重跑一遍合成：后者用的是同一段代码，
比出来永远相等，什么都证明不了。external_pred.pkl 是回测当时真正吃进去的
那一帧，它相等才说明实盘信号 = BT v4 官方信号。
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

BT_V4_PRED = (
    PROJECT_ROOT / "backtest" / "result"
    / "20260822_233132_phase_s_m0h20rankices_all_ladder_k3h5_ensemble"
    / "external_pred.pkl"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="alla_v4_ladder_k3h5_postclose_real")
    parser.add_argument("--signal-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--topk", type=int, default=3)
    args = parser.parse_args()

    from live_trading.modules.live_config import load_live_config
    from live_trading.modules.signal_generator import SignalGenerator
    from live_trading.modules.universe_gate import filter_scores

    config_path = PROJECT_ROOT / "live_trading" / "configs" / (args.config + ".yaml")
    config = load_live_config(config_path, PROJECT_ROOT)
    generator = SignalGenerator(config)
    generator.load_model()
    live = generator.predict(args.signal_date)
    live, stats = filter_scores(
        live, signal_date=args.signal_date,
        raw_spec=config["universe_filter"], project_root=PROJECT_ROOT,
    )
    live = live.dropna()

    reference = pd.read_pickle(BT_V4_PRED)
    day = reference.xs(pd.Timestamp(args.signal_date), level="datetime")["score"]

    common = live.index.intersection(day.index)
    max_gap = float((live[common] - day[common]).abs().max()) if len(common) else float("nan")

    live_top = list(live.sort_values(ascending=False).index[: args.topk])
    # 回测在同一份宇宙掩码上选 top-k，所以参照侧也要先对齐到 live 的宇宙。
    day_top = list(day[common].sort_values(ascending=False).index[: args.topk])

    print("universe filter stats:", stats)
    print("live names: %d, reference names on that day: %d, common: %d"
          % (len(live), len(day), len(common)))
    print("max |live - reference| on common names: %.12g" % max_gap)
    print("live top%d:      %s" % (args.topk, live_top))
    print("reference top%d: %s" % (args.topk, day_top))
    print("top%d match: %s" % (args.topk, live_top == day_top))
    return 0 if live_top == day_top else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

已核实的签名：`load_live_config(config_path, project_root=None) -> dict`、`SignalGenerator.load_model()`、`SignalGenerator.predict(target_date: str, allow_stale: bool = False) -> pd.Series`、`filter_scores(scores, *, signal_date, raw_spec, project_root) -> tuple[pd.Series, dict]`。若与实际不符，按仓库里的真实签名调整调用，**不要改它们的实现**。

- [ ] **Step 3: 跑对账**

Run:
```bash
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/verify_ladder_dry_run.py \
  --signal-date 2026-07-30
```
Expected: `top3 match: True`。

**实测（2026-08-23，signal_date 2026-07-30）：`top3 match: True`，但 `max |live - reference| = 1.33e-03`，远大于计划预期的 1e-9。已定因，结论是无害的口径差异，不需要改任何一侧的实现。**

定因过程见 `live_trading/scripts/diagnose_ladder_gap.py`（吃 `--dump-dir` 落下的 z-score **之前**的逐成员原始分数）：

1. 手算合成能复现落盘的合成到 `2.28e-11` —— 合成代码本身没问题。
2. 参考帧当日 5207 只，live 5203 只，多出的 4 只全是**指数**：`SH000300` / `SH000852` / `SH000903` / `SH000905`。live 一只都不多。
3. 合成是「按日横截面 z-score 后等权平均」，所以多这 4 行会改变当日的 μ 与 σ。参考帧的横截面均值恰好是 `+0.000000`（z-score 在含指数的 5207 只上做的），剔掉这 4 只后为 `+0.000219`、σ 从 `0.977278` 变 `0.977593`。
4. 因此差值几乎就是一个逐日仿射变换：拟合出 `scale=1.000308`、`shift=+0.000219` —— 与上面的 σ 比值 `0.977593/0.977278=1.000322` 和均值偏移 `0.000219` 对得上。残差只剩 `1.69e-05`（残差非零是因为 5 个成员各自的 (μ,σ) 偏移不同，逐成员仿射的平均不是单一仿射）。

**为什么无害：仿射变换保序。** 真阶梯只用当日 top-k，而 `scale > 0` 的仿射不改变任何名次。当日实测 4880 只里 4850 只名次完全相同、Spearman `0.999999998`，仅有的 30 处移动都在残差量级的尾部。能翻掉选股的只有那 `1.69e-05` 的非仿射残差，不是 `1.33e-03`——**用原始分数差当风险阈值会把风险高估约 80 倍。**

`live_trading/scripts/probe_topk_margin.py` 用正确尺度扫了全测试期 1211 个交易日的 rank3/rank4 间距：p1 = `5.8e-04`、p50 = `4.4e-02`；**间距低于 `1.69e-05` 的有 0 天，低于 6 倍保守上界 `1.0e-04` 的也是 0 天**（低于被误用的 `1.33e-03` 的有 31 天，2.56%——这个数字没有意义）。即这处偏差在整个测试期内一天都翻不动 top-3。

**方向性判断：live 是更正确的一侧。** 指数不可交易，本不该参与可交易截面的标准化；是 BT v4 的预测帧被 4 只指数轻微污染了。但 BT v4 是已确立的基线，改回测意味着重训重跑、重立基线（`backtest/EXPERIMENT_STANDARD.md` 6.x），而收益是零——因为它决策中性。故**登记为已知的、有界的、live-only 口径差异**，并把 dry-run 的验收口径从「绝对分数 ε」改成「top-k / 名次一致」。Plan 3 的 parity 门禁据此比名次，不比原始分数。

**若后续 top3 不等**：`max_gap` 极小但 top3 不同 → 并列名次的稳定排序问题，查 `stable_rank_scores` 与 `blend_score_series` 的排序键。`max_gap` 很大且**不是**上述仿射形态（拟合残差与 `max_gap` 同量级）→ 合成或宇宙过滤真的对不上，逐项查：成员顺序无关（z-score 后等权平均对顺序不敏感）、`filter_pipe` 是否生效（全A 应被 `^(SH60|SH68|SZ00|SZ30)` 削掉一批）、`universe_filter` 四项是否与回测同参。**不要为了让它相等去调 topk 或过滤参数**——那是拿 test 段调参。

- [ ] **Step 4: 把实测数字写回 spec 4.2**

从 `/tmp/ladder_dry_run.log` 取墙钟秒数（`real`）与 `maximum resident set size`（除以 1024³ 得 GiB）。把 spec 4.2 节末的「成本提示」段改成实测口径，例如：

```markdown
**成本提示（已实测）**。handler 从 CSI1000（约 1000 只）扩到全A（约 5400 只）后，
一次完整 `--dry-run` 实测墙钟 <N> 秒、峰值 RSS <M> GiB（2026-08-23，signal_date
2026-07-30，Apple Silicon / 16 GB）。16:00 发布 cron 的时间预算据此为 <N×2> 秒
（留一倍余量应对首次冷缓存）。若后续特征增多导致跑不完，再改预构建特征帧缓存。
```

- [ ] **Step 5: 提交**

```bash
git add live_trading/scripts/verify_ladder_dry_run.py \
        live_trading/scripts/diagnose_ladder_gap.py \
        live_trading/scripts/probe_topk_margin.py \
        docs/superpowers/specs/2026-08-23-live-v4-cohort-ladder-netting-design.md
git commit -m "test(live): verify the all-A dry-run reproduces the BT v4 prediction frame and record its cost"
```

---

# Part B — bridge 执行层

### Task 5: 精确定量、板块最低申报，与 `+0.1` 带来的容差

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py:2569-2579`
- Modify: `live_trading/modules/fill_importer.py:1612-1617`
- Test: `tests/live_trading/test_qmt_bridge_logic.py`
- Test: `tests/live_trading/test_fill_importer.py`

**Interfaces:**
- Produces（Task 7/8 消费）：
  - `_board_min_shares(stock_code: str) -> int` —— 盘后固定价的单笔最低申报股数
  - `_ladder_buy_shares(target_value: float, close_price: float, lot: int = 100) -> int` —— 与回测 `round_amount_by_trade_unit` 逐股相等
  - `_sized_buy_shares(stock_code: str, target_value: float, close_price: float, lot: int = 100) -> int` —— 上二者的组合，`B` 的最终值

- [ ] **Step 1: 写失败测试——精确性回归与板块最低申报**

追加到 `tests/live_trading/test_qmt_bridge_logic.py`：

```python
def _backtest_shares(target_value, close_price, factor=1.0, trade_unit=100):
    """qlib/backtest/exchange.py round_amount_by_trade_unit 的真实股数。

    回测传的是复权价，返回值也是复权口径，乘回 factor 才是真实股数；
    raw_close = adj_close / factor，所以 factor 完全约掉。这里直接用未复权价。
    """
    adjusted = target_value / (close_price * factor)
    return (adjusted * factor + 0.1) // trade_unit * trade_unit


@pytest.mark.parametrize(
    "target_value,close_price",
    [
        (60_000.0, 10.0),      # 整好 6000 股
        (60_000.0, 13.37),     # 普通零头
        (2_999.5, 10.0),       # V/C = 299.95：+0.1 抬进下一手的临界窗口
        (29_995.0, 100.0),     # 同一临界窗口，另一个价位
        (1_000_000.0, 3.01),
        (999.0, 10.0),         # 不足一手
    ],
)
def test_buy_sizing_equals_the_backtest_share_for_share(
    bridge, target_value, close_price,
):
    assert bridge._ladder_buy_shares(target_value, close_price) == int(
        _backtest_shares(target_value, close_price)
    )


def test_the_missing_epsilon_would_have_cost_a_whole_lot(bridge):
    """锁住 +0.1：丢掉它时 V/C=299.95 会算成 200 股而不是 300 股。"""
    assert bridge._ladder_buy_shares(2_999.5, 10.0) == 300
    assert int(2_999.5 / 10.0 / 100.0) * 100 == 200


@pytest.mark.parametrize(
    "stock_code,expected",
    [
        ("600000.SH", 100),
        ("000001.SZ", 100),
        ("300750.SZ", 100),
        ("688111.SH", 200),
    ],
)
def test_board_minimum_declaration_size(bridge, stock_code, expected):
    assert bridge._board_min_shares(stock_code) == expected


def test_star_market_below_two_hundred_shares_is_zeroed(bridge):
    # 科创板盘后固定价单笔买入不得少于 200 股
    assert bridge._sized_buy_shares("688111.SH", 1_500.0, 10.0) == 0
    assert bridge._sized_buy_shares("688111.SH", 2_500.0, 10.0) == 200
    # 主板同样金额照常成单
    assert bridge._sized_buy_shares("600000.SH", 1_500.0, 10.0) == 100


def test_star_market_above_the_floor_is_still_a_lot_multiple(bridge):
    # 与回测 trade_unit=100 一致：200 股门槛之上仍取 100 的整数倍
    assert bridge._sized_buy_shares("688111.SH", 35_000.0, 10.0) == 3_500


def test_missing_close_price_sizes_to_zero_never_guesses(bridge):
    assert bridge._ladder_buy_shares(60_000.0, 0.0) == 0
    assert bridge._sized_buy_shares("600000.SH", 60_000.0, 0.0) == 0
```

追加到 `tests/live_trading/test_fill_importer.py`。沿用该文件已有的 `_fill(...)`（返回回执 dict）、`_record_plan(recorder, fills, batch_id=...)`（写入对应的原始计划）与 `FillEvent.from_dict`。注意 `_record_plan` 对 BUY 用 `f.get("target_value", ...)` 取计划金额，所以显式塞一个 `target_value` 键即可控制它。

```python
def test_buy_gross_may_exceed_target_value_by_the_rounding_epsilon(tmp_path):
    """+0.1 的精度补偿让 B*C 可以比 target_value 多出至多 0.1*C 元。
    容差写死 1e-6 会让那 0.1% 的单子在导入时硬失败。"""
    recorder = LiveRecorder(str(tmp_path / "epsilon.db"), opening_cash=100_000.0)
    # V = 2999.5, C = 10.0 -> B = 300（含 +0.1），gross = 3000.0 > V
    fill = dict(
        _fill(batch_id="eps", client_order_id="eps-1", side="BUY",
              stock_code="600000.SH", requested=300, filled=300, price=10.0),
        target_value=2_999.5,
    )
    _record_plan(recorder, [fill], batch_id="eps")

    recorder.apply_fill(FillEvent.from_dict(fill))
    assert recorder.get_positions()["600000.SH"]["shares"] == 300


def test_buy_gross_beyond_the_epsilon_is_still_rejected(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "epsilon-over.db"), opening_cash=100_000.0)
    fill = dict(
        _fill(batch_id="eps2", client_order_id="eps2-1", side="BUY",
              stock_code="600000.SH", requested=400, filled=400, price=10.0),
        target_value=2_999.5,
    )
    _record_plan(recorder, [fill], batch_id="eps2")

    with pytest.raises(SchemaError, match="exceeds target_value"):
        recorder.apply_fill(FillEvent.from_dict(fill))
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_qmt_bridge_logic.py -k "sizing or board or epsilon or star_market" \
  tests/live_trading/test_fill_importer.py -k "gross" -q
```
Expected: FAIL —— `AttributeError: module has no attribute '_ladder_buy_shares'`；`SchemaError: BUY fill gross ... exceeds target_value`。

- [ ] **Step 3: 在 bridge 里加三个纯函数**

在 `live_trading/qmt_strategy/qmt_signal_bridge.py` 的 `_target_requested_quantity`（第 2569 行）**之前**插入。注释一律英文、无 f-string 之外的新语法。

```python
def _board_min_shares(stock_code):
    """Minimum single-order size for after-hours fixed-price trading.

    STAR Market (SH688*) requires at least 200 shares per buy order; the main
    board and ChiNext take any multiple of 100. Derived from the exchange rule,
    not from the broker, because a rejected order costs us the whole layer.
    """
    symbol = str(stock_code).split(".")[0]
    return 200 if symbol.startswith("688") else 100


def _ladder_buy_shares(target_value, close_price, lot=100):
    """Share count for one ladder layer, share-for-share equal to the backtest.

    Mirrors qlib/backtest/exchange.py round_amount_by_trade_unit:
        (deal_amount * factor + 0.1) // trade_unit * trade_unit / factor
    The backtest feeds it an adjusted price and multiplies the result back by
    factor to get real shares, and raw_close == adj_close / factor, so factor
    cancels out entirely and the raw close below is exact.

    The +0.1 is not cosmetic. Dropping it costs a full lot whenever
    target_value / close_price lands in [x*100 - 0.1, x*100): at V/C = 299.95
    this returns 300 while a plain floor returns 200.
    """
    if close_price <= 0 or target_value <= 0:
        return 0
    return int((float(target_value) / float(close_price) + 0.1) // lot) * lot


def _sized_buy_shares(stock_code, target_value, close_price, lot=100):
    """Final B: lot-rounded shares, zeroed when below the board minimum."""
    shares = _ladder_buy_shares(target_value, close_price, lot)
    if shares < _board_min_shares(stock_code):
        return 0
    return shares
```

- [ ] **Step 4: 放宽 `apply_fill` 的 BUY 金额容差**

`live_trading/modules/fill_importer.py` 第 1612–1617 行改为：

```python
                fill_gross = float(fill.filled_qty) * float(fill.avg_price)
                # B 用回测同款的 +0.1 精度补偿取整（见 bridge _ladder_buy_shares），
                # 所以 B*C 可以比 target_value 多出至多 0.1 股的钱。容差写死 1e-6
                # 会让落在那个窗口里的单子在导入时硬失败。
                allowance = 0.1 * float(fill.avg_price) + 1e-6
                if fill_gross > float(order["target_value"]) + allowance:
                    raise SchemaError(
                        f"BUY fill gross {fill_gross:.6f} exceeds target_value "
                        f"{order['target_value']:.6f}"
                    )
```

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_qmt_bridge_logic.py tests/live_trading/test_fill_importer.py -q
```
Expected: PASS 全绿。

- [ ] **Step 6: 提交**

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
        live_trading/modules/fill_importer.py \
        tests/live_trading/test_qmt_bridge_logic.py \
        tests/live_trading/test_fill_importer.py
git commit -m "feat(bridge): size ladder buys share-for-share with the backtest and honour board minimums"
```

---

### Task 6: 放开剩余两处整百假设

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py:1384-1387`
- Modify: `live_trading/modules/fill_importer.py:1591-1595`
- Test: `tests/live_trading/test_qmt_bridge_logic.py`
- Test: `tests/live_trading/test_fill_importer.py`

**Interfaces:**
- Consumes: 计划一 Task 7 已放开的 `signal_schema.validate_order`（SELL 只要求正整数）。
- Produces: 含零股的 SELL 批次能通过 bridge 校验并能完成回执导入。Task 7 的 `netted_qty = min(S, B)` 在 `B < S` 时也可能是非整百，同样依赖这里。

- [ ] **Step 1: 写失败测试**

追加到 `tests/live_trading/test_qmt_bridge_logic.py`：

```python
def test_odd_lot_sell_batch_is_accepted(bridge, monkeypatch, tmp_path):
    """零股来自 absorb_broker_excess 吸收的送股。阶梯到期时整层一次性卖出，
    含零股的层同样合规——bridge 不能因为不是整百就整批拒收。"""
    current_root, other_root = _profile_roots(tmp_path, "AFTER_HOURS_FIXED_PRICE")
    _activate_profile(bridge, "AFTER_HOURS_FIXED_PRICE", current_root, other_root)
    order = _order(coid="20260714001001S", side="SELL", priority=10)
    order["price_type"] = "AFTER_HOURS_CLOSE"
    order["quantity"] = 120
    _write_batch(bridge, bridge._today(), [order])
    bridge._claim_new_batch()
    assert bridge.g.batch is not None
    assert bridge.g.batch.orders[0]["quantity"] == 120


@pytest.mark.parametrize("quantity", [0, -100, 100.5, True, None])
def test_non_positive_or_non_integer_sell_quantity_is_still_rejected(
    bridge, monkeypatch, tmp_path, quantity,
):
    current_root, other_root = _profile_roots(tmp_path, "AFTER_HOURS_FIXED_PRICE")
    _activate_profile(bridge, "AFTER_HOURS_FIXED_PRICE", current_root, other_root)
    order = _order(coid="20260714001001S", side="SELL", priority=10)
    order["price_type"] = "AFTER_HOURS_CLOSE"
    order["quantity"] = quantity
    _write_batch(bridge, bridge._today(), [order])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
```

追加到 `tests/live_trading/test_fill_importer.py`（同样沿用 `_fill` / `_record_plan` / `FillEvent.from_dict`）：

```python
def test_odd_lot_sell_receipt_imports(tmp_path):
    recorder = LiveRecorder(str(tmp_path / "oddlot.db"), opening_cash=100_000.0)
    fill = _fill(batch_id="odd", client_order_id="odd-1", side="SELL",
                 stock_code="600000.SH", requested=120, filled=120, price=10.0)
    _record_plan(recorder, [fill], batch_id="odd")

    recorder.apply_fill(FillEvent.from_dict(fill))
    # 120 股 @ 10.0 扣费后入账，现金必须涨
    assert recorder.get_cash() > 100_000.0


@pytest.mark.parametrize("requested", [0, -100])
def test_non_positive_requested_qty_is_still_rejected(tmp_path, requested):
    recorder = LiveRecorder(str(tmp_path / ("bad%d.db" % abs(requested))))
    fill = _fill(batch_id="bad", client_order_id="bad-1", side="SELL",
                 stock_code="600000.SH", requested=120, filled=0, price=0.0)
    _record_plan(recorder, [fill], batch_id="bad")

    with pytest.raises(SchemaError, match="requested_qty"):
        recorder.apply_fill(
            FillEvent.from_dict(dict(fill, requested_qty=requested))
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_qmt_bridge_logic.py -k odd_lot \
  tests/live_trading/test_fill_importer.py -k odd_lot -q
```
Expected: FAIL —— bridge 用例里 `bridge.g.batch is None`（被 reject）；importer 用例抛 `SchemaError: fill requested_qty invalid for plan`。

- [ ] **Step 3: 改 bridge 校验**

`qmt_signal_bridge.py` 第 1384–1387 行改为：

```python
        else:
            # Odd lots are legal on the sell side: absorb_broker_excess folds
            # bonus shares into the ladder, and a maturing layer is always sold
            # whole. Rejecting non-multiples of 100 would drop the entire batch.
            if (not isinstance(quantity, int) or isinstance(quantity, bool)
                    or quantity <= 0):
                return reject("SELL quantity must be a positive integer")
```

- [ ] **Step 4: 改 importer 校验**

`fill_importer.py` 第 1591–1595 行改为：

```python
            if fill.requested_qty <= 0:
                raise SchemaError(
                    f"fill requested_qty invalid for plan: {fill.requested_qty!r} "
                    "must be positive"
                )
```

整百约束到此在三层全部下移到下单器（`CohortOrderManager._sell_quantity`：不足一手只能整笔清仓，否则向下取整到一手）。

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_qmt_bridge_logic.py tests/live_trading/test_fill_importer.py \
  tests/live_trading/test_signal_schema.py -q
```
Expected: PASS 全绿。

- [ ] **Step 6: 提交**

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
        live_trading/modules/fill_importer.py \
        tests/live_trading/test_qmt_bridge_logic.py \
        tests/live_trading/test_fill_importer.py
git commit -m "fix(live): let odd-lot sells through the bridge validator and the fill importer"
```

---

### Task 7: 抵销的回执字段 `netted_qty` 全链路

**Files:**
- Modify: `live_trading/modules/signal_schema.py:101-121`
- Modify: `live_trading/modules/fill_importer.py`（`fills` 建表 + `_migrate_composite_keys` + `apply_fill` 落盘）
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py:756-785`
- Modify: `live_trading/modules/cohort_advance.py:24-40`
- Test: `tests/live_trading/test_signal_schema.py`
- Test: `tests/live_trading/test_fill_importer.py`
- Test: `tests/live_trading/test_cohort_advance.py`

**Interfaces:**
- Consumes: `TERMINAL_FILL_STATUS`、`_POSITION_STATUS = {"FILLED", "PARTIAL"}`。
- Produces（Task 8 消费）：
  - `FillEvent.netted_qty: int = 0`
  - `fills.netted_qty INTEGER NOT NULL DEFAULT 0`
  - bridge `_write_fill` 把 `order.get("netted_qty")` 写进回执
  - `day_executions` 按 `applied_qty + netted_qty` 汇总

- [ ] **Step 1: 写失败测试——三层字段与账本汇总**

`tests/live_trading/test_signal_schema.py`：

```python
def test_fill_event_carries_netted_qty_and_defaults_to_zero():
    from live_trading.modules.signal_schema import FillEvent

    plain = FillEvent(
        batch_id="b", client_order_id="c", mode="LIVE", stock_code="SH600000",
        side="SELL", status="SKIPPED", requested_qty=300, filled_qty=0,
        avg_price=0.0, qmt_order_id="", message="", ts="t",
    )
    assert plain.netted_qty == 0
    assert '"netted_qty":0' in plain.to_json_line()


def test_fill_event_ignores_unknown_receipt_keys():
    """加字段必须对旧回执文件双向兼容。"""
    from live_trading.modules.signal_schema import FillEvent

    parsed = FillEvent.from_dict({
        "type": "fill_event", "batch_id": "b", "client_order_id": "c",
        "mode": "LIVE", "stock_code": "SH600000", "side": "SELL",
        "status": "SKIPPED", "requested_qty": 300, "filled_qty": 0,
        "avg_price": 0.0, "qmt_order_id": "", "message": "", "ts": "t",
        "some_future_field": 1,
    })
    assert parsed.netted_qty == 0
```

`tests/live_trading/test_fill_importer.py`：

```python
def test_netted_receipt_moves_no_shares_no_cash_and_charges_no_fee(tmp_path):
    """抵销省下的就是这两腿的手续费。若把它们写成正常成交回执，
    apply_fill 会重新计费，正好把省下的钱抹掉。"""
    recorder = LiveRecorder(str(tmp_path / "netted.db"), opening_cash=100_000.0)
    fill = _fill(batch_id="net", client_order_id="net-1", side="SELL",
                 stock_code="600000.SH", requested=300, filled=0, price=0.0,
                 status="SKIPPED")
    _record_plan(recorder, [fill], batch_id="net")

    recorder.apply_fill(FillEvent.from_dict(dict(fill, netted_qty=300)))

    assert recorder.get_cash() == pytest.approx(100_000.0)
    assert recorder.get_positions().get("600000.SH") is None
    row = recorder.get_fills("net")[0]
    assert row["netted_qty"] == 300
    assert row["applied_qty"] == 0
    assert row["applied_fee"] == pytest.approx(0.0)


def test_netted_qty_column_is_added_to_a_pre_existing_database(tmp_path):
    """存量库要能在线迁移出这一列，不能要求重建。"""
    import sqlite3

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE fills (
                batch_id TEXT NOT NULL, client_order_id TEXT NOT NULL,
                mode TEXT NOT NULL, stock_code TEXT NOT NULL, side TEXT NOT NULL,
                status TEXT NOT NULL, requested_qty INTEGER, filled_qty INTEGER,
                avg_price REAL, qmt_order_id TEXT, message TEXT, ts TEXT,
                applied_qty INTEGER NOT NULL DEFAULT 0,
                applied_amount REAL NOT NULL DEFAULT 0,
                applied_fee REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (batch_id, client_order_id)
            );
            INSERT INTO fills VALUES
                ('b','c','LIVE','SH600000','SELL','FILLED',300,300,10.0,'q','',
                 't',300,3000.0,1.5);
        """)

    LiveRecorder(str(path), opening_cash=1_000_000.0)

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM fills").fetchone()
    assert row["netted_qty"] == 0
    assert row["applied_qty"] == 300
```

`tests/live_trading/test_cohort_advance.py`（沿用该文件已有的 `_fill` / `_recorder` / `_stub_fills`）：

```python
def test_netted_shares_count_as_sold_and_bought_without_any_market_fill():
    """B > S 的净买：卖腿一股没成交，但 S 股是转记走的，到期层必须退掉。"""
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="SELL", stock_code="SH600000",
              status="SKIPPED", applied_qty=0, netted_qty=300),
        _fill(client_order_id="c2", side="BUY", stock_code="SH600000",
              applied_qty=200, netted_qty=300),
    ])

    assert sold == {"SH600000": 300.0}
    assert filled == {"SH600000": 500.0}


def test_residual_sell_adds_to_the_transferred_amount():
    """B < S 的净卖：转记 B，残余卖单成交 g，到期层退 B + g。"""
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="SELL", stock_code="SH600000",
              status="PARTIAL", applied_qty=100, netted_qty=200),
        _fill(client_order_id="c2", side="BUY", stock_code="SH600000",
              status="SKIPPED", applied_qty=0, netted_qty=200),
    ])

    assert sold == {"SH600000": 300.0}
    assert filled == {"SH600000": 200.0}


def test_fully_offset_pair_produces_no_orders_but_still_rolls_the_ledger():
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="SELL", stock_code="SH600000",
              status="SKIPPED", applied_qty=0, netted_qty=300),
        _fill(client_order_id="c2", side="BUY", stock_code="SH600000",
              status="SKIPPED", applied_qty=0, netted_qty=300),
    ])

    assert sold == {"SH600000": 300.0}
    assert filled == {"SH600000": 300.0}
```

同时把 `test_cohort_advance.py` 顶部的 `_fill()` 默认值加上 `"netted_qty": 0`。

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_signal_schema.py tests/live_trading/test_fill_importer.py \
  tests/live_trading/test_cohort_advance.py -q
```
Expected: FAIL —— `TypeError: FillEvent.__init__() got an unexpected keyword argument 'netted_qty'` 等。

- [ ] **Step 3: 加 `FillEvent.netted_qty`**

`live_trading/modules/signal_schema.py` 的 `FillEvent` 在 `ts: str` **之后**加（必须放最后，前面都是无默认值字段）：

```python
    ts: str
    # 被同名当日买卖抵销掉、因而没有走市场的股数（见 spec 4.4）。
    # 走独立字段而不是伪造成交额：伪造会让 apply_fill 重新计费，
    # 正好抹掉抵销省下的那 0.092%。
    netted_qty: int = 0
```

- [ ] **Step 4: 加列、加迁移、落盘**

`live_trading/modules/fill_importer.py` 三处：

1. `_init_db` 的 `CREATE TABLE IF NOT EXISTS fills`（第 237–254 行）在 `applied_fee` 之后加一行：

```sql
                    netted_qty INTEGER NOT NULL DEFAULT 0,
```

2. `_migrate_composite_keys` 的 `else:` 分支（第 722–732 行）里，`applied_amount` 之后追加同款在线迁移：

```python
            if "netted_qty" not in cols:
                conn.execute(
                    "ALTER TABLE fills ADD COLUMN netted_qty "
                    "INTEGER NOT NULL DEFAULT 0"
                )
```

同时把第 690–721 行重建表的 `CREATE TABLE fills (...)` 与 `INSERT INTO fills (...) SELECT ...` 也补上 `netted_qty`（`SELECT` 侧填常量 `0`），否则走重建路径的库会丢这一列。

3. `apply_fill` 的 `INSERT ... ON CONFLICT` 语句（第 1675 行起）把 `netted_qty` 加入列清单、占位符与 `excluded` 更新项，值取 `int(fill.netted_qty)`。**不要**让它参与 `delta_qty` / `delta_amount` / `fee_delta` 的任何计算——`netted_qty` 的全部意义就是「不动持仓、不动现金、不计费」。

- [ ] **Step 5: bridge 把 `netted_qty` 写进回执**

`qmt_signal_bridge.py` 的 `_write_fill`（第 756 行）在 `event` 字典里加一项，并把去重判断也纳入该字段：

```python
        "ts": datetime.datetime.now().isoformat(),
        # Shares satisfied by same-day internal transfer instead of a market
        # order. Mac reconstructs the ladder move from applied_qty + netted_qty.
        "netted_qty": int(order.get("netted_qty", 0) or 0),
    }
    prev = batch.fills.get(order["client_order_id"])
    if prev is not None and prev["status"] == status \
            and prev["filled_qty"] == event["filled_qty"] \
            and prev.get("netted_qty", 0) == event["netted_qty"]:
        return  # no change, do not spam the file
```

- [ ] **Step 6: `day_executions` 汇总进 `netted_qty`**

`live_trading/modules/cohort_advance.py` 的循环体改为：

```python
    for fill in fills:
        if fill.get("mode") != strategy_mode:
            continue
        if fill.get("status") not in TERMINAL_FILL_STATUS:
            continue
        # applied_qty 是真正进持仓的股数；netted_qty 是被同名抵销转记掉的股数。
        # 到期层要退掉「卖出的 + 转记走的」，今日层要记入「买到的 + 转记来的」，
        # 两侧都是这个和（推导见 plan2 前置事实里的三行表）。
        quantity = float(fill.get("applied_qty") or 0) + float(
            fill.get("netted_qty") or 0
        )
        if quantity <= 0:
            continue
        bucket = filled if fill.get("side") == "BUY" else sold
        code = fill["stock_code"]
        bucket[code] = bucket.get(code, 0.0) + quantity
```

同时把模块 docstring 里「汇总用 `fills.applied_qty`」那段改成 `applied_qty + netted_qty` 并说明原因。

- [ ] **Step 7: 跑测试确认通过**

Run:
```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -q
```
Expected: PASS 全绿。

- [ ] **Step 8: 提交**

```bash
git add live_trading/modules/signal_schema.py live_trading/modules/fill_importer.py \
        live_trading/modules/cohort_advance.py \
        live_trading/qmt_strategy/qmt_signal_bridge.py \
        tests/live_trading/test_signal_schema.py \
        tests/live_trading/test_fill_importer.py \
        tests/live_trading/test_cohort_advance.py
git commit -m "feat(live): carry netted shares on the receipt so the ledger rolls without fake fills"
```

---

### Task 8: 抵销决策的冻结与两阶段下单

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`（`Batch.__init__` 第 107–120 行、`_save_active_state` 第 631–655 行、`_load_active_state` 第 658–690 行、`_process_batch` 第 3167–3301 行；新增 `_net_ladder_pair` / `_plan_ladder_netting`）
- Test: `tests/live_trading/test_qmt_bridge_logic.py`

**Interfaces:**
- Consumes: Task 5 的 `_sized_buy_shares`；Task 7 的 `_write_fill` 的 `netted_qty`；测试文件已有的 `bridge` fixture、`_activate_profile`、`_profile_roots`、`_order`、`_write_batch`、`_read_fills`、`_read_events`、`_TickCtx`。
- Produces（Task 9/10 消费）：
  - `ENABLE_LADDER_NETTING`（模块级开关，仓库默认 `False`）
  - `_net_ladder_pair(sell_shares: int, buy_shares: int) -> tuple[str | None, int, int]` 返回 `(side, quantity, transferred)`
  - `_plan_ladder_netting(ContextInfo, batch) -> None`，把 `net_quantity` / `netted_qty` / `netting_close` / `netting_sized_shares` 冻结进 `batch.orders`
  - 每个 BUY 一条 `LADDER_NET` 结构化事件
  - 测试辅助 `_ladder_batch(bridge, sell_qty, target_value, code="000001.SZ") -> tuple[dict, dict]`（`sell_qty <= 0` 时只放 BUY）与 `_run_after_hours(bridge, monkeypatch, tmp_path, now="15:00:10") -> list`（返回 `passorder` 调用参数的累积列表），Task 9 / Task 10 直接复用

- [ ] **Step 1: 写失败测试——抵销算术五情形**

追加到 `tests/live_trading/test_qmt_bridge_logic.py`：

```python
@pytest.mark.parametrize(
    "sell_shares,buy_shares,side,quantity,transferred",
    [
        (300, 500, "BUY", 200, 300),    # B > S：净买
        (500, 300, "SELL", 200, 300),   # B < S：净卖
        (300, 300, None, 0, 300),       # B == S：无单，全部转记
        (300, 0, "SELL", 300, 0),       # 不在今日 top3 / 科创板被置 0
        (0, 300, "BUY", 300, 0),        # 无到期层
    ],
)
def test_netting_arithmetic(
    bridge, sell_shares, buy_shares, side, quantity, transferred,
):
    assert bridge._net_ladder_pair(sell_shares, buy_shares) == (
        side, quantity, transferred,
    )
```

- [ ] **Step 2: 写失败测试——两阶段接线**

```python
def _ladder_batch(bridge, sell_qty, target_value, code="000001.SZ"):
    """一张同名到期卖 + 当日买的批次，用于抵销接线测试。"""
    sell = _order(coid="20260714001001S", side="SELL", priority=10)
    buy = _order(coid="20260714001002B", side="BUY", priority=20)
    for order in (sell, buy):
        order["price_type"] = "AFTER_HOURS_CLOSE"
        order["stock_code"] = code
    sell["quantity"] = sell_qty
    buy["target_value"] = target_value
    _write_batch(bridge, bridge._today(), [sell, buy], mode="LIVE")
    return sell, buy


def _run_after_hours(bridge, monkeypatch, tmp_path, now="15:00:10"):
    current_root, other_root = _profile_roots(tmp_path, "AFTER_HOURS_FIXED_PRICE")
    _activate_profile(bridge, "AFTER_HOURS_FIXED_PRICE", current_root, other_root)
    bridge.ENABLE_LADDER_NETTING = True
    bridge.MAX_ORDER_QUANTITY = 0
    marker = current_root / "state" / ("PR49_LIVE_OK_" + bridge._today())
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("")
    monkeypatch.setattr(bridge, "_now_hms", lambda: now)
    monkeypatch.setattr(bridge, "_get_can_use_volume", lambda *a: 100_000)
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 10_000_000.0)
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    monkeypatch.setattr(bridge, "_real_account_preflight", lambda a: (True, ""))
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )
    return submitted


def test_net_buy_submits_only_the_difference_and_skips_the_sell_leg(
    bridge, monkeypatch, tmp_path,
):
    # C = 10.0, V = 5000 -> B = 500; S = 300 -> net BUY 200, transferred 300
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    bridge._claim_new_batch()
    ctx = _TickCtx(10.0, up_stop=11.0, down_stop=9.0)
    bridge._process_batch(ctx, bridge.g.batch)

    sides = [args[0] for args in submitted]
    assert sides == [23]                       # 23 = BUY, no SELL passorder
    assert submitted[0][6] == 200              # quantity argument

    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    sell_fill = fills["20260714001001S"]
    assert sell_fill["status"] == "SKIPPED"
    assert sell_fill["netted_qty"] == 300
    assert sell_fill["filled_qty"] == 0


def test_net_sell_submits_the_residual_and_skips_the_buy_leg(
    bridge, monkeypatch, tmp_path,
):
    # C = 10.0, V = 3000 -> B = 300; S = 500 -> net SELL 200, transferred 300
    _ladder_batch(bridge, sell_qty=500, target_value=3_000.0)
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)

    assert [args[0] for args in submitted] == [24]     # 24 = SELL
    assert submitted[0][6] == 200
    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    assert fills["20260714001002B"]["status"] == "SKIPPED"
    assert fills["20260714001002B"]["netted_qty"] == 300
    assert fills["20260714001002B"]["requested_qty"] == 300


def test_exact_offset_submits_nothing_and_transfers_everything(
    bridge, monkeypatch, tmp_path,
):
    _ladder_batch(bridge, sell_qty=300, target_value=3_000.0)
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)

    assert submitted == []
    fills = {row["client_order_id"]: row for row in _read_fills(bridge)}
    assert all(row["netted_qty"] == 300 for row in fills.values())
    assert bridge.g.batch is None      # 全部终态，批次已收尾


def test_odd_lot_due_amount_is_never_netted(bridge, monkeypatch, tmp_path):
    """S=120 时 net=B-S 不是整百，买入不允许非整百。该票整体走不抵销的老路，
    两腿都正常下单，代价是那一次的往返费。"""
    _ladder_batch(bridge, sell_qty=120, target_value=5_000.0)
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)

    assert sorted(args[0] for args in submitted) == [23, 24]
    assert all(row["netted_qty"] == 0 for row in _read_fills(bridge))


def test_netting_decision_is_frozen_across_a_restart(bridge, monkeypatch, tmp_path):
    """C 只在提交时刻读一次。重启后重算可能拿到不同的 B，而卖腿可能已经
    按旧决策提交过——两个不自洽的 B 会让转记股数与实际下单量对不上。"""
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    _run_after_hours(bridge, monkeypatch, tmp_path)
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)
    frozen = [dict(o) for o in bridge.g.batch.orders] if bridge.g.batch else None

    bridge.g.batch = None
    bridge._recover_processing_batch()
    # 价格变了也不该改变已冻结的决策
    bridge._process_batch(_TickCtx(20.0, up_stop=22.0, down_stop=18.0), bridge.g.batch)
    if frozen is not None:
        for before, after in zip(frozen, bridge.g.batch.orders):
            assert before["netted_qty"] == after["netted_qty"]
            assert before["net_quantity"] == after["net_quantity"]
            assert before["netting_close"] == after["netting_close"]


def test_ladder_net_event_records_every_buy_with_its_close_and_read_time(
    bridge, monkeypatch, tmp_path,
):
    """spec 4.7.1 的次日逐单对账要拿 C 与读取时刻。非重叠买单也要有，
    否则兜底路径下用错价的单子对不上账。"""
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    _run_after_hours(bridge, monkeypatch, tmp_path)
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)

    events = [e for e in _read_events(bridge) if e["event"] == "LADDER_NET"]
    assert len(events) == 1
    event = events[0]
    assert event["due_shares"] == 300
    assert event["target_value"] == 5_000.0
    assert event["official_close"] == 10.0
    assert event["official_close_read_at"]
    assert event["sized_shares"] == 500
    assert event["net_side"] == "BUY"
    assert event["net_quantity"] == 200
    assert event["transferred_shares"] == 300


def test_netting_is_off_by_default_so_close_auction_is_untouched(
    bridge, monkeypatch, tmp_path,
):
    assert bridge.ENABLE_LADDER_NETTING is False
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0)
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    bridge.ENABLE_LADDER_NETTING = False
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)

    assert sorted(args[0] for args in submitted) == [23, 24]
    assert all(row["netted_qty"] == 0 for row in _read_fills(bridge))


def test_broker_cash_still_caps_the_frozen_net_quantity(
    bridge, monkeypatch, tmp_path,
):
    """spec 4.7.2 的职责划分：Mac 防欠配、bridge 防超买。冻结 B 之后
    券商现金封顶必须照旧生效，否则 Mac 把预算算大就会真的超买。"""
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 2_100.0)
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)

    # B = 500 股，但 2100 元只够 200 股（含佣金与过户费）
    assert submitted and submitted[0][6] == 200


def test_frozen_quantity_below_affordable_floor_is_skipped_not_shrunk_to_zero(
    bridge, monkeypatch, tmp_path,
):
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 50.0)
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)

    assert submitted == []
    fill = _read_fills(bridge)[0]
    assert fill["status"] == "SKIPPED"
    assert "insufficient actual cash" in fill["message"]


def test_unavailable_close_errors_the_order_and_never_guesses(
    bridge, monkeypatch, tmp_path,
):
    """spec 4.4 边界情形最后一行：收盘价取不到就整单 ERROR，不猜价、不下单，
    该层变薄。_plan_ladder_netting 不冻结任何决策，BUY 阶段走现有 ERROR 分支。"""
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path)
    monkeypatch.setattr(bridge, "_official_close", lambda ctx, code: 0.0)
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(0.0), bridge.g.batch)

    assert submitted == []
    fill = _read_fills(bridge)[0]
    assert fill["status"] == "ERROR"
    assert fill["message"] == "official close unavailable"
```

`_ladder_batch` 要支持 `sell_qty <= 0`（只放 BUY 订单，用于上面三个无卖腿的用例）。

- [ ] **Step 3: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -k "netting or netted or ladder_net or offset or frozen or unavailable_close" -q`
Expected: FAIL —— `AttributeError: module has no attribute '_net_ladder_pair'` / `'ENABLE_LADDER_NETTING'`。

- [ ] **Step 4: 加开关与纯函数**

`qmt_signal_bridge.py` 的 user settings 区（`MAX_ORDERS_PER_BATCH = 40` 之后）加：

```python
# Same-name buy/sell offsetting for the cohort ladder. Repository default is
# False so a stray copy never nets; the QMT-local rendered copy opts in, the
# same posture as ALLOW_REAL_MONEY.
ENABLE_LADDER_NETTING = False
```

在 `_sized_buy_shares` 之后加：

```python
def _net_ladder_pair(sell_shares, buy_shares):
    """Offset one name's maturing sell against the same day's buy.

    Both legs would fill at the same closing price under prType=49, so
    "sell S then buy B" and "submit the net only" end at the identical
    position; the difference is the round-trip fee on min(S, B) shares.
    Returns (side, quantity, transferred); side None means submit nothing.
    """
    sell_shares = int(sell_shares)
    buy_shares = int(buy_shares)
    transferred = min(sell_shares, buy_shares)
    net = buy_shares - sell_shares
    if net > 0:
        return "BUY", net, transferred
    if net < 0:
        return "SELL", -net, transferred
    return None, 0, transferred
```

- [ ] **Step 5: 加 `_plan_ladder_netting`**

放在 `_process_batch` 之前。

```python
def _plan_ladder_netting(ContextInfo, batch):
    """Size every BUY once and freeze the offsetting decision into the orders.

    Runs on the first trading pass only. Freezing matters: the close is read
    once here, and by the time the BUY phase runs the SELL leg may already be
    submitted. A second, different B would leave the transferred share count
    and the submitted quantity mutually inconsistent.

    batch.orders is persisted by _save_active_state, so writing the decision
    into the order dicts survives a restart with no extra plumbing.
    """
    if not ENABLE_LADDER_NETTING or batch.netting_planned:
        return
    batch.netting_planned = True
    due_by_code = {}
    for order in batch.orders:
        if order["side"] == "SELL":
            due_by_code[order["stock_code"]] = order

    for buy in [o for o in batch.orders if o["side"] == "BUY"]:
        code = buy["stock_code"]
        close_price = _official_close(ContextInfo, code)
        read_at = datetime.datetime.now().isoformat()
        if close_price <= 0.0:
            # Leave it to the BUY phase's existing "official close unavailable"
            # branch: never guess a price, never place the order.
            continue
        sized = _sized_buy_shares(code, float(buy["target_value"]), close_price)
        sell = due_by_code.get(code)
        due_shares = int(sell["quantity"]) if sell is not None else 0
        # An odd due amount cannot be netted: B - S would not be a lot multiple
        # and buys must be whole lots. That name pays the round trip instead.
        offsetable = sell is not None and due_shares % 100 == 0
        if offsetable:
            side, quantity, transferred = _net_ladder_pair(due_shares, sized)
        else:
            side, quantity, transferred = "BUY", sized, 0

        buy["netting_close"] = close_price
        buy["netting_sized_shares"] = sized
        buy["netted_qty"] = transferred
        buy["net_quantity"] = quantity if side == "BUY" else 0
        if offsetable:
            sell["netted_qty"] = transferred
            sell["net_quantity"] = quantity if side == "SELL" else 0

        _log_event(
            "LADDER_NET",
            batch_id=batch.batch_id(),
            stock_code=code,
            buy_client_order_id=buy["client_order_id"],
            sell_client_order_id=(
                sell["client_order_id"] if sell is not None else ""
            ),
            due_shares=due_shares,
            offsetable=offsetable,
            target_value=float(buy["target_value"]),
            official_close=close_price,
            official_close_read_at=read_at,
            sized_shares=sized,
            net_side=side or "NONE",
            net_quantity=quantity,
            transferred_shares=transferred,
            message="ladder netting decided",
        )
    _save_active_state(batch)
```

`Batch.__init__` 加 `self.netting_planned = False`；`_save_active_state` 的 payload 加 `"netting_planned": batch.netting_planned`；`_load_active_state` 加 `batch.netting_planned = bool(payload.get("netting_planned", False))`。

- [ ] **Step 6: 接进 SELL 阶段**

`_process_batch` 在 `mode_live = batch.execution_authorized` 之后、`sells` / `buys` 切分**之前**插一行 `_plan_ladder_netting(ContextInfo, batch)`。SELL 循环开头（第 3174 行的 `for order in sells:` 之后、`if order["client_order_id"] in batch.submitted` 之后）插入：

```python
            frozen = order.get("net_quantity")
            if frozen is not None:
                if int(frozen) <= 0:
                    # Fully transferred internally: terminal receipt, no order.
                    batch.submitted[order["client_order_id"]] = True
                    _write_fill(batch, order, "SKIPPED", 0, 0.0, "",
                                "netted against same-day buy")
                    continue
                order["quantity"] = int(frozen)
```

注意 `_write_fill` 会读 `order["netted_qty"]`，而 `order["quantity"]` 此刻仍是 `S`，所以回执的 `requested_qty = S`、`netted_qty = S`，与 `apply_fill` 的 `requested_qty <= order["quantity"]` 校验相容。

- [ ] **Step 7: 接进 BUY 阶段**

BUY 循环（第 3219 行起）把取价与定量改为优先用冻结值：

```python
        for order in buys:
            if order["client_order_id"] in batch.submitted:
                continue
            frozen = order.get("net_quantity")
            if frozen is not None and int(frozen) <= 0:
                # Fully covered by the internal transfer; nothing to buy.
                order["quantity"] = int(order.get("netted_qty", 0) or 0)
                batch.submitted[order["client_order_id"]] = True
                _write_fill(batch, order, "SKIPPED", 0, 0.0, "",
                            "netted against same-day due sell")
                continue
            close_price = order.get("netting_close")
            if close_price is None:
                close_price = _official_close(ContextInfo, order["stock_code"])
            close_price = float(close_price)
            if frozen is not None:
                target_requested = int(frozen)
            else:
                target_requested = _target_requested_quantity(
                    close_price, float(order["target_value"]))
```

再往下，`requested = _target_requested_quantity(...)` 那一处（第 3254–3255 行）同样改为 `requested = target_requested`，非 live 分支的 `_target_buy_quantity(None, close_price, target_value)` 改为 `target_requested if frozen is not None else _target_buy_quantity(...)`。

**注意**：`frozen is not None and int(frozen) <= 0` 时若 `netted_qty` 也是 0（收盘价取不到、`_plan_ladder_netting` 里 `continue` 掉的票不会有 `net_quantity` 键，所以走不到这里），逻辑上 `netted_qty > 0` 必然成立；`_write_fill` 的 `requested_qty` 因此是正数，`apply_fill` 不会拒。

- [ ] **Step 8: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -q`
Expected: PASS 全绿。若既有的 CLOSE_AUCTION 用例失败，说明 `ENABLE_LADDER_NETTING` 的默认 `False` 没兜住，检查 `_plan_ladder_netting` 的第一行早退。

- [ ] **Step 9: 提交**

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
        tests/live_trading/test_qmt_bridge_logic.py
git commit -m "feat(bridge): freeze the ladder netting decision at submit time and submit only the net"
```

---

### Task 9: 买单阶段改卖单终态触发，超时改绝对时点

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`（第 57 行 `SELL_WAIT_TIMEOUT_SEC`、第 63–88 行 `_EXECUTION_PROFILES`、第 512–525 行 `_activate_profile_settings`、第 3199–3210 行阶段转换、第 3660–3680 行 `RUNTIME_CONFIG`）
- Modify: `live_trading/modules/execution_profile.py`
- Test: `tests/live_trading/test_qmt_bridge_logic.py`

**Interfaces:**
- Consumes: Task 8 产出的测试辅助 `_ladder_batch` / `_run_after_hours`。
- Produces（Task 10 消费）：profile 新增 `sell_deadline`（绝对时点字符串）；模块常量 `SELL_DEADLINE` 取代 `SELL_WAIT_TIMEOUT_SEC`；测试辅助 `_write_terminal_fill(bridge, batch, order)` 与 `_activate_profile_only(bridge, profile)`。

- [ ] **Step 1: 写失败测试**

```python
def test_buy_phase_starts_as_soon_as_every_sell_is_terminal(
    bridge, monkeypatch, tmp_path,
):
    """现有代码算出了 sells_done 却只用来打日志（第 3200-3205 行），
    于是卖单 30 秒成交也要干等满超时，买单错过最好的队列位置。"""
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0, code="600000.SH")
    _run_after_hours(bridge, monkeypatch, tmp_path, now="15:00:10")
    bridge.ENABLE_LADDER_NETTING = False
    bridge._claim_new_batch()
    batch = bridge.g.batch
    ctx = _TickCtx(10.0, up_stop=11.0, down_stop=9.0)

    bridge._process_batch(ctx, batch)
    # 卖单已提交并被回执标记终态
    for order in batch.orders:
        if order["side"] == "SELL":
            _write_terminal_fill(bridge, batch, order)
    bridge._process_batch(ctx, batch)

    assert batch.phase == "BUY"


def test_sell_phase_holds_while_sells_are_still_open_before_the_deadline(
    bridge, monkeypatch, tmp_path,
):
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0, code="600000.SH")
    _run_after_hours(bridge, monkeypatch, tmp_path, now="15:00:10")
    bridge.ENABLE_LADDER_NETTING = False
    bridge._claim_new_batch()
    batch = bridge.g.batch
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), batch)

    assert batch.phase == "SELL"


def test_sell_timeout_is_an_absolute_clock_time_not_a_relative_duration(
    bridge, monkeypatch, tmp_path,
):
    """提交提前到 15:00:05 后，240 秒相对超时会在 15:04 前后触发——撮合
    (15:05) 还没开始、一笔卖单都不可能成交，买单于是按快照现金发出，
    spec 4.7.2 的欠配照旧。超时必须从撮合开始起算。"""
    _ladder_batch(bridge, sell_qty=300, target_value=5_000.0, code="600000.SH")
    _run_after_hours(bridge, monkeypatch, tmp_path, now="15:04:00")
    bridge.ENABLE_LADDER_NETTING = False
    bridge._claim_new_batch()
    batch = bridge.g.batch
    # 相对时长早已耗尽（phase_started 在很久以前）
    monkeypatch.setattr(bridge.time, "time", lambda: batch.phase_started + 10_000)
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), batch)
    assert batch.phase == "SELL", "15:04 还没到撮合开始，不该转 BUY"

    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:09:00")
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), batch)
    assert batch.phase == "BUY"


def test_after_hours_sell_deadline_is_four_minutes_past_the_match_start(bridge):
    _activate_profile_only(bridge, "AFTER_HOURS_FIXED_PRICE")
    assert bridge.SELL_DEADLINE == "15:09:00"


def test_close_auction_never_waits_for_sells(bridge):
    _activate_profile_only(bridge, "CLOSE_AUCTION")
    assert bridge.SELL_DEADLINE == "14:57:05"
```

新增两个测试辅助（放在 `_run_after_hours` 附近）：

```python
def _write_terminal_fill(bridge, batch, order):
    bridge._write_fill(batch, order, "FILLED", int(order["quantity"]), 10.0,
                       "q1", "filled")


def _activate_profile_only(bridge, profile):
    bridge.EXECUTION_PROFILE = profile
    bridge._activate_profile_settings()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -k "sell_deadline or buy_phase or sell_phase or sell_timeout or close_auction_never" -q`
Expected: FAIL —— `AttributeError: 'SELL_DEADLINE'`；阶段转换用例断言不符。

- [ ] **Step 3: profile 换成绝对时点**

bridge 的 `_EXECUTION_PROFILES` 里把 `sell_wait_seconds` 换成 `sell_deadline`：

```python
    "CLOSE_AUCTION": {
        ...
        # Same as submit_after: the close auction has no sell-then-buy
        # sequencing, so the BUY phase must never wait.
        "sell_deadline": "14:57:05",
        "timer_start": "14:56:55",
    },
    "AFTER_HOURS_FIXED_PRICE": {
        ...
        # Absolute, not a duration from phase start. Continuous matching only
        # begins at 15:05, so a relative timeout measured from a 15:00:05
        # submission expires before a single sell could possibly have filled.
        "sell_deadline": "15:09:00",
        "timer_start": "15:04:55",
    },
```

第 57 行 `SELL_WAIT_TIMEOUT_SEC = 0` 改为 `SELL_DEADLINE = "14:57:05"`；`_activate_profile_settings` 里 `global SELL_WAIT_TIMEOUT_SEC` → `global SELL_DEADLINE`，赋值改为 `SELL_DEADLINE = settings["sell_deadline"]`。`init` 里 `RUNTIME_CONFIG` 的 `sell_wait_seconds=int(SELL_WAIT_TIMEOUT_SEC)` 改为 `sell_deadline=SELL_DEADLINE`。

Mac 侧 `live_trading/modules/execution_profile.py` 的 `ExecutionProfile` 加 `sell_deadline: str` 字段，两个 profile 各填 `"14:57:05"` / `"15:09:00"`。**不要**把它加进 `live_config.py` 第 96 行那个校验列表——那是「操作员要在 QMT 里逐项核对」的四个时点，`sell_deadline` 是内部时序参数，不属于配置契约。

- [ ] **Step 4: 改阶段转换条件**

`_process_batch` 第 3199–3210 行改为：

```python
        _poll_status(batch)
        sells_done = all(_order_is_terminal(batch, o["client_order_id"])
                         for o in sells) if sells else True
        # spec 4.7.2: the buy budget needs the sell proceeds, so start buying
        # the moment every sell is terminal instead of burning the full wait.
        wait_elapsed = _now_hms() >= SELL_DEADLINE
        if not sells or sells_done or wait_elapsed:
            batch.phase = "BUY"
            batch.phase_started = time.time()
            _save_active_state(batch)
            if not sells_done:
                _log("sell phase timeout, starting buys with actual cash")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -q`
Expected: PASS 全绿。`test_live_config.py` 若因 `ExecutionProfile` 新字段失败，检查是否有用例逐字段构造该 dataclass。

- [ ] **Step 6: 提交**

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
        live_trading/modules/execution_profile.py \
        tests/live_trading/test_qmt_bridge_logic.py
git commit -m "feat(bridge): trigger the buy phase on sell finality and time the fallback from match start"
```

---

### Task 10: 收盘价终态门禁与自适应提交

**Files:**
- Modify: `live_trading/qmt_strategy/qmt_signal_bridge.py`（`_EXECUTION_PROFILES`、`_activate_profile_settings`、`_market_price_evidence` 第 2632–2651 行、`_process_batch` 第 3121–3123 行；新增 `_close_is_final` / `_batch_close_is_final`）
- Modify: `live_trading/modules/execution_profile.py`
- Modify: `live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml:99`
- Modify: `live_trading/configs/csi1000_pr49_one_lot_probe.yaml:20`
- Test: `tests/live_trading/test_qmt_bridge_logic.py`
- Test: `tests/live_trading/test_live_config.py`

**Interfaces:**
- Consumes: `_get_tick(ContextInfo, stock_code)`、`_tick_field(tick, name, default=None)`；Task 8 的测试辅助 `_ladder_batch` / `_run_after_hours`；Task 9 的 `_activate_profile_only`。
- Produces: `_close_is_final(tick) -> True | False | None`（`None` = QMT 没暴露可用终态信号）；`_batch_close_is_final(ContextInfo, batch) -> True | False | None`；profile 新增 `submit_deadline`；模块常量 `SUBMIT_DEADLINE`；`AFTER_HOURS_FIXED_PRICE.submit_after` 改为 `"15:00:05"`、`timer_start` 改为 `"14:59:55"`。

- [ ] **Step 1: 写失败测试——终态判定与门禁行为**

```python
@pytest.mark.parametrize(
    "timetag,expected",
    [
        ("20260731 15:00:00", True),
        ("20260731 15:00:03", True),
        ("20260731 14:56:50", False),
        ("20260731 14:59:59", False),
        ("15:00:01", True),
        ("", None),
        (None, None),
        ("garbage", None),
        (20260731150000, None),
    ],
)
def test_close_finality_from_tick_timetag(bridge, timetag, expected):
    assert bridge._close_is_final({"timetag": timetag}) is expected


def test_batch_finality_requires_every_name_and_reports_unknown(bridge):
    class Ctx:
        def __init__(self, tags):
            self._tags = tags

        def get_full_tick(self, codes):
            return {c: {"timetag": self._tags[c]} for c in codes}

    batch = type("B", (), {"orders": [
        {"stock_code": "600000.SH"}, {"stock_code": "000001.SZ"},
    ]})()

    both_final = Ctx({"600000.SH": "20260731 15:00:01",
                      "000001.SZ": "20260731 15:00:02"})
    assert bridge._batch_close_is_final(both_final, batch) is True

    one_stale = Ctx({"600000.SH": "20260731 15:00:01",
                     "000001.SZ": "20260731 14:56:50"})
    assert bridge._batch_close_is_final(one_stale, batch) is False

    # 一只有信号一只没有：按未终态处理，等到兜底时点
    partial = Ctx({"600000.SH": "20260731 15:00:01", "000001.SZ": ""})
    assert bridge._batch_close_is_final(partial, batch) is False

    # 全都没有信号：QMT 不暴露该字段，退化成固定兜底
    none_at_all = Ctx({"600000.SH": "", "000001.SZ": None})
    assert bridge._batch_close_is_final(none_at_all, batch) is None


def test_after_hours_profile_attempts_from_fifteen_hundred_oh_five(bridge):
    _activate_profile_only(bridge, "AFTER_HOURS_FIXED_PRICE")
    assert bridge.TRADE_START == "15:00:05"
    assert bridge.SUBMIT_DEADLINE == "15:01:00"
    assert bridge._profile_settings()["timer_start"] == "14:59:55"


def test_close_auction_gate_never_engages(bridge):
    _activate_profile_only(bridge, "CLOSE_AUCTION")
    assert bridge.SUBMIT_DEADLINE == bridge.TRADE_START == "14:57:05"


def test_stale_close_defers_submission_until_the_deadline(
    bridge, monkeypatch, tmp_path,
):
    """15:00:05 起试，但收盘价还没终态就不能定量——那会用 14:57 的冻结价
    算出错的股数（spec 4.7.1 的尾部风险）。"""
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path, now="15:00:06")
    bridge._claim_new_batch()
    batch = bridge.g.batch
    stale = _TickCtx(10.0, up_stop=11.0, down_stop=9.0)
    monkeypatch.setattr(bridge, "_get_tick",
                        lambda ctx, code: {"lastPrice": 10.0,
                                           "timetag": "20260731 14:56:50"})
    bridge._process_batch(stale, batch)
    assert submitted == []
    assert batch.trading_started is False

    events = [e for e in _read_events(bridge) if e["event"] == "CLOSE_FINALITY_WAIT"]
    assert events

    # 到兜底时点，按现行 official_close > 0 门禁照常提交
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:01:00")
    bridge._process_batch(stale, batch)
    assert submitted


def test_final_close_submits_immediately_without_waiting_for_the_deadline(
    bridge, monkeypatch, tmp_path,
):
    _ladder_batch(bridge, sell_qty=0, target_value=5_000.0, code="600000.SH")
    submitted = _run_after_hours(bridge, monkeypatch, tmp_path, now="15:00:06")
    monkeypatch.setattr(bridge, "_get_tick",
                        lambda ctx, code: {"lastPrice": 10.0,
                                           "timetag": "20260731 15:00:01"})
    bridge._claim_new_batch()
    bridge._process_batch(_TickCtx(10.0, up_stop=11.0, down_stop=9.0), bridge.g.batch)
    assert submitted


def test_market_price_evidence_records_the_timetag(bridge):
    """兜底路径的对账要靠这个字段；不采集就无法把静默错价变成 CRIT。"""
    ctx = _TickCtx(10.0)
    evidence = bridge._market_price_evidence(ctx, "600000.SH", 10.0)
    assert "timetag" in evidence["tick_fields"]
```

`_ladder_batch` 需要支持 `sell_qty=0`（只发买单）；把 Task 8 里的 helper 改成 `sell_qty <= 0` 时不放 SELL 订单。

`tests/live_trading/test_live_config.py`：

```python
@pytest.mark.parametrize(
    "name",
    ["alla_v4_ladder_k3h5_postclose_real", "csi1000_pr49_one_lot_probe"],
)
def test_after_hours_configs_declare_the_adaptive_submission_start(name):
    """两个引用 AFTER_HOURS_FIXED_PRICE 的配置必须跟着 profile 一起改，
    否则 live_config 的逐项比对会把它们 fail-closed 掉。"""
    import yaml

    path = REPO_ROOT / "live_trading" / "configs" / (name + ".yaml")
    with open(path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    assert config["live"]["execution_session"] == "AFTER_HOURS_FIXED_PRICE"
    assert config["live"]["submit_after"] == "15:00:05"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_bridge_logic.py -k "finality or adaptive or timetag or deadline" tests/live_trading/test_live_config.py -k adaptive -q`
Expected: FAIL —— `AttributeError: '_close_is_final'` / `'SUBMIT_DEADLINE'`。

- [ ] **Step 3: 加两个终态判定函数**

放在 `_official_close`（第 2524 行）之后。

```python
def _close_is_final(tick):
    """Whether the tick's price is the settled close.

    Returns None when QMT does not expose a usable finality signal at all;
    the caller then falls back to the fixed deadline. Exchange rule 3.6.7
    fixes the close at 15:00:00, so anything stamped at or after that is
    settled and everything before it is the frozen 14:57 auction price.

    Deliberately does not use a volume jump as a signal: a name with no
    closing-auction trade would be misread as "not updated yet", and in
    exactly that case the 14:57 price already is the correct close.
    """
    if tick is None:
        return None
    timetag = _tick_field(tick, "timetag")
    if not isinstance(timetag, str):
        return None
    stamp = timetag.strip()[-8:]
    if not re.match(r"^\d{2}:\d{2}:\d{2}$", stamp):
        return None
    return stamp >= "15:00:00"


def _batch_close_is_final(ContextInfo, batch):
    """True only when every name in the batch reports a settled close.

    A name whose finality is unknown counts as not settled: waiting until the
    deadline is the cheap failure, sizing off a stale price is not. All-unknown
    means the signal does not exist here, which the caller treats as "go at the
    deadline" -- the fixed 15:01 behaviour.
    """
    signals = [_close_is_final(_get_tick(ContextInfo, o["stock_code"]))
               for o in batch.orders]
    if not signals or all(s is None for s in signals):
        return None
    return all(s is True for s in signals)
```

`re` 已在文件顶部 import（第 22 行）。

- [ ] **Step 4: profile 加 `submit_deadline`，时点提前**

bridge `_EXECUTION_PROFILES`：

```python
    "CLOSE_AUCTION": {
        ...
        "submit_after": "14:57:05",
        # Equal to submit_after: the finality gate must never engage here.
        "submit_deadline": "14:57:05",
        ...
    },
    "AFTER_HOURS_FIXED_PRICE": {
        ...
        # Earliest attempt. The close is settled at 15:00:00; what follows is
        # only quote propagation delay, and matching does not start until
        # 15:05, so this still queues ahead of every 15:05 arrival while the
        # finality gate keeps us from sizing off the frozen 14:57 price.
        "submit_after": "15:00:05",
        "submit_deadline": "15:01:00",
        ...
        "timer_start": "14:59:55",
    },
```

`_activate_profile_settings` 加 `global SUBMIT_DEADLINE` 与 `SUBMIT_DEADLINE = settings["submit_deadline"]`；模块常量区（第 58 行 `TRADE_START` 旁）加 `SUBMIT_DEADLINE = "14:57:05"`。`init` 的 `RUNTIME_CONFIG` 加 `submit_deadline=SUBMIT_DEADLINE`。

Mac 侧 `execution_profile.py` 的 `ExecutionProfile` 加 `submit_deadline: str`，两个 profile 填 `"14:57:05"` / `"15:01:00"`，并把 `AFTER_HOURS_FIXED_PRICE.submit_after` 改为 `"15:00:05"`。

- [ ] **Step 5: 采集 `timetag`**

`_market_price_evidence` 的 `_safe_detail` 字段清单加一项（放在 `latest_price` 之后）：

```python
        ("timetag", "timetag", "m_strTime", "time"),
```

- [ ] **Step 6: 接进 `_process_batch`**

第 3121–3123 行改为：

```python
    now = _now_hms()
    if now < TRADE_START:
        return
    if not batch.trading_started and now < SUBMIT_DEADLINE:
        finality = _batch_close_is_final(ContextInfo, batch)
        if finality is not True:
            _log_event(
                "CLOSE_FINALITY_WAIT",
                batch_id=batch.batch_id(),
                now=now,
                submit_deadline=SUBMIT_DEADLINE,
                finality=finality,
                message="close not settled yet; retrying before the deadline",
            )
            return
```

只在 `trading_started` 之前门禁：一旦开始交易就不再回头重判，否则半张批次会卡在两个时点之间。

- [ ] **Step 7: 改两个 live 配置的 `submit_after`**

两个引用 `AFTER_HOURS_FIXED_PRICE` 的配置各把 `submit_after` 改为 `"15:00:05"`：

- `live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml:99`
- `live_trading/configs/csi1000_pr49_one_lot_probe.yaml:20`

**为什么改探针的配置是安全的**：live 配置里这四个时点是**纯校验字段**，`live_config.py:96-104` 只拿它们与 Mac profile 逐项比对，Mac 侧不用它调度任何东西；探针实际提交时点由 QMT-local 编译副本里的 `TRADE_START` 常量决定，要到 Plan 3 重新 render 才会变。不改它反而会让探针的每日发布 fail-closed。

- [ ] **Step 8: 跑全套确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -q`
Expected: PASS 全绿。

- [ ] **Step 9: dry-run 回归，确认 Mac 侧没被 profile 改动打断**

Run:
```bash
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_publish_signals.py \
  --config alla_v4_ladder_k3h5_postclose_real \
  --trade-date 2026-07-31 --dry-run
```
Expected: 与 Task 4 Step 1 同样的输出，无 `ValueError: live.submit_after must match execution profile`。

- [ ] **Step 10: 提交**

```bash
git add live_trading/qmt_strategy/qmt_signal_bridge.py \
        live_trading/modules/execution_profile.py \
        live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml \
        live_trading/configs/csi1000_pr49_one_lot_probe.yaml \
        tests/live_trading/test_qmt_bridge_logic.py \
        tests/live_trading/test_live_config.py
git commit -m "feat(bridge): gate submission on close finality and attempt from 15:00:05 with a 15:01 fallback"
```

---

## 计划二完成判据

全部满足才算完成，才可以进入计划三：

- [x] `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ tests/backtest/test_cohort_ladder.py tests/backtest/test_cohort_ladder_strategy.py -q` 全绿（1071 passed，2026-08-24）
- [x] `check_backtest_parity.py` 对四个配置全部通过：`alla_v4_ladder_k3h5_postclose_real` / `csi1000_b6m_b2s_postclose_real` / `csi1000_b6m_b2s_postclose` / `csi300_topk10_live`。（`csi1000_pr49_one_lot_probe` 是 `OPERATOR_PROBE`，没有对照回测，不在此列）
- [x] 全A `--dry-run` 跑通（`EXIT=0`，无 traceback），输出 3 个 BUY：`301511.SZ` / `301196.SZ` / `603002.SH`，各 `target_value=60000.00`，与 `verify_ladder_dry_run.py` 的 top3 一致。`reason=cohort_layer`（卖腿 `cohort_due`）已单独核验——dry-run 的打印格式不含 reason 字段
- [x] `verify_ladder_dry_run.py --signal-date 2026-07-30` 打印 `top3 match: True`。~~`max |live - reference|` ≤ 1e-9~~ —— 该口径已作废：实测 `1.33e-03`，已定因为回测帧混入 4 只指数导致的**逐日仿射差**（Task 4 Step 3）。仿射保序，验收改判名次：Spearman ≥ 0.9999999，且全测试期 1211 天 rank3/rank4 间距无一天低于仿射残差的 6 倍上界 `1e-4`（实测 0 天，已由 `probe_topk_margin.py` 核实）
- [x] 全A dry-run 的墙钟与峰值 RSS 已实测并写回 spec 4.2：**墙钟 1098 秒、峰值 RSS 6.63 GiB**，其中 1075 秒（98%）在 handler 的 `Init data`
- [x] spec 第 6 节列的 bridge 侧单测全部落地：抵销五情形、板块最低申报、收盘价缺失、精确性回归、终态门禁、预算含卖出所得（计划一已覆盖）、买单阶段触发、现金封顶仍生效
- [x] `ENABLE_LADDER_NETTING` 仓库默认 `False`（`qmt_signal_bridge.py:54`），`test_netting_is_off_by_default_so_close_auction_is_untouched` 断言默认关闭时两腿照常下单
- [x] `MAX_ORDER_QUANTITY` 仍为 `100`（`qmt_signal_bridge.py:49`），两处 token 断言未被改动
- [x] 旧配置的 cron 未被改动；`render_qmt_runtime.py` 未被调用（自计划起点 `5cf3cd8e` 起 diff 为空）

## 不在本计划内（明确留给计划三）

这些都是 spec 第 5 节切换手册的步骤，或依赖真实 QMT 会话，刻意不在本计划做：

- **`MAX_ORDER_QUANTITY = 100` 取消**（spec 步骤 8）。代码路径已支持 `0 = 关闭`（`_process_batch` 第 3190/3261 行的 `MAX_ORDER_QUANTITY > 0` 判断），取消是**一行常量改动 + 三处测试 token 与两份 README 同步**（`tests/live_trading/test_repository_boundaries.py:70`、`tests/live_trading/test_operational_wrappers.py:69`、`tests/live_trading/test_render_qmt_runtime.py:18,33`、`live_trading/qmt_strategy/README_QMT.md`、`PR49_PROBE_CHECKLIST.md`）。本计划全程只跑 dry-run，一手闸对交付物毫无影响，提前拆掉纯属安全姿态的净损失——与计划一推迟 `MAIN_STRATEGY_ID` 的理由相同。
- **收盘价逐单对账判 CRIT**（spec 4.7.1 / 第 6 节）。bridge 侧的证据采集已在 Task 8/10 完成（`LADDER_NET` 带 `official_close` 与 `official_close_read_at`，`PREORDER_SNAPSHOT` 带 `timetag`）。Mac 侧次日 postmarket 用数据管道的权威收盘价逐单比对并告警属于监控，与 `fill_ratio` 一起做。**这条对账是接受 15:01 兜底路径尾部风险的前提，不得省略。**
- **`fill_ratio` 观测**（spec 4.7 / 第 8 节）：每日按单记录买卖两侧加权成交率、入监控、postmarket 输出，以及「连续 3 日买入侧 < 80% 或任一日 < 50% → 暂停发布」的回退触发。
- **收盘价终态信号探测**（spec 步骤 6）。Task 10 已把机制做成「无信号即退化为固定 15:01」，所以探测结论只影响实际走哪条分支，不阻塞代码。探测需要真实 QMT 会话：确认 `get_full_tick` 是否返回 `timetag`，结论写进实施记录。
- **三个主策略 ID 常量**（`operator_probe.MAIN_STRATEGY_ID` / `web/api.MAIN_REAL_STRATEGY_ID` / `live_config._OPERATOR_PROBE_MAIN_STRATEGY_ID`）仍指向 CSI1000，须与 `csi1000_pr49_one_lot_probe.yaml` 的 `main_strategy_id` 同步改（计划一已推迟到这里）。
- **`_SNAPSHOT_REQUEST_STRATEGIES` 加新 strategy_id**（`qmt_signal_bridge.py:1487-1490`）。注意这不是订单批次的允许列表（spec 4.1 的说法有误，见前置事实），只门禁快照观测请求。
- **`render_qmt_runtime.render_main_source` 补 `EXECUTION_PROFILE` / `BRIDGE_ROOT` / `OTHER_BRIDGE_ROOT`**（spec 步骤 7）。当前它只 render 六个账户相关常量，主实例因此仍会编译成 `CLOSE_AUCTION`。
- **退役 pr49 探针实例、双 marker 治理、cron 切换、新账本起账、README / AGENTS.md / EXPERIMENT_STANDARD.md / LESSONS.md 文档更新**（spec 步骤 5/7/9/10/11 与第 7 节）。
- **顺延频率诊断脚本**（spec 第 6 节）：给 BT v4 回测加计数器，量化「实盘不顺延」造成的名字集合差异频率。属于回测侧一次性诊断，与执行层无耦合。

## 交给计划三的接口

- `ENABLE_LADDER_NETTING` 在仓库里是 `False`。切换时要在 QMT-local 副本里置 `True`，**并且**在 `render_main_source` 的 settings 元组里加上它，否则每次 render 都会被打回 `False`。
- 抵销的权威记录是 `LADDER_NET` 事件，每个 BUY 一条（含非重叠买单，`due_shares=0`、`offsetable=false`）。字段：`stock_code` / `buy_client_order_id` / `sell_client_order_id` / `due_shares`(S) / `offsetable` / `target_value`(V) / `official_close`(C) / `official_close_read_at` / `sized_shares`(B) / `net_side` / `net_quantity` / `transferred_shares`(T)。次日对账读这一条即可，不必再拼 `PREORDER_SNAPSHOT`。
- 账本推进的口径是 `sold = applied_qty + netted_qty`、`filled = applied_qty + netted_qty`（`cohort_advance.day_executions`）。监控若要独立复算当日阶梯变动，用同一个口径，不要只看 `applied_qty`——被抵销的股数在那里恒为 0。
- 被抵销的两腿回执是 `status=SKIPPED` + `netted_qty>0` + `applied_qty=0`。**监控的「拒单率 / reject_rate」口径必须把它们排除**，否则抵销越成功、告警越响。`monitor.thresholds.reject_rate` 现为 0.5，一个三只票全重叠的交易日会直接打满。
- profile 的 `AFTER_HOURS_FIXED_PRICE` 现为 `submit_after=15:00:05` / `submit_deadline=15:01:00` / `sell_deadline=15:09:00` / `timer_start=14:59:55` / `cancel_at=15:28:00` / `finalize_at=15:30:00` / `snapshot_after=15:31:00`。spec 步骤 7 的 `RUNTIME_CONFIG` 逐项核对要按这组值，不是 spec 表里写的 15:05。
- `csi1000_pr49_one_lot_probe.yaml` 的 `submit_after` 已随 profile 改为 `15:00:05`（纯校验字段，探针的实际时点仍由其 QMT-local 编译副本决定）。退役探针时这个文件整体删除或标注即可，不必回滚该字段。
- `live_config.py:96-104` 校验的仍是 `submit_after` / `cancel_at` / `finalize_at` / `snapshot_after` 四项。`submit_deadline` 与 `sell_deadline` 是内部时序参数，**没有**进配置契约——若切换时想让操作员核对它们，要先把它们加进那个列表并同步两个配置。
