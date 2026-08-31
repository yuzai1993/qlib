# 全A v4 观察实盘（top1 × h5）

当前活动系统是 `alla_v4_ladder_k1h5_postclose_real`。模型是 v4 五种子 RankIC ES
日截面 z-score 等权，执行是盘后固定价格 `prType=49`。策略先用 **top1 × horizon=5、
仓位 100%、期初 30 万** 观察；稳定后再切回 100 万的
`alla_v4_ladder_k3h5_postclose_real`。研究基线 BT v4 仍是 k3×h5，本观察盘不改那条尺子。

**执行开关是 QMT 策略的启停，不是 `LIVE_OK_` / `PR49_LIVE_OK_`。**
Mac 只发布信号；Windows 上策略在跑、inbox 有 LIVE 批次，到点就会下单。不要为日常交易建 marker。
发布 wrapper 会主动 unset `LIVE_TRADING_CONFIRM`。

生产根是 `/Users/yuxianqi/Project/qlib`，不是 `qlib_exp` worktree。真实资金账号只通过本机
`QMT_REAL_ACCOUNT_ID` 与 QMT UI 绑定，不写入 Git。

## 固定契约

| 项目 | 值 |
|---|---|
| 活动配置 | `live_trading/configs/alla_v4_ladder_k1h5_postclose_real.yaml` |
| 对照配置 | `backtest/configs/alla_v4_ladder_k1h5_parity.yaml` |
| 股票池 / benchmark | 全A 四重过滤 / `SH000985` |
| 账户口径 | 账本期初 300,000 元、价值调整 0；真实账号只在 QMT 本地绑定 |
| 策略 | `CohortLadderStrategy` topk=1 horizon=5 risk_degree=1.0 |
| 模型 | v4 五种子日截面 z-score 等权 |
| 账本 | `live_trading/data/alla_v4_ladder_k1h5_postclose_real.db` |
| 账户 | QMT 策略源码里的 `ACCOUNT_ID`；启停策略即开关 |
| 执行 | 盘后固定价格 `prType=49`，15:00:05 起试 / 15:01 兜底 / 15:28 撤单 / 15:30 终态并拍持仓快照 |
| 授权 | 无 marker。QMT 启停即开关 |
| 后续切换 | `alla_v4_ladder_k3h5_postclose_real`（100 万、top3×h5、risk 0.90） |

这不是旧 CSI1000 的「欠仓到 30 只再卖」。每天买当日第 1 名开一层，持满 5 个交易日到期卖；
同一只可以多层叠加。每日买入预算是 `总资产 × 1.0 / 5`，再和现金取小。买单只带
`target_value`、计划股数为 0；QMT 用已确定的官方收盘价算整手（科创板盘后最低 200 股）。
同名到期卖与当日买在 bridge 提交时刻净额化。

## 未调度的配置

| 配置 | 状态 |
|---|---|
| `alla_v4_ladder_k3h5_postclose_real` | 预留切回，未装 cron |
| `csi1000_b6m_b2s_postclose_real` | 已停调度，账本冻结作历史。没有 `PAUSED` 行时 CLI `--get` 会显示默认 ACTIVE，这不是停机证明 |
| `csi300_topk10_live` | 更早的 B1 正式盘，仅留文件 |

CSI1000 一手 SELL / 快照 / marker 验收原文已迁到
[CSI1000 一手 SELL 验收](qmt_strategy/CSI1000_OPERATOR_SELL_RUNBOOK.md)，
不要把它当成现网日常命令。探针清单见
[PR49_PROBE_CHECKLIST.md](qmt_strategy/PR49_PROBE_CHECKLIST.md)。

## 文件与数据流

```text
T 日 22:30 Mac  postclose（导入 → postmarket → Tushare 日更 → 股票名 → 成功才日报）
             → 发布下一开市日 protocol-v2 批次 → evening 完整性检查
                                  │
                                  ▼
T+1 日 Windows QMT  15:00:05 起试 / 15:01 兜底 → prType=49 → 15:30 终态并拍持仓快照
```

22:30 是为了等 Tushare 收盘数据落稳；全A 冷启动建特征大约十几分钟。不要再按 16:00 理解现网。

BUY 计划只携带 `target_value`。只有查询到真实委托号才记录 `ACCEPTED`。持久日志在
`D:\qmt_bridge\logs`。

## 本地只读检查

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/check_backtest_parity.py \
  --config alla_v4_ladder_k1h5_postclose_real

