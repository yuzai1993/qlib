# Qlib 实验规范标准（EXPERIMENT_STANDARD）

版本：v2.4（2026-08-08）
状态：生效中
适用范围：本仓库内所有模型迭代与策略迭代实验（人工或 agent 执行）。
修改本文件需用户明确批准；agent 不得自行修改评测口径或时间划分。

---

## 0. 硬性约束（先读这里）

1. 当前研究模型基线为 **B6-M**，见第 1 节；模型迭代已收尾。历史实验的 `baseline_ref` 不改写，HTML 每个方向表格**第一行**仍为该方向对应的 baseline 指标行。
2. 模型与策略**分开迭代**：当前进入 Phase S，只使用 `backtest/models/baselines/<model-ref>/manifest.json` 指向的单一冻结模型，只改策略；当前研究策略基线为 B4-S。Phase M 的五种子训练与评估要求不变。
3. Phase M 看 **IC / RankIC**；Phase S 报告主展示为**扣费绝对收益**口径（年化/夏普/Alpha/Beta/基准涨幅/卡玛/波动/回撤/换手）；邻域选型另看**邻域 IR P25**。
4. 每个模型变体：**5 个固定种子，默认只在基线训练池（CSI1000）训练**（共 5 次训练），训练好的模型在 **3 个测试集**（csi1000/csi300/csi500）上评估 IC/RankIC，**研究主目标池为 CSI1000**。全A 暂不作为默认测试集（实验设计显式要求时再加）。仅训练样本类实验（更换训练池/起点/样本加权等）才使用其他训练池。
5. 默认时间划分固定（第 3 节）：Phase M 的评估集为 2020-01-13 ~ 2021-07-15、正式 test 截止 2026-07-16，**禁止用测试集调参**。Phase S：CSI1000 2020-01-13 ~ 2026-07-31 全历史连续区间允许用于策略比较与选型；该结果属于 `full_history_in_sample`，不得表述为样本外检验。仅第 3.4 节由用户明确批准的 post-2020 forward 成对实验使用其专用时间切分。
6. 每个实验必须登记到 `backtest/experiments/registry.jsonl`（配置路径 + 结果路径），并更新 HTML 报告（每个实验方向一张独立表格）。
7. **实验结束后必须同时清理 `mlruns/` 和 `backtest/result/`**（见第 6.3 节）。当前 Phase M 自动清理只保留模型 baseline 与超过它的最佳候选实验组，避免磁盘被打爆。

---

## 1. 基线定义（B6-M / B4-S）

研究基线与实盘配置分离维护。当前正式实盘仍是 `live_trading/configs/csi300_topk10_live.yaml` 的 B1 模型与 B1-S 策略；CSI1000 研究实盘配置随 B4-S 同步。实盘对照回测的唯一合法配置是 `backtest/configs/csi300_live_parity.yaml`（CSI300）与 `backtest/configs/csi1000_b6m_b2s_postclose_real_parity.yaml`（CSI1000 研究）。

### 1.1 模型基线 B6-M

| 项 | 值 |
|---|---|
| 基线版本 | `B6 v1.0`（B6-M，2026-07-31，由用户明确要求将超参冠军提升） |
| Phase M 五种子评估组 | `model-hyperparam/valid-rankic-search-v1` 的冻结胜者 `rankic-es-lr010`，固定种子 `[42, 1000, 2000, 3000, 4000]` |
| Phase S 冻结模型 | seed 4000 单模型；按 CSI1000 valid RankIC 选择，artifact 与 SHA-256 见 `backtest/models/baselines/b6-m/manifest.json` |
| 训练池 | CSI1000 |
| 来源 | B5（Alpha158+range、DoubleEnsemble、H40+CSRankNorm）之上，仅把学习率改为 0.1，并用固定次日 CSI1000 valid RankIC 早停与选型；registry `baseline/b6-m` 记录研究指标，baseline manifest 记录 Phase S 单模型 artifact |
| 特征 | Alpha158 + `range`（`backtest.features.technical.Alpha158Technical`，6 个密集区间/事件频率特征），handler start_time=2003-01-02 |
| 训练区间 | fit 2016-01-02 ~ 2020-01-10（csi1000 完整样本池） |
| 标签 | 累计未来 H40：`Ref($close, -41)/Ref($close, -1) - 1` |
| 标签处理 | `learn_processors`: DropnaLabel + CSRankNorm(fields_group=label)（覆盖 Alpha158 默认 CSZScoreNorm） |
| 模型 | `backtest.models.rankic_early_stop.RankICEarlyStoppingDEnsembleModel`（base_model=gbm，num_models=3，enable_sr/enable_fs=True，epochs=200，early_stopping_rounds=20） |
| 超参 | loss=mse, learning_rate=0.1，其余子模型 LGB 参数沿用 B5；早停指标为固定次日 valid 日 RankIC，有效 valid 截止 2021-07-13（见 `backtest/configs/model-hyperparam/rankic-es-lr010/`） |
| 数据处理 | infer_processors 含 ProcessInf，与实盘配置一致 |

