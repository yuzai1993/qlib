# QMT 剪贴板最小交易器

2026-10-30新版Top5（`CLOSE_AUCTION_TOPK_CASH_V1`）请使用[新版完整部署步骤](README_TOPK_CLIPBOARD.md)。下文旧阶梯策略示例的默认名称不适用于新版；新版必须同时更新QMT源码、relay和automation，安装时显式指定策略。

正常日流程自动运行：Mac 定时发布信号文件 → Windows 中转定时读取 → 剪贴板 → QMT 查询/下单 → 剪贴板 → Windows 保存日志、成交和账户文件 → Mac 原有盘后调度导入。Windows 只需普通 Python，无第三方依赖或 Qlib；QMT 完全不读写文件。

自动模式复用现有 protocol-v2 文件，Windows 自动发现 session、取行情、计算限价与股数。QMT 继续只接受明确股数的限价单，收到即提交。没有同名抵销、自动撤单、改价补单。原有 `qmt_signal_bridge.py` 保留；切换时先停止旧交易策略，避免同时运行两套执行器。

## 1. 部署

将以下文件放在同一 Windows 目录，例如 `D:\qmt_clipboard\`：

- [qmt_clipboard.py](qmt_clipboard.py)
- [clipboard_relay.py](clipboard_relay.py)
- [clipboard_auto.py](clipboard_auto.py)
- [install_clipboard_task.ps1](install_clipboard_task.ps1)

在 QMT 新建内置 Python 策略，把 `qmt_clipboard.py` **整份源码复制进去**，将 `ACCOUNT_ID = ""` 改成本地账号，并在 QMT UI 绑定同一账户。源码保持 ASCII / GBK，兼容内置 Python 3.6。QMT 不从磁盘导入这份脚本或配置；外部中转程序则正常从磁盘运行两个文件。

外部中转使用普通 Windows Python 3.8+。与 QMT 在同一用户的交互桌面运行，不作为 Session 0 系统服务。一次只启动一个 QMT 剪贴板策略；中转脚本通过 Windows 会话内互斥对象限制为一个进程。即使此前已部署手动 MVP，也必须用新版源码更新 QMT，旧版不支持账户/行情查询，自动模式会拒绝运行。

启动 QMT 策略后，输出 `ready session=...`，每秒处理剪贴板请求。仅启用策略不会下单；收到有效的 `trade` 请求才会提交。先运行以下只读命令验证完整读写往返：

```powershell
cd D:\qmt_clipboard
py clipboard_relay.py status --output-dir D:\qmt_clipboard\output
```

成功会打印会话编号并生成 `<session>.result.json`。2026-10-27 的 Windows 只读验收已通过：`poll`、`snapshot` 均返回 `ok=True`，自动检查为 `READY`，可读取券商现金、四笔持仓及信号股票行情；Mac 已能读取共享目录中的同一回执。本验收未提交交易，不代表实盘成交与盘后导入已验收。

## 2. 一次性启用自动日流程

共享根目录沿用 `D:\qmt_bridge`（Mac 挂载为 `/Volumes/qmt_bridge`），其中应有 Mac 发布的 `inbox/signal_*.jsonl` 与 `.done`。不要将它误填成脚本目录。

先做只读检查；即使在交易时间执行也不会下单：

```powershell
cd D:\qmt_clipboard
py clipboard_relay.py auto --bridge-root D:\qmt_bridge --check --timeout 30
```

有当天信号时应输出 `status=READY`、batch_id、session、可用资金、持仓和行情。无当天文件返回 `NO_SIGNAL`，不代表已经验证 QMT 查询；应核对日期和共享目录。检查不要求行情在非交易时段持续刷新，实际发单时会校验行情时间。

如果检查超时，旧版会误显示 `trade outcome unknown`；`--check` 发送的 `poll` / `snapshot` 均不提交订单。诊断版已区分只读超时与发单超时，可用以下命令延长只读等待时间并查看阶段：

```powershell
py clipboard_relay.py auto --bridge-root D:\qmt_bridge --check --timeout 30
```

- `[check] waiting for poll`：正在等待 QMT 会话响应。
- 已收到 `poll`、停在 `waiting for snapshot`：剪贴板基础往返成功，正在等待账户/持仓/行情查询。
- `published=no`：请求未成功写入剪贴板；查看同一行的 Windows 错误信息。
- `published=yes, last_seen=pending request`：请求已经放入剪贴板，但最后一次读取时仍未看到响应；不能单凭此判断 QMT 是未轮询还是处理卡住。
- QMT 新源码启动应显示 `capabilities=snapshot-v1 diagnostics=1`，首次回调显示 `clipboard polling active`，首次收到各类请求显示 `received request=...`。只覆盖磁盘文件不会更新 QMT 编辑器内的策略源码。处理异常会直接显示在 QMT 控制台，相同连续异常只打印一次。

只读检查可重复执行；若此前有真实交易请求，保留 `output/state` 和原订单编号，不能通过删除状态或生成新编号处理发单超时。

注册工作日 14:54 启动的计划任务（Windows 时区必须为中国标准时间）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_clipboard_task.ps1 -BridgeRoot D:\qmt_bridge
```

