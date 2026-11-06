# AI 实验 Harness（操作协议 v2）

状态：v1 已实现并验证；v2 于 2026-11-01 为用户授权的首轮全A多期限混合试验补充组合预测来源和共享 fit 预算。v1 合同/状态继续兼容，不自动升级；本工具仍不执行研究命令。

## 目的与边界

用户提出研究方向，AI 将其转成可证伪假设，在有限预算内设计、执行、评估、反思和继续试验，最后交付可追溯的结论、正式登记、报告与清理证据。新会话可以依据持久化记录继续，不靠聊天记忆恢复。

在本仓库直接提出方向即可，例如：“使用实验 harness 检验缩短训练标签是否改善全A模型，沿用当前基线，只改标签，最多三个候选。”AI 负责准备合同、运行命令和登记材料，用户无需手写下面的 JSON。续跑时说明原方向或 campaign；AI 先查找原状态，不能新建账本假装没有历史成本。

实验口径的唯一事实来源仍为 [EXPERIMENT_STANDARD.md](../../backtest/EXPERIMENT_STANDARD.md)。本文件只规定执行流程，不增加收益通过门槛、不修改评测窗口，也不授予基线晋升或生产操作权限。当前状态以仓库规则及最新明确的基线选择事件为准，不把最高版本号当当前版本。

2026-11-01 首版搭建只实现 harness，未启动研究或创建示例登记。随后用户已授权首次真实试验；真实实验合同、结果和问题记录各归本任务实际候选档案，不把下面的操作模板当作实验事实。

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

把任务合同写入本轮真实主实验的 `configs/harness-plan.json`，进度写入其 `docs/harness-state.json`，不另设第二份正式实验 registry。一次用户发起的研究迭代（同一研究问题的一批候选及其必要诊断）优先共用一个平台入口；内部候选、种子、技术尝试和派生账户保留稳定身份、实际配置、结果与成本。需要独立研究问题、轨道或选型入口时说明拆分原因，不因多跑一个种子、重试或补充诊断就新增平台实验。

平台展示、科学候选和执行作业是不同层次。一个入口中的七个H仍是七个科学臂，35次fit仍记35次。现有工具要求独立variant/exp_id的路径不得改填父ID绕过校验；聚合登记复用既有父实验内部来源和派生回执，缺少原生生命周期支持时按明确有限的补充账本记录能力缺口。新增正式工具或平台结构另提具体方案，不在收尾时顺带扩建。

使用补充账本前明确本轮校验责任，至少记录父实验与内部候选/attempt/job的关联、冻结输入与判据、预算起点与累计耗用、每次启动及终态证据、逐候选登记/报告回执、停止原因和清理回执。关闭前逐项核验候选覆盖、失败证据、预算及保留依赖；未覆盖项列为待办，不将适配器人工核验声称为正式工具已有能力。

计划与初始化状态合计冻结：方向原文、campaign ID、轨道、对照稳定 ID 与事件指纹、允许/禁止改动、假设、指标和判读规则、固定种子、训练与评估范围、数据版本、规则/相关源码/配置/模型/预测指纹、任务预算、临时目录及负责人标识。工具必需字段见下文；研究细节、负责人和临时目录可写入额外计划字段或列入 `inputs` 的冻结配置/文档，AI 须核实这些内容，工具不会推断其科学语义。

每轮候选运行前冻结本轮计划；先前计划、失败和反思只追加，不事后改写以适配结果。配置变化产生新的科学变体；相同配置的技术重试保留原变体和新的 attempt ID。状态更新使用原子写与独立锁，状态/合同损坏或并发冲突必须报错。

2026-11-01 首轮试用的输入预检要求：

