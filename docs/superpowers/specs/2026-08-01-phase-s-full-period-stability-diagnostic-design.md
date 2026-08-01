# Phase S 全区间策略稳定性诊断设计

## 1. 目标与结论边界

本实验用于诊断当前 Phase S 策略参数在更长时间上的稳定性，重点解释 B6-M
`topk-t30-d2-h20` 与 `topk-t30-d3-h20` 在短 valid 段上的巨大差异是否具有持续性。

本实验是**回顾性稳定性诊断**，不是新的策略选型实验：

- 不重训 B1-M 或 B6-M；
- 不修改当前 CSI1000 valid 冻结的 winner；
- 不修改实盘 B1 配置；
- 不使用全区间总指标宣布新 winner；
- 不覆盖现有 `backtest/experiments/strategy_report.html`。

## 2. 固定输入与实验范围

### 2.1 模型与预测

- 模型仅从 `backtest/models/baselines/b1-m/manifest.json` 与
  `backtest/models/baselines/b6-m/manifest.json` 加载和校验；
- 使用现有冻结 CSI1000 valid/test 预测拼接出连续诊断预测，不重新推理、不重新训练；
- 拼接时校验模型引用、源文件 SHA-256、精确 index SHA-256、无重复索引、日期连续覆盖；
- 完整诊断区间固定为 `2020-01-13`～`2026-07-31`。

### 2.2 策略与交易假设

- 股票池仅为 CSI1000，benchmark 为 `SH000852`；
- B1-M 使用现有 18 条冻结策略网格；
- B6-M 使用现有 22 条冻结策略网格；
- 共执行 40 条连续回测，每条策略只运行一次；
- 初始账户为 500,000 元，风险仓位为 0.95；
- 交易费率、涨跌停、整手及成交价继续使用当前实盘 B1-S 对照口径；
- B1-S `topk-t10-d2-h1` 在每个冻结模型方向中均为第一行对照。

## 3. 回测与年度拆分方法

每个策略从 2020-01-13 空仓、500,000 元开始，连续运行至 2026-07-31。持仓、现金和
`hold_thresh` 计数跨自然年延续，不在年初重置账户。

回测完成后，从同一条连续日度报告中拆分自然年度指标：

- 2020 年为 `2020-01-13` 起的部分年度；
- 2021～2025 年为完整自然年度；
- 2026 年为截至 `2026-07-31` 的部分年度；
- 年度收益、波动、夏普和回撤按该年度日收益重新起点计算，但年度第一日的持仓来自连续组合；
- “正收益年份数”等稳定性汇总只统计完整年度 2021～2025，部分年度单独展示、不参与计数。

## 4. 指标口径

日度扣费绝对收益定义为：

```text
net_return = report_normal.return - report_normal.cost
```

不展示或使用 IR。全区间和逐年度统一计算：

| 指标 | 公式与口径 |
|---|---|
| 扣费年化收益 | `mean(net_return) × 250`，与现有 Qlib 日频 annualized_return 口径一致 |
| 夏普比率 | `mean(net_return) / std(net_return, ddof=1) × sqrt(250)`，无风险利率为 0 |
| 卡玛比率 | `扣费年化收益 / abs(最大回撤)`；最大回撤为 0 时记为空值 |
| 年化波动率 | `std(net_return, ddof=1) × sqrt(250)` |
| 最大回撤 | 基于 `(1 + net_return).cumprod()` 的峰谷回撤 |
| 年化单边换手 | `mean(daily turnover) × 250 / 2`，沿用当前 Phase S 口径 |

同时展示 CSI1000 benchmark 的区间收益作为行情背景，但不计算超额 IR，也不参与排序或判定。

## 5. 稳定性展示

### 5.1 模型总表

B1-M 与 B6-M 各一张全策略表，B1-S 对照始终为第一行。每行展示全区间六项指标、完整年度
正收益年份数、完整年度夏普中位数和最差年度最大回撤。

表格仅提供描述性排序和高亮，不生成 `selected_candidate_id`，不改变当前 Phase S winner。

### 5.2 逐年度表

每个模型提供逐年度明细，包含 2020～2026 的收益、夏普、卡玛、波动率、最大回撤和换手。
2020、2026 明确标记为部分年度。

### 5.3 B6 参数邻域表

单独并排展示 B6-M：

```text
topk=30 × n_drop={2,3} × hold_thresh={5,10,20}
```

比较全区间和逐年度指标，突出：

- `d2/d3` 差异是否只集中在短 valid 时段；
- 差异来自收益、波动、回撤还是换手；
- `hold_thresh=20` 是否表现出明显的参数交互和年份依赖。

## 6. 产物、registry 与 HTML

新增独立产物目录：

```text
backtest/experiments/strategy-stability/20260801_full_period/
```

至少包含：完整预测拼接 manifest、两模型汇总 JSON、逐年度 JSON、失败与重试记录。

新增独立报告：

```text
backtest/experiments/strategy_stability_report.html
```

现有 `strategy_report.html` 和原 Phase S registry 结论保持不变。

registry 为每个模型新增一条 Phase S diagnostic 行：

- direction：`strategy-stability-full-period`；
- conclusion：`diagnostic_no_selection`；
- `baseline_ref: B1-S v1.0`；
- 准确填写 `frozen_model_ref`、预测 SHA/index SHA、账户、费率和完整区间；
- 登记所有成功、失败及 non-finite 结果；
- `cleanup_retention_eligible: false`，不得被清理器误认为候选 winner。

## 7. 留存与清理

该诊断不产生新 baseline 或 winner。HTML、registry、汇总 JSON 与预测拼接 manifest 永久保留；
40 条回测的 `backtest/result` 与对应 MLflow 目录在指标和审计信息落盘并验证后清理，不扩大现有
Phase S baseline/winner 留存集合。

清理器必须显式识别 `diagnostic_no_selection` 行并跳过 winner 完整性检查，同时继续对现有正式
Phase S 行执行 fail-closed 留存校验。

## 8. 验证要求

- 单元测试覆盖净收益、夏普、卡玛、波动率、回撤、年度切分和部分年度标记；
- 校验 18+22 策略矩阵完整，B1-S 第一行且没有 winner 字段；
- 校验拼接预测无重复、无缺日并匹配两个源预测 SHA/index SHA；
- 校验 HTML 不包含 IR 列，包含六项约定指标及 B6 参数邻域表；
- 校验 registry diagnostic 行不改变原 `strategy-sweep/b1-m`、`strategy-sweep/b6-m`；
- 清理 dry-run 零错误，正式 Phase S 17 个既有保留目录不变；
- 完成后运行聚焦回归测试并由独立代码审查确认无 Critical/Important 问题。
