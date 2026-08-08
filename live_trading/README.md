# CSI1000 B6-M 收盘集合竞价实盘交易

当前活动系统是 `csi1000_b6m_b2s_postclose_real`。它使用冻结的 B6-M seed 4000、CSI1000、Top30/Drop2/Hold20，以及每天 Top2 的渐进建仓；QMT 在 14:57 收盘集合竞价使用 `prType=11` 显式限价。

Windows QMT、SMB 桥接和 Server酱通知已完成基础连接验收。真实资金账号只通过本地
`QMT_REAL_ACCOUNT_ID` 与 QMT UI 绑定，不写入 Git；`LIVE_OK` 和后续晋级仍是显式步骤。
旧模拟盘配置仅作历史材料，不应再调度。

## 固定契约

| 项目 | 值 |
|---|---|
| 活动配置 | `live_trading/configs/csi1000_b6m_b2s_postclose_real.yaml` |
| 对照配置 | `backtest/configs/csi1000_b6m_b2s_postclose_real_parity.yaml` |
| 股票池 / benchmark | CSI1000 / `SH000852` |
| 账户口径 | 真实账号由 `QMT_REAL_ACCOUNT_ID` 提供；账本初始经济基准 1,000,000 元、价值调整 0；当前现金/持仓只信任已导入券商快照 |
| 策略 | Top30 / Drop2 / initial Top2 / Hold20 / risk 0.93（保留 7% 现金） |
| 研究策略基线 | `qlib_exp/backtest/configs/strategy-stability/b6-m/topk-t30-d2-h20_csi1000_full.yaml` |
| 模型 | B6-M seed 4000，SHA-256 `368a503c...e6325` |
| 账本 | `live_trading/data/csi1000_b6m_b2s_postclose_real.db` |
| 账户环境 | `REAL`，`allow_real_money: true`，每单最多 100 股 |
| 执行时间 | 14:57:05 买卖同时进入收盘集合竞价，15:00:30 终结，15:01 快照 |

欠仓阶段在实际持仓达到 30 只前不卖出，每天最多买入两只未持仓股票。每只买入目标毛市值始终是 `当前总资产 × 0.93 / 30`。持仓超过 30 只时禁止新增买单，优先恢复目标持仓数。

## 文件与数据流

```text
T 日 16:00 导入/对账 → 更新收盘数据/名称 → 日报
             → 生成预测 → 发布 T+1 protocol-v2 批次 → 完整性检查
                                  │
                                  ▼
T+1 日 Windows QMT 14:57–15:01 → prType=11 → fills/account 回执
```

BUY 计划只携带 `target_value`，计划股数为 0。QMT 用实时参考价计算整手数量，以当日涨停价校验冻结资金；SELL 使用当日跌停价。只有查询到真实委托号才记录 `ACCEPTED`。持久日志位于共享目录 `D:\qmt_bridge\logs`。

## 本地只读检查

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/check_backtest_parity.py \
  --config csi1000_b6m_b2s_postclose_real

openssl dgst -sha256 \
  live_trading/models/b6_m/seed4000/trained_model

/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_publish_signals.py \
  --config csi1000_b6m_b2s_postclose_real \
  --trade-date YYYY-MM-DD --mode SIMULATE --dry-run
