# 实验归档

模型和策略实验分别归入 `models/` 与 `strategies/`；模型按研究类型、策略按执行规则组织，内部 `exp_id` 保持稳定。配置、详细报告、文档、指标摘要和登记详情放在同一实验目录；同类策略的参数变体放在同一组的 `variants/` 内。
索引保留当前范围内的历史事件顺序，同一实验的预登记、完成和更正可以分别存在，不去重。
实验口径与产物保留规则以 [EXPERIMENT_STANDARD.md](../EXPERIMENT_STANDARD.md) 为准。

```text
experiments/
  registry.jsonl                   # 全部历史事件的轻量索引
  reports/                         # 模型、策略的公共总报告
  models/<type>/<short-name>/      # 模型研究、评估与模型诊断
  strategies/<rule>/<short-name>/   # 独立策略，或同类策略的参数组
    metadata.json                  # 类型、轨道、展示信息与资产清单
    configs/                       # 实验独有配置，保持冻结文件原字节
    reports/                       # 详细 HTML
    docs/                          # 实验说明或计划
    metrics/                       # 持久化指标 JSON
    data/                          # 其他研究证据
    records/<SHA-256>.json          # 完整登记事件
    variants/<variant>/            # 参数组的源实验；各自保留完整资产和 metadata.json
  _shared/                         # 公共资料、冻结预测与无独立实验的实盘对照
    deployment-parity/             # 当前及历史实盘对照配置、来源和哈希元数据
  _catalog/
    path_map.json                  # 旧路径到现路径的明确映射
    audits/                        # 长期迁移清单与核验结果
```

没有对应资产的实验不会创建空子目录。公共研究基线和多实验共享研究配置仍可位于
`backtest/configs/`；各实验 `metadata.json` 明确列出依赖，避免多份配置漂移。
实盘一致性对照（parity）统一放入 `_shared/deployment-parity/`，由元数据记录用途、来源、关联实验和文件哈希，避免生产配置依赖某一次诊断尝试的目录。
实盘 YAML 直接引用归档文件，`backtest/configs/` 不再另存 parity 副本。

现存对照为 [旧 K1×H5](_shared/deployment-parity/alla_v4_ladder_k1h5_parity.yaml)
与 [当前 Top5](_shared/deployment-parity/alla_v2_top5d1f100_parity.yaml)。

## 按文件夹维护与平台发现

2026-10-31 按用户追加要求，先删除无可读报告的模型档案，再将重复训练目标和历史v1归入汇总。当前保留全A模型6个、`phase-m-v1-bt`策略源实验17个，共23个源实体、24条登记事件。策略中的14个参数变体归为5组，另有3个独立入口；平台“实验记录”显示6个模型和8个策略入口。共享部署配置与模型原件不在本次删除范围。模型归并证据见 [_catalog/audits/20261031-baseline-comparison-cleanup/archive-consolidation.json](_catalog/audits/20261031-baseline-comparison-cleanup/archive-consolidation.json)，v1来源保留见 [_catalog/audits/20261031-v1-consolidation-v2-current/v1-archive-consolidation.json](_catalog/audits/20261031-v1-consolidation-v2-current/v1-archive-consolidation.json)。

平台每次刷新扫描 `models/` 与 `strategies/` 中的实验及参数组元数据，不扫描公共资料目录。源实验决定正式状态、历史指标和晋升；组只是把同类参数放在一个入口展示。目录类型不等于历史 `phase` 字段，保留事件的旧字段与指标保持原样。

模型侧当前分类：

- `training-target/`：训练目标 1 个，`horizon-comparison/` 四标签汇总入口；H20 v1另作该目录内的历史对照来源。
- `early-stopping/`：早停方法 3 个，保存 Top5、Top3、RankIC 三代准则。
- `model-structure/`：模型结构 2 个，v3五种子与v4单种子的DoubleEnsemble对照。

当前没有保留的特征实验，类型 `features` 可供后续使用，不创建空目录。v3 同时是 H20 标签对照行，按主要改动归入早停方法。集成模型采用对应的冻结超参，不能把多项改动的结果单独归因于模型结构。

