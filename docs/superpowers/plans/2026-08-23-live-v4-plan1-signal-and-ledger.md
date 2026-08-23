# 实盘迁移 BT v4 · 计划一：Mac 信号层与分层账本

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Mac 侧发布链路能为 BT v4 真阶梯策略产出一个正确的信号批次——五种子合成信号、全A 四重宇宙过滤、三张表持久化的分层账本、含预估卖出所得的预算——并能用 `--dry-run` 端到端验证，全程不触碰真实交易。

**Architecture:** 复用回测已有实现，不重写任何研究口径的逻辑。`CohortLedger`（`qlib/contrib/strategy/cohort_ladder.py`）加两个纯序列化方法和一个实盘专用的 `absorb_broker_excess`；新增 `live_trading/modules/cohort_store.py` 承载「账本状态 ↔ SQLite 行」的纯函数；`LiveRecorder` 只多三张表和两个薄 SQL 方法。信号合成复用 `blend_score_series`，宇宙过滤复用 `build_keep_mask`，都通过一层薄封装接入，把 `sys.path` 处理和单日索引构造收拢在一处。

**Tech Stack:** Python 3.11（`/opt/anaconda3/envs/qlib/bin/python`）、pandas、SQLite（`sqlite3` + WAL）、pytest、PyYAML、Qlib。

## Global Constraints

- Python 解释器一律用 `/opt/anaconda3/envs/qlib/bin/python`，测试从仓库根目录运行。
- macOS 下禁止用 heredoc / stdin 运行会触发 Qlib 并行取数的代码；此类验证必须写成 `.py` 文件再执行（见 `.cursor/rules/qlib-shell-multiprocessing.mdc`）。
- 固定 5 种子 `[42, 1000, 2000, 3000, 4000]`。
- BT v4 策略参数：`topk=3`、`horizon=5`、`risk_degree=0.90`、`only_tradable=false`、`forbid_all_trade_at_limit=false`。
- 宇宙过滤四项固定为：`st_daily="scripts/data_collector/tushare/st_daily.csv"`、`min_amount=10_000_000`、`min_listing_days=60`、`min_recent_trading_days=60`、`pool="all"`。
- **回测数字零风险**：本计划对 `qlib/contrib/strategy/cohort_ladder.py` 的改动只允许「新增方法」，不得修改 `due` / `settle` / `add` / `reconcile` / `extract` / `park_unsold` / `holdings` / `select_ladder_buys` / `cohort_budget` / `ledger_sell_amounts` 任何一行现有逻辑。每个任务结束前跑一次 `tests/backtest/test_cohort_ladder.py` 确认全绿。
- 新配置 id 固定为 `alla_v4_ladder_k3h5_postclose_real`；DB 路径 `live_trading/data/alla_v4_ladder_k3h5_postclose_real.db`。
- 账户号是私密信息，配置里 `account_id` 必须留空字符串，由 `QMT_REAL_ACCOUNT_ID` 提供。`tests/live_trading/test_repository_boundaries.py` 会扫描，写入任何数字账号都会让测试失败。
- 本计划**不改** `live_trading/qmt_strategy/qmt_signal_bridge.py`、不改 `live_trading/modules/backtest_parity.py`、不动 `MAX_ORDER_QUANTITY`。这些属于计划二与计划三。
- 本计划**不切换**任何调度：旧配置 `csi1000_b6m_b2s_postclose_real` 的 cron 保持原样运行，新配置只以 `--dry-run` 方式验证。

## 前置事实（已在写计划时验证，实施时不必重复验证）

- `backtest/` 与 `backtest/scripts/` **都没有** `__init__.py`。`from backtest.scripts.universe_filter import ...` 靠 PEP 420 命名空间包可用，但 `build_keep_mask` 内部是 `from eval_ic_multi_pool import ...` 这样的**裸模块导入**，所以必须把 `backtest/scripts` 目录本身插进 `sys.path`，否则运行时才会炸。
- `build_keep_mask` 对单日索引调用是正确的：`recent_trading_mask` 内部用 `D.calendar` 向前扩 `window-1` 个交易日再滚动（`backtest/scripts/eval_ic_multi_pool.py:1229-1246`），`_listing_age_mask` 用日历位置差，`amount_mask` 只用当日。不会因为窗口只有一天就把整个宇宙判掉。
- `SignalGenerator.predict` 返回的 Series 是**单层 instrument 索引**（`self._features.loc[target_ts]` 掉了 datetime 层）。而 `blend_score_series` 和 `build_keep_mask` 都要求两层 `(datetime, instrument)` MultiIndex。索引形状转换必须显式做。
- 信号 schema **无需改动**即可支撑 bridge 侧抵销：`live_trading/modules/signal_schema.py:169-187` 已强制 BUY 的 `quantity == 0` 且 `target_value > 0`，SELL 的 `quantity > 0` 且 `target_value == 0`。spec 4.4 里 bridge 需要的 `V` 就是 BUY 的 `target_value`，`S` 就是 SELL 的 `quantity`，两者都已在批次里。
- `LiveRecorder._conn()` 是 `@contextmanager`，正常退出 `commit()`、异常 `rollback()`（`fill_importer.py:172-189`）。所以「清表 + 重写」放在一个 `with self._conn() as conn:` 块里天然原子。
- 建表位置：spec 写的是 `_ensure_schema`，实际方法名是 `LiveRecorder._init_db`（`fill_importer.py:218`），用 `conn.executescript` 一次性跑 `CREATE TABLE IF NOT EXISTS`。
- `live_trading/modules/live_config.py` 的 `load_live_config` 没有键白名单，只校验 `performance_baseline` 与 `broker_environment` / `allow_real_money` 组合。新增 `model.members`、`strategy.horizon`、`universe_filter` 等段不会被拒。
- `live_trading/modules/fees.py` 的 `order_total_fee(side, cum_amount, fees)` 已按整单计费（含单笔最低佣金），可直接用来算预估卖出所得，不要另写费率乘法。
- 五个 v4 训练产物**存在**，在 `backtest/result/regimeadaptfast_m0h20_rankices_s{seed}/run_01/artifacts_root/artifacts/trained_model`。单个 60.1 MB，其中 `tradable_mask` 占 58.68 MB（97.6%），booster 仅 1.35 MB。`RegimeSingleLGBMModel` 没有覆写 `predict`（继承 `LGBModel`），`tradable_mask` 只在 `fit_prepared` 里被读——所以推理用不到。仓库没有 git-lfs，现有跟踪的 B6-M artifact 只有 3.2 MB。Task 9 负责精简导出。
- `tests/backtest/` 在 HEAD 上就有 22 个失败，全部集中在 `test_phase_s_protocol.py` / `test_run_strategy_sweep.py` / `test_finalize_strategy_neighborhood*.py` / `test_run_strategy_neighborhood_full.py`：`phase_s_protocol.py` 的 `RISK_DEGREE` 已随 B4-S 晋升改成 0.90，而这些测试还写着 0.95。**与本计划无关，不要试图修**，只需确认自己没有新增失败。

## 本计划新发现、spec 未覆盖的两个约束

1. **奇数股卖单会被 schema 拒掉。** `signal_schema.py:182-185` 要求 `SELL quantity % 100 == 0`。而真阶梯的 `_pending` 残量在送股（spec 4.3.1 的 `absorb_broker_excess`）之后必然出现非整百股数（如 400 股按 3:10 送股得 120 股）。这会让**发布脚本直接抛 SchemaError 卡死**。A 股规则是「不足一手只能一次性全部卖出」，而 schema 层看不到持仓、无法判断这一条，它现在执行的是一个对阶梯策略而言错误的近似。处理办法见 Task 7：把「整百」约束从 schema 移到下单器（那里能看到持仓），schema 只保留「正整数」。
2. **`cohort_layers` 的 `buy_trade_date` 不在 `CohortLedger` 里。** `_cohorts` 只是 `list[dict]`，`add()` 不接收日期。所以每层的买入日必须由 `cohort_store` 这一层维护，且**不能靠事后反推层数变化**。Task 3 的 `advanced_state` 用「settle 弹出条件 = `len(layers) >= horizon`」在调用前先算出来，避免反推。

---

### Task 1: `CohortLedger` 状态序列化

**Files:**
- Modify: `qlib/contrib/strategy/cohort_ladder.py`（在 `reconcile` 之后、`ledger_sell_amounts` 之前新增两个方法）
- Test: `tests/backtest/test_cohort_ladder.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `CohortLedger.to_state(self) -> dict[str, Any]`，返回 `{"horizon": int, "cohorts": list[dict[str, float]], "pending": dict[str, float]}`，`cohorts` 索引 0 最老
  - `CohortLedger.from_state(cls, state: Mapping[str, Any]) -> CohortLedger`（classmethod）

- [ ] **Step 1: 写失败测试**

追加到 `tests/backtest/test_cohort_ladder.py` 末尾：

```python
def test_to_state_preserves_layer_order_and_empty_layers():
    ledger = CohortLedger(horizon=3)
    ledger.add({"SH600000": 100.0})
    ledger.add({})  # 全部买单落空的空层，必须占位
    ledger.add({"SZ000001": 200.0, "SH600000": 300.0})

    state = ledger.to_state()

    assert state["horizon"] == 3
    assert state["cohorts"] == [
        {"SH600000": 100.0},
        {},
        {"SZ000001": 200.0, "SH600000": 300.0},
    ]
    assert state["pending"] == {}


def test_to_state_includes_pending_remnant():
    ledger = CohortLedger(horizon=1)
    ledger.add({"SH600000": 500.0})
    ledger.settle({"SH600000": 200.0})  # 到期只卖掉 200，300 转入 pending

    state = ledger.to_state()

    assert state["pending"] == {"SH600000": 300.0}
    assert state["cohorts"] == []


def test_from_state_round_trips_and_due_is_unchanged():
    original = CohortLedger(horizon=3)
    original.add({"SH600000": 100.0})
    original.add({})
    original.add({"SZ000001": 200.0})
    original.settle({})  # 让 pending 里有东西：horizon=3 且已有 3 层，弹出最老层
    original.add({"SH600519": 400.0})

    restored = CohortLedger.from_state(original.to_state())

    assert restored.horizon == original.horizon
    assert restored.to_state() == original.to_state()
    assert restored.due() == original.due()
    assert restored.holdings() == original.holdings()


def test_from_state_rejects_more_layers_than_horizon():
    state = {"horizon": 2, "cohorts": [{}, {}, {}], "pending": {}}

    with pytest.raises(ValueError, match="exceeds horizon"):
        CohortLedger.from_state(state)


def test_from_state_drops_zero_amounts():
    state = {
        "horizon": 2,
        "cohorts": [{"SH600000": 0.0, "SZ000001": 100.0}],
        "pending": {"SH600519": 0.0},
    }

    ledger = CohortLedger.from_state(state)

    assert ledger.to_state()["cohorts"] == [{"SZ000001": 100.0}]
    assert ledger.to_state()["pending"] == {}
```

确认该测试文件顶部已 `import pytest` 且已从 `qlib.contrib.strategy.cohort_ladder` 导入 `CohortLedger`；缺哪个就补哪个 import。

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_cohort_ladder.py -k "to_state or from_state" -v`
Expected: FAIL，报 `AttributeError: 'CohortLedger' object has no attribute 'to_state'`

- [ ] **Step 3: 实现两个方法**

在 `qlib/contrib/strategy/cohort_ladder.py` 的 `CohortLedger.reconcile` 方法之后插入：

```python
    def to_state(self) -> dict[str, Any]:
        """导出可序列化的台账快照，供跨进程持久化。

        ``cohorts`` 保持索引 0 最老的层序；空层照样导出，否则重建后阶梯账龄会错位。
        """
        return {
            "horizon": self.horizon,
            "cohorts": [dict(cohort) for cohort in self._cohorts],
            "pending": dict(self._pending),
        }

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> "CohortLedger":
        """从 ``to_state`` 的快照重建台账。"""
        ledger = cls(int(state["horizon"]))
        cohorts = list(state.get("cohorts") or [])
        if len(cohorts) > ledger.horizon:
            raise ValueError(
                f"cohorts ({len(cohorts)}) exceeds horizon ({ledger.horizon})"
            )
        ledger._cohorts = [
            {
                str(name): float(amount)
                for name, amount in cohort.items()
                if float(amount) > _EPS
            }
            for cohort in cohorts
        ]
        ledger._pending = {
            str(name): float(amount)
            for name, amount in (state.get("pending") or {}).items()
            if float(amount) > _EPS
        }
        return ledger
```

`_cohorts` 长度的合法区间是 `[0, horizon]`：每日先 `settle`（层数达 `horizon` 时弹出最老层）再 `add`（恒定追加一层），稳态恒为 `horizon`。所以 `> horizon` 是一个真实不变量，用来在重复导入回执时 fail-closed。

检查文件顶部 import：需要 `Any` 和 `Mapping`。若 `typing` / `collections.abc` 的导入行里没有，补上（现有代码已用 `Mapping`，通常只需补 `Any`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_cohort_ladder.py -v`
Expected: 全部 PASS（含原有用例）

- [ ] **Step 5: 提交**

```bash
git add qlib/contrib/strategy/cohort_ladder.py tests/backtest/test_cohort_ladder.py
git commit -m "feat(ladder): add CohortLedger state serialization for cross-process persistence"
```

---

### Task 2: `absorb_broker_excess`——券商多于台账时的吸收

**Files:**
- Modify: `qlib/contrib/strategy/cohort_ladder.py`（在 Task 1 的 `from_state` 之后新增）
- Test: `tests/backtest/test_cohort_ladder.py`

**Interfaces:**
- Consumes: Task 1 的 `to_state` / `from_state`（仅测试断言用）
- Produces: `CohortLedger.absorb_broker_excess(self, actual: Mapping[str, float]) -> dict[str, float]`，返回 `{股票: 实际吸收股数}` 供审计

背景见 spec 4.3.1：`reconcile` 只处理「台账 > 券商」，反向被 `continue` 跳过。实盘的送股 / 转增 / 配股 / 运维手工买卖会让券商股数多于台账，多出的部分对阶梯完全不可见，到期时 `ledger_sell_amounts` 取 `min(due, 实际持仓)` 只卖台账那部分，超出量会永久沉淀成孤儿仓位。

裁定：该票在阶梯里**已有分层** → 按各层股数等比例并入，随各层自然到期卖出；**完全没有分层** → 并入 `_pending`，次日全量进 `due` 尽快清掉。

- [ ] **Step 1: 写失败测试**

追加到 `tests/backtest/test_cohort_ladder.py`：

```python
def test_absorb_broker_excess_prorates_into_existing_layers():
    ledger = CohortLedger(horizon=3)
    ledger.add({"SH600000": 100.0})
    ledger.add({"SH600000": 300.0})
    # 台账合计 400 股；券商 520 股（3:10 送股得 120 股）

    absorbed = ledger.absorb_broker_excess({"SH600000": 520.0})

    assert absorbed == {"SH600000": 120.0}
    # 120 按 100:300 等比例 → 30 / 90
    assert ledger.to_state()["cohorts"] == [
        {"SH600000": 130.0},
        {"SH600000": 390.0},
    ]
    assert ledger.holdings() == {"SH600000": 520.0}


