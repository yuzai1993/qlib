# 10/30 新版 Top5 剪贴板部署

本说明用于 `alla_v2_top5d1f100_postclose_real`：v2 五种子模型，Topk5/drop1/hold1/跌出前100强卖，risk=1.0。QMT 与 relay 必须一起更新。新版 QMT 的 `source_version` 为 `2026-10-30-topk-cash-clipboard-v1`，能力包含 `snapshot-v1,topk-cash-v1`。

2026-11-09 外部 relay 修复版为 `2026-11-09-quote-retry-v1`，更新 `clipboard_auto.py` 和 `clipboard_relay.py`；本次无需修改 QMT 编辑器源码。操作者提供的计划任务 Action 已确认实际运行目录为 `D:\qmt_bridge\strategy\bt_v5_clipboard_20261030`，下文独立目录安装示例不是这台机器的当前路径。更新必须以任务的 `Actions` 为准。

### Windows 时间与报价异常

11月9日 Windows 的 `w32tm /query /status` 显示 `Free-running System Clock`、从未成功同步；NTP 实测系统慢约7.83秒。报价校验要求同日且 `-5 <= Windows时间 - 行情时间 <= 180` 秒，因此领先电脑超过5秒也会被旧版统一报为 `stale quote`。原卖出瞬间的报价未保留，精确卖出偏差不可追溯；当天保留的买入快照已重现相同校验错误。

修复版保持上述新鲜度限制。卖出窗口内，尚未提交的股票遇到无效或过期报价会重新查询，其他报价正常的卖单可继续提交；14:57:05后未提交者记为EXPIRED。已冻结的请求始终使用原订单号、数量和价格，响应丢失不创建替代订单。旧版已经写入的终态不会重新打开。买入仍等所有卖单终态，保留5只持仓上限和冻结现金预算。

`output/automation.status.json` 与共享日志副本新增 `runtime`（外部relay版本、实际路径和SHA-256）、`quote_diagnostics`（观察时间、行情时间、年龄、FRESH/FUTURE/STALE等状态）和 `quote_retry_errors`。最后一次失败证据即使随后恢复也保留，不能仅凭该历史字段判断当前仍有故障。`auto --check` 会显示同样的时间诊断，且不会下单；盘后报价停止更新而显示STALE属于正常现象。

先在**管理员 PowerShell**开启系统自动校时并同步：

```powershell
Set-Service W32Time -StartupType Automatic
Start-Service W32Time
w32tm /resync /rediscover
w32tm /query /status
w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly
```

拒绝访问（0x80070005）表示窗口未提权；在原窗口执行 `Start-Process powershell -Verb RunAs`，经系统确认后在新窗口操作。若无可用时间数据，先检查 `w32tm /query /configuration` 与源连通性，不能把命令发出视为同步成功，也不通过放宽报价阈值掩盖时钟失准。命令说明见[微软 W32Time 文档](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings)。

#### 固定源端口123不可达时的自动校时

11月9日的进一步对照中，Windows使用同一Cloudflare地址、同一种NTP请求，随机源端口两次成功，固定源端口123两次超时；此现象在独立Python查询中也重现。W32Time专用防火墙放行无效，测试规则已撤回；具体丢包设备尚未定位。W32Time只能使用源端口123，而`w32tm /stripchart`使用随机源端口，因此使用后者的可达路径实现独立校时。注意：实测`w32tm /resync`输出失败时也可能返回退出码0，不能单看退出码。