注册使用当前登录用户、Interactive 登录类型，不保存密码，不要求管理员权限。任务显式使用 `--timeout 30`，与本次通过的只读检查一致；这不意味着原超时原因已证实是等待时间不足。重复注册会更新同名任务，不会创建第二份任务。注册操作不立即启动交易。任务计划程序的“仅当用户登录时运行”是剪贴板共享的必要条件；Windows 用户和 QMT 账户保持登录、QMT 新策略保持运行，机器在交易时段保持可用。Mac 仍使用现有工作日 20:00 LaunchAgent，无需新增定时任务。

若安装时已过 14:54，可在终端启动当天中转（**这是自动交易命令**，有效信号到时间会下单；每个时段过期则跳过）：

```powershell
py clipboard_relay.py auto --bridge-root D:\qmt_bridge --timeout 30
```

此命令持续运行到北京时间 17:01。正常运行后不用每日再次操作；任务也可用 `Start-ScheduledTask -TaskName QlibClipboardRelay` 启动。一次只保留一个中转进程。

每日行为：

- 14:54 启动，每秒查看指定策略、当天的唯一信号批次，完整核对 `.done` checksum；不会补发过去日期。
- 14:55–14:57:05 卖出；14:57:05–14:59 买入。实际发送可能晚一个轮询/通信周期。买入前重新查询券商可用资金，卖单状态不明时不买；已接受但未成交的卖单不预支回款，按实际现金缩量。
- 限价沿用原 0.2% 偏移及分档取整，夹到可用涨跌停；行情须是当日且不旧于 180 秒。目标金额按现价定量后，再按限价预算上限、可用资金和手续费缩量，避免超预算导致 Mac 拒绝导入。单批次内预留资金，买入按整百股，科创板最低 200 股；卖出检查实际可卖数量及零股清仓。
- 手续费预留按现有账户配置：佣金 0.0000854、最低 5 元、过户费 0.00001（定量时所有股票保守预留）。实际记账仍由 Mac 的费用配置计算。
- 中转每次收到响应都保存本地结果/日志，并同步到共享目录 `logs/clipboard/`。整批均确认终态后发布 `outbound/fills_<batch>.jsonl/.done`；未成交/过期也有明确回执。17:00 发布 `outbound/account_<batch>.jsonl/.done`，17:01 退出。
- Mac 20:00 原调度自动导入这些旧格式回执和账户快照、更新持仓，再产生下一交易日信号。活跃部分成交或 UNKNOWN 不会伪造成终态；未确认完毕时中转返回非零。

当前自动模式支持现有全 A 池的沪深 A 股及 `CLOSE_AUCTION_LIMIT`，不包含北交所、盘后固定价格和多批次优选。一天同策略存在多个 `.done` 时拒绝执行，需核对重复发布。

