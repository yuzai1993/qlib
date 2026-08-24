# 实盘切换到 BT v4 真阶梯 + 下单抵销 + 盘后固定价格通道 · 设计

日期：2026-08-23
状态：待实施
影响面：真实资金账户（`allow_real_money: true`）

## 1. 背景

2026-08-23 已把 **BT v4 · v4 RankIC ES 真阶梯 k3×h5** 晋升为 Phase M v1 执行层回测基线
（`baseline/phase-m-v1-bt-v4`，见 `backtest/EXPERIMENT_STANDARD.md` 第 1.6 节）。本设计把这条
基线整体搬到实盘，替换当前活动系统 `csi1000_b6m_b2s_postclose_real`，在实盘下单层新增同名
买卖抵销以降低手续费，并把执行通道从收盘集合竞价（`CLOSE_AUCTION` / 14:57 / `prType=11`）
切换到盘后固定价格交易（`AFTER_HOURS_FIXED_PRICE` / 撮合 15:05–15:30 / `prType=49`）。

### 1.1 用户已确认的决策

| 决策点 | 选择 |
|---|---|
| 替换范围 | 整体替换当前实盘系统（不是新开一路并行） |
| 老持仓处理 | 一次性清仓换血；账户当前处于一手阶段，仅零星 100 股持仓或空仓 |
| 抵销落点 | 只在实盘实现；回测不动，BT v4 基线数字不重跑 |
| 板块权限 | 科创板与创业板均已开通，实盘宇宙与 BT v4 完全一致 |
| 单笔金额闸 | 直接取消 `MAX_ORDER_QUANTITY` 数量上限，不新增金额闸 |
| 券商多于台账（送股等） | 有分层的票按各层股数等比例并入；无分层的票进 `_pending` 尽快清掉（4.3.1） |
| 执行通道 | 切到盘后固定价格交易（`prType=49`），一并纳入本次变更 |
| 定量与抵销落点 | 放在 bridge 的提交时刻用**实际收盘价**计算，消除与当日涨跌幅正相关的持仓偏差（4.4） |
| 封板票 | 照常尝试买入，接受与回测「顺延跳过」的语义差异（4.7） |
| 上线节奏 | 直接按 `risk_degree=0.90` 全量切换，不做降规模成交率探针；风险由观测与回退触发条件承担（第 8 节） |
| 提交时点 | 尽早进队：15:00:05 起试 + 收盘价终态校验，通过即提交；无终态信号则固定 15:01（4.7.1） |
| 现金时序 | Mac 预算含预估卖出所得（防欠配）+ bridge 实际现金封顶（防超买）+ 买单阶段改为卖单终态即触发（4.7.2） |
| 早盘/盘中申报 | 不做。`prType=49` 结构上盘后专用，显式限价是未验证路径（4.7.1） |

## 2. 目标与非目标

**目标**

1. 实盘信号 = BT v4 官方信号（v4 五种子日截面 z-score 等权合成）。
2. 实盘宇宙 = BT v4 宇宙（全A 四重过滤）。
3. 实盘策略 = `CohortLadderStrategy(topk=3, horizon=5)`，含同票多层与按持有天数到期退出。
4. 同名到期卖与当日买做抵销，只提交净额。
5. 执行通道切到盘后固定价格交易；买单定量与抵销在收盘后用**已确定的当日收盘价**计算，
   使实盘股数与回测 `deal_price: close` 下的股数逐股相等。
6. Live/Backtest parity 门禁继续 fail-closed，覆盖上述全部新增维度。

**非目标**

- 不改回测侧的 `CohortLadderStrategy`，不重跑 BT v4，不改动刚晋升的基线数字。
- 不引入 `force_sell_rank` / `refill_force_sell`（BT v4 未开这两个开关）。
- 不改 CSI1000 研究轨道（B6-M / B4-S）的任何定义。

## 3. 现状差距

| 维度 | 当前实盘 | BT v4 要求 | 差距 |
|---|---|---|---|
| 模型 | 单 artifact + 单 SHA（B6-M seed 4000） | 五种子合成 | 需支持模型列表与逐个校验 |
| 股票池 | `instruments: csi1000` | 全A + `^(SH60\|SH68\|SZ00\|SZ30)` | 换 handler instruments |
| 宇宙过滤 | 仅日频 ST | ST + 成交额≥1000万 + 上市≥60日 + 近60日连续成交 | 新增三项 |
| 策略 | `TopkDropoutStrategy`（B4-S） | `CohortLadderStrategy` k3×h5 | 需分层账本 |
| 持仓账本 | `positions` 每票一行、一个 `opened_trade_date` | 同票多层、各层独立账龄 | 新增分层表 |
| 抵销 | 无 | 同名到期卖 ∩ 当日买取净额 | 新增，落在 bridge 提交时刻 |
| 执行通道 | `CLOSE_AUCTION` 14:57 `prType=11` | `AFTER_HOURS_FIXED_PRICE` 撮合 15:05–15:30 `prType=49` | 换 profile；退役 pr49 探针实例 |
| 买单定量 | bridge 用 14:57 实时价估收盘价 | 用已确定的收盘价 | 精确化，消除偏差 |
| 现金时序 | 快照现金即预算上限 | 需含当日卖出所得，否则稳态欠配约 7% | Mac 预算加预估所得；买单阶段改卖单终态触发 |
| 最低申报 | 统一 100 股口径 | 科创板盘后需 ≥200 股 | 需板块感知 |
| parity | 写死 TopkDropout 字段与单模型 | 需覆盖 CohortLadder 与五模型 | 需分支 |
| 单笔上限 | `MAX_ORDER_QUANTITY = 100` | 约 6 万元/只 | 取消数量上限 |

## 4. 架构

### 4.1 部署形态

新配置 `live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml`，独立账本 DB
（`live_trading/data/alla_v4_ladder_k3h5_postclose_real.db`）与独立日志目录。

