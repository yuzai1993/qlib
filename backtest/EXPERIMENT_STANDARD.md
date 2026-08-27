# Qlib 实验规范标准（EXPERIMENT_STANDARD）

版本：v2.16（2026-08-23）
状态：生效中
适用范围：本仓库内所有模型迭代与策略迭代实验（人工或 agent 执行）。
修改本文件需用户明确批准；agent 不得自行修改评测口径或时间划分。

本次修订（v2.16）由用户于 2026-08-23 **明确批准**：把 **v4 RankIC ES × 真阶梯 k3×h5**（`CohortLadderStrategy` topk=3 horizon=5）晋升为执行层回测基线 **BT v4**。晋升依据是组合规则对齐主格 top3×h5，不是对 BT v3 逐维占优。v2.15 由用户于 2026-08-22 **明确批准**：Phase M v1 **训练早停**改回评估窗上的 `daily_rank_ic`；把「v3 + RankIC 早停」官方合成信号晋升为模型基线 **v4 · M0 H20 RankIC ES**；总报告第 1 块增加**全局 RankIC**（官方合成信号、全宇宙日截面 Spearman，读 `ensemble.h5.rank_ic_mean`）。**评测主格不变**，仍是 top3×h5 扣费净年化/波动/夏普。v4 不是重跑 v1：v1 是 499 天次日 RankIC + `valid_frame_70.pkl`；v4 仍用 `valid_frame_t5h5es.pkl`（约 1454 天，H5 标签只作 RankIC 的 y）。v2.14 由用户于 2026-08-22 **明确批准**：Phase M v1 每个实验的主指标都是 **top3×h5**；分年、分风格只报这一格，不再叉乘 k×h。v2.13 将官方主格与早停改为 top3×h5，并按净年化晋升 **v3**。v2.12 修了前视窗截断，并把执行层回测基线提升为 **BT v3**。CSI1000 历史 Phase M / Phase S 轨道（B6-M / B4-S、IC/RankIC、邻域规则）保持不变。两套轨道禁止混比。

---

## 0. 硬性约束（先读这里）

本仓库同时维护两套研究轨道，**禁止混用指标、时间窗或 baseline**：

| 轨道 | 用途 | 模型基线 | 策略基线 | 评估口径 | 报告入口 |
|---|---|---|---|---|---|
| CSI1000 研究/实盘 | 历史 Phase M 已收尾；当前 Phase S | **B6-M** | **B4-S** | Phase M：IC/RankIC（三池）；Phase S：扣费绝对收益 + 邻域行 | `report.html` / `strategy_stability_report.html` |
| Phase M v1（全A / regime-adapt） | 现行全A 模型迭代 | **v4 · M0 H20 RankIC ES** | 本轨道只改模型，不套用 B4-S 选型 | 主格 = 五种子合成信号上的 top3×h5 扣费净年化/波动/夏普（无北极星）；总报告另报官方合成信号的全局 h5 RankIC；执行层对照另见第 1.6 节 | `phase_m_v1_report.html` + 各实验详细报告；执行层回测 `phase_m_v1_bt_report.html` |