def test_absorb_broker_excess_remainder_goes_to_largest_layer():
    ledger = CohortLedger(horizon=3)
    ledger.add({"SH600000": 100.0})
    ledger.add({"SH600000": 200.0})
    # 台账 300 股，券商 310 股，excess=10；等比例为 3.33 / 6.67
    # 最大余额法：floor 得 3 / 6，余 1 补给小数部分最大的那层（0.67 > 0.33）

    absorbed = ledger.absorb_broker_excess({"SH600000": 310.0})

    assert absorbed == {"SH600000": 10.0}
    assert ledger.holdings() == {"SH600000": 310.0}
    assert ledger.to_state()["cohorts"] == [
        {"SH600000": 103.0},
        {"SH600000": 207.0},
    ]


def test_absorb_broker_excess_without_layers_goes_to_pending():
    ledger = CohortLedger(horizon=3)
    ledger.add({"SH600000": 100.0})
    # SZ000001 在阶梯里完全没有分层（运维手工买入）

    absorbed = ledger.absorb_broker_excess({"SH600000": 100.0, "SZ000001": 500.0})

    assert absorbed == {"SZ000001": 500.0}
    assert ledger.to_state()["pending"] == {"SZ000001": 500.0}
    # 次日 due 会把它全量列入卖出清单
    assert ledger.due()["SZ000001"] == 500.0


def test_absorb_broker_excess_is_noop_when_ledger_matches_or_exceeds():
    ledger = CohortLedger(horizon=3)
    ledger.add({"SH600000": 100.0})
    ledger.add({"SZ000001": 200.0})
    before = ledger.to_state()

    # SH600000 相等、SZ000001 券商更少（该由 reconcile 处理）、SH600519 券商为 0
    absorbed = ledger.absorb_broker_excess({
        "SH600000": 100.0, "SZ000001": 50.0, "SH600519": 0.0,
    })

    assert absorbed == {}
    assert ledger.to_state() == before


def test_absorb_broker_excess_only_counts_layers_holding_that_name():
    ledger = CohortLedger(horizon=3)
    ledger.add({"SH600000": 100.0})
    ledger.add({"SZ000001": 900.0})  # 不持有 SH600000 的层不参与分配

    absorbed = ledger.absorb_broker_excess({"SH600000": 150.0, "SZ000001": 900.0})

    assert absorbed == {"SH600000": 50.0}
    assert ledger.to_state()["cohorts"] == [
        {"SH600000": 150.0},
        {"SZ000001": 900.0},
    ]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_cohort_ladder.py -k absorb -v`
Expected: FAIL，报 `AttributeError: 'CohortLedger' object has no attribute 'absorb_broker_excess'`

- [ ] **Step 3: 实现**

在 `qlib/contrib/strategy/cohort_ladder.py` 的 `from_state` 之后插入：

```python
    def absorb_broker_excess(self, actual: Mapping[str, float]) -> dict[str, float]:
        """券商股数多于台账时，把超出部分并入台账（**实盘专用，回测不调用**）。

        ``reconcile`` 只削减台账多出的部分，反向缺口它不处理。实盘的送股 / 转增 /
        配股 / 手工买卖都会让券商多于台账，而多出的股数对阶梯不可见，到期时
        ``ledger_sell_amounts`` 取小只卖台账那份，超出量会永久沉淀。

        该票已有分层 → 按各层股数等比例并入（最大余额法分配整股余数，余数并列时
        偏向股数更多的层），随各层自然到期卖出；完全没有分层 → 并入 ``_pending``，
        次日全量进 ``due``。返回每只票实际吸收的股数，供审计记录。
        """
        absorbed: dict[str, float] = {}
        ledger_totals = self.holdings()
        for name, amount in actual.items():
            broker = float(amount)
            if broker <= _EPS:
                continue
            excess = float(round(broker - ledger_totals.get(name, 0.0)))
            if excess <= _EPS:
                continue
            buckets = [c for c in self._cohorts if c.get(name, 0.0) > _EPS]
            if not buckets:
                self._pending[name] = self._pending.get(name, 0.0) + excess
                absorbed[name] = excess
                continue
            weights = [bucket[name] for bucket in buckets]
            total = sum(weights)
            exact = [excess * weight / total for weight in weights]
            shares = [math.floor(value) for value in exact]
            remainder = int(round(excess - sum(shares)))
            order = sorted(
                range(len(buckets)),
                key=lambda i: (-(exact[i] - shares[i]), -weights[i]),
            )
            for i in order[:remainder]:
                shares[i] += 1
            for bucket, extra in zip(buckets, shares):
                if extra > 0:
                    bucket[name] = bucket[name] + float(extra)
            absorbed[name] = float(sum(shares))
        return absorbed
```

`excess` 先 `round` 成整股，再用最大余额法分配，保证 `sum(shares) == excess` 精确成立——账实差必须被完全吸收，不允许留零头。

检查文件顶部是否已 `import math`；没有就加（放在标准库导入区，字母序）。

- [ ] **Step 4: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_cohort_ladder.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 确认回测路径未受影响**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_cohort_ladder.py tests/backtest/test_cohort_ladder_strategy.py -v`
Expected: 全部 PASS。`absorb_broker_excess` 是新增方法，`CohortLadderStrategy` 里没有任何调用点，BT v4 数字不变。

- [ ] **Step 6: 提交**

```bash
git add qlib/contrib/strategy/cohort_ladder.py tests/backtest/test_cohort_ladder.py
git commit -m "feat(ladder): absorb broker share excess into ledger layers for live trading"
```

---

### Task 3: 分层账本状态的纯函数层

**Files:**
- Create: `live_trading/modules/cohort_store.py`
- Test: `tests/live_trading/test_cohort_store.py`

**Interfaces:**
- Consumes: Task 1 的 `CohortLedger.to_state` / `from_state`，Task 2 的 `absorb_broker_excess`
- Produces:
  - `CohortState` dataclass：`layers: tuple[tuple[str, dict[str, int]], ...]`（索引 0 最老，元素为 `(buy_trade_date, {code: shares})`）、`pending: dict[str, int]`
  - `EMPTY_COHORT_STATE: CohortState`
  - `state_to_ledger(state: CohortState, *, horizon: int) -> CohortLedger`
  - `reconciled_state(state: CohortState, broker_positions: Mapping[str, float], *, horizon: int) -> tuple[CohortState, dict[str, float]]`
  - `advanced_state(state: CohortState, *, horizon: int, trade_date: str, sold: Mapping[str, float], filled: Mapping[str, float]) -> CohortState`

这一层是账本逻辑唯一的可测落点。`LiveRecorder`（Task 4）只做 SQL，不含判断。

- [ ] **Step 1: 写失败测试**

创建 `tests/live_trading/test_cohort_store.py`：

```python
import pytest

from live_trading.modules.cohort_store import (
    EMPTY_COHORT_STATE,
    CohortState,
    advanced_state,
    reconciled_state,
    state_to_ledger,
)


def test_state_to_ledger_preserves_layer_order():
    state = CohortState(
        layers=(
            ("2026-08-17", {"SH600000": 100}),
            ("2026-08-18", {}),
            ("2026-08-19", {"SZ000001": 200}),
        ),
        pending={"SH600519": 300},
    )

    ledger = state_to_ledger(state, horizon=5)

    assert ledger.horizon == 5
    assert ledger.to_state()["cohorts"] == [
        {"SH600000": 100.0}, {}, {"SZ000001": 200.0},
    ]
    assert ledger.to_state()["pending"] == {"SH600519": 300.0}


def test_advanced_state_appends_layer_without_maturing_below_horizon():
    state = CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={})

    out = advanced_state(
        state, horizon=5, trade_date="2026-08-20",
        sold={}, filled={"SZ000001": 200},
    )

    assert out.layers == (
        ("2026-08-19", {"SH600000": 100}),
        ("2026-08-20", {"SZ000001": 200}),
    )
    assert out.pending == {}


def test_advanced_state_pops_oldest_layer_at_horizon():
    state = CohortState(
        layers=tuple(
            (f"2026-08-1{i}", {"SH60000{}".format(i): 100}) for i in range(5)
        ),
        pending={},
    )

    out = advanced_state(
        state, horizon=5, trade_date="2026-08-20",
        sold={"SH600000": 100}, filled={"SZ000001": 200},
    )

    dates = [date for date, _ in out.layers]
    assert dates == ["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-20"]
    assert out.pending == {}


def test_advanced_state_parks_unsold_due_amount_in_pending():
    state = CohortState(
        layers=tuple(
            (f"2026-08-1{i}", {"SH600000": 500} if i == 0 else {}) for i in range(5)
        ),
        pending={},
    )

    # 到期层 500 股只卖掉 200（停牌 / 跌停），300 股必须挂账次日重试
    out = advanced_state(
        state, horizon=5, trade_date="2026-08-20",
        sold={"SH600000": 200}, filled={},
    )

    assert out.pending == {"SH600000": 300}
    assert out.layers[-1] == ("2026-08-20", {})


def test_advanced_state_records_empty_layer_when_all_buys_miss():
    state = CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={})

    out = advanced_state(
        state, horizon=5, trade_date="2026-08-20", sold={}, filled={},
    )

    assert out.layers[-1] == ("2026-08-20", {})


def test_advanced_state_rejects_duplicate_trade_date():
    state = CohortState(layers=(("2026-08-20", {"SH600000": 100}),), pending={})

    with pytest.raises(ValueError, match="already exists"):
        advanced_state(
            state, horizon=5, trade_date="2026-08-20", sold={}, filled={},
        )


def test_advanced_state_rejects_fractional_shares():
    state = CohortState(layers=(), pending={})

    with pytest.raises(ValueError, match="whole shares"):
        advanced_state(
            state, horizon=5, trade_date="2026-08-20",
            sold={}, filled={"SH600000": 100.5},
        )


def test_reconciled_state_prunes_ledger_surplus_and_keeps_dates():
    state = CohortState(
        layers=(
            ("2026-08-19", {"SH600000": 100}),
            ("2026-08-20", {"SH600000": 200}),
        ),
        pending={},
    )

    # 券商只有 100 股：最新一层的买单整单落空
    out, absorbed = reconciled_state(state, {"SH600000": 100}, horizon=5)

    assert absorbed == {}
    assert out.layers == (
        ("2026-08-19", {"SH600000": 100}),
        ("2026-08-20", {}),
    )


def test_reconciled_state_absorbs_broker_excess_and_reports_it():
    state = CohortState(
        layers=(
            ("2026-08-19", {"SH600000": 100}),
            ("2026-08-20", {"SH600000": 300}),
        ),
        pending={},
    )

    out, absorbed = reconciled_state(state, {"SH600000": 520}, horizon=5)

    assert absorbed == {"SH600000": 120.0}
    assert out.layers == (
        ("2026-08-19", {"SH600000": 130}),
        ("2026-08-20", {"SH600000": 390}),
    )


def test_empty_state_advances_from_scratch():
    out = advanced_state(
        EMPTY_COHORT_STATE, horizon=5, trade_date="2026-08-20",
        sold={}, filled={"SH600000": 100},
    )

    assert out.layers == (("2026-08-20", {"SH600000": 100}),)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_store.py -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'live_trading.modules.cohort_store'`

- [ ] **Step 3: 实现**

创建 `live_trading/modules/cohort_store.py`：

```python
"""分层账本状态与 SQLite 行之间的纯转换。

权威副本始终是 SQLite；``CohortLedger`` 只是单次进程内的临时视图。发布（16:00）
与回执导入（15:31 之后）是两个进程，中间隔着 QMT 执行，任何一步失败都不写回，
下次从 DB 重建并对券商快照 reconcile。

每层的买入日**不在** ``CohortLedger`` 里（``_cohorts`` 只是 list，``add`` 不接收
日期），所以由本模块维护，且不靠事后反推层数变化——``settle`` 的弹出条件在调用前
就能由 ``len(layers) >= horizon`` 算出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from qlib.contrib.strategy.cohort_ladder import CohortLedger


@dataclass(frozen=True)
class CohortState:
    """账本的持久化形态。``layers`` 索引 0 最老，空层照样占位。"""

    layers: tuple[tuple[str, dict[str, int]], ...] = ()
    pending: dict[str, int] = field(default_factory=dict)


EMPTY_COHORT_STATE = CohortState()


def _whole_shares(amounts: Mapping[str, float]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, amount in amounts.items():
        value = float(amount)
        if abs(value - round(value)) > 1e-6:
            raise ValueError(f"cohort ledger holds whole shares only: {name}={value}")
        shares = int(round(value))
        if shares > 0:
            out[str(name)] = shares
    return out


def state_to_ledger(state: CohortState, *, horizon: int) -> CohortLedger:
    """把持久化状态还原成台账视图。"""
    return CohortLedger.from_state({
        "horizon": horizon,
        "cohorts": [dict(shares) for _, shares in state.layers],
        "pending": dict(state.pending),
    })


def _ledger_to_state(ledger: CohortLedger, dates: list[str]) -> CohortState:
    snapshot: dict[str, Any] = ledger.to_state()
    cohorts = snapshot["cohorts"]
    if len(cohorts) != len(dates):
        raise ValueError(
            f"layer/date count mismatch: {len(cohorts)} layers vs {len(dates)} dates"
        )
    return CohortState(
        layers=tuple(
            (date, _whole_shares(shares)) for date, shares in zip(dates, cohorts)
        ),
        pending=_whole_shares(snapshot["pending"]),
    )


def reconciled_state(
    state: CohortState,
    broker_positions: Mapping[str, float],
    *,
    horizon: int,
) -> tuple[CohortState, dict[str, float]]:
    """先削减台账多出的部分，再吸收券商多出的部分。层数与层日期都不变。"""
    ledger = state_to_ledger(state, horizon=horizon)
    ledger.reconcile(broker_positions)
    absorbed = ledger.absorb_broker_excess(broker_positions)
    dates = [date for date, _ in state.layers]
    return _ledger_to_state(ledger, dates), absorbed


def advanced_state(
    state: CohortState,
    *,
    horizon: int,
    trade_date: str,
    sold: Mapping[str, float],
    filled: Mapping[str, float],
) -> CohortState:
    """按当日实际成交推进一天：``settle(卖出)`` 后 ``add(买入)``。

    ``settle`` 在层数达 ``horizon`` 时弹出最老层，``add`` 恒定追加一层，所以新的
    日期列表由旧列表按同一规则推导。重复推进同一天会被拒——否则阶梯会涨到
    ``horizon + 1`` 层，后续所有到期日集体错位。
    """
    if any(date == trade_date for date, _ in state.layers):
        raise ValueError(f"cohort layer for {trade_date} already exists")
    ledger = state_to_ledger(state, horizon=horizon)
    matured = len(state.layers) >= horizon
    ledger.settle(sold)
    ledger.add(_whole_shares(filled))
    dates = [date for date, _ in state.layers]
    if matured:
        dates = dates[1:]
    dates.append(trade_date)
    return _ledger_to_state(ledger, dates)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_store.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add live_trading/modules/cohort_store.py tests/live_trading/test_cohort_store.py
git commit -m "feat(live): add cohort ledger state layer with per-layer trade dates"
```

