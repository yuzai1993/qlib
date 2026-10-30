# 实验归档

每个实验以 `exp_id` 为目录名，配置、详细报告、文档、指标摘要和登记详情放在一起。
索引保留历史事件顺序，同一实验的预登记、完成和更正可以分别存在，不去重。
实验口径与产物保留规则以 [EXPERIMENT_STANDARD.md](../EXPERIMENT_STANDARD.md) 为准。

```text
experiments/
  registry.jsonl                   # 全部历史事件的轻量索引
  reports/                         # 模型、策略的公共总报告
  <direction>/<experiment>/
    metadata.json                  # 资产清单、共享依赖、登记事件
    configs/                       # 实验独有配置，保持冻结文件原字节
    reports/                       # 详细 HTML
    docs/                          # 实验说明或计划
    metrics/                       # 持久化指标 JSON
    data/                          # 其他研究证据
    records/<SHA-256>.json          # 完整登记事件
  _shared/                         # 公共报告、文档与冻结预测证据
  _catalog/
    path_map.json                  # 旧路径到现路径的明确映射
    audits/                        # 长期迁移清单与核验结果
```

没有对应资产的实验不会创建空子目录。公共基线、实盘和多实验共享配置仍可位于
`backtest/configs/`；各实验 `metadata.json` 明确列出依赖，避免多份配置漂移。
模型原件、运行 session 和 MLflow 保持各自正式目录，不复制进实验档案。

## 登记与索引

统一使用 [experiment_registry.py](../scripts/experiment_registry.py)：

```python
from pathlib import Path
from backtest.scripts.experiment_registry import load_registry, append_registry

registry = Path("backtest/experiments/registry.jsonl")
rows = load_registry(registry)  # 完整逻辑事件，不含索引传输字段
append_registry(registry, completed_record)
```

新索引采用 `qlib-experiment-index-v2`，`detail_path` 相对于索引目录，
固定为 `<exp_id>/records/<detail_sha256>.json`。详情保存完整事件字段，
包括指标、假设、路径、逐种子结果和审计信息。SHA-256 对实际文件字节计算。
读取仍兼容旧内联行及 v1 的 `registry_records/<SHA>.json` 格式。

读取器校验版本、安全实验 ID、目录边界、符号链接、16 MiB 大小限制、SHA-256，
并核对索引元字段和详情是否一致。严格消费者遇到损坏必须报错，清理器不能依据不完整记录删除产物。
历史数值类型、负零和列表顺序均保留；存储兼容性不改变正式写入者对有限指标的要求。

详情不可原地修改。写入器先保存新详情，再原子替换索引。
`append_registry` 和 `write_registry` 共用稳定锁，防止并发追加丢记录；
`.registry.jsonl.lock` 是运行时锁，不入 Git，登记进程运行时不能删除。
调用方分开的“读取—修改—整表写入”仍须串行执行，避免旧快照覆盖新事件。

## 配置和报告路径

[research_layout.py](../scripts/research_layout.py) 仅按照 `path_map.json` 解析明确的旧路径别名，
不会猜测缺失文件或替换已清理的运行产物。配置加载器支持旧短名、新完整路径和唯一文件名；
冻结配置与协议保留原字节及哈希，在内存中解析活动引用。

总报告集中在 [reports/](reports/)。详细报告及指标从登记路径读取，生成器按输出位置计算相对链接。
新增实验应直接写入自己的目录，并维护 `metadata.json` 的资产清单；共享依赖只保留一个权威文件。

## 核验和任务收尾

索引迁移证据见 [_catalog/audits/registry_index_migration_20261031/validation.json](_catalog/audits/registry_index_migration_20261031/validation.json)，
本次目录整理证据见 [_catalog/audits/20261031-layout/](_catalog/audits/20261031-layout/)。
迁移时逐项核对历史事件、指标数值、文件哈希、平台与清理计划；不会通过训练或重跑回测改写结果。

一次性迁移、修复、核验脚本在验收后删除；保留正式读写、报告、清理功能、复用测试和核验 JSON。
每次任务均执行仓库 [AGENTS.md](../../AGENTS.md) 的收尾规则。