`horizon-comparison/` 固定 Top3×H5 早停与评估，对照训练标签 H1/H5/H10/H20（v3）；四组净年化分别为 −39.79%、24.77%、24.37%、29.70%。元数据 `training_target_comparison` 的 `refs` 与 `control_ref` 声明四组原身份及H20对照；`archived_records`按相对路径和SHA256引用已合并的H1/H5/H10冻结来源记录，源指标保存在本目录`metrics/`，不再有三条独立目录或活动登记。平台按来源投影指标；缺失、种子不全或协议不同就暂停汇总。H20 的 v2 采用 Top5 早停，不能混入这组比较。

原H20 v1独立档案与活动登记已移除，其原字节记录保存在汇总目录`baseline-records/`，由元数据顶层`historical_baselines`声明相对路径与SHA256，仅供当时对照和历史模型引用，不恢复为独立实验或当前基线。历史v1的499天次日RankIC早停与四标签对照H20 v3的Top3×H5早停分别保留，29.33%与29.70%不混用。当前模型按用户选择为Top5早停的v2，v3/v4保留历史版本及原实验引用。

“训练目标期限对比”按用户确认的研究归属标记**产出v1 Benchmark**，在元数据`benchmark_outputs`中声明`ref/version`。Benchmark保留v1/v2/v3/v4四个模型版本，v1使用归并的冻结来源及`historical_baselines[].profile`所指的原始介绍；它不计入23个源实体，也不重新出现在实验记录。当前版本仍为v2，四标签对照的H20 v3结果不替换v1指标。

汇总入口保留原稳定 ID 与 event130 的旧 H20 持仓网格登记、指标和路径别名；当前分类与展示说明属于编辑层，不把旧事件改写成四组训练。两条30万元资金/费率复算的主指标窗口按源净值修正为2020-08-03～2026-07-31，新增57日诊断仍单列在原报告中，保持诊断身份。审查范围及数值不变核验见 `_catalog/audits/20261031-label-experiment-review/`。

s2与其原候选Top5换1·持1已归并，保留`baseline/phase-m-v1-bt-v2`源条目；本次进一步把与s3/s4/s5结果完全相同的候选归入相应Benchmark来源，并删除失败档案及其活动登记。原始指标、可用报告、必要冻结来源和旧资源链接保留；归并不会新增或提升Benchmark。前次审计见`_catalog/audits/20261031-bt-v2-dedup/`，本次见`_catalog/audits/20261031-strategy-experiment-groups/`。

策略侧按实际执行规则分组：

- `topk-dropout/`：12个源实验，按预测排名持有前 K 只、按规则轮换，归4个参数组及s1独立入口。
- `cohort-ladder/`：5个源实验，按批次买入并管理持有期，归1个参数组及K5、延长窗2个独立入口。

`baseline/`、`diag/`、`regime-adapt/` 不再作为策略目录分组。五代策略Benchmark及两份成功的资金/费用诊断继续保留；保留源实验的ID、基线引用、指标和登记事件不变，通过路径映射兼容旧路径。名称中的 `v2/v4` 指模型版本，`bt-vN` 指执行层基线版本。策略页只展示模型与策略，可按模型筛选，不显示恒为“策略回测”的实验类型；模型页保留类型列与筛选。

2026-10-31 按用户明确要求，已删除其余 112 个实验归档及对应 115 条登记事件，同时移除四份范围外公共报告和对应路径别名。保留实验及其共享依赖继续维护，当前 Top5 parity 已迁入共享部署档。详见[保留范围审计](_catalog/audits/20261031-retained-experiments/validation.json)。

旧迁移审计中的完整 `registry.before.jsonl` 备份也已删除，避免继续保存被删除的完整登记内容；其文件哈希与逐事件哈希保存在上述审计目录中，原有审计 JSON 继续保留。

新增文件夹的最小示例（仅示意，不会创建真实实验）：

```text
models/model-structure/new-model/
  metadata.json
  metrics/evaluation.json         # 可选；暂无结果时仍可显示档案
```

