# B5 固定次日 RankIC 早停与超参筛选设计

日期：2026-07-31  
阶段：Phase M  
正式基线：B5 v1.0

## 1. 目标

在不进行滚动重训、不改变训练样本、特征、H40 标签、DoubleEnsemble
结构和策略的前提下：

1. 将 LightGBM 子模型的 early stopping 指标由 valid L2 改为固定次日
   valid RankIC；
2. 用固定五种子和 valid-only 规则筛选少量有机制依据的超参；
3. 在任何 test 指标可见前冻结唯一候选，再按规范进行三池 test 评估；
4. 保持实盘 B1 配置和 artifact 不变，另行给出加入 2020 年后样本的
   部署建议。

## 2. 固定研究口径

- 训练池：CSI1000。
- 种子：`[42, 1000, 2000, 3000, 4000]`。
- train：2016-01-02～2020-01-10。
- valid：2020-01-13～2021-07-15。
- test：2021-07-16～2026-07-16。
- 训练标签：H40 累计收益
  `Ref($close, -41)/Ref($close, -1)-1`。
- 训练标签处理：`DropnaLabel + CSRankNorm`。
- 特征：Alpha158 + range。
- 模型：DoubleEnsemble，3 个 GBM 子模型，SR/FS 均开启。
- 最终评测标签：固定次日
  `Ref($close, -2)/Ref($close, -1)-1`。
- Phase M 只看 IC/RankIC，不运行策略回测。

## 3. 固定次日 valid RankIC early stopping

新增仓库侧 `RankICEarlyStoppingDEnsembleModel` 子类，不修改 Qlib 通用
`DEnsembleModel`。

训练梯度仍只来自 train 上的 H40 CSRankNorm 标签和 MSE objective。每轮
boosting 后，在 valid 上计算固定次日 RankIC：

1. 取 valid 推理特征；
2. 独立加载固定次日原始收益标签；
3. 按 `(datetime, instrument)` 对齐；
4. 每个交易日计算一次截面 Spearman；
5. 对有效交易日等权平均，作为该轮 custom metric；
6. 指标连续 `early_stopping_rounds` 轮未创新高时停止。

LightGBM 内置 L2 metric 设置为字符串 `"None"`，`valid_sets` 只包含
valid，custom metric 返回 `is_higher_better=True`，从而确保早停只受
固定次日 RankIC 控制。

固定次日标签需要未来两个交易日价格。为了不读取 test 价格，early
stopping 的最后 anchor 固定为 2021-07-13；2021-07-14 和
2021-07-15 仅作为标签价格日，不作为预测样本。模型不得准备或读取
test segment。

DoubleEnsemble 的 SR、FS、训练损失曲线和样本权重仍只使用 train H40
标签。early stopping 会改变子模型树数，因而会间接影响后续 SR/FS，
属于本实验预期的一部分。

## 4. 超参候选

四组候选共用：

- `epochs=200`
- `early_stopping_rounds=20`
- 固定次日 valid RankIC custom metric
- B5 其余数据、模型和训练参数

候选如下：

| 代号 | 唯一额外改动 | 假设 |
|---|---|---|
| `rankic-es-base` | 无 | 对照固定次日 RankIC early stopping 本身的效果 |
| `rankic-es-l1low` | `lambda_l1=51.425` | 较低 L1 释放 range 与后续子模型中的弱分裂 |
| `rankic-es-lr010` | `learning_rate=0.1` | 更细 boosting 步长降低 0.2 学习率下的离散过冲 |
| `rankic-es-leaves128` | `num_leaves=128` | 限制后两个 SR/FS 子模型频繁撞到 210 叶上限导致的过拟合 |

暂不改变 L2、max_depth、num_models、SR/FS 开关及其内部参数，避免一次
混入多个机制变量。

## 5. 防止 test 调参

执行分为两个不可逆阶段：

### 阶段 A：valid-only

1. 为四组候选各训练固定五种子；
2. 只在 CSI1000 valid 上按统一固定次日标签评估完整 ensemble；
3. 以五种子平均 valid RankIC 最大者为唯一候选；
4. 若相同，以 valid RankICIR、再以候选代号字典序决胜；
5. 写出包含所有候选 valid 指标、所选候选、配置哈希和选择规则的
   selection manifest。

阶段 A 不生成任何 test 预测或 test 指标。原 B5 valid 指标只作为
参考，不参与四个新候选之间的强制选择。

### 阶段 B：冻结后 test

selection manifest 落盘后，只对所选候选五种子进行
csi1000/csi300/csi500 三池 test 评估。不得因 test 结果改选第二名或
追加参数。

四组训练视为一个预声明的超参搜索实验，registry 的正式 Phase M 行
登记 selection manifest、全部候选配置、唯一冻结候选的五个 session
以及三池 test 结果。valid 未入选 trial 的指标仍保存在轻量审计产物
中，重训练 artifact 在收尾时按规范清理。

## 6. 实现边界与测试

新增内容限定在：

- 仓库侧 RankIC early stopping 模型；
- 超参配置生成器；
- valid-only 选择与冻结 manifest 工具；
- 实验登记支持；
- 对应单元测试与实验配置。

必要测试：

1. 两日截面 RankIC 等权平均，不得退化为全局 Spearman；
2. 行乱序、ties、NaN、常数预测、小截面与统一 `daily_ic` 口径一致；
3. valid 最后 anchor 为 2021-07-13，且不准备 test；
4. dtrain 仍为原 B5 H40 标签，dvalid 只用于固定次日评估；
5. LightGBM objective 为 MSE、内置 metric 关闭、仅传 valid；
6. 五种子、配置差异和 selection manifest fail-closed；
7. manifest 不存在时禁止 test 阶段；
8. registry 和 HTML 中 `model-hyperparam` 表第一行为 B5。

## 7. 2020 年后样本与实盘

本轮不改实盘。当前实盘 B1 artifact 的监督训练仍止于
2020-01-10；每日新行情只用于特征和推理，不会更新模型参数。

后续优先方案是固定截点 challenger，而不是自动年度滚动：

1. 冻结选出的模型结构与超参，仅把训练截止日扩展到当时可获得完整
   H40 标签的最新日期；
2. 使用时间上更晚、完全样本外的区间或未来 shadow 评估；
3. 先与静态 champion 做小权重 rank ensemble（建议预注册 70/30），
   再决定是否替换；
4. 部署前另做模型类型适配、特征 schema、artifact hash、parity、
   SIMULATE 和回滚验证。

已有年度 expanding 实验整体低于 B5，不能作为直接上线滚动重训的
依据。研究 B5 的任何提升也不会自动替换实盘 B1。

## 8. 收尾

- 正式实验预先写明 `baseline_ref=B5 v1.0`；
- 结果写入 registry 并自动重建 HTML；
- 按统一 cleanup 工具先 dry-run、再 apply；
- 只保留当前 baseline 与符合规范的最佳完整候选五种子重产物；
- 是否提升基线或部署仍由用户决定。
