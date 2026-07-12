# Qlib → QMT 实盘信号桥：通俗说明与运维手册

> 设计文档：[`docs/superpowers/specs/2026-07-11-qmt-live-signal-bridge-design.md`](../docs/superpowers/specs/2026-07-11-qmt-live-signal-bridge-design.md)  
> QMT 侧部署：[`qmt_strategy/README_QMT.md`](qmt_strategy/README_QMT.md)

---

## 一、这套系统在干什么（3 分钟版）

一句话：**Mac 上的 qlib 每晚算出「明天该买什么、卖什么」，写成一张"购物清单"文件；Windows 上的 QMT 第二天早上照单下单，并把"小票"（成交回执）写回来。**

为什么这么绕？因为：

1. 模型和数据（qlib、LightGBM、Alpha158）都在 Mac 上，QMT 只能跑在 Windows 上；
2. 券商关闭了 MiniQMT 外部 API，程序不能直接连券商下单，只有 QMT 客户端**内部**的策略能调用下单函数 `passorder`；
3. 所以两边用一个**共享文件夹**传纸条：Mac 写信号文件进去，QMT 读出来下单，再把回执写回去。

```
【Mac，每晚】                 【共享文件夹】              【Windows + QMT，次日盘中】
 qlib 数据更新                                             
 模型打分            ──►      inbox/   信号文件    ──►      内置策略轮询读取
 生成买卖清单                                               校验 → passorder 下单
                                                            盘中盯委托状态
 导入回执、对账      ◄──      outbound/ 回执文件   ◄──      写成交回执
```

### 几个关键保险丝（为什么敢自动化）

| 保险丝 | 作用 |
|--------|------|
| 信号文件写完才写 `.done` 标记 | QMT 绝不会读到半截文件 |
| 每张清单带日期（`trade_date`） | 昨天的旧清单今天绝不执行（比如 Windows 昨天没开机） |
| 每张清单带指纹（checksum） | 文件传输损坏就整批拒绝 |
| 每笔订单有唯一编号 | 同一笔绝不重复下单，重复投递自动跳过 |
| 模拟/实盘双开关 | 文件里 `mode=LIVE` **且** Windows 上有当日 `LIVE_OK` 文件，才碰真钱 |
| 先卖后买 | 卖出资金到账后才买入，避免资金不足 |
| 14:50 强制收尾 | 没成交的单子统一撤掉、写回执，绝不留悬案 |

### 每天钱和股票怎么记账

- Mac 侧有一个本地账本（SQLite：`live_trading/data/*.db`），记录每批信号、每笔回执、当前持仓和现金；
- 账本**只认 LIVE 回执**——模拟回执只留档，不改持仓；
- 账本是"推算值"（按回执累计），所以要**定期和 QMT 界面里的真实持仓人工核对**（见下文周检）。

---

## 二、Mac 侧部署（一次性，约 20 分钟）

> Windows 侧部署见 [`qmt_strategy/README_QMT.md`](qmt_strategy/README_QMT.md)。建议顺序：先 Windows 建好共享目录，再做本节。

### 2.1 挂载 Windows 共享目录

前提：Windows 上 `D:\qmt_bridge` 已设为 SMB 共享（README_QMT 第 1 节），两台机器同一局域网。

**方式 A：Finder（简单）**

1. Finder → 前往 → 连接服务器（`⌘K`）→ `smb://<Windows局域网IP>/qmt_bridge`
2. 输入 Windows 账号密码，勾选「在钥匙串中记住此密码」
3. 挂载后路径即 `/Volumes/qmt_bridge`

**方式 B：命令行**

```bash
mkdir -p ~/mnt/qmt_bridge
mount_smbfs //用户名@<Windows-IP>/qmt_bridge ~/mnt/qmt_bridge
```

> 注意：两种方式挂载点不同 —— 方式 A 是 `/Volumes/qmt_bridge`，方式 B 是 `~/mnt/qmt_bridge`。
> 本文档后续命令均以 `/Volumes/qmt_bridge` 为例；若用方式 B，请把示例路径和
> 配置里的 `live.bridge_root` 一并换成 `/Users/<你>/mnt/qmt_bridge`（配置里写绝对路径，别用 `~`）。
> 方式 B 重启后不会自动重挂，且卸载后 Finder 挂载会回到 `/Volumes`，建议二选一固定使用方式 A。