旧配置 `csi1000_b6m_b2s_postclose_real` 停止调度，账本冻结作历史，**不迁移持仓、不迁移现金记录**。
两套策略共用一个账本会让持仓对账与收益归因都失去意义。

`live.strategy_id` 变更会牵动若干处硬编码的主策略 ID，必须一并改：

- `live_trading/modules/operator_probe.py` 的 `MAIN_STRATEGY_ID`
- `live_trading/web/api.py` 的 `MAIN_REAL_STRATEGY_ID`
- `live_trading/modules/live_config.py` 的 `_OPERATOR_PROBE_MAIN_STRATEGY_ID` 相关校验
- `live_trading/qmt_strategy/qmt_signal_bridge.py` 第 1488 行附近的允许 strategy_id 列表
- 各 `run_*_cron.sh` 与 crontab 示例中的 config id

### 4.2 信号层

**模型列表**。配置的 `model` 段从单条改为成员列表：

```yaml
model:
  experiment_name: "regime-adapt/m0-h20-rankices-v1"
  baseline_ref: "v4"
  model_class: "backtest.models.regime_adapt.RegimeSingleLGBMModel"
  ensemble: "daily_zscore_mean"
  members:
    - seed: 42
      model_path: "live_trading/models/v4_rankices/s42/trained_model"
      sha256: "<导出 artifact 后由 openssl dgst -sha256 填入>"
    # seed 1000 / 2000 / 3000 / 4000 同构，共五个成员
```

五个 artifact 从训练 session `regimeadaptfast_m0h20_rankices_s{42,1000,2000,3000,4000}` 导出到
Git 跟踪目录。`RegimeSingleLGBMModel` 继承 `LGBModel`，pickle 后 `.predict` 走原生路径，
`load_model_artifact` 的现有 SHA 校验逻辑可直接复用，每个成员各校验一次。

`SignalGenerator` 保留单模型路径（`model.model_path`）以兼容存量配置；出现 `model.members`
时走多模型分支。handler 只构建一次，五个模型共享同一份特征帧，所以增量成本只是 5 次
单日截面 predict。

**合成**。复用 `backtest/scripts/ensemble_preds.py` 的 `blend_score_series`：各成员分数在
当日截面上 z-score，再等权平均。该函数按 `datetime` 分组，对单日输入退化为「该日截面
标准化后平均」，与研究口径在同一日上逐点相等，不引入前视。

**宇宙**。handler 段与 BT v4 回测配置逐字对齐：

```yaml
data:
  instruments: "all"
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
```

`ProcessInf` 无拟合状态，所以 `fit_*` 窗口不影响推理结果，保留只为与回测配置字段一致、
便于 parity 逐项比对。`start_time: 2020-02-03` 给出六年以上回看，足够 Alpha158 的最长窗口。

**过滤**。复用 `backtest/scripts/universe_filter.py` 的 `build_keep_mask`。该函数接受任意
`(datetime, instrument)` 两级索引，可以直接对 signal_date 的单日索引调用，一次拿齐四项：

```python
spec = parse_universe_filter({
    "st_daily": "scripts/data_collector/tushare/st_daily.csv",
    "min_amount": 10_000_000,
    "min_listing_days": 60,
    "min_recent_trading_days": 60,
    "pool": "all",
}, project_root=PROJECT_ROOT)
keep = build_keep_mask(single_day_index, spec)
```

发布脚本 `run_publish_signals.py` 现有的 `apply_st_daily` 被这一步取代（`build_keep_mask`
已包含同一份 `st_daily.csv` 的日频 ST 判定），避免两处各判一次。ST 缓存落后于 signal_date
时 `build_keep_mask` 自身会抛错，fail-closed 行为不变。

**成本提示（已实测）**。handler 从 CSI1000（约 1000 只）扩到全A（约 5400 只）后，一次完整
`--dry-run`（含日历校验、五种子推断、宇宙过滤、下单意图与订单行）实测**墙钟 1098 秒
（18.3 分钟）、峰值 RSS 6.63 GiB**（2026-08-24，signal_date 2026-07-30，Apple Silicon /
16 GB；其中 1075 秒花在 handler 的 `Init data`，即 98% 的时间都在建特征帧）。16:00 发布
cron 的时间预算据此定为 40 分钟（留一倍余量应对冷缓存）。峰值内存已占 16 GB 机器的 41%，
后续若特征增多需盯住这条；跑不完或换不动内存就改预构建特征帧缓存。

### 4.3 分层账本

**为什么必须新增表**。真阶梯允许同一只票被多个分层同时持有，各层账龄独立、到期日不同。
现有 `positions` 表每只票一行、只有一个 `opened_trade_date`，结构上装不下这个信息。

被否决的两个更轻方案：

- **用 `opened_trade_date` 推账龄**：同票多层会被合并成一条持仓，到期时只能全卖或全不卖。
  而「连续上榜自动加仓」正是真阶梯区别于 TopkDropout 的核心（见
  `qlib/contrib/strategy/cohort_ladder.py` 模块说明），压平后就不是 BT v4 了。
- **目标持仓差分下单**：差分天然等于净额，抵销免费。但差分同样把多层压成一个总数量，
  5 天后无法判断该退哪一层，阶梯结构丢失。

**表结构**（在 `LiveRecorder._ensure_schema` 中建表，走现有的 `CREATE TABLE IF NOT EXISTS`
加 `PRAGMA table_info` 迁移模式）：

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

`cohort_layer_dates` 单独存在，是因为**空分层也必须占位**：某天全部买单落空时
`cohort_layers` 不会有任何行，但那一层仍要占据阶梯的一个位置，否则整条阶梯的账龄提前一天、
到期日全部错位（`CohortLedger.add` 的注释已写明这点）。`seq` 在每次 `add` 时取当前最大值
加一，重建时按 `seq` 升序还原 `CohortLedger._cohorts` 的层序（索引 0 最老）。

