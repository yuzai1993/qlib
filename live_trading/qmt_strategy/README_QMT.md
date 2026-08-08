# QMT 收盘集合竞价桥接部署

`qmt_signal_bridge.py` 是 QMT 内置 Python 3.6 策略，消费 protocol-v2 批次并在 14:57 收盘集合竞价使用指定价 `prType=11`。买单显式传当日涨停价，卖单显式传当日跌停价。源码保持 ASCII，文件头 `#coding:gbk` 不得删除。

仓库源码默认失败关闭为 `SIMULATION` 且 `ALLOW_REAL_MONEY=False`。真实资金只能在 QMT 本地副本中显式选择 `REAL`，并与 Mac 的 REAL 批次双向匹配。

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

## 2. 导入两个独立策略实例

在大 QMT 的模型交易/策略编辑器中新建两个内置 Python 策略，分别复制同一版
`qmt_signal_bridge.py`，再只修改每个 QMT 本地副本的设置区。不得让两个实例共享
inbox/processing/active state。

主策略本地副本：

```python
EXECUTION_PROFILE = "CLOSE_AUCTION"
BRIDGE_ROOT = r"D:\qmt_bridge"
OTHER_BRIDGE_ROOT = r"D:\qmt_bridge\pr49_probe"
ACCOUNT_ID = "<只在 QMT 本地填写真实资金账号>"
ACCOUNT_TYPE = "STOCK"
STRATEGY_NAME = "qlib_bridge_main"
ACCOUNT_ENVIRONMENT = "REAL"
ALLOW_REAL_MONEY = True
MAX_ORDER_QUANTITY = 100
```

盘后固定价格探针本地副本：

```python
EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"
BRIDGE_ROOT = r"D:\qmt_bridge\pr49_probe"
OTHER_BRIDGE_ROOT = r"D:\qmt_bridge"
ACCOUNT_ID = "<只在 QMT 本地填写真实资金账号>"
ACCOUNT_TYPE = "STOCK"
STRATEGY_NAME = "qlib_pr49_probe"
ACCOUNT_ENVIRONMENT = "REAL"
ALLOW_REAL_MONEY = True
MAX_ORDER_QUANTITY = 100
```

两个实例的 `ACCOUNT_ID` 必须相同，并在两个 QMT UI 策略页面明确绑定同一真实账户；
脱敏日志或源码设置不能代替 UI 复核。真实账号只写入 Windows 本地副本，不提交到 Git。

共享账户已经持仓后，默认的空仓/初始资金预检会正确地失败关闭。每个实际测试日都从
QMT UI 读取当时的可用资金，把两个本地副本的 `REAL_EXPECTED_INITIAL_CASH` 更新为该值，
保留 `REAL_INITIAL_CASH_TOLERANCE = 100.0`，并设置
`REAL_REQUIRE_EMPTY_POSITIONS = False`。逐个重新编译；不得用很大的 tolerance 绕过复核。

编译后先只启动一个需要验收的执行实例。两个实例都可保持编译就绪，但主策略 SELL 与
prType=49 探针不得同日授权。每次启动都在对应根目录的持久日志检查：

```powershell
Get-Content D:\qmt_bridge\logs\qmt_events_YYYY-MM-DD.jsonl -Tail 100
Get-Content D:\qmt_bridge\pr49_probe\logs\qmt_events_YYYY-MM-DD.jsonl -Tail 100
```

必须出现 `RUNTIME_CONFIG` 和 `TIMER_REGISTERED`。逐项核对 source SHA/version、QMT
版本、策略名称、脱敏账户、profile、两个 root、price type、100 股上限及所有时间。
不一致时停止策略，不能创建授权 marker。

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

策略在 `init` 中绑定账户回调并注册两个 `ContextInfo.schedule_run`：独立的只读
`qlib_snapshot_observer` 从 09:35:00 起每 3 秒只扫描 snapshot request；原交易 timer
仍从 14:56:55（probe 为 15:04:55）起每 3 秒推进订单状态机。snapshot callback 不调用
订单 `_advance`；若 snapshot processor lock 正被另一 worker 持有，交易 timer 也失败关闭，
不会继续 claim/order/passorder。旧 QMT 没有新版接口时两个 timer 都退回 `run_time`。

执行顺序：

