# AI 实验 Harness（操作协议 v1）

状态：v1 已实现并验证；2026-11-01 用户选择并批准轻量版。本次搭建未启动研究实验。

## 目的与边界

用户提出研究方向，AI 将其转成可证伪假设，在有限预算内设计、执行、评估、反思和继续试验，最后交付可追溯的结论、正式登记、报告与清理证据。新会话可以依据持久化记录继续，不靠聊天记忆恢复。

在本仓库直接提出方向即可，例如：“使用实验 harness 检验缩短训练标签是否改善全A模型，沿用当前基线，只改标签，最多三个候选。”AI 负责准备合同、运行命令和登记材料，用户无需手写下面的 JSON。续跑时说明原方向或 campaign；AI 先查找原状态，不能新建账本假装没有历史成本。

实验口径的唯一事实来源仍为 [EXPERIMENT_STANDARD.md](../../backtest/EXPERIMENT_STANDARD.md)。本文件只规定执行流程，不增加收益通过门槛、不修改评测窗口，也不授予基线晋升或生产操作权限。当前状态以仓库规则及最新明确的基线选择事件为准，不把最高版本号当当前版本。

2026-11-01 这次搭建任务只实现 harness，不启动研究、不创建虚假实验或登记事件、不清理已有研究产物。后续用户提出研究方向并授权执行时，按以下协议开展真实实验。

## 已选择的实现方式

- **轻量技能 + 进度记录（用户选择）**：AI 负责科学推理和调用现有工具；一份很薄的 Python 工具负责计划、指纹、预算和进度合法性检查。复用现有训练、评估、登记、报告和清理能力。
- 未选择独立后台调度器或只写操作说明；不另造训练器、回测器或指标算法。

第一版由当前 AI 会话驱动。状态文件和检查工具不提供后台常驻、沙箱隔离或对任意命令的强制拦截；不能把操作约定宣传成不可绕过的执行保障。

## 交付范围与落点

所有正式变更控制在以下位置，不新增顶层业务目录，不迁移平台或实验归档：

1. `.agents/skills/qlib-experiment-harness/SKILL.md`：项目技能入口；这是本轮唯一新增的技能目录。简短说明触发条件，并要求读取本协议和实验标准。
2. `docs/research/experiment_harness.md`：本操作协议，维护流程、状态说明、入口和后续注意事项。
3. `backtest/scripts/maintenance/experiment_harness.py`：计划、状态和预算检查 CLI，使用标准库和现有 registry/path 模块；不执行任意 shell、不计算研究指标、不直接登记、晋升或删除文件。
4. `tests/backtest/test_experiment_harness.py`：验证身份漂移、状态转换、预算、失败恢复和越界输入等行为。
5. `AGENTS.md` 与 `backtest/scripts/README.md`：只增加简短入口说明。
6. 兼容修复沿用既有 `build_phase_m_v1_bt_report.py` 及其测试：失败/中断/无效/取消策略没有评估文件时仍能生成真实状态报告，重试后读取最新登记。它是失败收尾所需的现有渲染分支，不新增模块或目录。

不修改 `EXPERIMENT_STANDARD.md`。未来若确实需要新的正式模块、平台改造或额外结构，先说明具体改动并由用户同意，不把一次实验顺带扩成基础设施项目。

## 一次研究方向的运行流程

### 1. 定义任务

AI 先读当前规范、相关历史实验和教训，确定研究轨道、唯一改动轴、对照和允许的输入。输出简短计划：假设与机制、候选范围、对照配置、指标解释、选型规则、预算和停止条件。

用户已经提出的方向及运行授权允许 AI 在该范围内自主细化方案和重试，无需每轮反复申请开跑。缺少会改变研究含义的信息、需扩大范围或突破规则时才提问。

当前默认接入全A模型研究及独立执行层策略研究，分别以模型 v2 与策略 s5 为对照；实际运行仍从当前登记核对并冻结。旧 CSI1000 轨道只保留协议识别，其退役模型/预测输入缺失时必须拒绝启动，不寻找实盘或历史 session 替身。