**每日流程**：

1. 发布前：读三张表 → 重建 `CohortLedger(horizon=5)` → `reconcile(券商持仓快照)`
   → `absorb_broker_excess(券商持仓快照)`（见 4.3.1）
2. `due()` → 与实际持仓取小（`ledger_sell_amounts`）→ 到期卖清单
3. `select_ladder_buys(scores, k=3, is_buyable=None)` → 当日买清单。**发布期不做可买过滤**，
   理由见 4.7：T 日 16:00 无从判断 T+1 的封板/停牌，且已决定不顺延
4. 发布固定 3 个具名 BUY（各带 `target_value`，预算含预估卖出所得，见 4.7.2）+ 到期 SELL；
   抵销由 bridge 在提交时刻执行（见 4.4）
5. 回执导入后：`settle(实际卖出)` / `add(实际买入)` → 序列化回三张表

`CohortLedger` 直接复用回测的实现，不另写一份。它已经处理了实盘会遇到的两类账实不一致：

**到期卖不掉 → `settle`**。到期层无论卖光与否都 `pop(0)` 退出阶梯，未成交部分并入 `_pending`。
次日 `due()` 的第一行就是 `out = dict(self._pending)`，残量自动重新进入卖出清单，无限期每日
重试。这样账龄结构（`_cohorts`，层数恒定为 `horizon`）与卖不掉的残量（`_pending`，脱离账龄）
分离；若让卖不掉的层赖在 `_cohorts` 里，阶梯会涨到 6、7 层，后续所有到期日集体推后。

**买单落空 → `reconcile`**。台账多于券商时，按 `[最新层, …, 最老层, _pending]` 的顺序削减。
顺序是概率排序：最近一笔买入落空可能性最大；`_pending` 最后削，因为那部分成交已被证实过。

注意 `park_unsold` **只服务强平路径**：调用点传入的是 `extracted - sold`，而 `extracted` 来自
`force_sell_names(...)`。BT v4 的 `force_sell_rank` 为 `None`，该函数直接返回空元组，因此
`park_unsold` 在本配置下每天都是空操作，只在 `f100` / `f100r` 诊断臂上才有作用。实施时无需
为它编写验收用例，该覆盖的是 `settle` 的残量路径。

**跨进程边界**。发布（Mac，16:00）与回执导入（Mac，15:31 之后）是两个进程，中间隔着
QMT 执行。台账的权威副本始终是 SQLite；`CohortLedger` 只是单次进程内的临时视图。任何
一步失败都不写回，下次从 DB 重建并对券商快照 reconcile。

### 4.3.1 券商多于台账：`reconcile` 的单向缺口（新增，实盘特有）

`reconcile` 只处理 `surplus = 台账 − 券商 > 0`。反向（券商多于台账）会被 `continue` 跳过，
不做任何事。回测中不可能发生（策略是持仓的唯一作用者），实盘至少有四条途径会触发：

1. **送股 / 转增**：`corporate_actions` 表已有 `bonus_shares`、`stk_div` 字段，除权后券商股数增加
2. 运维在 QMT UI 手工买卖
3. 回执迟到或部分成交上报滞后
4. 配股

后果：多出来的股数对阶梯完全不可见。到期时 `ledger_sell_amounts` 取 `min(due, 实际持仓)`，
只卖台账那部分，超出量永久留在账户里、不再被任何逻辑卖出。小盘股送股不罕见，换手 44x
的策略跑数年会持续累积孤儿仓位。

**裁定策略**：分两种情况。

**该票在阶梯里已有分层** → 按各层股数**等比例并入**，随各层自然到期卖出。语义上最贴「送股
属于原仓位」：多出的股数是由这些层挣来的，就该跟着它们到期。按整股等比例分配，用最大余额
法把余数补给股数最多的层，保证并入总量与账实差完全相等。

**该票在阶梯里完全没有分层**（手工买入的票，或送股时原仓位已清空）→ 并入 `_pending`。
`_pending` 的语义正好就是「脱离账龄、每日全量进 `due`」，无需新建数据结构，尽快清掉。

**实现落点**：新增独立方法 `absorb_broker_excess(actual)`，**不改** `reconcile`。实盘路径先
`reconcile(actual)` 再 `absorb_broker_excess(actual)`；回测路径只调 `reconcile`，保持
BT v4 数字零风险。这也让新行为能被单独测试。

**parity 例外**：该行为与 netting 同属「实盘独有、回测不存在」，须在 parity 白名单里显式登记
并注明理由，不得靠 parity 字段缺省而默认放过。

### 4.4 抵销

**范围**：只抵销「今日到期卖清单 ∩ 今日 top3 买清单」这一个交集。不涉及其他任何卖出来源
（BT v4 未开 `force_sell_rank`，不存在强平）。

**执行位置**：抵销在 **QMT bridge 的提交时刻**（15:00:05–15:01，见 4.7.1）计算，不在 Mac
发布期。原因见「为什么必须在 bridge 算」。bridge 只做量的变换，**不做任何名字决策**——发布
批次里的股票集合在 T 日 16:00 就固定且经操作员逐票确认，治理链路不变。

**算法**。对交集中的票 X（`C` = 当日实际收盘价，提交时刻由 `_official_close` 取得）：

- `S` = 今日到期股数（Mac 已与实际持仓取小，随 SELL 单发布）
- `V` = X 今日的分层预算，由 Mac 随批次发布（`cohort_budget(...) / 3`）。预算划分在抵销
  **之前**完成，抵销不改变预算如何分配，只改变最终提交的订单
- `B = floor(V / C / lot) * lot`，`lot` 按板块取（见 4.7 科创板最低申报）
- `net = B - S`
  - `net > 0` → 提交 BUY，`quantity = net`（**股数单，不再是 target_value**）
  - `net < 0` → 提交 SELL，`quantity = -net`
  - `net == 0` → 不提交任何订单
