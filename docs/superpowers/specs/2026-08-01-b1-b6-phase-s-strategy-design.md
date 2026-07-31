# B1-M / B6-M Phase S 策略实验设计

日期：2026-08-01  
状态：用户已批准，连续执行  
实验阶段：Phase S（冻结模型，只修改策略）

## 1. 目标

在不重训模型的前提下，分别为 B1-M 与 B6-M 设计并选择适配其预测周期的策略。两组都以当前实盘 B1-S 配置为组内基线，只使用 CSI1000 valid 选型；冻结每组胜者后，才在 CSI1000、CSI300、CSI500 test 上比较基线与胜者。

本实验不比较或提升模型，不自动修改实盘配置，也不自动提升策略基线。最终是否采纳由用户根据独立策略报告决定。

## 2. 冻结模型契约

Phase S 的模型来源统一为：

- B1-M：`backtest/models/baselines/b1-m/manifest.json` 指向的单一 `trained_model`；
- B6-M：`backtest/models/baselines/b6-m/manifest.json` 指向的单一 `trained_model`。

运行前必须校验：

1. manifest 的 `baseline_exp_id` 与目录名称一致；
2. `retained_model.path` 位于对应 baseline 目录内；
3. 文件存在、大小与 `size_bytes` 一致；
4. SHA-256 与 `retained_model.sha256` 一致；
5. 预测产物记录模型路径、模型 SHA、handler 配置 SHA、预测 SHA、索引覆盖范围和数据版本。

Phase S 不得从 `mlruns/`、历史 `backtest/result/` 或 `live_trading/models/` 隐式寻找替代模型，也不做多种子集成。现行实验规范中 B6 五种子集成条款须同步改为此单一冻结 artifact 契约；Phase M 的五种子训练与评估要求不变。

## 3. 模型特征与策略假设

### 3.1 B1-M

B1-M 是 Alpha158 + LGBM 的次日收益模型，预测目标短、分数变化快。当前 Top10/Drop2/Hold1 与其短周期特征一致，但可能因持仓过度集中或日换仓比例偏高而损失扣费收益。

假设：在保持较短最低持有期的前提下，提高持仓数或降低单日替换比例，可改善扣费超额 IR；过长持有期预计会稀释一日信号，因此只测试 Hold1/Hold3。

### 3.2 B6-M

B6-M 使用 Alpha158+range、H40 累计收益标签、截面 RankNorm、DoubleEnsemble 和 valid RankIC 早停，信号对应更长持有周期且排序稳定性更强。当前 Top10/Drop2/Hold1 每日最多替换 20% 持仓，可能与 H40 目标错配并产生不必要成本。

假设：降低替换比例、将最低持有期延长到 5/10/20 日并适度分散到 Top20/Top30，可使持仓周期更贴近 H40，从而提高扣费超额 IR并控制回撤；SoftTopk 的渐进调仓可能进一步减少排序边界附近的无效往返。

## 4. 固定回测口径

| 项目 | 固定值 |
|---|---|
| 选型池 | CSI1000 |
| valid | 2020-01-13 ～ 2021-07-15 |
| test | 2021-07-16 ～ 2026-07-31 |
| test 稳健性池 | CSI1000、CSI300、CSI500 |
| benchmark | CSI1000=SH000852；CSI300=SH000300；CSI500=SH000905 |
| 初始账户 | 500,000 元 |
| 风险仓位 | 0.95 |
| 成交价 | close |
| 涨跌停阈值 | 0.095 |
| 开仓费率 | 0.00021 |
| 平仓费率 | 0.00071 |
| 最低费用 | 5 元 |
| 交易单位 | 100 股 |
| 可交易约束 | only_tradable=false；forbid_all_trade_at_limit=false |

B1-S 组内基线固定为 `TopkDropoutStrategy(topk=10, n_drop=2, hold_thresh=1, risk_degree=0.95)`，其余参数与上表一致。

## 5. valid 候选网格

### 5.1 B1-M 网格

TopkDropout 共 12 个候选：

- Top10：Drop1/Drop2 × Hold1/Hold3；
- Top20：Drop2/Drop4 × Hold1/Hold3；
- Top30：Drop3/Drop6 × Hold1/Hold3。

其中 Top10/Drop2/Hold1 是 B1-S 基线。

SoftTopk 共 6 个候选：

