# 量化研究平台

统一的本地服务，包含实盘监控、离线实验和分析档案，监听 `127.0.0.1:8090`。实盘查询直接使用 SQLite 只读连接，不再依赖旧 8082 Web 服务；交易执行、调度和每日快照/日报任务仍独立运行。

## 启动

在原 qlib 工作区手动运行（常驻服务已启动时无需重复运行）：

```bash
cd /Users/yuxianqi/Project/qlib
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m quant_platform \
  --live-config alla_v2_top5d1f100_postclose_real_active
```

打开 <http://127.0.0.1:8090>。默认值与仓库常驻服务模板配置：

- 项目根和研究源：`/Users/yuxianqi/Project/qlib`
- 实盘配置：`live_trading/configs/alla_v2_top5d1f100_postclose_real_active.yaml`（命令行默认值与当前生产一致，仍建议显式指定；只读加载）
- 分析存储：`/Users/yuxianqi/Project/qlib/backtest/diagnostics/`，每个子目录是一份分析
- 监听：`127.0.0.1:8090`

源目录、归档目录和端口可显式指定，例如：

```bash
/opt/anaconda3/envs/qlib/bin/python -m quant_platform \
  --research-root /Users/yuxianqi/Project/qlib \
  --data-root /Users/yuxianqi/Project/qlib/backtest/diagnostics \
  --project-root /Users/yuxianqi/Project/qlib \
  --live-config alla_v2_top5d1f100_postclose_real_active \
  --port 8090
```

省略 `--research-root` 时使用 `--project-root`；省略 `--data-root` 时使用研究源中的 `backtest/diagnostics`。显式指定的 `--data-root` 表示一套完整的分析目录集合，适合仓库外的测试或独立档案，不再混入另一处历史目录。活动配置从 `qlib` 读取研究资料；修改仓库模板不会自动切换正在运行的服务，实际研究根和分析目录可由 `/api/health` 的 `research_root`、`analysis_storage` 核验。

2026-10-30 已把必要基线训练原件、正式回测记录和持久化评估 JSON 迁入 `qlib`，更新已安装的 LaunchAgent 并重载平台；运行接口已确认研究根为 `/Users/yuxianqi/Project/qlib`，随后移除了旧 `qlib_exp` 工作区。旧源31条可用记录与2,623个既有指标值均保留，当时主根有36条记录、35条指标源可用，1条历史缺失保持标注。迁移清单及切换验收见 `backtest/experiments/_catalog/audits/repository_migration_20261030/`；实盘交易调度器未改动。

端口被占用时启动会退出，不会关闭已有服务。平台不读取交易环境密钥、不初始化或迁移生产 SQLite。`mode=ro` 与 `query_only=ON` 禁止业务数据和 schema 写入；正常读取正在更新的 WAL，不使用可能漏数据的 immutable 模式。

## 常驻服务

复用原 Web 监控的 LaunchAgent `com.yuxianqi.qlib-live-monitor`，登录后启动、异常退出后重启。仓库模板位于 `live_trading/launchd/com.yuxianqi.qlib-live-monitor.plist`，显式设置项目根和研究根为 `/Users/yuxianqi/Project/qlib`，安装路径为 `~/Library/LaunchAgents/`。原 8082 不再单独运行。

```bash
launchctl print gui/$(id -u)/com.yuxianqi.qlib-live-monitor
curl --noproxy '*' --fail http://127.0.0.1:8090/api/health
# 仅重启统一 Web 平台，不操作交易 scheduler
launchctl kickstart -k gui/$(id -u)/com.yuxianqi.qlib-live-monitor
```

日志沿用 `live_trading/logs/alla_v4_ladder_k1h5_postclose_real_web_service.{stdout,stderr}.log`。代码位于 `/Users/yuxianqi/Project/qlib`；分析档案统一放在研究根的 `backtest/diagnostics/`。

切换前的 plist 已保存在 `~/.qlib/research_platform/service_backups/20261028-single-service/com.yuxianqi.qlib-live-monitor.plist`。如需回退，先 bootout 上述 Web label，等待旧服务卸载完成，恢复备份 plist 再 bootstrap，可恢复原 8082；旧 `run_web_service.sh` / `scripts/run_web.py` 保留为回退入口。不要操作 `com.yuxianqi.qlib-live-scheduler`，也不要将 `run_monitor_cron.sh` 误当 Web 服务启动脚本。

## 使用

全A策略版本展示使用`s1–s5`，模型继续使用`v1–v4`。策略Benchmark核心与分年/分风格表同时标记模型版本、模型简称和策略简称；详情及CSV同步。原始登记的版本与实验ID保留用于来源追溯，展示编号不影响指标或当前选择。

**实盘监控**保留概览、批次与成交、预测信号。浏览器刷新获取新数据；图表库不可用时使用本地曲线降级。账本缺失或 schema 查询异常时返回 503，研究与分析仍可使用。启动时账本缺失的实例需在数据源恢复后重启；平台不会尝试建表或修复实盘账本。

