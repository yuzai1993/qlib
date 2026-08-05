# CSI1000 B6-M 盘后实盘交易

当前活动系统是 `csi1000_b6m_b2s_postclose_real`。它使用冻结的 B6-M seed 4000、CSI1000、Top30/Drop2/Hold20，以及每天 Top2 的渐进建仓；QMT 仅在盘后定价时段使用 `prType=49`。

Windows QMT、SMB 桥接和 Server酱通知已完成基础连接验收。2026-08-06 从资金账号 `8890116049` 的一手实盘开始；`LIVE_OK` 和后续晋级仍是显式步骤。旧模拟盘配置仅作历史材料，不应再调度。

## 固定契约

| 项目 | 值 |
|---|---|
| 活动配置 | `live_trading/configs/csi1000_b6m_b2s_postclose_real.yaml` |
| 对照配置 | `backtest/configs/csi1000_b6m_b2s_postclose_parity.yaml` |
| 股票池 / benchmark | CSI1000 / `SH000852` |
| 账户口径 | 资金账号 `8890116049`；可用资金/总资产 1,000,000 元；价值调整 0；普通股票空仓 |
| 策略 | Top30 / Drop2 / initial Top2 / Hold20 / risk 0.95 |
| 研究策略基线 | `qlib_exp/backtest/configs/strategy-stability/b6-m/topk-t30-d2-h20_csi1000_full.yaml` |
| 模型 | B6-M seed 4000，SHA-256 `368a503c...e6325` |
| 账本 | `live_trading/data/csi1000_b6m_b2s_postclose_real.db` |
| 账户环境 | `REAL`，`allow_real_money: true`，每单最多 100 股 |
| 执行时间 | 15:05 卖出，固定等待 4 分钟后买入，15:28 撤单，15:30 终结，15:31 快照 |

欠仓阶段在实际持仓达到 30 只前不卖出，每天最多买入两只未持仓股票。每只买入目标毛市值始终是 `当前总资产 × 0.95 / 30`，不会把剩余现金平均分给当天候选。达到 30 只后的下一交易日才进入 Drop2。

## 文件与数据流

```text
T 日 16:00 导入/对账 → 更新收盘数据/名称 → 日报
             → 生成预测 → 发布 T+1 protocol-v2 批次 → 完整性检查
                                  │
                                  ▼
T+1 日 Windows QMT 15:05–15:31 → prType=49 → fills/account 回执
```

BUY 计划只携带 `target_value`，计划股数为 0。QMT 在卖出后固定等待四分钟，再读取实际现金和官方收盘价，向下取整为整手并预留费用。SELL 计划携带账本股数。批次最多接受 40 单；即使当天没有订单，也会发布 header-only 批次并得到空回执文件；`planned=0, terminal=0, missing=0` 是正常终态，不是漏跑。

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
export QMT_REAL_ACCOUNT_ID='实盘资金账号'
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

- 确认 QMT 策略绑定资金账号 `8890116049`；
- 保持 `qmt_signal_bridge.py` 的 `MAX_ORDER_QUANTITY = 100`；
- 设置 `LIVE_RUN_MODE=LIVE` 和 `LIVE_TRADING_CONFIRM=YES`；
- 每个交易日人工创建当日 `LIVE_OK_YYYY-MM-DD`；
- 逐单核对 QMT 委托类型 49、官方收盘价、成交回报、账本现金与持仓。

删除 `LIVE_OK` 只会阻止尚未提交的新单；已提交订单仍会查询、撤单和终结，避免在途订单失管。
当天是否执行会冻结在 15:05 的首次交易唤醒：若当时缺少 `LIVE_OK`，盘中补建也不会让后半批订单突然转为执行，必须等下一交易日重新授权。

### 3. 实盘晋级

一手阶段通过后才可单独评审解除初始空仓/现金预检和数量上限。账户匹配、REAL 门禁、双开关和每日 `LIVE_OK` 始终保留；系统不会自动晋级。

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
- 15:28 后不再提交新单；15:30 将未完成订单写为终态；
- 回滚：停用新 cron、停止 QMT strategy ID、保留 SQLite 与 bridge archive。不要自动恢复旧 CSI300 调度。

发生账本/券商持仓或现金差异时，先停止下一日 LIVE 发布并按 QMT 委托、成交和账户快照核对。当前实盘账本价值调整固定为 0；任何人工账本修正都应使用有审计记录的管理入口。