- 2026-11-03 版本误记复盘：在 `init` 和任何 fit 之前，由实际评估适配器读取数据版本并核对“计划版本＝实际日历末日＝正式评估将输出的版本”，同时保存日历及真实数据依赖指纹。禁止从上一轮复制日期文本。文件SHA未漂移不代表初始化时版本文本正确；现有状态工具只校验声明与指纹，不会自动理解数据日历语义。发现误记不得改写冻结计划或评估JSON迎合门禁；保留失败回执，按实际终态登记，后续诊断明确记录实际版本。
- 分别盘点历史指标、真实模型、逐种子预测与缓存；历史结果不能代替缺失信号。需要重新物化时先声明数据版本差别、统一配方、准备/拟合/推理/评估成本及文件归属，全部耗用原 campaign 墙钟预算。
- 核对年度块、替代标签和训练器隐式生成的 valid/cache 文件，记录来源、SHA及清理所有权；已有文件先核验，生成后保存回执，恢复时重验。正式登记前核对 metadata 的资产路径基准；仓库相对清单须按归档协议声明 asset_snapshot。
- 训练、早停和正式推理分别记录实际有序索引、特征列/dtype/数值、标签和有效/可成交掩码签名，以及生成配方和数值环境。不能用早停面板替代正式推理输入做“精确预测复现”；需要比对时从正式推理入口取得同一面板，再用保存的真实模型核对。行数、文件大小或版本日期相同均不能代替内容核验，mtime只能定位线索，不能证明具体原始字段变化。
- 把股票/上市龄等有效样本掩码与 CSRankNorm 的先后顺序列入配方；同公式在不同截面排名仍是不同输入。2026-11-02 用户明确不要求七个H取共同样本：默认按各H自身有效标签及既定基础样本规则训练，记录各H最终样本与排名截面差异；只有研究问题明确要求严格等样本时才预先冻结交集，不把交集作为所有期限实验的强制前置。无论哪种方案，均验证特征列序、标签有限性与排名/并列组。
- 区分共同基础索引、各目标最终非空训练掩码和推理融合掩码。分别记录目标缺失分布、掩码指纹及五种子实际训练摘要；共同基础不证明最终训练行或排名截面相同。若研究要求严格等样本，须在拟合前冻结相应输入；不能看到差异后插补、改交集或重排已用标签。历史训练掩码未知就保留未知，命中率通过代码门槛不证明缺失无偏。
- 无拟合或收益曝光前的范围内物化修复，先等准备进程退出，保存修复前清单、操作来源SHA、前后文件SHA及特征/索引不变证据，再核验生成物；不得改写冻结合同或借机改权重、早停、窗口。若修复改变科学含义，回到新候选或授权流程。
- 组合前核对所有分量和种子的索引唯一、排序一致、有限掩码相同，并明确种子内期限融合和外层种子融合的归一化顺序。共同NaN可保留并审计；不能用 skipna 让有效权重逐行变化。
- 历史 booster 的 Column_i 只证明维数相容，不能证明语义列序；分别核对本轮列序、历史来源及同源推理诊断，保留仍未证明的部分，不强制新旧预测相等。
- 2026-11-02 H20 差距复核补充：共同样本内的配对控制不等于历史基线复现。改动样本交集或标签排名截面时，应在预算中预留旧配方桥接，并在全网格物化前核验；历史原始快照缺失时明确不能精确复现。对照存在明显且未解释的偏差，须登记为未决科学问题并限定结论范围，不能用执行完成、文件SHA一致或“不同样本”一句话替代解释。差异容忍与停止判据须事先规定，不能看完收益后选择有利阈值。
- 科学验收同时检查逐种子最佳树数、预测并列/退化、头部选股稳定性和合成后相对分量的表现。极早停止或合成明显弱于全部分量是需要调查的信号，不自动判定代码错误；保留五种子原结果，不能事后删种子或改权重。全局 RankIC/排名相关性接近不能替代 Top3/Top5 的一致性检查。这是操作协议要求，现有状态工具不会自动计算或执行这些科学门禁；本轮证据见 [H20 差距复核](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)。
- 模型指标接近不能宣告历史基线复现或账户表现恢复。桥接同时检查真实模型结构、早停样本、头部名单和固定策略实际持仓；回测核对每组信号各自匹配输入SHA，再核对跨组过滤索引/掩码、账户/费用/策略参数及逐日净值。参数字典、训练行数一致但第一棵树已不同，应继续标记上游复现未决；缺少历史训练输入和数值环境时不得猜定唯一原因。
- 2026-11-03 [v2 取证](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)：前复权末端因子变化可重写历史 float32 价量，进而改变特征、收益并列及排名标签；训练日期/公式相同不证明输入相同。分别冻结特征缓存、标签、valid、正式推理的真实内容与生成来源，不把模型训练日当作缓存生成日。旧锚点在当前原始 CSV 上的回放只能验证机制，不能冒充历史快照；缓存 horizon/样本协议从 manifest 配方字段读取并断言，禁止按文件顺序推断。历史对照存在输入漂移时，先做同一冻结快照 H20 的可复跑核验，再扩 H；保留历史 v2，不用当前重训自动替代基线。
- 同日用户纠正因果边界：多数比例/位置特征在统一复权缩放下理论不变，float32 舍入差异不能直接证明性能差距主因。须区分输入位差、重复拟合稳定性、早停选轮和实际账户影响；必要时做预先冻结的同输入复跑、固定树数或单变量输入干预。历史数据无法完整恢复时保留未决项，不能把“找到差异”写成“主要原因已查清”。
- 异常短树早停先分开核验分量贡献和树数机制：来源双向替换只证明已保存信号分量的影响；隔离树数时，在相同训练/标签/权重/早停输入上重放一条轨迹，先核对原前缀树及正式预测，再比较预先固定的截断位置。保存每一轮实际早停分数、最佳轮、触发停止轮和完整训练预算；不能只留下最佳轮及每20轮日志。曲线事后最高点仅作诊断，不自动成为新候选。减少种子的消融单列为补充，不冒充正式五种子结果。
- 对比目标曲线先统一指标定义、标签、特征和可成交掩码，区分原始训练日志与保存模型在同一输入上的前缀重算。只保留最佳轮的历史模型没有其后的耐心尾段；未达到新的停止点时标为截断观察，不能把最后可见轮当成新规则的最终最佳轮。
- 早停名义窗口相同不代表前视边界相同。H5 验证也须核对 t+6 退出日在窗内；现有正式训练入口从 2026-11-04 起按评估器相同规则截断，并在验证缓存配方、训练摘要中记录窗口策略。原 1454 日缓存保留为历史复现证据，不能作为修复后的有效缓存静默复用。修复口径不证明异常早停已解决，须在同一条轨迹上另验选轮影响。
- 头部早停记录 TopK 边界同分天数，并用固定、非收益驱动的同分优先序对照检查敏感性。全截面 RankIC 稳定不证明 Top5 稳定；辅助 RankIC 如使用可成交子宇宙，不能冒充正式全局 RankIC。反序或随机序仅是诊断，不能事后挑更高收益的顺序作新规则；只延长 patience 也不保证最终不会仍选到浅树。改进方案须预先冻结并完成 H20 五种子验证，再扩 H。
- 逐分量记录预测实际生成来源，区分训练快照、预测保存日与市场评估快照；不能用某个日历末日统一标注全部缓存预测。发现来源说明错误时保留原冻结合同及指标，追加更正审计，并核验真实过滤后的选股与执行排序是否受影响；不能只用原始排名或相同行数代替该验证。
- 在冻结结果前，将真实收集器输出交给正式报告渲染器预检完整结构。harness的有限指标门禁不能替代报告协议：固定s5产物还需提供正式报告器读取的`ensemble.session_dir`、可达报告路径及必要净值证据。已登记后才发现格式问题时保留原件、追加兼容格式及登记事件，不覆盖历史计算。仅在清理审计中留下“报告已完成”不够，须重建并核验链接和实际渲染。
- 资源预检包括大帧原始/推理/学习副本、缓存首次构建和后续复用。取数进程参数不等于内存上限；观察整棵进程树、实际峰值与系统换页，标明GB/GiB及采样时间，不把系统swap全部归因于单进程，也不凭主进程RSS或无报错判断健康。