1. CSI1000 轨道：当前研究模型基线为 **B6-M**，见第 1 节；该轨道模型迭代已收尾。历史实验的 `baseline_ref` 不改写，HTML 每个方向表格**第一行**仍为该方向对应的 baseline 指标行。Phase M v1 轨道：当前模型基线为 **v4**（第 1.5 节，评估窗 `daily_rank_ic` 早停）；总报告按第 5.1.2 节四块排版，不再按 direction 分表。v3 · M0 H20 t3h5es 与更早版本留在历史 baseline 表。
2. 模型与策略**分开迭代**。CSI1000 轨道当前进入 Phase S，只使用 `backtest/models/baselines/<model-ref>/manifest.json` 指向的单一冻结模型，只改策略；当前研究策略基线为 B4-S。该轨道 Phase M 的五种子训练与评估要求不变。Phase M v1 只改模型（特征/标签/加权/结构），用第 5.1.2 节口径评估，不得与 CSI1000 Phase S 回测数字混排或互相覆盖。
3. CSI1000 Phase M 看 **IC / RankIC**；CSI1000 Phase S 报告主展示为**扣费绝对收益**口径（年化/夏普/Alpha/Beta/基准涨幅/卡玛/波动/回撤/换手）；邻域选型另看**邻域 IR P25**。Phase M v1 **取消北极星**，主格为 **top3 × h5**，主指标为扣费净年化、扣费波动（HAC 年化标准差）、扣费夏普（净年化/波动）。
4. CSI1000 轨道：每个模型变体 **5 个固定种子，默认只在基线训练池（CSI1000）训练**（共 5 次训练），训练好的模型在 **3 个测试集**（csi1000/csi300/csi500）上评估 IC/RankIC，**研究主目标池为 CSI1000**。全A 暂不作为该轨道默认测试集（实验设计显式要求时再加）。仅训练样本类实验（更换训练池/起点/样本加权等）才使用其他训练池。Phase M v1：固定 5 种子 `[42, 1000, 2000, 3000, 4000]`，默认在**全A**长窗训练，评估宇宙为全A（三过滤，见 5.1.2）。
5. 默认时间划分固定（第 3 节）：CSI1000 Phase M 的评估集为 2020-01-13 ~ 2021-07-15、正式 test 截止 **2026-07-16**，**禁止用测试集调参**。CSI1000 Phase S：CSI1000 2020-01-13 ~ 2026-07-31 全历史连续区间允许用于策略比较与选型；该结果属于 `full_history_in_sample`，不得表述为样本外检验。仅第 3.4 节由用户明确批准的 post-2020 forward 成对实验使用其专用时间切分。**Phase M v1 评估窗为 2020-08-03 ~ 2026-07-31**（与 CSI1000 旧 test 截止 2026-07-16 不同；禁止把两套窗口上的数字直接对比）。
6. 每个实验必须登记到 `backtest/experiments/registry.jsonl`（配置路径 + 结果路径），并更新对应 HTML 报告。CSI1000 Phase M → `report.html`；CSI1000 Phase S → `strategy_stability_report.html`；Phase M v1 → `phase_m_v1_report.html`（四块，见第 5.1.2 节）+ 该实验详细报告。
7. **实验结束后必须同时清理 `mlruns/` 和 `backtest/result/`**（见第 6.3 节）。当前 CSI1000 Phase M 自动清理只保留模型 baseline 与超过它的最佳候选实验组，避免磁盘被打爆。

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

本次 B6-M 提升、Phase M 收尾、B2-S / B3-S / B4-S 提升、以及 2026-08-19 Phase M v1 的日频 ST 切口径、**M0 H20 ES** 模型提升、2026-08-22 的前视窗截断口径修正、**BT v3** 执行层提升、2026-08-22 用户明确批准的 **主格改 top3×h5 并按净年化晋升 v3**、同日用户明确批准的 **早停改回 daily_rank_ic 并晋升 v4**、以及 2026-08-23 用户明确批准的 **BT v4 真阶梯 k3×h5**，均已获用户明确确认。CSI1000 研究实盘配置随 B4-S 同步；CSI300 B1 正式实盘配置仍保留。2026-08-27 用户批准把全A 观察实盘切到 `alla_v4_ladder_k1h5_postclose_real`（30 万、top1×h5、仓位 100%），**不改变** BT v4 研究基线（仍是 k3×h5）。B5-M、B1-S、B2-S、B3-S、M0 H20、M0 H20 ES、M0 H20 t3h5es、BT v1 / v2 / v3 与更早基线保留为历史对照。

### 1.5 Phase M v1 模型基线（现行协议 top3×h5，早停 RankIC）

本基线只服务 **Phase M v1（全A / regime-adapt）** 轨道，**不替换**第 1.1 节 B6-M，也不进入 CSI1000 Phase S 冻结模型。两套轨道禁止混比。

| 项 | 值 |
|---|---|
| 基线版本 | **v4 · M0 H20 RankIC ES**（2026-08-22，用户明确批准：训练早停改回评估窗 `daily_rank_ic`，晋升官方合成信号） |
| registry | `regime-adapt/m0-h20-rankices-v1`（`baseline_version: v4`） |
| 训练池 | 全A |
| 训练窗 | 2004-01-02 ~ 2020-07-31 |
| 模型 | 单 LGBM（B3-M 冻结超参 + CSRankNorm；超参见 `backtest/scripts/train_regime_arm.py` 的 `FROZEN_SINGLE_KWARGS`） |
| 标签 | 累计未来 H20 + CSRankNorm |
| 特征 | Alpha158 + range；**无 regime 特征** |
| 日权重 | 自然分布（M0 权重，无风格再加权） |
| 早停 | `es_metric=daily_rank_ic`，`es_valid=eval_window`：全A 评估窗日截面 RankIC；valid 特征缓存仍是 `valid_frame_t5h5es.pkl`（标签仍是 H5，只作 RankIC 的 y）。新实验 `--es-valid auto` 也走评估窗，不再默认 499 日分层集 |
| 训练 session | `regimeadaptfast_m0h20_rankices_s{42,1000,2000,3000,4000}` |
| 种子 | `[42, 1000, 2000, 3000, 4000]` |
| 评估窗 | 2020-08-03 ~ 2026-07-31（常用；与 CSI1000 旧 test 截止 2026-07-16 不同） |
| 评估 ST | 日频 `scripts/data_collector/tushare/st_daily.csv` |
| 评估入口 | `backtest/scripts/eval_ic_multi_pool.py` |
| 官方信号 | 五种子 pred 先做**日截面 z-score**，再等权平均，**只评估这一次** |
| 主格 | 上述合成信号上的 top3 × h5；无北极星 |
| 全局 RankIC | 同一条合成信号、全宇宙、h5 标签的日 Spearman 均值（`ensemble.h5.rank_ic_mean`） |
| 禁止 | 用五种子指标的算术平均充当官方数字；用 `seed_mean` 的 RankIC 冒充官方全局 RankIC；把 v4 写成 v1 的 499 天次日协议 |

