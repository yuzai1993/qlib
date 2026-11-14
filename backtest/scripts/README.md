# 研究脚本入口

脚本按使用阶段分为六类，共156个正式模块/入口（127个Python文件、29个shell文件，不含包标记）。同一功能只维护一份实现，跨目录使用明确的Python包导入。

| 目录 | 数量 | 放什么 | 常用入口 |
|---|---:|---|---|
| `prepare/` | 25 | 准备样本、特征、标签、配置及固定预测输入。 | `prepare_regime_train_chunks.py`、`build_regime_alt_labels.py` |
| `train/` | 21 | 训练模型、选择训练轮次，以及串联训练流程的批处理配方。 | `train_regime_arm.py`、`run_backtest.py` |
| `evaluate/` | 49 | 预测、回测、计算指标、比较参数和诊断持仓；宇宙过滤也由这里统一提供。 | `eval_ic_multi_pool.py`、`run_pred_backtest.py`、`run_regime_phase_s.py` |
| `report/` | 15 | 从登记和指标生成报告，以及报告共用的显示工具。 | `build_phase_m_v1_report.py`、`build_phase_m_v1_bt_report.py`、`build_strategy_group_reports.py` |
| `register/` | 33 | 维护实验登记、完成结果登记及经批准的版本晋升。 | `experiment_registry.py`、`register_regime_ablation.py` |
| `maintenance/` | 13 | 路径解析、配置加载、实验档案校验、协议检查、实验进度与产物清理。 | `research_layout.py`、`experiment_archive.py`、`experiment_harness.py`、`cleanup_experiment_artifacts.py` |

`train/run_backtest.py`是训练/工作流调度入口，`evaluate/run_pred_backtest.py`是免重训回测实现，二者有调用关系，不是两份重复回测器。

## 怎么调用

在仓库根目录可用文件路径或包入口；下列命令只显示帮助，不运行实验：

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/train/train_regime_arm.py --help
/opt/anaconda3/envs/qlib/bin/python -m backtest.scripts.report.build_phase_m_v1_report --help
```

直接使用脚本绝对路径时，也支持从其它工作目录启动。代码导入使用完整分类路径，例如：

```python
from backtest.scripts.register.experiment_registry import load_registry
from backtest.scripts.evaluate.universe_filter import build_keep_mask
```

不再把平铺的`scripts/`目录塞进`sys.path`后裸导入。没有保留旧文件路径的CLI包装文件；旧recipe中的来源路径由正式`_catalog/path_map.json`显式映射，原SHA仍表示当时Git版本的源码指纹。日常命令使用本页的新路径。

## AI 实验迭代与续跑

`maintenance/experiment_harness.py` 的 `init`、`check`、`status`、`event` 管理冻结计划、输入指纹、累计预算与进度；它不启动训练、不计算指标、不直接登记或清理。入口是项目技能 [qlib-experiment-harness](../../.agents/skills/qlib-experiment-harness/SKILL.md)，合同、CLI 及恢复流程见 [操作协议](../../docs/research/experiment_harness.md)。每次研究启动前先检查状态；续跑沿用原状态和累计预算。

## 年度共享缓存（2026-11-03）

新全A任务可显式使用 `regime-adapt-cache/shared-v1/`。先执行
`prepare/prepare_regime_train_chunks.py --arm m0 --shared`，保存输出的特征 manifest；
再执行 `prepare/build_regime_alt_labels.py --feature-manifest <特征manifest> --horizons 20`，
保存标签 manifest。训练时在原命令增加 `--feature-manifest <特征manifest> --label-manifest <标签manifest>`；
此模式同时启用有内容身份的早停缓存，训练摘要保存两份输入清单及实际矩阵签名。
这些参数只改变数据准备和复用方式，训练的 H、早停、种子等参数仍须显式按实验合同指定。

纯特征保留标签筛选前的全部行；标签仍使用显式 `legacy-h40-base-v1` 基础协议。
H40 先排名再过滤上市龄；其它 H 在原 H40 有效且满足上市龄的基础索引上各自排名，不取七 H 共同交集。
新样本协议属于另一个研究改动，不由缓存优化隐式引入。未传 manifest 的旧命令保持历史兼容行为，
旧年度 pickle 不自动导入共享层，也不被当作完整纯特征缓存。

共享输入以真实数据文件/代码的 SHA-256 校验，不凭文件名、mtime 或日期文本命中。
准备命令在独立 Python 进程中运行，期间保持数据源只读；标签命令也须先初始化同一 Qlib provider。
冷构建按年提交回执，完成后提交总清单；损坏、未登记块或来源漂移明确拒绝复用。
活动读写由 `.references/` 回执保护；异常退出遗留的回执需核对正式任务终态与进程身份后人工处理，不能按超时自动删除。

共享缓存属于后续研究可复用数据，不随单次实验的临时目录一并清理。
独立检查：`maintenance/cleanup_experiment_artifacts.py --only-shared-cache`，默认 20 GiB 软上限；
核对精确候选后增加 `--apply`。保护最新配方版本、活动引用及其父依赖，未知/未完整关闭任务阻止删除；
剩余保护数据超限会明确报告。该模式与 `--only-result` 互斥，不处理模型、预测、result 或 mlruns。
中断删除通过精确文件清单恢复，遇到新增未知文件或引用则拒绝继续。

详见[优化方案及验收记录](../../docs/research/2026-11-03-annual-cache-reuse-plan.md)。
已验收的 M0 17 年缓存准备（含 H20 标签、验证集及内容校验）实测约 141 分钟冷构建、71 秒热复用，
共享数据约 10.01 GiB。该结果不代表训练耗时或历史模型科学复现；新数据/源码依赖变化会失效并需重建。

## 当前平台的result清理命令

当前平台维护的模型、策略、历史Benchmark及其现存模型/融合预测依赖都受保护；不按“当前基线”单独筛掉其它仍在展示的实验。先查看计划：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/maintenance/cleanup_experiment_artifacts.py \
  --result-retention platform --only-result
# 核对后执行同一规则：
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/maintenance/cleanup_experiment_artifacts.py \
  --result-retention platform --only-result --apply
```