五种子 test 固定一日正式指标以 registry `baseline/b6-m` 为准；H40 self-eval 仅为诊断。B5-M 及更早基线是历史对照，旧实验的 `baseline_ref` 不改写。Phase S 使用 `backtest/models/baselines/b6-m/manifest.json` 冻结的 seed 4000 单模型，但不自动切换实盘 B1 artifact。

### 1.2 策略基线 B4-S

| 项 | 值 |
|---|---|
| 基线版本 | `B4-S v1.0`（2026-08-08，由用户在审查邻域 IR P25 后明确要求提升 `topk-t22-d2-h2-r090`） |
| 冻结模型 | `B6 v1.0`；artifact 与 SHA-256 见 `backtest/models/baselines/b6-m/manifest.json` |
| 策略 | `TopkDropoutStrategy(topk=22, n_drop=2, risk_degree=0.90, hold_thresh=2, only_tradable=false, forbid_all_trade_at_limit=false)` |
| 研究账户 | 10,000,000（全历史稳定性主表与 baseline 锚点） |
| 选型依据 | B3-S 邻域 540 网格的邻域 IR P25 审查 + 用户确认；窗口 CSI1000 full 2020-01-13 ~ 2026-07-31；`full_history_in_sample`，不得表述为样本外检验 |
| registry | `baseline/b4-s-on-b6-m`；配置 `backtest/configs/baseline-strategy/b4-s/topk-t22-d2-h2_csi1000_full.yaml` |
| 历史基线 | B3-S（`topk=20, n_drop=2, hold_thresh=10, risk_degree=0.95`）与 B2-S 保留为历史对照 |
| 成交价 | close |
| 涨跌停限制 | limit_threshold=0.095 |
| 费率 | open_cost=0.00021, close_cost=0.00071, min_cost=5, trade_unit=100（按 QMT 2026-07-16 实际费用校准） |

注意：历史回测配置存在多套费率口径（如 0.0005/0.0015、0.0000954/0.0005954）。**本规范下所有策略回测统一采用上表实盘费率**，与历史结果对比时需注明费率口径。

回测指标除扣费绝对收益/超额收益外，必须计算相对基准的 CAPM **Alpha / Beta**（rf=0，日收益协方差）及**基准涨幅**（区间累计）。

**邻域行（晋升硬门）**：

- **轴向邻域**：策略参数网格上曼哈顿距离 ≤ 1 的点（自身 + 只改一个维度的相邻格）。
- **`strategy_stability_report.html` baseline 表**：每个 baseline 占两行，**列完全相同**（扣费年化/夏普/Alpha/Beta/基准涨幅/卡玛/波动/回撤/换手），不再单独挂「邻域 IR P25」列或行：
  - **上行（自身点）**：该候选自己的扣费绝对收益指标；
  - **邻域行**：同名各列均为轴向邻域内该绝对收益指标的 **25% 分位**（稳健下界），不是上行数字的复制。
- 邻域实验内部选型仍可用扣费超额 IR 的邻域 P25（`neighbor_ir_p25`）作排序键；baseline 报告展示以邻域行绝对指标 P25 为准。
- **晋升前必须先审查邻域行**（年化/夏普/回撤等是否相对自身点明显塌陷）；建议写入 registry（`neighbor_metrics_p25`，可选保留 `neighbor_ir_p25` 供邻域实验审计）。不得只凭单点夏普/年化晋升。