官方数字读 eval JSON 的 `pools.all.ensemble`（总报告 / registry `metrics` 已按此取），**禁止手写死数字**。`pools.all.seed_mean` 仍是种子指标均值，只作稳健性，不进总报告第 1～3 块。读表直觉约数（`eval_regime_ablation/eval_m0h20_rankices.json`，以 JSON 为准）：净年化约 **+30.6%**、波动 57.8%、夏普 0.53、全局 RankIC 约 **0.0999**、`n_days` 1448。

#### 1.5.1 历史基线 v3 · M0 H20 t3h5es

`regime-adapt/m0-h20-t3h5es-v1`（`baseline_version: v3`）是 2026-08-22 当日短暂的 Phase M v1 模型基线。配方与上表相同，但早停为 `top3_h5_net_ann`。读 `eval_regime_m0_t3h5es/eval_m0h20.json`：约净年化 **+29.7%**、波动 57.6%、夏普 0.52、非扣费 +33.6%、`n_days` 1448。训练 session `regimeadaptfast_m0h20_t3h5es_s{42,1000,2000,3000,4000}` 保留。历史实验的 `baseline_ref` 不改写。

#### 1.5.2 历史基线 v2 · M0 H20 ES

`regime-adapt/m0-h20-t5h5-es-v1`（`baseline_version: v2`）是 2026-08-19 至 2026-08-22 的 Phase M v1 模型基线。配方与上表相同，但训练标签固定 H20、早停为 `top5_h5_net_ann`。2026-08-22 起官方主格改为 top3×h5 后，该行改读 `eval_regime_m0_labels/eval_m0h20es_k123h2345.json`：约净年化 **+30.2%**、波动 58.2%、夏普 0.52、非扣费 +34.1%、`n_days` 1448；2026 净年化约 +9.8%。当时主格 top5×h5 的约数（+28.6% / 56.1% / 0.51，2026 −0.3%）只作历史尺子对照。训练 session `regimeadaptfast_m0h20_t5h5es_s{42,1000,2000,3000,4000}` 保留。

#### 1.5.3 历史基线 v1 · M0 H20

`regime-adapt/m0-h20-label-v4`（`baseline_version: v1`）是 2026-08-16 至 2026-08-19 的 Phase M v1 模型基线，早停为冻结 499 天次日 RankIC。2026-08-22 起该行官方评估改读 `eval_regime_m0_labels/eval_m0h20_k123h2345.json`（top3×h5）：约净年化 **+29.3%**、波动 56.7%、夏普 0.52、`n_days` 1448；2026 净年化约 +11.0%。当时主格 top5×h5 约数（+29.1% / 55.5% / 0.52，2026 +3.6%）只作历史尺子对照。8/16 的静态 `st_names` 数字、8/20 超额口径约数、以及 `eval_m0h20_st_daily.seedmean.json` 的五种子均值不再作官方口径。历史实验的 `baseline_ref` 不改写。

### 1.6 Phase M v1 执行层回测基线 BT v4

本基线只服务 **Phase M v1 的真实执行层对照**，不是 CSI1000 Phase S，也不替换第 1.5 节模型基线。当前策略是主格 top3×h5 的执行层等价物，不再是 TopkDropout。