```json
{
  "schema_version": 2,
  "entity_type": "experiment",
  "exp_id": "example/new-model",
  "archive_path": "backtest/experiments/models/model-structure/new-model",
  "kind": "model",
  "track": "all_a",
  "experiment_type": "model_structure",
  "title": "新的模型研究",
  "change_summary": "仅把单LGBM替换为新模型。",
  "comparison_baseline": {"ref": "regime-adapt/m0-h20-t5h5-es-v1", "version": "v2"},
  "metrics_source": {"path": "metrics/evaluation.json"}
}
```

`kind` 为 `model` 或 `strategy`，必须与目录一致；`track` 为 `all_a` 或 `csi1000`。原 ID 全局唯一，不能因搬目录而更名。分类目录使用仓库相对的 `archive_path` 声明实际位置；不声明时继续采用 `<kind>/<exp_id>` 旧约定。平台名称优先读元数据 `title`，类型读 `experiment_type`，这两项可人工维护。`activity`、`role`、`changed_axes` 保留原研究轴和审计说明。

模型类型键为 `training_target`、`early_stopping`、`model_structure`、`evaluation_method`、`features`，分别对应训练目标、早停方法、模型结构、评估方法、特征。

模型 Benchmark 的完整详情按统一顺序介绍：模型版本、实验ID、模型结构、使用特征、种子、训练样本、训练目标、评估样本、训练早停、预测信号、变更内容、模型地址，然后是原有评估指标与标准化原始资料。这里的实验ID使用归档目录从 `experiments/` 往后的相对路径（如 `models/early-stopping/rankic-v4`）；原稳定登记ID保留在“结论与原登记”中。

详细配方存于 `metadata.json.model_profile`：`structure`、`features`、`training_sample`（`pool/time_range/filters`）、`training_target`、`evaluation_sample`（同结构）、`early_stopping`、`prediction_signal`、`changes`、`model_paths`。每个模型地址含仓库相对 `path`、可选 `seed/label`；可用状态由实际文件派生，只展示地址，不加载或提供模型二进制下载。版本、种子、目录身份及资源权限仍由登记与扫描决定，不能通过介绍改写。缺失字段显示“未登记”。

介绍必须区分训练与评估过滤、名义时间划分与实际抽样日集、训练早停与信号融合。当前训练沿用共用H40缓存有效行并按冻结日期抽样；训练未套用评估的ST/成交额门槛。四代基线模型原件均可用，已清理的候选模型明确显示历史路径及缺失状态。报告、评估结果、研究方案、元数据、历史登记等资源使用固定用途标签与说明，原指标、资源ID及权限保持不变。

输入特征单独写在 `features`，不混入 `structure`；DoubleEnsemble内部的样本重加权、特征选择等算法机制仍属于结构。`early_stopping` 只说明训练早停窗口、标签与打分规则，`prediction_signal` 说明子模型/种子如何生成和融合预测。旧 `evaluation_method` 可兼容读取，但不会从混合文案中自动推测新字段；新介绍不再展示该合并字段。

模型记录的“实验内容”仅显示短标题、`change_summary` 中的本次改动和 `comparison_baseline.version` 中的当时对照版本。摘要写改了什么，例如“训练标签由H20改为H5”，不重复整套配方、实验动机或结论；多项同时改动时须如实列出。`comparison_baseline.ref` 填当时对照的稳定模型 ID，不能随当前基线更新；若未填，只按原登记的明确引用查版本，不把 `self` 或当前版本当对照。初始基线填 `{"ref":"","version":"","initial":true}`；未知版本显示“未登记”。已晋升历史行的 `self` 和旧引用保留原样，展示信息依据原报告单独核准，不改变基线或比较资格。CSV 同步使用摘要与当时版本；完整假设、原引用和目录仍在详情中。

策略也使用 `comparison_baseline` 与 `change_summary`，用于说明当时的BT基线和实际差分；原 `baseline_ref` 保持冻结。若登记锚点与直接对照组候选不同，摘要须说清“在何种规则上”变化，具体来源留在审计中，不能把候选写成已晋升基线。