B1-S（`topk=10, n_drop=2, hold_thresh=1`）及其模型专属锚点继续作为历史审计记录，不再进入当前 Phase S artifact 清理白名单。

### 1.3 历史基线 B0 v1.0

B0-M 为 CSI300、Alpha158、LGBM、fit 2006-01-02 ~ 2020-01-10；B0-S 与历史 B1-S 相同。历史实验的 `baseline_ref: B0 v1.0` 与指标继续保留，不改写历史对照关系。

### 1.4 基线变更流程

只有当某实验按本规范完成完整评估（第 4/5 节）、结果对比数据经用户确认后，才可将其提升为新基线；提升时在本文件更新当前基线定义并记录版本号与日期。agent 不得自行提升基线。

本次 B6-M 提升、Phase M 收尾、B2-S / B3-S / B4-S 提升均已获用户明确确认。CSI1000 研究实盘配置随 B4-S 同步；CSI300 B1 正式实盘配置仍保留。B5-M、B1-S、B2-S、B3-S 与更早基线保留为历史对照。

---

## 2. 迭代模式

```
Phase M（模型迭代）            Phase S（策略迭代）
改：特征/标签/模型/超参    →    改：策略类型/参数/调仓规则
冻结：B4-S 策略                冻结：Phase M 选出的最优模型
指标：IC / RankIC              指标：扣费绝对夏普 / 年化 / 回撤 + Alpha/Beta；晋升前必看邻域 IR P25
                    ↑ 当前已切换，冻结 B6-M ↑
```

- Phase M 配置必须使用 `run.mode=train_only`，只训练并保存模型；**不得随模型训练自动运行策略回测**。如确需参考策略回测，必须在模型评估完成后使用冻结模型另行运行，且 B4-S 参数原样不变，结果不参与 Phase M 选型。
- Phase S 期间**不重训模型**：模型只允许从 `backtest/models/baselines/<model-ref>/manifest.json` 解析，逐项校验 baseline ID、目录边界、文件大小与 SHA-256；不得从 `mlruns/`、历史 `backtest/result/` 或实盘目录隐式寻找替代模型。每个 model-ref 使用 manifest 指向的单一冻结 artifact 生成预测，并在同一份冻结分数上比较策略，不做多种子集成。
- 同时改模型和策略的实验结果**不予采信、不进 registry**。

---

## 3. 数据与时间划分（固定）

### 3.1 时间划分

| 分段 | 区间 | 用途 |
|---|---|---|
| 训练集 train | 见 3.2，止于 2020-01-10 | 拟合模型 |
| 评估集 valid | Phase M：2020-01-13 ~ 2021-07-15；Phase S 仅历史审计/复现 | Phase M 早停、调参、中间筛选；Phase S 不作新选型 |
| 测试集 test | Phase M：2021-07-16 ~ 2026-07-16；Phase S：2021-07-16 ~ 2026-07-31（仅历史审计/复现） | Phase M 最终评估（禁止参与任何调参决策） |
| Phase S 全历史 full | CSI1000：2020-01-13 ~ 2026-07-31 | 策略比较与选型；`full_history_in_sample`，非样本外检验 |

handler 时间：`start_time=2003-01-02`，Phase M `end_time >= 2026-07-16`、Phase S `end_time >= 2026-07-31`，`fit_start_time/fit_end_time` = 对应池的 train 区间。

### 3.2 四个训练/测试池

| 池 | instruments | train 起点 | train 终点 | benchmark |
|---|---|---|---|---|
| CSI300 | `csi300` | 2006-01-02 | 2020-01-10 | SH000300 |
| CSI500 | `csi500` | 2016-01-02 | 2020-01-10 | SH000905 |
| CSI1000 | `csi1000` | 2016-01-02 | 2020-01-10 | SH000852 |
| 全A | `all` | 2006-01-02 | 2020-01-10 | 中证全指 SH000985 本地无数据，训练/回测配置暂用 SH000300 占位（Phase M 只看 IC/RankIC 不受影响；全A 策略回测结论仅供参考） |