| 项 | 值 |
|---|---|
| 回测基线版本 | **BT v4**（2026-08-23，用户明确要求将 v4 RankIC ES × 真阶梯 k3×h5 均值信号回测提升为当前执行层对照锚点） |
| registry | `baseline/phase-m-v1-bt-v4` |
| 冻结模型 | M0 H20 RankIC ES 五种子 `regimeadaptfast_m0h20_rankices_s{42,1000,2000,3000,4000}`（与第 1.5 节模型基线 v4 同一组） |
| 官方信号 | 五种子 pred 先做**日截面 z-score**，再等权平均，**只回测这一次** |
| 禁止 | 用五次回测指标的算术平均充当官方数字 |
| 策略 | `CohortLadderStrategy`：`topk=3, horizon=5, risk_degree=0.90`（无 `force_sell_rank`） |
| 账户 | 1,000,000 |
| 窗 | 2020-08-03 ~ 2026-07-31 |
| 过滤 | 日频 `st_daily` + 成交额≥1000万 + 上市≥60日 + 近60交易日连续有成交 |
| 涨跌停 | `limit_threshold: market_cn`（主板 9.5%、创业板/科创板 19.5%） |
| 基准 | 等权全A |
| 总报告 | `backtest/experiments/phase_m_v1_bt_report.html` |
| 官方年化 | **CAGR**：\((1+\text{累计})^{250/n}-1\)。JSON 字段 `annualized_return` |
| 审计年化 | 算术：日均扣费收益 ×250，字段 `annualized_return_arith` |
| 夏普 | 算术年化 /（日收益标准差 ×√250），分子不用 CAGR |
| 官方数字 | 读 `backtest/result/phase_s_regime/all_ladder_k3h5/m0h20rankices.json` 的 `ensemble`；直觉约数：累乘年化 +26.3%、算术年化 +26.7%、夏普 1.04、Alpha +17.4%、回撤 −33.0%、单边年换手 44.3；2026 累乘年化 +3.8% |

**真阶梯含义**：每日买入当日 top-3；每只持满 5 个交易日到期无条件卖出；同一只票允许被多个分层同时持有（连续上榜自动加仓）。退出看持有天数，不看打分排名。实现见 `qlib/contrib/strategy/signal_strategy.py` 的 `CohortLadderStrategy`。

晋升依据是**组合规则对齐主格**，不是对 BT v3 逐维占优。相对 BT v3（v2 模型 × TopkDropout h5f100）：CAGR +26.3%/+30.1%、夏普 1.04/1.06、回撤 −33.0%/−34.7%、换手 44.3/58.1。k5×h5 真阶梯与后来的 f100 / 补新票变体仍只作诊断，不改写本行。

总报告必须维护：历史 BT baseline 全周期表（含带图 `report.html` 链接）、分年矩阵（一行一个 baseline 版本、一列一年、每个指标一张表）、分风格矩阵（D/F/T，同样每个指标一张表）、实验表（链接到该次实验详细报告）。实验详细报告版式与总报告相同，但「历史 baseline」换成**实验组 vs 基准组**。风格标签用 `backtest/configs/regime-adapt/monthly_regime_labels_eval_window_v1.csv`。当前行标 **BT v4**；v1、v2、v3 留在历史表。

#### 1.6.1 历史回测基线 BT v1 / v2 / v3

`baseline/phase-m-v1-bt-v1` 是 2026-08-19 当日的执行层锚点：修复后的 M0 H20（RankIC 早停）× top5d1 均值信号回测。`baseline/phase-m-v1-bt-v2` 是 2026-08-19~2026-08-22 的锚点：M0 H20 ES × top5d1（`hold_thresh=1`，无强制卖出）。`baseline/phase-m-v1-bt-v3` 是 2026-08-22~2026-08-23 的锚点：M0 H20 ES × TopkDropout `topk=5, n_drop=1, hold_thresh=5, force_sell_rank=100`（对 BT v2 逐维占优：CAGR +30.1%/+23.1%、夏普 1.06/0.86、回撤 −34.7%/−39.2%）。三者晋升后均保留在历史 baseline 表，不改写数字。

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
- Phase M v1（全A / regime-adapt）是独立的模型迭代轨道：只改模型，用第 5.1.2 节口径；不套用上图 CSI1000 Phase S 的 B4-S 冻结策略选型，也不把 B6-M 从 CSI1000 轨道撤下。

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

**Phase M v1 评估窗（与上表 CSI1000 轨道不同，必须分开写）**：常用评估窗为 **2020-08-03 ~ 2026-07-31**，训练窗为 **2004-01-02 ~ 2020-07-31**。该窗口由用户在本轨道中已使用，并于 2026-08-16 批准写入本规范。CSI1000 轨道的 valid/test 硬约束（test 截止 2026-07-16）不变；禁止把 Phase M v1 数字与 CSI1000 Phase M test 数字直接对比。

### 3.2 四个训练/测试池

| 池 | instruments | train 起点 | train 终点 | benchmark |
|---|---|---|---|---|
| CSI300 | `csi300` | 2006-01-02 | 2020-01-10 | SH000300 |
| CSI500 | `csi500` | 2016-01-02 | 2020-01-10 | SH000905 |
| CSI1000 | `csi1000` | 2016-01-02 | 2020-01-10 | SH000852 |
| 全A | `all` | 2006-01-02 | 2020-01-10 | 中证全指 SH000985 本地无数据，训练/回测配置暂用 SH000300 占位（Phase M 只看 IC/RankIC 不受影响；全A 策略回测结论仅供参考） |