实验记录不再提供手动对比。点击记录先展示Baseline、变更和“Baseline＋实验组”核心指标表，对照使用`comparison_baseline`指定的当时版本，不替换成当前版本。策略参数组直接列出对照组与各变体的原始指标，不计算平均分或合成新指标；参数值写在各行名称和说明中。单种子实验读取Baseline源JSON的同种子结果；缺失或无前版时明确保留空值行，窗口、资金或费用不同时注明。训练目标汇总保留H1/H5/H10与H20（v3）四行；原始报告与登记放在折叠区。Benchmark继续保留完整介绍、版本勾选及分年/分风格指标。报告入口按实际文件去重，只展示可用链接；原登记路径仍保留供审计。

新目录不需要加入平台白名单，刷新“实验记录”即可发现。未登记档案显示为草稿，不获得官方基线或正式比较资格。正式 `append_registry`、`write_registry` 或共享事件编码入口自动维护对应元数据和索引，避免多处人工填写。

新分类目录已经在元数据声明 `archive_path` 时，使用 `append_registry` 或 `write_registry` 正式登记，会在登记锁内原子添加明确的目录别名；无需手工再维护第二张映射表。冲突、循环、越界或有原件的旧位置会拒绝绑定，不能用新声明遮蔽旧数据。直接调用底层编码入口时仍须已有明确位置映射。

指标源路径相对实验目录，不允许绝对路径、`..`、符号链接或非普通文件；可提供 `sha256` 校验。正式实验优先使用登记的 `portable_eval_output`，损坏或缺失时明确标注，不能由元数据填造数字。原有 `asset_snapshot` 库存清单使用明确的仓库相对路径，正式工具负责维护共享依赖和配置哈希。

平台中的数值和正式登记保持只读，人工结论可编辑。股票范围选项来自实际档案，当前两页均只有全A。实验记录与导出的记录按日期倒序，同日保持稳定，无效或缺失日期放在最后；Benchmark 仍按版本从新到旧排列。合法的同 ID 后续事件表示修订，采用最新版，不再显示普通重复提示；重复目录、损坏登记或无效元数据仍会明确报错。平台不执行代码、训练或回测。
模型原件、运行 session 和 MLflow 保持各自正式目录，不复制进实验档案。

## 策略实验的模型与策略摘要

策略“实验记录”将内容分成两列：模型列只显示真实模型版本与简称，策略列显示策略简称和简短描述；长假设、内部ID与完整备注仍在详情。CSV同样拆分，搜索覆盖两列。

每个策略的 `metadata.json` 维护模型引用和策略文案，例如：

```json
{
  "model_ref": "regime-adapt/m0-h20-t5h5-es-v1",
  "strategy_summary": {
    "name": "Top5换1 · F100",
    "description": "目标持仓5只，常规每次最多换1只；排名掉出前100时触发强制卖出。"
  }
}
```

模型版本和简称由 `model_ref` 关联模型档案自动取得，不需要在每个策略变体中重复登记。模型筛选使用完整模型引用，和搜索、状态筛选取交集；CSV导出当前筛选结果。切换股票范围或模型/策略页会清空不适用的模型筛选，刷新后已消失的模型选项自动清除。模型版本与BT策略基线版本独立：s5使用模型v2，不能把它展示成模型v5。历史登记已明确模型引用时优先读原值，元数据冲突会提示；未明确引用时显示“未登记模型”，不从长标题或当前基线猜测。摘要只用于展示，不改变冻结模型、策略参数、指标或晋升关系。

原22份摘要的来源核对与保真结果见[策略两列审计](_catalog/audits/20261031-strategy-columns/validation.json)。

## 同类策略按参数组维护

策略实验的Baseline和晋升标记从已验证的源实验及Benchmark动态取得，无需在组元数据重复填写版本。`strategy_history`仅附加到API历史展示行，包含关联晋升版本、组内/历史Baseline与成员的原历史对照；不改变源登记、指标或组的非基线身份。新增变体如有正式晋升，刷新后会显示对应s版本。组内Baseline用于本组核心指标表，成员历史Baseline用于核对原实验，二者分别标明。

全A策略 Benchmark 使用 **s1–s5**，模型使用 **v1–v4**。平台首列同时列策略编号、模型版本/简称及策略简称，例如 **s5 · v2 Top5早停 · Top5换1·持1·F100**。详情、对照和CSV采用相同标记。冻结登记中的原始`bt_version: vN`、实验ID及生产配置路径保留，`display_version`提供统一展示编号，历史指标与当前选择不因改名而变化。

