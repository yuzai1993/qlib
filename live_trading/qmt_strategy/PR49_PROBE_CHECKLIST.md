# prType=49 一手 BUY→次交易日 SELL 操作清单

本清单只用于真实账户上的受控一手探针，不是正式建仓入口。探针固定使用：

- `live_trading/configs/csi1000_pr49_one_lot_probe.yaml`
- `live_trading/scripts/run_operator_probe.py`
- `bash live_trading/run_probe_import.sh`
- `live_trading/data/csi1000_b6m_b2s_postclose_real.db`
- `csi1000_pr49_one_lot_probe`
- Windows 根目录 `D:\qmt_bridge\pr49_probe`
- Mac 根目录 `/Volumes/qmt_bridge/pr49_probe`
- `EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"`
- `prType=49`
- `MAX_ORDER_QUANTITY = 100`
- 当日授权名 `PR49_LIVE_OK_YYYY-MM-DD`

任何步骤失败都停住。不得换股票、扩大数量、补建未来日期 marker、手工编辑
signal/fills/account JSONL，或把 `passorder` 的正常 API 返回解释为委托成功。

## 0. 一次性部署验收

1. 主策略的一手 SELL 已取得真实 QMT 委托号、可信终态及一致的账户快照；随后主策略
   已设为 `PAUSED`。
2. Windows 中主策略与探针是两个分别编译、分别启动/停止的策略实例。探针本地副本的
   设置逐项为：

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

3. 由于共享账户在测试期间并非空仓，每个测试日开始前，以 QMT UI 当时显示的可用资金
   更新本地 `REAL_EXPECTED_INITIAL_CASH`，保留
   `REAL_INITIAL_CASH_TOLERANCE = 100.0`，并设置
   `REAL_REQUIRE_EMPTY_POSITIONS = False`；重新编译后再启动。不得用扩大 tolerance 代替
   当日复核。
4. QMT UI 明确绑定预期的同一个真实账户；不要只相信源码里的 `ACCOUNT_ID`。
5. 共享目录同时存在并可写：

   ```text
   D:\qmt_bridge\pr49_probe\inbox
   D:\qmt_bridge\pr49_probe\processing
   D:\qmt_bridge\pr49_probe\outbound
   D:\qmt_bridge\pr49_probe\archive
   D:\qmt_bridge\pr49_probe\state
   D:\qmt_bridge\pr49_probe\logs
   ```

6. 启动后检查当天两个持久日志，而不是 QMT 临时控制台：

   ```powershell
   Get-Content D:\qmt_bridge\pr49_probe\logs\qmt_bridge_YYYY-MM-DD.log -Tail 100
   Get-Content D:\qmt_bridge\pr49_probe\logs\qmt_events_YYYY-MM-DD.jsonl -Tail 100
   ```

   Mac 经 SMB 读取同一证据：

   ```bash
   tail -n 100 /Volumes/qmt_bridge/pr49_probe/logs/qmt_bridge_YYYY-MM-DD.log
   tail -n 100 /Volumes/qmt_bridge/pr49_probe/logs/qmt_events_YYYY-MM-DD.jsonl
   ```

   必须看到 `RUNTIME_CONFIG` 和 `TIMER_REGISTERED`。`RUNTIME_CONFIG` 中逐项核对
   profile、两个 root、脱敏账号、`qmt_price_type=49`、`max_order_quantity=100`、
   `submit_after=15:05:00`、`cancel_at=15:28:00`、`finalize_at=15:30:00`、
   `snapshot_after=15:31:00`。缺失、版本/SHA 变化或日志中断时停止。

## 1. 两日共同前置门禁

在 Mac 仓库根目录执行。以下变量每一天重新填写，不可沿用未来日期：