- **默认训练池 = 基线训练池 CSI1000**（train 2016-01-02 ~ 2020-01-10）；**默认测试集 = csi1000 / csi300 / csi500**，其中 CSI1000 为研究主目标池。用同一个训练好的模型分别打分评估（跨池推理只需取数打分，无需重训）。
- **全A**：暂不纳入默认测试矩阵；若实验显式要求评估全A，剔除评估日距该股数据起始不足 60 个交易日的股票（次新股）；ST / 退市整理期一律按日频名单 `scripts/data_collector/tushare/st_daily.csv` 剔除（`eval_ic_multi_pool.py` 默认启用，缓存缺失则退出）。
- 上表中其余池的训练配置仅用于**训练样本类实验**（direction 如 `train-data`：更换训练池、调整训练起点、样本加权等）；此类实验须在 registry 中注明所用训练池，并与相同训练池的基线组对比。
- Phase S 默认在**研究主目标池**（当前 CSI1000）的连续全历史 `full` 段（2020-01-13 ~ 2026-07-31）执行比较与选型；`valid` / `test` 只供历史审计或复现，其余池作稳健性参考。所有 full 结果必须标注 `full_history_in_sample`，不得表述为样本外检验。当前实盘配置仍为 CSI300；研究目标池变更不自动修改实盘配置或 B1。

### 3.3 种子

Phase M 固定 5 个种子：`[42, 1000, 2000, 3000, 4000]`。不得增删或挑选种子；不得只报最优种子。CSI1000 历史轨道报告 5 种子指标均值与标准差。Phase M v1 官方主格是五种子**合成信号一次评估**，不是五种子指标均值；逐种子格子只作稳健性。Phase S 不重训、不重新选种子，统一使用 `backtest/models/baselines/` 中 manifest 已冻结的单一 artifact。

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

仓库内有两套 Phase M 口径，按轨道选用，**禁止混报**。统一计算入口都是 `backtest/scripts/eval_ic_multi_pool.py`，不得各自手写实现。

#### 5.1.1 历史轨道（CSI1000）：IC / RankIC

CSI1000 研究轨道的历史 Phase M 口径：test 段逐日截面 IC / RankIC 的时间均值，以及 ICIR / RankICIR（均值/标准差）。每个测试集先对 5 种子取均值。评测标签固定为默认 `Ref($close, -2)/Ref($close, -1) - 1`，**与训练标签无关**——这样不同标签设计的实验在同一把尺子下可比。该轨道报告入口仍是 `report.html`。

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

#### 5.1.2 现行全A 轨道：Phase M v1

现行全A / regime-adapt **模型迭代**使用本口径。统一计算入口仍是 `backtest/scripts/eval_ic_multi_pool.py`。**取消北极星**。主格：**五种子日截面 z-score 等权合成后再算 top3 × h5**。当前该轨道模型基线为 **v4**（第 1.5 节）。执行层回测仍是独立对照（第 1.6 节），不得与主格 ×238/h 直接相减。

合成方式由 `ensemble_preds.blend_score_series` / `eval_ic_multi_pool.official_head_from_preds` 计算，结果写入 eval JSON 的 `pools.<pool>.ensemble`。`seed_mean` 仍保留五种子指标算术平均，只作稳健性，**不得**作为总报告第 1～3 块或 registry 官方 `metrics`。

**主指标**（总报告第 1～3 块展示这些）：

| 优先级 | 指标 | 含义 |
|---|---|---|
| 主指标 | 扣费净年化 | 主格 top3×h5 头部等权绝对收益，扣费后按 ×238/h 年化 |
| 主指标 | 扣费波动 | 绝对收益序列 HAC 年化标准差 |
| 主指标 | 扣费夏普 | 净年化 / 波动 |
| 主表次列 | 非扣费年化 | 未扣交易成本的头部绝对年化 |
| 主表次列 | 日换手 | 见下方定义 |
| 主表次列 | 全局 RankIC | 官方合成信号、全宇宙、h5 标签的日 Spearman 均值（`ensemble.h5.rank_ic_mean`）；只进第 1 块，不铺进分年/分风格 |

**子维度**（一律只读主格 top3×h5，禁止再叉乘 k×h）：

- 总报告第 2 块 / 实验详细报告分年：2020–2026，每个指标一张表，列=年
- 总报告第 3 块 / 实验详细报告分风格：D / F / T，每个指标一张表，列=风格
- 全期网格 `k∈{1,2,3,4,5} × h∈{2,3,4,5}` 只可作详细 HTML 的全期稳健性表，**不得**铺进分年或分风格

