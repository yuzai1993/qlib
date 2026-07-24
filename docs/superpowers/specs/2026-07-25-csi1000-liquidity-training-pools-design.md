# CSI1000 流动性分层训练池实验设计

## 目标

在不改变 Alpha158、LGBM、标签、时间划分、策略和默认三测试池的前提下，判断
CSI1000 的优势主要来自每日截面样本数量，还是来自不同流动性股票的样本结构。
实验完成后，将同数据版本的 `CSI1000-full` 五种子组提升为 B1-M，并只使用
valid 段 RankIC 选择一个单模型更新实盘模型引用。

## 实验矩阵

统一使用 CSI1000 point-in-time 成分、训练期 2016-01-02~2020-01-10、固定五种子
`[42, 1000, 2000, 3000, 4000]`：

1. `csi1000-full`：完整 CSI1000，对照组。
2. `csi1000-random-third`：每日确定性抽取约三分之一，控制样本数量。
3. `csi1000-liquidity-high`：每日历史流动性最高三分之一。
4. `csi1000-liquidity-mid`：每日历史流动性中间三分之一。
5. `csi1000-liquidity-low`：每日历史流动性最低三分之一。

流动性定义为 `Ref(Mean($vwap*$volume, 20), 1)`：只使用 t-1 及更早的20日平均
成交额代理，避免使用未来信息或当日成交信息。分层仅过滤 train 段；valid/test
保持完整 CSI1000，确保五组使用同一个早停与评测分布。随机组三分之一由
`日期 + 股票代码 + 固定 salt` 的稳定哈希决定，且与模型 seed 解耦。

## 评测与解释

每组五个模型都通过统一入口在 csi300/csi500/csi1000 测试池计算 IC、RankIC、
ICIR、RankICIR，并在 CSI300 上保存逐种子成对比较。

- 随机三分之一接近 full：样本结构比截面数量更重要。
- 随机三分之一明显回落：截面宽度是重要来源。
- 某个流动性层显著超过随机三分之一：该层样本具有额外信息价值。
- low 层 RankIC 高但 RankICIR 下降：可能存在较强但不稳定、难交易的信号。

实验仍属于 Phase M，不根据策略回测选择模型，也不改变 B0-S。

## B1-M 与实盘更新

实验完成后，`csi1000-full` 五种子组整体登记为 B1-M。实盘仍使用单模型，因此
在固定 valid 段 2020-01-13~2021-07-15 上计算五个 full 模型的 RankIC，以
RankIC 均值最高、RankICIR 次优作为平局规则选择 recorder；禁止使用 test 指标
挑 seed。

本次只更新实盘模型及其 handler 训练起点，实盘交易股票池仍保持 CSI300；改变
实盘交易池属于后续独立 Phase S 决策，不在本次提交中隐式完成。

选中的 `trained_model` 复制到 Git 跟踪目录
`live_trading/models/b1_m/<model-id>/trained_model`，同目录保存来源实验、recorder、
valid 指标、文件大小和 SHA-256。实盘配置使用相对项目根目录的 `model_path`
直接读取该文件，不再依赖 `mlruns/`。parity 配置必须校验相同的模型路径。

## 清理与保留

实验前按用户明确要求清空当前工作树的 `backtest/result/` 和 `mlruns/` 内容。
实验后按规范仅在实验工作树的 `mlruns/` 保留 B1-M 的五个
`CSI1000-full` 训练 recorder；删除分层实验和所有 backtest recorder 及
`.trash`。Git 跟踪模型是实盘加载的长期产物；registry、IC JSON、配置和 HTML
报告作为长期审计摘要保留。
