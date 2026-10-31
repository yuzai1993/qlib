# 仓库一级、二级目录指南与整理建议

> 梳理日期：2026-10-31；代码基点：`b163d514`。本页是后续讨论清单，不是已经批准执行的删除方案。

## 阅读方法

本页按仓库根目录 `/Users/yuxianqi/Project/qlib` 计层级：`backtest/` 是一级目录，
`backtest/models/` 是二级目录。共覆盖初次梳理时实际存在的 **13 个一级目录、99 个二级目录**，
包括隐藏目录、未纳入 Git 的本地数据、缓存和运行记录。后续删除的目录在原表中标明状态；三级以下只举例，不逐项展开。

- **用途**尽量按“放的是什么、谁会用它”解释；表中示例用于帮助找到实际内容。
- **建议**分为保留、归档候选、缓存可重建、清理器管理、待核实。归档候选需要先核对引用和用途，不表示现在就删除。
- **体积**是写作时读取普通文件大小后的合计，包含子目录；不跟随符号链接，不代表实际独占的磁盘空间，也不等于可释放空间。1 GiB = 1024 MiB。
- 示例中的短文件名相对于该行目录；`<日期>`、`<运行ID>` 等表示路径格式，不是实际文件名。数字是写作过程中的快照，文档编辑和后台运行会造成少量变化。
- 已纳入 Git 的文件不一定是源码；未纳入 Git 的文件也可能是生产账本、数据或必要模型。不能用“有没有提交”判断能不能删。
- 仓库外的 `~/.qlib/` 行情、Conda 环境和外部 Git 工作区不计入本页体积。运行中的日志和缓存也会继续变化。

本页初次梳理只补充说明，没有清理目录、变更运行配置或重新做实验；以上统计和下表体积、文件数保留为当时快照。

## 整理进展

更新于 2026-10-31；未列出的目录尚未开始逐项整理。

| 一级目录 | 二级目录 | 整理进展 |
|---|---|---|
| `backtest/` | `configs/` | **本轮结束。**删除 4 份旧 parity，保留项归入实验档案；回测须显式指定 `--config`。其余研究配置保留，K1 历史实验归属后议。 |
| `backtest/` | `data/` | **完成。**已删除目录及 IM 校验、占位和系统缓存文件；来源路径与哈希保留在历史实验记录中。 |
| `backtest/` | `datasets/` | **确认保留。**研究用数据读取与样本选择源码，维持现状。 |
| `backtest/` | `diagnostics/` | **确认保留。**平台分析档案及其元数据、报告和代码，维持现状。 |
| `backtest/` | `experiments/` | **完成。**23个源实验归档；平台显示6模型＋8策略入口、9个Benchmark。策略编号s1–s5，Benchmark标记模型及策略简称；当前模型v2、策略s5。 |

## 先理解几组容易混淆的名字

| 看起来相近的目录 | 实际区别 |
|---|---|
| `qlib/backtest/` 与 `backtest/` | 前者是通用回测“发动机”的源码；后者是我们使用这台发动机做研究的工作台。 |
| `qlib/data/`、`backtest/datasets/` 与行情数据 | 前两者主要是读数据、加工样本的程序；真实行情常在 `scripts/data_collector/` 的输出及仓库外 `~/.qlib/`。 |
| `qlib/model/`、`backtest/models/`、`live_trading/models/` | 分别是通用模型接口、本地研究模型实现及冻结研究模型、给实盘加载的模型导出。 |
| `backtest/experiments/` 与 `backtest/result/` | 前者是长期档案柜；后者是每次运行的工作目录，里面仍可能有必须保留的模型和预测。 |
| `backtest/diagnostics/` 与实验报告 | diagnostics 放独立分析，平台“分析档案”页读取；模型/策略实验按 experiments 下的分类目录与元数据展示，登记表保存历史事件。 |
| `tests/` 与 `qlib/tests/` | 前者主要是自动检查；后者还提供被框架示例导入的测试辅助工具。 |
| `logs/` 与 `.git/logs/` | 前者是运行记录；后者是 Git 用于找回提交/分支状态的操作历史。 |

文中几个常用词：**配置**是“告诉程序怎么跑”的参数；**指标 JSON**是程序可读取的结果；
**metadata/manifest 清单**说明文件放在哪里、对应哪个模型或实验；**SHA/哈希**相当于文件指纹，用于检查内容是否改变；
**session**是一组运行工作文件；**seed/随机种子**用于固定训练中的随机过程，同一方案的五个种子是五次训练，不是五份可随意互换的副本。

## 一级目录总览

所有一级目录中文件内容合计约 **6.23 GiB**。下表体积已经包含其二级目录，不能再相加一次。
“Git 文件/全部文件”表示目录中已跟踪文件数与实际普通文件数；`.git` 是特殊的版本管理内部区。