### 2. 开跑前冻结

把任务合同写入首个真实候选实验的 `configs/harness-plan.json`，进度写入其 `docs/harness-state.json`，不另设第二份正式实验 registry。后续候选仍各自归入现有模型/策略目录，状态记录其稳定 ID 和实际档案路径。

计划与初始化状态合计冻结：方向原文、campaign ID、轨道、对照稳定 ID 与事件指纹、允许/禁止改动、假设、指标和判读规则、固定种子、训练与评估范围、数据版本、规则/相关源码/配置/模型/预测指纹、任务预算、临时目录及负责人标识。工具必需字段见下文；研究细节、负责人和临时目录可写入额外计划字段或列入 `inputs` 的冻结配置/文档，AI 须核实这些内容，工具不会推断其科学语义。

每轮候选运行前冻结本轮计划；先前计划、失败和反思只追加，不事后改写以适配结果。配置变化产生新的科学变体；相同配置的技术重试保留原变体和新的 attempt ID。状态更新使用原子写与独立锁，状态/合同损坏或并发冲突必须报错。

### 3. 执行与核验

AI 调用既有正式入口，默认串行执行昂贵训练。先记录启动意图、命令、输入身份、日志和预期输出，再启动子进程；中断恢复先核对子进程及已有输出，不能看到文件就跳过或重跑。

- 全A当前 v2 训练须显式填写 H20、single、`top5_h5_net_ann`、`eval_window` 等冻结参数；不沿用入口的 H40/RankIC 默认值。实验改动轴之外的参数保持不变。
- 模型实验固定五种子。核对五个成功模型、预测覆盖与索引、配置和数据身份一致；官方数字取五种子日截面 z-score 等权合成信号的一次评估，不用种子指标均值替代。
- 官方评估使用 `eval_ic_multi_pool.py`；全A主格 Top3×H5、分年/分风格、全局 h5 RankIC、过滤与标签窗口截断遵循实验标准。
- 执行层只比较冻结模型/信号下的策略改动，按 s5 对照的账户、费用和窗口解释 CAGR、夏普、回撤、Alpha/Beta 等，不与模型主格算术年化直接相减。
- 目录存在或退出码为零不构成完成证据；缺种子、非有限主指标、缺官方 ensemble、协议不一致分别记录为无效或失败，不填零、不伪造结论。

### 4. 评估、反思与下一轮

每个候选结束后，先保存可移植指标、配置指纹和技术失败摘要，正式追加登记并重建对应详情/总报告，再决定下一轮，避免整个方向中断后漏记失败。

AI 每轮说明：观察到了什么、原假设是否得到支持、证据限制、失败属于研究结果还是执行问题、下一轮只改什么以及该改动为何可能带来新信息。没有改善不等于机械扫更多参数，改善也不等于已获准晋升。

CSI1000 Phase M 若未来获准恢复：下一轮只能依据 train/valid；正式 test 暴露后不得据此生成新科学变体。可以预先冻结整批候选后统一评估，技术修复仅重试同一方案。全A现有评估窗参与历史早停和选型，每轮持续标记 `historical_selection_window`；CSI1000 Phase S 则为 `full_history_in_sample`，不能声称独立样本外。

### 5. 有限迭代与停止

以下是第一版的默认任务预算，属于 harness 的运行限制，**不是实验规范的通过门槛**；用户可在初始化前覆盖，恢复会话不重置预算：

- 最多 3 轮、4 个不同科学候选；策略网格中的每个不同参数配置都计入候选数，不能将大网格算成一个。
- 每个候选最多 2 次技术尝试；模型每次正式尝试仍须完整五种子，部分完成只用于恢复同一次尝试。
- 最多 8 小时探索时间；从 `init` 的时间戳累计墙钟时间，聊天中断也不暂停。开始下一候选前评估剩余时间，并为登记、报告和清理保留时间。这是开跑前检查的软预算，不会终止已运行进程，也不承诺强杀失联进程。
- 默认昂贵任务并发为 1；连续 2 轮没有新增有效证据则停止扩展，解释哪些假设被检验、哪些仍未检验。