本地 `D:\qmt_clipboard\output\state\` 保存先于发单写入的不可变请求。中转重启会沿用原 ID 和原内容；首次交易前 session 可以重新发现，一旦已准备交易请求，QMT session 变化就停止，不自动补单。前一天仍有未导出成交的 journal 时也停止。**不要通过删除 state 或换订单编号处理异常。** 全部委托已终态的 QMT 会话在新交易日自动轮换，无需每日手动重启。

检查运行状态：本地 `output/automation.status.json`，以及 Mac 可读的 `/Volumes/qmt_bridge/logs/clipboard/<日期>_automation.status.json`。`COMPLETE` 表示成交回执和账户快照已导出，不保证所有委托成交；逐笔看 `FILLED/SKIPPED/EXPIRED/REJECTED`。

兼容细节：旧账本要求未提交 BUY 的 `requested_qty` 也为正数，因此此类回执沿用旧 bridge 的 `100` 占位约定，同时 `filled_qty=0`、message 明示未提交；不是实际发出 100 股委托。

## 3. 手动指令（可选诊断，正常日流程不用）

复制 [clipboard_order.example.json](clipboard_order.example.json) 为 `orders.json`。示例故意使用过期时间和占位 session，不能直接成交。

- `protocol` 固定为 `qlib-clipboard-v1`，`type` 固定为 `trade`。
- `session` 原样填写 `status` 返回的当前 QMT 会话编号。
- `request_id` 是本次请求编号。超时重试必须保留原编号和订单内容。
- `expires_at` 是**北京时间当天**的失效时间，格式 `YYYY-MM-DDTHH:MM:SS+08:00`。收到即提交，失效时间不表示预约时间。
- `orders` 是 1–40 笔订单。每笔只允许 `id`、`symbol`、`side`、`quantity`、`price` 五个字段。
- `id`、`request_id` 使用 1–64 位 ASCII 字母、数字、下划线或连字符。
- `symbol` 为六位代码加 `.SH` / `.SZ` / `.BJ`；`side` 为 `BUY` 或 `SELL`。
- `quantity` 是实际股数，必须为正整数；`price` 是实际限价，必须为正有限数。单位、板块最小数量、价格档位、涨跌停及可用资金由发单方准备，券商执行最终校验。本版不会修正、缩量或重报。

发送（会实际调用 QMT 下单接口）：

```powershell
python clipboard_relay.py send D:\qmt_clipboard\orders.json --output-dir D:\qmt_clipboard\output
```

也可以让 Mac 写 UTF-8 JSON 到现有 Windows 共享目录，然后用同一命令读取该路径。`send` 不监听目录也不等待交易时间；自动日流程请使用上述 `auto`，不要混用手写订单。

同一批订单按输入顺序尝试提交，**不会等待前一笔成交**。需要先卖出回款再买时，先发送卖单，观察卖出结果后再发送买单。默认提交后持续查询最多 300 秒；`--watch-seconds 0` 表示拿到首次响应就退出。

## 4. 查看结果

重新查询一次：

```powershell
python clipboard_relay.py status --output-dir D:\qmt_clipboard\output
```

持续查询已有订单，不提交新单（将会话编号替换为实际值）：

```powershell
python clipboard_relay.py watch --session CURRENT_SESSION --watch-seconds 300 --output-dir D:\qmt_clipboard\output
```

一次只有一个中转进程，`send` 正在跟踪时不能另开 `status`；可以直接查看输出文件。

每次响应保存：

- `<session>.result.json`：当前会话的完整订单快照、请求错误和券商查询错误，原子替换。
- `<session>.log.jsonl`：增量日志，按递增 `seq` 去重。QMT 内存只保留最近 100 条，长时间未查询可能漏掉更早的日志。

每笔结果含原始指令、`status`、`filled_quantity`（累计成交股数）、`average_price`（成交均价）、`broker_orders`（真实委托号及券商状态）、`remark` 和 `error`。`query_error` 非空说明本次券商查询失败，订单字段保留上次观察值。

状态含义：

- `SUBMITTED`：已尝试提交，等待券商证据，不能据此认为成交。
- `ACCEPTED` / `PARTIAL`：已观察委托 / 部分成交，仍待终态。
- `FILLED`：累计成交达到请求数量。
- `CANCELED`：已知委托确认结束，可能已有部分成交，务必读取成交数量。
- `REJECTED`：券商确认废单。
- `EXPIRED`：批次处理中该笔提交前已到失效时间，未提交。
- `UNKNOWN`：提交异常或证据矛盾，需要继续查询或人工核对，不能自动补单。

退出码：`0` 表示本次命令完成（不保证所有订单成交；废单/撤单也可为终态），`1` 表示通信/参数/请求错误，`2` 表示查询期限结束仍有未确认终态的订单，`130` 表示 Ctrl+C 停止。到期或退出都不会自动撤单。

## 5. 去重与重启边界

QMT 下单前在内存登记订单 ID。相同 ID、相同内容只返回已有结果；改变内容则整批拒绝。请求内所有订单先校验再提交，避免最后一笔格式错误时前面的订单已经下单。提交接口抛异常也不自动再次提交。

重试使用**原文件**；不要为了消除超时而换订单 ID。QMT 重启生成新 session，旧文件会被拒绝，中转不会自动绑定新会话。重启前后的所有未确认委托和成交需要在券商界面人工核对；只有核对后才能决定新交易。内存没有重启恢复能力。

停止中转或 QMT 策略不会撤销已送到券商的委托。QMT 崩溃可能丢失未导出的日志和结果；外部已经保存的文件仍在，但不会恢复到新 QMT 会话。

## 6. 通信限制

使用普通 Unicode 文本剪贴板。`trade` / `poll` 请求与 `response` 一问一答；读取后释放锁，写响应前在剪贴板锁内比较原文本，防止覆盖后来内容。普通复制内容被忽略，中转超时只重发原请求。

交易期间建议关闭远程桌面剪贴板重定向、剪贴板历史/同步，减少覆盖。运行环境按可信、专用桌面使用；协议标识和 session 是识别及防陈旧机制，不是密码或身份认证。不要通过剪贴板传账户密码。

本会话最多接收 200 个不同订单。自动模式的已结算会话次日自动轮换；同日达到上限需核对，不自动重置。大批量分包、自动补偿和可靠日志队列均不在此版本范围。

输入文件限 256 KiB，响应文本限 2 MiB UTF-16。中文直接编码传输，避免转义膨胀；超大结果返回明确的尺寸错误而不冒充成功，需要在 QMT 核对。正常 200 笔订单、多子单及中文错误信息的完整快照有模拟测试覆盖。

Win32 实现参考：[剪贴板读写](https://learn.microsoft.com/en-us/windows/win32/dataxchg/using-the-clipboard)、[写入所有权](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setclipboarddata)、[窗口站与会话](https://learn.microsoft.com/en-us/windows/win32/winstation/window-stations)。

## 7. 本地模拟测试

仓库内运行，不连接券商：

```text
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/test_qmt_clipboard.py tests/live_trading/test_clipboard_relay.py tests/live_trading/test_clipboard_auto.py -q
```

涵盖订单去重、响应覆盖、旧会话、异常提交、部分成交、查询回退、整批校验、过期、日志输出以及 Win32 适配层模拟。模拟通过不等于 Windows/QMT 实机验收。

自动流程测试还覆盖真实 SignalPublisher → 内存剪贴板 + QMT Engine → 真实 FillImporter/LiveRecorder，验证持仓、现金及重复导入；包括资金不足/过期 BUY、限价预算约束和中转重启。QMT 查询字段参考[迅投内置 Python 数据结构](https://dict.thinktrader.net/innerApi/data_structure.html)及[合约信息](https://knowledge.thinktrader.net/docs/cloud/data_function.html)。
