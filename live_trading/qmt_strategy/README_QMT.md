# QMT 收盘集合竞价桥接部署

当前生产主实例应编译为盘后固定价格（`EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"`，
`prType=49`）。**日常开关是本策略在 QMT 里的启停**，不要再创建 `LIVE_OK_` /
`PR49_LIVE_OK_`；bridge 会忽略这些文件。停策略 = 当天不交易；开策略且 inbox 有
LIVE 批次 = 到点下单。

`qmt_signal_bridge.py` 是 QMT 内置 Python 3.6 策略，消费 protocol-v2 批次并在 14:57 收盘集合竞价使用指定价 `prType=11`。买单显式传当日涨停价，卖单显式传当日跌停价。源码保持 ASCII，文件头 `#coding:gbk` 不得删除。

策略源码里写死 `ACCOUNT_ID`。启停本策略就是交易开关；批次头不再带账号、环境或 mode。

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

## 2. 导入生产策略实例

在大 QMT 的模型交易/策略编辑器中新建一个内置 Python 策略，复制渲染后的
`qmt_signal_bridge.py`。日常只跑这一份；启停本策略就是交易开关。

生产本地副本（由 `render_qmt_runtime.py` 写出，不要手改这三个常量以外的通道设置）：

```python
EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"
BRIDGE_ROOT = r"D:\qmt_bridge"
ACCOUNT_ID = "<仓库模板已写生产账号，渲染时也可覆盖>"
ACCOUNT_TYPE = "STOCK"
STRATEGY_NAME = "qlib_bridge_main"
MAX_ORDER_QUANTITY = 0
ENABLE_LADDER_NETTING = True
```

仓库模板默认仍是收盘集合竞价、一手上限、不抵销，避免误把模板当生产脚本编译。
需要回退到 `EXECUTION_PROFILE = "CLOSE_AUCTION"` 时重新渲染，不要就地改文件。

`ACCOUNT_ID` 必须在 QMT UI 策略页面绑定同一真实账户；源码设置不能代替
UI 复核。

每次启动都在根目录的持久日志检查：

```powershell
Get-Content D:\qmt_bridge\logs\qmt_events_YYYY-MM-DD.jsonl -Tail 100
```

必须出现 `RUNTIME_CONFIG` 和 `TIMER_REGISTERED`。逐项核对 source SHA/version、QMT
版本、策略名称、账户、profile、bridge root、price type 及所有时间。
不一致时停止策略。

### 2.1 共享授权锁与 SMB 互操作验收