满足预算上限、没有范围内可证伪的新假设、重复技术故障、输入/基线/规则漂移、用户要求停止或需要越权变更时，都应停止继续开跑并进入收尾。分别记录 `budget_exhausted`、`hypotheses_exhausted`、`execution_blocked`、`user_stopped` 等原因；预算耗尽不能写成“已证明方向无效”。

自主决策只覆盖是否继续研究及候选建议。修改规范、提升模型/策略基线、改变当前基线选择、切换生产、触发实盘或扩大基础设施范围仍须用户明确同意。

## 进度、恢复与最小检查工具

CLI 仅管理合同和进度，不执行命令、计算指标、追加正式 registry、生成报告或删除研究产物。下面的命令和 JSON 均为操作模板；`example/h10-probe`、对应档案、配置、证据和 `/tmp/qlib-harness-example/` 只用于说明，未创建真实实验，不能原样向真实 registry 写示例。实际运行前替换为获准方向中的真实新候选及其现存文件，不把当前基线档案用作可修改的示例锚点。

### 初始化计划

下列为最小计划形状，保存在真实候选的 `configs/harness-plan.json`。该候选须已有符合 [归档约定](../../backtest/experiments/README.md) 的 `metadata.json`，其中 `entity_type=experiment`、`kind=model`、`track=all_a`，且 `exp_id/archive_path` 与计划相同；参数组不能充当候选。

```json
{
  "schema_version": 1,
  "campaign_id": "all-a-horizon-example",
  "idea": "固定当前 v2 其余配方，仅把训练标签 H20 改为 H10，检验头部信号稳定性",
  "track": "all_a_model",
  "direction": "training-target",
  "anchor_exp_id": "example/h10-probe",
  "archive_path": "backtest/experiments/models/training-target/example-h10",
  "baseline_ref": "regime-adapt/m0-h20-t5h5-es-v1",
  "allowed_changes": ["label_horizon"],
  "selection_rule": "historical_selection_window；比较官方 ensemble 的 Top3×H5、分年/分风格和 h5 RankIC，不自动晋升",
  "data_version": "2026-07-31",
  "inputs": [
    "backtest/scripts/train/train_regime_arm.py",
    "backtest/scripts/evaluate/eval_ic_multi_pool.py",
    "backtest/experiments/models/training-target/example-h10/configs/frozen-candidate.json"
  ],
  "seeds": [42, 1000, 2000, 3000, 4000],
  "budget": {}
}
```

`data_version` 必须换成实际数据版本；`frozen-candidate.json` 是实际候选的配置证据，须包含完整五种子、窗、早停与评估约束。`inputs` 列出全部相关规则外源码、配置、数据清单/缓存、模型和预测文件，不能只照抄上述三项。工具指纹化显式普通文件，不递归哈希目录，不从 `data_version` 推断数据身份；允许仓库内相对路径或明确的绝对文件路径，拒绝 `..`、符号链接、缺失输入。计划/状态/档案必须在本仓库的正确位置。

轨道仅支持 `all_a_model` 和 `all_a_strategy`。后者档案须为 `kind=strategy`、路径在 `strategies/`，对照来自最新明确 `current_bt` 的 s5 稳定 ID `baseline/phase-m-v1-bt-v5`；不是重选模型。CSI1000 轨道明确拒绝初始化。工具自动冻结当前基线事件、AGENTS、实验标准、本协议及显式输入；合同 schema 当前为 `1`。`budget: {}` 使用上文默认值，可覆盖字段为 `max_rounds`、`max_variants`、`max_attempts_per_variant`、`max_wall_hours`、`max_no_new_evidence_rounds`、`concurrency`；第一版只接受 `concurrency=1`。