### 3. 执行与核验

AI 调用既有正式入口，默认串行执行昂贵训练。先记录启动意图、命令、输入身份、日志和预期输出，再启动子进程；中断恢复先核对子进程及已有输出，不能看到文件就跳过或重跑。

存在共享输入拟合时先冻结 execution_budget，逐次 fit_start/fit_result 记账，失败、中止和重试累计消耗拟合上限；在创建 running 启动意图前检查剩余墙钟及收尾余量，不能只在候选启动时检查。

固定组件控制仅作物化与诊断时，报告科学候选数、实际fit启动/重试及唯一模型数，避免隐藏工作量。若独立选择某个组件、改变其配方或据控制结果生成新权重，它成为新科学候选，须重新冻结并计入候选预算。

- 全A当前 v2 训练须显式填写 H20、single、`top5_h5_net_ann`、`eval_window` 等冻结参数；不沿用入口的 H40/RankIC 默认值。实验改动轴之外的参数保持不变。
- 模型实验固定五种子。核对五个成功模型、预测覆盖与索引、配置和数据身份一致；官方数字取五种子日截面 z-score 等权合成信号的一次评估，不用种子指标均值替代。
- 官方评估使用 `eval_ic_multi_pool.py`；全A主格 Top3×H5、分年/分风格、全局 h5 RankIC、过滤与标签窗口截断遵循实验标准。
- 执行层只比较冻结模型/信号下的策略改动，按 s5 对照的账户、费用和窗口解释 CAGR、夏普、回撤、Alpha/Beta 等，不与模型主格算术年化直接相减。
- 目录存在或退出码为零不构成完成证据；缺种子、非有限主指标、缺官方 ensemble、协议不一致分别记录为无效或失败，不填零、不伪造结论。
- 旧预测与当前真实输入不一致时保留旧档案；必要的同源对照用全部固定种子的旧模型在当前正式推理面板重算，再统一评估模型和账户。不能把旧预测与新数据结果直接拼成“同批配对”，也不能放宽预测容差隐藏真实变化。只读诊断扩展要记录为何变更检查来源/范围、哪些容差保持不变；技术核验不改变科学阈值或产生晋升权限。

### 4. 评估、反思与下一轮

每个候选结束后，先保存可移植指标、配置指纹和技术失败摘要，正式追加登记并重建对应详情/总报告，再决定下一轮，避免整个方向中断后漏记失败。

AI 每轮说明：观察到了什么、原假设是否得到支持、证据限制、失败属于研究结果还是执行问题、下一轮只改什么以及该改动为何可能带来新信息。没有改善不等于机械扫更多参数，改善也不等于已获准晋升。

先判断指标是否合理，再解释好坏：核对定义与单位、样本和收益归属日期、前视退出边界、费用/资金暴露及对照可比性。异常早停、极端收益、相近模型指标却悬殊的账户表现等，应触发适度排查。把已知事实与猜想分开，提出可证伪解释，在原授权和剩余预算内优先做小规模、单变量验证，并预先记录输入、参数、判据及成本；不把每个意外结果都扩成无边界研究。

诊断结果可说明正式指标的局限，不能据此事后换指标、挑窗口、放宽通过阈值或改写历史结果。诊断指标单列，正式口径变更仍按实验标准取得授权；用户要求停止时记录未决项并收尾，不继续验证新猜想。

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

轨道仅支持 `all_a_model` 和 `all_a_strategy`。后者档案须为 `kind=strategy`、路径在 `strategies/`，对照来自最新明确 `current_bt` 的 s5 稳定 ID `baseline/phase-m-v1-bt-v5`；不是重选模型。CSI1000 轨道明确拒绝初始化。工具自动冻结当前基线事件、AGENTS、实验标准、本协议及显式输入；合同 schema 支持 `1`、`2`，初始化状态沿用合同版本。`budget: {}` 使用上文默认值，可覆盖字段为 `max_rounds`、`max_variants`、`max_attempts_per_variant`、`max_wall_hours`、`max_no_new_evidence_rounds`、`concurrency`；只接受 `concurrency=1`。

schema v2 的普通五 session 模型输出、策略输出及原生命周期不变；`jobs` 初始为空。只有 v2 的全A模型合同可选增加下列**执行成本**预算，普通合同可省略；v1 若声明该字段会报错，不会静默忽略：

```json
{
  "schema_version": 2,
  "execution_budget": {
    "max_model_fits": 22,
    "new_label_horizons": [1, 5, 10, 40]
  }
}
```

这只是合同字段片段，不能单独 `init`。`max_model_fits` 为正整数，`new_label_horizons` 为不重复的正整数列表。首轮真实试验预留 H1/H5/H10/H40 × 五种子共20次新 fit、最多2次基础设施重试；H20复用经过指纹验证的现存五种子。四个冻结 BLEND 配方各计一个科学候选，共享控制/分量模型是这些候选的物化依赖，不另计科学候选。已有 fit 及失败重试的成本在整个 campaign 累计，换 variant、attempt 或聊天都不能重置。

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

启用 v2 执行预算时，每一次**实际调用训练入口之前**另发 `fit_start`，失败重试必须用新 `job_id`；同一次调用中断恢复先核实原进程，不直接重复调用。事件示例：

```json
{
  "type": "fit_start",
  "exp_id": "example/h10-probe",
  "job_id": "h10-s42-try1",
  "seed": 42,
  "label_horizon": 10,
  "command": ["/opt/anaconda3/envs/qlib/bin/python", "backtest/scripts/train/train_regime_arm.py", "--seed", "42", "--label-horizon", "10"]
}
```

示例 argv 是字段模板，实际命令仍须包含完整冻结配方。`fit_start` 重新检查身份漂移、截止时间和活动状态，要求 `exp_id` 有 running attempt、种子属于固定五种子、期限在 `new_label_horizons` 内、job ID全局唯一；账本记录父 attempt ID、时间及 argv。默认全 campaign 串行1个 fit。每个获准 `fit_start` 即消耗一次 `max_model_fits`，失败、中止和重试均计数，不按成功模型数计费；调用没有真正启动时也保留启动意图与中止证据，不删除账项返还额度。