把仓库中的 `New-OperatorAuthorizationMarker.ps1` 复制到
`D:\qmt_bridge\tools\`，记录并复核两端 SHA256。主策略和 probe 的唯一共同授权锁是：

- Windows：`D:\qmt_bridge\state\OPERATOR_AUTHORIZATION.lock`
- macOS：`/Volumes/qmt_bridge/state/OPERATOR_AUTHORIZATION.lock`

Mac publisher 用 `filelock.FileLock` 持锁覆盖 marker 复核、byte preflight、jsonl 和 done
全部 rename；PowerShell 脚本用 `FileStream(FileShare.None)` 加 byte lock，持锁覆盖日期、
截止时间、另一 profile marker、自身 marker 的复核和创建。主 root 与嵌套
`pr49_probe` 禁止各建一把锁。

PowerShell 先在最终 marker 同目录创建唯一 `.intent.<guid>.tmp`，完成强制 flush 和内容
读回后，才用同目录原子 rename 提交最终 marker。该 rename 是唯一不可逆 commit point；
最终 marker 一旦存在就必须按已授权处理，禁止删除回滚。rename 返回异常时脚本检查
final/intent 状态消歧；final 存在或状态无法可靠判定时保守输出
`AUTHORIZATION_COMMITTED` 并 exit 0。commit 后的 readback、Unlock、Dispose 故障只能
输出 `AUTHORIZATION_COMMITTED_WARNING`，不能降级为普通失败。

`AUTHORIZATION_NOT_COMMITTED`/exit 1 只会在脚本仍持有共享授权锁时确认最终 marker
不存在并写出状态，随后才释放锁；未持锁时读到不存在可能与另一个 creator 的提交竞态，
必须按 unknown 处理。遗留 intent 仍会让 Mac publisher 拒绝继续，并令 monitor 报
`AUTHORIZATION_INTENT_REMAINS` CRIT。`AUTHORIZATION_STATE_UNKNOWN`/exit 2（机器动作
`STOP_BOTH_QMT_NO_RETRY`）表示最终 marker 不可读，QMT 可能已经获得授权；立即停止主策略
和 probe 策略，禁止重试、删除 marker 或清理 intent，直至人工明确最终状态。只有停止
两个 QMT 策略、确认 final marker 不存在、确认输出从未出现 committed token，并核对
intent 内容与对应批次后，才允许人工隔离该 intent；不得把它直接改名为 marker。锁超时
或任何失败都禁止绕过受控脚本。

不同 SMB 服务端/macOS 挂载版本的锁映射可能不同。首次启用、共享盘重配或系统升级后，
必须在**不创建任何 marker**的前提下做双向互操作验收：一端持有上述文件的独占锁时，
另一端必须在超时内无法获取；交换方向再测一次。可分别用 Python `FileLock` 与脚本中
相同的 `FileStream.Open` + `Lock(0, 1)` 片段测试。验收期间同时确认两个 QMT inbox 均无
新增文件。若任一方向能同时进锁、lock 文件落在不同路径，或释放后不能重新获取，立即
停止两个 QMT 策略，不得创建 marker，先修复 SMB 挂载/锁语义。

## 3. 定时与状态机

策略在 `init` 中绑定账户回调，并注册一个 `ContextInfo.schedule_run`：交易 timer
从 14:56:55（盘后 profile 为 14:59:55）起每 3 秒推进订单状态机。旧 QMT 没有新版
接口时退回 `run_time`。

执行顺序：

1. 认领当日 `signal_*.jsonl` + `.done`，检查 schema 2.0、checksum、当日 `trade_date`；
2. 收盘集合竞价 profile 在 14:57:05 提交；盘后固定价格 profile 从 15:00:05 起尝试，
   15:05 连续撮合前由终价门控挡住尚未结算的收盘价；
3. 用 `get_instrument_detail`（旧版为 `get_instrumentdetail`）读取 `UpStopPrice`/`DownStopPrice`；
4. 集合竞价调用 `prType=11` 并显式传涨跌停价；盘后固定价格调用 `prType=49`，
   价格传当日官方收盘价（创业板买入限价不得低于收盘价，传 0 会被拒），
   同时把官方收盘参考及来源写入日志；
5. 查询到真实 QMT 委托编号后才写 `ACCEPTED`；
6. 撤单截止后处理仍未终态委托，到点终结，并写一笔账户与持仓快照到 `outbound/`。

如果 `lastPrice` 缺失或非正数，BUY 失败关闭，不使用盘口价、滑点、昨收或信号价格回退。
盘后固定价格不再做证券资格门禁；QMT 详情只记日志，不拦截 `passorder`。
快照和事件写完整 `account_id`，不做账号脱敏。

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

仓库模板里 `MAX_ORDER_QUANTITY = 100`、`ENABLE_LADDER_NETTING = False`、`EXECUTION_PROFILE = "CLOSE_AUCTION"` 是**故意保守**的：模板被误当成生产脚本直接跑也不会造成损失。生产形态一律由 [`live_trading/scripts/render_qmt_runtime.py`](../scripts/render_qmt_runtime.py) 渲染产生——盘后固定价格通道、开启阶梯抵销、`MAX_ORDER_QUANTITY = 0`（即无上限）。**不要手工编辑本地副本里的这三个常量**：渲染产物是唯一的生产事实来源，手改会让运行时与仓库记录对不上。需要回退到收盘集合竞价时，重新渲染并传 `execution_profile="CLOSE_AUCTION", enable_ladder_netting=False`，不要就地改文件。

一手阶段（`MAX_ORDER_QUANTITY = 100`）把每个可执行买卖订单限制为一手，那是探针的刻意限制。每天开盘前确认 QMT UI 绑定的账户，收盘后逐单核对价格类型、官方收盘价、数量、委托状态和回执。

开关是 QMT 策略启停，不是 `LIVE_OK` 文件。停策略 = 当天不再认领新批次；已经提交的
订单仍会继续查询、撤单和终结。

## 6. 实盘一手验收

一手验收是历史流程，不是现网日常。命令见
[CSI1000 一手 SELL 验收](CSI1000_OPERATOR_SELL_RUNBOOK.md) 与
[PR49_PROBE_CHECKLIST.md](PR49_PROBE_CHECKLIST.md)。现网合同见
[实盘 README](../README.md)。

QMT 桥接不再实现 `snapshot_requests` 观察协议，也不再读取 `LIVE_OK_` /
`PR49_LIVE_OK_`。账户与持仓以批次终结时写入 `outbound/account_*.jsonl` 为准。

## 7. 恢复与排障

| 现象 | 检查 |
|---|---|
| 批次不消费 | QMT 策略是否运行；inbox 是否同时有 jsonl/done；trade_date 是否为今天 |
| 15:00 后停止 | QMT 日志是否显示 timer 注册；版本是否退回 `run_time` |
| LIVE 全部 simulated | QMT 是否绑对账号、策略是否在跑、批次是否被认领拒绝 |
| BUY `official close unavailable` | `get_full_tick` 的 `lastPrice` 是否已更新为正数 |
| 涨跌停价无效 | `get_instrument_detail` 是否返回正的 `UpStopPrice`/`DownStopPrice` |
| 账户查询失败 | QMT UI 绑定账号、`ACCOUNT_ID`、header account ID 是否一致 |
| 回执缺失 | 检查 `processing/`、active state 与 `D:\qmt_bridge\logs\qmt_events_YYYY-MM-DD.jsonl` |
| 探针日志缺失 | 检查 `D:\qmt_bridge\pr49_probe\logs\qmt_bridge_YYYY-MM-DD.log` 和 `qmt_events_YYYY-MM-DD.jsonl` |
| API 返回但 UI 无委托 | 查 `ORDER_QUERY`/`ORDER_NOT_OBSERVED`；API return 本身不是 acceptance，必须有真实委托号、`ORDER_OBSERVED` 后才允许 `ACCEPTED` |
| 重启后未恢复 | processing 的信号对是否完整；active state JSON 是否可读 |
| Mac 报持仓漂移 | 对照 QMT 委托/成交、`account_*.jsonl` 和本地 fills，先停下一日 LIVE |

共享目录每天持久化 `qmt_bridge_YYYY-MM-DD.log` 和 `qmt_events_YYYY-MM-DD.jsonl`，客户端重启不会清除。FormulaOutput 仅作为辅助日志。
# 当前简化运行方式（2026-08-09）

主发布端不再创建或检查 `LIVE_OK`、`PR49_LIVE_OK`、`LIVE_TRADING_CONFIRM`，也不判断模拟/真实账户。QMT 操作员负责启动正确的策略实例并在 QMT 内确认账户绑定；任何已启动且指向主 inbox 的实例都会消费已发布信号。账本、成交回执和监控仅用于审计，不是执行闸门。

主策略卖出测试：先让 macOS 正常发布买入批次，再使用 `override_main_signal.py` 为上次买入标的生成 SELL 覆盖批次。不要改写原始预测或原批次。

prType=49 调试：复制 `qmt_pr49_debug.py` 到单独的 QMT 策略，绑定独立目录 `D:\qmt_bridge\pr49_debug`。在该目录放置 request.json，策略会以 `prType=49` 发出 BUY/SELL 并将完整返回写入 `qmt_pr49_events.jsonl`；它不接主监控、不写主账本。