**过滤**（评估宇宙；top-k 与等权基准同口径）：

1. ST 日频名单：`scripts/data_collector/tushare/st_daily.csv`（Phase M 评估默认启用，可用 `--st-daily` 覆盖路径；缓存缺失则退出）。来源 Tushare stock_st（按交易日，2017-01-03 起）+ namechange（区间展开，回溯至 1999，并用 stock_basic.delist_date 覆盖退市整理期）；同名含「退」的整理期股票一并剔除。回测与实盘发布查同一份缓存。
2. 成交额 ≥ 1000 万：本地无 `$amount`，用 `$volume × ($close/$factor) × 100`
3. 上市 ≥ 60 交易日
4. 另保留 t+1 涨停/零量剔除（`--exclude-limit-up`）；封板阈值与回测同一套 `market_cn`（主板 9.5%、科创板 19.5%、创业板 2020-08-24 起 19.5%）
5. **前视窗必须完整落在评估窗内**：标签在日 t 记入 t+1→t+h+1 的收益，故末端 h+1 个评估日的平仓日越出窗口，其收益在窗口内无法兑现，一律截断（`eval_ic_multi_pool.label_window_cutoff`，截断日写入 JSON 的 `label_window_cutoff` 供审计）。不截断会让评估偷看窗口外行情：2026-07 末 6 天（权重 4.3%）曾贡献当年头部收益的 77%，把 2026 净年化从 −0.2% 抬到 +38.0%，即主格与执行层回测 35pp 缺口的全部来源（v2.12 修复，见 LESSONS 2026-08-22）。

**日换手** = 相隔 h 日的单边换手 / h（h=5 全换仓 → 日换手 20%）。年化成本 = `238 × 日换手 × 0.092%`（买 0.021% + 卖 0.071%）。

训练：全A 长窗 2004-01-02 ~ 2020-07-31；评估窗常用 2020-08-03 ~ 2026-07-31；固定 5 种子 `[42, 1000, 2000, 3000, 4000]`。

**报告要求**：

1. 总报告 `phase_m_v1_report.html` 固定四块，由 `build_phase_m_v1_report.py` 生成，禁止手工编辑、禁止按 direction 分表：
   1. **历史 baseline**：每个曾晋升的模型基线一行，版本号 `v1`、`v2`、…（当前基线标「当前」）。主格五指标 + 全局 RankIC + 详细报告链接。
   2. **分年**：每个主指标一张表，行=baseline 版本，列=2020–2026；只读 top3×h5。
   3. **分风格**：每个主指标一张表，行=baseline 版本，列=D / F / T；只读 top3×h5。
   4. **历史实验记录**：一次实验一行（日期 / 名称 / 假设 / 详细报告链接），不含与 baseline 混排的指标对比。`state=archived` 或无 `phase_m_protocol=v1` 的行不进入此块。
2. 每个实验另有详细 HTML：主指标必须是 top3×h5；分年/分风格同样只报这一格。全期 k×h 网格可保留作稳健性，不得再叉乘进分年/分风格。registry 行须带 `phase_m_protocol: "v1"` 与 `detail_report`；baseline 行另须带 `baseline_version`。
3. 数字从 eval JSON 的 `ensemble`（无则回退 `seed_mean`）/ registry `metrics` 读取，禁止手写死数字。
4. 不得与 CSI1000 轨道的 IC/RankIC 或 Phase S 回测数字混排。无 `phase_m_protocol=v1` 的历史 regime-adapt 行不进入总报告。
5. 未按现行日频 ST 重评的侧翼模型（H1/H2/H3/H5/H10/H40、feat、sample 等）已归档，不得再写回总报告对比表。
6. 禁止用五种子指标均值当官方主格；也禁止拿 Phase M 主格净年化（×238/h）和执行层回测 CAGR / 算术年化并排当同一列。

#### 5.1.3 Phase M 头部 与 执行层回测：对齐规则（强制）

两条线**不是同一个组合**，任何跨线对比必须先做以下三步，否则数字无意义（2026-08-19 定，起因是 2025/2026 出现 50pp 量级的假缺口）：

| 维度 | Phase M 头部 | 执行层回测 |
|---|---|---|
| 收益含义 | **绝对**（头部等权 h 日前瞻收益） | **绝对**（账户净值） |
| 年化 | `mean × 238 / h`（算术，标签是 h 日收益） | 主数字 **CAGR**（累计净值 \(^{250/n}-1\)）；算术 `mean × 250` 只作审计 |
| 基准 | 过滤后可成交池等权（剔 ST/小额/新股/t+1 涨停） | `bench_ew_all.csv` 全池等权（不过滤） |
| 持有期 | 满 h 个交易日**到期必卖** | 当前 BT v4：满 `horizon=5` 天到期必卖（`CohortLadderStrategy`）；历史 BT v1–v3 是 TopkDropout 按打分卖 `n_drop` |
| 入场只数 | 每天等权 k 只 | 当前 BT v4：每天买入当日 top-k（k=3），允许同票多层；历史 v1–v3 每天大约 `n_drop` 只 |

