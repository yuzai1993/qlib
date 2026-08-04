# 一手模拟盘晋级与监控基准修正设计

日期：2026-08-05

## 目标

1. 将 `csi1000_b6m_b2s_postclose` 从 Shadow 发布晋级为仅 2026-08-05 有效的一手模拟盘执行。
2. 将实盘监控曲线图例从硬编码的“沪深300”修正为“中证1000”。

## 安全边界

- QMT 策略继续绑定模拟账户，协议头继续要求 `account_environment=SIMULATION`。
- Mac 发布环境切换为 `LIVE_RUN_MODE=LIVE`，并设置 `LIVE_TRADING_CONFIRM=YES`。
- 只创建 `state/LIVE_OK_2026-08-05`；不创建未来日期授权，不增加自动续权任务。
- QMT 端保留 `MAX_ORDER_QUANTITY=100`，每个可执行订单最多一手。
- `allow_real_money=false` 保持不变；本次不设计或开放真实账户路径。
- 删除当日 `LIVE_OK` 只阻止尚未开始的新批次，不能撤回已经提交的订单。

## 执行流程

2026-08-05 的 `SIMULATE` 批次已经被 QMT 认领到 `processing`，但状态显示 `trading_started=false`、`execution_live=false`、无 submitted、无 fills。它不能原地改成 LIVE，也不能在 QMT 仍运行时移动其状态文件。

采用以下受控重置流程：

1. 用户先在 QMT UI 停止桥接策略，确保内存中的活动批次不再运行。
2. Mac 侧恢复工具重新读取 active state，并仅在未开始交易、未授权 LIVE、无提交、无成交时继续；任一条件不满足立即拒绝。
3. 工具先复制并校验旧批次的 processing JSONL、done marker 和 active state 到带时间戳的 `archive/operator_retired_*` 目录，落盘包含原路径、目标路径和 SHA256 的 retirement manifest；全部校验通过后才移除原路径。中途失败时保留原文件并拒绝发布新批次。
4. 旧 Shadow 批次在账本中保留完整审计记录，并标记为由新的 LIVE 批次替代；跨模式替代只允许 `SIMULATE -> LIVE`、同交易日、同策略且旧批次无终态回执的这一条晋级路径。
5. 将发布环境切换为 `LIVE_RUN_MODE=LIVE` 和 `LIVE_TRADING_CONFIRM=YES`，以 `seq=2` 发布 2026-08-05 新批次，避免复用不可变的旧 batch id。
6. 只创建 `state/LIVE_OK_2026-08-05`，核对新批次为 `LIVE + SIMULATION` 后，用户在 QMT UI 重启桥接策略。

QMT 只有在新批次为 LIVE、账户环境为 SIMULATION、账户 ID 匹配且当日 `LIVE_OK` 存在时才提交模拟委托；任一条件不满足均关闭执行。

执行前检查 SMB 可写、QMT 模拟策略运行、账户绑定一致和一手上限仍为 100。执行后通过 fills、账户快照、批次归档和监控事件核对委托数量、价格类型及终态。

## 监控基准

底层数据配置 `data.benchmark` 与 `monitor.benchmark` 已是 `SH000852`，无需迁移数据库或重算历史快照。本次新增展示配置 `monitor.benchmark_name=中证1000`，由监控 API 返回并供前端净值图的图例与 series 使用，避免展示名称再次与底层配置脱节。

## 测试

- 恢复工具：覆盖 dry-run、活动批次已开始/已有提交/已有成交时拒绝、归档校验失败时保留原文件，以及成功归档时 manifest 校验通过。
- 账本：覆盖只允许未执行的同日同策略 `SIMULATE -> LIVE` 晋级，拒绝反向、跨日、跨策略或已有终态回执的替代。
- 发布：覆盖 seq=2 的 LIVE 批次保留 `account_environment=SIMULATION` 和双开关检查。
- 监控：静态前端测试断言不再出现“沪深300”，并显示“中证1000”；现有 API 与配置测试继续通过。
- 上线前：运行恢复工具 dry-run，人工停止 QMT 后运行实际恢复，再逐项核对归档、账本、共享目录和安全门。

## 失败处理

- QMT 尚未停止或旧 active state 不满足未执行条件：不移动任何文件，不发布 LIVE 批次。
- SMB 不可写：不发布 LIVE 批次，不创建绕过文件。
- 双开关不完整：QMT 按 simulated 处理，不补执行批次的后半段。
- 账户环境或 ID 不一致：拒绝执行并保留告警。
- 发现单笔数量超过 100 股：停止当日晋级，删除尚未使用的 `LIVE_OK` 并检查 QMT 部署版本。
- 当日完成后不为下一交易日自动创建 `LIVE_OK`；下一日恢复为无逐日授权状态。

## 验收

- 环境为 `LIVE_RUN_MODE=LIVE` 且 `LIVE_TRADING_CONFIRM=YES`。
- 共享目录仅存在 `LIVE_OK_2026-08-05`，不存在未来日期授权。
- 旧 Shadow 文件完整保存在 operator-retired 归档，manifest 的 SHA256 与归档文件一致。
- 最终待消费或已消费的 seq=2 批次为 `LIVE + SIMULATION`，订单数与账本一致，旧批次审计记录指向替代批次。
- QMT 源码与已安装版本确认 `MAX_ORDER_QUANTITY=100`。
- 监控 API 仍使用 1,000 只 CSI1000 信号，页面净值图显示“中证1000”。
- 相关自动化测试通过，监控服务重启后页面与 API 正常。