- 账本：`min(S, B)` 股从旧到期层**转记**到今日层，其余按净额结果记账

**为什么必须在 bridge 算**。若在 Mac 用 T 日收盘价 `P` 估 `B`、提交 `target_value = (B−S)·P`，
bridge 再用 T+1 实际收盘价 `C` 换回股数，则最终持仓为：

```
S + (B−S)·P/C  =  V/C + S·(C−P)/C  =  N_backtest + S·(C−P)/C
```

`(C−P)/C` 就是当日涨跌幅，即**涨得越多、持仓比回测超配越多**——凭空引入一个 BT v4 里
不存在的、与当日收益正相关的动量倾斜。S=600 股、当日 +5% 时超配约 29 股。放到收盘后用
已确定的 `C` 计算，`B` 与回测的 `round_amount_by_trade_unit(V / C, factor)` 逐股相等，该
偏差归零。这是选择盘后固定价格通道的主要收益之一。

**为什么等价**。两腿都以当日收盘价成交（`prType=49` 固定按收盘价撮合），所以「卖 S 再买 B」
与「只做净额」的最终持仓完全相同，差别只是省掉 `min(S, B)` 股的往返费 0.092%。被转记的
旧股早已过 T+1，可自由卖出，不引入新的可卖性约束。

**边界情形**：

| 情形 | 处理 |
|---|---|
| `B == S` | 无订单，S 股全部转记到今日层 |
| `S > 0, B == 0`（不在今日 top3） | 正常卖 S 股，无抵销 |
| `S > 0, B == 0`（科创板 `B < 200` 被置 0） | 正常卖 S 股，买入腿 SKIPPED，该层此票缺席 |
| `B > 0, S == 0`（无到期层） | 正常买，无抵销 |
| 残余 SELL 无对手盘/15:00 仍停牌 | `settle` 把残量留在 `_pending`，次日 `due()` 自动重试；已转记部分不受影响 |
| 残余 BUY 无对手盘/未成交 | `add` 只记实际成交，层变薄；已转记的 `min(S,B)` 股是实际持有的，不受影响 |
| 同票既有到期层又有未到期层 | `S` 只统计到期层，其余层不动 |
| 收盘价取不到（`_official_close` 返回 0） | 该票整单 ERROR 回执，不猜价、不下单；层变薄 |

**残余单的语义**。抵销后两侧都是**股数单**：`B` 已用实际收盘价算定，残余 BUY 直接提交 `net` 股，
不再经 `target_value` 二次换算，因此不存在换算偏差。残余 SELL 本来就是股数单。

**审计**。因 `B` 只在提交时刻才确定，审计证据分两处：

- Mac 审计预览（`--audit-preview`）输出每只重叠票的 `S / V / B_est / net_est`，其中
  `B_est` 用 T 日收盘价估算并**显式标注为估计值**，仅供发布前 sanity check
- bridge 在提交前写结构化事件 `LADDER_NET`，含 `S / V / C / B / net / 转记股数`，这是
  抵销的**权威记录**；`signal_orders.reason` 取值 `ladder_net`

切换初期逐日人工核对 bridge 侧 `LADDER_NET` 事件，而不是核对 Mac 的估计值。

**与回测的偏离**。回测不做抵销，因此实盘手续费应始终 ≤ 回测，现金略高于回测同期。这是
单向的、可解释的偏离。parity 门禁把该项列为显式豁免并在配置中标注，监控侧若观察到实盘
费用**高于**回测同期，视为异常需排查。

### 4.5 parity 门禁

`live_trading/modules/backtest_parity.py` 的 `validate_backtest_parity` 按 `strategy.class` 分支：

- `CohortLadderStrategy`：比较 `topk`、`horizon`、`risk_degree`、`only_tradable`、
  `forbid_all_trade_at_limit`；不比较 `n_drop` / `hold_thresh` / `initial_buy_count`
- `TopkDropoutStrategy`：维持现有比较项不变（存量配置不受影响）
- 未知 class：直接抛 `ParityError`，不静默放行

模型比较从单条改为列表：成员数量、每个 `model_path` 与 `sha256` 逐一比对，顺序无关但集合
必须相等。存量单模型配置继续走原路径。

新增宇宙过滤四项进入比较（当前完全没有比对）：`st_daily` 路径、`min_amount`、
`min_listing_days`、`min_recent_trading_days`、`pool`。

`exchange.limit_threshold` 两侧统一写 `market_cn`。这只是 parity 字段——QMT 实际下单价取
券商 `get_instrument_detail` 的 `UpStopPrice` / `DownStopPrice`，科创板与创业板的 20% 限幅
天然正确，不存在硬编码 9.5% 的问题。

**执行通道**新增比较：live 配置 `execution_profile` 必须为 `AFTER_HOURS_FIXED_PRICE`、
`signal_price_type` 为 `AFTER_HOURS_CLOSE`，且对照回测配置 `exchange.deal_price` 必须为
`close`。这两者是绑定关系——盘后固定价格恒以收盘价成交，若回测改用 `vwap` / `open` 则通道
语义不再对应，必须 fail。

**已知且已批准的单向偏离**三项，两侧配置各写一个标记字段、比对相等即通过：

| 项 | live 字段 | parity 回测字段 |
|---|---|---|
| 抵销 | `strategy.netting: "live_only"` | `parity.netting: "live_only"` |
| 券商多于台账吸收（4.3.1） | `strategy.absorb_broker_excess: "live_only"` | `parity.absorb_broker_excess: "live_only"` |
| 不顺延封板/停牌票（4.7） | `strategy.no_buyable_substitution: "live_only"` | `parity.no_buyable_substitution: "live_only"` |

这三项都不得靠字段缺省而默认放过：缺字段即 `ParityError`。