调用结束后先保存真实模型/日志或档案证据，再发：

```json
{
  "type": "fit_result",
  "job_id": "h10-s42-try1",
  "outcome": "failed",
  "evidence": ["backtest/experiments/models/training-target/example-h10/docs/h10-s42-try1-failure.json"]
}
```

`fit_result` 只接受已 running 的 job，outcome 为 `succeeded/failed/interrupted`，非空 evidence 的文件必须存在并位于父候选档案或真实 `backtest/result/` 模型/日志路径，写入指纹。漂移、预算到限或 `stopping` 后仍允许如实结束已有 fit；不执行开跑检查。父 attempt 的 `result`、轮次反思和收尾都拒绝仍有 running fit。v1 或未声明执行预算的 v2 不能用 fit 事件。训练是否实际成功、argv 是否与声明同义、失联进程是否已退出仍由外部适配器核实，工具不提供 shell 或模型语义沙箱。

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

- 普通模型：`sessions` 恰好包含五个不同 `session`，`seed` 覆盖固定五种子，`data_version` 匹配；`pools.all.ensemble.head["3"]["5"]` 的 `net_ann/net_ann_vol/net_sharpe/ann/turnover` 及 `pools.all.ensemble.h5.rank_ic_mean` 为有限数值，主格 `n_days` 为正整数。v2 组合来源见下节。
- 策略：`seeds` 为固定列表，`model_ref` 等于初始化时当前策略基线的模型引用，`evaluation_mode=historical_selection_window`；`ensemble.full_period` 的 `annualized_return/sharpe_ratio/max_drawdown/alpha/beta/benchmark_cumulative_return` 有限，`n_days` 为正整数。

这些是结构与身份检查，不重新计算指标或查验模型训练语义；任意日志也不能替代官方评估。AI 必须核对真实五种子产物、预测覆盖、合成方式和协议一致性后才可登记 `completed`；`regress` 等研究结论另写正式事件，任何成功标记均不授予晋升权限。其他 outcome 为 `failed`、`interrupted`、`invalid`、`cancelled`，均需非空 `reason` 与现有证据，例如档案内 `docs/attempt-a1-failure.json`。每次 attempt 用独立、稳定的证据文件名，重试保存 `evaluation-a2.json` 等新文件，不能覆盖失败日志或旧评估；收尾会重新校验旧证据指纹。尚未启动的计划可直接以 `cancelled` 留痕，不能悄悄移除；部分种子恢复同一次运行须先核实原进程及输出，确需新的技术重试时先结束并登记、报告原 attempt。

### v2 多期限组合预测来源

普通模型评估继续要求五个不同真实 session 的 `sessions`，不能伪造五个“组合 session”绕过检查。多期限信号只有在 v2 合同下显式使用 `prediction_source_type=multihorizon_seed_groups_v1` 才进入独立来源分支；评估 JSON 也须 `schema_version=2`，不再填 `sessions`。保留正常 `data_version`、`pools.all.ensemble.head.3.5` 有限主指标及 `ensemble.h5.rank_ic_mean`。

该评估 JSON 额外包含 `composition_config: {"path": "所属候选/configs/blend.json", "sha256": "实际SHA-256"}`，以及按固定种子顺序排列的**恰好五个** `seed_groups`。每组字段为 `seed`、组合输出的 `prediction_path/prediction_sha256` 和 `components`；每个真实分量含：

```json
{
  "label_horizon": 5,
  "seed": 42,
  "session": "实际训练session名称",
  "session_dir": "backtest/result/实际训练session名称",
  "model_path": "backtest/result/实际训练session名称/run_01/artifacts_root/artifacts/trained_model",
  "model_sha256": "实际SHA-256",
  "prediction_path": "所属候选/metrics/实际H5-s42预测.pkl",
  "prediction_sha256": "实际SHA-256",
  "weight": 0.3333333333333333
}
```

这仍是字段示例，不是可登记证据。`composition_config` 必须与该 variant 的 `configs` 中**一份冻结 JSON 文件**的路径、SHA及当前字节一致，内容包含：

```json
{
  "signal_blend": {
    "horizon_weights": {"5": 0.3333333333333333, "10": 0.3333333333333333, "20": 0.3333333333333333},
    "construction": "per_seed_daily_zscore_horizon_weighted_then_daily_zscore_equal_seed"
  }
}
```

期限键为不带前导零的正整数文本；权重均为有限正数且和为1（浮点绝对容差 `1e-12`）。JSON对象字段顺序不决定语义；`components` 必须按期限数值升序，全部组使用相同、不重复的期限及对应权重，且每条分量 seed 与所属组相同。检查每份模型、分量预测和组合预测文件的实际SHA、重复来源路径及输出别名；session目录须在 `backtest/result/`，模型在对应真实session内，`meta.json.session_name` 与 `regime_adapt.seed/label_horizon` 须对应。成功回执与登记核对均重新检查这些证明。

构造含义是同种子内各期限预测先做日截面z-score、按冻结权重合成，再由原官方入口对五个种子组合信号做日截面z-score等权合成并评估。预测数值、完整索引与缺失掩码相等、实际构造是否符合该声明，由真实研究适配器验证并保存证据；stdlib harness 不加载庞大 pandas 预测或重新计算指标。字段检查不能代替数值核验。

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

每个实验（包括失败和中止）通过 `register/experiment_registry.py:append_registry` 追加正式事件；已冻结详情不覆盖。本轮优先维护一个平台主入口，内部每个候选及其失败/中止、技术尝试、派生回测均在主实验中登记和追溯，不能只留下最终优胜者。原生工具仍要求独立登记的旧路径按其真实身份完成，不静默改写历史ID或声称未实现的聚合能力已经可用。失败没有指标时保留原因和现有证据，不为了凑完整结果填造数字。对应报告必须能看出该实验未完成或无效。

