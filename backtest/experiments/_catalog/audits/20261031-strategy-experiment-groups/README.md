# 2026-10-31 策略实验整理审计

本轮仅汇总策略实验，未训练、回测或重新选型，模型归档保持不变。

- 三对完全重复的候选归入 BT v3/v4/v5；各 Benchmark 版本、历史指标和冻结登记原件保留。
- 删除一个失败活动实验及其活动登记，保留必要失败审计；共享生产 parity 不受影响。
- 14个源变体迁入五组 `variants/`，三个策略单列。平台历史页共8个策略入口，模型仍6个。
- 原始源实体23个、登记事件24条、Benchmark 9个；当前模型仍v2，执行基线仍BT v5。
- 平台新增模型筛选，汇总组结论保存到本组 `metadata.json` 的 `analysis`。组无独立指标分数或晋升权威。

## 证据

- `dedup-consolidation.json`：三对重复来源、冻结原件及旧资源链接验证。
- `failure-removal.json`：失败删除范围及保留生产配对的检查。
- `grouping-audit.json`：五组参数轴、固定口径、对照、源文件哈希及证据局限。
- `archive-grouping.json`：14个目录迁移及86份原始文件的字节校验。
- `validation.json`：23源/9 Benchmark科学字段对照、26份模型JSON未改、67份指标/冻结来源校验、127个去重旧资源入口有效。
- `report-validation.json`：5份组汇总及总报告，115个本地链接有效；生成前后42份源HTML、132份数据/配置/登记文件字节未变。
- `completion.json`：最终测试、页面验收及临时文件清理记录。

目录示例：`strategies/topk-dropout/v2-top5-hold-f100/{metadata.json,README.md,reports/summary.html,variants/}`。

一次性工具原位置为 `/private/tmp/qlib-experiment-groups-20261031`，完成核验后整体删除。正式报告生成器保留为 `backtest/scripts/build_strategy_group_reports.py`。