两边都是绝对收益后，仍须对齐时间单位才能比数量级；**不得**把 Phase M 的 `×238/h` 和回测 CAGR（或算术 ×250）直接相减。JSON 里仍保留 `ann_excess` 作审计，报告不读。

即便组合规则已对齐（BT v4 = 主格 top3×h5 的执行层等价物），**主格 ×238/h 净年化仍不能用来预期回测 CAGR**：年化定义、基准宇宙、费率与可成交性不同。要预期某个执行层策略，只能跑那个策略本身。

### 5.2 Phase S（策略迭代）

**报告主展示口径**（与 `strategy_stability_report.html` baseline 表一致）：扣费绝对收益年化、夏普、Alpha、Beta、基准涨幅、卡玛、年化波动、最大回撤、年化单边换手。经 `strategy_stability_metrics` 汇总的回测（Phase M v1 执行层、regime Phase S）主年化为 **CAGR**，算术年化只作审计；夏普分子仍是算术年化。CSI1000 邻域表仍读 Qlib 既有字段，不在本次改写。

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
- 5 种子均值 + 成对胜出是 CSI1000 历史轨道当前成本下的最低置信要求；仍不足以支撑绝对收益承诺，只用于相对淘汰。Phase M v1 官方主格改为合成信号一次评估后，逐种子均值不再是官方数字。

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
- Phase M v1 行另须填写 **`phase_m_protocol: "v1"`**、`baseline_ref`（当前为 `regime-adapt/m0-h20-rankices-v1`，该基线行写 `self`）、`detail_report`，以及主格主指标（`net_ann` / `net_ann_vol` / `net_sharpe` / `ann` / 日换手 `turnover`；可选 `rank_ic_mean`）。baseline 行另须 `baseline_version`（现 v1 / v2 / v3 / v4）。无该标记的历史行不进入总报告第 1～3 块。历史实验的 `baseline_ref` 不改写。

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

- `backtest/experiments/report.html` 由 `backtest/scripts/build_experiment_report.py` 从 `registry.jsonl` 自动生成，是 **CSI1000 历史 Phase M（IC/RankIC）** 的规范入口；**不是 Phase S 的活动报告，也不是 Phase M v1 入口**。registry 仍是唯一数据源，禁止手工编辑 HTML。
- CSI1000 Phase M 报告顶部自动生成目录与指标说明；每个方向一张表，表格第一行固定为对应 baseline，指标为 4 项（RankIC / RankICIR / IC / ICIR）× 3 指数。带 `phase_m_protocol=v1` 的行不进入此报告。
- `backtest/experiments/phase_m_v1_report.html` 由 `backtest/scripts/build_phase_m_v1_report.py` 从 registry 生成，是 **Phase M v1 模型评估总报告入口**。固定四块：历史 baseline 版本表、分年表、分风格表、历史实验记录。禁止按 direction 分表，禁止手工编辑 HTML。每个实验另有详细 HTML。
- `backtest/experiments/phase_m_v1_bt_report.html` 由 `backtest/scripts/build_phase_m_v1_bt_report.py` 从 registry 生成，是 **Phase M v1 执行层回测总报告**。官方数字必须来自五种子均值信号单次回测（`report_kind=phase_m_v1_bt_baseline` / `phase_m_v1_bt`）。禁止与 CSI1000 `strategy_stability_report.html` 混排。
- `backtest/experiments/strategy_stability_report.html` 是**唯一活动的 Phase S 报告**，由 `backtest/scripts/build_strategy_stability_report.py` 从 registry 生成。每次 Phase S full-period 登记后必须重建它，并显著展示 `evaluation_mode: full_history_in_sample` 与非样本外声明。
- 无效实验也要登记并保留在相应报告中，避免重复试错。
- 历史报告 `build_benchmark_html.py` 仅作为规范生效前旧实验的存档，不再新增内容。

---

## 8. 标准执行流程（checklist）

CSI1000 轨道 Phase M 已以 B6-M 收尾。该轨道 Phase S checklist：

