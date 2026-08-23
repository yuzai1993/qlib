# Idea：Phase M v2-exec 可执行口径评估器

- 日期：2026-08-17
- 状态：**想法（未排期、未批准）**。本文件不构成对 `backtest/EXPERIMENT_STANDARD.md` 的修改；任何评测口径变更须用户明确批准后才写入规范。
- 触发：M0 H20 主格 top5×h5（Phase M v1）与 `top5/n_drop=1` 真实回测在 2026 出现 48pp 均值裂口
- 前置结论：裂口根因已定位为**策略缺陷**，不是评估口径缺陷（见第 1 节）

---

## 1. 背景：为什么 v2 的定位变了

2026 裂口的根因是真实策略里的**停牌股组合自锁死循环**，与评估口径无关：

1. 持仓中某只股票停牌 → 当日无行情、分数为 NaN
2. `qlib/contrib/strategy/topk_dropout.py` 的 `_rank_instruments` 用 `na_position="last"`，把 NaN 排在最后 → `select_topk_dropout` 每天都把这只停牌股选为唯一卖出目标
3. 卖单在 `qlib/contrib/strategy/signal_strategy.py:267` 被 `is_stock_tradable` 拦下 → 卖出失败
4. 买单照发 → 持仓变 6 只
5. 次日起 `position_delta = topk - len(last) = -1` → `buy_count = max(len(sell) + position_delta, 0) = 0` → 买单也空。组合彻底冻结，直到复牌

第 5 步是死锁闭环。2026 年度冻结天数（冻结/交易日）：

| | s42 | s1000 | s2000 | s3000 | s4000 |
|---|---|---|---|---|---|
| 2021 / 2022 / 2025 | 0 | 0 | 0–2 | 0 | 0 |
| 2026 | 33/139 | 67/139 | 35/139 | 67/139 | 35/139 |

2026 有两只肇事股（SZ300391、SZ300029）。踩中两只的 seed 1000/3000 冻结 48% 的交易日，正是亏损最惨的两个；只踩一只的冻结 33–35 天。**种子间的巨大离散度是「踩到几只停牌股」的随机性，不是模型分数差异。** SZ300029 为退市股，2026-04-30 起停牌，6-18 复牌当日 −92.78%。

同时暴露第二个缺陷：`calculate_topk_buy_value` 的 legacy 分支用 `cash * risk_degree / buy_count`，把账上全部现金压在一笔买单上。seed 1000 买入 SZ300029 时单只占 36% 仓位（`topk=5 + risk_degree=0.90` 应为 18%），把 −92.78% 放大成组合单日 −32.17%。

**对已有结论的影响**：Phase M v1 侧完全免疫（每天独立重选 top5，停牌股 label 为 NaN 被 `dropna` 剔除，不存在持仓锁死）。所以 Phase M v1 的数字有效，Phase S 的 2026 数字无效；全周期还能对上（净 alpha +24.0% vs alpha +21.4%）是因为 2021–2025 冻结天数近 0。

**结论**：v2 的价值不再是"修正 Phase M 的乐观偏差"，而是三件事：

1. 提供一个**廉价的可执行性体检**（不启动 qlib 回测引擎），让这类"策略层缺陷静默污染结论"的情况在几分钟内暴露，而不是靠事后 48pp 裂口反查
2. 定义**参考语义（intended semantics）**，作为修上述两个缺陷的规格书与回归 oracle
3. 量化 Phase M v1 里唯一真实存在的、非缺陷的均值偏差：**drop-1 粘性持仓造成的信号陈旧化**

---

## 2. 目标与非目标

### 目标

- 新增 `phase_m_protocol: "v2-exec"` 口径，与 v1 **并存**，独立表格、独立 registry 字段
- 在 score panel 上做单路径持仓递推，不依赖 qlib 回测引擎（分钟级完成五种子全周期）
- 支持双模式：`--semantics=reference`（参考语义）与 `--semantics=legacy-defect`（复制现有缺陷），后者用于验证 evaluator 忠实性
- 输出桥梁诊断指标，把"Phase M 好 / Phase S 差"翻译成机制量

### 非目标