---

### Task 4: 三张表与 `LiveRecorder` 读写

**Files:**
- Modify: `live_trading/modules/fill_importer.py`（`_init_db` 的 `executescript` 里加三张表；类末尾加两个方法）
- Test: `tests/live_trading/test_cohort_store.py`（追加 recorder 往返用例）

**Interfaces:**
- Consumes: Task 3 的 `CohortState` / `EMPTY_COHORT_STATE`
- Produces:
  - `LiveRecorder.load_cohort_state(self) -> CohortState`
  - `LiveRecorder.save_cohort_state(self, state: CohortState) -> None`

`cohort_layer_dates` 必须单独存在，因为**空层也要占位**：某天全部买单落空时 `cohort_layers` 不会有任何行，但那一层仍要占阶梯的一个位置，否则整条阶梯账龄提前一天、到期日全部错位。`seq` 决定层序（升序，0 最老）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/live_trading/test_cohort_store.py`：

```python
from live_trading.modules.fill_importer import LiveRecorder


def _recorder(tmp_path):
    return LiveRecorder(str(tmp_path / "ladder.db"), opening_cash=1_000_000.0)


def test_load_cohort_state_is_empty_on_fresh_db(tmp_path):
    assert _recorder(tmp_path).load_cohort_state() == EMPTY_COHORT_STATE


def test_save_then_load_cohort_state_round_trips(tmp_path):
    recorder = _recorder(tmp_path)
    state = CohortState(
        layers=(
            ("2026-08-17", {"SH600000": 100}),
            ("2026-08-18", {}),
            ("2026-08-19", {"SZ000001": 200, "SH600519": 300}),
        ),
        pending={"SH601318": 400},
    )

    recorder.save_cohort_state(state)

    assert recorder.load_cohort_state() == state


def test_saved_empty_layer_keeps_its_ladder_slot(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-18", {}), ("2026-08-19", {"SH600000": 100})),
                    pending={})
    )

    loaded = recorder.load_cohort_state()

    assert [date for date, _ in loaded.layers] == ["2026-08-18", "2026-08-19"]
    assert loaded.layers[0][1] == {}


def test_save_cohort_state_replaces_previous_snapshot(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={"SZ000001": 50})
    )

    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-20", {"SH600519": 200}),), pending={})
    )

    loaded = recorder.load_cohort_state()
    assert loaded.layers == (("2026-08-20", {"SH600519": 200}),)
    assert loaded.pending == {}