- **默认训练池 = 基线训练池 CSI1000**（train 2016-01-02 ~ 2020-01-10）；**默认测试集 = csi1000 / csi300 / csi500**，其中 CSI1000 为研究主目标池。用同一个训练好的模型分别打分评估（跨池推理只需取数打分，无需重训）。
- **全A**：暂不纳入默认测试矩阵；若实验显式要求评估全A，剔除评估日距该股数据起始不足 60 个交易日的股票（次新股）；ST 股在股票名称缓存可用时一并剔除（`eval_ic_multi_pool.py --st-names`），不可用时在结果中注明"未剔除 ST"。
- 上表中其余池的训练配置仅用于**训练样本类实验**（direction 如 `train-data`：更换训练池、调整训练起点、样本加权等）；此类实验须在 registry 中注明所用训练池，并与相同训练池的基线组对比。
- Phase S 默认在**研究主目标池**（当前 CSI1000）的连续全历史 `full` 段（2020-01-13 ~ 2026-07-31）执行比较与选型；`valid` / `test` 只供历史审计或复现，其余池作稳健性参考。所有 full 结果必须标注 `full_history_in_sample`，不得表述为样本外检验。当前实盘配置仍为 CSI300；研究目标池变更不自动修改实盘配置或 B1。

### 3.3 种子

Phase M 固定 5 个种子：`[42, 1000, 2000, 3000, 4000]`。不得增删或挑选种子；报告必须给出 5 种子的均值与标准差，不得只报最优种子。Phase S 不重训、不重新选种子，统一使用 `backtest/models/baselines/` 中 manifest 已冻结的单一 artifact。

### 3.4 Post-2020 固定截点成对实验（一次性批准协议）

用户于 2026-07-31 明确要求：后续实盘目标改为当前研究最优模型，并允许在已选定的 `rankic-es-lr010` 超参版本上继续研究 2020 年后的训练样本。为避免把 2021-2026 同时作为训练和测试，批准以下一次性 forward 协议：

| 分组 | train | valid | test |
|---|---|---|---|
| stale control | 2016-01-02 ~ 2020-01-10 | 2023-01-03 ~ 2024-06-28 | 2024-07-01 ~ 2026-07-16 |
| post-2020 expanded | 2016-01-02 ~ 2022-12-30 | 2023-01-03 ~ 2024-06-28 | 2024-07-01 ~ 2026-07-16 |

协议约束：

1. 两组均使用 `model-hyperparam/valid-rankic-search-v1` 已冻结胜者的完整结构与超参；除 train 终点及对应 handler `fit_end_time` 外不得改变模型变量。
2. 两组均在 CSI1000 训练，使用固定五种子，并在共同 test 上评估 csi1000/csi300/csi500。结论只按两组在同一 forward test 上的成对差异给出，不得把 forward 数值直接与原 B5 的 2021-07-16 ~ 2026-07-16 数值比较。
3. early stopping 仍使用固定次日 valid 日 RankIC；为防标签跨入 test，有效 valid 锚点固定为 2023-01-03 ~ 2024-06-26（valid 最后两个交易日不作为早停样本）。训练 H40 标签继续由 `PurgedHorizonDataset(label_horizon=40)` 清除边界样本。
4. test 不参与早停、调参或二次筛选。两个分组、配置哈希及结论规则必须在训练前写入冻结协议清单；实验完成后，无论结果好坏都须登记 registry 并更新 HTML。
5. 该协议不改变 B5 v1.0 基线定义，不自动提升研究基线，也不直接切换实盘。registry 行必须同时标记 `evaluation_comparable_to_baseline: false` 与 `cleanup_retention_eligible: false`，清理器不得把该 forward 指标用于当前 baseline 候选排序。
6. 若 expanded 组胜出，生产重训、DoubleEnsemble 实盘推理适配、shadow/SIMULATE 与切换仍是后续独立步骤；不得用本协议的 test 继续调 production 模型超参。