- **不替换 v1 主指标**。v1 全周期与真实回测吻合良好、方差小一个量级，作为模型排序器继续保留（规范 5.1.2 不动）
- 不做任何基线晋升，不改实盘配置
- **不在本方案内修 `topk_dropout.py` / `signal_strategy.py`**。缺陷修复是独立 track；本文档只提供其规格与 oracle
- 不引入冲击成本 / 分钟撮合 / 成交量上限建模（列为后续可选项）

---

## 3. 核心设计原则：参考语义 vs 真实缺陷

这是 v2 与我此前口头方案相比最重要的修正。**一个"忠实复现真实策略"的评估器会把死锁一起复现出来，从而得出同样无效的结论。** 因此必须先把真实行为切成两类，并显式声明每一条：

| # | 真实行为 | 分类 | 参考语义下的处理 |
|---|---|---|---|
| D1 | NaN 分数（停牌）持仓被排在末位、每天被选为卖出目标 | **缺陷** | 停牌持仓移出"最差"排序，进入独立的待清理队列 |
| D2 | 卖出失败但买单照发 → 持仓 > topk | **缺陷** | 先确认卖出成交，再按空槽数发买单；持仓数恒 ≤ topk |
| D3 | 持仓 6 只导致 `buy_count = 0`，组合冻结 | **缺陷**（D1+D2 的后果） | 不可卖持仓不占用 drop 名额，剩余槽位继续轮动 |
| D4 | 新买入按 `cash * risk_degree / buy_count` 定量 | **缺陷** | 目标权重制：每只 `risk_degree / topk`，即 `calculate_topk_buy_value(staged=True)` |
| F1 | 跌停/停牌/零量时卖不掉 | **真实摩擦** | 保留。当日该持仓不可卖，`effective_n_drop` 下降 |
| F2 | 涨停/零量时买不进 | **真实摩擦** | 保留（v1 已有 `--exclude-limit-up`） |
| F3 | 持仓变 ST / 成交额不足时只能排队等 drop | **真实摩擦** | 保留。持仓继续占槽并计入收益（v1 是直接凭空剔除，属高估） |
| F4 | 调仓间权重随价格漂移，不做日度再平衡 | **真实摩擦** | 保留 |
| F5 | `risk_degree=0.90` 现金缓冲 | **真实设定** | 保留 |
| F6 | `min_cost=5`、`trade_unit=100` 取整 | **真实摩擦** | 保留（按逐笔计费实现） |

> 注：`docs/superpowers/specs/2026-08-01-softtopk-suspended-price-repair-design.md` 此前只修了停牌股的**估值**回退（`_calculate_current_stock_value` 用持仓价），没有触及选股侧的死锁。D1–D3 是选股侧的独立缺陷。

**未决项（需用户决定）**：单只权重漂移到多高时应该减仓。真实策略没有任何 cap 机制，参考语义若引入 cap（建议 `2 × risk_degree/topk = 36%`）就偏离了"只修缺陷"的边界。默认**不加 cap**，仅在诊断里输出 `max_position_weight`。

---

## 4. 架构与文件布局

复用现有纯函数，不改 v1 主路径：

| 新增/复用 | 路径 | 说明 |
|---|---|---|
| 新增 | `backtest/scripts/eval_exec_path.py` | v2 主入口：持仓递推 + 收益会计 + 指标 |
| 新增 | `backtest/scripts/build_exec_path_report.py` | v2 详细 HTML |
| 新增 | `tests/backtest/test_exec_path_eval.py` | 单元 + 回归（含死锁用例） |
| 复用 | `qlib/contrib/strategy/topk_dropout.py::select_topk_dropout` | 粘性选股，纯函数、无价格依赖 |
| 复用 | `qlib/contrib/strategy/topk_dropout.py::select_daily_topk` | 每日全量重选 = v1 语义，作阶梯对照臂 |
| 复用 | `backtest/scripts/eval_ic_multi_pool.py` 的掩码 | `entry_tradable_mask` / `amount_mask` / `_listing_age_mask` / `_load_st_symbols` |
| 复用 | `backtest/scripts/eval_ic_multi_pool.py::hac_vol` / `appraisal` | 波动与 beta/alpha 口径与 v1 一致，保证可对读 |

`select_daily_topk` 的存在让阶梯第一步几乎零成本：把 v1 的"截面 top5 标签均值"换成"`select_daily_topk` 递推 + 逐日收益"，语义等价但走的是 v2 的会计管线，可以直接量出会计口径本身贡献了多少差异。

---

## 5. 算法规范