左侧的**模型实验、策略实验**各含「Benchmark」「实验记录」。每次刷新扫描 `backtest/experiments/models/` 与 `strategies/` 的实验元数据，新目录自动发现，无需更新白名单。完整档案协议见[实验归档说明](../backtest/experiments/README.md)。正式状态、晋升与历史仍从 `registry.jsonl` 校验，数值从声明的 JSON 读取，原 HTML 作为证据链接。旧 `#research` 链接仍可打开模型实验。

新增符合协议的文件夹和 `metadata.json` 即可在对应轨道的“实验记录”中展示；未登记档案显示草稿，不获得官方基线或比较资格。实验数值与登记保持只读，支持表格编辑人工结论，不提供网页新建实验或修改指标。2026-10-31 按用户要求归并重复策略、删除失败档案并汇总参数变体后，保留23个源实体（全A模型6个、`phase-m-v1-bt`策略17个）、24条登记事件。策略中14个变体按5组展示，另有3条独立策略；实验记录共14个入口（6模型、8策略）。Benchmark单独保留4个模型和5个策略版本。审计见[策略参数归组](../backtest/experiments/_catalog/audits/20261031-strategy-experiment-groups/)。

- **Benchmark** 在当前股票范围内展示已晋升 baseline，按版本从新到旧排列；首列勾选控制下方分维度表，表头可全选或清空。模型、策略及股票范围分别维护当前会话的选择。核心表下方可切换分风格、分年；每个指标一张表，行是版本，列是年度或风格。Benchmark版本详情继续使用同一套指标视图。CSV 导出当前已选版本的分维度指标。
- **实验记录**按日期倒序展示日期、内容、结论、报告链接和晋升版本；同日稳定排列，缺失或无效日期置后，导出沿用相同顺序。模型页另有实验类型列及类型筛选；策略页可按模型筛选，与搜索、状态筛选取交集，CSV导出当前筛选结果。策略类型均为策略回测，不展示类型列或类型筛选。晋升仅来自源实验明确的版本登记或 `promoted_to` / `promoted_from` 关系，不从组的指标表现推测。可查看详情、导出与编辑结论；不提供手动实验对比入口。同ID正常修订采用最新版，重复元数据、损坏事件仍报错。