组目录保存介绍、人工结论和汇总报告，`variants/<变体>/` 保存各源实验的完整资产。当前五组分别是：

- [Top5持有期×F100](strategies/topk-dropout/v2-top5-hold-f100/)：最短持有1/5日和F100开关的四格对照，含s2/s3/s5来源。
- [Top25 F100](strategies/topk-dropout/v2-top25-f100/)：固定Top25换5、最短持5日，对比F100开关。
- [阶梯F100/补位](strategies/cohort-ladder/v4-k3-h5-f100-refill/)：原阶梯、F100、F100加补位三档，含s4来源。
- [v4持仓与换仓](strategies/topk-dropout/v4-topk-drop/)：Top3换1、Top5换1、Top15换3，明确保留同时变化的参数。
- [30万仓位比例](strategies/topk-dropout/bt-v5-300k-risk/)：同一当前费用与账户口径下，对比risk_degree 0.90/1.00。

组的 `metadata.json` 使用 `entity_type: experiment_group`，`experiment_group.members` 声明各成员的稳定 `ref` 和行名，`control_ref` 指定对照组，`axes` 列变更参数，`fixed_summary` 说明固定口径。示例见[Top5组元数据](strategies/topk-dropout/v2-top5-hold-f100/metadata.json)。二维或多参数对照须如实列出各项变化，不能写成单参数实验；模型、窗口、资金与费用的差异只有被明确声明时才可汇总。

新增参数变体仍在自己的目录按正式入口登记，再把稳定ID加入组的成员清单。组本身不写入registry、不合并成员事件、不生成新的指标，也不获得基线或晋升资格。平台的 `records` 保留23个源实体，`experiment_records` 只保留14个展示入口，`benchmarks` 独立保留9个历史版本；原始记录仍供报告和清理器使用。实施说明见[策略实验归组方案](../../docs/superpowers/plans/2026-10-31-strategy-experiment-groups.md)。

## 如何登记实验结论

模型和策略“实验记录”的结论列支持点击“编辑”，在原表格填写1–500字并“保存”或“取消”。保存只更新对应 `metadata.json.analysis` 的人工结论、日期与草稿状态；参数组的结论写到组元数据，不改各变体结论。证据、局限、原登记结论和指标保留。若文件已在别处改动，保存会提示刷新并保留当前输入，不覆盖新文件。预填结论依据已有报告和指标，状态均为草稿；完整证据、局限与原登记内容在详情保留。

当前12条模型历史事件没有 `conclusion` 字段，只有末端前视诊断登记了完整结论；备注中的晋升事实和限制仍保留。字段未填不表示没有分析。本次只准备空的人工分析入口，不推断或补写研究结果。

日常整理时，编辑所属实验的 `metadata.json` 中 `analysis`，保存后刷新平台。例如[RankIC早停的元数据](models/early-stopping/rankic-v4/metadata.json)已经预留这些字段：

```json
{
  "analysis": {
    "conclusion": "在这里填写人工核对后的简短结论",
    "evidence": ["metrics/eval_m0h20_rankices.json"],
    "limitations": "说明样本窗口、比较口径和不能推出的结论",
    "review_status": "draft",
    "updated_at": "2026-10-31"
  }
}
```

这段只是字段示例，不是该实验的研究结论；编辑时保留文件其它字段。`evidence` 是相对本实验目录的证据路径，不能使用绝对路径或 `..`。`review_status` 可为 `draft`（草稿）或 `reviewed`（已人工核对）。长篇分析放在该实验 `docs/` 下，并在元数据中列为证据。

平台优先展示填写的**人工分析**，未填写时回退原登记结论；详情保留两者的区分。人工文字、审核标记和类型均不能改变数值、正式比较资格、当前基线或晋升。

正式实验登记仍通过下节的 `append_registry` 追加新事件：`conclusion` 保存正式结论或结论代码，`note` 保存解释与局限，并更新对应报告。不要直接编辑 `records/<SHA>.json`，也不要手改索引的哈希。已有事件原文继续作为历史证据保留。