新增对照回测配置 `backtest/configs/alla_v4_ladder_k3h5_parity.yaml`，内容镜像
`backtest/configs/regime-adapt/phase-s/bt_m0h20rankices_all_ladder_k3h5_ensemble.yaml`
并补齐 `parity.*` 字段。

### 4.6 风控闸

按用户决定，`qmt_signal_bridge.py` 的 `MAX_ORDER_QUANTITY = 100` 取消，不新增金额闸。

**取消后剩余的约束**，实施时需在 README 中写明：

- `cohort_budget(total_value, cash, risk_degree=0.90, horizon=5)` 把当日总买入预算限制为
  `总资产 × 0.9 / 5`，再由 `_orders_for_names` 三等分，1,000,000 账户约 6 万元/只
- 预算取 `min(目标, 可用现金)`，不会透支
- `MAX_ORDERS_PER_BATCH = 40` 仍然有效
- QMT 侧 `_max_affordable_quantity` 仍按可用资金与涨停价二次约束

**已接受的风险**：配置写错（如 `horizon` 填 1、`risk_degree` 填 9.0）不会被第二道闸拦下，
只会被 parity 门禁在字段层面拦截。parity 因此成为唯一的配置正确性防线。

### 4.7 执行通道：切到盘后固定价格交易

本次一并把执行通道从 `CLOSE_AUCTION` 切到 **`AFTER_HOURS_FIXED_PRICE`**（`prType=49`，
15:05–15:30 以当日收盘价逐笔连续撮合）。该 profile 在 `live_trading/modules/execution_profile.py`
与 `qmt_signal_bridge.py` 中均已实现，并已通过 `PR49_PROBE_CHECKLIST.md` 的真实账户一手探针。

**为什么切**。两点收益都来自「提交时当日收盘价已确定」：

1. 抵销与股数换算可用精确的 `C`，消除 4.4 里那个与当日涨跌幅正相关的持仓偏差
2. `deal_price: "close"` 的价格 parity 从「近似」变为「恒等」

**前次探针的验证边界**（必须写进 README，避免误以为已验证成交能力）。探针是单票
（`600000.SH`，主板最高流动性档）、100 股、单笔、无部分成交、无多票并发、无科创板/创业板
标的。它验证的是管路：授权 marker 语义、`SECURITY_DETAIL` 资格门禁、真实委托号观测、
回执导入、生命周期闭环、快照一致。**成交能力完全未验证。**

**标的资格**由 bridge 逐票向券商查询，不硬编码板块规则：`get_instrument_detail` 的
`IsAfterHoursTrading` / `AfterHoursTrading` / `FixedPriceTrading` 字段，取值不是 `True`
（含字段缺失导致的 `None`）即在 `passorder` 前产生 `SECURITY_ELIGIBILITY_ERROR` 并写 ERROR
回执。注意这是 fail-closed：若券商不暴露该字段，**所有**订单都会被拒，切换前必须先确认
字段在生产账户上可读。

**板块最低申报**。盘后固定价格下科创板（SH688*）单笔买入不得小于 200 股，主板/创业板为
100 股整数倍。bridge 现有检查是 100 股口径的 `target_value below one board lot`
（第 3231–3235 行），对科创板会出废单，需改为板块感知：

- 主板 / 创业板：`B = floor(V/C/100)*100`
- 科创板：同样取 100 的整数倍以保持与回测 `trade_unit=100` 一致，但 `B < 200` 时直接置 0
  并写 SKIPPED 回执

卖出侧无需特殊处理：阶梯每层到期时总是把该层全部股数一次性卖出、从不拆单，因此不会构造
出「拆出零股」的违规形态；含零股的层（送股并入后可能出现）整层一次性卖出同样合规。

**与回测的名字集合差异（已批准）**。回测的 `select_ladder_buys` 会为封板/停牌票顺延取下一名，
实盘**不顺延**：

- 封板票：按用户决定**照常尝试买入**。盘后固定价格下封板票法律上可买（成交价=收盘价=涨停价，
  买入限价不低于收盘价即有效），但对手盘极稀薄，实际多半买不成，结果是该层变薄
- 15:00 仍停牌的票：交易所规则不进入盘后固定价格交易，订单被拒，该层变薄

所以在存在封板/停牌 top3 的交易日，实盘与回测的**持仓名字集合会不同**，不只是成交结果不同。
该差异的发生频率由第 6 节的顺延频率诊断脚本量化。

抵销在现金上是正协同——被抵销的 `min(S,B)` 股既不需要卖出腿也不需要现金。

**成交率观测**。每日按单记录 `fill_ratio = 实际成交股数 / 申报股数`，分买卖两侧汇总入监控，
并在 postmarket 阶段输出。回退触发条件见第 8 节。

#### 4.7.1 提交时点：尽早进队

深交所《交易规则》（2023 修订）第三章第六节给出的三条依据：

- **3.6.7**：以收盘价为成交价，**按时间优先原则逐笔连续撮合** → 队列位置决定成交顺序，
  早申报确实更容易成交
- **3.6.2**：撮合时间 15:05–15:30，但**申报时间为 9:15–11:30、13:00–15:30**；接受申报的
  时间内未成交申报可以撤销
- **3.6.8**：9:15–15:05 的盘后申报**不纳入即时行情** → 提前申报不泄露信号

**为什么不做盘中/早盘申报**（尽管规则允许）。`prType=49` 传给 `passorder` 的价格是 `0`
（第 2733 行），限价由 QMT 服务端补——规则 3.6.3/3.6.4 要求委托指令必须含限价，QMT 只能用
收盘价去填，因此这条通道在结构上就是盘后专用。规则意义上的早申报需要**自己显式给一个合法
限价**（买入挂涨停价即可，3.6.5），那是另一条代码路径，本集成没走、探针零证据。成交率本身
已是最大未知项，不为抢队列位置去启用未验证的下单路径。

