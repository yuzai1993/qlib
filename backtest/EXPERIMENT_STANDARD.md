# Qlib 实验规范标准（EXPERIMENT_STANDARD）

版本：v1.0（2026-07-24）
状态：生效中
适用范围：本仓库内所有模型迭代与策略迭代实验（人工或 agent 执行）。
修改本文件需用户明确批准；agent 不得自行修改评测口径或时间划分。

---

## 0. 硬性约束（先读这里）

1. 基线（B0）固定，见第 1 节；任何实验必须与 B0 对比。
2. 模型与策略**分开迭代**：一期只改模型（Phase M），策略冻结为 B0-S；确定更优模型后才进入策略迭代（Phase S），此时模型冻结。
3. Phase M 看 **IC / RankIC**；Phase S 看**扣费超额 IR / 扣费超额年化 / 扣费最大回撤**。
4. 每个模型变体：**5 个固定种子，默认只在基线训练池（CSI300）训练**（共 5 次训练），训练好的模型在 **4 个测试集**（csi300/csi500/csi1000/全A）上评估 IC/RankIC。仅训练样本类实验（更换训练池/起点/样本加权等）才使用其他训练池。
5. 时间划分固定（第 3 节）：测试集 2021-07-16 ~ 2026-07-16；评估集 2020-01-13 ~ 2021-07-15。**禁止用测试集调参**。
6. 每个实验必须登记到 `backtest/experiments/registry.jsonl`（配置路径 + 结果路径），并更新 HTML 报告（每个实验方向一张独立表格）。

---

## 1. 基线定义（B0）

基线取自当前实盘配置 `live_trading/configs/csi300_topk10_live.yaml`，拆为模型基线与策略基线两部分。实盘对照回测的唯一合法配置是 `backtest/configs/csi300_live_parity.yaml`。

### 1.1 模型基线 B0-M

| 项 | 值 |
|---|---|
| 实盘模型 | mlruns 实验 `train_20260711_223223_train_start_2006_run01`（experiment_id=265134483362085141, recorder_id=40a17c74aa7b4d97a4caa35015aaead5） |
| 特征 | Alpha158（`qlib.contrib.data.handler.Alpha158`），handler start_time=2003-01-02 |
| 训练区间 | fit 2006-01-02 ~ 2020-01-10（csi300 池） |
| 标签 | 默认 `Ref($close, -2)/Ref($close, -1) - 1` |
| 模型 | `qlib.contrib.model.gbdt.LGBModel` |
| 超参 | loss=mse, learning_rate=0.2, colsample_bytree=0.8879, subsample=0.8789, lambda_l1=205.6999, lambda_l2=580.9768, max_depth=8, num_leaves=210（即 qlib 官方 benchmark 参数，见现有 configs） |
| 数据处理 | infer_processors 含 ProcessInf，与实盘配置一致 |

注：实盘模型是单种子产物。做 Phase M 对比时，B0-M 指"用上表配置在基线训练池（CSI300）按 5 种子重训、并在 4 个测试集上打分得到的基线组"，而非直接复用实盘那一个 recorder。基线组只需跑一次，结果登记后供后续所有实验复用。

### 1.2 策略基线 B0-S

| 项 | 值 |
|---|---|
| 策略 | `TopkDropoutStrategy(topk=10, n_drop=2, risk_degree=0.95, hold_thresh=1, only_tradable=false, forbid_all_trade_at_limit=false)` |
| 成交价 | close |
| 涨跌停限制 | limit_threshold=0.095 |
| 费率 | open_cost=0.00021, close_cost=0.00071, min_cost=5, trade_unit=100（按 QMT 2026-07-16 实际费用校准） |

注意：历史回测配置存在多套费率口径（如 0.0005/0.0015、0.0000954/0.0005954）。**本规范下所有策略回测统一采用上表实盘费率**，与历史结果对比时需注明费率口径。

### 1.3 基线变更流程

只有当某实验按本规范完成完整评估（第 4/5 节）、结果对比数据经用户确认后，才可将其提升为新基线；提升时在本文件更新 B0 定义并记录版本号与日期。agent 不得自行提升基线。

---

## 2. 迭代模式