通用读取器仍区分历史指标口径：`annualized_return_basis` 可声明来源路径、SHA 和算术或 CAGR 口径，声明只在当前源文件及其字节哈希匹配时生效。没有明确证据时不把旧年化称为 CAGR。新指标协议中的 `annualized_return`（CAGR）与 `annualized_return_arith`（算术审计值）保持各自含义。

## 登记与索引

统一使用 [experiment_registry.py](../scripts/experiment_registry.py)：

```python
from pathlib import Path
from backtest.scripts.experiment_registry import load_registry, append_registry

registry = Path("backtest/experiments/registry.jsonl")
rows = load_registry(registry)  # 完整逻辑事件，不含索引传输字段
append_registry(registry, completed_record)
```

新索引采用 `qlib-experiment-index-v3`，`detail_path` 相对于索引目录，
按 `_archive_kind` 构造为 `models/<exp_id>/records/<detail_sha256>.json` 或 `strategies/<exp_id>/records/<detail_sha256>.json` 的**逻辑位置**，分类后的模型和策略目录由显式 `path_map.json` 别名解析到实际位置。详情保存完整事件字段，
包括指标、假设、路径、逐种子结果和审计信息。SHA-256 对实际文件字节计算。
读取仍兼容旧内联行、v1 的 `registry_records/<SHA>.json` 与 v2 的 `<exp_id>/records/<SHA>.json` 格式。本次模型分类不改写索引、历史详情或 SHA，只调整实际目录及路径别名。

读取器校验版本、安全实验 ID、目录边界、符号链接、16 MiB 大小限制、SHA-256，
并核对索引元字段和详情是否一致。严格消费者遇到损坏必须报错，清理器不能依据不完整记录删除产物。
历史数值类型、负零和列表顺序均保留；存储兼容性不改变正式写入者对有限指标的要求。

详情不可原地修改。写入器先保存新详情，再原子替换索引。
`append_registry` 和 `write_registry` 共用稳定锁，防止并发追加丢记录；
`.registry.jsonl.lock` 是运行时锁，不入 Git，登记进程运行时不能删除。
调用方分开的“读取—修改—整表写入”仍须串行执行，避免旧快照覆盖新事件。

## 配置和报告路径

[research_layout.py](../scripts/research_layout.py) 仅按照 `path_map.json` 解析明确的旧路径别名，
也支持明确登记的实验目录别名，供未来同目录报告写入使用；不会猜测其它缺失文件或替换已清理的运行产物。配置加载器支持旧短名、新完整路径和唯一文件名；
冻结配置与协议保留原字节及哈希，在内存中解析活动引用。

总报告集中在 [reports/](reports/)。详细报告及指标从登记路径读取，生成器按输出位置计算相对链接。
新增实验应直接写入所属模型或策略目录；正式登记工具维护 `metadata.json` 的资产清单，共享依赖只保留一个权威文件。

## 核验和任务收尾

索引迁移证据见 [_catalog/audits/registry_index_migration_20261031/validation.json](_catalog/audits/registry_index_migration_20261031/validation.json)，
本次目录整理证据见 [_catalog/audits/20261031-layout/](_catalog/audits/20261031-layout/)。
模型/策略分类迁移证据见 [_catalog/audits/20261031-kind-layout/](_catalog/audits/20261031-kind-layout/)，记录当时 147 个实验、150 个事件及 33,935 个数值字段的迁移核对。随后按用户要求缩减到当时35个实验，实际删除范围与保留内容校验见 [_catalog/audits/20261031-retained-experiments/](_catalog/audits/20261031-retained-experiments/)；后续缺报告清理与重复实验归并后为当前29个。
13个模型按实际研究类型分类、简名及人工分析入口的核验见 [_catalog/audits/20261031-model-categories/](_catalog/audits/20261031-model-categories/)；本次35条历史事件和索引字节完全不变。
迁移时逐项核对历史事件、指标数值、文件哈希、平台与清理计划；不会通过训练或重跑回测改写结果。

一次性迁移、修复、核验脚本在验收后删除；保留正式读写、报告、清理功能、复用测试和核验 JSON。
每次任务均执行仓库 [AGENTS.md](../../AGENTS.md) 的收尾规则。