正式工具为`windows_clock_guard.ps1`，安装器为`install_clock_guard_task.ps1`，可复用测试位于`tests/live_trading/test_windows_clock_guard.ps1`（部署包内放同名文件供安装时运行）。管理员执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\qmt_bridge\strategy\bt_v5_clipboard_20261030\install_clock_guard_task.ps1"
```

安装器先运行测试，再将校时脚本复制到`C:\Program Files\QlibClockGuard`，明确设置目录与文件ACL：SYSTEM和Administrators可写，Users只读，拒绝重解析路径。`QlibClockGuard`以SYSTEM身份调用Windows自带PowerShell，每天09:00、14:40及开机两分钟后运行；失败最多间隔两分钟重试两次。任务不调用QMT或交易relay。

每次从Cloudflare、Apple、Microsoft各取两个`/rdtsc`样本，按源选择最低往返延迟样本；要求样本不超过30秒、往返延迟不超过1秒，至少两个独立源在250毫秒内一致。自动调整上限60秒；14:50～15:05内及跨入此区间的调整被拒绝。校前、校后采样均检测系统时钟是否中途跳变；校后必须重新取得多源共识且偏差不超过500毫秒，才写`SYNCHRONIZED`。默认不加`-Apply`时只检查。

结果与每次运行审计保存在`D:\qmt_bridge\logs\clock_sync`。安装器必须确认新的`run_id`、新的任务运行记录及退出码0，才报告本次安装验收成功：

```powershell
Get-ScheduledTaskInfo -TaskName QlibClockGuard
Get-Content D:\qmt_bridge\logs\clock_sync\clock.status.json -Raw
w32tm /stripchart /computer:time.cloudflare.com /samples:3 /dataonly
```

此方案修复并定期维护系统时钟，但没有恢复固定123端口的网络路径；W32Time自身可能仍显示`Local CMOS Clock`或未同步。应依据独立采样、`clock.status.json`与任务执行结果判断校时是否成功。不可将旧的`SYNCHRONIZED`记录当作当前状态，必须核对运行时间；原有报价新鲜度保护继续生效。

2026-11-09 23:35已在当前Windows主机安装并完成SYSTEM任务实测：Windows PowerShell 5.1下28项检查通过，时钟偏差由慢7.8502148秒降至慢0.0047421秒，校前和校后均取得上述三个时间源的共识。审计位于`live_trading/logs/clock_repair_20261109/clock_guard_deployment.json`；后续状态以每次新运行记录为准。

Mac 本次使用2026-10-29信号生成2026-10-30批次，序号002；初始可用现金244607元、空仓、无活动委托来自用户确认。买单在信号中金额和股数均为0，relay在执行时查询券商实际现金、持仓和委托后定量。不要手工把信号中的0改成预算或数量。

## 1. 停止旧程序并复制完整新包

旧QMT策略保持停止，旧 `QlibClipboardRelay` 任务和手动relay进程也停止。保留原 `output/state` 与执行记录。新旧两套不要同时运行。

Windows PowerShell：

```powershell
Disable-ScheduledTask -TaskName 'QlibClipboardRelay' -ErrorAction SilentlyContinue
Stop-ScheduledTask -TaskName 'QlibClipboardRelay' -ErrorAction SilentlyContinue
# 手动relay在原终端按Ctrl+C结束。

$target = 'D:\qmt_clipboard_top5'
New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item 'D:\qmt_bridge\strategy\bt_v5_clipboard_20261030\*' $target -Force
Set-Location $target
py --version
```

包内四个运行文件必须来自同一版：`qmt_clipboard.py`、`clipboard_relay.py`、`clipboard_auto.py`、`install_clipboard_task.ps1`。外部relay使用普通Windows Python3.8+，只依赖标准库，无需安装Qlib。`D:\qmt_bridge`是共享根，Mac对应 `/Volumes/qmt_bridge`；它不是安装目录。首次安装使用新的 `D:\qmt_clipboard_top5`，保留旧目录作审计；后续更新继续用这个新目录，保留它自己的state，不能每天换目录来重跑。

## 2. 更新QMT内置策略

在大QMT新建内置Python策略，把包内 `qmt_clipboard.py` **整份源码**复制到编辑器，只把 `ACCOUNT_ID = ""` 填成本地实际资金账号，编译并在QMT界面绑定相同账号。账号和密码不要写回共享包。此路径使用 `qmt_clipboard.py`，不加载文件桥 `qmt_signal_bridge.py`。

外部relay与QMT须在同一个Windows用户的交互桌面运行；保持该用户登录，Windows时区为“中国标准时间”。避免远程桌面剪贴板重定向/同步覆盖通信。

启动这一个新QMT策略。QMT输出应含 `ready session=...`、新版本号及 `topk-cash-v1`。仅启动QMT时尚未收到交易请求，不会下单。

## 3. 只读往返验收

先运行status；不要同时启动另一个relay：

```powershell
py .\clipboard_relay.py status --timeout 30 --output-dir .\output
```

输出应为 `no orders`，并明确包含：

```text
source_version=2026-10-30-topk-cash-clipboard-v1 capabilities=snapshot-v1,topk-cash-v1
```

再检查今天的共享信号及账户：

```powershell
py .\clipboard_relay.py auto --check --timeout 30 --bridge-root D:\qmt_bridge --strategy alla_v2_top5d1f100_postclose_real --output-dir .\output
```

10月30日应看到 `READY`、批次 `20261030_alla_v2_top5d1f100_postclose_real_002`、`max_positions:5`、`cash_risk_degree:1.0`、初始 `positions:[]`、`working_orders:[]`，并核对券商实际可用现金约244607元。现金或持仓与确认状态不一致时先核对，不能用改信号、删除journal、换订单号解决。`NO_SIGNAL` 表示尚未找到当天该策略批次；旧能力或不完整账户查询会报错停止。

`--check`不会提交交易、不会冻结买入预算；盘前验收的报价不表示收盘时可成交。实际预算、报价和可用资金在交易窗口重新核对。

## 4. 安装或手动启动relay

确认只读检查通过后，由操作者安装新任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_clipboard_task.ps1 -BridgeRoot D:\qmt_bridge -Strategy alla_v2_top5d1f100_postclose_real -TaskName QlibClipboardRelayTop5
Get-ScheduledTask -TaskName QlibClipboardRelayTop5 | Select-Object TaskName,State
```