该成对实验最终为 `inconclusive`，只回答“加入 post-2020 样本是否改善共同 forward 窗口”。B6-M 的提升来自此前冻结的 valid 超参选择及原始 test 评估，**不是**由该 forward 结果驱动；B6-M 训练窗口仍固定为 2016-01-02 ~ 2020-01-10。

---

## 4. 运行矩阵

一个模型变体的默认评估 = 基线训练池（CSI1000）× 5 种子训练（5 次训练），训练好的模型在**默认 3 个测试集**（csi1000/csi300/csi500）上打分评估（仅推理，无需重训）。

- Phase M 的 5 次训练统一使用 `backtest/scripts/run_backtest.py` 的 `train_only` 模式；该模式只创建 train recorder 和 `trained_model`，不创建 SignalRecord、PortAnaRecord 或 backtest recorder。
- 除非实验设计中**事先明确**只评估特定测试集，否则不得省略默认三指数中的任何一个；全A 默认不跑。
- 训练样本类实验：训练池由实验设计决定，种子数（5）与默认测试集（3 指数）要求不变。

---

## 5. 指标与报告要求

暂不设固定通过门槛：agent 负责按统一口径产出对比数据，**是否采纳/提升基线由用户判断**；agent 不得自行宣布"通过"或修改基线。

### 5.1 Phase M（模型迭代）

指标口径：test 段逐日截面 IC / RankIC 的时间均值，以及 ICIR / RankICIR（均值/标准差）。每个测试集先对 5 种子取均值。

**统一计算入口**：所有 IC/RankIC 一律通过 `backtest/scripts/eval_ic_multi_pool.py` 计算（内部调用 `eval_protocol.daily_ic`），不得各自手写实现。评测标签固定为默认 `Ref($close, -2)/Ref($close, -1) - 1`，**与训练标签无关**——这样不同标签设计的实验在同一把尺子下可比。

#### 指标含义与关注优先级

| 优先级 | 指标 | 含义 | 怎么用 |
|---|---|---|---|
| **最高优先（主指标）** | **RankIC** | 预测分数与真实收益的截面 Spearman 秩相关时间均值 | 直接对应选股排序能力（Topk 吃的是排序）；先看各测试集 RankIC 是否相对当前基线整体抬升 |
| **次优先** | **RankICIR** | RankIC 均值 / RankIC 标准差 | 排序信号的时间稳定性；均值升但 RankICIR 明显下降说明信号不稳 |
| 参考 | IC | 截面 Pearson 相关时间均值 | 对极端值更敏感，作辅助对照 |
| 参考 | ICIR | IC 均值 / IC 标准差 | Pearson 口径下的时间稳定性 |

读表顺序：同一方向内先看研究主目标池 **CSI1000 RankIC** → 再看 **CSI1000 RankICIR** 是否同步改善 → 最后用 CSI300 / CSI500 确认不是以严重牺牲跨池泛化换取主池提升。

**报告要求**：
1. 每个测试集给出 5 种子均值（HTML 以一指标一列展示，便于横向对比）；
2. 附研究主目标池（CSI1000）测试集上的逐种子成对比较结果（`backtest/scripts/eval_protocol.py: pairwise_win_count`）作为稳健性参考；
3. 不得只报最优种子或只报表现好的测试集。

### 5.2 Phase S（策略迭代）

**报告主展示口径**（与 `strategy_stability_report.html` baseline 表一致）：扣费绝对收益年化、夏普、Alpha、Beta、基准涨幅、卡玛、年化波动、最大回撤、年化单边换手。

**邻域选型 / 报告口径**：

| 角色 | 指标 |
|---|---|
| 报告主指标（自身点行） | 扣费绝对收益夏普 / 年化（及 Alpha/Beta/基准涨幅等） |
| 邻域行 | 与自身点行**同名同列**的绝对收益指标，取值均为轴向邻域内该指标的 P25 |
| 邻域实验排序键（不单独占 baseline 表列） | 邻域扣费超额 IR P25（`neighbor_ir_p25`） |
| 副指标 | 扣费超额 IR / 年化 / 最大回撤、换手（登记审计） |

说明：邻域行展示的是**邻域分位指标**，不是把自身点再抄一行；读表时上行看尖峰、邻域行看局部平坦度。baseline 表**不再**单独展示「邻域 IR P25」列/行。

