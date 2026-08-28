# CSI1000 一手 SELL 验收（历史）

**不是当前日常流程。** 现网调度是 `alla_v4_ladder_k1h5_postclose_real`，日常开关是
QMT 策略启停，不要为全A 观察盘建 `LIVE_OK_` / `PR49_LIVE_OK_`。

下面整段是 CSI1000 收盘集合竞价一手阶段的受控验收原文，只在明确复用旧账本
`csi1000_b6m_b2s_postclose_real` 时使用。现网合同见 [../README.md](../README.md)。
探针逐日门禁见 [PR49_PROBE_CHECKLIST.md](PR49_PROBE_CHECKLIST.md)。

## 主策略 SELL 验收与 PAUSED 交接

本流程只卖出用户明确选择的一手持仓，不开始正式建仓。所有命令在仓库根目录运行，
先把占位符改为用户确认的实际交易日和 QMT 股票代码：

```bash
TRADE_DATE=YYYY-MM-DD
STOCK_CODE=600000.SH
PREVIEW="live_trading/logs/csi1000_b6m_b2s_postclose_real/previews/signal_${TRADE_DATE}.json"
```

### 1. 正常策略计划只做审计预览

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_publish_signals.py \
  --config csi1000_b6m_b2s_postclose_real \
  --trade-date "$TRADE_DATE" --mode LIVE --audit-preview "$PREVIEW"

/opt/anaconda3/envs/qlib/bin/python -m json.tool "$PREVIEW"
```

preview 只是证据：它不会登记 batch，也不会写 QMT inbox，更不是执行授权。复核
`trade_date`、`signal_date`、当前持仓、最多两笔 BUY、`sell_count=0`，并确认 Top30
渐进建仓期间没有 Drop/SELL。若不满足，停止，不进入人工 SELL。

禁止手工编辑 JSONL，包括 preview、signal、fills 和 account 文件。人工测试意图只能由
下面的工具生成新 batch/checksum/client order ID；已经发布的文件不可覆盖或改写。

### 2. 预览并发布一笔受审计 SELL

先进入 durable `PAUSED`，从这一刻阻止普通策略发布与 operator SELL 并发；operator
工具还会持有账本旁的跨进程发布锁，覆盖 QMT root 预检、durable record 和 inbox
发布/接管，保证两个恢复进程不能重建同一批次。后续验收完成后仍保持暂停：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/set_execution_state.py \
  --config csi1000_b6m_b2s_postclose_real \
  --state PAUSED --reason 'exclusive one-lot main sell verification'

/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/set_execution_state.py \
  --config csi1000_b6m_b2s_postclose_real --get
```

输出必须为 `PAUSED`。再确认账本和 QMT root 没有同日其他 main LIVE batch。下面的
`batch_status.py` 预期以状态码 1 返回且 stdout 为空；0 表示已有 active batch，其他状态
表示检查失败，二者都必须停住：

```bash
set +e
ACTIVE_BATCH=$(/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/batch_status.py \
  --config csi1000_b6m_b2s_postclose_real --trade-date "$TRADE_DATE")
BATCH_STATUS=$?
set -e
test "$BATCH_STATUS" -eq 1
test -z "$ACTIVE_BATCH"

DATE_COMPACT=${TRADE_DATE//-/}
find /Volumes/qmt_bridge/inbox /Volumes/qmt_bridge/processing \
  -maxdepth 1 -type f \
  -name "signal_${DATE_COMPACT}_csi1000_b6m_b2s_postclose_real_*" -print
find /Volumes/qmt_bridge/state -maxdepth 1 -type f \
  -name "*${DATE_COMPACT}_csi1000_b6m_b2s_postclose_real_*" -print
find /Volumes/qmt_bridge/state /Volumes/qmt_bridge/pr49_probe/state \
  -maxdepth 1 -type f \( -name "LIVE_OK_${TRADE_DATE}" \
  -o -name "PR49_LIVE_OK_${TRADE_DATE}" \) -print
```

三个 `find` 都必须无输出。发布 CLI 会在跨进程锁内、写账本/SMB 前再次执行同样的
durable state、同日 LIVE ledger、inbox/processing/state 和两个 profile 授权 marker
排他检查，并在实际暴露 inbox 前再次检查 marker。任一同日 marker、其他 main LIVE
batch 或 QMT artifact 都拒绝 seq900 发布或 DB-only recovery；即使 exact inbox pair 已
存在，只要 marker 已存在也会停止接管。不得删除旧证据来强行通过，应先查明并验收。

首先确保账本和同一交易日的可信券商账户快照都显示 `$STOCK_CODE` 可用至少 100 股。
若当日 snapshot 缺失，使用下面独立的 snapshot-only 观察入口；不能复用旧快照或伪造
account JSONL。观察请求不是 LIVE batch，不创建/读取授权 marker，也没有下单路径。