```bash
TRADE_DATE=YYYY-MM-DD
STOCK_CODE=600000.SH

test -d /Volumes/qmt_bridge/pr49_probe/inbox
test -w /Volumes/qmt_bridge/pr49_probe/inbox

/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/set_execution_state.py \
  --config csi1000_b6m_b2s_postclose_real --get

find /Volumes/qmt_bridge/state /Volumes/qmt_bridge/pr49_probe/state \
  -maxdepth 1 -type f \( -name "LIVE_OK_${TRADE_DATE}" \
  -o -name "PR49_LIVE_OK_${TRADE_DATE}" \) -print
```

要求主策略输出 `PAUSED`，且两个当日 marker 都不存在。两个 marker 同日并存会令两个
实例失败关闭。

发布器还要求与 `TRADE_DATE` 相同、含可信 REAL `ACCOUNT` 行的券商快照。先运行：

```bash
bash live_trading/run_probe_import.sh
```

若输出或预览提示当天 broker snapshot 缺失，立即停止，并选择当前实际运行的一个 QMT
实例作为 collector。下面示例由 probe 实例采集、证据归属于 probe preflight；也可把
collector 改为 main 配置，但 profile/root 会随 collector 一起绑定，不能跨 root 搬文件：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/request_account_snapshot.py \
  --collector-config csi1000_pr49_one_lot_probe \
  --for-config csi1000_pr49_one_lot_probe \
  --trade-date "$TRADE_DATE"

/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/request_account_snapshot.py \
  --collector-config csi1000_pr49_one_lot_probe \
  --for-config csi1000_pr49_one_lot_probe \
  --trade-date "$TRADE_DATE" --prepare

# 从 prepare 输出逐字复核并复制 request_id；publish 不再接受业务字段重建。
REQUEST_ID=snapshot_YYYYMMDD_32位小写十六进制ID
# 必须严格早于 14:45:00；到点及之后不得新发 snapshot request。
SNAPSHOT_OBSERVATION_CONFIRM=YES /opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/request_account_snapshot.py \
  --collector-config csi1000_pr49_one_lot_probe \
  --publish-request-id "$REQUEST_ID"

bash live_trading/run_probe_import.sh
```

Windows 持久日志必须按顺序出现 `SNAPSHOT_REQUEST_RECEIVED` 和
`SNAPSHOT_REQUEST_TERMINAL`，且没有 `PASSORDER_ATTEMPT`；Mac durable request 必须成为
`IMPORTED_COMPLETE`。ACCOUNT 必须恰好一行，且返回行账号与运行时 full/masked 精确匹配；
缺失、OTHER、多行、ERROR、identity/profile/root/checksum mismatch 都不能授权 BUY/SELL。
此请求不会创建或依赖任何 marker。处理或导入未完成时
`snapshot_requests/status.json` 必须保持 ERROR/blocking；Mac 导入把 COMPLETE response
归档后，等待 observer 写成 CLEAR。任何半对、tmp/intent、processing 或不完整 archive
都保留证据并阻断交易，禁止删除绕过。导入后再次执行上面的 marker `find`，仍应无输出，
然后停在 operator batch 发布之前重新复核。不得复用旧日快照，也不得手工制作 account
JSONL。

main/probe 必须指向同一主根 `state/SNAPSHOT_ORDER_ADVANCE.lock`。publish 后该 gate 应继续
存在，直到 Mac durable 状态为 `IMPORTED_COMPLETE` 且 request/response 四件套完整归档；
ERROR 或 crash 后必须仍存在。首次启用前在实际 SMB 上分别让 Mac 和 Windows/QMT 创建
gate，确认对端 `O_EXCL` 必然失败，并保存两次元数据/日志。若 gate 残留，停止两个 QMT
实例并保存证据，只有新的人工审批允许受控移入 quarantine，禁止自动或直接删除。

当天启动 QMT 后先确认 `SNAPSHOT_TIMER_REGISTERED` 的 `first_wakeup` 为 09:35:00 且
`registered=true`；不得等到 15:04:55 的交易 timer 才发观察请求。若只读 timer 缺失或
processor lock 残留，停止，不得为赶上 15:05 绕过 snapshot preflight。

## 2. 第一天：BUY 100 股

先在 QMT UI 的证券详情/交易权限页面核对候选证券明确支持盘后固定价格交易，并把包含
股票代码、查询时间和“支持/是”结果的截图保存到 probe 的持久日志目录。只有该结果为
肯定值才能传 `--eligibility-confirmed`；“客户账号支持该业务”不能代替单只证券资格。

运行时 QMT 还会读取证券详情字段；只有结构化 `SECURITY_DETAIL` 中
`after_hours_eligible=true` 才能继续。`false` 或无法识别都会在 `passorder` 前产生
`SECURITY_ELIGIBILITY_ERROR` 和 ERROR 回执，禁止绕过或换用 API return 推断资格。

完成上述人工证据后，让工具校验候选证券、当日账户证据和生命周期；预览不写 SQLite
或 SMB：

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_operator_probe.py \
  --config csi1000_pr49_one_lot_probe \
  --trade-date "$TRADE_DATE" --stock-code "$STOCK_CODE" \
  --side BUY --quantity 100 --reason operator_pr49_buy_probe \
  --eligibility-confirmed
```

