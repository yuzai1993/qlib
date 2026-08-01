# CSI1000 B6-M 盘后模拟交易

当前活动候选系统是 `csi1000_b6m_b2s_postclose`。它使用冻结的 B6-M seed 4000、CSI1000、Top30/Drop2/Hold20，以及每天 Top2 的渐进建仓；QMT 仅在盘后定价时段使用 `prType=49`。

本仓库只完成了代码、配置、测试和部署模板。以下外部动作尚未执行：创建/绑定新 QMT 模拟账户、安装 Windows 策略、挂载 SMB、安装 crontab、创建 `LIVE_OK`、晋级全额模拟盘。旧 `csi300_topk10_live.yaml` 仅作历史材料，不应再调度。

## 固定契约

| 项目 | 值 |
|---|---|
| 活动配置 | `live_trading/configs/csi1000_b6m_b2s_postclose.yaml` |
| 对照配置 | `backtest/configs/csi1000_b6m_b2s_postclose_parity.yaml` |
| 股票池 / benchmark | CSI1000 / `SH000852` |
| 初始资金 | 500,000 元，空仓 |
| 策略 | Top30 / Drop2 / initial Top2 / Hold20 / risk 0.95 |
| 模型 | B6-M seed 4000，SHA-256 `368a503c...e6325` |
| 账本 | `live_trading/data/csi1000_b6m_b2s_postclose.db` |
| 账户环境 | `SIMULATION`，`allow_real_money: false` |
| 执行时间 | 15:05 卖出，固定等待 4 分钟后买入，15:28 撤单，15:30 终结，15:31 快照 |

欠仓阶段在实际持仓达到 30 只前不卖出，每天最多买入两只未持仓股票。每只买入目标毛市值始终是 `当前总资产 × 0.95 / 30`，不会把剩余现金平均分给当天候选。达到 30 只后的下一交易日才进入 Drop2。

## 文件与数据流

```text
T 日收盘数据 → 21:00 生成 T 日预测 → 发布 T+1 protocol-v2 批次
                                           │
                                           ▼
Windows QMT timer 15:05–15:31 → prType=49 → fills/account 回执
                                           │
                                           ▼
Mac 15:32 导入 → 15:35 对账、快照、日报
```

BUY 计划只携带 `target_value`，计划股数为 0。QMT 在卖出后固定等待四分钟，再读取实际现金和官方收盘价，向下取整为整手并预留费用。SELL 计划携带账本股数。批次最多接受 40 单；即使当天没有订单，也会发布 header-only 批次并得到空回执文件；`planned=0, terminal=0, missing=0` 是正常终态，不是漏跑。

## 本地只读检查

```bash
/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/check_backtest_parity.py \
  --config csi1000_b6m_b2s_postclose

openssl dgst -sha256 \
  live_trading/models/b6_m/seed4000/trained_model

/opt/anaconda3/envs/qlib/bin/python \
  live_trading/scripts/run_publish_signals.py \
  --config csi1000_b6m_b2s_postclose \
  --trade-date YYYY-MM-DD --mode SIMULATE --dry-run
```

正式发布默认拒绝 stale signal，并在写共享目录前通过模型 SHA 和 Live/Backtest parity 门禁。

## 外部环境

密钥与账户放在 `~/.qlib_live_env`，不要提交：

```bash
export TUSHARE_TOKEN='...'
export QMT_SIM_ACCOUNT_ID='新模拟账户ID'
export LIVE_CONFIG_ID='csi1000_b6m_b2s_postclose'
export LIVE_RUN_MODE='SIMULATE'
```

`QMT_ACCOUNT_ID` 不会被新发布器读取。配置和协议都只接受 `account_environment=SIMULATION`；QMT 查询返回的账户 ID 也必须与请求一致。真实账户没有环境变量捷径，需要另行设计和明确授权。

Windows 安装步骤见 [QMT 部署说明](qmt_strategy/README_QMT.md)。Mac 的 `live.bridge_root` 必须指向已挂载的共享目录，发布前至少确认：

```bash
test -d /Volumes/qmt_bridge/inbox
test -w /Volumes/qmt_bridge/inbox
```

## 受控晋级

### 1. Shadow（当前默认）

