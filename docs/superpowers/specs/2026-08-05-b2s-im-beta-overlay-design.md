# B2-S + IM 补 β（外部 Overlay）策略实验设计

## 1. 目标与边界

把「做多中证1000股指期货（IM），把组合对 CSI1000 的 β 补到 1.0」做成可复现的
Phase S 策略实验。股票腿保持 B2-S 不变；期货腿用中金所结算价连主力收益做
**外部 overlay**，不进入 qlib 股票 Exchange，也不把 IM 伪造成与股票等价的
instrument。

若 IM 窗口内主指标优于 B2-S 对照，则晋升为新的研究策略 baseline。
**本轮不开发实盘期货信号/下单。**

本实验属于 `im_window_in_sample`（见第 4 节），不得表述为样本外检验，也不得
自动修改正式实盘配置（仍为 B1 / B1-S，除非另行部署）。

## 2. 已锁定决策

| 项 | 选择 |
|---|---|
| 方向 | 补 β（做多 IM），不是做空对冲 |
| 架构 | A：股票 `TopkDropout` 回测 + 事后 overlay |
| 候选 | 唯一：目标 β=1.0 × 滚动 60 日滞后估计 × 始终开启 × real_IM |
| 对照腿 | 同窗口 B2-S 原始（无 overlay）；不做 paper_spot |
| 选型/晋升窗口 | 仅 `2022-07-22`～`2026-07-31`（IM 上市日起） |
| 主指标 | 扣费**绝对收益**夏普（rf=0），对齐 `strategy_stability_report.html` 稳定性口径 |
| 晋升规则 | overlay 夏普 **严格大于** 同窗口 B2-S |
| 账户 | **280 万元**（使约 1 手 IM ≈ gap 0.5；50 万不足以交易 IM） |
| 实盘 | 本轮不做 |

## 3. 固定输入

### 3.1 股票腿

- 冻结模型：`backtest/models/baselines/b6-m/manifest.json`（B6 v1.0）
- 策略：`TopkDropoutStrategy(topk=30, n_drop=2, hold_thresh=20, risk_degree=0.95)`
- 池 / benchmark：CSI1000 / `SH000852`
- 费率与成交假设：与当前 B2-S / 稳定性报告一致
- 初始资金：**2,800,000**（相对既有 50 万稳定性会话上调；百分比仓位策略下日收益应与规模近似无关，但仍须用 280 万重跑一条股票回测以统一 provenance）
- 回测入口：`run_pred_backtest.py` + B2-S 等价 YAML（账户字段改为 280 万）

### 3.2 期货腿（外部数据，非股票 instrument）

- 源：中金所日结算 zip（已缓存于 `backtest/experiments/ic/cffex_daily/`）
- 连主力规则：每个交易日取成交量最大的 `IM????` 合约；隔夜持仓收益为
  **持仓合约**结算价比 `settle_t / settle_{t-1} - 1`（换月不注入价格跳空）
- 固化产物：`backtest/data/im/im_continuous_daily.csv`（由脚本从 cffex 缓存重生，
  带生成参数与内容 hash；可用现有 `im_continuous_basis.csv` 迁移）
- **禁止**用新浪 IM0 收盘拼接序列作为主结果（吃不到展期）
- **不** dump 进 `~/.qlib/qlib_data/cn_data/features` 冒充可撮合股票

### 3.3 Overlay 公式

对每个交易日 \(t\)（信息集截至 \(t-1\)）：

```text
net_t      = return_t - cost_t          # 股票腿扣费绝对收益
beta_hat_t = Cov_60(net, bench)_{t-1} / Var_60(bench)_{t-1}
gap_t      = 1.0 - beta_hat_t           # 始终开启；允许为负（β>1 时减多）
r_im_t     = IM 连主力 settle-to-settle
port_t     = net_t + gap_t * r_im_t     # 主序列：扣费绝对收益（含 IM 腿）
```

主结果使用**连续名义**（允许非整数手）。另附离散手数敏感性（第 6 节），
不参与晋升。

## 4. 评价窗口与 evaluation_mode

| 窗口 | 用途 |
|---|---|
| `2022-07-22`～`2026-07-31` | **唯一**选型与晋升比较窗口 |
| `2020-01-13`～`2022-07-21` | 不虚构 IM；不参与比较 |
| 全历史 `2020-01-13`～`2026-07-31` | 不作为本实验晋升降门 |

Registry / 报告必须标注：

```text
evaluation_mode: im_window_in_sample
im_window: [2022-07-22, 2026-07-31]
account: 2800000
```

说明：标准 Phase S 邻域选型默认 `full_history_in_sample` + 扣费超额 IR；本方向因
IM 上市约束改用 IM 窗口 + 绝对夏普。报告文案须写明「非样本外、非标准 full 邻域
选型口径」。