### 5.1 输入契约

| 输入 | 定义 | 来源 |
|---|---|---|
| `pred[t, i]` | 模型分数 | 现有 session，与 v1 同一份 |
| `filter_ok[t, i]` | 三过滤：非 ST、成交额 ≥ 1000 万、上市 ≥ 60 日 | 复用 v1 掩码 |
| `buy_ok[t, i]` | t+1 可买：非涨停、非零量、非停牌 | 复用 `entry_tradable_mask` |
| `sell_ok[t, i]` | t+1 可卖：非跌停、非零量、非停牌 | **新增**，`buy_ok` 的对偶 |
| `r[t, i]` | `close_{t+2}/close_{t+1} - 1`，对齐 `deal_price=close` 的 T+1 成交 | 新增，替代 h 日累计 label |
| `px[t, i]` | 真实价 `close/factor`，用于 `trade_unit` 取整与逐笔计费 | 新增 |

`h` 这个维度在 v2 中**不存在**。真实策略只有 `(topk, n_drop, hold_thresh)`；v1 主格 `top5×h5` 对应的是 `topk=5, n_drop=1`，`k/n_drop=5` 只是平均持有期的近似。网格随之从 `k × h` 改为 `k × n_drop`：

```
k ∈ {5, 15, 50} × n_drop ∈ {1, 2, ceil(k/5), k}   # 去重；n_drop=k 等价于 select_daily_topk
```

`n_drop=k` 一格即"每日全量重选 top-k"，是 v1 选股语义在 v2 会计管线下的镜像，用作阶梯对照的锚点。

### 5.2 参考语义递推

```python
S: dict[str, float] = {}        # 持仓 -> 股数
cash = account * 1.0

for t in eval_dates:
    # 1. 分数与候选池
    s_t = pred[t].where(filter_ok[t])
    cand = s_t.where(buy_ok[t])                       # 可买候选

    # 2. 持仓切成"可卖"与"冻结"两组 —— 修 D1/D3 的关键
    frozen  = [i for i in S if not sell_ok[t][i]]     # 停牌/跌停/零量，含 NaN 分数
    sellable = [i for i in S if i not in frozen]
    topk_eff  = topk - len(frozen)
    n_drop_eff = min(n_drop, max(topk_eff, 0))

    # 3. 只在可卖持仓 + 可买候选之间做 TopkDropout 递推
    sel = select_topk_dropout(
        cand.drop(index=frozen, errors="ignore"),
        sellable, topk=topk_eff, n_drop=n_drop_eff,
    )

    # 4. 先卖后买 —— 修 D2，持仓数恒 <= topk
    for i in sel.sell:
        cash += execute_sell(S, i, px[t], fees)
    slots = topk - len(frozen) - len(sellable) + len(sel.sell)
    for i in list(sel.buy)[:max(slots, 0)]:
        target_value = total_value(S, px[t], cash) * risk_degree / topk   # 修 D4
        cash -= execute_buy(S, i, target_value, px[t], trade_unit, fees)

    # 5. 收益会计：权重漂移，不重置等权
    r_port[t] = portfolio_return(S, cash, r[t])
```

`execute_sell` / `execute_buy` 按 `open_cost=0.00021`、`close_cost=0.00071`、`min_cost=5`、`trade_unit=100` 逐笔计费取整，替代 v1 的 `ann_cost = 238 × 日换手 × 0.092%` 常数公式。

### 5.3 legacy-defect 模式

`--semantics=legacy-defect` 时跳过第 2 步的冻结拆分、第 4 步的先卖后买与目标权重制，改为直接调用 `select_topk_dropout(cand, list(S), topk, n_drop)`、卖单失败即 `continue`、买单按 `cash * risk_degree / buy_count`。该模式**唯一用途是验证 evaluator 忠实性**（第 7 节 V1），不得用于产出任何研究结论。

---

## 6. 指标定义

### 6.1 主指标（进总报告）