- Top10/Top20/Top30；
- 每个 Topk 分别使用目标单股权重的 50% 或 100% 作为 `trade_impact_limit`。

目标单股权重为 `0.95 / topk`，因此配置必须写入计算后的确定小数，不在运行时接受模糊比例。

### 5.2 B6-M 网格

低换手 TopkDropout 共 15 个候选：

- Top10/Drop1 × Hold5/Hold10/Hold20；
- Top20/Drop1、Top20/Drop2 × Hold5/Hold10/Hold20；
- Top30/Drop2、Top30/Drop3 × Hold5/Hold10/Hold20。

另加入 Top10/Drop2/Hold1 的 B1-S 基线，共 16 个 TopkDropout 配置。

SoftTopk 共 6 个候选：

- Top10/Top20/Top30；
- 每个 Topk 分别使用目标单股权重的 25% 或 50% 作为 `trade_impact_limit`。

两个模型组的候选 ID 必须稳定、唯一，且在运行前完整写入 registry 预登记记录。不得根据中途 valid 结果追加或删除候选。

## 6. 选型与 test 打开规则

每个模型组独立排序，选择一个 valid 胜者。排序键依次为：

1. CSI1000 valid `excess_return_with_cost.information_ratio`，降序；
2. CSI1000 valid 扣费超额年化，降序；
3. CSI1000 valid 扣费最大回撤，降序（数值越接近 0 越优）；
4. 年化单边换手率，升序；
5. 候选 ID，字典序升序。

不设置固定通过门槛。valid 胜者冻结后，每组只允许以下 test 回测：

- B1-S 基线 × CSI1000/CSI300/CSI500；
- 冻结胜者 × CSI1000/CSI300/CSI500。

test 不参与重新选型或二次调参。若 test 失败，只允许修复确定性的工程错误后用完全相同的冻结预测和策略配置重跑，并记录失败与重跑原因。

## 7. 指标与报告

主指标：扣费超额 IR。  
副指标：扣费超额年化、扣费最大回撤。  
诊断指标：年化单边换手率、累计交易成本、持仓数量、test 扣费分年度 IR。

registry 是唯一结构化数据源。新增独立报告：

- `backtest/experiments/strategy_report.html`

报告至少包含：

1. 两个冻结模型的路径与 SHA；
2. 固定时间、账户、费率和 benchmark；
3. B1-M valid 全候选排名，基线第一行、胜者高亮；
4. B6-M valid 全候选排名，基线第一行、胜者高亮；
5. 两组 test 的三池基线/胜者对比；
6. test 分年度扣费超额 IR；
7. test 不用于选型的声明。

原 `backtest/experiments/report.html` 也必须由同一 registry 重新生成，不得手工编辑任何 HTML。

## 8. 预登记与产物保留

开跑前为两个模型组写入 Phase S 预登记记录，至少包含：

- `baseline_ref: B1-S v1.0`；
- `frozen_model_ref`、模型路径和 SHA；
- 完整候选网格；
- selection segment、selection metric 和完整并列规则；
- test policy；
- 账户、费用、benchmark 与数据截止日；
- 原始预测路径和 SHA（预测冻结后补齐，但必须在首个策略回测前完成）。

Phase S 清理规则在首个回测前通过自动化测试：

- `backtest/models/baselines/` 中的冻结模型不参与清理；
- valid 落选策略仅保留 registry 指标摘要，删除其 `backtest/result/` 与 MLflow backtest recorder；
- 每个模型组保留 B1-S 基线和 valid 冻结胜者所需的预测、配置、最终 test 摘要与可审计 artifact；
- Phase S 保留逻辑与 Phase M 五种子模型保留逻辑相互独立；
- dry-run 有任何完整性或路径安全错误时禁止全部删除。

## 9. 验证与失败处理

实现代码采用测试驱动：先为单模型 baseline 解析、预测覆盖校验、模型定制网格、valid 选型、Phase S 报告和 retention 写失败测试，再写最小实现。

运行前验证数据日历覆盖 2026-07-31。预测必须满足：

- MultiIndex 精确包含 `datetime`、`instrument`；
- 无重复索引；
- 每个配置使用同一模型/股票池/分段的同一冻结预测；
- valid 与 test 物理分文件或具备不可混淆的分段 manifest；
- test 文件在 valid 胜者冻结前不得交给扫参入口。

任何候选失败都登记失败状态和错误摘要；不得从表中删除以美化结果。