```bash
/opt/anaconda3/envs/qlib/bin/python /Users/yuxianqi/Project/qlib/backtest/scripts/maintenance/experiment_harness.py \
  --repo-root /Users/yuxianqi/Project/qlib init \
  --plan /Users/yuxianqi/Project/qlib/backtest/experiments/models/training-target/example-h10/configs/harness-plan.json

/opt/anaconda3/envs/qlib/bin/python /Users/yuxianqi/Project/qlib/backtest/scripts/maintenance/experiment_harness.py \
  --repo-root /Users/yuxianqi/Project/qlib check \
  --state /Users/yuxianqi/Project/qlib/backtest/experiments/models/training-target/example-h10/docs/harness-state.json

/opt/anaconda3/envs/qlib/bin/python /Users/yuxianqi/Project/qlib/backtest/scripts/maintenance/experiment_harness.py \
  --repo-root /Users/yuxianqi/Project/qlib status \
  --state /Users/yuxianqi/Project/qlib/backtest/experiments/models/training-target/example-h10/docs/harness-state.json
```

- `init` 只创建一次状态，初始 `revision=0`；不会覆盖已有进度。预算截止时间在这里固定，不能重新 `init` 或换 campaign 绕过已用预算。
- `check` 验证状态完整性、活动状态、规则/计划/输入/基线/候选配置身份、时间及连续无新证据限制；每次外部开跑前调用。轮次、候选数、attempt 数分别在对应事件时检查。
- `status` 检验状态文件身份和校验和，返回账本；它跳过漂移和开跑预算检查，便于受阻后读取原事实，成功返回不代表允许开跑。
- 成功时输出完整 JSON，异常退出码为 `2`。状态损坏、并发写入或旧 revision 必须报错；状态旁的独立锁仅保护状态写入，不管理外部进程。

同一 `campaign_id` 在模型/策略档案中只能初始化一次，换档案路径也会拒绝。第一版不提供中途扩预算命令；若用户明确要求继续超出原上限的研究，先如实收尾原任务，再确认新的任务预算、建立关联原 campaign/状态路径的新合同，完整保留原成本和结论。新聊天本身不是预算变更授权，不能把这类继续研究包装成原任务未消耗预算。

### 追加事件与 revision

每次将**一个 JSON 对象**写入本任务仓库外临时事件文件，再调用；以下 `0` 仅适用于初始化后第一个事件：

```bash
/opt/anaconda3/envs/qlib/bin/python /Users/yuxianqi/Project/qlib/backtest/scripts/maintenance/experiment_harness.py \
  --repo-root /Users/yuxianqi/Project/qlib event \
  --state /Users/yuxianqi/Project/qlib/backtest/experiments/models/training-target/example-h10/docs/harness-state.json \
  --event /tmp/qlib-harness-example/event.json --expected-revision 0
```

采用上次返回状态的 `revision`；每次成功事件加一。遇到 stale revision 时重读 `status`，核对其他操作者已写事件及实际进程，重做决策；不能只把数字加一重放启动。事件追加和状态替换在锁内完成，不能手改状态、校验和或冻结计划。

首个候选也须显式 `plan_variant`；`changed_axis` 必须是计划 `allowed_changes` 中的一项。下面各代码块分别是一份事件，不是一次提交的数组：

```json
{
  "type": "plan_variant",
  "exp_id": "example/h10-probe",
  "archive_path": "backtest/experiments/models/training-target/example-h10",
  "round": 1,
  "changed_axis": "label_horizon",
  "hypothesis": "仅缩短训练标签至 H10，预期减少头部信号跨年波动",
  "configs": ["backtest/experiments/models/training-target/example-h10/configs/frozen-candidate.json"]
}
```

`configs` 必须属于该候选自己的 `configs/`，工具冻结其指纹。声明改动轴不等于验证实际差分，AI 仍须检查完整配置。

```json
{
  "type": "start",
  "exp_id": "example/h10-probe",
  "command": [
    "/opt/anaconda3/envs/qlib/bin/python",
    "/Users/yuxianqi/Project/qlib/backtest/scripts/train/train_regime_arm.py",
    "--arm", "m0", "--weights", "m0", "--seed", "42", "--model", "single",
    "--label-horizon", "10", "--es-metric", "top5_h5_net_ann", "--es-valid", "eval_window",
    "--session-name", "example_h10_s42"
  ]
}
```

