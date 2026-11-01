# 旧研究baseline副本删除凭证

2026-11-01，用户明确要求删除`backtest/models/`中的全部baseline，并评估其余代码。

- 删除`baselines/b1-m/`、`baselines/b6-m/`的两份模型、两份清单及目录中的Finder缓存，共3,520,704字节（约3.36 MiB，含缓存）。原模型合计3,512,402字节。
- `retirement.json`保留4份原件及缓存的路径、字节数、SHA，两份原始清单的完整内容，以及独立实盘模型的指纹；可按`source_commit`找回原件。
- `validation.json`记录删除前后文件和平台快照核验。24条登记、23个源实体、14个实验入口、9个Benchmark及指标均保持原样。
- `live_trading/models/`全部模型与清单、当前生产配置、`backtest/result/`和`mlruns/`模型原件、本目录6个模型源码均未修改。
- 旧CSI1000正式入口缺失清单时明确拒绝；诊断登记也不再悄悄写入空模型路径。不从实盘或训练记录自动找替代权重，不放松清理门禁。

审计仅供追溯，正式运行代码不读取这里的JSON。前次共享目录审计中“保留B6-M研究模型”的记录是当时事实，本次删除覆盖其当前保留状态；原历史凭证不改写。原清单中的旧路径保留作来源记录。

其余代码的逐文件评估在[目录guide](../../../../../docs/developer/repository-directory-guide.md)：`regime_adapt.py`、`rankic_early_stop.py`及包标记保留；`lgbm_ranker.py`、`loss_design.py`、`tra_device.py`是后续与旧配置、生成器一起清理的候选，本次未删除。

一次性工具在仓库外`/private/tmp/qlib-model-baseline-cleanup-20261101/`，验证后删除；仅保留本凭证和可复用测试。
