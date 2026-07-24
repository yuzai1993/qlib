# 训练池年代对照与全 A OOM 诊断设计

## 目标

补齐两个 Phase M 训练样本实验：

1. 用 `CSI300 × 2016-01-02~2020-01-10` 隔离训练年代效应，解释 CSI1000 训练优于 B0 的原因。
2. 保留原 `train-data/all`（2006 起点）OOM 失败记录，新增 `全 A × 2016-01-02~2020-01-10`，验证缩短训练区间能否在当前 16GB M1 环境完成训练，并在失败时定位内存峰值所在阶段。

## 固定约束

- 遵循 `backtest/EXPERIMENT_STANDARD.md` v1.0。
- Phase M，仅改变训练样本池或训练起点；Alpha158、LGBM 参数、标签、valid/test、B0-S 均保持不变。
- 固定种子 `[42, 1000, 2000, 3000, 4000]`。
- 成功训练的变体统一在 `csi300/csi500/csi1000` 三个测试池，通过 `eval_ic_multi_pool.py` 计算 IC、ICIR、RankIC、RankICIR。
- 不使用 test 结果调参。
- 原 `train-data/all` 配置与失败 registry 行不覆盖、不改写。
- macOS 下不通过 heredoc 或 stdin 执行会触发 Qlib 并行取数的 Python。

## 实验假设（运行前冻结）

### `train-data/csi300-start2016`

将 CSI300 训练起点从 2006 提至 2016，去除较旧市场阶段。若 B0 的跨池 RankIC 明显上升并接近 CSI1000 训练组，说明 CSI1000 优势有较大部分来自训练数据更新；若仍明显落后 CSI1000，则截面宽度和中小盘样本结构是主要来源。

### `train-data/all-start2016`

将全 A 训练起点从 2006 提至 2016，可显著减少股票日样本和 Alpha158 矩阵内存；预期 seed 42 能在 16GB M1 上完成 `model.fit`。若成功，补齐五种子并检验更宽训练池是否继续提高三测试池 RankIC；若仍 OOM，则根据分阶段峰值内存定位问题，而不直接改变特征或模型参数。

## 方案比较

### 方案 A：覆盖原全 A 实验

直接把 `train-data/all` 的起点改为 2016。文件最少，但会混淆并覆盖已经登记的 2006 起点失败证据，不采用。

### 方案 B：独立年代对照与独立全 A 变体（采用）

新增 `csi300-start2016` 和 `all-start2016` 两个 exp_id。该方案一次只改变明确的训练样本变量，保留旧实验，可直接支持归因。

### 方案 C：立即降维、抽样或修改 LightGBM

可能快速规避 OOM，但同时改变特征或学习器，无法回答“仅把起点改为 2016 是否足够”，仅在方案 B 仍 OOM 且完成根因定位后作为后续独立实验候选。

## 执行流程

### CSI300 年代对照

1. 从 B0 配置复制五个种子配置。
2. 仅修改 exp_id、note、`fit_start_time` 和 train 起点为 `2016-01-02`。
3. 顺序训练五个种子，避免并行训练争抢内存。
4. 使用统一入口在三个默认测试池评估。
5. 汇总相对 B0、CSI500、CSI1000 训练组的 RankIC/RankICIR，并在 CSI300 测试池做逐种子成对比较。

### 全 A 2016

1. 从原全 A 配置复制五个种子配置。
2. 仅修改 exp_id、note、handler/train 起点为 `2016-01-02`；保留 `instruments: all` 和其他模型设置。
3. 先运行 seed 42，同时用系统工具采集进程峰值常驻内存和退出状态。
4. 若 seed 42 成功，顺序运行其余四个种子，再执行三测试池统一评估。
5. 若 seed 42 OOM：
   - 读取完整退出信息和系统内存事件；
   - 分别测量 handler 初始化、train/valid prepare、LightGBM fit 前后的数据形状与内存；
   - 与成功的 CSI1000 2016 配置比较样本数和峰值；
   - 形成单一根因结论和下一项最小实验建议。
6. 不在本轮擅自采用降维、抽样、降低精度或修改 LGBM 参数。

## 产物与验收

- 新配置分别位于：
  - `backtest/configs/train-data/csi300-start2016/`
  - `backtest/configs/train-data/all-start2016/`
- 成功实验包含五个训练 session、三个统一 IC JSON、五种子均值与标准差。
- 失败实验保留错误与诊断证据，不伪造空指标。
- 两个实验均追加至 `backtest/experiments/registry.jsonl`；旧全 A 失败行保持原样。
- 运行 `backtest/scripts/build_experiment_report.py` 重建 HTML。
- 最终报告明确区分：
  - 年代效应；
  - 截面/股票池效应；
  - 全 A OOM 是否由训练区间长度单独解决。