**自适应提交**（替代固定 `submit_after`）。收盘价在 15:00:00 即已确定，之后纯粹是行情传播
延迟；15:01 是代码作者选的安全余量而非实测下限（`SNAPSHOT_REFRESH_AT` 注释「once the close
is in (>= 15:01)」）。因此不固定时点，改为：

1. 15:00:05 开始尝试，`timer_start` 相应提前到 14:59:55
2. 每次尝试读 tick → 校验收盘价**已终态** → 通过即提交；不通过等 1 秒重试
3. 兜底时点 **15:01:00**：到点仍未通过终态校验，则按现行 `official_close > 0` 门禁提交

这样拿到的是理论最早值，且撮合尚未开始（15:05），仍排在所有 15:05 及之后申报者之前。

**终态信号探测顺序**（实施第一步，探测结果决定能否早于兜底时点提交）：

1. tick 的 `timetag`（QMT `get_full_tick` 通常返回 `"YYYYMMDD HH:MM:SS"`），要求 ≥ 15:00:00。
   当前 `_market_price_evidence` 收集的字段里没有它，需先探明是否存在
2. `get_instrument_detail` 中只在收盘后填充的收盘价专用字段（现有代码只读了 `PreClose` /
   `LastClose`）
3. 累计成交量相对 14:56:50 的读数跳变——**仅作辅助**：收盘竞价无成交的冷门票会误判，而那种
   情况下 14:57 的价其实就是正确收盘价，该信号区分不了「无成交」与「行情未更新」

1、2 均不存在时不实现自适应，直接固定在兜底时点 15:01。

**残余风险与其控制**。若无终态信号而停在 15:01，理论上仍可能读到 14:57 的旧价并通过
`> 0` 门禁（集合竞价期间不成交，`lastPrice` 冻结在 14:57 的值）——静默用错价格定量。按用户
决定接受该尾部风险，但必须把它变成**可检测**而非静默：

- bridge 把每单实际使用的 `C` 与读取时刻写入 `LADDER_NET` 事件
- 次日 postmarket 阶段用数据管道的权威收盘价逐单对账 `C`，任何不符即 CRIT

这条对账是接受 15:01 兜底的前提条件，不得省略。

#### 4.7.2 现金时序：一个尚未解决的 parity 缺口

**回测的预算包含当日卖出所得**。`generate_trade_decision` 里卖单在第 488 行就对 `current_temp`
inline `deal_order`，而预算在第 506 行才算：

```python
trade_val, _, _ = self.trade_exchange.deal_order(sell_order, position=current_temp)
...
budget = cohort_budget(
    total_value=decision_total_value,
    cash=current_temp.get_cash(),   # 已包含当日卖出所得
    risk_degree=self.risk_degree,
    horizon=self.horizon,
)
```

**实盘拿不到这笔钱**。Mac 用的是 T 日券商快照现金，不含 T+1 的卖出所得；而买单在提交时刻就要
被券商冻结资金，此时盘后撮合（15:05）尚未开始，卖单一股都没成交。

**后果是稳态系统性欠配**。设总资产 1，稳态下 `cohort_budget` 目标为 0.18：

- 回测：卖出 0.18 后现金 0.28，预算 = `min(0.18, 0.28)` = 0.18 ✓
- 实盘：快照现金约 0.10，预算 = `min(0.18, 0.10)` = 0.10 ✗

解不动点：设持仓 `I`、现金 `C`、`I + C = 1`，每日卖 `I/5`、买 `min(0.18, C)`，稳态买卖相等得
`C = I/5`，故 `I = 5/6 ≈ 0.833`。即**稳态持仓约 83% 而非目标 90%**，相对欠配约 7%，敞口与收益
同比例缩水。

**注意有两道现金约束，会得出同一个 83%**。Mac 侧 `cohort_budget` 的 `min` 是一道；bridge 侧的
券商现金封顶是另一道，且它才是真正起作用的那道——买单数量在提交前被实际可用现金硬性削减：

```python
quantity = _max_affordable_quantity(
    batch.remaining_cash, reservation_price, requested)   # 第 3256 行
```

`batch.remaining_cash` 来自 `_get_available_cash` → `m_dAvailable`，是券商真实可用资金，**不计
未成交的卖单**。所以 Mac 把预算算大不会导致超买，超出部分只会被削减或整单 SKIPPED
（`insufficient actual cash`）。

**裁定方案（三段，缺一不可）**：

1. **Mac 预算含预估卖出所得**，表达完整意图。
   `cash = 快照现金 + Σ S_i × P_i × (1 − 卖出费率)`，`P_i` 取 T 日未复权收盘价（发布脚本现有
   `get_prev_close`），卖出费率取配置中的佣金 + 过户费 + 印花税。
   **为什么必需**：若用快照现金，发布的 `target_value` 本身就只有 0.10 的量，即便卖单成交后
   现金涨到 0.28，bridge 也只会按 `target_value` 买 0.10。Mac 必须把意图表达成完整的 0.18，
   才能让券商现金封顶成为**唯一**约束而不是双重约束。
2. **bridge 实际现金封顶保持不变**，作为防超买的安全兜底。职责划分：Mac 防欠配，bridge 防超买。
3. **买单阶段改为「卖单终态即触发」**。现有代码算出了 `sells_done` 却没放进转阶段条件
   （第 3200–3205 行），只用于打日志，导致卖单 30 秒成交也要干等满 240 秒：

   ```python
   if not sells or wait_elapsed:          # 现状
   if not sells or sells_done or wait_elapsed:   # 应改为
   ```

**超时语义必须同时改成绝对时点**。`wait_elapsed` 现在是从 `phase_started` 起算的相对时长；
提交时点提前到 15:01 后，240 秒超时会在 15:05 触发——正好是撮合刚开始、一笔卖单都没成交的
时刻，于是买单按快照现金发出，欠配照旧。超时应从 **15:05（撮合开始）** 起算。