```bash
# 先只读预览；collector 选择实际正在运行的 QMT 实例，for-config 记录证据用途。
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/request_account_snapshot.py \
  --collector-config csi1000_b6m_b2s_postclose_real \
  --for-config csi1000_b6m_b2s_postclose_real \
  --trade-date "$TRADE_DATE"

# prepare 只把 exact canonical bytes 写入 SQLite；记下输出的 request_id 后停止复核。
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/request_account_snapshot.py \
  --collector-config csi1000_b6m_b2s_postclose_real \
  --for-config csi1000_b6m_b2s_postclose_real \
  --trade-date "$TRADE_DATE" --prepare

REQUEST_ID=snapshot_YYYYMMDD_32位小写十六进制ID

# 只按 durable request_id 暴露已复核的原字节；该确认变量不是交易授权。
# 必须在交易日 14:45:00 之前完成；到点及之后 CLI 硬拒绝新请求。
SNAPSHOT_OBSERVATION_CONFIRM=YES /opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/request_account_snapshot.py \
  --collector-config csi1000_b6m_b2s_postclose_real \
  --publish-request-id "$REQUEST_ID"

bash live_trading/run_import_cron.sh csi1000_b6m_b2s_postclose_real
```

QMT 的 `SNAPSHOT_REQUEST_RECEIVED`、`SNAPSHOT_REQUEST_TERMINAL` 和 Mac 状态
`IMPORTED_COMPLETE` 必须匹配 request ID、当日日期、profile、root 和脱敏账号。QMT
ACCOUNT 查询必须恰好返回一行，返回行自身的**完整账号**须与运行时完整账号精确匹配；
只有 masked 值、缺失、OTHER 或多行都产生 ERROR，不能用 request 中的 fingerprint 或
脱敏账号代替券商返回身份。POSITION 行若带账号也必须是完整账号精确匹配。Mac 导入仍会
再次核对 response 与 durable request；磁盘 response/日志只保存 mask 和 fingerprint。
旧版或诊断工具产生的 `DIAGNOSTIC_POSITIONS_ONLY` 仍只能排障，不能通过发布门禁。

请求处理期间 `snapshot_requests/status.json` 为持久 ERROR/blocking：半对、`.tmp`/intent、
processing、未导入 response 或不完整 archive 都会阻止交易 `_advance`，且不会自动删除。
成功导入会把 response 两件套移入 archive；只有 request + COMPLETE response 四件套均
校验成功并完整归档后，下一次 observer 才写 `CLEAR`。监控出现
`SNAPSHOT_RESIDUE_BLOCKED` 时不得绕过，应完成导入或人工核查残留。导入后再次确认两个
marker 仍不存在并停止；批次发布和 marker 创建属于后续独立停点。

main 与 probe 共用主根 `state/SNAPSHOT_ORDER_ADVANCE.lock`。Mac 在 14:45 前发布时用
`O_CREAT|O_EXCL` 创建包含 owner/request/profile/timestamp 的 gate；它不是短锁，而是从
request 暴露一直保留到 Mac 已提交 `IMPORTED_COMPLETE`、response 完整归档且四件套再次
校验通过。两个 QMT profile 的完整 `_advance` 都必须先取得同一个 gate，observer 不取
gate，因而能继续完成只读查询。ERROR、半对、DB/归档崩溃、owner 不匹配都会保留 gate。
Mac publisher 与 importer 还共用主根 `state/SNAPSHOT_MAC_LIFECYCLE.lock`：双方先取该锁，
再读取/提交 SQLite 状态；importer 在锁内完成归档校验，并把 gate 删除严格放在最后一步。
若 DB 已 terminal 但四件套不完整，原 matching gate 缺失或损坏即失败关闭，禁止补造 gate、
补归档或当作已释放；publisher 的 `REQUESTED` retry 也只能复核原 matching gate，缺失或
损坏时不得补造 gate/请求文件；terminal retry 同样绝不重建 gate。
不得按文件年龄自动删除。若 monitor 报 gate 残留，先停止 main/probe 两个 QMT 实例，
保存 gate 元数据和四目录清单；只有确认对应 request 已安全终止并获得新的人工审批后，
才可把 gate 受控移动到独立 quarantine 留证，禁止直接删除后继续交易。

首次部署必须在真实 Windows↔Mac SMB 上做双向互操作验收：Mac 持有 gate 时 main/probe
QMT 的 `O_EXCL` 均须失败；任一 QMT 持有 gate 时 Mac 发布也须失败。仓库单测只证明双方
使用相同主根、文件名和原子创建协议，不能替代 SMB 服务端原子性的实机复核。