def test_cohort_state_survives_a_full_day_advance(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={})
    )

    state = advanced_state(
        recorder.load_cohort_state(), horizon=5, trade_date="2026-08-20",
        sold={}, filled={"SZ000001": 200},
    )
    recorder.save_cohort_state(state)

    assert recorder.load_cohort_state() == state
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_store.py -k cohort_state -v`
Expected: FAIL，报 `AttributeError: 'LiveRecorder' object has no attribute 'load_cohort_state'`

- [ ] **Step 3: 建表**

在 `live_trading/modules/fill_importer.py` 的 `_init_db` 里，`conn.executescript("""...""")` 字符串内部、`CREATE TABLE IF NOT EXISTS execution_state` 之前插入：

```sql
                CREATE TABLE IF NOT EXISTS cohort_layers (
                    buy_trade_date TEXT NOT NULL,
                    stock_code     TEXT NOT NULL,
                    shares         INTEGER NOT NULL,
                    updated_at     TEXT DEFAULT (datetime('now', 'localtime')),
                    PRIMARY KEY (buy_trade_date, stock_code)
                );

                CREATE TABLE IF NOT EXISTS cohort_layer_dates (
                    buy_trade_date TEXT PRIMARY KEY,
                    seq            INTEGER NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS cohort_pending (
                    stock_code TEXT PRIMARY KEY,
                    shares     INTEGER NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
                );
```

- [ ] **Step 4: 实现读写方法**

在 `LiveRecorder` 类里、`get_positions` 方法之后插入：

```python
    # ---------- 分层账本（真阶梯专用）----------

    def load_cohort_state(self):
        """按 seq 升序还原分层账本；空层靠 cohort_layer_dates 占位。"""
        from live_trading.modules.cohort_store import CohortState

        with self._conn() as conn:
            dates = [
                row["buy_trade_date"]
                for row in conn.execute(
                    "SELECT buy_trade_date FROM cohort_layer_dates ORDER BY seq"
                )
            ]
            shares_by_date: dict[str, dict[str, int]] = {date: {} for date in dates}
            for row in conn.execute(
                "SELECT buy_trade_date, stock_code, shares FROM cohort_layers"
            ):
                bucket = shares_by_date.get(row["buy_trade_date"])
                if bucket is None:
                    raise SchemaError(
                        "cohort_layers references an unregistered layer date: "
                        f"{row['buy_trade_date']}"
                    )
                bucket[row["stock_code"]] = int(row["shares"])
            pending = {
                row["stock_code"]: int(row["shares"])
                for row in conn.execute("SELECT stock_code, shares FROM cohort_pending")
            }
        return CohortState(
            layers=tuple((date, shares_by_date[date]) for date in dates),
            pending=pending,
        )

    def save_cohort_state(self, state) -> None:
        """整体重写三张表。_conn() 正常退出即提交、异常即回滚，天然原子。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM cohort_layers")
            conn.execute("DELETE FROM cohort_layer_dates")
            conn.execute("DELETE FROM cohort_pending")
            for seq, (date, shares) in enumerate(state.layers):
                conn.execute(
                    "INSERT INTO cohort_layer_dates (buy_trade_date, seq) VALUES (?, ?)",
                    (date, seq),
                )
                for code, amount in shares.items():
                    conn.execute(
                        "INSERT INTO cohort_layers (buy_trade_date, stock_code, shares) "
                        "VALUES (?, ?, ?)",
                        (date, code, int(amount)),
                    )
            for code, amount in state.pending.items():
                conn.execute(
                    "INSERT INTO cohort_pending (stock_code, shares) VALUES (?, ?)",
                    (code, int(amount)),
                )
```

`load_cohort_state` 里的 `SchemaError` 已在 `fill_importer.py` 顶部从 `signal_schema` 导入，直接用。方法内导入 `CohortState` 是为了避免 `cohort_store` → `cohort_ladder` → qlib 的导入链在 `fill_importer` 模块加载时就被拉起。

- [ ] **Step 5: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_store.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 确认存量账本迁移无损**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_fill_importer.py -v`
Expected: 全部 PASS。三张表走 `CREATE TABLE IF NOT EXISTS`，对旧 DB 只是新增空表。

- [ ] **Step 7: 提交**

```bash
git add live_trading/modules/fill_importer.py tests/live_trading/test_cohort_store.py
git commit -m "feat(live): persist cohort ladder layers in three dedicated tables"
```

---

### Task 5: 五种子合成信号

**Files:**
- Modify: `live_trading/modules/signal_generator.py`
- Test: `tests/live_trading/test_signal_generator.py`

**Interfaces:**
- Consumes: 无
- Produces: `SignalGenerator.predict(target_date, allow_stale=False) -> pd.Series`（单层 instrument 索引，语义不变；配置含 `model.members` 时返回五成员合成分数）

合成口径必须与研究一致：复用 `backtest/scripts/ensemble_preds.py` 的 `blend_score_series`——各成员分数在当日截面 z-score 后等权平均。该函数按 `datetime` 分组，对单日输入退化为「该日截面标准化后平均」，与研究口径在同一日上逐点相等，不引入前视。

注意 `_score_features` 现在返回单层 instrument 索引的 Series，而 `blend_score_series` 要求含 `datetime` 的 MultiIndex，所以要先套上单日的 datetime 层、合成后再摘掉。

- [ ] **Step 1: 写失败测试**

追加到 `tests/live_trading/test_signal_generator.py`（先读一遍该文件顶部，沿用它已有的 fixture 与假模型构造方式；下面用最小自足写法，若文件里已有等价 helper 就复用而不是重复定义）：

```python
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from qlib.data.dataset.handler import DataHandlerLP

from live_trading.modules.signal_generator import SignalGenerator


class _FakeModel:
    """按 instrument 顺序返回固定分数，用于验证合成算术。"""

    def __init__(self, values):
        self._values = values

    def predict(self, dataset, segment="test"):
        features = dataset.prepare(
            segment, col_set="feature", data_key=DataHandlerLP.DK_I,
        )
        return pd.Series(self._values, index=features.index, dtype=float)


def _generator():
    return SignalGenerator({"model": {}, "handler": {}, "data": {}}, Path("."))


def _install(gen, models, features, date):
    gen._models = models
    gen._features = pd.DataFrame(
        {"f": np.arange(len(features), dtype=float)},
        index=pd.MultiIndex.from_product(
            [[pd.Timestamp(date)], features], names=["datetime", "instrument"],
        ),
    )
    gen._handler_end_date = date


def test_ensemble_zscores_each_member_before_averaging():
    gen = _generator()
    names = ["SH600000", "SZ000001", "SH600519"]
    # 成员 A 与成员 B 排序相反，等权合成后应完全抵平
    _install(gen, [_FakeModel([1.0, 2.0, 3.0]), _FakeModel([3.0, 2.0, 1.0])],
             names, "2026-08-20")

    scores = gen.predict("2026-08-20")

    assert list(scores.index) == names
    np.testing.assert_allclose(scores.to_numpy(), [0.0, 0.0, 0.0], atol=1e-9)


def test_ensemble_is_invariant_to_member_scale():
    gen = _generator()
    names = ["SH600000", "SZ000001", "SH600519"]
    # 同一排序、量纲差 1000 倍：z-score 后两成员完全相同，合成等于单成员
    _install(gen, [_FakeModel([1.0, 2.0, 3.0]), _FakeModel([1000.0, 2000.0, 3000.0])],
             names, "2026-08-20")
    blended = gen.predict("2026-08-20")

    gen_single = _generator()
    _install(gen_single, [_FakeModel([1.0, 2.0, 3.0])], names, "2026-08-20")
    single = gen_single.predict("2026-08-20")

    np.testing.assert_allclose(blended.to_numpy(), single.to_numpy(), atol=1e-9)


def test_single_member_path_returns_plain_instrument_index():
    gen = _generator()
    names = ["SH600000", "SZ000001"]
    _install(gen, [_FakeModel([1.0, 2.0])], names, "2026-08-20")

    scores = gen.predict("2026-08-20")

    assert not isinstance(scores.index, pd.MultiIndex)
    assert list(scores.index) == names


def test_load_model_requires_sha256_for_every_member(tmp_path):
    config = {
        "model": {
            "members": [
                {"seed": 42, "model_path": "live_trading/models/x/s42/trained_model",
                 "sha256": "a" * 64},
                {"seed": 1000, "model_path": "live_trading/models/x/s1000/trained_model"},
            ]
        },
        "handler": {}, "data": {},
    }
    gen = SignalGenerator(config, tmp_path)

    with pytest.raises(ValueError, match="sha256"):
        gen.load_model()


def test_load_model_rejects_empty_members_list(tmp_path):
    gen = SignalGenerator({"model": {"members": []}, "handler": {}, "data": {}}, tmp_path)

    with pytest.raises(ValueError, match="members"):
        gen.load_model()


def test_handler_receives_filter_pipe_when_configured(monkeypatch, tmp_path):
    """配置里的 filter_pipe 必须真的传给 handler，不能被静默忽略。"""
    import live_trading.modules.signal_generator as sg

    captured = {}

    def fake_init_instance_by_config(cfg):
        captured["kwargs"] = cfg["kwargs"]

        class _H:
            def fetch(self, col_set, data_key):
                return pd.DataFrame(
                    {"f": [1.0]},
                    index=pd.MultiIndex.from_tuples(
                        [(pd.Timestamp("2026-08-20"), "SH600000")],
                        names=["datetime", "instrument"],
                    ),
                )

        return _H()

    monkeypatch.setattr(sg, "init_instance_by_config", fake_init_instance_by_config)

    filter_pipe = [{"filter_type": "NameDFilter", "name_rule_re": "^(SH60|SH68|SZ00|SZ30)"}]
    gen = SignalGenerator(
        {
            "model": {},
            "data": {"instruments": "all"},
            "handler": {
                "class": "Alpha158Technical",
                "module": "backtest.features.technical",
                "start_time": "2020-02-03",
                "fit_start_time": "2020-02-03",
                "fit_end_time": "2020-08-03",
                "infer_processors": [{"class": "ProcessInf"}],
                "feature_groups": ["range"],
                "filter_pipe": filter_pipe,
            },
        },
        tmp_path,
    )
    gen._models = [_FakeModel([1.0])]

    gen._ensure_handler("2026-08-20")

    assert captured["kwargs"]["filter_pipe"] == filter_pipe


def test_handler_omits_filter_pipe_when_absent(monkeypatch, tmp_path):
    import live_trading.modules.signal_generator as sg

    captured = {}

    def fake_init_instance_by_config(cfg):
        captured["kwargs"] = cfg["kwargs"]

        class _H:
            def fetch(self, col_set, data_key):
                return pd.DataFrame(
                    {"f": [1.0]},
                    index=pd.MultiIndex.from_tuples(
                        [(pd.Timestamp("2026-08-20"), "SH600000")],
                        names=["datetime", "instrument"],
                    ),
                )

        return _H()

    monkeypatch.setattr(sg, "init_instance_by_config", fake_init_instance_by_config)

    gen = SignalGenerator(
        {
            "model": {},
            "data": {"instruments": "csi1000"},
            "handler": {
                "class": "Alpha158Technical",
                "module": "backtest.features.technical",
                "start_time": "2003-01-02",
                "fit_start_time": "2016-01-02",
                "fit_end_time": "2020-01-10",
                "infer_processors": [{"class": "ProcessInf"}],
                "feature_groups": ["range"],
            },
        },
        tmp_path,
    )
    gen._models = [_FakeModel([1.0])]

    gen._ensure_handler("2026-08-20")

    assert "filter_pipe" not in captured["kwargs"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_signal_generator.py -k "ensemble or member" -v`
Expected: FAIL，报 `AttributeError` 或 `_models` 相关错误（当前实现只有 `_model` 单数）

- [ ] **Step 3: 实现**

改 `live_trading/modules/signal_generator.py`。

顶部导入区加：

```python
import sys

_BACKTEST_SCRIPTS = Path(__file__).resolve().parents[2] / "backtest" / "scripts"
if str(_BACKTEST_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKTEST_SCRIPTS))
from ensemble_preds import blend_score_series  # noqa: E402
```

`__init__` 里 `self._model = None` 改为：

```python
        self._models = None
```

`load_model` 整体替换为：

```python
    def load_model(self):
        if self._models is not None:
            return

        model_cfg = self.config["model"]
        members = model_cfg.get("members")
        if members is None:
            specs = [{
                "model_path": model_cfg.get("model_path"),
                "sha256": model_cfg.get("sha256"),
            }]
        else:
            if not isinstance(members, list) or not members:
                raise ValueError("model.members must be a non-empty list")
            specs = members

        models = []
        for spec in specs:
            relative_path = spec.get("model_path")
            if not relative_path:
                raise ValueError(
                    "model_path is required; live models must be loaded "
                    "from the Git-tracked model directory"
                )
            expected_sha256 = spec.get("sha256")
            if not expected_sha256:
                raise ValueError(
                    f"sha256 is required for live model integrity: {relative_path}"
                )
            model, model_path = load_model_artifact(
                relative_path,
                expected_sha256,
                project_root=self.project_root,
            )
            models.append(model)
            logger.info(
                "Model loaded from %s (sha256=%s)", model_path, expected_sha256
            )
        self._models = models
```

`_score_features` 整体替换为：

```python
    def _score_features(self, day_features: pd.DataFrame, target_date: str) -> pd.Series:
        """Score one day through each frozen model, then blend as research does."""
        day_features = day_features.dropna(how="all")
        stamp = pd.Timestamp(target_date)
        member_scores = []
        for model in self._models:
            raw_scores = model.predict(_InferenceDataset(day_features), segment="test")
            if not isinstance(raw_scores, pd.Series):
                raise TypeError("model.predict must return a pandas Series")
            if raw_scores.index.has_duplicates:
                raise ValueError("model prediction index contains duplicates")
            if not raw_scores.index.equals(day_features.index):
                raise ValueError("model prediction index must exactly match feature index")
            member = raw_scores.astype(float)
            member = member[np.isfinite(member)]
            member.index = pd.MultiIndex.from_arrays(
                [[stamp] * len(member), member.index],
                names=["datetime", "instrument"],
            )
            member_scores.append(member.rename("score"))

        blended = blend_score_series(member_scores)
        scores = blended.droplevel("datetime").rename("score")
        scores = scores[np.isfinite(scores)]

        if scores.empty:
            logger.warning("Generated no finite predictions for %s", target_date)
        else:
            logger.info(
                "Generated predictions for %s: %d instruments from %d model(s), "
                "top=%.6f, bottom=%.6f",
                target_date, len(scores), len(self._models),
                scores.max(), scores.min(),
            )
        return scores
```

`_ensure_handler` 里，紧跟在现有的 `feature_groups` 透传之后补一段——配置写了 `filter_pipe` 却不透传，这个字段就是死的，而 parity 门禁又要按它逐项比对，两边会不一致：

```python
        if "filter_pipe" in handler_cfg:
            handler_kwargs["filter_pipe"] = handler_cfg["filter_pipe"]
```

`predict` 里的 `self.load_model()` 调用位置不变。

单成员路径为什么也走 `blend_score_series`：一个成员时 z-score 是单调变换，不改变任何排序，`TopkDropout` 与阶梯都只看排序，所以存量配置的选股结果不变；这样只有一条代码路径，不必维护两套。

各成员先各自剔非有限值再合成，与研究侧 `ensemble_preds` 读多个 pred 文件后 `mean(axis=1)` 跳过 NaN 的行为一致。

- [ ] **Step 4: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_signal_generator.py -v`
Expected: 全部 PASS（含原有单模型用例）

- [ ] **Step 5: 提交**

```bash
git add live_trading/modules/signal_generator.py tests/live_trading/test_signal_generator.py
git commit -m "feat(live): blend five-seed ensemble scores using research daily z-score path"
```

---

### Task 6: 全A 四重宇宙过滤

**Files:**
- Create: `live_trading/modules/universe_gate.py`
- Test: `tests/live_trading/test_universe_gate.py`

**Interfaces:**
- Consumes: 无
- Produces: `filter_scores(scores: pd.Series, *, signal_date: str, raw_spec: dict, project_root: Path) -> tuple[pd.Series, dict]`——入参出参都是单层 instrument 索引的分数；被剔除的票置 `NaN`（与现有 `apply_st_daily` 语义一致，下游按 NaN 判不可选）。第二个返回值是过滤统计，供日志与审计。

新建独立模块的理由：把 `sys.path` 处理、单日 MultiIndex 构造、以及 `build_keep_mask` 的调用收拢在一个可测的小文件里，`run_publish_signals.py` 保持薄。

- [ ] **Step 1: 写失败测试**

创建 `tests/live_trading/test_universe_gate.py`。这些用例不碰 Qlib 数据，只验证索引变换与转发契约（真实四项过滤在 Task 11 的 dry-run 里实测）：

```python
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from live_trading.modules import universe_gate

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_filter_scores_nans_out_excluded_names(monkeypatch):
    captured = {}

    def fake_build_keep_mask(index, spec):
        captured["index"] = index
        captured["spec"] = spec
        inst = index.get_level_values("instrument")
        return pd.Series(inst != "SZ000001", index=index)

    monkeypatch.setattr(universe_gate, "build_keep_mask", fake_build_keep_mask)

    scores = pd.Series(
        {"SH600000": 1.0, "SZ000001": 2.0, "SH600519": 3.0}, dtype=float
    )
    out, stats = universe_gate.filter_scores(
        scores,
        signal_date="2026-08-20",
        raw_spec={"min_amount": 10_000_000, "pool": "all"},
        project_root=PROJECT_ROOT,
    )

    assert not isinstance(out.index, pd.MultiIndex)
    assert list(out.index) == ["SH600000", "SZ000001", "SH600519"]
    assert np.isnan(out["SZ000001"])
    assert out["SH600000"] == 1.0 and out["SH600519"] == 3.0
    assert stats["n_raw"] == 3 and stats["n_keep"] == 2


def test_filter_scores_passes_single_day_index_and_parsed_spec(monkeypatch):
    captured = {}

    def fake_build_keep_mask(index, spec):
        captured["index"] = index
        captured["spec"] = spec
        return pd.Series(True, index=index)

    monkeypatch.setattr(universe_gate, "build_keep_mask", fake_build_keep_mask)

    universe_gate.filter_scores(
        pd.Series({"SH600000": 1.0}, dtype=float),
        signal_date="2026-08-20",
        raw_spec={
            "st_daily": "scripts/data_collector/tushare/st_daily.csv",
            "min_amount": 10_000_000,
            "min_listing_days": 60,
            "min_recent_trading_days": 60,
            "pool": "all",
        },
        project_root=PROJECT_ROOT,
    )

    index = captured["index"]
    assert index.names == ["datetime", "instrument"]
    assert index.get_level_values("datetime").unique().tolist() == [
        pd.Timestamp("2026-08-20")
    ]
    spec = captured["spec"]
    assert spec.min_amount == 10_000_000
    assert spec.min_listing_days == 60
    assert spec.min_recent_trading_days == 60
    assert spec.pool == "all"
    assert spec.st_daily.name == "st_daily.csv"


def test_filter_scores_rejects_empty_input():
    with pytest.raises(ValueError, match="empty"):
        universe_gate.filter_scores(
            pd.Series(dtype=float),
            signal_date="2026-08-20",
            raw_spec={"pool": "all"},
            project_root=PROJECT_ROOT,
        )


def test_filter_scores_requires_all_four_filter_items():
    with pytest.raises(ValueError, match="min_amount"):
        universe_gate.filter_scores(
            pd.Series({"SH600000": 1.0}, dtype=float),
            signal_date="2026-08-20",
            raw_spec={"pool": "all", "st_daily": "x.csv"},
            project_root=PROJECT_ROOT,
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_universe_gate.py -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'live_trading.modules.universe_gate'`

- [ ] **Step 3: 实现**

创建 `live_trading/modules/universe_gate.py`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_universe_gate.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add live_trading/modules/universe_gate.py tests/live_trading/test_universe_gate.py
git commit -m "feat(live): reuse backtest four-item universe filter for live signal date"
```

---

### Task 7: 卖单整百约束下移到下单器

**Files:**
- Modify: `live_trading/modules/signal_schema.py:182-185`
- Test: `tests/live_trading/test_signal_schema.py`

**Interfaces:**
- Consumes: 无
- Produces: `validate_order` 对 SELL 只要求 `quantity` 为正整数，不再要求整百；`TRADE_UNIT` 常量保留（别处仍在用）

为什么必须改：真阶梯的 `_pending` 残量在 `absorb_broker_excess` 吸收送股之后必然出现非整百股数（400 股按 3:10 送股得 120 股）。A 股规则是「不足一手只能一次性全部卖出」，schema 层看不到持仓、判不了这一条，它现在执行的是一个对阶梯而言错误的近似，会让发布脚本直接抛 `SchemaError` 卡死。真正的规则移到 Task 8 的下单器——那里能看到持仓。

- [ ] **Step 1: 写失败测试**

追加到 `tests/live_trading/test_signal_schema.py`（沿用该文件已有的订单构造 helper；若没有就按文件里现成的 `SignalOrder(...)` 写法照抄字段）：

```python
def test_sell_allows_odd_lot_for_full_position_liquidation():
    """送股 / 部分成交残量会产生非整百股数，schema 不该在这里拦。

    「不足一手只能整笔卖出」需要持仓信息才能判定，由下单器负责。
    """
    order = _sell_order(quantity=120)

    validate_order(order)  # 不抛异常


def test_sell_still_rejects_zero_and_negative_quantity():
    for bad in (0, -100):
        with pytest.raises(SchemaError, match="positive int"):
            validate_order(_sell_order(quantity=bad))


def test_sell_still_rejects_non_integer_quantity():
    with pytest.raises(SchemaError, match="positive int"):
        validate_order(_sell_order(quantity=100.5))
```

补一个该文件里的 helper（若已存在同义 helper 就复用）：

```python
def _sell_order(*, quantity):
    return SignalOrder(
        client_order_id="c1",
        stock_code="SH600000",
        instrument_qlib="SH600000",
        side="SELL",
        quantity=quantity,
        target_value=0.0,
        price_type="AFTER_HOURS_CLOSE",
        reason="cohort_due",
    )
```

`SignalOrder` 的确切字段名与必填项以 `live_trading/modules/signal_schema.py` 里的 dataclass 定义为准，实施时照抄，不要凭记忆填。

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_signal_schema.py -k odd_lot -v`
Expected: FAIL，报 `SchemaError: SELL quantity must be multiple of 100: 120`

- [ ] **Step 3: 删掉 schema 里的整百约束**

在 `live_trading/modules/signal_schema.py` 里删除这一段：

```python
        if order.quantity % TRADE_UNIT != 0:
            raise SchemaError(
                f"SELL quantity must be multiple of {TRADE_UNIT}: {order.quantity}"
            )
```

并在紧邻的 SELL 校验分支上方加一行说明：

```python
        # 整百约束需要持仓信息（不足一手只能整笔卖出），由下单器判定
```

`TRADE_UNIT` 常量与 `max_quantity % TRADE_UNIT` 的校验都保留不动——`max_quantity` 是买入上限，整百仍然成立。

- [ ] **Step 4: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_signal_schema.py -v`
Expected: 全部 PASS。若有存量用例断言 SELL 非整百会被拒，把它改成断言下单器行为并在提交信息里说明。

- [ ] **Step 5: 确认下游未受影响**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add live_trading/modules/signal_schema.py tests/live_trading/test_signal_schema.py
git commit -m "fix(live): move odd-lot sell rule from schema to position-aware sizing"
```

---

### Task 8: 阶梯下单器（含预估卖出所得的预算）

**Files:**
- Create: `live_trading/modules/cohort_order_manager.py`
- Test: `tests/live_trading/test_cohort_order_manager.py`

**Interfaces:**
- Consumes: Task 3 的 `CohortState` / `state_to_ledger`
- Produces:
  - `CohortOrderManager(config: dict)`，读 `strategy.topk`、`strategy.horizon`、`strategy.risk_degree`、`exchange.trade_unit`、`fees`
  - `CohortOrderManager.generate_orders(self, scores, cohort_state, broker_positions, cash, close_prices, total_value) -> list[dict]`
  - 意图 dict 形状与现有 `OrderManager.generate_orders` 一致（`{"side", "stock_code", "quantity", "target_value", "reason"}`），以便复用 `OrderPlanner`

三条必须落到测试里的规则：

1. **买清单**：`select_ladder_buys(scores, k=topk, is_buyable=None)`。发布期**不做可买过滤**——T 日 16:00 无从判断 T+1 的封板/停牌，且已决定不顺延（spec 4.7）。故意不去重：连续上榜就自动加仓。
2. **预算含预估卖出所得**（spec 4.7.2）：`cash = 快照现金 + Σ(S_i × P_i − 卖出费用)`，`P_i` 取 T 日未复权收盘价，费用用 `order_total_fee("SELL", S_i × P_i, fees)`。若用快照现金，发布的 `target_value` 本身只有 1/h 暴露的一小部分，即便卖单成交后现金回补，bridge 也只会按 `target_value` 买那么多，稳态欠配约 7%。
3. **奇数股卖单**：`quantity` 非整百时，只有等于该票券商全部持仓才合法（A 股「不足一手整笔卖出」），否则向下取整到整百。

- [ ] **Step 1: 写失败测试**

创建 `tests/live_trading/test_cohort_order_manager.py`：

```python
import pandas as pd
import pytest

from live_trading.modules.cohort_order_manager import CohortOrderManager
from live_trading.modules.cohort_store import CohortState

CONFIG = {
    "strategy": {
        "class": "CohortLadderStrategy",
        "topk": 3,
        "horizon": 5,
        "risk_degree": 0.90,
    },
    "exchange": {"trade_unit": 100},
    "fees": {
        "commission_rate": 0.00020,
        "min_commission": 5.0,
        "stamp_duty_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
        "dividend_tax_rate": 0.20,
    },
}


def _scores(mapping):
    return pd.Series(mapping, dtype=float)


def test_buys_top_k_without_dedup_against_existing_layers():
    # SH600000 已被两层持有，仍应再次入选（连续上榜自动加仓）
    state = CohortState(
        layers=(
            ("2026-08-18", {"SH600000": 100}),
            ("2026-08-19", {"SH600000": 100}),
        ),
        pending={},
    )
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SH600000": 3.0, "SZ000001": 2.0, "SH600519": 1.0, "SH601318": 0.5}),
        cohort_state=state,
        broker_positions={"SH600000": 200},
        cash=1_000_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0, "SH600519": 30.0, "SH601318": 5.0},
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    assert [o["stock_code"] for o in buys] == ["SH600000", "SZ000001", "SH600519"]


def test_each_buy_carries_one_third_of_the_daily_layer_budget():
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SH600000": 3.0, "SZ000001": 2.0, "SH600519": 1.0}),
        cohort_state=CohortState(),
        broker_positions={},
        cash=1_000_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0, "SH600519": 30.0},
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    # 预算 = 1_000_000 × 0.90 / 5 = 180_000，三等分 = 60_000
    assert len(buys) == 3
    for order in buys:
        assert order["quantity"] == 0          # BUY 由券商按 target_value 定量
        assert order["target_value"] == pytest.approx(60_000.0)


def test_budget_includes_estimated_sell_proceeds():
    # 到期层 1000 股 @ 20 元 = 20_000 元毛收入
    state = CohortState(
        layers=tuple(
            (f"2026-08-1{i}", {"SZ000001": 1000} if i == 0 else {}) for i in range(5)
        ),
        pending={},
    )
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SH600000": 3.0, "SH600519": 2.0, "SH601318": 1.0}),
        cohort_state=state,
        broker_positions={"SZ000001": 1000},
        cash=100_000.0,
        close_prices={
            "SZ000001": 20.0, "SH600000": 10.0, "SH600519": 30.0, "SH601318": 5.0,
        },
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    # 目标 180_000 > 快照现金 100_000；加上卖出所得后现金约 119_986 元，
    # 预算 = min(180_000, 119_986) 被现金卡住，故必须显著高于 100_000/3
    assert sum(o["target_value"] for o in buys) > 119_000
    assert sum(o["target_value"] for o in buys) < 120_000


def test_budget_without_due_layer_falls_back_to_snapshot_cash():
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SH600000": 3.0, "SZ000001": 2.0, "SH600519": 1.0}),
        cohort_state=CohortState(),
        broker_positions={},
        cash=90_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0, "SH600519": 30.0},
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    assert sum(o["target_value"] for o in buys) == pytest.approx(90_000.0)


def test_due_layer_becomes_sell_orders_capped_by_broker_position():
    state = CohortState(
        layers=tuple(
            (f"2026-08-1{i}", {"SH600000": 500} if i == 0 else {}) for i in range(5)
        ),
        pending={},
    )
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 300},  # 券商只有 300 股
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sells = [o for o in orders if o["side"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["stock_code"] == "SH600000"
    assert sells[0]["quantity"] == 300
    assert sells[0]["target_value"] == 0.0
    assert sells[0]["reason"] == "cohort_due"


def test_pending_remnant_is_retried_as_sell():
    state = CohortState(layers=(("2026-08-19", {}),), pending={"SH600000": 200})
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 200},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sells = [o for o in orders if o["side"] == "SELL"]
    assert [(o["stock_code"], o["quantity"]) for o in sells] == [("SH600000", 200)]


def test_odd_lot_sell_allowed_only_when_it_clears_the_position():
    # 送股后 pending 有 120 股，券商也正好 120 股 → 整笔卖出合法
    state = CohortState(layers=(("2026-08-19", {}),), pending={"SH600000": 120})
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 120},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sells = [o for o in orders if o["side"] == "SELL"]
    assert [(o["stock_code"], o["quantity"]) for o in sells] == [("SH600000", 120)]


def test_odd_lot_sell_rounds_down_when_position_remains():
    # 台账要卖 120 股，但券商还有 500 股 → 只能卖整百
    state = CohortState(layers=(("2026-08-19", {}),), pending={"SH600000": 120})
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 500},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sells = [o for o in orders if o["side"] == "SELL"]
    assert [(o["stock_code"], o["quantity"]) for o in sells] == [("SH600000", 100)]


def test_sub_lot_sell_below_one_lot_is_dropped_when_position_remains():
    state = CohortState(layers=(("2026-08-19", {}),), pending={"SH600000": 40})
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 500},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    assert [o for o in orders if o["side"] == "SELL"] == []


def test_names_missing_a_close_price_are_not_buyable():
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SH600000": 3.0, "SZ000001": 2.0}),
        cohort_state=CohortState(),
        broker_positions={},
        cash=1_000_000.0,
        close_prices={"SH600000": 10.0},  # SZ000001 无价
        total_value=1_000_000.0,
    )

    buys = [o for o in orders if o["side"] == "BUY"]
    assert [o["stock_code"] for o in buys] == ["SH600000"]


def test_sells_come_before_buys():
    state = CohortState(
        layers=tuple(
            (f"2026-08-1{i}", {"SH600000": 500} if i == 0 else {}) for i in range(5)
        ),
        pending={},
    )
    manager = CohortOrderManager(CONFIG)

    orders = manager.generate_orders(
        scores=_scores({"SZ000001": 3.0}),
        cohort_state=state,
        broker_positions={"SH600000": 500},
        cash=100_000.0,
        close_prices={"SH600000": 10.0, "SZ000001": 20.0},
        total_value=1_000_000.0,
    )

    sides = [o["side"] for o in orders]
    assert sides.index("SELL") < sides.index("BUY")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_order_manager.py -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'live_trading.modules.cohort_order_manager'`

- [ ] **Step 3: 实现**

创建 `live_trading/modules/cohort_order_manager.py`：

```python
"""真阶梯（BT v4）的 Mac 侧下单意图生成。

只决定**名字与预算**，不决定股数：BUY 带 target_value、quantity=0，由 bridge 在
提交时刻用已确定的收盘价定量并与到期卖单抵销（见 spec 4.4）。这样 B 与回测的
round_amount_by_trade_unit(V / C, factor) 逐股相等，不引入动量倾斜。
"""

from __future__ import annotations

import logging
import math

import pandas as pd

from live_trading.modules.cohort_store import CohortState, state_to_ledger
from live_trading.modules.fees import fees_from_config, order_total_fee
from qlib.contrib.strategy.cohort_ladder import (
    cohort_budget,
    ledger_sell_amounts,
    select_ladder_buys,
)

logger = logging.getLogger("live_trading.cohort_orders")


class CohortOrderManager:
    """按 CohortLadderStrategy 语义生成买卖意图。"""

    def __init__(self, config: dict):
        strategy = config["strategy"]
        self.topk = int(strategy["topk"])
        self.horizon = int(strategy["horizon"])
        self.risk_degree = float(strategy["risk_degree"])
        self.trade_unit = int(config["exchange"].get("trade_unit", 100))
        self.fees = fees_from_config(config)

    def _sell_quantity(self, wanted: float, position: float) -> int:
        """不足一手只能整笔卖出；否则向下取整到一手。"""
        wanted = int(round(wanted))
        position = int(round(position))
        if wanted <= 0:
            return 0
        if wanted >= position:
            return position
        if wanted % self.trade_unit == 0:
            return wanted
        return (wanted // self.trade_unit) * self.trade_unit

    def _estimated_proceeds(self, sells: dict[str, int], close_prices: dict) -> float:
        """预估当日卖出所得（扣费）。回测预算天然含当日卖出所得，实盘必须补上。"""
        total = 0.0
        for code, quantity in sells.items():
            price = close_prices.get(code)
            if price is None or not math.isfinite(float(price)) or float(price) <= 0:
                continue
            gross = float(price) * int(quantity)
            total += gross - order_total_fee("SELL", gross, self.fees)
        return total

    def generate_orders(
        self,
        scores: pd.Series,
        cohort_state: CohortState,
        broker_positions: dict,
        cash: float,
        close_prices: dict,
        total_value: float,
    ) -> list[dict]:
        ledger = state_to_ledger(cohort_state, horizon=self.horizon)
        position_amounts = {
            code: float(amount) for code, amount in broker_positions.items()
        }

        due = ledger_sell_amounts(ledger.due(), position_amounts)
        sells: dict[str, int] = {}
        for code, wanted in due.items():
            quantity = self._sell_quantity(wanted, position_amounts.get(code, 0.0))
            if quantity > 0:
                sells[code] = quantity
            else:
                logger.warning(
                    "due %s dropped: wanted=%.0f position=%.0f below one lot",
                    code, wanted, position_amounts.get(code, 0.0),
                )

        orders = [
            {
                "side": "SELL",
                "stock_code": code,
                "quantity": quantity,
                "target_value": 0.0,
                "reason": "cohort_due",
            }
            for code, quantity in sells.items()
        ]

        # 发布期不做可买过滤：T 日 16:00 无从判断 T+1 的封板/停牌，且已决定不顺延。
        # 只剔掉没有收盘价的票——那样连预算都算不了。
        priced = scores[
            [code in close_prices for code in scores.index]
        ] if len(scores) else scores
        buys = select_ladder_buys(priced, k=self.topk, is_buyable=None)

        budget = cohort_budget(
            total_value=float(total_value),
            cash=float(cash) + self._estimated_proceeds(sells, close_prices),
            risk_degree=self.risk_degree,
            horizon=self.horizon,
        )
        per_name = budget / self.topk if self.topk else 0.0
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

        logger.info(
            "cohort orders: %d sell / %d buy, budget=%.2f (per name %.2f)",
            len(sells), len(buys), budget, per_name,
        )
        return orders
```

`per_name = budget / topk`（不是 `budget / len(buys)`）是刻意的：与回测 `_orders_for_names` 的三等分一致。凑不满 3 只时剩余预算就是不投，不摊到其他票上。

`priced` 那一行用列表推导保序过滤，避免 `scores.index.isin(close_prices)` 在 `close_prices` 为 dict 时的歧义。

- [ ] **Step 4: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_order_manager.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add live_trading/modules/cohort_order_manager.py tests/live_trading/test_cohort_order_manager.py
git commit -m "feat(live): generate cohort ladder intents with sell-proceeds-aware budget"
```

---

### Task 9: 导出精简后的五种子 artifact

**Files:**
- Create: `live_trading/scripts/export_live_model.py`
- Test: `tests/live_trading/test_export_live_model.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `slim_model(model) -> list[str]`，把训练态属性置 `None`，返回被清掉的属性名
  - `export(src: Path, dst: Path) -> dict`，导出精简 artifact 并返回 `{"sha256", "src_bytes", "dst_bytes", "cleared"}`
  - 产物 `live_trading/models/v4_rankices/s{42,1000,2000,3000,4000}/trained_model`

**为什么需要这一步。** 训练产物单个 60.1 MB，其中 `tradable_mask` 占 58.68 MB（97.6%），真正的 LightGBM booster 只有 1.35 MB。`RegimeSingleLGBMModel` **没有覆写 `predict`**，走的是 `LGBModel` 原生路径；`tradable_mask` 只在 `fit_prepared`（训练早停算 ES 指标）里被读。仓库没有 git-lfs，现有跟踪的 B6-M artifact 只有 3.2 MB，直接提交五个 60 MB 等于塞进 289 MB 的推理死重量。

源路径（已确认存在）：`backtest/result/regimeadaptfast_m0h20_rankices_s{seed}/run_01/artifacts_root/artifacts/trained_model`

**验收的核心是等价性**：精简前后对同一批特征的预测必须**逐点严格相等**（不是近似相等）。只要这条成立，精简就是纯粹的体积优化，不改变任何研究结论。

- [ ] **Step 1: 写失败测试**

创建 `tests/live_trading/test_export_live_model.py`：

```python
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from live_trading.scripts.export_live_model import export, slim_model

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRAIN_ROOT = PROJECT_ROOT / "backtest/result"
SEEDS = (42, 1000, 2000, 3000, 4000)


def _src(seed: int) -> Path:
    return (
        TRAIN_ROOT
        / f"regimeadaptfast_m0h20_rankices_s{seed}"
        / "run_01/artifacts_root/artifacts/trained_model"
    )


class _Fake:
    """替身：只验证 slim_model 清哪些属性、保留哪些。"""

    def __init__(self):
        self.model = "booster"
        self.tradable_mask = pd.Series([True, False])
        self.day_weights = pd.Series([1.0])
        self.rankic_evals_result = [{"x": 1}]
        self.protocol_id = "regime-adapt-v1"
        self.es_metric = "daily_rank_ic"
        self.params = {"lr": 0.2}


def test_slim_model_clears_training_only_attributes():
    fake = _Fake()

    cleared = slim_model(fake)

    assert set(cleared) == {"tradable_mask", "day_weights", "rankic_evals_result"}
    assert fake.tradable_mask is None
    assert fake.day_weights is None
    assert fake.rankic_evals_result is None


def test_slim_model_keeps_everything_inference_needs():
    fake = _Fake()

    slim_model(fake)

    assert fake.model == "booster"
    assert fake.protocol_id == "regime-adapt-v1"
    assert fake.es_metric == "daily_rank_ic"
    assert fake.params == {"lr": 0.2}


def test_slim_model_is_idempotent():
    fake = _Fake()

    slim_model(fake)
    cleared_again = slim_model(fake)

    assert cleared_again == []


@pytest.mark.parametrize("seed", SEEDS)
def test_source_artifact_exists(seed):
    assert _src(seed).is_file(), f"training artifact missing for seed {seed}"


@pytest.mark.parametrize("seed", SEEDS)
def test_export_shrinks_artifact_by_at_least_ten_times(tmp_path, seed):
    dst = tmp_path / f"s{seed}" / "trained_model"

    info = export(_src(seed), dst)

    assert dst.is_file()
    assert info["dst_bytes"] * 10 < info["src_bytes"], info
    assert len(info["sha256"]) == 64
    assert "tradable_mask" in info["cleared"]


@pytest.mark.parametrize("seed", SEEDS)
def test_exported_model_predicts_identically(tmp_path, seed):
    """精简是纯体积优化：同一批特征的预测必须逐点严格相等。"""
    from qlib.data.dataset.handler import DataHandlerLP

    dst = tmp_path / f"s{seed}" / "trained_model"
    export(_src(seed), dst)

    with open(_src(seed), "rb") as fh:
        original = pickle.load(fh)
    with open(dst, "rb") as fh:
        slimmed = pickle.load(fh)

    n_features = original.model.num_feature()
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        rng.standard_normal((64, n_features)),
        index=pd.MultiIndex.from_product(
            [[pd.Timestamp("2026-08-20")], [f"SH{600000 + i}" for i in range(64)]],
            names=["datetime", "instrument"],
        ),
    )

    class _DS:
        def prepare(self, segment, *, col_set, data_key):
            assert col_set == "feature" and data_key == DataHandlerLP.DK_I
            return frame

    before = original.predict(_DS(), segment="test")
    after = slimmed.predict(_DS(), segment="test")

    pd.testing.assert_series_equal(before, after, check_exact=True)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_export_live_model.py -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'live_trading.scripts.export_live_model'`

- [ ] **Step 3: 实现**

创建 `live_trading/scripts/export_live_model.py`：

```python
"""把 regime-adapt 训练产物精简成实盘推理用的 artifact。

训练产物里 tradable_mask 占 97.6% 体积（单个 58.68 MB / 60.1 MB），而
RegimeSingleLGBMModel 没有覆写 predict，走 LGBModel 原生路径，tradable_mask
只在 fit_prepared 算早停指标时被读。仓库没有 git-lfs，不能塞进 289 MB 死重量。

用法：
    python live_trading/scripts/export_live_model.py
"""

import argparse
import hashlib
import logging
import pickle
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("live_trading.export_model")

# 只在 fit_prepared / 早停时使用，推理路径不读
TRAINING_ONLY_ATTRS = ("tradable_mask", "day_weights", "rankic_evals_result")

SEEDS = (42, 1000, 2000, 3000, 4000)
SRC_TEMPLATE = (
    "backtest/result/regimeadaptfast_m0h20_rankices_s{seed}"
    "/run_01/artifacts_root/artifacts/trained_model"
)
DST_TEMPLATE = "live_trading/models/v4_rankices/s{seed}/trained_model"


def slim_model(model) -> list:
    """把训练态属性置 None，返回实际清掉的属性名。"""
    cleared = []
    for name in TRAINING_ONLY_ATTRS:
        if getattr(model, name, None) is not None:
            setattr(model, name, None)
            cleared.append(name)
    return cleared


def export(src: Path, dst: Path) -> dict:
    """读训练产物、精简、写实盘 artifact，返回体积与哈希。"""
    src = Path(src)
    dst = Path(dst)
    src_bytes = src.stat().st_size
    with open(src, "rb") as fh:
        model = pickle.load(fh)

    cleared = slim_model(model)

    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as fh:
        pickle.dump(model, fh, protocol=pickle.HIGHEST_PROTOCOL)

    payload = dst.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "src_bytes": src_bytes,
        "dst_bytes": len(payload),
        "cleared": cleared,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seeds", type=int, nargs="+", default=list(SEEDS),
        help="seeds to export (default: all five)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print(f"{'seed':>6} {'src MB':>9} {'dst MB':>9}  sha256")
    for seed in args.seeds:
        src = PROJECT_ROOT / SRC_TEMPLATE.format(seed=seed)
        dst = PROJECT_ROOT / DST_TEMPLATE.format(seed=seed)
        if not src.is_file():
            raise FileNotFoundError(f"training artifact not found: {src}")
        info = export(src, dst)
        print(
            f"{seed:>6} {info['src_bytes'] / 1e6:>9.1f} "
            f"{info['dst_bytes'] / 1e6:>9.2f}  {info['sha256']}"
        )
        logger.info("cleared %s for seed %s", info["cleared"], seed)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_export_live_model.py -v`
Expected: 全部 PASS，特别是五个 `test_exported_model_predicts_identically` —— 这条是精简合法性的唯一凭据

- [ ] **Step 5: 真正导出五个 artifact**

Run: `/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/export_live_model.py`
Expected: 五行输出，`dst MB` 各约 1.4，打印五个 sha256。**记下这五个哈希，Task 10 的配置要用。**

- [ ] **Step 6: 核对体积**

Run: `du -sh live_trading/models/v4_rankices && du -h live_trading/models/v4_rankices/s*/trained_model`
Expected: 合计约 7 MB，单个约 1.4 MB。若合计仍超过 20 MB，说明还有别的大属性没清掉，停下来重新诊断 `vars(model)` 里各属性的 `pickle.dumps` 体积，不要硬着头皮提交。

- [ ] **Step 7: 提交**

```bash
git add live_trading/scripts/export_live_model.py \
        live_trading/models/v4_rankices \
        tests/live_trading/test_export_live_model.py
git commit -m "feat(live): export inference-only v4 ensemble artifacts without training state"
```

---

### Task 10: 新配置与 strategy_id 硬编码

**Files:**
- Create: `live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml`
- Modify: `live_trading/modules/operator_probe.py`（`MAIN_STRATEGY_ID`）
- Modify: `live_trading/web/api.py`（`MAIN_REAL_STRATEGY_ID`）
- Modify: `live_trading/modules/live_config.py`（`_OPERATOR_PROBE_MAIN_STRATEGY_ID` 相关校验）
- Test: `tests/live_trading/test_live_config.py`

**Interfaces:**
- Consumes: Task 5 的 `model.members`、Task 6 的 `universe_filter`、Task 8 的 `strategy.horizon`、Task 9 导出的五个 artifact 与其 SHA-256
- Produces: 配置 id `alla_v4_ladder_k3h5_postclose_real`

本任务**不改** `qmt_signal_bridge.py` 里允许的 strategy_id 列表（计划二负责），也**不切换任何 cron**。

- [ ] **Step 1: 取回 Task 9 导出的五个哈希**

Run: `for seed in 42 1000 2000 3000 4000; do openssl dgst -sha256 "live_trading/models/v4_rankices/s${seed}/trained_model"; done`
Expected: 五行 SHA-256，与 Task 9 Step 5 打印的一致。不一致就说明 artifact 被改动过，停下来重新导出。

- [ ] **Step 2: 写失败测试**

追加到 `tests/live_trading/test_live_config.py`：

```python
def test_ladder_live_config_matches_bt_v4_parameters():
    config = load_live_config(
        REPO_ROOT / "live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml",
        REPO_ROOT,
    )

    assert config["strategy"]["class"] == "CohortLadderStrategy"
    assert config["strategy"]["topk"] == 3
    assert config["strategy"]["horizon"] == 5
    assert config["strategy"]["risk_degree"] == 0.90
    assert config["strategy"]["only_tradable"] is False
    assert config["strategy"]["forbid_all_trade_at_limit"] is False
    assert config["data"]["instruments"] == "all"
    assert config["live"]["execution_session"] == "AFTER_HOURS_FIXED_PRICE"
    assert config["live"]["strategy_id"] == "alla_v4_ladder_k3h5_postclose_real"


def test_ladder_live_config_declares_five_ensemble_members():
    config = load_live_config(
        REPO_ROOT / "live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml",
        REPO_ROOT,
    )

    members = config["model"]["members"]
    assert [m["seed"] for m in members] == [42, 1000, 2000, 3000, 4000]
    assert config["model"]["ensemble"] == "daily_zscore_mean"
    for member in members:
        assert len(member["sha256"]) == 64
        assert (REPO_ROOT / member["model_path"]).is_file()


def test_ladder_live_config_declares_all_four_universe_filters():
    config = load_live_config(
        REPO_ROOT / "live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml",
        REPO_ROOT,
    )

    spec = config["universe_filter"]
    assert spec["st_daily"] == "scripts/data_collector/tushare/st_daily.csv"
    assert spec["min_amount"] == 10_000_000
    assert spec["min_listing_days"] == 60
    assert spec["min_recent_trading_days"] == 60
    assert spec["pool"] == "all"


def test_ladder_live_config_marks_live_only_deviations():
    config = load_live_config(
        REPO_ROOT / "live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml",
        REPO_ROOT,
    )

    strategy = config["strategy"]
    assert strategy["netting"] == "live_only"
    assert strategy["absorb_broker_excess"] == "live_only"
    assert strategy["no_buyable_substitution"] == "live_only"
```

该文件顶部若没有 `REPO_ROOT` 与 `load_live_config` 的导入，照该文件现有风格补上。

- [ ] **Step 3: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_live_config.py -k ladder -v`
Expected: FAIL，报 `FileNotFoundError`

- [ ] **Step 4: 写配置**

创建 `live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml`。把 `<sha-...>` 换成 Step 1 拿到的真实哈希：

```yaml
# All-A real account: BT v4 true ladder (CohortLadderStrategy k3 x h5) on the
# v4 RankIC ES five-seed ensemble, executed via after-hours fixed price.
# Account ID is private and must come from QMT_REAL_ACCOUNT_ID.

data:
  qlib_dir: "~/.qlib/qlib_data/cn_data"
  region: "cn"
  instruments: "all"
  benchmark: "SH000985"

account:
  opening_cash: 1000000.0
  opening_value_adjustment: 0.0

provenance:
  repository: "/Users/yuxianqi/Project/qlib_exp"
  strategy_baseline_config: "backtest/configs/regime-adapt/phase-s/bt_m0h20rankices_all_ladder_k3h5_ensemble.yaml"

model:
  experiment_name: "regime-adapt/m0-h20-rankices-v1"
  baseline_ref: "v4"
  model_class: "backtest.models.regime_adapt.RegimeSingleLGBMModel"
  ensemble: "daily_zscore_mean"
  members:
    - seed: 42
      model_path: "live_trading/models/v4_rankices/s42/trained_model"
      sha256: "<sha-42>"
    - seed: 1000
      model_path: "live_trading/models/v4_rankices/s1000/trained_model"
      sha256: "<sha-1000>"
    - seed: 2000
      model_path: "live_trading/models/v4_rankices/s2000/trained_model"
      sha256: "<sha-2000>"
    - seed: 3000
      model_path: "live_trading/models/v4_rankices/s3000/trained_model"
      sha256: "<sha-3000>"
    - seed: 4000
      model_path: "live_trading/models/v4_rankices/s4000/trained_model"
      sha256: "<sha-4000>"

handler:
  class: "Alpha158Technical"
  module: "backtest.features.technical"
  start_time: "2020-02-03"
  fit_start_time: "2020-02-03"
  fit_end_time: "2020-08-03"
  infer_processors:
    - class: "ProcessInf"
  feature_groups:
    - "range"
  filter_pipe:
    - filter_type: "NameDFilter"
      name_rule_re: "^(SH60|SH68|SZ00|SZ30)"

universe_filter:
  st_daily: "scripts/data_collector/tushare/st_daily.csv"
  min_amount: 10000000
  min_listing_days: 60
  min_recent_trading_days: 60
  pool: "all"

stock_names:
  source: "tushare"

strategy:
  class: "CohortLadderStrategy"
  topk: 3
  horizon: 5
  risk_degree: 0.90
  only_tradable: false
  forbid_all_trade_at_limit: false
  # 实盘独有、回测不存在的三项偏离；parity 门禁按字段比对，缺字段即 ParityError
  netting: "live_only"
  absorb_broker_excess: "live_only"
  no_buyable_substitution: "live_only"

exchange:
  freq: "day"
  deal_price: "close"
  limit_threshold: "market_cn"
  open_cost: 0.00021
  close_cost: 0.00071
  min_cost: 5.0
  trade_unit: 100

parity:
  backtest_config: "backtest/configs/alla_v4_ladder_k3h5_parity.yaml"

live:
  bridge_root: "/Volumes/qmt_bridge"
  strategy_id: "alla_v4_ladder_k3h5_postclose_real"
  account_id: ""
  account_type: "STOCK"
  broker_environment: "REAL"
  allow_real_money: true
  default_mode: "LIVE"
  execution_session: "AFTER_HOURS_FIXED_PRICE"
  submit_after: "15:05:00"
  cancel_at: "15:28:00"
  finalize_at: "15:30:00"
  snapshot_after: "15:31:00"
  max_orders_per_day: 40

storage:
  db_path: "live_trading/data/alla_v4_ladder_k3h5_postclose_real.db"
  log_dir: "live_trading/logs/alla_v4_ladder_k3h5_postclose_real"

fees:
  commission_rate: 0.00020
  min_commission: 5.0
  stamp_duty_rate: 0.0005
  transfer_fee_rate: 0.00001
  dividend_tax_rate: 0.20

monitor:
  benchmark: "SH000985"
  benchmark_name: "中证全指"
  notify:
    channel: "serverchan"
    daily_report: true
  thresholds:
    daily_loss: -0.03
    consecutive_loss_days: 5
    reject_rate: 0.5
    cash_tolerance: 100.0
  broker_reconcile:
    cash_check: true

web:
  host: "127.0.0.1"
  port: 8082
```

`submit_after: "15:05:00"` 先与 `AFTER_HOURS_FIXED_PRICE` profile 保持一致。spec 4.7.1 的 15:00:05 自适应提交由计划二改，不在本计划范围。

`parity.backtest_config` 指向的 `backtest/configs/alla_v4_ladder_k3h5_parity.yaml` 由计划三创建；本计划只留引用，不跑 parity 门禁。

- [ ] **Step 5: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_live_config.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 改三处硬编码的主策略 ID**

在下面三处把 `csi1000_b6m_b2s_postclose_real` 换成 `alla_v4_ladder_k3h5_postclose_real`：

```bash
grep -rn "csi1000_b6m_b2s_postclose_real" \
  live_trading/modules/operator_probe.py \
  live_trading/web/api.py \
  live_trading/modules/live_config.py
```

只改这三个文件里的 `MAIN_STRATEGY_ID` / `MAIN_REAL_STRATEGY_ID` / `_OPERATOR_PROBE_MAIN_STRATEGY_ID` 常量本身。**不要**动 `PR49_PROBE_CHECKLIST.md` 里的字符串——`tests/live_trading/test_repository_boundaries.py:54-72` 断言那份清单包含 `live_trading/data/csi1000_b6m_b2s_postclose_real.db` 等 token，属于计划三的切换手册范围。

- [ ] **Step 7: 跑全量 live 测试**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -v`
Expected: 全部 PASS。若 `test_operator_probe.py` / `test_monitor_web_api.py` 里有用例硬编码了旧 strategy_id，一并改成新 id。

- [ ] **Step 8: 提交**

```bash
git add live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml \
        live_trading/models/v4_rankices \
        live_trading/modules/operator_probe.py \
        live_trading/web/api.py \
        live_trading/modules/live_config.py \
        tests/live_trading/test_live_config.py
git commit -m "feat(live): add all-A v4 ladder config and repoint main strategy id"
```

---

### Task 11: 接入发布脚本并实测全A dry-run

**Files:**
- Modify: `live_trading/scripts/run_publish_signals.py`
- Test: `tests/live_trading/test_run_publish_signals.py`

**Interfaces:**
- Consumes: Task 3 的 `reconciled_state`、Task 4 的 `load_cohort_state` / `save_cohort_state`、Task 6 的 `filter_scores`、Task 8 的 `CohortOrderManager`
- Produces: `main()` 在 `strategy.class == "CohortLadderStrategy"` 时走阶梯分支；`--audit-preview` 额外输出每只重叠票的 `S / V / B_est / net_est`

发布前顺序（spec 4.3 每日流程）：读三张表 → 重建账本 → `reconcile(券商持仓)` → `absorb_broker_excess(券商持仓)` → **写回 DB** → `due()` → 卖清单 → `select_ladder_buys` → 买清单。

reconcile 后必须写回：否则次日回执导入时 `settle` 作用在未 reconcile 的账本上，弹出的层与发布时看到的不是同一层。

- [ ] **Step 1: 写失败测试**

追加到 `tests/live_trading/test_run_publish_signals.py`（先读该文件，沿用它已有的 monkeypatch 手法与 fixture）：

```python
def test_ladder_branch_persists_reconciled_state_before_deciding(monkeypatch, tmp_path):
    """reconcile 结果必须落库，否则次日 settle 弹错层。"""
    from live_trading.modules.cohort_store import CohortState
    from live_trading.modules.fill_importer import LiveRecorder
    import live_trading.scripts.run_publish_signals as pub

    recorder = LiveRecorder(str(tmp_path / "l.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(
        CohortState(
            layers=(
                ("2026-08-19", {"SH600000": 100}),
                ("2026-08-20", {"SH600000": 200}),
            ),
            pending={},
        )
    )

    # 券商只有 100 股：最新层整单落空，reconcile 应把它削掉并写回
    state = pub.reconcile_cohort_state(
        recorder, broker_positions={"SH600000": 100}, horizon=5,
    )

    assert state.layers == (
        ("2026-08-19", {"SH600000": 100}),
        ("2026-08-20", {}),
    )
    assert recorder.load_cohort_state() == state


def test_ladder_branch_reports_absorbed_broker_excess(monkeypatch, tmp_path):
    from live_trading.modules.cohort_store import CohortState
    from live_trading.modules.fill_importer import LiveRecorder
    import live_trading.scripts.run_publish_signals as pub

    recorder = LiveRecorder(str(tmp_path / "l.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-19", {"SH600000": 400}),), pending={})
    )

    state = pub.reconcile_cohort_state(
        recorder, broker_positions={"SH600000": 520}, horizon=5,
    )

    assert state.layers == (("2026-08-19", {"SH600000": 520}),)


def test_audit_preview_estimates_netting_for_overlapping_names():
    import live_trading.scripts.run_publish_signals as pub

    rows = pub.netting_preview(
        orders=[
            {"side": "SELL", "stock_code": "SH600000", "quantity": 600,
             "target_value": 0.0, "reason": "cohort_due"},
            {"side": "BUY", "stock_code": "SH600000", "quantity": 0,
             "target_value": 60_000.0, "reason": "cohort_layer"},
            {"side": "BUY", "stock_code": "SZ000001", "quantity": 0,
             "target_value": 60_000.0, "reason": "cohort_layer"},
        ],
        close_prices={"SH600000": 100.0, "SZ000001": 20.0},
        trade_unit=100,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["stock_code"] == "SH600000"
    assert row["S"] == 600
    assert row["V"] == 60_000.0
    assert row["B_est"] == 600          # floor(60000 / 100 / 100) * 100
    assert row["net_est"] == 0
    assert row["estimate"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_run_publish_signals.py -k "ladder or audit_preview" -v`
Expected: FAIL，报 `AttributeError: module ... has no attribute 'reconcile_cohort_state'`

- [ ] **Step 3: 加两个模块级函数**

在 `live_trading/scripts/run_publish_signals.py` 的 `apply_st_daily` 之后插入：

```python
def reconcile_cohort_state(recorder, broker_positions: dict, horizon: int):
    """发布前把分层账本对齐券商持仓，并**立即写回**。

    写回是必需的：次日回执导入时 settle 必须作用在同一个已对齐的账本上，
    否则弹出的到期层与发布时看到的不是同一层。
    """
    from live_trading.modules.cohort_store import reconciled_state

    state, absorbed = reconciled_state(
        recorder.load_cohort_state(), broker_positions, horizon=horizon,
    )
    recorder.save_cohort_state(state)
    if absorbed:
        logger.warning(
            "absorbed broker share excess into ledger: %s", absorbed,
        )
    return state


def netting_preview(orders: list, close_prices: dict, trade_unit: int) -> list:
    """审计预览：估算 bridge 侧抵销结果。

    B_est 用 T 日收盘价估算，**只是估计值**——权威记录是 bridge 提交前写的
    LADDER_NET 事件，它用当日实际收盘价。本函数仅供发布前 sanity check。
    """
    sells = {
        order["stock_code"]: int(order["quantity"])
        for order in orders if order["side"] == "SELL"
    }
    rows = []
    for order in orders:
        if order["side"] != "BUY":
            continue
        code = order["stock_code"]
        if code not in sells:
            continue
        price = close_prices.get(code)
        if price is None or float(price) <= 0:
            continue
        value = float(order["target_value"])
        b_est = int(value / float(price) // trade_unit) * trade_unit
        rows.append({
            "stock_code": code,
            "S": sells[code],
            "V": value,
            "B_est": b_est,
            "net_est": b_est - sells[code],
            "estimate": True,
        })
    return rows
```

- [ ] **Step 4: 在 `main()` 里接上阶梯分支**

改 `main()`：

1. 把 `scores = apply_st_daily(scores, st_daily, signal_date)` 那一段替换为按配置分派。有 `universe_filter` 段时走四重过滤，否则保持旧的 `apply_st_daily`（存量 CSI1000 配置不受影响）：

```python
    universe_spec = config.get("universe_filter")
    if universe_spec:
        from live_trading.modules.universe_gate import filter_scores

        scores, filter_stats = filter_scores(
            scores,
            signal_date=signal_date,
            raw_spec=universe_spec,
            project_root=PROJECT_ROOT,
        )
        logger.info("universe filter stats: %s", filter_stats)
    else:
        scores = apply_st_daily(scores, st_daily, signal_date)
        banned = st_symbols_on(st_daily, signal_date)
        logger.info(
            "signal_date=%s, scored %d instruments, ST daily banned %d",
            signal_date, len(scores), len(banned),
        )
```

`build_keep_mask` 已包含同一份 `st_daily.csv` 的日频 ST 判定，所以走四重过滤时不要再叠 `apply_st_daily`，否则两处各判一次。ST 缓存落后于 signal_date 时 `build_keep_mask` 自身会抛错，fail-closed 行为不变。

2. 把 `intents = OrderManager(config).generate_orders(...)` 替换为按 `strategy.class` 分派：

```python
    if config["strategy"].get("class") == "CohortLadderStrategy":
        from live_trading.modules.cohort_order_manager import CohortOrderManager

        horizon = int(config["strategy"]["horizon"])
        broker_positions = {
            code: value["shares"] for code, value in current_positions.items()
        }
        cohort_state = reconcile_cohort_state(recorder, broker_positions, horizon)
        intents = CohortOrderManager(config).generate_orders(
            scores=scores,
            cohort_state=cohort_state,
            broker_positions=broker_positions,
            cash=cash,
            close_prices=prev_close,
            total_value=total_value,
        )
    else:
        from live_trading.modules.order_manager import OrderManager

        intents = OrderManager(config).generate_orders(
            scores,
            current_positions,
            cash,
            prev_close,
            total_value,
            signal_date=signal_date,
            trade_dates=trade_dates,
        )
```

`preview_only`（`--dry-run` / `--audit-preview`）为真时**不要**调 `reconcile_cohort_state` 的写回。把写回收在一个条件里：

```python
        cohort_state = (
            reconcile_cohort_state(recorder, broker_positions, horizon)
            if not preview_only
            else reconciled_state(
                recorder.load_cohort_state(), broker_positions, horizon=horizon,
            )[0]
        )
```

（顶部相应 `from live_trading.modules.cohort_store import reconciled_state`。）

3. 在 `--audit-preview` 的输出里加抵销预览。找到现有写审计文件的位置，把 `netting_preview(intents, prev_close, config["exchange"]["trade_unit"])` 的结果作为一个 `netting_estimate` 键并入，字段里 `estimate: True` 必须保留——审计报告要能一眼看出这是估计值而非权威记录。

- [ ] **Step 5: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_run_publish_signals.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 全A dry-run 实测墙钟时间与峰值内存**

handler 从 CSI1000（约 1000 只）扩到全A（约 5400 只），内存与耗时都会显著上升。必须实测一次再决定 16:00 发布 cron 的时间预算。

创建 `live_trading/scripts/measure_publish_dry_run.sh`：

```bash
#!/usr/bin/env bash
# 实测全A dry-run 的墙钟时间与峰值内存（macOS 下 /usr/bin/time -l 给 maximum resident set size）
set -euo pipefail
cd "$(dirname "$0")/../.."
/usr/bin/time -l /opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_publish_signals.py \
  --config alla_v4_ladder_k3h5_postclose_real \
  --dry-run 2>&1 | tail -40
```

Run: `chmod +x live_trading/scripts/measure_publish_dry_run.sh && ./live_trading/scripts/measure_publish_dry_run.sh`

不要用 heredoc 或 stdin 跑这个——会触发 Qlib 并行取数，在 macOS 下挂死（见 Global Constraints）。

Expected: 脚本跑完并打印 `real` 墙钟秒数与 `maximum resident set size`。把两个数字记到 spec 的 4.2「成本提示」小节。判定标准：墙钟 > 30 分钟或峰值内存超过本机可用内存的 60%，就要改成预构建特征帧缓存，并在提交信息里标注该结论。

- [ ] **Step 7: 人工核对一次 dry-run 输出**

检查 Step 6 的输出满足下面全部条件，任何一条不满足都不要进入下一步：

- 过滤后 `n_keep` 在 2000–4500 之间（全A 约 5400 只，剔掉 ST / 低成交额 / 新股 / 长期停牌后的合理区间）
- 恰好 3 个 BUY 意图，每个 `quantity == 0` 且 `target_value` 相等
- `target_value` 之和约等于 `总资产 × 0.90 / 5`（首日无到期层，预算应等于 `min(目标, 快照现金)`）
- SELL 意图数量与 `cohort_layers` 里最老层的票数一致（首日为 0）
- 日志里有 `universe filter stats:` 一行且 `n_st_hits` 不为负

- [ ] **Step 8: 提交**

```bash
git add live_trading/scripts/run_publish_signals.py \
        live_trading/scripts/measure_publish_dry_run.sh \
        tests/live_trading/test_run_publish_signals.py
git commit -m "feat(live): wire cohort ladder branch into publish path with audit preview"
```

---

### Task 12: 回执导入后推进账本

**Files:**
- Create: `live_trading/modules/cohort_advance.py`
- Modify: `live_trading/scripts/run_import_fills.py`
- Test: `tests/live_trading/test_cohort_advance.py`

**Interfaces:**
- Consumes: Task 3 的 `advanced_state`、Task 4 的 `load_cohort_state` / `save_cohort_state`
- Produces:
  - `day_executions(fills: list, *, strategy_mode: str = "LIVE") -> tuple[dict[str, float], dict[str, float]]`，返回 `(sold, filled)`，按 `stock_code` 汇总当日实际成交股数
  - `advance_after_import(recorder, *, trade_date: str, horizon: int, strategy_id: str) -> CohortState | None`

**没有这一步，账本一天都不会前进**：`due()` 永远返回同一层、`add` 永远不记新层，阶梯彻底失效。spec 4.3 每日流程第 5 步就是这件事，前面十个任务只建了能力、没接上触发点。

汇总口径用 `fills.applied_qty` 而不是 `filled_qty`：`applied_qty` 是已真正计入持仓的增量（`apply_fill` 的幂等账），与券商持仓同源。只统计 `mode == "LIVE"` 且状态属于 `TERMINAL_FILL_STATUS` 的回执。

- [ ] **Step 1: 写失败测试**

创建 `tests/live_trading/test_cohort_advance.py`：

```python
import pytest

from live_trading.modules.cohort_advance import advance_after_import, day_executions
from live_trading.modules.cohort_store import CohortState
from live_trading.modules.fill_importer import LiveRecorder


def _fill(**kw):
    base = {
        "batch_id": "b1", "client_order_id": "c1", "mode": "LIVE",
        "stock_code": "SH600000", "side": "BUY", "status": "FILLED",
        "requested_qty": 100, "filled_qty": 100, "applied_qty": 100,
        "avg_price": 10.0,
    }
    base.update(kw)
    return base


def test_day_executions_splits_sides_and_sums_applied_qty():
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="BUY", stock_code="SH600000", applied_qty=300),
        _fill(client_order_id="c2", side="BUY", stock_code="SH600000", applied_qty=200),
        _fill(client_order_id="c3", side="SELL", stock_code="SZ000001", applied_qty=400),
    ])

    assert filled == {"SH600000": 500.0}
    assert sold == {"SZ000001": 400.0}


def test_day_executions_ignores_non_live_and_non_terminal_fills():
    sold, filled = day_executions([
        _fill(client_order_id="c1", mode="SIMULATE", applied_qty=100),
        _fill(client_order_id="c2", status="SUBMITTED", applied_qty=0),
        _fill(client_order_id="c3", side="BUY", stock_code="SZ000001", applied_qty=100),
    ])

    assert filled == {"SZ000001": 100.0}
    assert sold == {}


def test_day_executions_drops_zero_applied_quantities():
    sold, filled = day_executions([
        _fill(client_order_id="c1", side="BUY", status="REJECTED", applied_qty=0),
    ])

    assert filled == {}
    assert sold == {}


def test_advance_after_import_appends_layer_from_actual_fills(tmp_path, monkeypatch):
    recorder = LiveRecorder(str(tmp_path / "l.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={})
    )
    monkeypatch.setattr(
        recorder, "get_batches_by_date",
        lambda trade_date, strategy_id=None: [{"batch_id": "b1"}],
    )
    monkeypatch.setattr(
        recorder, "get_fills",
        lambda batch_id: [_fill(side="BUY", stock_code="SZ000001", applied_qty=200)],
    )

    state = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )

    assert state.layers == (
        ("2026-08-19", {"SH600000": 100}),
        ("2026-08-20", {"SZ000001": 200}),
    )
    assert recorder.load_cohort_state() == state


def test_advance_after_import_is_idempotent(tmp_path, monkeypatch):
    """回执导入可能一天跑多次，重复推进必须被拒而不是叠出第 6 层。"""
    recorder = LiveRecorder(str(tmp_path / "l.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(CohortState(layers=(), pending={}))
    monkeypatch.setattr(
        recorder, "get_batches_by_date",
        lambda trade_date, strategy_id=None: [{"batch_id": "b1"}],
    )
    monkeypatch.setattr(
        recorder, "get_fills",
        lambda batch_id: [_fill(side="BUY", stock_code="SZ000001", applied_qty=200)],
    )

    first = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )
    second = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )

    assert second is None
    assert recorder.load_cohort_state() == first


def test_advance_after_import_parks_unsold_due_amount(tmp_path, monkeypatch):
    recorder = LiveRecorder(str(tmp_path / "l.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(
        CohortState(
            layers=tuple(
                (f"2026-08-1{i}", {"SH600000": 500} if i == 0 else {})
                for i in range(5)
            ),
            pending={},
        )
    )
    monkeypatch.setattr(
        recorder, "get_batches_by_date",
        lambda trade_date, strategy_id=None: [{"batch_id": "b1"}],
    )
    # 到期 500 股只卖掉 200：停牌 / 无对手盘
    monkeypatch.setattr(
        recorder, "get_fills",
        lambda batch_id: [
            _fill(side="SELL", stock_code="SH600000", applied_qty=200),
        ],
    )

    state = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )

    assert state.pending == {"SH600000": 300}
    assert state.layers[-1] == ("2026-08-20", {})


def test_advance_after_import_records_empty_layer_when_no_batch(tmp_path, monkeypatch):
    """当天没有批次（停市 / 发布失败）也要占位，否则阶梯账龄提前一天。"""
    recorder = LiveRecorder(str(tmp_path / "l.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(
        CohortState(layers=(("2026-08-19", {"SH600000": 100}),), pending={})
    )
    monkeypatch.setattr(
        recorder, "get_batches_by_date",
        lambda trade_date, strategy_id=None: [],
    )

    state = advance_after_import(
        recorder, trade_date="2026-08-20", horizon=5, strategy_id="s",
    )

    assert state.layers[-1] == ("2026-08-20", {})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_advance.py -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'live_trading.modules.cohort_advance'`

- [ ] **Step 3: 实现**

创建 `live_trading/modules/cohort_advance.py`：

```python
"""回执导入后推进分层账本一天。

没有这一步，``due()`` 永远返回同一层、``add`` 永远不记新层，阶梯彻底失效。

汇总用 ``fills.applied_qty`` 而非 ``filled_qty``：``applied_qty`` 是已真正计入持仓的
增量（``apply_fill`` 维护的幂等账），与券商持仓同源。
"""

from __future__ import annotations

import logging

from live_trading.modules.cohort_store import CohortState, advanced_state
from live_trading.modules.signal_schema import TERMINAL_FILL_STATUS

logger = logging.getLogger("live_trading.cohort_advance")


def day_executions(
    fills: list, *, strategy_mode: str = "LIVE",
) -> tuple[dict[str, float], dict[str, float]]:
    """把当日回执汇总成 ``(sold, filled)``，按股票代码合并同侧多笔。"""
    sold: dict[str, float] = {}
    filled: dict[str, float] = {}
    for fill in fills:
        if fill.get("mode") != strategy_mode:
            continue
        if fill.get("status") not in TERMINAL_FILL_STATUS:
            continue
        quantity = float(fill.get("applied_qty") or 0)
        if quantity <= 0:
            continue
        bucket = filled if fill.get("side") == "BUY" else sold
        code = fill["stock_code"]
        bucket[code] = bucket.get(code, 0.0) + quantity
    return sold, filled


def advance_after_import(
    recorder, *, trade_date: str, horizon: int, strategy_id: str,
):
    """按当日实际成交推进账本一天并落库。

    已推进过同一天则返回 ``None``——回执导入一天可能跑多次，重复推进会让阶梯
    涨到 ``horizon + 1`` 层、后续所有到期日集体错位。当天没有批次也要记一个空层，
    否则阶梯账龄会提前一天。
    """
    state = recorder.load_cohort_state()
    if any(date == trade_date for date, _ in state.layers):
        logger.info("cohort layer for %s already recorded; skipping", trade_date)
        return None

    fills: list = []
    for batch in recorder.get_batches_by_date(trade_date, strategy_id=strategy_id):
        fills.extend(recorder.get_fills(batch["batch_id"]))
    sold, filled = day_executions(fills)

    advanced = advanced_state(
        state, horizon=horizon, trade_date=trade_date, sold=sold, filled=filled,
    )
    recorder.save_cohort_state(advanced)
    logger.info(
        "cohort ladder advanced to %s: sold=%s filled=%s pending=%s",
        trade_date, sold, filled, advanced.pending,
    )
    return advanced
```

`CohortState` 的 import 在类型上没被直接用到，但保留它让本模块的返回类型对读者自明；若 linter 报未使用就改成 `# noqa: F401` 或删掉。

- [ ] **Step 4: 接进导入脚本**

在 `live_trading/scripts/run_import_fills.py` 的 `main()` 里，`positions = recorder.get_positions()` 之前插入：

```python
    if config["strategy"].get("class") == "CohortLadderStrategy":
        from live_trading.modules.cohort_advance import advance_after_import

        trade_date = importer.latest_imported_trade_date()
        if trade_date is None:
            print("no imported trade date; cohort ladder not advanced")
        else:
            advanced = advance_after_import(
                recorder,
                trade_date=trade_date,
                horizon=int(config["strategy"]["horizon"]),
                strategy_id=strategy_id,
            )
            if advanced is None:
                print(f"cohort ladder already advanced for {trade_date}")
            else:
                print(
                    f"cohort ladder advanced to {trade_date}: "
                    f"{len(advanced.layers)} layers, "
                    f"{len(advanced.pending)} pending names"
                )
```

`importer.latest_imported_trade_date()` 不存在，需要在 `FillImporter` 上加一个薄方法。先在 `fill_importer.py` 里确认 `FillImporter` 类的位置，然后加：

```python
    def latest_imported_trade_date(self, strategy_id: str | None = None):
        """最近一个有回执的交易日；没有则返回 None。"""
        batches = self.recorder.list_batches(limit=1, strategy_id=strategy_id)
        return batches[0]["trade_date"] if batches else None
```

`FillImporter` 持有 recorder 的属性名以类里实际定义为准（可能是 `self.recorder` 或 `self._recorder`），实施时照抄，不要凭记忆。若该类没有 recorder 引用，就改成在 `run_import_fills.py` 里直接用 `recorder.list_batches(limit=1, strategy_id=strategy_id)`，不新增方法——那样更简单，优先选这条。

- [ ] **Step 5: 跑测试确认通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_cohort_advance.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 端到端跑一次「发布 → 假回执 → 推进」**

创建 `live_trading/scripts/smoke_cohort_cycle.py`，用临时 DB 走完三天循环，验证阶梯层数与到期行为。写成 `.py` 文件执行，不要用 heredoc：

```python
"""三天阶梯循环自检：层数、到期、pending 重试。不碰 Qlib 数据、不碰真实账本。"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from live_trading.modules.cohort_store import (
    CohortState,
    advanced_state,
    state_to_ledger,
)
from live_trading.modules.fill_importer import LiveRecorder