## 5. 指标口径（对齐稳定性报告）

日度扣费绝对收益：`port_t`（overlay）或 `net_t`（对照）。

| 指标 | 口径 |
|---|---|
| **夏普（主）** | `mean(r) / std(r) * sqrt(244)`，rf=0 |
| 扣费年化 | 与稳定性报告同一套年化定义（实现时复用其计算函数，避免两套公式） |
| 最大回撤 | 净值曲线相对峰值 |
| 卡玛 | 年化 / \|最大回撤\| |
| 年化波动 | `std(r) * sqrt(244)` |
| 超额 IR（附表） | `mean(port - bench) / std(port - bench) * sqrt(244)`，不参与晋升 |

对照与候选必须在同一 IM 窗口、同一股票回测 session 上计算。

## 6. 离散手数敏感性（不晋升）

在账户 280 万、保证金假设 12%、乘数 200 下：

```text
target_notional_t = gap_t * V_t
lots_t = round(target_notional_t / (settle_t * 200))   # 可另报 floor/ceil
```

用整数手路径重算一条 `port_discrete`，与连续名义并列表述可执行误差。
若离散路径夏普仍高于 B2-S，增强可交易性信心；若翻转，晋升仍以连续名义为准，
但结论中必须写明「整数手下手数粒度风险」。

50 万账户结论（背景，不单开实验）：1 手名义约 140 万、保证金约 17 万，50 万
无法按 gap≈0.5 交易 IM；280 万是「约 1 手对齐 gap 0.5」的研究门槛。

## 7. 实现落点

1. `backtest/scripts/build_im_continuous.py`  
   从 `cffex_daily/` 生成/校验 `backtest/data/im/im_continuous_daily.csv`。
2. `backtest/scripts/run_beta_overlay_experiment.py`  
   输入：280 万 B2-S `report_normal` + IM 连续收益；输出 JSON/CSV artifact、
   对照表、registry 行所需字段。
3. 配置：`backtest/configs/strategy-beta-overlay/b2s-im-target1-roll60_csi1000_imwindow.yaml`
   （账户 280 万；指向冻结 pred 与 overlay 参数）。
4. 测试：纯函数测 β 滞后、overlay 公式、IM 窗口切片、夏普计算；用合成序列测
   整数手 round。
5. 登记：`direction=strategy-beta-overlay`，`baseline_ref=B2-S v1.0`，
   `frozen_model_ref=B6 v1.0`；重建 Phase S 相关 HTML 时在该方向表第一行注入
   B2-S **IM 窗口**对照指标。
6. 晋升：若主指标通过，经用户确认后走类似 `promote-baseline` 路径，锚点名
   如 `baseline/b3-s-on-b6-m`（最终 slug 以实现时 registry 惯例为准），并注明
   overlay 依赖与账户 280 万。

复用优先：`analyze_b2s_beta_alpha.py` 中的 `rolling_beta` / 绝对收益汇总逻辑；
稳定性报告中的夏普/年化计算函数（若已抽出则直接 import，禁止复制粘贴分叉）。

## 8. 明确不做

- 熊市开关 / MA 择时 / paper_spot 对照腿
- 把 IM 写入 qlib `features/` 并交给股票 Exchange 撮合
- 改造原生期货交易所或保证金引擎
- 本轮实盘期货 publisher / QMT 期货下单
- 用 2020～2022-07-21 无期货区间做晋升
- 自动修改 `live_trading/configs/csi300_topk10_live.yaml`

## 9. 风险与披露

- 历史 IM 长期贴水使做多腿偏正贡献；升水时同一规则可能变成本。结论须带基差
  均值/期货相对现货年化超额附表。
- 连续名义假设与整数手有差距；280 万是最低合理研究门槛，不是资金建议。
- `risk_degree=0.95` 在 280 万下约留 5% 现金（14 万），略低于 1 手保证金
  （约 17 万）。离散敏感性与晋升后的实盘设计（未来）需单独处理保证金预留；
  本轮回测连续名义不模拟保证金挤占股票仓。

## 10. 验收清单

- [ ] IM 连续合约可由脚本从 CFFEX 缓存重生且 hash 稳定
- [ ] 280 万 B2-S 股票回测 session 可复现并归档
- [ ] overlay runner 产出 IM 窗口对照表：夏普主指标 + 附表
- [ ] registry 行含 `im_window_in_sample`、账户 280 万、artifact SHA
- [ ] 单元测试覆盖滞后 β 与 overlay 公式
- [ ] 若夏普胜出：用户确认后再晋升 baseline；否则登记失败/不晋升结论
- [ ] 无实盘代码变更
