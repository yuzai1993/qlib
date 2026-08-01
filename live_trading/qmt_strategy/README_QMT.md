# QMT 盘后定价桥接部署（仅模拟账户）

`qmt_signal_bridge.py` 是 QMT 内置 Python 3.6 策略，消费 protocol-v2 批次并使用盘后定价 `prType=49`。源码保持 ASCII，文件头 `#coding:gbk` 不得删除。

本说明不会授权真实资金。策略、批次和 Mac 配置都要求 `account_environment=SIMULATION`。

## 1. Windows 目录

默认根目录是 `D:\qmt_bridge`：

```text
D:\qmt_bridge\
  inbox\
  processing\
  outbound\
  archive\
  state\
  logs\
```

首次运行会创建子目录。通过 SMB 把根目录共享给 Mac；Mac 上的配置应指向 `/Volumes/qmt_bridge`。共享账户只需该目录的读写权限，不要给管理员权限。Windows 应使用固定局域网 IP，并在交易时段禁用睡眠和自动重启。

## 2. 导入策略

1. 在大 QMT 的模型交易/策略编辑器中新建内置 Python 策略；
2. 导入 `qmt_signal_bridge.py`；
3. 设置 `BRIDGE_ROOT`；
4. 把 `ACCOUNT_ID` 填为新模拟账户 ID。非空时，header 中的账户必须完全一致；
5. 保持 `ACCOUNT_TYPE = "STOCK"`；
6. 保持 `MAX_ORDER_QUANTITY = 100`，这是首次一手模拟盘的硬上限；
7. 编译后，在模型交易界面明确绑定同一个模拟账户。

不要把真实账户 ID 写入这个策略。账户环境无法从一个普通环境变量切换。

## 3. 定时与状态机

策略在 `init` 中注册 `ContextInfo.schedule_run`，从 15:04:55 起每 3 秒唤醒。旧 QMT 没有新版接口时退回 `run_time`。`handlebar` 只作兼容唤醒，因此 15:00 后没有行情 tick 也能继续执行。

执行顺序：

1. 认领当日 `signal_*.jsonl` + `.done`，检查 schema 2.0、checksum、日期、账户和 SIMULATION 环境；
2. 15:05 提交 SELL；
3. 等卖单终态，最多等 240 秒；
4. 查询实际可用现金，用 QMT `lastPrice` 作为官方收盘价，将 BUY `target_value` 换算为整手并预留佣金/过户费；
5. `passorder(..., orderType=1101, prType=49, price=0, quantity, ..., quickTrade=2, client_order_id, ContextInfo)`；
6. 15:28 撤销未完成订单；
7. 15:30 写最终 fills marker；
8. 15:31 重写账户与持仓快照。

如果 `lastPrice` 缺失或非正数，BUY 失败关闭，不使用盘口价、滑点、昨收或信号价格回退。

## 4. Shadow 验收

Mac 侧保持：

```bash
export QMT_SIM_ACCOUNT_ID='新模拟账户ID'
export LIVE_RUN_MODE='SIMULATE'
```

不要创建 `LIVE_OK`。发布：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_publish_signals.py \
  --config csi1000_b6m_b2s_postclose \
  --trade-date YYYY-MM-DD --mode SIMULATE
```

预期：

- inbox 文件被移动到 processing，再归档；
- 每个计划订单得到 `SKIPPED simulated`；
- BUY 回执的 `requested_qty` 是按收盘价推导的正整手；
- 无订单日也生成空 `fills_*.jsonl` 和 `.done`；
- SIMULATE 不查询券商账户、不生成账户快照；
- 重放相同 batch 会标记 duplicate，不会下单。

## 5. 一手模拟盘双开关

Shadow 通过后，必须同时满足：

1. Mac 发布 `mode=LIVE`，且进程环境有 `LIVE_TRADING_CONFIRM=YES`；
2. Windows 当天存在 `D:\qmt_bridge\state\LIVE_OK_YYYY-MM-DD`。

此外保持 `MAX_ORDER_QUANTITY = 100`。这会把每个可执行买卖订单限制为一手。每天开盘前确认 QMT UI 绑定的是模拟账户，收盘后逐单核对价格类型、官方收盘价、数量、委托状态和回执。

删除 `LIVE_OK` 只禁止后续新提交。已经提交的 LIVE 订单仍会继续查询、撤单和终结。

## 6. 全额模拟盘

一手阶段连续验收后，人工把已安装策略的 `MAX_ORDER_QUANTITY` 改为 `0`，重新编译并复核账户。该操作只解除数量上限，不改变 SIMULATION 门禁、双开关和每日人工 `LIVE_OK`。

系统没有真实账户晋级路径。

## 7. 恢复与排障

| 现象 | 检查 |
|---|---|
| 批次不消费 | QMT 策略是否运行；inbox 是否同时有 jsonl/done；trade_date 是否为今天 |
| 15:00 后停止 | QMT 日志是否显示 timer 注册；版本是否退回 `run_time` |
| LIVE 全部 simulated | 当日 `LIVE_OK` 是否存在；header mode 是否 LIVE |
| BUY `official close unavailable` | `get_full_tick` 的 `lastPrice` 是否已更新为正数 |
| 账户查询失败 | QMT UI 绑定账号、`ACCOUNT_ID`、header account ID 是否一致 |
| 回执缺失 | `processing/` 与 `state/active_<batch>.json` 是否完整；查看 FormulaOutput 日志 |
| 重启后未恢复 | processing 的信号对是否完整；active state JSON 是否可读 |
| Mac 报持仓漂移 | 对照 QMT 委托/成交、`account_*.jsonl` 和本地 fills，先停下一日 LIVE |

日志通常位于 `{QMT安装目录}\userdata\log\XtClient_FormulaOutput_*.log`。

QMT 官方知识库中，`ContextInfo.schedule_run` 是不依赖 bar 的定时器；`prType=49` 定义为盘后定价。部署前应在当前券商 QMT 版本的模拟账户中再次验证接口可用性。