使用既有报告生成器；历史写死配方的 `register_*` / `promote_*` 和报告 `--spec` 不能直接当新实验的通用配方。第一版若缺少适用详细报告入口，复用现有渲染函数并在仓库外任务目录运行适配脚本，保留最终 HTML，收尾删除适配脚本；反复出现同样适配需求时再提议正式化，不为每次实验新增仓库工具。

方向报告说明尝试范围、全部候选和失败、对照差异、分年/分风格局限、下一步建议、停止原因与未完成事项；登记和 HTML 的官方数字必须来自正式指标。方向总结保存在首个真实实验的 `docs/` 中，其他候选由稳定 ID 和路径关联。

清理顺序固定：保存必要证据 → 登记全部终态 → 重建/核验报告 → 确认所有相关运行已结束 → dry-run → 核对并保存精简审计 → 按同一模式执行 → 清理本任务临时材料 → 核验 Git 状态和引用。

当前全A平台 `result/` 使用 `cleanup_experiment_artifacts.py --result-retention platform --only-result`；它保护全部平台维护实验及现存必要依赖，不等于删除所有落选实验，且不处理 `mlruns/`。不隐式调用旧CSI默认模式；没有适用的 MLflow 安全清理方案时明确记录该项受阻，不谎称全部清理完成。存在其他活动研究任务或清理门禁错误时拒绝执行全局产物删除，不换用手工批量删除。

一次性工具只放仓库外专用临时目录，创建时登记用途和路径；删除前检查引用，只清理本任务确知的材料。保留配置、正式指标、登记、HTML、精简审计及规范保护的模型/预测，不保留无后续用途的脚本、fixture、备份和工作副本。

stop 后仍须等全部 finalize、报告和数值审计工具结束，再清理本任务 audit-only 临时适配器及缓存；即使它们列在 plan.inputs，也不改写原路径/SHA，保留历史来源、物化清单及删除审计。stop/cleanup/close 不运行开跑输入检查，不能因此把 stopping 当成可恢复开跑。

成功 composite 的冻结配置、真实 session/meta/模型、分量原始预测与五个种子组合预测，以及所有 attempt evidence、登记记录、最新详情/总报告链接和 cleanup audit 都会参与收尾核验，须保留；必要 manifest、最终ensemble及对照评估也按研究依赖保护。删除前逐项核对实际证据引用，不能仅凭文件名判断临时性。

已终态fit的`fit_result.evidence`同样在反思、cleanup和close时重验，包括失败与中断作业。默认以精简的持久训练回执或日志作为fit证据；不要把计划清理的临时文件作为唯一证据。已登记的证据缺失或变更时必须停止收尾并恢复真实证据，不能只保留fit计数或伪造新日志。

cleanup.outcome=complete 要求审计 result=applied/no_changes、mlruns=handled/not_applicable、temporary=cleaned；--only-result 不等于已处理 MLflow。受阻要写 blocked、具体 reason 和真实待办；提交后修改 audit 或更新正式登记，应重新取得相应回执再 close。

运行锁保留稳定inode，不作为研究文档或清理对象。正式元数据刷新排除确切harness sidecar，保留其它用户.lock证据；修正派生清单使用既有ensure_experiment_metadata，不追加研究事件或改写冻结record。

### 提交前交付 review（2026-11-06 用户反馈）

实验结束后先完成必要清理，再向用户交付可审阅的变更清单。清单按实际文件说明用途，区分训练/评估行为、harness工具与测试、配置与研究证据、登记/报告和文档；列明新增、修改、删除、验证结果、未决问题及仍留在本地的必要数据依赖。文件很多时正文按类别概括，同时提供完整清单及主要diff入口。

用户偏好代码库整洁、易维护：复用现有正式模块，临时脚本、fixture、工作副本和备份在核验后清理，不为了“可能有用”留下一次性入口。必要模型/数据/审计和仍被引用的证据继续保护；不能用删研究原件代替清理临时材料。review前后检查`git status`、`git diff --check`及临时目录。

普通`git diff`不包含未跟踪文件；提交前还须核对新增文件清单，并在暂存后执行`git diff --cached --check`。生成报告的格式修正也要核验内容不变，不把工作区diff通过当作全部待提交文件已经检查。

提交推送按用户当次指令执行。要求“先review”或“准备提交推送”时，完成整理、验证与清理后停在可review状态；已明确授权直接提交推送时，先展示具体变更再执行，不重复申请已有授权。该步骤不增加研究平台实体，也不要求新增正式模块。

## 年度共享缓存接入（2026-11-03）

年度缓存复用通过现有准备/训练/维护入口提供，命令见[研究脚本入口](../../backtest/scripts/README.md)。
后续新任务优先使用显式 feature/label manifest，记录路径、SHA、实际日历末日、真实数据内容指纹和样本协议；
把清单列入合同 inputs，物化后保存精确文件回执。日期版本文本不能替代内容校验。
纯特征、各H标签/基础样本协议、早停验证缓存分别拥有身份；旧 H40 筛后年度文件不能作为完整纯特征来源。

共享模式保留 H40 排名先于上市龄过滤、历史其它 H 在 H40/上市过滤后索引上排名的区别；
不强制共同H交集，也不把移除历史H40基础限制作为工程优化。样本协议改变仍需科学候选冻结。
同数据、特征和样本配方的五种子共享只读缓存；权重、训练日期和实际装配矩阵另留签名。

将 `shared-v1/` 及其清单列为有后续用途的研究数据，不能放入某轮临时材料删除列表。
任务自己的临时装配文件、适配器、fixture 等照常清理；历史已冻结合同和清理审计不改写。
共享缓存独立清理默认 dry-run、20 GiB 软上限，保护活动/未知引用、最新配方和父依赖；
所有研究 campaign 完整关闭后才允许实际删除。活动引用由缓存入口保护，Harness 状态不冒充进程锁。
异常退出引用须核对正式任务终态、实际进程及文件身份后人工释放，不根据 PID 消失或超时推断。
`--only-result` 仍只按平台原规则处理 result，不附带共享缓存或 MLflow 删除。