`start` 再次检查开跑门禁，只登记 argv，不执行它；返回的 `attempt_id` 形如 `all-a-horizon-example/example/h10-probe/a1`，以后从返回状态读取，不能凭记忆填写。本例 argv 只是一次完整模型尝试中的首个种子；实际外部编排还须保存全部五种子与评估命令、日志、进程身份和预期输出，可用 `note` 事件记录非空 `text`。不得为每个种子另计一次技术尝试，也不能在尚有 `running` attempt 时另发 `start`。

```json
{
  "type": "result",
  "exp_id": "example/h10-probe",
  "outcome": "succeeded",
  "evaluation": "backtest/experiments/models/training-target/example-h10/metrics/evaluation-a1.json",
  "evidence": ["backtest/experiments/models/training-target/example-h10/metrics/evaluation-a1.json"]
}
```

`result.succeeded` 是执行状态，必须指定 `evaluation`，并把同一文件列入 `evidence`；全部证据须持久保存在该候选档案内。工具读取 JSON 并检查：

- 模型：`sessions` 恰好包含五个不同 `session`，`seed` 覆盖固定五种子，`data_version` 匹配；`pools.all.ensemble.head["3"]["5"]` 的 `net_ann/net_ann_vol/net_sharpe/ann/turnover` 及 `pools.all.ensemble.h5.rank_ic_mean` 为有限数值，主格 `n_days` 为正整数。
- 策略：`seeds` 为固定列表，`model_ref` 等于初始化时当前策略基线的模型引用，`evaluation_mode=historical_selection_window`；`ensemble.full_period` 的 `annualized_return/sharpe_ratio/max_drawdown/alpha/beta/benchmark_cumulative_return` 有限，`n_days` 为正整数。

这些是结构与身份检查，不重新计算指标或查验真实模型成功；任意日志也不能替代官方评估。AI 必须核对真实五种子产物、预测覆盖、合成方式和协议一致性后才可登记 `completed`；`regress` 等研究结论另写正式事件，任何成功标记均不授予晋升权限。其他 outcome 为 `failed`、`interrupted`、`invalid`、`cancelled`，均需非空 `reason` 与现有证据，例如档案内 `docs/attempt-a1-failure.json`。每次 attempt 用独立、稳定的证据文件名，重试保存 `evaluation-a2.json` 等新文件，不能覆盖失败日志或旧评估；收尾会重新校验旧证据指纹。尚未启动的计划可直接以 `cancelled` 留痕，不能悄悄移除；部分种子恢复同一次运行须先核实原进程及输出，确需新的技术重试时先结束并登记、报告原 attempt。

正式登记必须由现有 `append_registry` 完成，`registered` 事件不代写 registry。每个 attempt 的最新正式事件须匹配冻结的 `hypothesis`、`baseline_ref`、`data_version`，包含实际 `detail_report` 和以下回执字段；这是字段片段，不能当作完整事件写入：

```json
{
  "harness": {
    "campaign_id": "all-a-horizon-example",
    "attempt_id": "all-a-horizon-example/example/h10-probe/a1"
  },
  "evaluation_mode": "historical_selection_window",
  "portable_eval_output": "backtest/experiments/models/training-target/example-h10/metrics/evaluation-a1.json"
}
```

成功事件须为 `state=completed` 并列完整五种子，`evaluation_mode=historical_selection_window`，`portable_eval_output`（优先）或 `eval_output` 指向该 attempt 保存且指纹相同的评估；其他执行结果只能登记失败/中断/无效/取消状态，无指标时不伪造该路径。全A模型事件须 `phase_m_protocol=v1`，执行层事件须 `report_kind=phase_m_v1_bt`；不能附带 `current_baseline/current_bt/current/is_current_baseline=true` 或非空 `baseline_version/bt_version/promoted_to`。工具核对该 `exp_id` 的最新实际登记，旧 attempt 或旧事件不能抵新回执。

```json
{"type": "registered", "exp_id": "example/h10-probe"}
```

```json
{"type": "reported", "exp_id": "example/h10-probe"}
```