1. 认领当日 `signal_*.jsonl` + `.done`，检查 schema 2.0、checksum、日期、账户和配置的账户环境；
2. 主策略在 14:57:05 提交 SELL/BUY；固定价格探针在 15:05:00 提交一笔受控订单；
3. 用 `get_instrument_detail`（旧版为 `get_instrumentdetail`）读取 `UpStopPrice`/`DownStopPrice`；
4. 主策略调用 `prType=11` 并显式传涨跌停价；探针调用 `prType=49`、`price=0`，同时把
   官方收盘参考及来源写入日志；
5. 查询到真实 QMT 委托编号后才写 `ACCEPTED`；
6. 15:00:05 后处理仍未终态委托，15:00:30 终结，15:01 重写账户与持仓快照。

如果 `lastPrice` 缺失或非正数，BUY 失败关闭，不使用盘口价、滑点、昨收或信号价格回退。
探针 profile 的时间为 15:05:00 提交、15:28:00 撤单、15:30:00 终结、15:31:00
账户快照；其独立授权是 `PR49_LIVE_OK_YYYY-MM-DD`。主策略仍使用
`LIVE_OK_YYYY-MM-DD`。同一天两个 marker 同时存在时，两个实例都在 `passorder` 前失败关闭。
固定价格 profile 还要求 QMT 证券详情明确给出盘后固定价格资格；只有结构化
`after_hours_eligible=true` 才会继续。false/缺失/无法解析时写
`SECURITY_ELIGIBILITY_ERROR` 与 ERROR 回执，不调用 `passorder`。

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
执行决定在 14:57:05 首次交易唤醒时冻结；首次缺少 `LIVE_OK` 的批次即使稍后补建开关，也会整批保持 simulated。

## 6. 实盘一手验收

Mac 批次必须是 `account_environment=REAL` 与 `mode=LIVE`。主策略 SELL 使用当日
`LIVE_OK`；探针使用不同的 `PR49_LIVE_OK`。一手结果验收前不得改动 100 股上限。
两个 profile 的当日 marker 在 operator 批次发布前都必须不存在；marker 只能在批次
发布后获得用户当日明确确认，再按各自 runbook 手工创建。旧 marker 会令初次发布、
DB-only recovery 和 visible inbox 接管全部失败关闭，禁止通过删除执行证据绕过。

主策略 SELL 的 preview→人工发布→导入→postmarket→`PAUSED` 命令见上级
[实盘 README](../README.md)。prType=49 BUY→下一交易日 SELL 的逐项门禁见
[PR49_PROBE_CHECKLIST.md](PR49_PROBE_CHECKLIST.md)。两条流程都要求发布前存在同交易日、
与真实账户可靠绑定的 broker snapshot。受控入口是
`live_trading/scripts/request_account_snapshot.py`：它只在所选实例的
`snapshot_requests/inbox` 写独立 request/done；QMT 原子认领到 `processing`，只调用
`get_trade_detail_data(..., "ACCOUNT"/"POSITION")`，把带 checksum 的 response/done 写到
`responses` 后归档 request。该路径不解析 signal、不读取 `LIVE_OK`/`PR49_LIVE_OK`，也不
调用 `passorder`。

Mac CLI 先用 `--prepare` 把 canonical request/checksum 持久化为 `PREPARED`，但不写 SMB；
人工逐字段复核 exact bytes 后，只能用 `--publish-request-id` 引用该 durable 行。publish
不接受日期、用途或账号等业务字段重建，复核 profile/root/account/当天日期后原字节暴露，
状态变为 `REQUESTED`。崩溃重试仍引用同一 request ID，不生成替代请求。
新请求只能在交易日 `14:45:00` 之前发布；到点及之后 Mac 硬拒绝。QMT 还会校验
request 的 `trade_date`、`publish_cutoff=14:45:00` 与同日且早于 cutoff 的 `created_at`，
但 cutoff 前已发布的请求可在 cutoff 后继续由 observer 完成，未完成 residue 继续阻断。

两个实例可任选一个作为 collector，但 request 中的 profile、canonical Windows root、
requested-for strategy、REAL account fingerprint 和 purpose 必须逐项匹配，不能把 main
root 的请求放入 probe root 或反向复用。QMT 重启会恢复
`snapshot_requests/processing`；相同 terminal request 只记录 replay，改变任何字段或
checksum 都保留证据并失败关闭。只读 timer 的冷启动只初始化 snapshot 目录、profile/root
校验与账号回调绑定，不会恢复交易批次、注册交易 timer 或进入 `_advance`。