**选型与报告要求**：

1. 首先校验所评估 model-ref 的 baseline manifest、模型路径与 SHA-256；记录 raw prediction 路径、SHA-256、精确索引覆盖、handler/config SHA 与数据版本。Phase S 不做多种子集成。
2. 策略网格、主指标与并列规则须在运行前预登记；在 CSI1000 `full` 段（2020-01-13 ~ 2026-07-31）对当前策略 baseline 与全部预登记候选作统一比较和选型。该比较必须显式标注 `full_history_in_sample`，不得表述为样本外检验。
3. 将 full-period 比较、胜者、baseline 对照、扣费绝对收益指标（含 Alpha/Beta/基准涨幅）一并登记到 registry，并从 registry 重建唯一活动的 Phase S 报告 `strategy_stability_report.html`；baseline 表必须含自身点行 + 邻域行（同列、邻域行取绝对指标 P25，无单独邻域 IR P25 列）；邻域段展示列与 baseline 绝对收益列一致；不得再把 `valid` 冻结胜者和一次性 `test` 打开作为新 Phase S 的选型流程。
4. **晋升门**：任何策略晋升为研究 baseline 前，必须先计算并展示其邻域行（`neighbor_metrics_p25`），写入 registry，且经用户确认；禁止仅凭单点绝对夏普/年化晋升。
5. 新 Phase S 方向使用 `baseline_ref: B4-S v1.0`，并准确填写 `frozen_model_ref: B6 v1.0`。若未来更换冻结模型，须在该模型上重新建立策略对照锚点，不得跨模型复用数值。

### 5.3 历史教训

- 单种子单次运行的 IR 差异可达 ±0.3 以上（见 `20260718_115728_label_horizon_multiseed` 的归因分析），top10 集中持仓会放大信号噪声——**任何单种子结论无效**。
- 5 种子均值 + 成对胜出是当前成本下的最低置信要求；仍不足以支撑绝对收益承诺，只用于相对淘汰。

---

## 6. 实验登记规范

### 6.1 命名

- 实验方向（direction）：短横线小写，如 `label-design`、`feature-ablation`、`model-arch`、`strategy-sweep`、`train-data`。
- 实验 ID（exp_id）：`<direction>/<变体名>`，如 `label-design/cum_h10`、`baseline/b0-m`、`train-data/csi1000`。
- 配置文件：**按 exp_id 建目录**，放在 `backtest/configs/<exp_id>/` 下，例如：
  - `backtest/configs/baseline/b0-m/b0_csi300_lgbm_s42.yaml`
  - `backtest/configs/train-data/csi1000/td_csi1000_lgbm_s42.yaml`
  文件名含变体与种子；配置头部注释写明 exp_id 与运行命令（`--config baseline/b0-m/b0_csi300_lgbm_s42.yaml`）。`config_loader` 支持相对 `configs/` 的子路径，也支持仅文件名（唯一匹配时）。
- 结果目录：`backtest/result/<时间戳>_<变体名>/`（run_backtest.py 默认行为，note 字段填变体名）。

### 6.2 registry（必填）

每个实验（含判负的）完成后，向 `backtest/experiments/registry.jsonl` 追加一行 JSON：

```json
{
  "exp_id": "label-design/cum_h10",
  "direction": "label-design",
  "phase": "M",
  "date": "2026-07-18",
  "hypothesis": "10 日累计标签比 1 日标签信噪比更高，预期 RankIC 提升",
  "baseline_ref": "B1 v1.0",
  "seeds": [42, 1000, 2000, 3000, 4000],
  "train_pool": "csi300",
  "test_pools": ["csi300", "csi500", "csi1000"],
  "data_version": "2026-07-16",
  "configs": ["backtest/configs/label-design/cum_h10/csi300_lgbm_cum_h10_s42.yaml"],
  "result_dirs": ["backtest/result/20260718_122513_ms_cum_h10_s42"],
  "metrics_summary": {"csi500": {"rank_ic_mean": 0.061, "rank_icir": 0.45}},
  "conclusion": "regress",
  "note": "RankIC 全测试集低于 B1，判负"
}
```