| 字段 | 定义 | 与 v1 的关系 |
|---|---|---|
| `exec_ann_excess` | 算术年化超额，`mean(r_port − r_bench) × 238` | 对应 v1 `ann_excess`（v1 是 `× 238/h`） |
| `exec_cagr_excess` | **几何**年化超额，净值口径 | v1 无此项。2026 波动 45–65% 下 vol drag ≈ σ²/2 ≈ 10–20pp/年 |
| `exec_ann_vol` | `hac_vol(excess, horizon=1)` = 普通年化标准差 | h=1 后不再需要 Newey-West |
| `exec_beta` / `exec_ann_alpha` | 复用 `appraisal()` | 对应 v1 `beta` / `ann_alpha` |
| `exec_sharpe` | `exec_cagr_excess / exec_ann_vol` | v1 用算术分子 |
| `exec_turnover_daily` | **实际**单边日换手 | v1 用理想换手（2026 稳定 18.3–19.0%，真实塌到 10%）→ v1 在 2026 反而**高估**成本 |
| `exec_ann_cost` | 逐笔实际费用年化 | 替代常数公式 |
| `n_days` / `partial_year` | 分年表必带 | 2026 仅 139 天；年化把累计 −43.5% 放大成 −85%，分年表应报**区间累计** |

主指标建议用 `exec_ann_alpha` 而非 `exec_ann_excess`：实测 beta 1.06–1.23，beta≡1 的硬减基准会把基准波动注入分母（`appraisal()` 的 docstring 已论证）。

### 6.2 桥梁诊断指标（本方案最高价值的新增）

| 字段 | 定义 | 回答什么问题 |
|---|---|---|
| `stale_rank_mean` / `stale_rank_p90` | 真实持仓在当日 score 截面中的平均/P90 分位，对比理想 top-k 分位 | **量化 drop-1 粘性造成的信号陈旧化** —— v1 与 v2 之间唯一真实存在的非缺陷均值偏差 |
| `sell_blocked_ratio` | `1 − len(sell_ok) / len(sel.sell)` 年度均值 | 卖出侧摩擦强度 |
| `effective_n_drop` | 实际成交卖出数的年度均值 | 与 `n_drop` 的差就是摩擦损耗 |
| `frozen_days_ratio` | 「当日无任何成交」天数占比 | **死锁探针**。参考语义下应接近 0 |
| `holding_count_violation_days` | `len(S) > topk` 的天数 | **D2 探针**。参考语义下必须恒为 0 |
| `max_position_weight` | 单只权重上限 | **D4 探针**。目标 18%，legacy 模式下会看到 36% |
| `no_sell_by_rule_days` | `sel.sell` 为空且非摩擦所致的天数 | 分离「规则不想卖」与「跌停卖不掉」。`combined` 里新候选分数低于全部持仓时 `sell` 天然为空，与涨跌停无关 |

`frozen_days_ratio` 与 `holding_count_violation_days` 是本方案的核心产出：**如果 2026 的调查一开始就有这两个数，48pp 裂口的排查会从数小时缩短到一次读表。**

### 6.3 基准统一

v1 用「当日三过滤后可交易池等权」，Phase S 回测用 `backtest/configs/regime-adapt/bench_ew_all.csv`，两者 2026 差 3.1pp、2020 差 9.0pp。v2 **主口径用池内等权**（它才是策略真实的机会集），同时输出 CSV 口径作对照列并记录差值。

---

## 7. 验证计划

| # | 验证 | 通过判据 |
|---|---|---|
| V1 | `--semantics=legacy-defect` 跑 M0 H20 五种子全周期 | 复现 2026 冻结天数 33/67/35/67/35；复现年度收益与 Phase S 回测差 ≤ 2pp。**这是 evaluator 忠实性的 oracle，必须先过** |
| V2 | `--semantics=reference` 同上 | `frozen_days_ratio ≈ 0`、`holding_count_violation_days = 0`、`max_position_weight ≤ 0.20`（无 cap 时允许漂移，仅记录） |
| V3 | 参考语义 vs Phase M v1 的年度 gap | 2021–2025 落在历史带 −15 ~ +15pp；**2026 也应落入该带** —— 这是本方案可被证伪的核心预测 |
| V4 | 单元测试：NaN 分数停牌持仓 | 断言不死锁：`sel.buy` 非空、持仓数 ≤ topk、剩余槽位继续轮动 |
| V5 | 单元测试：连续跌停持仓 | 断言 `effective_n_drop` 下降但组合继续轮动，`frozen_days_ratio` 不飙升 |
| V6 | 单元测试：仓位定量 | 前一日卖 2 买 1 的场景下，单笔买入权重 = `risk_degree/topk`，不是双份 |

