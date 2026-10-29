# 10/30 新版 Top5 剪贴板部署

本说明用于 `alla_v2_top5d1f100_postclose_real`：v2 五种子模型，Topk5/drop1/hold1/跌出前100强卖，risk=1.0。QMT 与 relay 必须一起更新。新版 QMT 的 `source_version` 为 `2026-10-30-topk-cash-clipboard-v1`，能力包含 `snapshot-v1,topk-cash-v1`。

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

Mac本次只执行一次发布，原LaunchAgent保持停用。成交后可人工导入新版账本：

```sh
bash live_trading/run_import_cron.sh alla_v2_top5d1f100_postclose_real_active
```

实际共享配置为 `live_trading/configs/alla_v2_top5d1f100_postclose_real_active.yaml`，桥指向 `/Volumes/qmt_bridge`，与离线准备配置使用同一新策略账本及策略ID。以后人工调用wrapper时显式传这个配置ID，并以实际日期核对导入范围。本次一次性发布不启用自动调度；不要启动仍指向旧策略的默认wrapper。

该配置的收益统计从新策略交接余额244607元开始，首份快照日为2026-10-30；基准起点为旧账本2026-10-29快照保存的中证全指收盘5070.46923828125。原始资本30万元和旧策略历史保留，不把此前损益当作新策略首日损益。

停止relay或QMT不会撤销已提交到券商的委托；出现UNKNOWN、会话变化或未导出前日journal时，保留状态并核对券商，不能删除state或换编号重放。