openssl dgst -sha256 live_trading/models/v4_rankices/s42/trained_model
openssl dgst -sha256 live_trading/models/v4_rankices/s1000/trained_model
openssl dgst -sha256 live_trading/models/v4_rankices/s2000/trained_model
openssl dgst -sha256 live_trading/models/v4_rankices/s3000/trained_model
openssl dgst -sha256 live_trading/models/v4_rankices/s4000/trained_model

/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_publish_signals.py \
  --config alla_v4_ladder_k1h5_postclose_real \
  --trade-date YYYY-MM-DD --dry-run
```

正式发布默认拒绝 stale signal，并在写共享目录前通过五种子 SHA 和 Live/Backtest parity 门禁。
`st_daily.csv` 缺失或 `max_date` 落后于 `signal_date` 时发布失败，须先成功跑完日更。

## 外部环境

密钥与账户放在 `~/.qlib_live_env`，不要提交：

```bash
export TUSHARE_TOKEN='...'
export LIVE_CONFIG_ID='alla_v4_ladder_k1h5_postclose_real'
```

发布器不读账号环境变量，批次头也不再带 `mode` / `account_environment` / `account_id`。
QMT 用策略源码里的 `ACCOUNT_ID` 下单。`QMT_REAL_ACCOUNT_ID` 只给探针/快照工具用。

Windows 安装与生产渲染见 [QMT 部署说明](qmt_strategy/README_QMT.md)。仓库里的
`qmt_signal_bridge.py` 是保守模板（集合竞价、一手上限、不抵销）。生产形态由
`live_trading/scripts/render_qmt_runtime.py` 渲出：盘后通道、开启阶梯抵销、
`MAX_ORDER_QUANTITY=0`。不要直接拿仓库模板当生产脚本。

Mac 的 `live.bridge_root` 必须指向已挂载的共享目录，发布前至少确认：

```bash
test -d /Volumes/qmt_bridge/inbox
test -w /Volumes/qmt_bridge/inbox
```

## 调度

[crontab.csi1000_postclose.example](crontab.csi1000_postclose.example) 是唯一的调度模板：

- crontab 只维护一行，每个工作日 **22:30** 启动一次；
- 先串行运行回执导入、`postmarket`、Tushare 行情更新和股票名称缓存刷新；仅在行情更新成功后运行 `report`；
- Tushare 日更（`run_update_to_bin.sh`）在复权巡检之后会增量刷新 ST 日频名单
  `scripts/data_collector/tushare/st_daily.csv`；发布脚本按 `signal_date` 做四重宇宙过滤，
  命中 ST / 退市整理期则剔除。缓存缺失或落后于 `signal_date` 时发布失败；
- postclose 完成后立即发布下一开市日，发布完成后立即运行 `evening` 完整性检查；
- 三个阶段之间没有额外定时或等待。

调度器把每日阶段回执原子写到
`live_trading/.scheduler/<config>/<YYYY-MM-DD>/<stage>.json`。每次调用都按
postclose → publish → evening 的固定顺序补齐尚无回执的阶段；无论成功还是失败，每阶段每天都只自动尝试一次。某阶段失败会让整条流水线最终返回非零，但不会阻止后续阶段执行，失败后按告警人工恢复，不会盲目重试。

两项依赖权威收盘价的对账只能放在 `report`，因为 `postmarket` 跑在行情更新之前：
`NETTING_CLOSE_MISMATCH`（bridge 定量用价是否等于权威收盘价）与 `FILL_RATIO_*`
（买入侧加权成交率与回退触发：连续 3 日 < 80%，或任一日 < 50%）。行情更新失败会连带跳过
`report`，这两项当天就没有对账证据——必须先修好数据再手工补跑
`run_monitor_cron.sh report alla_v4_ladder_k1h5_postclose_real`。

`run_postclose_cron.sh` 即使遇到导入、postmarket 或股票名称刷新告警也会继续后续步骤；
行情更新失败时跳过日报。股票名称刷新到本地 SQLite，Web 页面只读本地数据。
该脚本在整个流水线期间持有 `.locks/<config>_postclose.lock`，并与发布任务双向锁检查；
任一方向发现并发都失败关闭。

所有 wrapper 都支持位置参数 config ID，也支持 `LIVE_CONFIG_ID` / `QLIB_LIVE_CONFIG_ID`，
默认 `alla_v4_ladder_k1h5_postclose_real`。残留 `.locks/<config>_*.lock` 时应先确认没有任务运行，再人工删除。监控 WARN/CRIT 的退出码会原样返回。系统不自动重试失败阶段或补发。

部署或迁移机器时先检查现有任务，确保旧 CSI300 / CSI1000 条目已停用且没有重复任务，再安装该单行模板：

```bash
crontab live_trading/crontab.csi1000_postclose.example
crontab -l
```

## 监控服务

只读 Web 监控由 macOS `launchd` 常驻托管，登录后自动启动，异常退出后自动拉起。服务只监听本机
`127.0.0.1:8082`，不会直接暴露给局域网。仓库模板是
`live_trading/launchd/com.yuxianqi.qlib-live-monitor.plist`，部署到
`~/Library/LaunchAgents/` 后由 `launchctl` 加载。

```bash
launchctl print gui/$(id -u)/com.yuxianqi.qlib-live-monitor
curl --noproxy 127.0.0.1 --fail http://127.0.0.1:8082/api/overview
open http://127.0.0.1:8082
```

标准输出与错误日志分别写入
`live_trading/logs/alla_v4_ladder_k1h5_postclose_real_web_service.stdout.log` 和
`live_trading/logs/alla_v4_ladder_k1h5_postclose_real_web_service.stderr.log`。
`run_web_service.sh` 会加载 `~/.qlib_live_env`，与 cron 使用同一套环境和活动配置。

## 日常命令

```bash
# 查看今天三个阶段的自动尝试状态
find live_trading/.scheduler/alla_v4_ladder_k1h5_postclose_real/$(date +%Y-%m-%d) \
  -maxdepth 1 -name '*.json' -print