- `LIVE_RUN_MODE=SIMULATE`；
- 不创建 `state/LIVE_OK_YYYY-MM-DD`；
- QMT 只生成 `SKIPPED simulated` 回执，不访问交易账户；
- 连续核对 signal date、Top2、单槽金额、空批次、Hold20 和文件 SHA。

### 2. 一手模拟盘

只有 Shadow 验收后人工执行：

- 确认 QMT 策略绑定的是新模拟账户；
- 保持 `qmt_signal_bridge.py` 的 `MAX_ORDER_QUANTITY = 100`；
- 设置 `LIVE_RUN_MODE=LIVE` 和 `LIVE_TRADING_CONFIRM=YES`；
- 每个交易日人工创建当日 `LIVE_OK_YYYY-MM-DD`；
- 逐单核对 QMT 委托类型 49、官方收盘价、成交回报、账本现金与持仓。

删除 `LIVE_OK` 只会阻止尚未提交的新单；已提交订单仍会查询、撤单和终结，避免在途订单失管。
当天是否执行会冻结在 15:05 的首次交易唤醒：若当时缺少 `LIVE_OK`，盘中补建也不会让后半批订单突然转为执行，必须等下一交易日重新授权。

### 3. 全额模拟盘

一手阶段通过后，人工把 QMT 本地副本的 `MAX_ORDER_QUANTITY` 改为 `0`，才解除一手上限。其余模拟账户门禁和每日 `LIVE_OK` 均保留。系统不会自动晋级。

## 调度模板（未安装）

[crontab.csi1000_postclose.example](crontab.csi1000_postclose.example) 是唯一的新调度模板：

- 15:32：导入回执后运行 `postmarket`；
- 15:35：在日线数据已更新的前提下运行 `report`；
- 21:00：发布下一交易日；
- 22:05：只在缺少持久化批次时 catch-up；
- 22:15：运行 `evening` 发布完整性检查。

所有 wrapper 都支持位置参数 config ID，也支持 `LIVE_CONFIG_ID` / `QLIB_LIVE_CONFIG_ID`，默认新 CSI1000 配置。它们使用原子目录锁防止同类任务并发；残留 `.locks/<config>_*.lock` 时应先确认没有任务运行，再人工删除。监控 WARN/CRIT 的退出码会原样返回，不再被 `|| true` 吞掉。

不要在本仓库内自动执行 `crontab` 修改。安装前先检查现有任务，确保旧 CSI300 条目已停用且没有同一时间的重复任务。

## 日常命令

```bash
# Shadow 发布下一开市日
bash live_trading/run_publish_cron.sh csi1000_b6m_b2s_postclose

# 只在该交易日没有活动批次时补发
bash live_trading/run_publish_catchup_cron.sh csi1000_b6m_b2s_postclose

# 导入并打印 planned/terminal/missing
bash live_trading/run_import_cron.sh csi1000_b6m_b2s_postclose

# 分阶段监控
bash live_trading/run_monitor_cron.sh postmarket csi1000_b6m_b2s_postclose
bash live_trading/run_monitor_cron.sh report csi1000_b6m_b2s_postclose
bash live_trading/run_monitor_cron.sh evening csi1000_b6m_b2s_postclose
```

## 失败关闭与恢复

- parity、模型 SHA、目标信号日、模拟账户环境、共享目录或批次内容冲突任一失败：不发布；
- 同 batch 的字节完全相同：幂等成功；不同：拒绝覆盖；
- 先前 LIVE 批次未取得终态回执：拒绝发布后续 LIVE 批次；
- QMT 重启：从 `processing/` 和 `state/active_*.json` 恢复，已标记提交的订单不重提；
- 活动状态损坏：保留 `.corrupt_*` 证据并把整批视为可能已提交，只查询/撤单/终结，绝不重提；
- 畸形或超限批次：移入 archive；已有同名归档不覆盖，使用 `.repeat_*` 保留两份；
- 15:28 后不再提交新单；15:30 将未完成订单写为终态；
- 回滚：停用新 cron、停止 QMT strategy ID、保留 SQLite 与 bridge archive。不要自动恢复旧 CSI300 调度。

发生账本/券商持仓差异时，先停止下一日 LIVE 发布并按 QMT 委托、成交和账户快照核对。任何人工账本修正都应使用现有 cash-flow/position 管理入口留下审计记录。