```
Phase M（模型迭代）            Phase S（策略迭代）
改：特征/标签/模型/超参    →    改：策略类型/参数/调仓规则
冻结：B0-S 策略                冻结：Phase M 选出的最优模型
指标：IC / RankIC              指标：扣费超额 IR / 年化 / 最大回撤
                    ↑ 用户确认后切换 ↑
```

- Phase M 期间**不做**策略扫参；策略仅作为参考回测（可选）时也必须用 B0-S 原样参数。
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
| 全A | `all` | 2006-01-02 | 2020-01-10 | SH000985（中证全指；如数据缺失，IC/RankIC 评估不受影响，策略回测需先确认基准可用） |

- **默认训练池 = 基线训练池 CSI300**（train 2006-01-02 ~ 2020-01-10）；4 个池均作为测试集，用同一个训练好的模型分别打分评估（跨池推理只需取数打分，无需重训）。
- **全A 测试集口径**：剔除评估日距该股数据起始不足 60 个交易日的股票（次新股）；ST 股在股票名称缓存可用时一并剔除（`eval_ic_multi_pool.py --st-names`），不可用时在结果中注明"未剔除 ST"。
- 上表中其余池的训练配置仅用于**训练样本类实验**（direction 如 `train-data`：更换训练池、调整训练起点、样本加权等）；此类实验须在 registry 中注明所用训练池，并与相同训练池的基线组对比。
- Phase S 默认在**实盘目标池**（当前 CSI300）上执行，其余池作稳健性参考。

### 3.3 种子

固定 5 个种子：`[42, 1000, 2000, 3000, 4000]`。不得增删或挑选种子；报告必须给出 5 种子的均值与标准差，不得只报最优种子。

---

## 4. 运行矩阵

一个模型变体的默认评估 = 基线训练池（CSI300）× 5 种子训练（5 次训练），训练好的模型在**全部 4 个测试集**上打分评估（仅推理，无需重训）。

- 除非实验设计中**事先明确**只评估特定测试集，否则不得省略任何测试集。
- 训练样本类实验：训练池由实验设计决定，种子数（5）与测试集（4）要求不变。

---

## 5. 指标与报告要求

暂不设固定通过门槛：agent 负责按统一口径产出对比数据，**是否采纳/提升基线由用户判断**；agent 不得自行宣布"通过"或修改基线。

### 5.1 Phase M（模型迭代）

指标口径：test 段逐日截面 IC / RankIC 的时间均值，以及 ICIR / RankICIR（均值/标准差）。每个测试集先对 5 种子取均值。

**统一计算入口**：所有 IC/RankIC 一律通过 `backtest/scripts/eval_ic_multi_pool.py` 计算（内部调用 `eval_protocol.daily_ic`），不得各自手写实现。评测标签固定为默认 `Ref($close, -2)/Ref($close, -1) - 1`，**与训练标签无关**——这样不同标签设计的实验在同一把尺子下可比。

| 角色 | 指标 |
|---|---|
| 主指标 | RankIC 均值（5 种子平均） |
| 副指标 | IC 均值、ICIR、RankICIR |

**报告要求**：
1. 每个测试集给出 5 种子均值 ± 标准差，以及相对 B0-M 的 Δ；
2. 附实盘目标池（CSI300）测试集上的逐种子成对比较结果（`backtest/scripts/eval_protocol.py: pairwise_win_count`）作为稳健性参考；
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