人工复核输出为 LIVE/REAL、`AFTER_HOURS_CLOSE`、BUY、同一股票，且
`max_quantity=100`。再显式发布不可变批次：

```bash
LIVE_TRADING_CONFIRM=YES /opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_operator_probe.py \
  --config csi1000_pr49_one_lot_probe \
  --trade-date "$TRADE_DATE" --stock-code "$STOCK_CODE" \
  --side BUY --quantity 100 --reason operator_pr49_buy_probe \
  --eligibility-confirmed --publish
```

检查新的 signal/done 已进入 `/Volumes/qmt_bridge/pr49_probe/inbox`，并在 QMT 持久日志
再次核对策略实例、账户、源码 SHA 和 timer。

### BUY 日确认停点

到这里必须停住并向用户重新确认股票代码和交易日。用户对这一天的
`$STOCK_CODE/$TRADE_DATE` 作出新的明确确认后，才允许在 Windows 创建恰好当日的
`D:\qmt_bridge\pr49_probe\state\PR49_LIVE_OK_YYYY-MM-DD`。禁止提前创建、脚本自动
创建或创建未来日期 marker。

先把仓库中的 `New-OperatorAuthorizationMarker.ps1` 复制到
`D:\qmt_bridge\tools\New-OperatorAuthorizationMarker.ps1` 并复核哈希。只有该脚本获准
创建 marker；它与两个 Mac publisher 共用主 root 的
`state\OPERATOR_AUTHORIZATION.lock`。首次启用前必须完成 README_QMT 的双向 SMB 锁
互操作验收。

```powershell
$TradeDate = "YYYY-MM-DD"
& "D:\qmt_bridge\tools\New-OperatorAuthorizationMarker.ps1" `
  -Profile AFTER_HOURS_FIXED_PRICE -TradeDate $TradeDate
```

必须看到 `AUTHORIZATION_COMMITTED` 且 exit 0。任何
`AUTHORIZATION_COMMITTED_WARNING` 仍表示 marker 已不可逆提交，禁止重试或删除；
`AUTHORIZATION_NOT_COMMITTED`/exit 1 只表示脚本仍持有共享授权锁时确认最终 marker
不存在并写出状态，随后才释放锁；应停止流程并检查遗留 intent/monitor CRIT，禁止绕过
脚本。未持锁时读到不存在必须按 unknown 处理。若出现 `AUTHORIZATION_STATE_UNKNOWN`/
exit 2 和 `STOP_BOTH_QMT_NO_RETRY`，最终 marker 不可读、QMT 可能已获得授权：立即停止
probe 和主策略，不重试、不删除 marker、不清理 intent，直至人工明确最终状态。

15:05 后持续查看事件。API 返回不等于委托受理；只有 ORDER 查询或可信 callback 出现
真实 QMT 委托号的 `ORDER_OBSERVED`，随后才允许有 `ACCEPTED`。15:31 后执行：

```bash
bash live_trading/run_probe_import.sh
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_monitor.py \
  --config csi1000_b6m_b2s_postclose_real \
  --stage postmarket --date "$TRADE_DATE"