**开机自动挂载（推荐，否则重启后 crontab 会写失败）：**
系统设置 → 通用 → 登录项 → 添加该共享卷；或在「连接服务器」的个人收藏中保留。

验证（方式 B 则替换为你的挂载点）：

```bash
ls /Volumes/qmt_bridge          # 应看到 inbox outbound state 等子目录
touch /Volumes/qmt_bridge/inbox/_write_test && rm /Volumes/qmt_bridge/inbox/_write_test
```

### 2.2 修改配置

编辑 `live_trading/configs/csi300_topk10_live.yaml`：

```yaml
live:
  bridge_root: "/Volumes/qmt_bridge"   # 改成你的实际挂载点
  account_id: ""                        # 留空，账号走环境变量（推荐，不进 git）
```

资金账号通过环境变量提供（加入 `~/.zshrc`）：

```bash
export QMT_ACCOUNT_ID="你的资金账号"
```

### 2.3 Python 环境

复用 qlib 环境即可，无新增依赖（只用到 qlib / pandas / yaml / sqlite3 标准库）：

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -q   # 应全部通过
```

### 2.4 初始化实盘账本

首次使用前，把 QMT 界面里的**真实持仓和可用资金**灌入 Mac 账本（空仓则只需 set_cash）：

```bash
/opt/anaconda3/envs/qlib/bin/python - <<'EOF'
import sys; sys.path.insert(0, ".")
from live_trading.modules.fill_importer import LiveRecorder

r = LiveRecorder("live_trading/data/csi300_topk10_live.db")
r.set_cash(1000000.0)                      # 以 QMT 界面可用资金为准
# 已有持仓则逐只登记（QMT 代码格式，数量，成本价）：
# r.upsert_position("600000.SH", 800, 10.52)
print("cash:", r.get_cash())
print("positions:", r.get_positions())
EOF
```

> 此脚本不触发 qlib 并行取数，heredoc 可用。

### 2.5 首次链路验证（必做）

```bash
# 1. 先 dry-run 看订单是否合理（会加载模型和数据，几分钟）
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_publish_signals.py \
    --config csi300_topk10_live --trade-date <下一交易日> --mode SIMULATE --dry-run

# 2. 正式发布 SIMULATE 批次
#（去掉 --dry-run 重跑）

# 3. 确认文件已落到共享目录
ls /Volumes/qmt_bridge/inbox/

# 4. 次日盘中 Windows 消费后，回来导入回执
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_import_fills.py \
    --config csi300_topk10_live
```

SIMULATE 批次回执应为每单一条 `SKIPPED simulated`，且导入后持仓/现金**不变**——这说明隔离正确。

### 2.6 定时任务（可选，跑顺后再加）

```cron
# 数据更新（已有，沿用模拟盘）
0 18 * * 1-5  <数据更新脚本>
# 发布次日信号（等数据更新完成；注意 date 计算下一交易日由脚本内日历处理，传明天即可，非交易日会报错跳过）
30 21 * * 1-5 cd /Users/yuxianqi/Project/qlib && /opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_publish_signals.py --config csi300_topk10_live --trade-date $(date -v+1d +\%Y-\%m-\%d) --mode SIMULATE >> live_trading/logs/publish_cron.log 2>&1
# 导入回执
0 16 * * 1-5  cd /Users/yuxianqi/Project/qlib && /opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_import_fills.py --config csi300_topk10_live >> live_trading/logs/import_cron.log 2>&1
```

> 建议前两周手动执行、人工检查，再逐步交给 crontab；LIVE 模式**不建议**放进 crontab（保留每晚人工确认这道闸）。

---

## 三、日常操作流程（每个交易日）

### 晚上（T-1 日，Mac，约 21:00 后）

数据更新完成后发布次日信号：

```bash
# 1.（确认当日 Tushare→qlib 数据已更新，与模拟盘同一条 crontab）

