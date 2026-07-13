# Qlib → QMT 实盘信号桥接设计方案

> 日期：2026-07-11  
> 状态：**定稿 v1.0**（2026-07-11 评审通过，可进入实现计划）  
> 关联：[`docs/qmt_qlib_live_guide.md`](../../qmt_qlib_live_guide.md)、[`docs/vibe_coding/paper_trading_plan.md`](../../vibe_coding/paper_trading_plan.md)、现有 `paper_trading/`

## 1. 背景与目标

### 1.1 约束（已确认）

| 约束 | 含义 |
|------|------|
| MiniQMT / 外部 XtQuant 下单不可用 | 不能用 `xttrader.order_stock` 作为主通道 |
| 已开通券商大 QMT + 极速柜台 | 可用内置 Python + `passorder` 实盘下单；极速柜台加速报单，不解决 API |
| 研究机多为 macOS | qlib 训练/信号在 Mac；QMT 只能跑在 Windows |
| 已有模拟盘 | `paper_trading/` 已有 SignalGenerator、OrderManager、SQLite 账户簿 |
| 策略形态 | CSI300 + LightGBM + TopkDropout（topk=10, n_drop=2），**日频调仓** |

### 1.2 目标

搭建一条可审计、可幂等、可先模拟再实盘的链路：

```
qlib 产出目标持仓/订单清单
  → 标准化信号文件落到共享目录
  → Windows 大 QMT 内置策略轮询消费
  → passorder 下单（可接极速柜台）
  → 成交回执写回共享目录
  → 研究侧对账入库
```

**非目标（本阶段不做）：**

- MiniQMT / XtQuant 直连（权限恢复后再做可选 Broker）
- 用 QMT 行情替换 Tushare→qlib 研究数据管线
- 高频/逐笔策略
- 两融、期权、期货

### 1.3 成功标准

1. 同一交易日同一 `batch_id` 信号只会被执行一次（幂等）。
2. QMT「模拟」模式可完整跑通：读信号 → 记意图 → 写回执（不下真单）。
3. 切换「实盘」后，订单字段与模拟盘/回测口径一致（代码映射、手数、买卖方向正确）。
4. 任一环节失败可告警，且不产生重复下单。
5. 盘后可对上：信号订单数 ≈ 委托数 ≈ 回执数（允许部分拒单并有原因）。

---

## 2. 总体架构

### 2.1 逻辑视图

```
┌──────────────────────────── Mac / Linux 研究机 ────────────────────────────┐
│  Tushare 增量 → qlib cn_data                                                │
│       ↓                                                                     │
│  paper_trading SignalGenerator（T 日收盘后预测）                             │
│       ↓                                                                     │
│  OrderPlanner（复用 TopkDropout 逻辑 + 实盘风控）                            │
│       ↓                                                                     │
│  SignalPublisher → 写出 signal_batch_YYYYMMDD.jsonl + .done                 │
│       ↓                                                                     │
│  FillImporter ← 读 fills_*.jsonl → SQLite / 告警                            │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ 共享目录（见 2.3）
┌───────────────────────────────────▼─────────────────────────────────────────┐
│  Windows + 大 QMT                                                           │
│  内置策略 qmt_signal_bridge.py                                              │
│    轮询 inbox/*.done → 解析 → 风控复核 → passorder → 写 outbound/fills_*.jsonl│
│  账号：普通/极速柜台（资金需在对应柜台可用）                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 职责切分

| 组件 | 跑在哪 | 职责 | 不负责 |
|------|--------|------|--------|
| SignalGenerator | 研究机 | 模型打分 | 下单 |
| OrderPlanner | 研究机 | 目标持仓→买卖清单、代码转换、预风控 | 交易所交互 |
| SignalPublisher | 研究机 | 原子写信号文件、版本号 | QMT API |
| QMT Bridge Strategy | Windows QMT | 消费信号、`passorder`、写回执 | 因子/模型 |
| FillImporter | 研究机 | 回执入库、对账、告警 | 改单 |

### 2.3 共享目录约定

推荐目录（Windows 与研究机都能访问同一逻辑路径）：

```
{BRIDGE_ROOT}/
  inbox/          # 研究机写入，QMT 只读消费
  processing/     # QMT 认领后移入，防重复消费
  outbound/       # QMT 写回执，研究机读取
  archive/        # 完成后归档
  state/          # QMT 本地状态（已处理 batch_id）
  logs/           # 双端日志副本（可选）