```

BUY 日只有同时满足下列证据才能进入第二天：实际成交恰好 100 股；账本生命周期为
`BUY_FILLED`；券商快照显示同一股票持有且可用数量正确；无拒单、现金/持仓漂移、
`ORDER_NOT_OBSERVED` 终态或监控 CRIT。

## 3. 下一交易日：SELL 同一 100 股

重新设置当天 `TRADE_DATE`，保持 `STOCK_CODE` 与 BUY 完全一致，并重新完成“共同前置
门禁”。SELL 日必须是 Qlib 日历中 BUY 之后的交易日；卖单来自已经导入的实际成交，
不能从 BUY 计划数量推断。

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_operator_probe.py \
  --config csi1000_pr49_one_lot_probe \
  --trade-date "$TRADE_DATE" --stock-code "$STOCK_CODE" \
  --side SELL --quantity 100 --reason operator_pr49_sell_probe

LIVE_TRADING_CONFIRM=YES /opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_operator_probe.py \
  --config csi1000_pr49_one_lot_probe \
  --trade-date "$TRADE_DATE" --stock-code "$STOCK_CODE" \
  --side SELL --quantity 100 --reason operator_pr49_sell_probe --publish
```

### SELL 日确认停点

发布后仍必须停住并向用户重新确认股票代码和交易日。只有获得针对 SELL 当天的新确认，
复核 QMT UI 可用数量至少 100 股、持久日志版本和账户都正确后，才能创建当天的
`PR49_LIVE_OK_YYYY-MM-DD`。BUY 日的确认不能授权 SELL 日。

```powershell
# SELL 日必须重新赋值、重新确认，不能沿用 BUY 日会话
$TradeDate = "YYYY-MM-DD"
& "D:\qmt_bridge\tools\New-OperatorAuthorizationMarker.ps1" `
  -Profile AFTER_HOURS_FIXED_PRICE -TradeDate $TradeDate
```

必须按 BUY 日相同的 committed/not-committed/unknown 状态契约处理；最终 marker 存在时
永远按已授权处理，不能因为 readback、Unlock 或 Dispose 警告而重试或删除；unknown 必须
停止两个策略且不得重试。

15:31 后重复 import 与 postmarket 命令。完成标准是实际 SELL 恰好 100 股、生命周期
`CLOSED`、探针证券持仓归零、费用/现金/账户快照一致。只有生命周期 `CLOSED` 才表示
完整闭环；委托号和终态还必须能在 QMT UI、
JSONL 事件与导入账本中互相对应。

## 4. 停止与恢复

- 最终 marker 是不可逆授权事实，任何阶段都禁止用删除 marker 声称回滚；立即停止 probe 策略
  和主策略，并继续核对是否已有 `PASSORDER_ATTEMPT`、委托或成交。
- 遗留 intent 不等于最终 marker，但会阻断 publisher 并触发 monitor CRIT。只有两个 QMT
  策略都已停止、final marker 明确不存在、没有任何 committed token，并核对 intent 内容
  后，才允许把 intent 隔离到证据目录；禁止直接删除证据或把 intent 改名成 marker。
- `AUTHORIZATION_STATE_UNKNOWN` 表示无法证明 final marker 不存在、QMT 可能已授权；按
  `STOP_BOTH_QMT_NO_RETRY` 停止两个策略，在明确 final 状态前禁止重试或清理任何授权证据。
- 回滚必须保留 processing/、outbound/ 和 logs/ 证据，也保留 archive/、state 中的
  active/corrupt/snapshot 证据与共享 SQLite 账本。
- 不删除 fills、账户快照或已发布 signal，不重写 batch ID，不清零手续费/盈亏。
- QMT 重启、版本/SHA 变化、日志写入恢复事件、双授权、数量超过 100、缺失真实委托号、
  快照缺失或任何漂移，均停止闭环并保持主策略 `PAUSED`。