新任务工作日14:54在当前用户桌面启动；安装不会立即执行。旧任务继续Disabled。若需要今天手动启动，选择下面一种，不要和计划任务各起一个进程：

```powershell
Start-ScheduledTask -TaskName QlibClipboardRelayTop5
# 或在本目录手动运行同一程序：
# py .\clipboard_relay.py auto --timeout 30 --bridge-root D:\qmt_bridge --strategy alla_v2_top5d1f100_postclose_real --output-dir .\output
```

启动auto后将按时间处理当天有效批次：卖单14:55后，买单14:57:05后，14:59停止新增买入请求，17:00导出账户快照，17:01退出。买入等待全部卖单终态；部分卖出剩余持仓仍占名额，其他活动委托阻止买入。新协议冻结候选和预算，失败候选的份额保留现金，重启沿用原请求。策略满仓上限5只，实际建仓可能因整手、费用或不可成交而少于5只。

## 5. 日志、回执与Mac后续导入

查看 `D:\qmt_clipboard_top5\output\automation.status.json`、该目录下的会话result/log及 `output\state`。共享副本在 `D:\qmt_bridge\logs\clipboard`，终态回执和账户快照在 `D:\qmt_bridge\outbound`。`COMPLETE`表示回执与快照导出完成，不表示全部买单成交；逐笔核对实际状态和成交量。

Mac 已于 2026-10-30 启用新版配置的工作日 20:00 调度，执行 postclose→publish→evening，包含回执导入；旧策略已停调度。此前一次性发布是切换准备步骤，不能作为当前调度状态。需要人工补导入时使用新版账本：

```sh
bash live_trading/run_import_cron.sh alla_v2_top5d1f100_postclose_real_active
```

实际共享配置为 `live_trading/configs/alla_v2_top5d1f100_postclose_real_active.yaml`，桥指向 `/Volumes/qmt_bridge`，与离线准备配置使用同一新策略账本及策略ID。人工调用 wrapper 时显式传这个配置 ID，并以实际日期核对导入范围。Mac 调度与 Windows relay/QMT 的启停分别管理；当前状态及部署证据见 `live_trading/BT_V5_CUTOVER.md`，Windows 执行器仍须现场核验。

该配置的收益统计从新策略交接余额244607元开始，首份快照日为2026-10-30；中证全指以该日收盘4952.4951171875为基准，首日净值为1、基准收益为0，后续按收盘价连续计算。2026-11-03修正了此前误用10月29日收盘价的起点，并同步修正已有快照的基准收益及日超额收益；审计见`live_trading/logs/benchmark_rebase_audit/20261103-v2.json`。原始资本30万元和旧策略历史保留，不把此前损益当作新策略首日损益。

停止relay或QMT不会撤销已提交到券商的委托；出现UNKNOWN、会话变化或未导出前日journal时，保留状态并核对券商，不能删除state或换编号重放。