先真正重建详情及该轨道总报告，再发 `reported`。它要求登记回执仍是最新事件、两份 HTML 非空且总报告包含正确详情链接；不检查每个数字或失败文字，内容和官方来源仍由 AI 核验。

```json
{
  "type": "reflection",
  "round": 1,
  "observations": "按已保存的正式评估与对照逐项填写观察及局限",
  "interpretation": "区分科学判负、执行故障与证据不足，不把选型窗称为独立样本外",
  "new_evidence": true,
  "next_hypothesis": ""
}
```

上述文字必须替换为真实反思；`new_evidence` 表示新增有效证据，不表示收益改善。该轮全部 attempt 登记、报告完毕才能反思；反思后该轮冻结，下一轮编号递增。同配置重试仍为同一 variant 的新 attempt，成功或取消的 variant 不能重新启动。

```json
{"type": "stop", "reason": "hypotheses_exhausted"}
```

`stop` 固定停止原因并进入 `stopping`，不能继续探索；其他合法原因为 `budget_exhausted`、`execution_blocked`、`user_stopped`。先确认相关进程全部结束，再执行下文真实清理，保存 JSON 审计。例如只有实际完成这些步骤后才可写 `{"result":"no_changes","mlruns":"not_applicable","temporary":"cleaned"}`；`mlruns=not_applicable` 必须有真实依据，不能因为 `--only-result` 不处理它就声称无需处理。

```json
{
  "type": "cleanup",
  "outcome": "complete",
  "audit": "backtest/experiments/models/training-target/example-h10/docs/harness-cleanup.json"
}
```

完成审计要求 `result=applied/no_changes`、`mlruns=handled/not_applicable`、`temporary=cleaned`。受阻则用 `outcome=blocked`、非空 `reason` 和反映待办的真实审计；临时文件仍在使用时允许 `temporary=blocked`，不能谎报已删。工具不会执行或独立复核删除，也不会发现其他任务的进程。

```json
{"type": "close"}
```

全部候选 attempt 都须终态、登记和报告完毕，且有 `stop`、清理回执后才可关闭。关闭前重新校验各 attempt 证据、历史登记是否仍存在、最新登记回执、最新详情与总报告链接及清理审计指纹。正式登记更新会使旧报告回执失效，并要求重新核验清理。清理完成得到 `closed`，受阻得到 `closed_with_pending_cleanup`，后者必须交付待办，不能宣称全部收尾完成；关闭后不可再追加事件，若要在本 campaign 继续处理清理，先保持 `stopping`。

### 漂移与恢复

状态记录 campaign、round、variant、attempt 四层身份，关联真实实验 ID；每次事件包含时间、证据路径/指纹、前序关系和下一步。研究结论与运行状态分开；`regress` 是研究结果，`failed` 是执行失败，缺证据则不能标成已完成。

检查工具负责输入路径边界与指纹、必须字段、当前规则/基线身份、预算不重置、合法状态前置条件、重复启动与并发写入保护；不能识别声明之外的参数变化或任意文件中的科学结论。正式指标和事件仍由现有评估器/registry 决定。报告和清理操作由单一编排者串行处理；其他代理可做独立设计审查或只读核验。

恢复必须重读规则、合同、`status`、正式 registry 与本任务进程状态。进程存活、PID 复用、日志与产物身份、部分种子能否续跑均由 AI 在工具外核对，不把 `running` 自动判成仍存活，也不把文件存在判成成功。必要输入、规则或基线漂移时 `check`、新计划和 `start` 拒绝通过；时间或无证据预算到限同样停止新开跑。保持原账本，仍可追加 `result/registered/reported/reflection/stop/cleanup/close/note` 完成旧 attempt 的真实收尾，不重写冻结协议来迎合新输入。不能自动重建已退役输入；完成预算、已失败尝试和已暴露的 test 状态都随进度保留。