# 2. 发布信号（先用模拟模式；实盘时改 --mode LIVE）
python live_trading/scripts/run_publish_signals.py \
    --config csi300_topk10_live \
    --trade-date <明天的日期> \
    --mode SIMULATE

# 建议先 --dry-run 看一眼订单再正式发布
```

**发布后自查（1 分钟）：**

- 终端输出 `published N orders`；
- 共享目录 `inbox/` 里出现 `signal_*.jsonl` 和 `.done` 两个文件；
- 订单数量合理（常规日 ≤ 2 买 + 2 卖；建仓日约 10 买）。

### 早上（T 日，Windows，9:15 前，约 2 分钟）

1. QMT 已登录（行情+交易），策略交易在运行中；
2. 若今天要**实盘**：手工创建当日开关文件 `D:\qmt_bridge\state\LIVE_OK_<今天日期>`（模拟日跳过此步）；
3. 瞄一眼策略日志有 `[qlib_bridge] initialized`。

### 盘中（T 日，可选）

不需要盯盘。9:25 后策略自动消费信号：先卖后买，14:50 自动撤未成单，14:55 前回执文件必然写完。想看进度就看 QMT 策略输出日志或 `outbound/fills_*.jsonl`。

### 下午（T 日，Mac，15:30 后，约 2 分钟）

```bash
python live_trading/scripts/run_import_fills.py --config csi300_topk10_live
```

**看输出三行字：**

- `imported N fill events`——有回执进来；
- `[OK ] <batch_id> planned=X terminal=X missing=0`——对上了；
- 持仓和现金列表——和你预期一致。

如果显示 `[WARN] ... missing>0`，说明有订单没收到终态回执，按第四节排障。

---

## 四、每周 / 每月运维

### 周检（约 10 分钟，建议周五盘后）

1. **持仓核对（最重要）**：打开 QMT 界面持仓页，逐只对比 `run_import_fills.py` 打印的持仓。数量不一致时用人工校正入口修正 Mac 账本：

```python
from live_trading.modules.fill_importer import LiveRecorder
r = LiveRecorder("live_trading/data/csi300_topk10_live.db")
r.upsert_position("600000.SH", 800, 10.52)   # 以 QMT 界面为准
r.set_cash(123456.78)
```

2. **现金核对**：Mac 账本现金是"不含手续费的近似值"，和 QMT 可用资金差异会随时间累积，每周校正一次；
3. **归档清理**：`archive/` 目录只增不减，超过几百个文件可打包移走；
4. **磁盘与共享**：Mac 上 `ls /Volumes/qmt_bridge/inbox` 确认 SMB 挂载还活着。

### 月检

- 滑点回顾：从 `fills` 表统计 `avg_price` vs `limit_price`，如果买入经常打满 +1% 滑点上限，考虑调 `buy_slippage`；
- 拒单/过期率：`EXPIRED`、`REJECTED` 占比高说明限价太保守或流动性有问题；
- 与模拟盘对比：同一 `signal_date` 下 `paper_trading` 与实盘买卖标的应基本一致，长期偏离说明持仓漂移，需要校正。

---

## 五、故障排查速查表

按"哪一环没动静"定位：

### 1. 晚上发布失败

| 报错 | 原因与处理 |
|------|-----------|
| `refusing LIVE mode` | 没设环境变量：`export LIVE_TRADING_CONFIRM=YES` |
| `account_id missing` | 配置 `live.account_id` 为空且没设 `QMT_ACCOUNT_ID` 环境变量 |
| `batch ... already published` | 当天已发过；如需重发用 `--seq 2` 生成新批次 |
| `orders exceed max_orders_per_day` | 订单数异常膨胀，先检查持仓账本是否漂移，不要盲目调大上限 |
| 写文件报错 / 挂载点不存在 | SMB 掉了，Finder 重新挂载 Windows 共享 |

### 2. 早上 QMT 不消费信号

按顺序检查：

1. `inbox/` 里 `.jsonl` 和 `.done` 都在吗？（只有 jsonl 没有 done = 发布中断，重新发布）
2. QMT 策略在运行吗？有行情 tick 吗？（**9:25 前和非交易日不会有动作，正常**）
3. 策略日志说什么？
   - `expired`：信号的 `trade_date` 不是今天——昨晚发错日期或今天补跑，重新发布正确日期；
   - `duplicate`：这个 batch_id 已处理过——想重跑就换 `--seq`；
   - `checksum mismatch`：文件传坏了，重新发布。

### 3. 消费了但没下真单

- 回执里全是 `SKIPPED simulated`：文件是 SIMULATE 模式，或 **忘了建当日 `LIVE_OK` 文件**（最常见）；
- 回执 `SKIPPED insufficient sellable volume`：可卖数量不足（T+1：今天买的今天不能卖），正常保护；
- 回执 `SKIPPED insufficient cash`：可用资金不足，检查资金是否在所选柜台（极速柜台需先划拨）；
- 有 `ACCEPTED` 但界面无委托：看 QMT 消息栏被柜台拒绝的原因（权限、价格笼子等）。

### 4. 下午导入异常

| 现象 | 处理 |
|------|------|
| `imported 0 fill events` | `outbound/` 没有 `.done`——QMT 侧没跑完或没跑；确认策略当天在运行。若 14:55 后仍无 done，说明盘中策略挂了，需人工去 QMT 界面核对当日委托，手工补记账本 |
| `missing > 0` | 个别订单无终态回执，对照 QMT 界面委托记录，手工确认后用 `upsert_position` / `set_cash` 校正 |
| 持仓出现负数警告日志 | 账本与实际严重漂移，立即停止次日发布，全量人工核对后校正 |

### 5. 紧急停止

任何时候想停：

1. **最快**：QMT 界面停止该策略交易（或关 QMT）；
2. 删掉当日 `LIVE_OK` 文件（新批次不会再下真单）；
3. 晚上不发布信号即可（没有信号 = 没有交易）。

已发出的委托去 QMT 界面手工撤单。

---

## 六、日常关注点清单（打印贴桌上）

**每天必看（合计 5 分钟）：**

- [ ] 晚上：发布输出 `published N orders`，N 合理
- [ ] 早上：QMT 在线 + 策略运行中 +（实盘日）LIVE_OK 已建
- [ ] 下午：`run_import_fills` 显示 `missing=0`，持仓无意外变化

**红线（出现即停，先查后跑）：**

- 持仓出现负数 / 与 QMT 界面对不上超过 1 只股票
- 同一天出现两个 batch 都被执行（幂等失效，理论不应发生）
- 回执文件 14:55 后仍未出现（盘中策略挂了）
- 单日 `REJECTED`+`ERROR` 超过订单半数

**容易忘的三件事：**

1. 实盘日早上建 `LIVE_OK_<日期>` 文件（忘了 = 当天全部空跑，属于安全侧失误，不亏钱但踏空）；
2. 节后第一天确认 Mac 数据更新正常再发布；
3. 换模型 / 改配置后，先发 SIMULATE 批次走一轮再上实盘。

---

## 七、关键路径与文件速查

| 东西 | 位置 |
|------|------|
| 共享目录（Windows） | `D:\qmt_bridge\{inbox,processing,outbound,archive,state,logs}` |
| 共享目录（Mac 挂载） | 配置 `live.bridge_root`（默认 `/Volumes/qmt_bridge`） |
| 实盘账本 | `live_trading/data/csi300_topk10_live.db` |
| 发布/导入脚本 | `live_trading/scripts/run_publish_signals.py` / `run_import_fills.py` |
| QMT 策略源码 | `live_trading/qmt_strategy/qmt_signal_bridge.py` |
| QMT 策略日志 | QMT 安装目录 `userdata\log\XtClient_FormulaOutput_*.log` |
| 策略内时间参数 | 9:25 开始下单；卖单最多等 30 分钟后开始买入；14:50 撤单；14:52 强制写回执 |
| LIVE 双开关 | 信号 `mode=LIVE`（需 env `LIVE_TRADING_CONFIRM=YES`）+ `state\LIVE_OK_<日期>` |