2026-11-03 共享缓存工程验收已完成：M0 2004—2020 年特征、H20/H40/H5 标签、五种子仅装配及验证帧复用通过；
新任务须显式传入 manifest，旧合同与适配器不自动切换。实测、对照覆盖范围和限制见[验收记录](2026-11-03-annual-cache-reuse-plan.md)。

## 验收与后续演进

第一版在隔离的临时测试目录验证，不用真实训练验证 harness：正常迭代、判负后调整、预算停止、失败/中断恢复、规则/配置/基线漂移、退役输入拒绝、并发/重复启动、未登记或报告未完成时拒绝完成、清理受阻时保留待办。

静态验证技能 frontmatter、文档链接、CLI 帮助和相关测试；用独立代理走读几种失败场景。不得为测试向真实 registry 写样例事件。最后执行 `git diff --check`、`git status` 并删除本任务临时材料。

隔离测试在任何写入前断言最终解析后的输出路径属于 fixture 根；检查 ROOT 默认参数、闭包和被导入模块捕获的真实路径，重绑定一个全局变量不足以证明隔离。测试后核对真实研究路径无副作用，误写仅按已有证据记录，不补造写前stat或SHA。

多个适配器交换JSON时，用同一份实际生产者结构联调消费端，并在首份真实产出上验证；单侧各造fixture不能证明字段契约一致。技术尝试的输出路径应事先按attempt隔离，恢复先查旧证据，不覆盖先前结果。

核验最终渲染的图表与HTML，检查标注、脚注、单位和来源。收益/波动百分数与无量纲Sharpe/IC分开，模型头部算术年化不写成账户CAGR；单点RankIC或局部年份改善不代替整体风险收益判断。

问题日志分别标明计划、已修复、已验证和待办，并引用真实证据；fixture通过、预检完成、实际模型/数值验证、完整研究与清理完成分别陈述。尚未物化或验证的项不能写 resolved，不因已有草稿或部分准备而声称试用完成。

2026-11-01 首版验证：37 项 harness 生命周期测试及相关登记、路径、报告、产物清理、退役输入、策略分组和脚本布局测试，合计 317 项通过；技能 frontmatter、CLI 帮助、独立恢复场景演练通过。另只读校验了当前 v2/s5 登记及现存官方评估文件的结构兼容性，没有训练、登记样例或重建真实报告。独立审查发现的收尾回执失效、清理审计漂移、失败策略出报告、临时清理受阻记录四项问题均已加入回归测试并修复。

2026-11-01 v2 支持验证：原37项生命周期用例保留，harness共92项通过；联合登记、研究路径、模型/策略总报告、产物清理和脚本布局回归共306项通过。新增覆盖组合证明/配方/真实session身份拒绝、普通v1/v2兼容、共享fit失败重试累计额度、串行及父attempt约束、漂移后终态记录。CLI帮助、Black与`git diff --check`通过；这些测试使用隔离fixture，不替代真实试验的预测数值及训练核验。真实试验的问题与结论另存实际候选档案，不据测试通过声明研究完成。

2026-11-01 首次真实试用：[多期限融合复盘](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)。20次新fit、5个历史模型复用、25份预测及两轮四候选完成正式登记/报告；四项净年化和夏普均未超过同源v2，锚定方案有波动/RankIC取舍，未晋升。477项相关回归、技能前置检查及独立只读续跑场景通过；实际没有制造技术失败或中断。科学规则保持原合同，通用经验和锁清单修正在全部研究停止后更新，旧合同/指标/record不改写。

2026-11-02 期限与风格试用：[研究结论](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)、[过程复盘](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)。用户确认只改H，七期限共同最终样本完成35次新fit、5个历史模型复用、40份原始预测、8条官方合成信号及8次固定s5连续账户；六科学候选与六派生执行事件分开登记。实际0技术重试，未晋升。模型与账户的T、F峰值不同，D峰均为H40；F仍仅有2024年136日，完整运行不等于解决跨年证据不足。

这轮补充的操作经验：把“随H单调改善”“端点最好”“最优位于区间”分别冻结和判读，收益与夏普也分开，保留全部并列最优，不能看完结果换一个有利含义。固定策略验证须在对应模型评估后执行；模型轨道合同不改成假装冻结v2的策略合同，模型行不加BT类型，另按标准登记真实model_ref的派生执行验证并更新BT总报告。信号SHA证明的是原始输入；验证真实过滤结果时保存正式prepare返回点的完整有序索引、分数与有限掩码签名，不能只凭相同行数或统计量断言相同执行信号。

跨工具集成测试至少有一条使用正式API实际产生的生产者结构。本轮手写ISO字符串fixture漏掉正式状态的Unix秒类型，修复后用真实状态/登记/报告接口联调并明确显示时区。CSV压缩或category读取只改变表示时，应核对规范化后的有序逻辑索引与数值，不能把类型元数据差异误报成研究输入漂移。资源探针核对真实协调器回执中的PID；无进程样本不当作零RSS健康证明。临时工具修复、fixture验证、真实数值验证和清理回执分别记录，不自动把已知科学局限写成已解决。

删除前先完成全部只读校验并在实验档案保存精确所有权/指纹清单；清理步骤按这份清单可中断续跑，不能因审计已存在或部分已删而无法继续。实际删除临时源后，仍须能依据永久清单和正式状态工具完成cleanup/close；不要把唯一收尾入口先删掉。该检查来自本轮清理初稿的独立审阅，在真实删除前已用中断恢复fixture验证修正。

本次搭建的临时目录 `/tmp/qlib-harness-build.3Kv1pO`、已确认归本任务所有的 pytest 工作副本及专用字节码已删除；必要结论与验证摘要保存在本协议，正式测试保留在测试目录。该临时路径只作已完成清理的来源记录，不是运行依赖。