- 实验方向（direction）：短横线小写，如 `label-design`、`feature-ablation`、`model-arch`、`strategy-sweep`。
- 实验 ID（exp_id）：`<direction>/<变体名>`，如 `label-design/cum_h10`。
- 配置文件：放 `backtest/configs/`，文件名含 exp 变体与池、种子，如 `csi300_lgbm_cum_h10_s42.yaml`；配置头部注释写明 exp_id 与运行命令。
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
  "baseline_ref": "B0 v1.0",
  "seeds": [42, 1000, 2000, 3000, 4000],
  "train_pool": "csi300",
  "test_pools": ["csi300", "csi500", "csi1000", "all"],
  "data_version": "2026-07-16",
  "configs": ["backtest/configs/csi500_lgbm_ms_cum_h10_s42.yaml"],
  "result_dirs": ["backtest/result/20260718_122513_ms_cum_h10_s42"],
  "metrics_summary": {"csi500": {"rankic_mean": 0.061, "rankic_delta_vs_b0": 0.004}},
  "conclusion": "regress",
  "note": "RankIC 全测试集低于 B0，判负"
}
```

字段要求：
- **`hypothesis` 必填，且必须在实验开跑前写好**（改了什么、预期哪个指标为什么会变好）；事后只按该口径解读结果，防止"事后找亮点"。
- **`data_version` 必填**：填当时数据日历的最后交易日（`eval_ic_multi_pool.py` 输出中自动带出）。数据前复权重标定不改变 Alpha158 特征值（全部为比值形态），但历史修正/补数会轻微改变截面构成，此字段用于事后解释不同时间实验结果的差异，无需做数据快照。

### 6.3 清理

- 判负实验：清理 mlruns 中模型与预测大文件，保留 `metrics.json`、registry 行、配置文件。
- 通过实验：保留完整 artifact（模型、pred.pkl、报告）。

---

## 7. HTML 报告规范

- 报告由 `backtest/scripts/build_experiment_report.py` 从 `registry.jsonl` **自动生成**（`backtest/experiments/report.html`，自包含单文件）。**registry 是唯一数据源**，禁止手工编辑 HTML；登记新行后重跑脚本即可。
- 报告顶部自动生成**目录**（各实验方向的锚点链接）。
- **每个实验方向一张独立表格**（一个 direction 一张表），由脚本按 registry 的 `direction` 字段自动分组。
- 指标列由 `metrics_summary` 按测试集展开；Phase M 填 RankIC/IC/RankICIR 及 Δ vs B0，Phase S 填扣费超额 IR/年化/最大回撤及 Δ vs B0-S。
- 无效实验也要登记并保留在表格中（`conclusion` 标注），避免重复试错。
- 历史报告 `build_benchmark_html.py` 仅作为规范生效前旧实验的存档，不再新增内容。

---

## 8. 标准执行流程（checklist）

```
[ ] 1. 读本文件，确认当前 Phase（M 或 S）与 B0 版本
[ ] 2. 写实验假设与变体设计（即 registry 的 hypothesis 字段，开跑前定稿，不得事后修改）
[ ] 3. 生成配置（复用现有 config 模板，只改实验变量；时间/种子/费率不得动）
[ ] 4. 基线训练池（CSI300）× 5 种子训练，在全部 4 个测试集上打分评估
[ ] 5. 按第 5 节口径汇总指标，与 B0 对比
[ ] 6. 登记 registry.jsonl，并重跑 build_experiment_report.py 生成 HTML
[ ] 7. 将对比数据报告用户，由用户决定是否采纳/提升基线；不自行改 B0
```

---

## 附录 A：相关脚本

| 脚本 | 用途 |
|---|---|
| `backtest/scripts/run_backtest.py` | 训练 + 回测主入口 |
| `backtest/scripts/eval_ic_multi_pool.py` | **Phase M 统一 IC/RankIC 跨池评估**（含全A过滤与 data_version 输出） |
| `backtest/scripts/eval_protocol.py` | daily_ic / summarize_ic / pairwise_win_count / yearly_ir |
| `backtest/scripts/run_pred_backtest.py` | 基于现成 pred 分数回测（Phase S 用） |
| `backtest/scripts/run_strategy_sweep.py` | 策略扫参（Phase S 用） |
| `backtest/scripts/ensemble_preds.py` | 多种子预测集成（截面 z-score 等权） |
| `backtest/scripts/build_experiment_report.py` | **registry.jsonl → 标准实验 HTML 报告**（含目录，唯一渲染入口） |
| `backtest/scripts/build_benchmark_html.py` | 旧实验存档报告（规范生效前，只读） |

## 附录 B：环境注意事项

- macOS 下禁止用 heredoc/stdin 运行会触发 Qlib 并行取数的代码，见 `.cursor/rules/qlib-shell-multiprocessing.mdc`。
- Python 解释器：`/opt/anaconda3/envs/qlib/bin/python`。
- 数据：`~/.qlib/qlib_data/cn_data`，跑实验前确认数据已更新到 `end_time` 之后。
