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

## 3. 定时与状态机

策略在 `init` 中绑定账户回调并注册 `ContextInfo.schedule_run`，从 14:56:55 起每 3 秒唤醒。旧 QMT 没有新版接口时退回 `run_time`。

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

主策略 SELL 的 preview→人工发布→导入→postmarket→`PAUSED` 命令见上级
[实盘 README](../README.md)。prType=49 BUY→下一交易日 SELL 的逐项门禁见
[PR49_PROBE_CHECKLIST.md](PR49_PROBE_CHECKLIST.md)。两条流程都要求发布前存在同交易日、
与真实账户可靠绑定的 broker snapshot；若 snapshot-only 受控入口尚未提供，必须停止，
禁止手工编辑 account JSONL。

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
| Mac 报持仓漂移 | 对照 QMT 委托/成交、`account_*.jsonl` 和本地 fills，先停下一日 LIVE |

共享目录每天持久化 `qmt_bridge_YYYY-MM-DD.log` 和 `qmt_events_YYYY-MM-DD.jsonl`，客户端重启不会清除。FormulaOutput 仅作为辅助日志。
