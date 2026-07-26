# Qlib 实验规范标准（EXPERIMENT_STANDARD）

版本：v1.4（2026-07-26）
状态：生效中
适用范围：本仓库内所有模型迭代与策略迭代实验（人工或 agent 执行）。
修改本文件需用户明确批准；agent 不得自行修改评测口径或时间划分。

---

## 0. 硬性约束（先读这里）

1. 当前基线（B1）固定，见第 1 节；任何新实验必须与 B1 对比。**每个实验方向必须写明对照的 baseline 版本**（registry 的 `baseline_ref`，当前为 `B1 v1.0`），HTML 该方向表格**第一行**为对应 baseline 指标行。
2. 模型与策略**分开迭代**：一期只改模型（Phase M），策略冻结为 B1-S；确定更优模型后才进入策略迭代（Phase S），此时模型冻结。
3. Phase M 看 **IC / RankIC**；Phase S 看**扣费超额 IR / 扣费超额年化 / 扣费最大回撤**。
4. 每个模型变体：**5 个固定种子，默认只在基线训练池（CSI1000）训练**（共 5 次训练），训练好的模型在 **3 个测试集**（csi1000/csi300/csi500）上评估 IC/RankIC，**研究主目标池为 CSI1000**。全A 暂不作为默认测试集（实验设计显式要求时再加）。仅训练样本类实验（更换训练池/起点/样本加权等）才使用其他训练池。
5. 时间划分固定（第 3 节）：测试集 2021-07-16 ~ 2026-07-16；评估集 2020-01-13 ~ 2021-07-15。**禁止用测试集调参**。
6. 每个实验必须登记到 `backtest/experiments/registry.jsonl`（配置路径 + 结果路径），并更新 HTML 报告（每个实验方向一张独立表格）。
7. **实验结束后必须同时清理 `mlruns/` 和 `backtest/result/`**（见第 6.3 节）。当前 Phase M 自动清理只保留模型 baseline 与超过它的最佳候选实验组，避免磁盘被打爆。

---

## 1. 基线定义（B1）

基线取自当前实盘配置 `live_trading/configs/csi300_topk10_live.yaml`，拆为模型基线与策略基线两部分。实盘对照回测的唯一合法配置是 `backtest/configs/csi300_live_parity.yaml`。

### 1.1 模型基线 B1-M

| 项 | 值 |
|---|---|
| 基线版本 | `B1 v1.0`（2026-07-25，由用户明确批准从 B0 提升） |
| 五种子基线组 | `train-data/csi1000-full-v2`，固定种子 `[42, 1000, 2000, 3000, 4000]` |
| 实盘单模型 | 五种子中仅按 valid RankIC 选出的 seed=2000；Git artifact `live_trading/models/b1_m/csi1000_full_v2_s2000_20260725/trained_model` |
| 来源 | `train_20260725_004255_td_csi1000_full_v2_lgbm_s2000_run01`（experiment_id=836973677275181001, recorder_id=7a7c592d3b764b62b78423e9b5009926） |
| 特征 | Alpha158（`qlib.contrib.data.handler.Alpha158`），handler start_time=2003-01-02 |
| 训练区间 | fit 2016-01-02 ~ 2020-01-10（csi1000 完整样本池） |
| 标签 | 默认 `Ref($close, -2)/Ref($close, -1) - 1` |
| 模型 | `qlib.contrib.model.gbdt.LGBModel` |
| 超参 | loss=mse, learning_rate=0.2, colsample_bytree=0.8879, subsample=0.8789, lambda_l1=205.6999, lambda_l2=580.9768, max_depth=8, num_leaves=210（即 qlib 官方 benchmark 参数，见现有 configs） |
| 数据处理 | infer_processors 含 ProcessInf，与实盘配置一致 |

五种子 test RankIC 均值：CSI300=0.02090、CSI500=0.02012、CSI1000=0.02885。实盘 seed=2000 的选模只使用 valid（2020-01-13 ~ 2021-07-15），valid RankIC=0.04955、RankICIR=0.54489；禁止按 test 结果改选种子。

### 1.2 策略基线 B1-S

| 项 | 值 |
|---|---|
| 策略 | `TopkDropoutStrategy(topk=10, n_drop=2, risk_degree=0.95, hold_thresh=1, only_tradable=false, forbid_all_trade_at_limit=false)` |
| 成交价 | close |
| 涨跌停限制 | limit_threshold=0.095 |
| 费率 | open_cost=0.00021, close_cost=0.00071, min_cost=5, trade_unit=100（按 QMT 2026-07-16 实际费用校准） |

注意：历史回测配置存在多套费率口径（如 0.0005/0.0015、0.0000954/0.0005954）。**本规范下所有策略回测统一采用上表实盘费率**，与历史结果对比时需注明费率口径。

### 1.3 历史基线 B0 v1.0

B0-M 为 CSI300、Alpha158、LGBM、fit 2006-01-02 ~ 2020-01-10；B0-S 与当前 B1-S 相同。历史实验的 `baseline_ref: B0 v1.0` 与指标继续保留，不改写历史对照关系。

### 1.4 基线变更流程