**一个消不掉的物理约束**。撮合 15:05 才开始，卖单在此之前不可能成交，所以买单无论如何都在
15:05 之后。这个循环依赖（买单要卖单的钱、卖单要对手盘）无解。能做的是：卖单拿最好的队列
位置、卖单一到账立刻发买单、以及被抵销的重叠票完全不需要现金因而可与卖单一同早发。

**残余风险**：卖单大面积落空时买单会被券商削减，当日层变薄。但卖单大面积落空本身已触发第 8 节
的回退条件，不需要额外的控制手段。

## 5. 切换手册

| 步骤 | 内容 | 通过标准 |
|---|---|---|
| 0 | 导出五种子 artifact，记录 SHA-256 | 五个文件就位且 SHA 与 manifest 一致 |
| 1 | 新配置 + 新 parity 配置就位 | `check_backtest_parity.py --config alla_v4_ladder_k3h5_postclose_real` 通过 |
| 2 | 全量单测通过 | 见第 6 节 |
| 3 | 选一个历史交易日跑 `--dry-run`，与 BT v4 同日回测选股逐只比对 | top3 完全一致 |
| 4 | 确认生产账户上 `get_instrument_detail` 的盘后资格字段可读 | 抽查 10 只票（含 SH688*、SZ30*）`after_hours_eligible=true` |
| 5 | T 日用旧配置（CLOSE_AUCTION）清空零星一手持仓 | 券商快照持仓为空 |
| 6 | 探测收盘价终态信号（4.7.1） | 明确 `timetag` 或收盘价专用字段是否存在，结论写入实施记录 |
| 7 | 退役 pr49 探针实例；主实例改编译为 `AFTER_HOURS_FIXED_PRICE` | `RUNTIME_CONFIG` 逐项核对 `qmt_price_type=49`、`submit_after`（自适应起点 15:00:05 或固定 15:01）、`timer_start=14:59:55`、`cancel_at=15:28:00`、`finalize_at=15:30:00`、`snapshot_after=15:31:00` |
| 8 | 取消 `MAX_ORDER_QUANTITY` | `RUNTIME_CONFIG` 中不再有 100 上限 |
| 9 | 新账本以券商现金快照起账 | `opening_cash` 与券商可用资金一致 |
| 10 | 切 cron 到新 config id | 旧 config 不再被调度 |
| 11 | T+1 起真阶梯建仓 | 第 5 个交易日集齐 5 个分层，最多 15 个仓位 |

**通道切换的两个陷阱**：

1. **双 marker 失败关闭**。同日同时存在 `LIVE_OK_` 与 `PR49_LIVE_OK_` 会令两个实例都失败关闭。
   步骤 7 退役探针实例后只保留一个授权前缀。`AFTER_HOURS_FIXED_PRICE` profile 的
   `authorization_prefix` 仍是历史命名 `PR49_LIVE_OK_`——它从此就是**生产**授权 marker 名。
   本次**不重命名**：授权机制（`New-OperatorAuthorizationMarker.ps1` + 共享锁 + 不可逆提交语义）
   不与策略切换同批变更。仅在 README 中写明该命名的含义。
2. **marker 不可逆**。最终 marker 一旦提交即为授权事实，禁止用删除 marker 声称回滚。
   回退只能通过停止实例 + 保留证据，见第 8 节。

前 5 个交易日（建仓期）每日人工核对：入场是否恒为当日 top3（允许因 4.7 的不顺延而少于 3 只
成交）、分层账龄是否严格递增、bridge 侧 `LADDER_NET` 事件的抵销算术、以及买卖两侧 `fill_ratio`。

## 6. 测试计划

新增单测：

- **账本持久化往返**：多层含同票、含空层，写入后重建与原对象等价；空层占位不丢失
- **`settle` 残量路径**：到期层停牌卖不掉时该层仍退出阶梯、残量进 `_pending`、次日 `due()`
  重新包含它；阶梯层数保持恒定
- **账本 reconcile**：券商持仓少于台账时按「最新层 → 最老层 → `_pending`」顺序削减
- **券商多于台账**（`absorb_broker_excess`）：已有分层的票按股数等比例并入、余数归最大层、
  并入总量等于账实差；无分层的票进 `_pending` 并在次日 `due()` 中出现；断言 `reconcile`
  自身行为不因此改变
- **parity 分支**：CohortLadder 配置通过；缺 `horizon` 报错；`TopkDropout` 存量配置行为不变；未知 class 抛错
- **parity 模型列表**：成员数不等、SHA 不等、顺序不同（应通过）
- **parity 宇宙过滤**：四项任一不等即报错
- **parity 执行通道**：`execution_profile` / `signal_price_type` / 回测 `deal_price` 三者绑定，
  任一不匹配即报错；三个 `live_only` 标记字段缺失即报错
- **五种子合成**：给定五组固定分数，合成结果等于 `blend_score_series` 的输出

bridge 侧单测（加入 `tests/live_trading/test_qmt_bridge_logic.py`，沿用现有
`importlib.util.spec_from_file_location` 加载方式）。抵销与定量必须实现为**不依赖
`ContextInfo` 的纯函数**，收盘价与板块作为入参传入，否则无法测：

- **抵销算术**：`B > S`、`B < S`、`B == S`、`S > 0 B == 0`、`B > 0 S == 0` 五种情形的
  提交方向、股数与转记股数
- **板块最低申报**：主板/创业板取 100 整数倍；SH688* 在 `B < 200` 时置 0 并写 SKIPPED；
  `B >= 200` 时仍为 100 整数倍
- **收盘价缺失**：`_official_close` 返回 0 时整单 ERROR，不猜价、不下单
- **精确性回归**：给定 `V` 与 `C`，`B` 等于回测 `round_amount_by_trade_unit(V / C, factor)`
  的结果（这条直接锁住 4.4 里那个偏差不会回归）
