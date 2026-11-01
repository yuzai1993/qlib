# 研究脚本入口

脚本按使用阶段分为六类，共150个正式模块/入口（121个Python文件、29个shell文件，不含包标记）。同一功能只维护一份实现，跨目录使用明确的Python包导入。

| 目录 | 数量 | 放什么 | 常用入口 |
|---|---:|---|---|
| `prepare/` | 23 | 准备样本、特征、标签、配置及固定预测输入。 | `prepare_regime_train_chunks.py`、`build_regime_alt_labels.py` |
| `train/` | 20 | 训练模型、选择训练轮次，以及串联训练流程的批处理配方。 | `train_regime_arm.py`、`run_backtest.py` |
| `evaluate/` | 49 | 预测、回测、计算指标、比较参数和诊断持仓；宇宙过滤也由这里统一提供。 | `eval_ic_multi_pool.py`、`run_pred_backtest.py`、`run_regime_phase_s.py` |
| `report/` | 14 | 从登记和指标生成报告，以及报告共用的显示工具。 | `build_phase_m_v1_report.py`、`build_phase_m_v1_bt_report.py`、`build_strategy_group_reports.py` |
| `register/` | 33 | 维护实验登记、完成结果登记及经批准的版本晋升。 | `experiment_registry.py`、`register_regime_ablation.py` |
| `maintenance/` | 11 | 路径解析、配置加载、实验档案校验、协议检查与产物清理。 | `research_layout.py`、`experiment_archive.py`、`cleanup_experiment_artifacts.py` |

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

## 当前平台的result清理

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