```

正式发布默认拒绝 stale signal，并在写共享目录前通过模型 SHA 和 Live/Backtest parity 门禁。

## 外部环境

密钥与账户放在 `~/.qlib_live_env`，不要提交：

```bash
export TUSHARE_TOKEN='...'
export QMT_REAL_ACCOUNT_ID='<仅在本机填写真实资金账号>'
export LIVE_CONFIG_ID='csi1000_b6m_b2s_postclose_real'
export LIVE_RUN_MODE='LIVE'
```

`QMT_ACCOUNT_ID` 不会被发布器读取。REAL 只读 `QMT_REAL_ACCOUNT_ID`，SIMULATION 只读 `QMT_SIM_ACCOUNT_ID`；QMT 查询返回的账户 ID 必须与请求一致。`LIVE_TRADING_CONFIRM=YES` 不持久化，在一手验收阶段每次发布显式提供。

Windows 安装步骤见 [QMT 部署说明](qmt_strategy/README_QMT.md)。Mac 的 `live.bridge_root` 必须指向已挂载的共享目录，发布前至少确认：

```bash
test -d /Volumes/qmt_bridge/inbox
test -w /Volumes/qmt_bridge/inbox
```

## 受控晋级

### 1. 历史 Shadow/模拟盘（已退役）

- `LIVE_RUN_MODE=SIMULATE`；
- 不创建 `state/LIVE_OK_YYYY-MM-DD`；
- QMT 只生成 `SKIPPED simulated` 回执，不访问交易账户；
- 连续核对 signal date、Top2、单槽金额、空批次、Hold20 和文件 SHA。

### 2. 一手实盘（当前）

必须同时满足：

- 确认 QMT UI 绑定的账号与本地 `QMT_REAL_ACCOUNT_ID` 完全一致；
- 保持 `qmt_signal_bridge.py` 的 `MAX_ORDER_QUANTITY = 100`；
- 设置 `LIVE_RUN_MODE=LIVE` 和 `LIVE_TRADING_CONFIRM=YES`；
- 每个交易日人工创建当日 `LIVE_OK_YYYY-MM-DD`；
- 逐单核对 QMT 委托类型 11、涨跌停限价、成交回报、账本现金与持仓。

删除 `LIVE_OK` 只会阻止尚未提交的新单；已提交订单仍会查询、撤单和终结，避免在途订单失管。
当天是否执行会冻结在 14:57:05 的首次交易唤醒：若当时缺少 `LIVE_OK`，补建也不会让该批订单突然转为执行，必须等下一交易日重新授权。

### 3. 实盘晋级

一手阶段通过后才可单独评审解除初始空仓/现金预检和数量上限。账户匹配、REAL 门禁、双开关和每日 `LIVE_OK` 始终保留；系统不会自动晋级。

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
ACCOUNT 查询必须恰好返回一行，返回行自身的账号须与运行时账号 full/masked 精确匹配；
缺失、OTHER 或多行都产生 ERROR，不能用 request 中的 fingerprint 代替券商返回身份。
POSITION 行若带账号也会逐行复核。Mac 导入仍会再次核对 response 与 durable request。
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
不得按文件年龄自动删除。若 monitor 报 gate 残留，先停止 main/probe 两个 QMT 实例，
保存 gate 元数据和四目录清单；只有确认对应 request 已安全终止并获得新的人工审批后，
才可把 gate 受控移动到独立 quarantine 留证，禁止直接删除后继续交易。

首次部署必须在真实 Windows↔Mac SMB 上做双向互操作验收：Mac 持有 gate 时 main/probe
QMT 的 `O_EXCL` 均须失败；任一 QMT 持有 gate 时 Mac 发布也须失败。仓库单测只证明双方
使用相同主根、文件名和原子创建协议，不能替代 SMB 服务端原子性的实机复核。

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

## 调度

[crontab.csi1000_postclose.example](crontab.csi1000_postclose.example) 是唯一的新调度模板：

- crontab 只维护一行，每个工作日 16:00 启动一次；
- 先串行运行回执导入、`postmarket`、Tushare 行情更新和股票名称缓存刷新；仅在行情更新成功后运行 `report`；
- postclose 完成后立即发布下一交易日，发布完成后立即运行 `evening` 完整性检查；
- 三个阶段之间没有额外定时或等待。

调度器把每日阶段回执原子写到
`live_trading/.scheduler/<config>/<YYYY-MM-DD>/<stage>.json`。每次调用都会按
postclose → publish → evening 的固定顺序补齐尚无回执的阶段；无论成功还是失败，每阶段每天都只自动尝试一次。某阶段失败会让整条流水线最终返回非零，但不会阻止后续阶段执行，失败后仍按告警提示人工恢复，不会形成盲目重试。

`run_postclose_cron.sh` 即使遇到导入、postmarket 或股票名称刷新告警也会继续后续步骤，避免非关键问题连带阻断信号发布；行情更新失败时跳过日报，由更新脚本直接告警。股票名称从 Tushare 刷新到本地 SQLite 缓存，Web 页面只读本地数据，不在请求期间访问外网。该脚本在整个流水线期间持有 `.locks/<config>_postclose.lock`，并与发布任务执行双向锁检查；任一方向发现并发都失败关闭，避免读取正在改写的数据。

所有 wrapper 都支持位置参数 config ID，也支持 `LIVE_CONFIG_ID` / `QLIB_LIVE_CONFIG_ID`，默认新 CSI1000 配置。调度器和阶段 wrapper 都使用原子目录锁防止并发；残留 `.locks/<config>_*.lock` 时应先确认没有任务运行，再人工删除。监控 WARN/CRIT 的退出码会原样返回，不再被吞掉。系统不自动重试失败阶段或补发，`evening` 告警后必须先检查原因再人工恢复。

部署或迁移机器时先检查现有任务，确保旧 CSI300 条目已停用且没有重复任务，再安装该单行模板：

```bash
crontab live_trading/crontab.csi1000_postclose.example
crontab -l
```

## 监控服务

只读 Web 监控由 macOS `launchd` 常驻托管，登录后自动启动，异常退出后自动拉起。服务只监听本机
`127.0.0.1:8081`，不会直接暴露给局域网。仓库内的受控模板是
`live_trading/launchd/com.yuxianqi.qlib-live-monitor.plist`，部署到
`~/Library/LaunchAgents/` 后由 `launchctl` 加载。

```bash
# 服务状态与健康检查
launchctl print gui/$(id -u)/com.yuxianqi.qlib-live-monitor
curl --noproxy 127.0.0.1 --fail http://127.0.0.1:8081/api/overview