with tempfile.TemporaryDirectory() as tmp:
    recorder = LiveRecorder(str(Path(tmp) / "smoke.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(CohortState())

    # 前 5 天各买一只，阶梯逐日长到 5 层
    for day in range(1, 6):
        date = f"2026-08-{day:02d}"
        state = advanced_state(
            recorder.load_cohort_state(), horizon=5, trade_date=date,
            sold={}, filled={f"SH60000{day}": 100},
        )
        recorder.save_cohort_state(state)
        assert len(state.layers) == day, (date, len(state.layers))

    # 第 6 天：最老层 SH600001 到期
    ledger_due = state_to_ledger(recorder.load_cohort_state(), horizon=5).due()
    assert ledger_due == {"SH600001": 100.0}, ledger_due

    # 卖掉一半：剩 50 股必须挂进 pending 次日重试
    state = advanced_state(
        recorder.load_cohort_state(), horizon=5, trade_date="2026-08-06",
        sold={"SH600001": 50}, filled={"SH600006": 100},
    )
    recorder.save_cohort_state(state)
    assert len(state.layers) == 5, len(state.layers)
    assert state.pending == {"SH600001": 50}, state.pending

    # 第 7 天：pending 残量 + 新到期层一起进 due
    due = state_to_ledger(recorder.load_cohort_state(), horizon=5).due()
    assert due["SH600001"] == 50.0, due
    assert due["SH600002"] == 100.0, due

    print("cohort ladder 3-day cycle OK")
```

Run: `/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/smoke_cohort_cycle.py`
Expected: 打印 `cohort ladder 3-day cycle OK`，无 assert 失败

- [ ] **Step 7: 提交**

```bash
git add live_trading/modules/cohort_advance.py \
        live_trading/scripts/run_import_fills.py \
        live_trading/scripts/smoke_cohort_cycle.py \
        tests/live_trading/test_cohort_advance.py
git commit -m "feat(live): advance cohort ladder from actual fills after receipt import"
```

---

## 计划一完成判据

全部满足才算完成，才可以进入计划二：

- [ ] `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ tests/backtest/test_cohort_ladder.py tests/backtest/test_cohort_ladder_strategy.py -v` 全绿
- [ ] `alla_v4_ladder_k3h5_postclose_real` 的 `--dry-run` 能跑通，且 Task 11 Step 7 的五条人工核对全部满足
- [ ] `smoke_cohort_cycle.py` 打印 OK：阶梯能长到 5 层、到期层正确退出、卖不掉的残量进 `_pending` 并在次日重新进 `due`
- [ ] 全A dry-run 的墙钟时间与峰值内存已实测并写回 spec 4.2
- [ ] 旧配置 `csi1000_b6m_b2s_postclose_real` 的 cron 未被改动，仍在正常运行
- [ ] 五个精简后的模型 artifact 已 Git 跟踪（合计约 7 MB，非 289 MB），五个 SHA-256 已填进配置且与 `openssl dgst -sha256` 一致
- [ ] `test_exported_model_predicts_identically` 五个种子全绿——精简前后预测逐点严格相等，证明这只是体积优化

## 交给计划二的接口

- 信号批次里 BUY 带 `target_value`（即 spec 4.4 的 `V`）、`quantity == 0`、`reason == "cohort_layer"`；SELL 带 `quantity`（即 `S`）、`target_value == 0`、`reason == "cohort_due"`。bridge 侧抵销所需的两个量都已在批次里，**信号 schema 无需再改**。
- 奇数股 SELL 已被允许通过 schema（Task 7），bridge 侧的板块感知最低申报（科创板 ≥200 股）要自己判，不能假设收到的 `quantity` 是整百。**注意 `qmt_signal_bridge.py:1385-1387` 还镜像着一份整百校验**（`quantity % 100 != 0` → `reject("SELL quantity must be a positive whole lot")`），计划二必须同步放开，否则含零股的到期层会在 bridge 侧被整批拒收。
- `live.execution_session` 已是 `AFTER_HOURS_FIXED_PRICE`，但 `submit_after` 仍是 profile 缺省的 `15:05:00`。spec 4.7.1 的 15:00:05 自适应提交由计划二实现。
- `qmt_signal_bridge.py` 第 1488 行附近允许的 strategy_id 列表**尚未**加入 `alla_v4_ladder_k3h5_postclose_real`，计划二必须补，否则 bridge 会拒收批次。
- `PR49_PROBE_CHECKLIST.md` 与 `tests/live_trading/test_repository_boundaries.py:54-72` 仍锁着 `MAX_ORDER_QUANTITY = 100` 与旧 DB 路径两个 token，取消数量闸时要同步改测试。
- 各 `run_*_cron.sh` 与 crontab 里的 config id **仍指向旧配置**，这是刻意的：计划一全程只用 `--dry-run`，不动任何调度。切换属于计划三的切换手册。
- 账本推进（Task 12）已接在 `run_import_fills.py` 上并且幂等（同一交易日重复推进返回 `None`）。计划三加监控时可以直接用 `cohort_layers` / `cohort_pending` 两张表做每日对账，不必另建状态。