V4–V6 应先在现有 `tests/backtest/test_topk_dropout_selection.py` 的风格下写成失败测试，再实现——它们同时是修 D1–D4 的回归测试。

---

## 8. 统计口径：k=5 的固有下限

v2 是单路径，会失去 v1 用 overlapping 全起点换来的精度红利。这一点无法靠指标设计弥补：

- 单路径年化波动 2026 约 45–65%，139 天 ≈ 0.58 年 → 单路径年化收益抽样标准差 ≈ 50%/√0.58 ≈ 66pp
- 实测五种子简单超额 sd 42.8pp（< 66pp，因五条路径共享同一市场，不独立）
- 五种子均值标准误 ≈ 42.8/√5 ≈ 19pp

因此：

1. **模型选型主指标留在 v1，且优先 k=15 / k=50**；v2 top5 只作可执行性验收，不作模型排序依据
2. v2 若必须用 top5，报**多起点路径集合的中位数与 P25**，而非均值。多起点靠错开起始日 1..`n_drop×k` 天或不同初始持仓构造——drop-1 的持仓状态有记忆，这些路径不会立刻收敛，能给出真实路径分布。这与规范 1.2 节 Phase S 邻域行取 P25 作稳健下界是同一思路
3. 分年表报区间累计 + `n_days` + `partial_year`，禁止对 139 天做年化后与整年并列

---

## 9. 分阶段落地

每步只记录三个数：2026 均值 / sd / 实际换手。

| 阶段 | 内容 | 验收 |
|---|---|---|
| S0 | `eval_exec_path.py` 骨架 + legacy-defect 模式 | V1 通过（忠实性 oracle） |
| S1 | 参考语义 D1–D3（冻结拆分 + 先卖后买） | V2 / V4 / V5 通过；2026 `frozen_days_ratio ≈ 0` |
| S2 | 参考语义 D4（目标权重制）+ 逐笔计费 | V6 通过；`max_position_weight` 回到 ~18% |
| S3 | 阶梯对照：`select_daily_topk`（= v1 语义）vs `select_topk_dropout`，同一会计管线 | 分离出 `stale_rank` 造成的均值差；V3 通过 |
| S4 | 报告 + registry 集成 | v2 独立表，不与 v1 混排 |

S3 是本方案的**科学产出**：同一套收益会计下，只切换选股映射，得到的差就是 drop-1 粘性的纯效应。这个数以前从未被单独量过。

---

## 10. registry 与报告集成

- registry 行标 `phase_m_protocol: "v2-exec"`，`baseline_ref` 指向 v2 自己的基线行，**不与 v1 行同表**（规范第 0 节硬约束：两套轨道禁止混比）
- 新增 `detail_report` 指向 `backtest/experiments/exec_path_report.html`
- `build_phase_m_v1_report.py` 需按 `phase_m_protocol` 过滤，确保 v2 行不漏进 v1 总报告（现有过滤逻辑已按此设计，需补测试）
- 数字全部从 eval JSON / registry `metrics` 读取，禁止手写死数字（规范 5.1.2 报告要求 3）

---

## 11. 风险与未决问题

| 风险 | 说明 | 缓解 |
|---|---|---|
| v2 沦为第二套口径、增加混比风险 | 仓库已有两套 Phase M 轨道 | 严格 protocol 隔离 + 报告过滤测试；v2 明确定位为"体检"而非"选型" |
| 参考语义的边界主观 | D/F 分类是判断，不是事实 | 第 3 节逐条显式声明；任何调整须更新本表并说明理由 |
| `sell_ok` 掩码正确性 | 跌停/停牌/零量的判定需与 qlib exchange 一致 | V1 忠实性验证会同时校验该掩码——若掩码错，冻结天数无法复现 |
| 单只权重 cap 未决 | 真实策略无 cap，加了就偏离"只修缺陷" | 默认不加，只输出 `max_position_weight`，交用户决定 |
| 复现 legacy 缺陷的代码长期留存 | 可能被误用 | `--semantics=legacy-defect` 打印显著警告，且拒绝写 registry |

**独立 track（不在本方案内，但优先级更高）**：修 `topk_dropout.py` / `signal_strategy.py` 的 D1–D4，并重跑受影响的 Phase S 结果。本文档第 3 节的参考语义表与第 7 节的 V4–V6 可直接作为该 track 的规格与回归测试。