模型按训练目标、早停方法、模型结构、评估方法分类，名称优先读 `metadata.json.title`，类型读 `experiment_type`；分类后的实际目录与原ID在详情保留。日常人工结论填在本实验元数据 `analysis.conclusion`，并可填写证据、限制和复核信息，刷新即可显示。平台将它标为“人工分析”，原登记结论独立保留；人工文字不授予比较、基线或晋升资格。保留源实验及策略参数组已依据现有报告和指标预填精简人工结论，状态为草稿。示例见[登记实验结论](../backtest/experiments/README.md#如何登记实验结论)。

结论列点击“编辑”后在原表格填写，支持“保存／取消”；保存需1–500字，使用 `PUT /api/research/conclusion`。文件版本冲突会提示刷新并保留输入，防止覆盖新内容。只有通过目录校验的元数据可编辑，更新限于人工结论、日期和草稿状态；参数组保存到组的 `metadata.json.analysis`，不会改写成员结论。原证据、局限、登记、数值和比较权限保留。

模型“实验内容”已精简为短标题、改动摘要与当时 Baseline 版本，使用元数据的 `change_summary`、`comparison_baseline` 维护。列表不再铺开完整假设、评估范围和目录；这些信息仍在详情。摘要、Baseline 同步用于 CSV，搜索也覆盖这两项。v1 是初始基线，v2/v3/v4 当时分别对照 v1/v2/v3；原登记中的 `self` 或后来改写的引用保持不变，不据此猜测当时版本。

模型 Benchmark 的版本名称可点击打开标准介绍，按版本、归档目录ID、结构、使用特征、种子、训练样本/目标、评估样本、训练早停、预测信号、变更与模型地址展示；训练和评估样本均分别说明股票池、时间与过滤逻辑。介绍由 `model_profile` 维护，模型地址显示qlib目录相对路径及可用状态。评估指标沿用原有核心/分年/分风格视图；原始报告与产物逐项说明用途后提供预览和下载，模型二进制仅显示地址。内部登记ID、原假设、人工分析及原登记结论保留在折叠区；Benchmark策略详情保持原布局。

策略“实验记录”将原“实验内容”拆成“模型”和“策略”：模型只显示关联模型的版本、简称，策略显示简称与简短规则。各策略元数据维护 `model_ref` 和 `strategy_summary`，模型名称/版本从模型档案关联获取；BT版本不作为模型版本。CSV同步拆列，搜索覆盖模型和策略文本；模型实验记录及Benchmark保持原布局。长实验假设和原ID仍在详情。

策略列表同时标出对比Baseline及关联的晋升策略版本。参数组注明“组内对照”，晋升标记来自组内成员的已验证Benchmark，例如Top5组关联s2、s3、s5（当前），其中s2为组内对照；这不表示汇总组自行晋升。未晋升的实验明确标注，30万诊断不会因为与s5比较就被标成晋升s5。详情另列成员原登记的历史Baseline，CSV同步保留组内对照和历史对照，避免混淆。

模型、策略的实验记录详情独立精简为“Baseline、变更内容、核心指标结果”，并保留简短结论；完整假设、原登记和附件折叠保存。Baseline及差分说明由 `comparison_baseline`、`change_summary` 维护；有明确版本时策略展示 `sN`，参数组可显示对照组名称，不假造版本。核心结果表列出Baseline和各实验组的原始指标，不做新的合成计算。记录详情没有分年/分风格切换或加入对比，Benchmark的完整介绍、版本选择及分维度视图保留。

策略归档按实际执行规则放在 `strategies/topk-dropout/` 和 `strategies/cohort-ladder/`。同类参数放到 `<规则族>/<组名>/variants/<变体>/`，组元数据使用 `entity_type: experiment_group`，只负责汇总展示；各源变体保留稳定ID、登记、完整报告和原始指标。API的 `records` 保存23个源实体，`experiment_records` 保存14个展示入口，`benchmarks` 独立保存9个版本。旧版API无 `experiment_records` 时兼容使用 `records`；显式空数组不恢复隐藏变体。

页面把原“研究轨道”称为“股票范围”，表示实验从哪些股票中选取候选标的，例如全A。选项由当前类型的实际档案生成，当前两页均仅有全A；数据刷新移除当前范围时会自动切换并清理旧筛选。策略页的模型选项按完整 `model_summary.ref` 匹配，仅出现在实验记录；切换股票范围或模型/策略页会清空旧模型筛选，刷新移除当前模型时也会清空。策略页不提供只有一个方向的筛选；模型页保留类型筛选。内部轨道字段与比较协议保持不变：模型主格 top3×h5（算术净年化/HAC 波动/夏普），策略使用 CAGR，不同轨道、池、窗口、费用与过滤口径分开呈现。单种子、扩展窗口、未登记与历史摘要单独标识。

全站下拉框使用一致的内缩箭头与文字留白，保留原生选择、键盘操作和聚焦提示；系统强制配色模式下恢复原生箭头。

历史源元数据不完整时保留来源说明；s3 的种子声明来自 registry。指标不从清理过的结果目录臆造补齐，缺失值显示为 `—`。研究源可持续更新，刷新页面即可重新读取；本平台不会运行报告生成器、训练、回测、pickle 反序列化或清理任务，也不修改实验 registry。

登记 `portable_eval_output` 的实验优先读取这份持久化 JSON，指标资源链接与来源显示同步指向它；原 `eval_output` 作为来源证据保留。明确登记的持久化源缺失、无效或越界时如实报告，不自动改读旧结果掩盖问题。这使正式摘要能够独立于可清理的原始运行目录保留。

**分析档案**直接扫描 `backtest/diagnostics` 的各个子目录，元数据保存在各目录的 `metadata.json`，正文、报告与代码分别保存。新建一个符合[目录协议](../backtest/diagnostics/README.md)的分析目录，刷新页面即可加载，无需重启平台。元数据损坏、路径越界或内容校验失败会明确报错，不根据报告标题猜测替代记录。

页面支持新建、编辑、归档与恢复、搜索及日期筛选。每篇可编辑分析包括背景、方法、结论、代码使用说明和至少一份代码；单份代码限 256 KiB，最多 20 份，完整记录限 2 MiB。代码仅按文本保存和下载。编辑使用不可变版本目录，最后原子更新元数据指针；跨进程锁与版本号防止旧编辑覆盖新版本，冲突时页面保留未提交内容。

现有三份历史报告保留原始字节、稳定 ID、只读标识与缺失附件说明，现存的两份永久代码已归档到对应分析目录。导出 ZIP 包含 `REPORT.md`、`metadata.json`、`code/`，可编辑记录另含 `document.json`；解压到分析根的一个子目录后可直接重新加载。没有元数据的旧 `REPORT.md` 仍可兼容读取为只读历史资料，新分析应提供独立元数据。

原始 HTML 在沙箱中展示，不执行脚本；依赖脚本的旧报告图形可能不显示，可下载原文件在本机自行查看。平台仅向注册资源发放读取入口，不提供任意本地路径读取或代码执行接口。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/quant_platform -q -p no:cacheprovider
node --test tests/quant_platform/*.cjs
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_monitor_web_api.py \
  tests/live_trading/test_monitor_web_frontend.py \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_operational_wrappers.py -q -p no:cacheprovider
```

Python 测试使用临时档案和临时 SQLite 账本，不启动或写入生产交易系统。分析档案写入仅限 `backtest/diagnostics` 专属子树；实验结论编辑仅写经校验的实验 `metadata.json.analysis`，不允许任意文件路径。交易共享目录及其父目录始终禁止作为分析根。实盘只读验证另见 `tests/live_trading/test_monitor_read_only.py`。