只有当某实验按本规范完成完整评估（第 4/5 节）、结果对比数据经用户确认后，才可将其提升为新基线；提升时在本文件更新当前基线定义并记录版本号与日期。agent 不得自行提升基线。

---

## 2. 迭代模式

```
Phase M（模型迭代）            Phase S（策略迭代）
改：特征/标签/模型/超参    →    改：策略类型/参数/调仓规则
冻结：B1-S 策略                冻结：Phase M 选出的最优模型
指标：IC / RankIC              指标：扣费超额 IR / 年化 / 最大回撤
                    ↑ 用户确认后切换 ↑
```

- Phase M 配置必须使用 `run.mode=train_only`，只训练并保存模型；**不得随模型训练自动运行策略回测**。如确需参考策略回测，必须在模型评估完成后使用冻结模型另行运行，且 B1-S 参数原样不变，结果不参与 Phase M 选型。
- Phase S 期间**不重训模型**：使用冻结模型的 5 种子预测分数（建议经 `backtest/scripts/ensemble_preds.py` 做截面 z-score 等权集成），在同一份分数上比较策略，用 `backtest/scripts/run_pred_backtest.py` / `run_strategy_sweep.py` 执行。
- 同时改模型和策略的实验结果**不予采信、不进 registry**。

---

## 3. 数据与时间划分（固定）

### 3.1 时间划分

| 分段 | 区间 | 用途 |
|---|---|---|
| 训练集 train | 见 3.2，止于 2020-01-10 | 拟合模型 |
| 评估集 valid | 2020-01-13 ~ 2021-07-15 | 早停、调参、中间筛选 |
| 测试集 test | 2021-07-16 ~ 2026-07-16 | 最终评估（禁止参与任何调参决策） |

handler 时间：`start_time=2003-01-02`，`end_time >= 2026-07-16`，`fit_start_time/fit_end_time` = 对应池的 train 区间。

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
- Phase S 默认在**研究主目标池**（当前 CSI1000）上执行，其余池作稳健性参考。当前实盘配置仍为 CSI300；研究目标池变更不自动修改实盘配置或 B1。

### 3.3 种子

固定 5 个种子：`[42, 1000, 2000, 3000, 4000]`。不得增删或挑选种子；报告必须给出 5 种子的均值与标准差，不得只报最优种子。

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

指标口径：qlib PortAnaRecord 的 `excess_return_with_cost`（1day）。

| 角色 | 指标 |
|---|---|
| 主指标 | 扣费超额 IR（information_ratio） |
| 副指标 | 扣费超额年化（annualized_return）、扣费最大回撤（max_drawdown） |

**报告要求**：在冻结模型的同一份预测分数上对比；三项指标齐报，并附分年度 IR（`eval_protocol.py: yearly_ir`）以确认不是单一年份驱动。

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

- 报告由 `backtest/scripts/build_experiment_report.py` 从 `registry.jsonl` **自动生成**（`backtest/experiments/report.html`，自包含单文件）。**registry 是唯一数据源**，禁止手工编辑 HTML；登记新行后重跑脚本即可。
- 报告顶部自动生成**目录**，并含 Phase M 指标说明（含义 + 关注优先级）。
- **每个实验方向一张独立表格**（一个 direction 一张表），由脚本按 registry 的 `direction` 字段自动分组。
- **每个方向必须明确 baseline 版本**：该方向内各实验的 `baseline_ref` 应一致；表格标题旁标注该版本，**第一行固定为对应 baseline 指标行**（从 registry 的 `direction=baseline` 锚点行注入；`baseline` 方向本身则以其锚点行置顶）。不得省略 baseline 行后直接罗列变体。
- 表格列精简为：**实验名**、**实验内容**（hypothesis）、**指标列**。Phase M 为 4 指标（RankIC / RankICIR / IC / ICIR）× 3 指数，**两行表头**（第一行指标、第二行指数）；Phase S 为扣费超额 IR/年化/最大回撤。同列最优值高亮。
- 无效实验也要登记并保留在表格中，避免重复试错。
- 历史报告 `build_benchmark_html.py` 仅作为规范生效前旧实验的存档，不再新增内容。

---

## 8. 标准执行流程（checklist）

```
[ ] 1. 读本文件，确认当前 Phase（M 或 S）与当前 baseline 版本；本方向 baseline_ref 写死为该版本
[ ] 2. 写实验假设与变体设计（即 registry 的 hypothesis 字段，开跑前定稿，不得事后修改）
[ ] 3. 生成 `run.mode=train_only` 配置（复用现有 config 模板，只改实验变量；时间/种子不得动）
[ ] 4. 基线训练池（CSI1000）× 5 种子仅训练，在默认 3 个测试集（csi1000/csi300/csi500）上打分评估
[ ] 5. 按第 5 节口径汇总指标，与当前 baseline 对比
[ ] 6. 登记 registry.jsonl（含 baseline_ref），并重跑 build_experiment_report.py 生成 HTML（确认表首行为 baseline）
[ ] 7. 先 dry-run、再按 6.3 `--apply` 清理 mlruns 与 backtest/result，确认只保留当前 baseline + 最佳合格候选
[ ] 8. 将对比数据报告用户，由用户决定是否采纳/提升基线；不自行改 baseline
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