`COMPLETE` 要求 ACCOUNT 恰好一行，且返回行自身的完整账号与 `ACCOUNT_ID` 精确匹配；
masked-only、缺失、OTHER、多行均为 ERROR。POSITION 若暴露账号字段，也必须返回完整
账号并精确匹配；masked 值只能展示，不能授权。request 内的 fingerprint 不能替代券商
返回身份，只有完整账号校验后才重新计算 response fingerprint；落盘仅存 mask/fingerprint。
`snapshot_requests/status.json` 会把任一半对、tmp/intent、processing、待导入 response 或
不完整 archive 标记为 ERROR/blocking；原文件不会自动删除。Mac 成功导入 response 后，
完整 request + COMPLETE response 四件套全部归档，下一 observer 周期才转 `CLEAR`。
监控出现 `SNAPSHOT_RESIDUE_BLOCKED` 时必须处置，禁止绕过。观察完成后必须再次停止，确认
没有授权 marker，再进入 operator batch 发布和用户确认流程。

两个 profile 共享 main root 的 `state/SNAPSHOT_ORDER_ADVANCE.lock`；probe 不能在自身
nested root 建另一把锁。Mac 用 `O_CREAT|O_EXCL` 写入 owner/request/profile/timestamp，
并持续持有到 `IMPORTED_COMPLETE` + 完整四件套归档后由 importer 校验 owner/request 和
所有 checksum 再释放。QMT 的订单 `_advance` 从 snapshot scan 到 claim/process 完成全程
持有同一 gate；observer 不持有。gate 无 stale 自动恢复。残留时停止两个实例、保存元数据
和目录证据，获得人工审批后受控移入 quarantine；禁止直接删除。首次部署必须在实际 SMB
上双向证明 Windows 与 Mac 的 `O_EXCL` 互斥，单元测试不能替代该验收。

Mac publisher/importer 共用主根 `state/SNAPSHOT_MAC_LIFECYCLE.lock`，并统一按“Mac 锁 → SQLite
状态 → 四件套归档校验 → 最后删除 gate”的顺序执行。`REQUESTED` publisher retry 只能
复核原 matching gate，缺失/损坏时不能重建 gate 或请求文件；terminal retry 也不能重建。
DB 已 terminal 但 archive 未完整时同样失败关闭，不能补造或继续归档。postmarket monitor
还要求 bridge root 和 inbox/processing/archive/responses
四目录均存在且可列；缺失或 SMB list error 都是 CRIT，正常空目录才是 CLEAR control。

## 7. 恢复与排障

| 现象 | 检查 |
|---|---|
| 批次不消费 | QMT 策略是否运行；inbox 是否同时有 jsonl/done；trade_date 是否为今天 |
| 15:00 后停止 | QMT 日志是否显示 timer 注册；版本是否退回 `run_time` |
| LIVE 全部 simulated | 当日 `LIVE_OK` 是否存在；header mode 是否 LIVE |
| BUY `official close unavailable` | `get_full_tick` 的 `lastPrice` 是否已更新为正数 |
| 涨跌停价无效 | `get_instrument_detail` 是否返回正的 `UpStopPrice`/`DownStopPrice` |
| 账户查询失败 | QMT UI 绑定账号、`ACCOUNT_ID`、header account ID 是否一致 |
| 回执缺失 | 检查 `processing/`、active state 与 `D:\qmt_bridge\logs\qmt_events_YYYY-MM-DD.jsonl` |
| 探针日志缺失 | 检查 `D:\qmt_bridge\pr49_probe\logs\qmt_bridge_YYYY-MM-DD.log` 和 `qmt_events_YYYY-MM-DD.jsonl` |
| API 返回但 UI 无委托 | 查 `ORDER_QUERY`/`ORDER_NOT_OBSERVED`；API return 本身不是 acceptance，必须有真实委托号、`ORDER_OBSERVED` 后才允许 `ACCEPTED` |
| 重启后未恢复 | processing 的信号对是否完整；active state JSON 是否可读 |
| `SNAPSHOT_RESIDUE_BLOCKED` | 查看 `snapshot_requests/status.json` 与四个子目录；完成 Mac 导入或人工核对半对/冲突，禁止删除证据强行放行 |
| `SNAPSHOT_ORDER_ADVANCE.lock` 残留 | 同时停止 main/probe，保存 owner/request/profile/timestamp 与四目录清单；人工审批后受控 quarantine，禁止按年龄自动删锁 |
| Mac 报持仓漂移 | 对照 QMT 委托/成交、`account_*.jsonl` 和本地 fills，先停下一日 LIVE |

共享目录每天持久化 `qmt_bridge_YYYY-MM-DD.log` 和 `qmt_events_YYYY-MM-DD.jsonl`，客户端重启不会清除。FormulaOutput 仅作为辅助日志。