# 22:30 收盘流水线（导入 → 检查 → 更新 → 日报）
bash live_trading/run_postclose_cron.sh alla_v4_ladder_k1h5_postclose_real

# 发布下一开市日
bash live_trading/run_publish_cron.sh alla_v4_ladder_k1h5_postclose_real

# 数据库没有下一交易日批次时，检查发布日志后人工补发（不要加入 crontab）
bash live_trading/run_publish_catchup_cron.sh alla_v4_ladder_k1h5_postclose_real

# 数据库已有批次但共享文件缺失时，按明确交易日幂等重发
bash live_trading/run_publish_cron.sh \
  alla_v4_ladder_k1h5_postclose_real YYYY-MM-DD

# 导入并打印 planned/terminal/missing
bash live_trading/run_import_cron.sh alla_v4_ladder_k1h5_postclose_real

# 分阶段监控
bash live_trading/run_monitor_cron.sh postmarket alla_v4_ladder_k1h5_postclose_real
bash live_trading/run_monitor_cron.sh report alla_v4_ladder_k1h5_postclose_real
bash live_trading/run_monitor_cron.sh evening alla_v4_ladder_k1h5_postclose_real

# 查看 durable 执行状态（无行时默认 ACTIVE，不是「已确认在跑」）
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/set_execution_state.py \
  --config alla_v4_ladder_k1h5_postclose_real --get
```

人工恢复前先查看
`live_trading/logs/alla_v4_ladder_k1h5_postclose_real_publish_cron.log`。
若 SMB 不可访问，先恢复挂载；若 `postclose` 锁存在，先确认 22:30 流水线是否仍在运行。
不要和正在跑的 cron 叠在一起手工补跑。

## 失败关闭与恢复

- parity、模型 SHA、目标信号日、账户环境/账号、共享目录或批次内容冲突任一失败：不发布；
- 同 batch 的字节完全相同：幂等成功；不同：拒绝覆盖；
- QMT 重启：从 `processing/` 和 `state/active_*.json` 恢复，已标记提交的订单不重提；
- 活动状态损坏：保留 `.corrupt_*` 证据并把整批视为可能已提交，只查询/撤单/终结，绝不重提；
- 畸形或超限批次：移入 archive；已有同名归档不覆盖，使用 `.repeat_*` 保留两份；
- 15:00:05 起试、15:01 之后不再为等收盘价而 defer；15:28 撤未完成单，15:30 写终态；
- 回滚：停用 cron、停止 QMT 策略、保留 SQLite 与 bridge archive。不要自动恢复旧 CSI300 / CSI1000 调度。

发生账本/券商持仓股数对不上时，先停 QMT 策略并停止下一日 LIVE 发布，再按委托、成交和账户快照核对。
日常对账只比持仓股数，先不对现金和费用；账本价值调整固定为 0。任何人工账本修正都应使用有审计记录的管理入口。
`execution_state=PAUSED` 不再挡住主发布器；要当天不下单，停 Windows 上的 QMT 策略。
