# CSI1000 实盘信号人工补发设计

## 目标

取消 22:05 的自动补发任务。21:00 发布失败后，由 22:15 `evening`
完整性检查告警，用户确认故障原因后再人工触发恢复，避免在未知故障下盲目重试。

## 调度

- 21:00：保留下一交易日信号发布。
- 22:05：删除 `run_publish_catchup_cron.sh` 的 crontab 条目。
- 22:15：保留 `evening` 完整性检查。
- `run_publish_catchup_cron.sh` 文件继续保留，但只作为人工恢复工具，不再建议加入 cron。

## 告警与恢复

`PUBLISH_MISSING` 告警必须包含下一交易日、失败原因、发布日志路径和可复制的
恢复命令。恢复方式按状态区分：

1. 本地数据库没有批次记录：运行
   `bash live_trading/run_publish_catchup_cron.sh <config_id>`。
2. 数据库已有批次，但 `inbox` 缺 `.jsonl` 或 `.done`：运行
   `bash live_trading/run_publish_cron.sh <config_id> <trade_date>`，让发布器按持久化
   计划进行幂等重试；若共享目录只残留单个文件并触发内容冲突，则停止自动处理，
   人工检查共享文件和日志。
3. SMB `inbox` 不可访问：先恢复挂载，再执行对应的发布命令。

发布日志固定提示为
`live_trading/logs/<config_id>_publish_cron.log`。人工命令继续受现有模型 SHA、
Live/Backtest parity、账户环境、`LIVE_TRADING_CONFIRM`、发布锁和批次内容冲突门禁保护。

## 代码与文档范围

- 从 `live_trading/crontab.csi1000_postclose.example` 删除 22:05 条目。
- 更新 `live_trading/README.md` 和 `run_publish_catchup_cron.sh` 注释，明确仅人工使用。
- 让 `evening` 检查接收 config ID，并在三类 `PUBLISH_MISSING` 消息中加入日志和
  对应恢复命令。
- 增加监控单元测试，覆盖无批次、缺共享文件和 SMB 不可访问三种告警文案。

## 不变项

- 不删除人工补发脚本。
- 不改变 21:00 发布、QMT 消费、下单或账户逻辑。
- 不自动创建 `LIVE_OK`，不改变 Shadow、一手模拟盘或全额模拟盘门禁。
- 不安装或修改用户机器上的实际 crontab；部署时仍由用户按模板人工安装。

## 验收标准

- 调度模板不含 22:05 自动补发。
- 22:15 检查正常时不告警；三类失败均产生 `PUBLISH_MISSING` CRIT。
- 告警包含可执行的恢复命令和发布日志位置。
- 手工 catch-up 和正常发布脚本仍通过 Bash 语法检查及相关测试。