全A正式入口沿用 `train/train_regime_arm.py`、`evaluate/eval_ic_multi_pool.py`、需要时 `evaluate/dump_regime_preds.py` 与独立的免重训策略入口，再调用对应报告生成器。必须核对入口实际参数：训练默认 H40/RankIC，不等于当前 v2 配方；`run_regime_phase_s.py` 的固定 `ARMS/STRATEGIES`、千万账户、默认旧策略及跳过已有输出也不能充当新方案或 s5 的隐式配置。只使用明确支持且已冻结的配方，不因 harness 接受 argv 就认定命令正确；缺适用入口时按本协议在仓库外适配或提出具体正式改动，不扩建基础设施。

## 登记、报告与清理

每个实验（包括失败和中止）通过 `register/experiment_registry.py:append_registry` 追加正式事件；已冻结详情不覆盖。失败没有指标时保留原因和现有证据，不为了凑完整结果填造数字。对应报告必须能看出该实验未完成或无效。

使用既有报告生成器；历史写死配方的 `register_*` / `promote_*` 和报告 `--spec` 不能直接当新实验的通用配方。第一版若缺少适用详细报告入口，复用现有渲染函数并在仓库外任务目录运行适配脚本，保留最终 HTML，收尾删除适配脚本；反复出现同样适配需求时再提议正式化，不为每次实验新增仓库工具。

方向报告说明尝试范围、全部候选和失败、对照差异、分年/分风格局限、下一步建议、停止原因与未完成事项；登记和 HTML 的官方数字必须来自正式指标。方向总结保存在首个真实实验的 `docs/` 中，其他候选由稳定 ID 和路径关联。

清理顺序固定：保存必要证据 → 登记全部终态 → 重建/核验报告 → 确认所有相关运行已结束 → dry-run → 核对并保存精简审计 → 按同一模式执行 → 清理本任务临时材料 → 核验 Git 状态和引用。

当前全A平台 `result/` 使用 `cleanup_experiment_artifacts.py --result-retention platform --only-result`；它保护全部平台维护实验及现存必要依赖，不等于删除所有落选实验，且不处理 `mlruns/`。不隐式调用旧CSI默认模式；没有适用的 MLflow 安全清理方案时明确记录该项受阻，不谎称全部清理完成。存在其他活动研究任务或清理门禁错误时拒绝执行全局产物删除，不换用手工批量删除。

一次性工具只放仓库外专用临时目录，创建时登记用途和路径；删除前检查引用，只清理本任务确知的材料。保留配置、正式指标、登记、HTML、精简审计及规范保护的模型/预测，不保留无后续用途的脚本、fixture、备份和工作副本。

## 验收与后续演进

第一版在隔离的临时测试目录验证，不用真实训练验证 harness：正常迭代、判负后调整、预算停止、失败/中断恢复、规则/配置/基线漂移、退役输入拒绝、并发/重复启动、未登记或报告未完成时拒绝完成、清理受阻时保留待办。

静态验证技能 frontmatter、文档链接、CLI 帮助和相关测试；用独立代理走读几种失败场景。不得为测试向真实 registry 写样例事件。最后执行 `git diff --check`、`git status` 并删除本任务临时材料。

2026-11-01 首版验证：37 项 harness 生命周期测试及相关登记、路径、报告、产物清理、退役输入、策略分组和脚本布局测试，合计 317 项通过；技能 frontmatter、CLI 帮助、独立恢复场景演练通过。另只读校验了当前 v2/s5 登记及现存官方评估文件的结构兼容性，没有训练、登记样例或重建真实报告。独立审查发现的收尾回执失效、清理审计漂移、失败策略出报告、临时清理受阻记录四项问题均已加入回归测试并修复。

本次搭建的临时目录 `/tmp/qlib-harness-build.3Kv1pO`、已确认归本任务所有的 pytest 工作副本及专用字节码已删除；必要结论与验证摘要保存在本协议，正式测试保留在测试目录。该临时路径只作已完成清理的来源记录，不是运行依赖。

新注意事项优先补在本协议并记录日期和缘由；新规则影响合同语义时提升合同版本，旧实验保持原协议。涉及评测口径或基线的变化仍回到实验标准的用户审批流程。
