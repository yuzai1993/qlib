# CSI1000 盘后调度编排设计

## 目标

把 CSI1000 模拟交易的工作日调度收敛为三个明确时点：

- 20:00：导入 QMT 回执、执行 postmarket 检查、更新当日行情；行情更新成功后生成日报；
- 21:30：发布下一交易日信号；
- 22:30：执行 evening 发布完整性检查。

取消独立的 16:30 行情更新和任何自动补发任务。发布失败后由 22:30 告警引导人工恢复。

## 20:00 串行流水线

新增 `live_trading/run_postclose_cron.sh`，按以下顺序运行：

1. `run_import_cron.sh`；
2. `run_monitor_cron.sh postmarket`；
3. `scripts/data_collector/tushare/run_update_to_bin.sh`；
4. 仅当行情更新成功时运行 `run_monitor_cron.sh report`。

导入或 postmarket 检查失败不应阻断行情更新，避免一个回执问题同时造成下一交易日信号缺数。各子任务保留自己的日志和通知；总包装器另写一份摘要日志，并在任一已执行阶段失败时非零退出。行情更新失败时不运行日报，因为此时快照价格不可信，更新脚本本身负责发送失败通知。

整个流水线持有配置级 `postclose` 目录锁。锁存在时，21:30 发布和人工 catch-up 都必须失败关闭，避免读取正在改写的 Qlib provider。异常退出由 trap 释放锁；若机器强制重启留下残锁，运维人员必须先确认没有任务运行再人工处理。

## 21:30 发布与 22:30 检查

21:30 继续使用 `run_publish_cron.sh csi1000_b6m_b2s_postclose`。发布器原有的模型 SHA、Live/Backtest parity、严格信号日期、SIMULATION 环境、幂等批次和共享目录检查保持不变。

22:30 运行 `run_monitor_cron.sh evening csi1000_b6m_b2s_postclose`。不安排自动 catch-up；告警包含日志路径和状态对应的人工恢复命令。`run_publish_catchup_cron.sh` 仅保留为人工工具，并同样受 `postclose` 锁约束。

## crontab 迁移

实际 crontab 最终只保留下列新系统条目：

```cron
0 20 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_postclose_cron.sh csi1000_b6m_b2s_postclose
30 21 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_publish_cron.sh csi1000_b6m_b2s_postclose
30 22 * * 1-5 /Users/yuxianqi/Project/qlib/live_trading/run_monitor_cron.sh evening csi1000_b6m_b2s_postclose
```

安装时删除原有 16:30 `run_update_to_bin.sh`，避免同一天重复拉取。模板不得包含 22:05 自动补发。

## 账户初始化门禁

调度代码和模板可以先完成，但实际安装与首次账本初始化必须等待空仓账户指标自洽：持仓市值、冻结资金和未完成委托均为 0，且 QMT `m_dBalance`（总资产）与 `m_dAvailable`（可用金额）一致到可解释的结算差额。正式 opening cash 只取核对后的空仓现金，不根据较大或较小值猜测。

环境保持 `LIVE_RUN_MODE=SIMULATE`，`LIVE_TRADING_CONFIRM` 未设置，且不创建 `LIVE_OK`。

## 测试与验收

- 包装器在导入或 postmarket 非零时仍运行行情更新；
- 行情更新成功才运行 report，失败时明确记录跳过；
- 任一阶段非零使总包装器非零退出；
- postclose 锁阻止正常发布和人工 catch-up；
- crontab 模板只有 20:00、21:30、22:30 三个工作日条目且无自动补发；
- Bash 语法检查、相关 wrapper 测试和完整 `tests/live_trading` 套件通过。

本次只调整生产工程调度，不涉及训练、因子、标签、策略参数或回测选型，因此不新增实验 registry 记录。