Mac postmarket monitor 把 bridge root 以及 `snapshot_requests/inbox`、`processing`、
`archive`、`responses` 四个目录都视为部署必需项；任一路径缺失、不是目录或无法列目录均
产生带 `path/expected/observed` 的 `SNAPSHOT_RESIDUE_BLOCKED` CRIT，不能因 status 缺失
或显示 CLEAR 而忽略。四个可读空目录是唯一的无残留 control。

```bash
# 只读预览；stdout 应为一笔 100 股 CLOSE_AUCTION_LIMIT SELL
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_operator_probe.py \
  --config csi1000_b6m_b2s_postclose_real \
  --trade-date "$TRADE_DATE" --stock-code "$STOCK_CODE" \
  --side SELL --quantity 100 --reason operator_sell_probe

# 逐字段人工复核后才发布不可变批次；确认变量只作用于本进程
LIVE_TRADING_CONFIRM=YES /opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_operator_probe.py \
  --config csi1000_b6m_b2s_postclose_real \
  --trade-date "$TRADE_DATE" --stock-code "$STOCK_CODE" \
  --side SELL --quantity 100 --reason operator_sell_probe --publish
```

发布后再次核对 Windows QMT 主实例绑定、`CLOSE_AUCTION/prType=11`、
`MAX_ORDER_QUANTITY=100`、股票和日期。得到用户当日明确确认后才手工创建同日
`LIVE_OK_YYYY-MM-DD`；不创建未来日期 marker，也不允许 probe 同日授权。
Windows 必须先把仓库中的
`live_trading/qmt_strategy/New-OperatorAuthorizationMarker.ps1` 复制到
`D:\qmt_bridge\tools\` 并复核哈希；禁止再使用裸文件创建命令。受控脚本和 Mac
publisher 使用同一个 `D:\qmt_bridge\state\OPERATOR_AUTHORIZATION.lock`，脚本在锁内
重新检查日期、截止时间及两个 profile marker：

```powershell
$TradeDate = "YYYY-MM-DD"
& "D:\qmt_bridge\tools\New-OperatorAuthorizationMarker.ps1" `
  -Profile CLOSE_AUCTION -TradeDate $TradeDate
```

只有机器状态行 `AUTHORIZATION_COMMITTED`（exit 0）才表示命令完成；即使同时出现
`AUTHORIZATION_COMMITTED_WARNING`，也必须把 marker 当作已经不可逆授权，不能重试、
删除或称为失败。`AUTHORIZATION_NOT_COMMITTED`（exit 1）只在脚本仍持有共享授权锁时
确认最终 marker 不存在并写出状态，随后才释放锁；但仍可能留下阻断后续
publisher/monitor 的 intent。未持锁时读到不存在并不是稳定证明，必须按 unknown 处理。
`AUTHORIZATION_STATE_UNKNOWN`（exit 2，`action=STOP_BOTH_QMT_NO_RETRY`）表示最终 marker
不可读、QMT 可能已经获得授权：立即停止主策略和 probe 策略，不重试、不删除 marker、
不清理 intent，直至人工明确最终状态。若命令中断或输出不可读，最终 marker 一旦存在仍按
committed 处理。任何失败都不得改用其他命令补建 marker。首次启用前还
必须按 QMT README 的“双向 SMB 锁互操作验收”确认 macOS `filelock` 与 Windows
`FileStream` 在当前共享盘上确实互斥。

### 3. 导入、盘后验收并保持暂停

15:01 后先导入，再运行盘后检查：

```bash
bash live_trading/run_import_cron.sh csi1000_b6m_b2s_postclose_real
bash live_trading/run_monitor_cron.sh postmarket csi1000_b6m_b2s_postclose_real

/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_monitor.py \
  --config csi1000_b6m_b2s_postclose_real \
  --stage postmarket --date "$TRADE_DATE"
```

只有真实 QMT 委托号、100 股可信终态、费用、卖后持仓、券商账户快照及现金/持仓对账
全部一致才算通过。仅有 API return、`SUBMITTED_UNCONFIRMED` 或 `passorder` 日志不算
受理；必须观察到 `ORDER_OBSERVED`，之后才可出现 `ACCEPTED`。

验收通过后复核主策略仍为经审计暂停：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/set_execution_state.py \
  --config csi1000_b6m_b2s_postclose_real --get
```

`PAUSED` 后，16:00 数据、预测、日报和监控照常运行；发布 wrapper 只写审计 preview，
不登记或发布 LIVE 批次。只有新的用户明确指令才能改回 `ACTIVE`。prType=49 的双实例
部署与逐日操作见 [QMT 部署说明](qmt_strategy/README_QMT.md) 和
[PR49 探针清单](qmt_strategy/PR49_PROBE_CHECKLIST.md)。