| 一级目录 | 体积 | Git 文件/全部文件 | 通俗用途与初步建议 |
|---|---:|---:|---|
| [backtest/](#backtest) | 1.12 GiB | 1,038 / 1,359 | 离线研究工作区：定义模型和策略、运行历史检验、保存实验档案与必要模型原件。<br>**初步建议：**保留为研究主入口；先按源码、长期档案、可清理运行产物分清边界，再讨论瘦身。 |
| [live_trading/](#live-trading) | 683.52 MiB | 117 / 44,544 | 实盘业务：Mac 产生和发布信号、导入回执、维护账本；Windows QMT 执行下单。<br>**初步建议：**优先理清当前生产、历史回退、测试探针三类文件，逐步瘦身，保持正式入口稳定。 |
| [quant_platform/](#quant-platform) | 289.19 KiB | 16 / 22 | 当前统一的本地网页平台：展示实盘账本、模型/策略实验，以及分析档案。<br>**初步建议：**保留现网平台源码；把旧前端与当前前端的分工写清。研究源现已统一为本仓库的 experiments 与 diagnostics 目录。 |
| [scripts/](#scripts) | 3.37 GiB | 74 / 10,895 | 项目级数据工具：下载样例行情、把 CSV 转为 Qlib 二进制格式、检查数据质量，以及采集各数据源。<br>**初步建议：**保留正式工具；重点把采集代码与不断增长的原始/标准化数据分离管理。 |
| [qlib/](#qlib) | 5.39 MiB | 237 / 377 | Qlib 基础框架的源码，相当于研究和回测系统的发动机；不是下载行情或存放训练结果的地方。<br>**初步建议：**整体保留。瘦身优先清理生成缓存，不按当前策略暂时没用到就裁剪模块。 |
| [examples/](#examples) | 121.27 MiB | 192 / 735 | Qlib 的教程和功能演示，演示各种模型、滚动训练、组合优化及订单执行；不等同于本仓库当前生产配置。<br>**初步建议：**先处理本地运行产物和根目录中保存大量输出的重复 Notebook，再按学习、维护和测试需要挑选示例；保留必要输出证据后再讨论合并。 |
| [tests/](#tests) | 7.50 MiB | 182 / 363 | 验证代码是否仍按预期工作的自动检查。既包含 Qlib 上游测试，也包含本项目的实盘、实验归档和平台测试，根目录还保留整条工作流等综合测试。<br>**初步建议：**保留必要测试；后续可按基础框架、业务核心、可选重型测试分组运行，而不是因为慢或需要外部数据就删掉测试。 |
| [docs/](#docs) | 5.09 MiB | 182 / 184 | 仓库的说明书：既有 Qlib 上游的使用手册，也有本项目的开发约定、研究笔记和历史设计方案。<br>**初步建议：**保留说明书；后续先把上游框架文档和本地业务文档分清入口，再按主题合并重复的历史说明。文档通常很小，整理的主要收益是更好找。 |
| [mlruns/](#mlruns) | 60.63 MiB | 0 / 341 | MLflow 为训练/回测保存的运行记录仓库，数字目录是实验编号，内部有指标、模型和记录元数据。<br>**初步建议：**只按统一清理器的保留计划管理；将数字ID和实际用途在目录指南中对应，避免人工猜测。 |
| [logs/](#logs) | 182.98 MiB | 0 / 1,128 | 仓库通用数据维护/回测日志，与实盘专用 live_trading/logs 分开。<br>**初步建议：**可统一日志命名与轮转策略；把通用日志和实盘日志的归属写清。 |
| [ideas/](#ideas) | 7.22 KiB | 0 / 4 | 零散研究想法草稿，目前4个无扩展名的文件，其中2个为空；被Git忽略。<br>**初步建议：**先把有价值想法整理到 docs/research 的研究待办，再决定删空占位文件或取消该目录。 |
| [.github/](#github) | 42.57 KiB | 13 / 14 | GitHub 上的项目协作和自动检查设置，包含问题模板、合并请求模板、测试及发布流程。<br>**初步建议：**保留与当前仓库协作和检查相关的设置；可单独评估不再使用的上游发布与维护自动化。 |
| [.git/](#git) | 703.76 MiB | Git 内部 | Git 保存版本历史和工作区登记的内部目录，相当于仓库的历史账本，不是业务代码。<br>**初步建议：**整体保留；目录改动提交后，历史版本仍会占空间，这是正常现象。不要把手工删除 .git 当作项目瘦身。 |

**最值得先讨论的方向：**

1. **数据输出是否重复：**`scripts/data_collector/` 约 3.37 GiB，是当前最大一项；但其中有实盘使用的 ST 名单。先分采集程序、原始数据、标准化数据和正式数据源，再谈迁移或删副本。
2. **本地运行记录和修复副本：**`examples/mlruns/` 约 79 MiB，`logs/data/` 约 183 MiB；先核对保留价值、修复回退和实验引用。
   `examples/` 根下另有两份合计约 40.70 MiB 的本地 Notebook，单元格源码相同、保存输出不同，值得先确认输出价值再讨论合并。
3. **阅读负担：**`backtest/scripts/` 有 152 份已跟踪文件，`docs/superpowers/` 有 102 份历史文档。这里更适合分类和补入口，不能期待省下很多磁盘空间。
4. **纯残留：**`examples/Users/` 是没有文件的旧路径套娃；各处 `__pycache__/` 和 `.pytest_cache/` 可重建。清理它们主要让目录更整齐，体积收益很小。
5. **需要保留规则的地方：**`backtest/result/` 虽约 1.07 GiB，但保留当前/历史基线产物；根目录 `mlruns/` 的 7 组也都有明确用途。以 [实验规范](../../backtest/EXPERIMENT_STANDARD.md) 和清理器计划为准。

以下逐项列出每个一级目录的全部二级目录。

<a id="backtest"></a>

## 1. `backtest/`

本层还保存研究入口与 `EXPERIMENT_STANDARD.md`。这里把研究源码、长期档案和运行产物分开，避免看到“数据/模型”就判断可以清理。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `backtest/configs/` | 865.96 KiB | 多实验共用的研究配置、公共研究基线配置及少量固定辅助数据。实验独有的配置已搬到各自 experiments 目录，parity 也统一归入 experiments。<br>例：regime-adapt/eval_m0fast.yaml；regime-adapt/train_dates_v1.csv；baseline-strategy/b4-s/topk-t22-d2-h2_csi1000_full.yaml。 | **本轮整理告一段落，保留其余内容。**parity 清理与归档已完成，详见上方“整理进展”；其余配置尚未逐项筛选，后续再检查模板、共享数据和失效引用。 |
| `backtest/data/` | 原快照 90 B | **已于 2026-10-31 删除。**原来只剩 IM 行情的校验文件和占位文件，CSV 本体已缺失，日调度也不更新该目录。 | **整理完成。**历史输入路径与哈希已存于对应实验的结果和登记记录；不再维护此公共数据目录。 |
| `backtest/datasets/` | 19.28 KiB | 研究用的数据读取和样本选择代码，决定给模型喂哪些股票、怎样组织时间序列，并非原始数据目录。<br>例：liquidity_segment.py 按成交活跃程度分组训练样本；lean_mts.py 降低序列数据加载的内存占用。 | **保留。**保留源码；如果以后产生 regime-adapt-cache 等大缓存，应另列缓存子目录并设置生命周期，别和这些 Python 模块一起删。<br>注意：删除这些代码可能使历史配置无法加载；__pycache__ 才是可重建缓存。 |
| `backtest/diagnostics/` | 48.05 KiB | 独立分析档案：一份分析一个目录，报告、元数据和代码放在一起，平台分析页从这里读取。<br>例：20260809_style_regimes/REPORT.md、metadata.json；20260809_a0b_regime_features/code/。 | **保留。**保持现布局；新增分析按 README 补元数据，检查缺失附件；较旧分析可标记 archived，优先保留结论与复现代码。<br>注意：是长期研究记录，不应按运行结果清理规则删除；旧报告中的缺图/缺代码提示要保留。 |
| `backtest/experiments/` | 原快照 44.28 MiB | 实验档案柜，按 models/ 与 strategies/ 分组；源实验保存配置、报告、指标、文档和历史登记，同类策略的参数变体归在同一组。<br>例：models/early-stopping/top5-v2/metadata.json；strategies/topk-dropout/v2-top5-hold-f100/variants/。 | **完成。**23个源实验按目录发现，平台显示14个入口（6模型、8策略）；保留源实验的原ID、指标和历史Benchmark。详见下一级目录说明。 |
| `backtest/features/` | 24.17 KiB | 额外特征的计算代码，也就是把价格、成交量、市场状态加工成模型输入的规则。<br>例：technical.py 定义布林带/动量/趋势特征；regime.py 将每日市场状态特征拼到股票样本。 | **保留。**保留被配置和模型依赖的模块；可补模块用途索引，未来确认完全无引用的废弃特征再归档。 |
| `backtest/label_design/` | 14.99 KiB | 训练目标的定义和防止未来信息泄漏的代码，例如让模型预测未来若干天的收益。<br>例：horizons.py 的 cumulative_label(20) 生成20日收益表达式；dataset.py 切掉跨训练边界的标签样本。 | **保留。**保留；如实验方向停止，先保留必要标签生成器和测试，重复的试验说明归到实验文档。 |
| `backtest/models/` | 3.42 MiB | 研究用模型扩展源码，以及 CSI 策略回测使用的冻结基线模型与清单。<br>例：regime_adapt.py、rankic_early_stop.py；baselines/b6-m/manifest.json 绑定 seed4000 的冻结模型。 | **保留。**保留源码和被清单引用的模型；后续可将“模型实现”和“冻结权重”在说明上分开，审查没有引用的重复导出。<br>注意：不是全部可重训缓存。Python模型原件还可能依赖这里的类定义；Phase S 必须按manifest读取固定模型。 |
| `backtest/result/` | 1.07 GiB | 训练、预测、回测每次运行生成的工作目录，包含模型原件、元数据、信号、日收益和临时图表等。<br>例：regimeadaptfast_m0h20_rankices_s42/；20260822_091415_phase_s_m0h20es_all_top5d1f100_ensemble/。 | **清理器管理。**本轮仅缩减实验归档，没有删除本目录。纯全A dry-run 保留 24 个目录；额外提出的旧 CSI session 删除尚未执行，后续单独讨论。<br>注意：按完整五种子、版本化模型基线、当前BT依赖与正式回测保留；不能按文件夹日期或大小直接删。 |
| `backtest/scripts/` | 3.08 MiB | 研究运行入口和工具：准备数据、训练评估、登记实验、生成报告、晋升基线、清理产物。<br>例：experiment_registry.py、research_layout.py、build_phase_m_v1_report.py、cleanup_experiment_artifacts.py。 | **保留。**有明显的可整理空间：可按准备/训练/评估/报告/登记/维护分类，再查重复入口；一次性工具任务结束后删除。 |

### 实验档案内容示例

下面是目录形状示意；只有实际存在的资产才建对应子目录：

```text
backtest/experiments/models/<类型>/<简名>/  # 策略侧使用 strategies/<执行规则>/<简名>/
  metadata.json          # 实际位置、类型、名称、人工分析及资产清单
  configs/               # 本实验独有的参数配置
  reports/               # 给人看的结果报告
  docs/                  # 实验说明、计划和结论
  metrics/ 或 data/      # 给平台/报告使用的指标和其他研究证据
  records/<哈希>.json    # 完整的历史登记事件，含详细指标
```

同类策略另用 `strategies/<规则族>/<组名>/metadata.json` 维护组介绍和结论，源实验放在组内的 `variants/<变体>/`，各自仍有上述完整资产。

公共总报告在 `backtest/experiments/reports/`，共享资料在 `_shared/`（含 `_shared/deployment-parity/` 的当前 Top5 与旧 K1 实盘对照档），迁移证据在 `_catalog/audits/`。
少量共享研究/基线配置仍在 `backtest/configs/`，由各实验清单明确引用；这类“引用”不等于漏整理。当前主仓库未保留 K1 对应的独立实验档案，部署对照暂放 `_shared/deployment-parity/`；旧 `qlib_exp` 曾有相关 K1 延长窗实验，归属关系仍待核实。
详细约定见 [实验归档说明](../../backtest/experiments/README.md) 和 [分析目录说明](../../backtest/diagnostics/README.md)。

### `backtest/experiments/` 下一级目录

2026-10-31 按用户要求保留全A模型和 `phase-m-v1-bt` 策略，归并重复项并删除失败档案。现有23个源实验、24条登记事件；同ID可有后续修订。14个策略变体汇总成5组，另有3条独立策略，平台记录页共显示6模型＋8策略入口。H1/H5/H10及历史v1来源仍在训练目标汇总，模型源码仍在 `backtest/models/`。

| 子目录 | 实验数 | 内容与维护方式 |
|---|---:|---|
| `models/` | 6 | 全A模型训练、评估与模型诊断；每个实验一个目录和一份元数据。 |
| `strategies/` | 17 | `phase-m-v1-bt` 源策略；14个变体归5组、3个单列，平台显示8个入口。 |
| `reports/` | — | 全A模型与执行回测两份公共总报告。 |
| `_shared/` | — | 共享输入、冻结预测、协议、公共资料和部署对照档。 |
| `_catalog/` | — | 路径映射及长期迁移审计，平台不把它当实验。 |

保留的方向目录如下，目录名称与登记字段 `direction` 可不同：

| 方向目录 | 实验数 | 通俗用途 |
|---|---:|---|
| `models/training-target/` | 1 | horizon-comparison保存四标签对照及H1/H5/H10来源，H20对照引用v3；标记产出v1 Benchmark，v1原介绍和指标在此保留，供Benchmark及v2对照读取。 |
| `models/early-stopping/` | 3 | 比较早停准则；top5-v2、top3-v3、rankic-v4，当前模型选择为v2，历史版本不重编号。 |
| `models/model-structure/` | 2 | v3五种子与v4单种子集成对照；v3重复单种子条目已删除。 |
| `strategies/topk-dropout/` | 12 | 按排名选股并轮换；4个参数组＋s1单列。包含Top5持有期×F100、Top25 F100、v4持仓/换仓、30万仓位比例对照。 |
| `strategies/cohort-ladder/` | 5 | 分批买入并管理持有期；F100/补位3档归1组，v2 K5×H5和v4延长窗诊断各自单列。 |

各参数组元数据使用 `entity_type: experiment_group`，声明成员、对照、变更参数及固定口径；它只负责展示，不进入registry，也不决定晋升。成员的配置、报告、指标和冻结登记保存在 `variants/`，历史路径通过明确别名继续可读。组结论写在组的 `metadata.json.analysis`；表格编辑不会改动成员结论或指标。方案见[策略实验归组](../superpowers/plans/2026-10-31-strategy-experiment-groups.md)，本次核验见[归组审计](../../backtest/experiments/_catalog/audits/20261031-strategy-experiment-groups/)。

平台按目录元数据自动发现，实验记录和CSV按日期倒序。“股票范围”选项来自实际档案，当前均为全A。模型页按实验类型筛选，策略页新增模型筛选，与搜索、状态筛选组合使用；Benchmark独立保留4个模型和5个策略版本。普通同ID修订采用最新版，真正重复目录或损坏数据仍报错。原始收窄范围的历史快照见[保留范围审计](../../backtest/experiments/_catalog/audits/20261031-retained-experiments/validation.json)。

模型Benchmark单独展示v1/v2/v3/v4四个基准版本，当前为v2；归并的v1只在Benchmark中恢复，不恢复独立实验条目。训练目标汇总通过`benchmark_outputs`记录产出v1，v1原介绍放在`baseline-profiles/v1.json`，冻结记录和指标继续保留原口径。

模型侧已移除不反映实际研究内容的 `regime-adapt/` 目录，名称和类型在元数据维护。当前没有特征实验，不建空目录；未来可使用 `features` 类型。原 `exp_id` 保持稳定。模型列表仅展示本次改动摘要和当时Baseline，分别在 `change_summary`、`comparison_baseline` 维护。人工结论和证据写在 `metadata.json.analysis`，表格支持编辑、保存和取消；原指标与登记结论不变。填写示例见[如何登记实验结论](../../backtest/experiments/README.md#如何登记实验结论)。

策略记录表按“模型版本＋简称”和“策略简称＋简述”分列，CSV同步。模型关联与策略短文案保存在策略元数据的 `model_ref`、`strategy_summary`；模型版本从对应模型档案读取，s5仍显示模型v2。详细假设和原ID保留在详情。

策略侧按执行规则归档，五代基线继续供Benchmark及清理器保留使用；两份成功的30万资金/费用诊断归为仓位比例组。当前Top5 parity独立保存在 `_shared/deployment-parity/`，生产YAML直接引用。策略页不展示恒定的实验类型列、类型或方向筛选。全站下拉箭头内缩并统一留白。

本次分类、名称与路径调整的原始记录/指标核对见[模型分类审计](../../backtest/experiments/_catalog/audits/20261031-model-categories/validation.json)。

模型Benchmark介绍中，“使用特征”已独立于结构，“训练早停”和“预测信号”分别维护；对应 `model_profile.features/early_stopping/prediction_signal`，其它指标和记录详情布局保持。

<a id="live-trading"></a>

## 2. `live_trading/`

当前生产方案为 Top5，活动配置是 `alla_v2_top5d1f100_postclose_real_active`。配置、账本、模型、调度和 QMT 端共同组成完整运行链路；历史回退和探针可单独讨论，但不按目录名整包删除。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `live_trading/.locks/` | 0 B | 运行中的互斥锁位置，用来阻止同一策略同时导入、发布或跑两次流水线。<br>例：&lt;config&gt;_publish.lock、&lt;config&gt;_postclose.lock（当前目录为空）。 | **保留。**保留机制；只清理已经核实没有进程占用的失效锁。目录本身很小，不是磁盘重点。<br>注意：运行时删锁可能让两个任务同时执行；不要把它按普通缓存统一删除。 |
| `live_trading/.scheduler/` | 19.08 KiB | 按配置和业务日期记录调度回执，说明某个阶段成功、失败或跳过。<br>例：alla_v2_top5d1f100_postclose_real_active/&lt;日期&gt;/ 下的阶段回执。 | **保留。**可设计旧日期回执归档和保留周期；保留当前生产的近期、失败及关键切换日期记录。<br>注意：既用于排障也用于判断是否已执行，不能随意清当前日期或活动配置的记录。 |
| `live_trading/__pycache__/` | 150 B | Python 为加快导入自动生成的编译缓存。<br>例：__init__.cpython-312.pyc。 | **缓存可重建。**可在空闲时与其他 __pycache__ 一并清理；运行后会自动再生成。 |
| `live_trading/configs/` | 25.41 KiB | 实盘策略的操作配置，指定模型、策略参数、账本和共享目录等。<br>例：alla_v2_top5d1f100_postclose_real_active.yaml；旧阶梯 K1 及仅供历史查阅的 K3、CSI300、CSI1000 配置。 | **保留。**保留当前生产、明确回退及历史查阅配置；未来可给旧配置加状态清单，停用探针单独分类。 |
| `live_trading/data/` | 665.26 MiB | 实盘状态和证据：SQLite账本、成交与计划、回执、备份、剪贴板交接及修复记录。<br>例：alla_v2_top5d1f100_postclose_real.db 及 .db-wal/.db-shm；backups/、clipboard/、deployments/。 | **保留。**先细分账本、运行状态、备份、交接/修复证据，再按各自保留策略评估；可能有空间，但不宜整目录清理。<br>注意：活跃SQLite的WAL/SHM不是可随手扔的缓存；历史账本仍在平台展示，备份应按一致性方式处理。 |
| `live_trading/launchd/` | 4.07 KiB | macOS 定时任务和常驻平台服务的安装模板。<br>例：com.yuxianqi.qlib-live-scheduler.plist；com.yuxianqi.qlib-live-monitor.plist。 | **保留。**保留两类正式模板；说明清楚“仓库模板”和已安装到用户Library的服务文件区别。<br>注意：修改模板不会自动改变正在运行的服务，不能为了瘦身删除现网所用入口。 |
| `live_trading/logs/` | 781.00 KiB | 实盘与平台的运行日志，另混有部署、切换和验证的JSON/图片证据。<br>例：*_scheduler.stdout.log；bt_v5_services_enabled_20261030.json；top5_ledger_monitor_20261030.png。 | **归档候选。**建议优先做日志轮转与压缩；把长期审计证据与可过期.log分清，按日期/用途列保留策略。 |
| `live_trading/models/` | 15.70 MiB | 给实盘直接加载的模型导出包，包含当前生产与旧方案的固定权重。<br>例：v2_t5h5es/manifest.json 和5个种子模型用于当前Top5；v4_rankices/ 是旧阶梯所用模型组。 | **保留。**保留当前 v2 五种子导出；其他组先核查回退/探针/配置引用再讨论去重或冷归档。<br>注意：有些导出是经过裁剪并验证等价的较小模型，不等于 result 中训练原件；不能只靠文件名认为可互换。 |
| `live_trading/modules/` | 745.70 KiB | 实盘业务逻辑源码：选股、订单计划、发布信号、导入成交、账本与监控等。<br>例：order_planner.py、signal_publisher.py、fill_importer.py、cohort_store.py。 | **保留。**保留；后续可按信号/执行/账本/监控分模块梳理，先找重复逻辑和已停用分支。 |
| `live_trading/qmt_strategy/` | 556.72 KiB | Windows QMT 侧的策略模板、剪贴板/共享目录桥接程序、部署脚本和操作说明。<br>例：qmt_clipboard.py、clipboard_relay.py、clipboard_auto.py；README_TOPK_CLIPBOARD.md。 | **保留。**把当前Top5剪贴板链路、旧桥接回退、PR49/一手探针文档分清；旧探针可在确认无回退需要后归档。 |
| `live_trading/scripts/` | 361.48 KiB | 实盘命令行入口和维护工具，调用 modules 完成发布、导入、监控、部署和人工恢复。<br>例：run_scheduler.py、run_publish_signals.py、run_import_fills.py、refresh_stock_names.py。 | **保留。**可整理为正式日常命令、部署/切换工具、诊断恢复工具；清理明确一次性且无依赖的旧入口。 |
| `live_trading/web/` | 73.32 KiB | 实盘监控后端API和旧独立Web界面。虽然现网界面已合并进 quant_platform，这里部分后端仍被直接使用。<br>例：quant_platform/ledgers.py 导入 live_trading.web.api.create_router；app.py/static 是旧独立页面入口。 | **保留。**可讨论把仍在用的API迁成明确公共模块，再归档旧 app/static 和回退启动入口；不要整目录删除。<br>注意：api.py 是现平台的运行依赖；“旧Web”不等于整个目录已废弃。 |

<a id="quant-platform"></a>

## 3. `quant_platform/`

本层的 `app.py`、`research.py`、`analyses.py`、`ledgers.py`、`__main__.py` 是平台后端。当前“分析档案”从 `backtest/diagnostics/` 加载；实验页扫描 `backtest/experiments/models/` 与 `strategies/` 的元数据，并关联登记表和独立指标 JSON。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `quant_platform/__pycache__/` | 104.75 KiB | 平台 Python 模块的自动编译缓存。<br>例：research.cpython-312.pyc、analyses.cpython-312.pyc。 | **缓存可重建。**可重建，统一清理即可；不用把它当平台代码的一部分维护。 |
| `quant_platform/static/` | 104.51 KiB | 现平台网页的HTML、JavaScript和CSS，即浏览器实际显示和交互的界面。<br>例：index.html；js/platform.js；css/。 | **保留。**保留；检查是否有未使用图片、重复JS或旧页面资产时再针对性瘦身。 |

<a id="scripts"></a>

## 4. `scripts/`

本层还有 `get_data.py`、`dump_bin.py`、`check_data_health.py` 等数据工具。这里和 `backtest/scripts/` 的差别是：前者偏通用数据准备，后者偏本地研究流程。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `scripts/__pycache__/` | 32.40 KiB | 运行数据工具后自动生成的 Python 字节码。<br>例：`scripts/__pycache__/dump_bin.cpython-312.pyc` | **缓存可重建。**可重建，后续可统一做缓存清理；不进 Git。 |
| `scripts/data_collector/` | 3.37 GiB | 采集行情、指数成分和 ST 名单的代码，同时混放了采集出的 CSV。当前几乎全部体积来自 tushare 的原始和标准化数据。<br>例：`scripts/data_collector/tushare/collector.py`、`scripts/data_collector/tushare/normalize/sz000581.csv` | **待核实。**属于重点瘦身讨论对象：保留采集代码，把数据输出改到明确的数据根后再迁文件；核对 source/normalize/最终 Qlib bin 是否重复保留，按可重建性制定保留策略。<br>注意：约 3.37 GiB、10,884 个文件，只有 66 个 Git 跟踪文件。活动实盘配置和发布脚本直接使用 st_daily.csv，它是日频 ST 过滤事实来源，不能与中间数据一起清除。 |

数据文件的层次可以这样理解：`source` 类输出接近“从数据源拿到的原稿”，`normalize` 类输出是“整理过的表格”，
Qlib bin 则是“便于训练快速读取的格式”。三者可能内容重叠，但承担下载留证、修复核对和快速读取等不同用途；
下一轮需要先确定重建成本和实际读取路径，不能只按文件内容相似就删。

<a id="qlib"></a>

## 5. `qlib/`

这是需要保留的 Qlib 基础框架。整个目录当前约 5.39 MiB，优先裁剪它对磁盘收益很小，反而容易破坏配置、已保存模型中的模块引用和测试。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `qlib/__pycache__/` | 46.66 KiB | Python 为加快再次启动自动生成的字节码缓存。<br>例：`qlib/__pycache__/config.cpython-312.pyc` | **缓存可重建。**属于可重建缓存，可在后续缓存清理时处理；不纳入 Git。 |
| `qlib/backtest/` | 475.31 KiB | 回测引擎源码：按历史行情模拟买卖、资金和持仓，计算收益及交易指标。<br>例：`qlib/backtest/exchange.py`、`qlib/backtest/account.py` | **保留。**保留。与根目录 backtest/ 的研究配置、报告和运行结果分开，避免混放。 |
| `qlib/cli/` | 4.81 KiB | 命令行入口，把一份 YAML 配置交给 Qlib 执行工作流，或下载示例数据。<br>例：`qlib/cli/run.py`、`qlib/cli/data.py` | **保留。**保留。目录很小，删它对节省空间帮助有限。 |
| `qlib/contrib/` | 1.35 MiB | 可直接使用的模型、特征、选股策略和报告组件。例如 LightGBM 模型及按排名换股的策略。<br>例：`qlib/contrib/model/gbdt.py`、`qlib/contrib/strategy/topk_dropout.py` | **保留。**保留当前实现。若以后要做精简发行版，应先列出配置和模型引用的模块，再讨论可选组件分包。 |
| `qlib/data/` | 2.45 MiB | 读取和处理行情的程序，包括交易日历、股票池、特征表达式、数据缓存接口；这里主要是代码，不是实际行情仓库。<br>例：`qlib/data/data.py`、`qlib/data/dataset/` | **保留。**保留。可重建的编译中间文件与 Python 缓存可以单独讨论，不要清理数据接口源码。<br>注意：目录中的 .so 是已编译的计算组件，删后可能需要重新编译，不能和自动重建的 __pycache__ 混为一谈。 |
| `qlib/model/` | 106.80 KiB | 模型的通用接口、训练器、集成和风险模型代码；不是训练完成的 .pkl 模型文件。<br>例：`qlib/model/base.py`、`qlib/model/trainer.py` | **保留。**保留，继续把真正的模型产物放在正式模型或运行产物目录。 |
| `qlib/rl/` | 233.70 KiB | 强化学习的通用接口和训练、交易执行组件，例如让算法学习如何分批完成订单。<br>例：`qlib/rl/interpreter.py`、`qlib/rl/order_execution/` | **保留。**基础框架内暂时保留。以后若明确做不含强化学习的发行版，再一起处理依赖和测试。<br>注意：qlib/strategy/base.py 直接导入这里的接口，因此当前不用强化学习不等于整个目录可以删除。 |
| `qlib/strategy/` | 23.74 KiB | 策略的基础接口，约定策略怎样接收信号、读取持仓并生成交易决策。<br>例：`qlib/strategy/base.py` | **保留。**保留，代码很少但连接了模型信号和回测执行。 |
| `qlib/tests/` | 55.96 KiB | 随基础库提供的测试辅助工具、测试数据下载工具和通用示例配置，也含少量直接测试。<br>例：`qlib/tests/data.py`、`qlib/tests/config.py` | **保留。**保留。若以后拆分测试辅助包，应先迁移示例和测试中的导入。<br>注意：examples/model_rolling、examples/online_srv 等示例直接导入 qlib.tests.config，不能只因目录叫 tests 就删除。 |
| `qlib/utils/` | 259.52 KiB | 各模块共用的小工具，如文件处理、配置实例化、序列化、并行处理、时间频率换算。<br>例：`qlib/utils/file.py`、`qlib/utils/paral.py` | **保留。**保留。不建议为省少量源码空间拆散通用工具。 |
| `qlib/workflow/` | 369.58 KiB | 实验记录和任务管理代码：组织训练任务，记录参数、指标与模型，并衔接 MLflow。<br>例：`qlib/workflow/recorder.py`、`qlib/workflow/expm.py` | **保留。**保留；把运行结果继续放在 mlruns/ 等产物目录，不混入这里。 |

<a id="examples"></a>

## 6. `examples/`

这些是教程和功能样板，不代表当前生产正在使用。二级目录中的示例源码整体很小，运行记录和本地 Notebook 保存的输出要另看。根下还有未跟踪的 `workflow_by_code_100w.ipynb` 与 `workflow_by_code_100w_new.ipynb`，合计约 **40.70 MiB**：两份的单元格源码相同，主要差别在输出。建议先确认输出是否包含唯一结果，再保留一份代码、将有价值输出转入报告，讨论合并重复内容。它们是个人研究工作文件，不按缓存直接删除。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `examples/benchmarks/` | 1.15 MiB | 不同预测模型的对照示例与训练配置，相当于模型样板间。<br>例：`examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml`、`examples/benchmarks/XGBoost/` | **保留。**至少保留 LightGBM 及当前需要的模型样例；其余可在确认不用后归档，源码本身不是主要空间占用。<br>注意：GitHub 源码测试直接执行 LightGBM 配置，文档也引用此目录。 |
| `examples/benchmarks_dynamic/` | 14.32 KiB | 应对市场变化的模型对照示例，比较普通滚动训练与 DDG-DA 等适应方法。<br>例：`examples/benchmarks_dynamic/baseline/`、`examples/benchmarks_dynamic/DDG-DA/` | **归档候选。**若后续不做动态适应研究，可作为整组示例归档；保留说明和引用跳转。 |
| `examples/data_demo/` | 4.13 KiB | 演示数据缓存和内存复用，说明如何减少重复读取与重复计算。<br>例：`examples/data_demo/data_cache_demo.py`、`examples/data_demo/data_mem_resuse_demo.py` | **保留。**体积很小，建议保留作数据性能问题的参考；无需优先瘦身。 |
| `examples/highfreq/` | 22.92 KiB | 高频行情特征、数据处理及价格趋势预测的演示。<br>例：`examples/highfreq/highfreq_handler.py`、`examples/highfreq/workflow.py` | **归档候选。**当前只做日频研究时，可列为可选示例归档候选；先确认没有个人高频实验依赖。 |
| `examples/hyperparameter/` | 4.38 KiB | 演示自动搜索 LightGBM 参数的流程，分别针对 Alpha158 和 Alpha360 数据。<br>例：`examples/hyperparameter/LightGBM/hyperparameter_158.py`、`examples/hyperparameter/LightGBM/hyperparameter_360.py` | **归档候选。**若已统一使用 backtest/ 的正式调参协议，可保留一个入门样例，或整组归档。 |
| `examples/mlruns/` | 79.14 MiB | 从 examples 下运行过的 MLflow 实验记录，含模型、预测与组合分析结果；当前有 18 次旧运行。<br>例：`examples/mlruns/418161825478917845/meta.yaml（train_model）`、`examples/mlruns/655236239280187225/meta.yaml（backtest_analysis）` | **待核实。**优先核对能否清理：先与正式登记表、冻结模型和结果引用交叉检查，再归档或删除已无用的运行组；以后统一实验跟踪根目录。<br>注意：未纳入 Git，当前约 79.1 MiB；名称中还有 verify_stock_pool 等实际验证记录，不能把它们一律当作可重建示例。根目录 mlruns/ 的清理器不会因此自动覆盖这个独立位置。 |
| `examples/model_interpreter/` | 938 B | 用特征重要性等结果解释模型在看什么，帮助定位模型依赖哪些因子。<br>例：`examples/model_interpreter/feature.py` | **归档候选。**很小，可保留作模型诊断参考；若正式分析已覆盖也可归档。 |
| `examples/model_rolling/` | 4.18 KiB | 演示按时间窗口不断训练新模型，并用任务管理器收集滚动结果。<br>例：`examples/model_rolling/task_manager_rolling.py`、`examples/model_rolling/requirements.txt` | **归档候选。**需要滚动训练时保留；不采用该任务系统时，可作为整组演示归档。 |
| `examples/nested_decision_execution/` | 16.43 KiB | 演示把决策分层执行，例如每周决定股票组合，再按日或分钟拆分成交。<br>例：`examples/nested_decision_execution/workflow.py` | **归档候选。**若不研究多频率执行，可归档示例；基础回测执行接口继续保留。 |
| `examples/online_srv/` | 14.01 KiB | 演示在线模型管理：定期生成训练任务、更新模型并补充预测。<br>例：`examples/online_srv/online_management_simulate.py`、`examples/online_srv/update_online_pred.py` | **归档候选。**当前实盘已由 live_trading/ 管理时，可归档这组框架演示，或保留作未来在线训练参考。 |
| `examples/orderbook_data/` | 26.28 KiB | 演示如何保存和查询不按固定时间间隔到来的订单簿数据，使用 Arctic/MongoDB 后端。<br>例：`examples/orderbook_data/create_dataset.py`、`examples/orderbook_data/example.py` | **归档候选。**若没有订单簿研究需求，可作为较明确的可选示例归档；它目前只有代码和说明，删它不会回收大量行情空间。 |
| `examples/portfolio/` | 5.34 KiB | 演示组合优化：在追求预测收益时控制跟踪误差和风险，而不只是简单买排名靠前的股票。<br>例：`examples/portfolio/config_enhanced_indexing.yaml`、`examples/portfolio/prepare_riskdata.py` | **归档候选。**未来可能研究风险控制时保留；若只保留当前 Topk 策略，可归档为可选示例。 |
| `examples/rl/` | 68.31 KiB | 强化学习的入门 Notebook，用交互方式演示基础工作流。<br>例：`examples/rl/simple_example.ipynb` | **归档候选。**无强化学习学习需求时可归档；它本身很小，不是磁盘瘦身重点。 |
| `examples/rl_order_execution/` | 20.17 KiB | 强化学习订单执行的完整演示，包含训练、数据准备和与 TWAP 等方法比较的配置。<br>例：`examples/rl_order_execution/exp_configs/train_opds.yml`、`examples/rl_order_execution/scripts/gen_pickle_data.py` | **归档候选。**当前不研究强化学习拆单时，可整组归档。若以后生成 data/outputs，再把运行产物与代码分开管理。 |
| `examples/rolling_process_data/` | 7.05 KiB | 演示滚动训练时如何复用原始特征，只重新计算每个时间窗的标准化等处理。<br>例：`examples/rolling_process_data/rolling_handler.py`、`examples/rolling_process_data/workflow.py` | **归档候选。**保留作性能和滚动研究参考，或在停止滚动研究后归档。 |
| `examples/tutorial/` | 29.03 KiB | 从数据、训练到评估的完整入门教学 Notebook。<br>例：`examples/tutorial/detailed_workflow.ipynb` | **保留。**建议保留一个完整入门入口；若对仓库作极简部署，可以移到单独教程包并保留链接。 |
| `examples/Users/` | 0 B | 历史路径拼接留下的空目录套娃，递归检查没有文件。<br>例：`examples/Users/.../Project/qlib/examples/mlruns/（仅目录，无文件）` | **归档候选。**可列为优先确认的空目录清理候选；确认无正在运行任务写入后直接移除空树即可。<br>注意：未被 Git 跟踪；不要据此推断其它名为 Users 的路径也都无用。 |

<a id="tests"></a>

## 7. `tests/`

二级目录以外，本层还存在整条工作流等综合测试。整理时看它验证什么功能；对重型或依赖数据的用例分组运行，通常比删除源码更合适。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `tests/.pytest_cache/` | 292.73 KiB | pytest 自动留下的运行记忆，例如上次失败了哪些测试、发现了哪些测试。<br>例：`tests/.pytest_cache/v/cache/lastfailed`、`tests/.pytest_cache/v/cache/nodeids` | **缓存可重建。**可作为低风险清理候选，删除后下次测试会重建；无需纳入版本管理。 |
| `tests/__pycache__/` | 1.29 KiB | Python 为运行测试生成的编译缓存 .pyc，用于减少下次启动时的重复编译。<br>例：`tests/__pycache__/conftest.cpython-312-pytest-9.1.1.pyc` | **缓存可重建。**可清理，Python 会自动重建；同类缓存应统一处理，不要误删旁边的 .py 测试。 |
| `tests/backtest/` | 2.17 MiB | 本地研究与回测功能的检查：配置、因子和标签、策略选股、指标计算、报告、登记表、模型保留及清理保护。<br>例：`tests/backtest/test_experiment_registry.py`、`tests/backtest/test_cleanup_experiment_artifacts.py` | **保留。**重点保留；可按数据/模型/策略/归档整理入口，减少固定历史路径和过期测试样本或配置，让历史实验测试与当前规则明确区分。 |
| `tests/data_mid_layer_tests/` | 16.88 KiB | 检查原始行情变成可训练样本的中间加工过程：数据加载、清洗、标准化、取训练/验证/测试切片，以及加工结果的保存恢复。<br>例：`tests/data_mid_layer_tests/test_handler.py`、`tests/data_mid_layer_tests/test_processor.py` | **保留。**随 qlib/data/dataset 保留；可改进命名和说明，但不要仅因和 dataset_tests 名字接近就合并删除。 |
| `tests/dataset_tests/` | 1.82 KiB | 检查数据提供层拿到的样本是否基本合理，例如沪深 300 每天的股票数量和价格变化范围，而不是存数据集本身。<br>例：`tests/dataset_tests/test_datalayer.py`、`tests/dataset_tests/README.md` | **保留。**保留核心数据质量检查；后续可把依赖特定旧数据源的阈值与通用取数检查分开，避免旧数据质量问题拖累所有单测。 |
| `tests/dependency_tests/` | 1.24 KiB | 检查第三方库是否还满足框架依赖的关键假设。当前只有 MLflow 客户端创建速度的兼容检查。<br>例：`tests/dependency_tests/test_mlflow.py` | **保留。**保留兼容检查，但可放入依赖升级/环境检查分组，避免把易受机器负载影响的时间阈值当成业务逻辑错误。 |
| `tests/live_trading/` | 4.03 MiB | 实盘系统的安全网：生成信号、计划订单、资金和持仓、成交导入、企业行动、交易桥、调度、只读监控与 QMT 执行。<br>例：`tests/live_trading/test_order_manager.py`、`tests/live_trading/test_monitor_read_only.py` | **保留。**优先保留；可把已停用执行方案的测试随对应实现一起归档，但活动调度、账本、费用、风控和只读边界测试必须持续保留。 |
| `tests/misc/` | 547.70 KiB | 尚未单独分组的检查：既有通用工具、并行取数，也有指数成分历史、行情采集、命令行及密钥使用方式等本地测试。<br>例：`tests/misc/test_csindex_hybrid_history.py`、`tests/misc/test_tushare_credentials.py` | **保留。**可整理分类：将采集/指数相关与通用工具相关检查分开；保留行为明确的测试，先核对覆盖范围再合并重复项。 |
| `tests/model/` | 2.55 KiB | 检查框架的通用神经网络模型能否同时接收普通表格样本和时间序列样本，完成训练与预测。<br>例：`tests/model/test_general_nn.py` | **保留。**只要保留神经网络框架就保留；可作为需要 PyTorch 的可选测试分组，日常不必每次跑。 |
| `tests/ops/` | 5.78 KiB | 检查因子公式中的数学操作是否算对，例如取前一天价格、绝对值、协方差，以及把基准指数价格拿进个股公式。这里的 ops 指公式算子，不是运维脚本。<br>例：`tests/ops/test_elem_operator.py`、`tests/ops/test_special_ops.py` | **保留。**作为 Qlib 数据/因子基础能力保留；可把需要真实样本的慢用例与小型模拟数据用例分开运行。<br>注意：公式算错会影响训练和回测的所有后续结果，不适合作为“当前业务没直接调用”的删减项。 |
| `tests/quant_platform/` | 353.45 KiB | 检查网页平台的后端接口、前端交互、研究指标、分析目录发现/版本保存、账本选择和监控只读保护。<br>例：`tests/quant_platform/test_analysis_directories.py`、`tests/quant_platform/test_research.py` | **保留。**随平台保留；把 Python 接口测试和 Node 前端测试的运行入口说明清楚即可，目录没有必要为瘦身拆掉。 |
| `tests/rl/` | 41.54 KiB | 检查强化学习训练和模拟交易环境：数据排队、日志、训练器，以及把一笔订单分步执行的模拟过程。<br>例：`tests/rl/test_saoe_simple.py`、`tests/rl/test_qlib_simulator.py` | **保留。**与 qlib/rl 一起保留或一起评估可选归档；可从默认快速测试中分离，保留需要 RL 依赖和专用数据的独立测试入口。 |
| `tests/rolling_tests/` | 4.48 KiB | 检查在线/滚动预测更新：补算新日期或修正一段预测时，是否只更新应该更新的部分，已有其它日期能否保持原样。<br>例：`tests/rolling_tests/test_update_pred.py` | **保留。**如果保留 qlib/workflow/online 更新能力就保留；可归入慢测试组，并随在线工作流模块一起决定是否作为可选功能。 |
| `tests/storage_tests/` | 6.11 KiB | 检查 Qlib 如何把交易日历、股票生效区间和各股票行情字段存入磁盘并读回来，防止底层数据读错或写坏。<br>例：`tests/storage_tests/test_storage.py` | **保留。**随 qlib/data/storage 保留；仅清理它运行后生成的可重建测试数据，测试源码应保留。 |

<a id="docs"></a>

## 8. `docs/`

上游手册主要是 Sphinx 的 `.rst`，本地开发/研究/历史方案多为 Markdown。本指南放在 developer 下，不接入当前仅识别 `.rst` 的文档站，避免为目录说明增加构建依赖。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `docs/_static/` | 3.69 MiB | 说明书用到的图片、图标和演示素材，例如流程图、回测示意图、项目标志。<br>例：`docs/_static/img/analysis/analysis_model_IC.png`、`docs/_static/demo.sh` | **保留。**保留被手册引用的素材；可在核对全部 .rst/.md 引用后清理确实没有使用的重复图片或演示素材。 |
| `docs/advanced/` | 19.02 KiB | Qlib 的进阶用法：写因子公式、保存和恢复对象、按任务运行研究，以及避免在历史研究中提前看到未来信息。<br>例：`docs/advanced/alpha.rst`、`docs/advanced/PIT.rst` | **保留。**随 Qlib 基础框架保留；可把暂未使用的功能在导航中标为进阶阅读，通常没有必要为节省空间删正文。 |
| `docs/changelog/` | 31 B | Qlib 上游各版本改了什么的历史记录，用于查兼容性变化。<br>例：`docs/changelog/changelog.rst` | **保留。**保留；如要压缩导航，可放到参考资料入口，而不是删除。 |
| `docs/component/` | 108.68 KiB | 按功能解释 Qlib 各个零件怎么用：数据读取、模型、策略回测、实验记录、结果分析、在线更新和强化学习。<br>例：`docs/component/data.rst`、`docs/component/strategy.rst` | **保留。**作为框架主手册保留；以后若明确停用某个可选模块，可连同对应模块、示例和测试一起评估归档，不单独删说明。 |
| `docs/developer/` | 59.83 KiB | 给维护仓库的人看的操作说明：代码规范、构建镜像，以及 macOS 下避免 Qlib 并行取数出错的运行约定。<br>例：`docs/developer/code_standard_and_dev_guide.rst`、`docs/developer/how_to_build_image.rst` | **保留。**保留，并作为本次目录指南的落点；可补一个轻量 Markdown 索引，把项目内常用说明放在一起。 |
| `docs/FAQ/` | 6.27 KiB | Qlib 常见问题和排查答案。<br>例：`docs/FAQ/FAQ.rst` | **保留。**保留；后续将重复答案合并，并补上本项目确实遇到的环境差异即可。 |
| `docs/hidden/` | 31.11 KiB | 仍留在源码中的旧客户端、在线模型管理和调参说明；当前 Sphinx 配置明确不把这个目录发布到文档站。<br>例：`docs/hidden/client.rst`、`docs/hidden/online.rst` | **归档候选。**可优先核对对应功能和外部引用；确认只是旧版说明后，整体归入历史资料或移除重复内容。<br>注意：已确认 conf.py 的 exclude_patterns 包含 hidden，本地正文未检出对这三篇的直接路径引用；这仍不是外部链接完全不存在的证明。 |
| `docs/introduction/` | 7.97 KiB | 第一次接触 Qlib 时看的概览和快速上手教程。<br>例：`docs/introduction/introduction.rst`、`docs/introduction/quick.rst` | **保留。**保留；可在本地总入口旁标注这是框架入门，避免和当前实盘操作手册混在一起。 |
| `docs/reference/` | 4.21 KiB | 框架接口的查字典入口，列出数据、存储、模型等类和函数的说明。<br>例：`docs/reference/api.rst` | **保留。**保留；可以维护导航和失效接口引用，不建议为了目录数量删掉 API 参考。 |
| `docs/research/` | 11.43 KiB | 适合长期参考、与单次实验解耦的研究解释。当前只有一篇市场风格识别与波动滞后关系的说明。<br>例：`docs/research/regime/volatility_and_detection_lag.md` | **保留。**保留通用研究知识；单次实验笔记放到 models/ 或 strategies/ 中所属实验的 docs/，此处只留通用结论和链接。 |
| `docs/start/` | 20.01 KiB | 安装、初始化数据、下载数据和接入自定义模型的入门步骤。<br>例：`docs/start/installation.rst`、`docs/start/getdata.rst` | **保留。**随框架保留；可在说明中区分上游通用环境和当前 Mac 环境，减少照抄命令造成的困惑。 |
| `docs/superpowers/` | 1.11 MiB | 本项目过去讨论过的设计方案、实施计划和修复报告。内部按 specs（设计）、plans（执行步骤）、reports（结果）保存，共 102 个已跟踪文件。<br>例：`docs/superpowers/specs/2026-10-28-quant-platform-design.md`、`docs/superpowers/plans/2026-10-29-bt-v5-top5-cutover.md` | **归档候选。**是文档整理的主要候选：按实盘、平台、研究等主题补目录；已完成方案标记完成并归档，合并重复说明，让当前操作手册优先展示。 |

<a id="mlruns"></a>

## 9. `mlruns/`

数字编号不是无用目录。以下用途已从各目录 `meta.yaml` 的实验名称核对：5 组是 B6-M 的五个随机种子训练，另 2 组是正式回测记录。这里不涵盖独立的 `examples/mlruns/`。本轮仅缩减 experiments 归档，没有删除这里；纯全A清理器提出的旧运行目录候选需后续单独讨论。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `mlruns/.trash/` | 0 B | MLflow 的已删除实验回收区域，目前为空。<br>例：空目录。 | **清理器管理。**若以后有内容，先核对没有活跃引用，再按清理器计划清空；当前没有明显可回收空间。 |
| `mlruns/193294180634344660/` | 3.31 MiB | B6-M 模型基线的 seed1000 训练运行<br>例：`mlruns/193294180634344660/meta.yaml`、`mlruns/193294180634344660/<运行ID>/artifacts/` | **清理器管理。**保留当前完整保留组；基线切换时由统一清理器重新评估，避免漏掉某个种子或回测证据。 |
| `mlruns/202999456416839429/` | 3.42 MiB | B6-M 模型基线的 seed42 训练运行<br>例：`mlruns/202999456416839429/meta.yaml`、`mlruns/202999456416839429/<运行ID>/artifacts/` | **清理器管理。**保留当前完整保留组；基线切换时由统一清理器重新评估，避免漏掉某个种子或回测证据。 |
| `mlruns/347176805538849999/` | 35.90 MiB | B4-S CSI1000 正式策略回测及其图表记录<br>例：`mlruns/347176805538849999/meta.yaml`、`mlruns/347176805538849999/<运行ID>/artifacts/` | **清理器管理。**保留当前完整保留组；基线切换时由统一清理器重新评估，避免漏掉某个种子或回测证据。 |
| `mlruns/383955905181939329/` | 3.53 MiB | B6-M 模型基线的 seed3000 训练运行<br>例：`mlruns/383955905181939329/meta.yaml`、`mlruns/383955905181939329/<运行ID>/artifacts/` | **清理器管理。**保留当前完整保留组；基线切换时由统一清理器重新评估，避免漏掉某个种子或回测证据。 |
| `mlruns/440511413852618761/` | 6.92 MiB | 当前 s5 对应的五种子合成信号 Top5/f100 官方回测运行<br>例：`mlruns/440511413852618761/meta.yaml`、`mlruns/440511413852618761/<运行ID>/artifacts/` | **清理器管理。**保留当前完整保留组；基线切换时由统一清理器重新评估，避免漏掉某个种子或回测证据。 |
| `mlruns/657807164385264418/` | 3.36 MiB | B6-M 模型基线的 seed4000 训练运行<br>例：`mlruns/657807164385264418/meta.yaml`、`mlruns/657807164385264418/<运行ID>/artifacts/` | **清理器管理。**保留当前完整保留组；基线切换时由统一清理器重新评估，避免漏掉某个种子或回测证据。 |
| `mlruns/708236692900899016/` | 4.21 MiB | B6-M 模型基线的 seed2000 训练运行<br>例：`mlruns/708236692900899016/meta.yaml`、`mlruns/708236692900899016/<运行ID>/artifacts/` | **清理器管理。**保留当前完整保留组；基线切换时由统一清理器重新评估，避免漏掉某个种子或回测证据。 |

<a id="logs"></a>

## 10. `logs/`

仓库级日志目前主要是数据维护与修复记录。普通日志、原文件副本和长期审计材料应采用不同保留方式。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `logs/backtest/` | 0 B | 通用回测日志目录，目前为空。<br>例：当前空目录。 | **待核实。**可保留为自动生成的日志出口；若要简化结构，先统一写入位置再去掉这个占位目录。 |
| `logs/data/` | 182.98 MiB | 行情下载和数据维护的按日日志，也混有 repairs/ 下的修复前文件副本、数据来源和核验记录。<br>例：`logs/data/2026-10-30.log`、`logs/data/repairs/20261026_index_revision/` | **归档候选。**先把普通 .log 与修复备份分开看；旧正常日志可按周期压缩，修复前全量副本应在确认回退窗口结束后再归档或缩减。<br>注意：这里不全是可直接丢弃的文字日志；部分文件是数据修复的来源证据。 |

<a id="ideas"></a>

## 11. `ideas/`

本层没有二级目录。当前有 `中性化`、`策略优化`、`特征优化`、`损失优化` 四个无扩展名的草稿文件，其中两个为空；整个目录被 Git 忽略。先保留或整理原文到 `docs/research/`，再决定处理空占位文件。


<a id="github"></a>

## 12. `.github/`

这些文件控制 GitHub 上的协作入口和自动检查。后续可以减少不需要的检查组合，但应先区分代码验证、文档构建、发布和仓库维护。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `.github/ISSUE_TEMPLATE/` | 1.98 KiB | 提交 bug、功能建议或使用问题时显示的填写模板。<br>例：`.github/ISSUE_TEMPLATE/bug-report.md`、`.github/ISSUE_TEMPLATE/question.md` | **保留。**保留或按个人仓库实际使用情况简化，不是优先瘦身目标。 |
| `.github/workflows/` | 13.17 KiB | 在推送或合并请求时自动运行测试、格式检查及文档构建，也包含发版和维护流程。<br>例：`.github/workflows/test_qlib_from_source.yml`、`.github/workflows/test_qlib_from_source_slow.yml` | **保留。**先保留必要验证；后续可评估上游大矩阵、pip 安装测试、发版和陈旧 issue 自动处理是否都符合此仓库用途。 |

<a id="git"></a>

## 13. `.git/`

这里是特殊管理区，列出它是为解释磁盘占用和用途，不建议把内部目录逐个手工删除。当前 `.git/worktrees/` 对应两个仓库外工作区，仍可被其他工作使用。

| 二级目录 | 内容大小 | 内容作用与示例 | 后续整理建议 |
|---|---:|---|---|
| `.git/cursor/` | 8.14 MiB | 编辑器附加的本地检索索引；这里能看到 index.bin、postings.bin 和 metadata.json。<br>例：`.git/cursor/crepe/` | **待核实。**可先核实编辑器是否能自动重建，再作为缓存候选处理；当前体积不大，优先级低。 |
| `.git/hooks/` | 22.96 KiB | Git 操作前后可调用的小程序模板，比如提交前检查。目前这里是 13 份 .sample 示例。<br>例：`.git/hooks/pre-commit.sample` | **保留。**保留即可，只有几十 KiB；若将来启用正式钩子，单独注明其作用。 |
| `.git/info/` | 2.92 KiB | 本机的 Git 辅助信息，包括本地忽略规则和引用摘要。<br>例：`.git/info/exclude`、`.git/info/refs` | **保留。**交给 Git 维护，不作为业务目录整理对象。 |
| `.git/logs/` | 91.33 KiB | Git 的操作历史，记录分支指针、HEAD 曾经到过哪些提交，便于找回误操作。<br>例：`.git/logs/HEAD` | **保留。**保留恢复能力；如以后确需压缩历史，使用 Git 自身维护流程并先讨论恢复要求。 |
| `.git/objects/` | 694.83 MiB | Git 保存历次文件内容和提交对象的地方，占 .git 的绝大部分体积。<br>例：`.git/objects/pack/` | **保留。**当前不手工整理；删除业务文件不会立刻让旧版本从这里消失，历史压缩应作为另一项专门工作。<br>注意：不要按文件日期、大小直接删除对象文件。 |
| `.git/refs/` | 932 B | 各分支、标签及远端分支所指向的提交位置。<br>例：`.git/refs/heads/`、`.git/refs/remotes/` | **保留。**保留，用 Git 命令管理分支和标签。 |
| `.git/worktrees/` | 395.64 KiB | 登记其他 Git 工作区，保存它们的分支状态、索引等内部信息；当前还有两个外部工作区。<br>例：`.git/worktrees/qlib/`、`.git/worktrees/qlib1/` | **保留。**与实际工作区一起管理；只在确认工作区不再使用时，通过工作区管理工具清理。<br>注意：根目录 .worktrees 已移除，不代表这里也该删除；两者不是同一层目录。 |

## 根目录文件与下一轮讨论方式

根目录的 `AGENTS.md`、`README.md`、`Makefile`、`pyproject.toml`、`.gitignore` 是文件，未计入上述目录数量。
它们分别约定协作规则、项目入口、常用命令、Python 包/依赖和忽略规则，建议保留。
尤其注意：`.gitignore` 中有 CSV、模型文件的通用忽略规则，也有永久研究证据的精确例外；移动文件时要核对新位置是否仍被 Git 收录。

后续可以直接逐行讨论：这个目录现在是否还用、需要保留哪些原件、能否重建、准备迁到哪里、哪些代码和文档要跟着改。
对源码先查调用和测试；对数据先查来源和重建方法；对模型/回测产物先查登记表和保留清单；对历史文档先查是否仍是唯一说明。

本页依据本地目录、代表文件、Git 跟踪清单、配置与代码引用，以及 MLflow 的 `meta.yaml` 梳理。
体积与文件数来自只读扫描，含缓存和隐藏文件；本轮未读取模型对象、未运行训练或回测，也未执行产物清理。