```

同步方式任选其一（按可靠性排序）：

1. **同一台 Windows**：研究机用 SMB/SSH 直接写盘（最稳）
2. **局域网 NAS + 固定挂载**
3. Syncthing / Resilio（注意完成标记必须后写 `.done`）
4. 对象存储（OSS）+ 两端拉取（延迟更大，日频可用）

**禁止**：未完成写入就改扩展名为 `.done`；多进程同时写同一 `batch_id`。

---

## 3. 日程与时序（对齐模拟盘）

核心原则与 `paper_trading_plan` 一致：

> **T 日交易消费的是 T-1 日收盘后生成的信号；T 日收盘后再生成供 T+1 使用的信号。**

### 3.1 推荐日程（A 股日频）

| 时间（建议） | 节点 | 动作 |
|--------------|------|------|
| T-1 18:00–21:00 | 研究机 | 数据更新 → 检查日历 → 生成 T-1 日预测 → 生成 **T 日** 交易批次信号 → 发布到 `inbox/` |
| T 09:15 | Windows | 确认 QMT 已登录「行情+交易」、资金在目标柜台、策略已挂「模拟/实盘」 |
| T 盘中 | QMT | 认领当日 `batch`（等待尾盘窗口，不提前报单） |
| T 14:45 | QMT | **尾盘执行窗口**（贴近回测 `deal_price=close` 口径）：先提交全部卖单；报单价 = 实时最新价 ±0.3% 缓冲，且不越过 Mac 侧 `limit_price`（昨收 ±1%）硬边界；`quickTrade=2` 立即下单 |
| T 14:45–14:49 | QMT | 卖单全部终态（或超时 4 分钟）后提交买单；买前查可用资金，不足则整手缩量 |
| T 尾盘 | QMT | 轮询委托/成交，增量写 `fills` |
| T 14:56 | QMT | 对仍未终态的委托执行撤单（`cancel`），标记 `EXPIRED`/`PARTIAL` |
| T 14:57 | QMT | **强制写 `fills_{batch_id}.done`**（收盘后 handlebar 不再触发，必须盘中兜底） |
| T 15:30 后 | 研究机 | 导入回执、对账、告警 |
| T 18:00–21:00 | 研究机 | 更新数据 → 生成供 T+1 的信号 |

### 3.2 信号「交易日」字段语义

- `trade_date`：计划执行交易的交易日（T）
- `signal_date`：打分所用行情日（通常 T-1）
- `batch_id`：全局唯一，建议 `{trade_date}_{strategy_id}_{seq}`，如 `20260714_csi300_topk10_001`

---

## 4. 信号产出设计

### 4.1 复用现有模块

| 现有 | 用途 |
|------|------|
| `paper_trading/modules/signal_generator.py` | 预测分数 |
| `paper_trading/modules/order_manager.py` | TopkDropout 买卖意图 |
| `paper_trading/configs/csi300_topk10.yaml` | 模型/策略参数同源 |
| `paper_trading/*/db` | 可继续存 predictions；实盘另开 `live_*.db` 或加 `mode=live` 表 |

新增模块建议路径：

```
live_trading/
  configs/csi300_topk10_live.yaml
  modules/
    order_planner.py      # 在 OrderManager 之上加实盘字段与校验
    signal_schema.py      # schema / 校验
    signal_publisher.py   # 原子发布
    fill_importer.py      # 回执导入
    code_map.py           # SH600000 ↔ 600000.SH
  scripts/
    run_publish_signals.py
    run_import_fills.py
  qmt_strategy/
    qmt_signal_bridge.py  # 拷贝进 QMT 策略目录的内置脚本（GBK 友好）
    README_QMT.md         # 如何在客户端挂载运行
```

### 4.2 OrderPlanner 输出

在模拟盘订单 `{instrument, direction, target_shares}` 基础上扩展为**可执行订单行**：

| 字段 | 示例 | 说明 |
|------|------|------|
| `client_order_id` | `20260714_csi300_001_01` | 幂等键，映射到 passorder `userOrderId`（注意长度） |
| `instrument_qlib` | `SH600000` | 研究侧 |
| `stock_code` | `600000.SH` | QMT 侧 |
| `side` | `BUY` / `SELL` | |
| `order_type` | `LIMIT` | 一期只用限价 |
| `price_type` | `FIX` / `PEER_BEST` | 一期建议限价；二期可对手价 |
| `limit_price` | `10.52` | 限价；卖可用略低于现价保护，买略高 |
| `quantity` | `800` | 必须 100 整数倍 |
| `priority` | `10` | 越小越先；建议卖单 < 买单 |
| `reason` | `topk_drop` | 审计 |

价格生成规则（一期）：

- **卖**：用信号发布时可用的最新收盘价 × `(1 - sell_slippage)`，默认 `sell_slippage=0.01`（偏保护成交；也可配置为昨收）
- **买**：收盘价 × `(1 + buy_slippage)`，默认 `0.01`
- 实盘当日由 QMT 侧用实时价**复核**：若相对开盘涨跌停不可交易则跳过并回执 `REJECTED`

> 说明：日频策略不追求最优成交价；优先「能成交 + 可审计」。若要以开盘价/VWAP 成交，二期再改价源。

### 4.3 预风控（研究机发布前）

- 非整手数量 → 向下取整到 100 整数倍；取整后为 0 则丢弃该单并打日志
- 现金预估不足 → 缩减买入数量或取消最弱买入
- 同一 `stock_code` 同向合并
- ST 新开仓禁止（可配置）
- 单日买入标的数 ≤ `n_drop`（建仓日除外）

---

## 5. 信号文件协议（桥接核心）

### 5.1 批次头文件（可选）+ 订单行文件

为降低内置 Python 解析复杂度，一期采用 **单文件 JSON Lines**：

路径：`inbox/signal_{batch_id}.jsonl`

第 1 行：`type=batch_header`  
后续行：`type=order`  
全部写完后另写空文件：`inbox/signal_{batch_id}.done`

#### Header

```json
{
  "type": "batch_header",
  "schema_version": "1.0",
  "batch_id": "20260714_csi300_topk10_001",
  "strategy_id": "csi300_topk10",
  "trade_date": "2026-07-14",
  "signal_date": "2026-07-11",
  "account_id": "YOUR_FUND_ACCOUNT",
  "account_type": "STOCK",
  "mode": "SIMULATE",
  "created_at": "2026-07-11T21:05:00+08:00",
  "order_count": 4,
  "checksum": "sha256:...."
}
```

`mode`：

- `SIMULATE`：QMT 策略只写回执（`status=ACCEPTED`、`message=simulated`），不调用 `passorder`
- `LIVE`：允许真实 `passorder`

> **双保险（定稿）**：文件 `mode=LIVE` **且** Windows 本地存在当日开关文件 `{BRIDGE_ROOT}/state/LIVE_OK_{trade_date}`，两者同时满足才真下单。  
> 内置 API 无法可靠检测客户端「模拟/实盘」运行模式，因此界面模式只作为**人工核对项**（上线 checklist），不作为程序判断依据。

`checksum` 定义（定稿）：对文件中**全部 order 行**（不含 header 行）按出现顺序拼接原始字节做 sha256。`.done` 文件内容为同一 checksum，QMT 侧读入后校验不一致则拒绝整批并写 `ERROR`。

#### Order 行

```json
{
  "type": "order",
  "batch_id": "20260714_csi300_topk10_001",
  "client_order_id": "20260714_csi300_001_01",
  "stock_code": "600000.SH",
  "side": "SELL",
  "quantity": 800,
  "price_type": "FIX",
  "limit_price": 10.41,
  "priority": 10,
  "instrument_qlib": "SH600000",
  "reason": "topk_drop"
}
```

#### 完成标记

`signal_{batch_id}.done` 内容为 checksum 一行（与 header 一致），QMT 读到 `.done` 才开始处理对应 `.jsonl`。

### 5.2 原子写（研究机）

```text
1. 写 inbox/signal_{batch_id}.jsonl.tmp
2. fsync
3. rename → signal_{batch_id}.jsonl
4. 写 signal_{batch_id}.done.tmp → rename → .done
```

### 5.3 回执文件

路径：`outbound/fills_{batch_id}.jsonl`

```json
{
  "type": "fill_event",
  "batch_id": "20260714_csi300_topk10_001",
  "client_order_id": "20260714_csi300_001_01",
  "mode": "LIVE",
  "stock_code": "600000.SH",
  "side": "SELL",
  "status": "FILLED",
  "requested_qty": 800,
  "filled_qty": 800,
  "avg_price": 10.45,
  "qmt_order_id": "...",
  "message": "",
  "ts": "2026-07-14T09:31:12+08:00"
}
```

`status` 枚举：`ACCEPTED` | `FILLED` | `PARTIAL` | `REJECTED` | `SKIPPED` | `EXPIRED` | `ERROR`

- `EXPIRED`：14:50 撤单后仍未成交的部分（或整批过期未执行）
- **`mode` 为必填**：`FillImporter` 只允许 `mode=LIVE` 的回执更新 `live_positions` / 现金，`SIMULATE` 回执单独入表用于链路验证，绝不污染实盘账簿

批次结束再写：`outbound/fills_{batch_id}.done`（最迟 14:55 前强制写出，见 §3.1）

### 5.4 幂等与认领

QMT 侧：

1. 看到 `.done` → 先只读 header 取 `trade_date`：
   - `trade_date > 今天`（T-1 晚发布的次日信号）→ **不认领**，原地留在 `inbox/` 等到当天；
   - 否则将 `jsonl`+`done` 移到 `processing/`
2. **过期批次防护（定稿，防误执行）**：`header.trade_date < 当前交易日` → 整批 `SKIPPED expired`，直接归档。防止某日 QMT 未开机时，残留信号在次日被执行（策略常驻运行时，未来日期批次由上一条保护，不会提前消费）
3. checksum 校验失败 → 整批 `ERROR`，归档并告警
4. 若 `state/processed_batches.txt` 已含该 `batch_id` → 直接归档，写 `SKIPPED duplicate`
5. 处理完写入 `state/`，再移到 `archive/`

`client_order_id` 映射 `passorder` 的 `userOrderId` / remark：  
Mini/极简有 24 字符限制；大 QMT 较宽裕，仍建议 `client_order_id` ≤ 24 字符（例如 `0714T10S01` 编码规则）。

---

## 6. QMT 内置策略开发

### 6.1 运行方式

1. 将 `live_trading/qmt_strategy/qmt_signal_bridge.py` 导入大 QMT「模型交易」
2. 主图代码可任选（如 `000001.SZ`），周期选 `1m` 即可（实盘实际 tick 驱动）
3. 运行模式：先 **模拟**，验证回执后再改 **实盘**
4. 账号选已划入资金的柜台账户（若用极速柜台，确认资金在极速侧）

### 6.2 策略骨架（逻辑规格）

内置环境注意：

- 源文件编码常用 **GBK**
- 使用 `ContextInfo`；下单用 `passorder`
- 立即下单：`quickTrade=2`（并做好「每单只触发一次」状态机，避免 tick 重复下单）
- 用自定义全局对象存状态，**不要**依赖 `ContextInfo` 属性存委托状态（官方对 quickTrade 的说明）

伪代码规格：

```python
# coding: gbk
# qmt_signal_bridge.py — 逻辑规格，落地时按 QMT 内置 API 微调

BRIDGE_ROOT = r"D:\qmt_bridge"
STRATEGY_NAME = "qlib_bridge"
POLL_SECONDS = 3

class BridgeState(object):
    def __init__(self):
        self.last_poll = 0
        self.current_batch = None
        self.pending = []          # 待报订单
        self.submitted = set()     # 已提交 client_order_id
        self.finished_batches = set()

g_state = BridgeState()

def init(ContextInfo):
    # 加载 state/processed_batches.txt
    # ContextInfo.set_account / 账号在界面绑定亦可
    pass

def handlebar(ContextInfo):
    if not ContextInfo.is_last_bar():
        return
    now = _now_ts()
    if now - g_state.last_poll < POLL_SECONDS:
        return
    g_state.last_poll = now

    _claim_new_batch_if_any()          # 含过期批次/重复批次/checksum 检查
    _process_pending_orders(ContextInfo)  # 先卖后买两阶段状态机
    _poll_order_status(ContextInfo)    # get_trade_detail_data 按 remark 匹配
    _force_finalize_if_near_close(ContextInfo)  # >=14:50 撤未成单; >=14:52 写 fills .done
    _flush_fills_if_batch_done()
```

### 6.3 passorder 映射（股票普通账户，已按官方文档核实）

调用形式（传 `userOrderId` 时 `strategyName`、`quickTrade` **必须**一并填写）：

```python
passorder(opType, orderType, accountid, orderCode, prType, price, volume,
          strategyName, quickTrade, userOrderId, ContextInfo)
```

| 参数 | 定稿值 | 说明 |
|------|--------|------|
| `opType` | BUY=`23`，SELL=`24` | 股票买入/卖出 |
| `orderType` | `1101` | 单股、单账号、按股数下单 |
| `prType` | `11` | 指定价（限价）；`price` 仅在 prType=11/49 时生效 |
| `price` | `limit_price` | 信号文件中的限价 |
| `volume` | `quantity` | 股数（100 整数倍） |
| `strategyName` | `qlib_bridge` | 固定 |
| `quickTrade` | `2` | 调用即下单；配合 `submitted` 集合防 tick 重复触发 |
| `userOrderId` | `client_order_id` | 回执匹配键（对应委托/成交对象 `m_strRemark`） |

示例：`passorder(24, 1101, account_id, '600000.SH', 11, 10.41, 800, 'qlib_bridge', 2, '0714T10S01', ContextInfo)`

下单前二次检查（QMT 内）：

1. `get_instrument_detail` / 行情：停牌、涨跌停不可交易则 `SKIPPED`
2. 卖出数量 ≤ 持仓**可用量**（`POSITION` 对象 `m_nCanUseVolume`，T+1 制度下当日买入不可卖）
3. `batch.mode==LIVE` 且当日 `LIVE_OK` 开关文件存在，才调用 passorder；否则只写 `ACCEPTED(simulated)`
4. 买单仅在全部卖单终态（或等待超时）后提交；提交前查账户可用资金（`ACCOUNT` 对象），不足则按整手向下缩量

### 6.4 成交与委托查询

用内置 `get_trade_detail_data(accountID, 'STOCK', 'ORDER'|'DEAL'|'POSITION')`：

- 提交后写 `ACCEPTED`
- 轮询匹配 `m_strRemark == client_order_id` → 更新 `FILLED` / `PARTIAL` / `REJECTED`
- 批次所有订单终态后写 `.done`
- **兜底（定稿）**：14:50 对未终态委托调用 `cancel` 撤单并标记 `EXPIRED`；14:55 前无论如何写出 `.done`。收盘后 `handlebar` 不再被 tick 触发，任何「盘后再写」的设计都不可行

### 6.5 极速柜台相关

- 极速柜台**不改变**本桥接协议
- 仅影响：界面所选资金账号是否为极速通道账号、资金是否已划入
- 日频 TopkDropout：**不必**为极速柜台改策略逻辑；保持简单限价即可
- 若普通/极速分账户：配置文件写死 `account_id`，禁止运行时猜测

### 6.6 日志与排障

- 策略内 `print` → `XtClient_FormulaOutput_*.log`
- 同时 append 到 `{BRIDGE_ROOT}/logs/qmt_bridge_YYYYMMDD.log`
- 关键错误写 `outbound/error_{batch_id}.json`

---

## 7. 研究机侧脚本

### 7.1 `run_publish_signals.py`

```text
输入：--config live_trading/configs/csi300_topk10_live.yaml
      --trade-date 2026-07-14
      --mode SIMULATE|LIVE

步骤：
1. qlib.init + 检查 signal_date 数据就绪
2. SignalGenerator.predict(signal_date)
3. 读取「计划交易日开盘前」持仓快照
   - 模拟盘：来自 paper db
   - 实盘：来自上一交易日 fills 汇总后的 live positions（无则人工 seed）
4. OrderPlanner.generate → orders
5. 校验 schema + checksum
6. SignalPublisher.publish(BRIDGE_ROOT)
7. 记录 batch 到 live db
```

持仓来源策略：

- **一期**：实盘持仓以 QMT 回执累计的 `live_positions` 为准；每日开盘前允许 `--sync-positions-from` 手工 JSON 校正
- **二期**：内置策略盘前导出 `outbound/positions_snapshot_YYYYMMDD.jsonl`，研究机导入后再规划订单（推荐，减少漂移）

### 7.2 `run_import_fills.py`

```text
1. 扫描 outbound/*.done
2. 解析 fills，按 client_order_id upsert
3. 更新 live_positions / cash（若回执含成交额；否则用成交价估算）
4. 对账：order_count vs terminal fills
5. 告警：拒单、超超时未回执、数量不一致
6. 归档 outbound → 研究机 archive 备份
```

### 7.3 配置示例要点

```yaml
# live_trading/configs/csi300_topk10_live.yaml
extends: paper_trading/configs/csi300_topk10.yaml   # 或复制关键段保持同源

live:
  bridge_root: "/Volumes/qmt_bridge"   # Mac 挂载点；Windows 为 D:\\qmt_bridge
  strategy_id: "csi300_topk10"
  account_id: "REPLACE_ME"
  default_mode: "SIMULATE"
  buy_slippage: 0.01
  sell_slippage: 0.01
  max_orders_per_day: 20
  client_order_id_max_len: 24

schedule:
  publish_after: "21:00"
  import_after: "15:30"
```

---

## 8. 数据与代码映射

```python
def qlib_to_qmt(code: str) -> str:
    return f"{code[2:]}.{code[:2]}"  # SH600000 -> 600000.SH

def qmt_to_qlib(code: str) -> str:
    symbol, market = code.split(".")
    return f"{market}{symbol}"
```

与 Tushare `ts_code` 同形，可复用 `stock_names` 表。

复权：研究用前复权特征；**下单价格一律未复权实价**（QMT 实时价/昨收）。

---

## 9. 风控与安全

| 层级 | 措施 |
|------|------|
| 配置 | `default_mode=SIMULATE`；LIVE 需显式 CLI `--mode LIVE` + 二次确认 env `LIVE_TRADING_CONFIRM=YES` |
| 文件 | LIVE 批次必须同时存在 Windows 本地 `state/LIVE_OK_{trade_date}` 当日开关文件（每日人工触摸，不随共享目录同步） |
| 策略 | 双条件：文件 `mode=LIVE` + `LIVE_OK` 开关；界面「实盘」模式为人工核对项 |
| 时间 | `trade_date != 当日` 的批次一律 SKIPPED expired |
| 资金 | 单笔名义金额上限、单日买入总额上限 |
| 标的 | 仅允许 CSI300（或白名单文件） |
| 幂等 | batch_id + client_order_id |
| 密钥 | 账号写在 Windows 本地配置，不进 git |

---

## 10. 观测与对账

每日报表字段：

- 发布：`batch_id, order_count, mode, publish_ts`
- 执行：`accepted, filled, partial, rejected, skipped`
- 滑点：`(avg_price - limit_price) / limit_price`
- 持仓漂移：计划持仓 vs 回执持仓
- 与 paper_trading 同日信号对比（可选）：同一 `signal_date` 买卖标的是否一致

告警通道：复用 `paper_trading` email 告警配置。

---

## 11. 分阶段落地

### Phase 0 — 协议与目录（0.5 天）

- 创建 `BRIDGE_ROOT` 目录结构
- 定稿 schema，写 JSON Schema 或 pydantic 校验
- 文档：Windows 挂载与 QMT 导入步骤

### Phase 1 — 研究机发布链路（1–2 天）

- `code_map` / `signal_schema` / `signal_publisher`
- `order_planner` 基于现有 OrderManager
- `run_publish_signals.py` 打出 SIMULATE 文件
- 单测：原子写、checksum、代码转换、整手

### Phase 2 — QMT 内置策略模拟消费（2–3 天）

- 实现 `qmt_signal_bridge.py`
- 客户端「模拟」模式跑通：读文件 → 不报单 → 写假回执
- 验证幂等、乱序 `.done`、残缺文件

### Phase 3 — 回执导入与对账（1 天）

- `fill_importer` + live db
- 日终报表与告警

### Phase 4 — 实盘小资金（谨慎）

- 先 1 笔极小数量验证 `passorder` 字段
- 再放开 TopkDropout 全量
- 确认极速柜台资金划拨（若使用）

### Phase 5 — 增强（可选）

- 盘前持仓快照回传
- 部成撤单重挂
- MiniQMT 恢复后增加 `XtQuantBroker` 并行通道（同信号协议）

---

## 12. 测试计划

| 用例 | 预期 |
|------|------|
| 只写 jsonl 不写 done | QMT 不消费 |
| 重复 done 同一 batch | 第二次 SKIPPED duplicate |
| `trade_date` 为昨日的批次 | 整批 SKIPPED expired，不下任何单 |
| checksum 与 order 行不符 | 整批 ERROR + 告警 |
| 含非法代码 `SH600000` 未转换 | 发布阶段失败 |
| quantity=150 | 发布前向下取整到 100（定稿：取整而非拒绝） |
| mode=SIMULATE | 不调用 passorder，回执 `mode=SIMULATE` |
| mode=LIVE 但无 `LIVE_OK_{date}` 文件 | 不下真单，回执 SKIPPED + 原因 |
| SIMULATE 回执导入 | 不改动 `live_positions` / 现金 |
| 卖出超过可用持仓（含 T+1 当日买入） | 按可用量缩量或 SKIPPED + 原因 |
| 涨停买入 / 跌停卖出 | SKIPPED |
| 卖单未全部终态 | 买单不提交（等待或超时后按实际资金缩量） |
| 14:50 仍有未成委托 | 撤单、标记 EXPIRED，14:55 前写出 fills done |
| 网络同步延迟导致半文件 | 无 done 不读；有 done 且 checksum 通过才处理 |

研究机单测不依赖 QMT；QMT 侧用模拟模式 + 手工投放样例文件验收。

---

## 13. 风险与对策

| 风险 | 对策 |
|------|------|
| 文件同步冲突 | 单写者、原子 rename、done 后置 |
| tick 重复下单 | `submitted` 集合 + processed batch 持久化 |
| 持仓漂移 | 盘前快照（Phase5）+ 人工校正入口 |
| 内置 Python 3.6 / GBK | 策略代码保持简单，复杂逻辑放研究机 |
| 信号用收盘价、盘中价差 | 滑点缓冲；拒绝追涨停 |
| 误开 LIVE | 双重开关 + 环境变量确认 |
| 共享盘延迟 | 日频场景可接受；关键路径优先本机盘 |

---

## 14. 与现有文档的关系

| 文档 | 关系 |
|------|------|
| `docs/qmt_qlib_live_guide.md` | 知识地图；本方案是落地设计 |
| `docs/vibe_coding/paper_trading_plan.md` | 信号时序与 TopkDropout 口径同源 |
| 本文件 | **无 MiniQMT 条件下的主实盘方案** |

后续若 MiniQMT 权限恢复：保留本文件协议，仅将 QMT 内置策略替换/并联为 `xttrader` 执行器，信号层不动。

---

## 15. 决策点定稿（默认值，如需变更在开发前提出）

| # | 决策点 | 定稿默认 | 备注 |
|---|--------|----------|------|
| 1 | 共享目录 | Windows 本机盘 `D:\qmt_bridge`，研究机 SMB 挂载直接写 | 最少同步环节；Mac 侧挂载点写入 `live.bridge_root` |
| 2 | 一期成交价 | 昨收 ± 1% 滑点限价（`prType=11`） | 二期再评估对手价/算法单 |
| 3 | 持仓真相源 | 一期回执累计 + `--sync-positions-from` 人工校正；Phase 5 加盘前快照 | 每周至少人工核对一次 |
| 4 | 实盘账户 | 极速柜台账号（资金已划入方可）；`account_id` 写 Windows 本地配置 | 若资金在普通柜台则先划拨 |
| 5 | 模型配置 | 与 `paper_trading/configs/csi300_topk10.yaml` 共用模型/策略段，live 配置只加执行参数 | YAML 无原生 extends，实现时用显式 merge 函数 |
| 6 | 非整手数量 | 发布前向下取整到 100，取整后为 0 则丢弃该单 | 测试计划已对应 |
| 7 | 批次终结 | 14:50 撤未成单 → EXPIRED；14:55 前强制写 fills done | handlebar 收盘后不触发，盘中兜底是硬约束 |

---

## 16. 评审记录（2026-07-11 定稿）

本次评审修订：

1. 补「过期批次防护」：`trade_date != 当日` 整批 SKIPPED（防 QMT 停机日残留信号次日误执行）
2. 补「批次终结兜底」：14:50 撤单、14:55 强制写回执 done（handlebar 收盘后不触发，无兜底会卡死对账）
3. 定稿先卖后买资金时序：卖单全部终态后再提交买单，买前查可用资金并整手缩量
4. 回执协议增加必填 `mode` 字段：SIMULATE 回执不得污染 `live_positions`
5. 「界面实盘检测」降级为人工核对项；程序双保险改为 文件 `mode=LIVE` + 本地 `LIVE_OK_{date}` 开关文件
6. `passorder` 参数按官方文档核实定稿：限价 `prType=11`；传 `userOrderId` 时 `strategyName`/`quickTrade` 必填；回执匹配键为 `m_strRemark`
7. 卖出可用量明确用 `POSITION.m_nCanUseVolume`（T+1）
8. `checksum` 语义定稿（order 行 sha256，done 文件同值），status 枚举增加 `EXPIRED`

下一步：按 Phase 0→4 编写实现计划并落地代码。