```
[ ] 1. 从 `backtest/models/baselines/<model-ref>/manifest.json` 校验单一冻结模型；生成并冻结 raw predictions（路径 + SHA + 精确覆盖）
[ ] 2. 以 B2-S 建立组内 baseline，并冻结费用/benchmark/回测配置
[ ] 3. 在 registry 预登记策略网格、CSI1000 full-period 选型指标和并列规则，并标记 `full_history_in_sample`
[ ] 4. 在 CSI1000 2020-01-13 ~ 2026-07-31 全历史连续区间统一比较 B2-S 与全部预登记候选并选型；不得称为样本外检验
[ ] 5. 齐报 full-period 扣费超额 IR/年化/最大回撤与扣费分年度 IR，并将 B2-S 对照和胜者一并登记
[ ] 6. 从 registry 重建唯一活动的 Phase S 报告 `strategy_stability_report.html`；提升获批后只保留当前 Phase S baseline 的 CSI1000 full-period 正式比较 session
```

Phase M v1（全A / regime-adapt）checklist：

```
[ ] 1. 开跑前写好 hypothesis；对照 baseline 为第 1.5 节当前 v4（未填入前对照历史 v3 · M0 H20 t3h5es）
[ ] 2. 全A 长窗训练（2004-01-02~2020-07-31），固定五种子；只改模型，不改策略
[ ] 3. 用 eval_ic_multi_pool.py 按第 5.1.2 节口径评估（主格 top3×h5；分年/分风格只报这一格，不叉乘 k×h；日频 ST + 成交额 + 上市 + 剔 t+1 涨停；官方合成信号须带 h5 RankIC）
[ ] 4. 登记 registry：phase_m_protocol=v1、baseline_ref、detail_report、主格主指标
[ ] 5. 重建 phase_m_v1_report.html（四块）与该实验详细 HTML；总报告第 1 块须含当前 baseline 版本（现为 v4）与全局 RankIC
[ ] 6. 若做执行层对照：五种子均值信号单次回测，对照当前 BT baseline（现为 BT v4），登记 report_kind=phase_m_v1_bt，重建 phase_m_v1_bt_report.html
```


---

## 附录 A：相关脚本

| 脚本 | 用途 |
|---|---|
| `backtest/scripts/run_backtest.py` | `train_only` 模型训练、显式 `train_backtest` 兼容模式与 `backtest_only` 入口 |
| `backtest/scripts/eval_ic_multi_pool.py` | **Phase M 统一评估入口**：CSI1000 轨道算 IC/RankIC；Phase M v1 算主格 top3×h5 扣费净年化/波动/夏普，以及官方合成信号的全局 RankIC（含全A 三过滤与 data_version） |
| `backtest/scripts/dump_regime_preds.py` | 从训练 session 推理 test pred 并合成官方信号（回测 / RankIC 补算） |
| `backtest/scripts/patch_official_rank_ic.py` | 给已有 eval JSON 补 `ensemble.h5.rank_ic_mean`，不重跑头部网格 |
| `backtest/scripts/promote_phase_m_v1_rankices.py` | 把 RankIC 早停官方合成信号晋升为模型基线 v4 |
| `backtest/scripts/promote_bt_v4_ladder_k3h5.py` | 把 v4 × 真阶梯 k3×h5 晋升为执行层基线 BT v4 |
| `backtest/scripts/eval_protocol.py` | daily_ic / summarize_ic / pairwise_win_count / yearly_ir |
| `backtest/scripts/run_pred_backtest.py` | 基于现成 pred 分数回测（Phase S 用） |
| `backtest/scripts/run_strategy_sweep.py` | 策略扫参（Phase S 用） |
| `backtest/scripts/ensemble_preds.py` | 多种子预测集成（截面 z-score 等权） |
| `backtest/scripts/build_experiment_report.py` | **registry.jsonl → CSI1000 历史 Phase M HTML**（`report.html`） |
| `backtest/scripts/build_phase_m_v1_report.py` | **registry.jsonl → Phase M v1 总报告**（`phase_m_v1_report.html`，四块：baseline 版本 / 分年 / 分风格 / 实验记录） |
| `backtest/scripts/build_regime_m0_label_report.py` | M0 训练标签期限实验的详细报告（主指标 + 网格/风格/分年） |
| `backtest/scripts/register_regime_m0_labels.py` | 从 eval JSON snap 主指标并 upsert Phase M v1 的 M0 H20（v1）行 |
| `backtest/scripts/cleanup_experiment_artifacts.py` | registry 驱动的 mlruns/result dry-run 与统一清理 |

## 附录 B：环境注意事项

- macOS 下禁止用 heredoc/stdin 运行会触发 Qlib 并行取数的代码，见 `.cursor/rules/qlib-shell-multiprocessing.mdc`。
- Python 解释器：`/opt/anaconda3/envs/qlib/bin/python`。
- 数据：`~/.qlib/qlib_data/cn_data`，跑实验前确认数据已更新到 `end_time` 之后。