新注意事项优先补在本协议并记录日期和缘由；新规则影响合同语义时提升合同版本，旧实验保持原协议。涉及评测口径或基线的变化仍回到实验标准的用户审批流程。

2026-11-05 H20 早停验证与零拟合续验：[综合报告](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)、[过程复盘](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)。原阶段实际新增6次fit（包括失败/重试），保留5条完整训练轨迹；续验没有新增fit，累计68次。统一H5退出边界后，patience20→50的模型Top3×H5净年化23.67%→31.31%、夏普0.343→0.525；固定s5账户CAGR8.53%→30.54%、夏普0.415→1.064、最大回撤−44.40%→−33.14%。同源历史v2账户CAGR仍为34.81%，不能据此宣布历史复现根因已解决。用户已授权确认有效后固定该方法重跑H；H研究结果不包含在本段验收中。

本轮补充的操作要求：

- 仓库外适配器必须在任何Qlib导入前设置仓库导入路径，并断言关键模块的实际`__file__`；设置`cwd`不能代替导入来源核对。遇到环境/仓库源码不同，分别核验相关实现和实际输出。本轮修正导入后重新物化全窗口，15份分量预测逐值一致；这仅排除了该差异对本轮已核验预测的影响，不证明所有输入上两套实现等价。
- 拟合后先保存模型、完整逐轮目标及必要诊断数组，再执行一致性断言。训练已完成但后置校验失败时，保留失败作业和真实fit成本；已保存模型可在核验后用于诊断，不因恢复产物把失败状态改成成功。未保存的首轮模型不能靠重试或稀疏日志补造。
- 树JSON/叶值逐位差异、实际路由差异、预测数值差异、头部名单与账户影响分别报告。缺失默认方向改变必须检查实际到达节点的缺失样本，不能一概视作非预测字段。原合同严格门槛失败仍保留失败；新的用户授权下可事先冻结有实际解释的验证条件，但不得改写原门槛或把找到浮点差异当作收益差距主因。
- 分年推理若省略无关学习链或限制日截面处理并发，须核验正式DK_I计算顺序、列序/索引/数值签名与真实模型预测。Qlib取数`kernels`不控制所有处理器的`n_jobs`；整窗及副本可能引起换页，不能只观察主进程RSS。
- 模型综合报告与派生回测报告必须各自拥有独立输出路径；正式BT报告器会写入登记中的详情路径。所有生成器执行后再核对配对控制、曲线、单位、关键数值及实际页面，链接存在和无JavaScript错误不能证明内容完整。本轮视觉检查发现并修复了报告覆盖，追加了纠正登记，指标未改写。
- 原墙钟预算到限时保留旧账本并停止开跑；新的明确继续授权可形成关联且有限的新合同，携带原累计成本、耗时及已暴露结果，不能重置原合同时间。本轮原阶段与零拟合续验分别关闭，关闭状态不表示原严格复现门槛转为通过。
- 清理每阶段保存执行回执，某阶段已删除后允许从下一阶段继续，不能因旧dry-run与当前目录不同而重复删除或覆盖原回执。任务专属pytest链接按链接本身登记并unlink，不跟随目标；不得把临时链接的目标当成任务所有物。必要数据/模型/预测与一次性工具分别核验。

2026-11-05 延长早停后的七H复验：[完整报告](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/reports/detail.html)、[过程复盘](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/harness-trial-review.json)、[清理回执](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/harness-cleanup.json)。H=5/7/10/15/20/30/40各五种子，35次fit全部成功、无fit重试，累计103次；统一patience50，各H保留自身标签掩码。统一Nov3标签后H20有106个排名值改变，因此重新拟合控制臂并先完成同轨迹p20/p50桥接：模型净年化19.47%→29.49%，固定s5账户CAGR4.71%→23.13%。这与上一轮原标签缓存的绝对成绩是不同批次，不能混写。

本轮三个严格猜想均未获支持；模型与账户收益最大值均为T/H20、D/H7、F/H15，F夏普最大值均为H30，T/D账户夏普最大值H40与模型端不同。保留一个模型实验入口和一个固定s5派生验证入口；未晋升。研究已关闭并清理一次性材料，完整浏览器页面视觉检查的限制见复盘，历史v2复现根因仍未解决。

本轮补充的操作要求：

- 控制H的配对早停验证有效，不代表所有H的早停问题消失。短轮、边界同分、整条轨迹反序敏感和轮数上限须预先列为诊断，逐项报告并限制归因；不能看完结果为不同H改耐心、删种子。选中轮无同分也不足以证明稳健：本轮H40/seed2000原选29轮，但早期同分替代峰使反序回放改选3轮。
- 已观测轨迹回放与真实重训分开。反序到实际训练末端还未触发停止时写“未观测停止”，不能外推未来轮数。达到200轮上限也不等同于自然早停；本轮H30/seed4000对此保留截断标记。
- 相同目标曲线和选轮不等于完整预测逐位相同。训练输入、逐轮目标、全量预测和账户影响分别留证；小规模输入变化与训练数值变化未被独立分离时，不宣布唯一性能原因。
- 保留精确共享输入须使用现有缓存的持久引用，区分研究依赖与活动进程租约，不依赖“最近使用”保护；市场、ST、风格标签来源同时保存内容指纹。静态过滤准备优化须保留原股票生存区间和谓词，并先核对已完成年份的索引、标签和上市掩码。
- 汇总元数据先通过现有归档校验再登记。`training_target_comparison`不允许owner自引用，控制臂也使用独立内部ref；内部来源不另进registry。本轮登记适配器失败发生在result已记账、registry未追加之间，恢复时核验已有证据并跳过重复result，不伪造新fit或科学attempt。
- 浏览器拒绝本地报告协议时不绕过限制，也不把HTML源码检查写成浏览器视觉检查。本轮三张静态图已目视核验，主要HTML表格数字、结构和链接通过；完整页面布局未视觉核验，单独披露。