字段要求：
- **`hypothesis` 必填，且必须在实验开跑前写好**（改了什么、预期哪个指标为什么会变好）；事后只按该口径解读结果，防止"事后找亮点"。
- **`baseline_ref` 必填**：写明对照的 baseline 版本（当前如 `B1 v1.0`）；同一 `direction` 内不得混用多个版本。HTML 该方向表第一行即此版本对应的 baseline 指标。
- **`data_version` 必填**：填当时数据日历的最后交易日（`eval_ic_multi_pool.py` 输出中自动带出）。数据前复权重标定不改变 Alpha158 特征值（全部为比值形态），但历史修正/补数会轻微改变截面构成，此字段用于事后解释不同时间实验结果的差异，无需做数据快照。
- Phase S 行另须填写 **`frozen_model_ref`**、manifest/模型/预测 artifact 与 SHA、selection segment、**`evaluation_mode: full_history_in_sample`**、冻结策略参数、费率、benchmark 及三项扣费指标；模型必须来自 `backtest/models/baselines/<model-ref>/manifest.json`。新 Phase S full-period 行缺少该 `evaluation_mode` 不得登记为完成的选型结果。

### 6.3 mlruns 与 result 清理（强制，防磁盘打爆）

`mlruns/` 与 `backtest/result/` 默认 gitignore，训练、预测和回测会持续堆积 artifact。**每次实验收尾（无论成败）都必须在 registry 和 HTML 更新后运行统一清理**：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/cleanup_experiment_artifacts.py
# 确认 dry-run 白名单与删除列表后：
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/cleanup_experiment_artifacts.py --apply
```

清理器以 registry 为唯一选择依据，按**完整五种子实验组**保留，不得按 test 结果挑单个 seed。

**长期保留组**：

1. 当前 Phase M baseline 五种子组；
2. 当前 baseline 下超过 baseline 的最佳 Phase M 候选五种子组，最多一个；没有合格候选则不保留候选；
3. 实盘单模型继续存放于 Git 跟踪目录，由 `live_trading/configs/csi300_topk10_live.yaml` 引用，不依赖 result session。

**Phase M 候选资格与排序**：

- CSI300、CSI500、CSI1000 三池五种子平均 RankIC 必须全部严格高于当前 baseline；
- baseline 与候选用于比较的 RankIC/RankICIR 必须是有限数值；NaN、Infinity 或缺失指标不得参与保留决策；
- 合格候选先按 CSI1000 RankIC 增量排序；
- CSI1000 RankIC 增量相同时，以 CSI1000 RankICIR 增量作为并列规则；
- 上述两项仍相同时，以三池 RankIC 平均增量作为第二并列规则；
- Phase M 与 Phase S 指标不可混排。当前清理器只自动评选 Phase M；进入 Phase S 前须先为第 5.2 节三项策略指标补齐独立 baseline/候选 schema 与清理测试，不得套用 RankIC 规则。

Phase S 的预测与回测 bundle 使用独立 retention schema：只长期保留 registry 中最新、显式 `cleanup_retention_eligible: true` 的策略 baseline 锚点所引用的 CSI1000 full-period 正式比较 session；历史 valid/test 扫参、旧策略 baseline、诊断回测不长期保留运行目录。单一冻结模型校验、精确预测覆盖校验及清理测试必须持续通过。

**`mlruns/` 保留内容**：

- 当前 baseline 与最佳候选仍存在的 train recorder（含 `artifacts/trained_model`）；
- 删除所有 backtest recorder、落选/失败 train recorder、未知历史实验和 `.trash/`。

**`backtest/result/` 保留内容**：

- 当前 baseline 与最佳候选 registry 行引用的五种子 session；
- 删除其他所有 session，包括判负、未超过 baseline、中途失败和已被更优候选替代的目录。

registry 中的历史 `result_dirs` 字符串允许指向已清理目录，它们是审计记录而非永久文件承诺。以下 Git 跟踪摘要永不参与清理：`registry.jsonl`、配置文件、`backtest/experiments/ic/*.json` 和自动生成的 HTML 报告。

清理脚本默认 dry-run，只有显式 `--apply` 才删除；删除目标必须是 `mlruns/` 或 `backtest/result/` 的直接子目录。保留组必须具有规范中的完整五种子列表、五个现存 result session，且每个 session 能唯一解析到成功的 train experiment；任一条件不满足时脚本必须阻止全部删除。提升 baseline 后，旧 baseline 仅在仍是最佳候选时保留，否则由下一次清理删除。

---

## 7. HTML 报告规范

- `backtest/experiments/report.html` 由 `backtest/scripts/build_experiment_report.py` 从 `registry.jsonl` 自动生成，是 Phase M 与模型实验的规范入口；**不是 Phase S 的活动报告**。registry 仍是唯一数据源，禁止手工编辑 HTML。
- Phase M 报告顶部自动生成目录与指标说明；每个方向一张表，表格第一行固定为对应 baseline，指标为 4 项（RankIC / RankICIR / IC / ICIR）× 3 指数。
- `backtest/experiments/strategy_stability_report.html` 是**唯一活动的 Phase S 报告**，由 `backtest/scripts/build_strategy_stability_report.py` 从 registry 生成。每次 Phase S full-period 登记后必须重建它，并显著展示 `evaluation_mode: full_history_in_sample` 与非样本外声明。
- 无效实验也要登记并保留在相应报告中，避免重复试错。
- 历史报告 `build_benchmark_html.py` 仅作为规范生效前旧实验的存档，不再新增内容。

---

## 8. 标准执行流程（checklist）

Phase M 已以 B6-M 收尾。Phase S checklist：

```
[ ] 1. 从 `backtest/models/baselines/<model-ref>/manifest.json` 校验单一冻结模型；生成并冻结 raw predictions（路径 + SHA + 精确覆盖）
[ ] 2. 以 B2-S 建立组内 baseline，并冻结费用/benchmark/回测配置
[ ] 3. 在 registry 预登记策略网格、CSI1000 full-period 选型指标和并列规则，并标记 `full_history_in_sample`
[ ] 4. 在 CSI1000 2020-01-13 ~ 2026-07-31 全历史连续区间统一比较 B2-S 与全部预登记候选并选型；不得称为样本外检验
[ ] 5. 齐报 full-period 扣费超额 IR/年化/最大回撤与扣费分年度 IR，并将 B2-S 对照和胜者一并登记
[ ] 6. 从 registry 重建唯一活动的 Phase S 报告 `strategy_stability_report.html`；提升获批后只保留当前 Phase S baseline 的 CSI1000 full-period 正式比较 session
```

---

## 附录 A：相关脚本

| 脚本 | 用途 |
|---|---|
| `backtest/scripts/run_backtest.py` | `train_only` 模型训练、显式 `train_backtest` 兼容模式与 `backtest_only` 入口 |
| `backtest/scripts/eval_ic_multi_pool.py` | **Phase M 统一 IC/RankIC 跨池评估**（含全A过滤与 data_version 输出） |
| `backtest/scripts/eval_protocol.py` | daily_ic / summarize_ic / pairwise_win_count / yearly_ir |
| `backtest/scripts/run_pred_backtest.py` | 基于现成 pred 分数回测（Phase S 用） |
| `backtest/scripts/run_strategy_sweep.py` | 策略扫参（Phase S 用） |
| `backtest/scripts/ensemble_preds.py` | 多种子预测集成（截面 z-score 等权） |
| `backtest/scripts/build_experiment_report.py` | **registry.jsonl → 标准实验 HTML 报告**（含目录，唯一渲染入口） |
| `backtest/scripts/cleanup_experiment_artifacts.py` | registry 驱动的 mlruns/result dry-run 与统一清理 |

## 附录 B：环境注意事项

- macOS 下禁止用 heredoc/stdin 运行会触发 Qlib 并行取数的代码，见 `.cursor/rules/qlib-shell-multiprocessing.mdc`。
- Python 解释器：`/opt/anaconda3/envs/qlib/bin/python`。
- 数据：`~/.qlib/qlib_data/cn_data`，跑实验前确认数据已更新到 `end_time` 之后。