- **收盘价终态门禁**（4.7.1）：`timetag < 15:00:00` 时不提交并重试；`>= 15:00:00` 时提交；
  到兜底时点 15:01 仍未通过则按 `> 0` 门禁提交；终态字段完全不可用时走固定 15:01 路径
- **收盘价对账**：`LADDER_NET` 记录的 `C` 与权威收盘价不符时判 CRIT
- **预算含卖出所得**（4.7.2）：给定快照现金与到期卖清单，`cohort_budget` 的 `cash` 入参等于
  `快照现金 + Σ S_i × P_i × (1 − 卖出费率)`；空到期清单时退化为快照现金
- **买单阶段触发**（4.7.2）：卖单全部终态时立即转 BUY，不等满超时；卖单未终态且未到超时则
  保持 SELL 阶段；超时按**绝对时点**（15:05 起算）而非相对时长判定
- **现金封顶仍生效**：预算被高估时 `_max_affordable_quantity` 削减或 SKIPPED，不得超买

诊断脚本（一次性，不改策略）：

- **顺延频率**：给 BT v4 回测加计数器，统计 `select_ladder_buys` 每日为凑够 3 只跳过了几只
  不可买票、最深走到第几名。这个分布量化 4.7「实盘不顺延」造成的名字集合差异频率

回归：现有 `tests/live_trading/` 全套必须继续通过（存量 TopkDropout 配置不得受影响）。

## 7. 文档更新

- `live_trading/README.md`：活动系统、固定契约表、受控晋级章节全部改写到新策略；补写
  执行通道已切到盘后固定价格、`PR49_LIVE_OK_` 从此为生产授权 marker 名、前次一手探针的
  验证边界（管路已验证、成交能力未验证）
- `live_trading/qmt_strategy/PR49_PROBE_CHECKLIST.md`：标注探针已退役及其结论适用范围
- `AGENTS.md` 第 1 条：补记全A 实盘配置切到 BT v4
- `backtest/EXPERIMENT_STANDARD.md` 第 1.4 节：记录本次实盘切换已获用户批准
- `backtest/experiments/LESSONS.md`：切换完成后补一条实盘条目（含抵销实测省费）

## 8. 风险

**成交率（本次头号风险）**。盘后固定价格是在一个独立的薄池子里按时间优先逐笔撮合，需要
对手盘；而 BT v4 的回测假设以收盘价成交、深度无限。全A top-3 由强 alpha 模型选出，天然偏
小盘，盘后对手盘更稀薄。成交率若显著低于 100%，后果不是滑点而是**系统性欠配**：买不成则
层变薄且无替补，卖不成则 `_pending` 持续累积。

这一项回测测不出，前次一手探针也测不出（`600000.SH`、100 股是最容易成交的组合）。按用户
决定直接全量切换、不做降规模探针，因此**风险控制手段只剩观测与回退**：

- 每日买卖两侧加权 `fill_ratio` 入监控，postmarket 阶段输出
- **回退触发**：连续 3 个交易日买入侧加权成交率 < 80%，或任一日 < 50% → 暂停发布，评估
  切回 `CLOSE_AUCTION`。切回时 4.4 的抵销必须同时改回「Mac 估算 + 已知偏差登记」，因为
  14:57 拿不到确定的收盘价
- 回退不得通过删除授权 marker 实现（marker 是不可逆授权事实）；只能停止实例并保留全部证据

**同时变更五个维度**：模型、股票池、策略、执行通道、单笔金额（约 1–3 千元 → 约 6 万元）。
任一维度出问题都会直接作用于真实资金，五者叠加后故障归因困难。这是本设计最主要的结构性
风险，已在知情下接受。

**策略特征**：BT v4 历史最大回撤 −33.0%，2026 年至今 +3.8%，持仓中位 10 只。热门票连续
上榜可同时占据 3 层，单名义敞口约 18%。

**无实盘历史**：这套组合（全A + v4 五种子 + 真阶梯 + 盘后固定价格）在实盘一天都没有跑过。

**名字集合与回测不同**（4.7）：封板票实盘尝试买入而回测顺延跳过，停牌票实盘让层变薄而回测
顺延。`live_trading/scripts/diagnose_ladder_skip_rate.py` 已量化（测试期 2021-07-16 ~
2026-07-16 共 1211 个交易日，topk=3，可买判定复用回测自己的 `apply_market_cn_limits`）：

- 发生过顺延的交易日 **224 个（18.5%）**，累计顺延候选 **305 次**，平均每日 0.252 次
- 凑不满 top3 的交易日 **0 个**——顺延之后总能补满，所以实盘「不顺延」的后果是**换名字**
  而不是层变薄

即约每五个交易日有一天，实盘的入场名单会与回测差一只票：回测拿的是顺延后的第 4 名，实盘
拿的是那只封板/停牌的原第 1~3 名（且可能买不到）。这个差异的**收益方向仍无法预估**——封板
票次日既可能续涨也可能回落，量化频率不等于量化影响。它是建仓期人工核对的重点之一。

**旧价定量（4.7.1 兜底路径）**：若终态信号不可用而固定在 15:01，理论上仍可能读到 14:57 的
冻结价并通过 `> 0` 门禁，导致股数按错价算出。按用户决定接受该尾部风险。控制手段是次日用
权威收盘价逐单对账 `LADDER_NET` 里记录的 `C`，把静默错误转为 CRIT 告警——**该对账是接受
本风险的前提，不得省略**。

**建仓期敞口爬升**：前 5 个交易日仓位从 0 线性升到 90%，期间的组合特征与稳态不同。

## 9. 后续（不在本次范围）

- 抵销实际省费的量化：上线一个月后统计实盘费用与同期回测费用之差
- 若抵销收益显著，再评估是否值得把 netting 开关反向移植回回测并重跑 BT v4