2026-11-04 用户要求清理旧试用，并把后续完整 H 研究放在一个实验入口。旧融合/共同样本候选的冻结结果转为[历史审计](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)，不再作为活动候选；原始 62 次拟合成本不归零。后续先在计划中列全各 H、五种子、内部对照、固定 s5 验证及总 fit 预算；各臂配置和结果仍须独立可追溯，不以合并展示减少科学候选或执行成本记账。当前 H20 复现与早停诊断是重启 H 的前置，未通过前不物化 H 网格。

2026-11-03 H20差距排查：[结论](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)、[过程复盘](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)。仅新增5次H20拟合和3个固定s5账户诊断，没有重扫七H。恢复自身样本/原排名后模型净年化13.54%→28.07%（v2为30.22%），账户CAGR仅1.78%→8.09%（v2为34.81%）；实际持仓与v2平均重合51.01%。共同样本协议影响得到支持，但剩余历史复现差距未完全归因。原计划误记数据版本10-30，实际冻结日历为11-02，因此模型尝试按invalid登记，真实计算结果仅保留作诊断，不晋升。协议经验在全部研究停止后更新；原合同、原失败证据和历史指标保持不变，最终清理状态以该档案正式状态与清理审计为准。

2026-11-05 用户进一步收敛归档：新增平台记录只保留“训练目标期限与风格复验 · patience50”，其中保留 D/H7、F/H15、T/H20 的完整模型与派生回测。先前独立复现、早停、固定 s5 入口及旧试用审计目录已清理；必要历史结论、原登记事件、累计103次拟合和删除指纹汇入[精简保留审计](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/retention-20261105.json)。上文旧阶段链接现指向该审计，不表示旧档案或已关闭合同仍可续跑。全七H对比保留数值摘要；未晋升、未切换生产。


2026-11-05 账户回撤与真阶梯派生验证：[完整结论](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/reports/account-gap-validation.html)。H7/H15/H20的官方模型指标、实际成交价/费用、持仓现金与每日收益均核验；没有发现本轮账户计算错误。固定H7/D与H15/F信号的Top3×H5真阶梯均改善对应风格收益，2026的H7表现却恶化，不能把执行对齐当成普遍收益修复。新增2个账户、2次同配置观察性回放，0次拟合；累计103次拟合不重置。

- 先核对收益正负、时间归属及风格覆盖，再定位逐日持仓损失；年内累计收益、年化、峰谷回撤分开。2026全为T，不能用该年的损失直接代表D/F表现。
- 用真实持仓与原始成交价格/费用重建现金和每日损益，包括当日卖出的股票；模型端同时从冻结行情重算，不能仅凭两个汇总数字的差距猜测撮合错误。
- 真阶梯只对齐选股与持有规则，不保证账户收益恒等于标签均值年化。分别记录信号日与损益日归属、固定本金与动态净值、实际资金暴露、整手/限价/停牌、到期未卖余量和费用；固定本金/理想撮合只作解释，不替代真实账户或伪装新正式候选。
- 用户要求只留一个实验入口时，派生验证完整登记在父实验并保留内部模型身份、独立指标和真实session；不能为展示合并而谎报为全局v2信号。现有正式harness策略轨道仍锁定v2，本轮使用明确有限的仓库外适配器与派生作业回执，工具覆盖缺口尚未正式扩展。旧关闭状态不重开、不重置预算，也不将新账户结果伪装为旧合同成功证据。
- 清理除平台入口外还须检查无meta的旧预测bundle、嵌套非保留H数据、MLflow及pytest工作副本。先证实来源和引用，补充精简审计，再使用正式清理入口处理已确认归属目录；不能用压缩或搬入审计目录代替用户要求的删除。

2026-11-06 两轮 patience50 稳定性与真阶梯：[完整报告](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/reports/stability-20261106.html)、[过程经验](../../backtest/experiments/models/training-target/horizon-style-p50-20261105-v1/docs/stability-20261106-lessons.json)。旧、新标签各对 seed3000 独立复跑两次，原8线程下完整目标、树哈希和验证预测逐位一致；只换106条规范化标签，从第5轮产生不同分裂。恢复旧组五种子后，模型头部指标及s5账户表现复现，旧预测逐位复原仍无证明。固定Top3×H5真阶梯使两组CAGR差从7.41个百分点缩到2.30个百分点，但旧组全窗收益下降、两组2026收益均下降。新增8次fit和3个账户，累计111次fit；历史v2原训练原因仍是另一个未决问题。

- 先做同输入重复，再研究输入变化；重复使用原线程与参数。确定性参数只在事先约定的诊断分支启用，不能用修改后的配置宣称历史环境复现。单个问题种子的通过不外推到全部种子和硬件。
- 分别保存输入签名、每轮目标、树字段、实际叶子路由、头部名单和账户影响。微小浮点差异、文件SHA差异与实质选股变化不能互相替代；本轮保存模型逐年推理逐值一致，新重训模型与存档的微小位差则未改变已核验验证集头部名单。
- 已清理模型的恢复使用事先选定的重复次数和全部五种子，不能按收益挑复跑。派生比较前先冻结历史控制门槛；本轮旧组s5 CAGR复现误差为4.44e-16，之后才启动两个阶梯账户。记录历史只能比较的摘要与无法逐值验证的部分。
- 临时适配器启动前保存完整源文及实际运行环境、模块来源和关键二进制指纹，只有SHA不足以重建已删除工具。收尾保留必要日志、模型与精简诊断证据，再删除执行脚本和冗余数组；不能倒推补造以前缺失的源文。
- 同一档案关闭后补充阶段仍是正式harness的能力缺口。保留旧关闭状态，以明确有限的补充账本登记输入、进程、预算、失败和累计成本，不另建平台入口，也不宣称工具扩展已实现。
- 差距收窄须同时展示两侧绝对收益与分年风险，区分旧组下降和新组改善。模型238/H算术年化、账户算术年化及CAGR分别报告；规则、集中度与资金暴露同时变化时，不能把全部变化只归因于持有期。