# 本机浏览器访问
open http://127.0.0.1:8081
```

标准输出与错误日志分别写入
`live_trading/logs/csi1000_b6m_b2s_postclose_real_web_service.stdout.log` 和
`live_trading/logs/csi1000_b6m_b2s_postclose_real_web_service.stderr.log`。服务入口
`run_web_service.sh` 会加载 `~/.qlib_live_env`，与 cron 使用同一套运行环境和活动配置。

## 日常命令

```bash
# 查看今天三个阶段的自动尝试状态
find live_trading/.scheduler/csi1000_b6m_b2s_postclose_real/$(date +%Y-%m-%d) \
  -maxdepth 1 -name '*.json' -print

# 16:00 收盘流水线（导入 → 检查 → 更新 → 日报）
bash live_trading/run_postclose_cron.sh csi1000_b6m_b2s_postclose_real

# 显式确认后发布下一开市日
bash live_trading/run_publish_cron.sh csi1000_b6m_b2s_postclose_real

# 数据库没有下一交易日批次时，检查发布日志后人工补发
bash live_trading/run_publish_catchup_cron.sh csi1000_b6m_b2s_postclose_real

# 数据库已有批次但共享文件缺失时，按明确交易日幂等重发
bash live_trading/run_publish_cron.sh \
  csi1000_b6m_b2s_postclose_real YYYY-MM-DD

# 导入并打印 planned/terminal/missing
bash live_trading/run_import_cron.sh csi1000_b6m_b2s_postclose_real

# 分阶段监控
bash live_trading/run_monitor_cron.sh postmarket csi1000_b6m_b2s_postclose_real
bash live_trading/run_monitor_cron.sh report csi1000_b6m_b2s_postclose_real
bash live_trading/run_monitor_cron.sh evening csi1000_b6m_b2s_postclose_real
```

人工恢复前先查看
`live_trading/logs/csi1000_b6m_b2s_postclose_real_publish_cron.log`。若 SMB 不可访问，先恢复挂载；若 `postclose` 锁存在，先确认 16:00 流水线是否仍在运行。

## 失败关闭与恢复

- parity、模型 SHA、目标信号日、账户环境/账号、共享目录或批次内容冲突任一失败：不发布；
- 同 batch 的字节完全相同：幂等成功；不同：拒绝覆盖；
- 先前 LIVE 批次未取得终态回执：拒绝发布后续 LIVE 批次；
- QMT 重启：从 `processing/` 和 `state/active_*.json` 恢复，已标记提交的订单不重提；
- 活动状态损坏：保留 `.corrupt_*` 证据并把整批视为可能已提交，只查询/撤单/终结，绝不重提；
- 畸形或超限批次：移入 archive；已有同名归档不覆盖，使用 `.repeat_*` 保留两份；
- 15:00:05 后不再提交新单；15:00:30 将未完成订单写为终态；
- 回滚：停用新 cron、停止 QMT strategy ID、保留 SQLite 与 bridge archive。不要自动恢复旧 CSI300 调度。

发生账本/券商持仓或现金差异时，先停止下一日 LIVE 发布并按 QMT 委托、成交和账户快照核对。当前实盘账本价值调整固定为 0；任何人工账本修正都应使用有审计记录的管理入口。