该模式不删除`mlruns/`。普通历史产物早已缺失会告警，不补造结果；必要的现存模型/预测缺失、SHA不符、越界或符号链接会阻止删除。原CSI基线保留模式保留历史契约，不能代替当前平台模式；已退役的CSI模型与预测不能自动恢复使用。

## 重复入口与历史工具

- `maintenance/plan_artifact_cleanup.py`是旧清理计划/命令导出接口，委托统一清理器；其默认基线模式只为旧调用兼容，当前平台请用上面的明确参数。
- `report/build_strategy_report.py`保留旧表格渲染兼容，CLI已委托`build_strategy_stability_report.py`，没有独立维护第二套登记或指标算法。
- 各种固定版本的`promote_*`、`register_*`及批处理shell是历史复现工具，仍有测试或共享逻辑；它们不是每日任务，不能因为文件存在就重放旧晋升。新增实验先遵循`backtest/EXPERIMENT_STANDARD.md`。
- 本轮删除6个已完成的一次性入口：旧模型归档补丁、RankIC补丁及其shell、两份固定策略收集器、固定日期vol20标签探针。来源指纹及理由见[删除凭证](../experiments/_catalog/audits/20261101-result-script-layout/script-retirements.json)。三个有复用价值的低价、停牌、个股追踪诊断继续保留。

以后的一次性迁移/修复/核验工具放在仓库外任务临时目录，验证完成后删除；正式可复用能力归入以上六类，并保留必要测试。不要为了保留一份临时入口而复制公共逻辑。

## 周度 H5 风格专家

十档 RankIC 早停、Top3 SPO+ 和 T 风格待分析问题见[两项保留实验](../experiments/reports/weekly_h5_style_experts.html)。

两项专家直接读取冻结的周标签和风格概率。重建周分类器的市场特征或回放固定股票篮子价差时，在仓库根目录运行以下命令恢复有效报价。

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/prepare/restore_weekly_h5_quotes.py
```

命令默认读取本地的 `backtest/result/20261103_h20_causal_inputs/evaluation-provider`。在其他机器上可用 `--provider` 指定同一份冻结行情。脚本合并归档中的 30 只股票报价，校验原始 SHA-256 后写入历史输入路径。

## 历史月度四分类工具

市场级独立协议 `monthly-k4-next-day-v1` 使用 D/F/T/P 事后月度标签，不套用选股收益指标。
正式入口：`prepare/prepare_market_style.py` 准备，`train/train_market_style.py` 单次拟合，
`report/build_market_style_report.py` 核验四候选五种子内层/最终共40份回执、评估并生成报告。
每次真实准备或拟合前用 `maintenance/experiment_harness.py style` 检查及登记预算，详见
[分类 Harness 合同](../../docs/research/experiment_harness.md#市场风格四分类专用伴随轨道)。

训练2004-01-02～2020-07-31，评估2020-08-03～2026-07-31；目标跨窗的末日剔除。
外部源先探测可用性，历史发布时间未验证的字段排除；当前接口日程不能证明全历史可得性。
涨跌停相关列明确为阈值代理，指数发布前的回算数据屏蔽。标签验收不通过时不启动分类拟合。
本协议独立于当前 v5 选股基线和 s5 执行策略，不赋予任何晋升或生产权限。
