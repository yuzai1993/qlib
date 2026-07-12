# QMT × Qlib 实盘对接指南（通俗版）

> 面向本仓库后续「qlib 研究/回测 → QMT 实盘」的能力地图。  
> 资料来源：[QMT 新人教程](https://dict.thinktrader.net/freshman/rookie.html)、[迅投知识库](https://dict.thinktrader.net/)、[XtQuant 快速开始](https://dict.thinktrader.net/nativeApi/start_now.html)。

---

## 1. 一句话搞懂 QMT

**QMT（迅投极速策略交易系统）= 券商柜台旁的「交易终端 + 行情 + Python 策略运行时」。**

- **研究端（投研端）**：买迅投产品，偏回测、仿真、数据研究。
- **券商端（券商 QMT / MiniQMT）**：向券商申请开通，挂真实资金账户，能真下单。

对本仓库来说：

| 阶段 | 用什么 |
|------|--------|
| 因子、训练、回测 | 继续用 **Qlib**（已有流水线） |
| 每日信号 / 模拟盘 | 继续用 **Qlib + 自建 paper trading** |
| 真实报单 / 查持仓 / 盘中行情 | 走 **MiniQMT + XtQuant（原生 Python）** |

**Qlib 不负责下单；QMT 不负责 Alpha158/LightGBM。** 两者通过「信号 → 订单」桥接。

---

## 2. 两条 Python 路线（只记这条就够）

迅投有两套 Python，用途完全不同：

```
┌─────────────────────────────────────────────────────────┐
│  A. 内置 Python（写在 QMT 客户端策略编辑器里）           │
│     - Python 3.6 内置环境                               │
│     - 用 passorder / ContextInfo / handlebar            │
│     - 适合「策略完全跑在 QMT 里」                        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  B. 原生 Python / XtQuant（本仓库应选这条）              │
│     - 你自己的 conda/venv（3.6–3.12，64 位）            │
│     - import xtquant.xtdata / xtquant.xttrader          │
│     - 必须先启动 MiniQMT 客户端，再连 userdata_mini     │
│     - 适合「Qlib 算信号 → 外部脚本下单」                 │
└─────────────────────────────────────────────────────────┘
```

**结论：配合 qlib 实盘，用路线 B（XtQuant）。**  
路线 A 的 `passorder` 了解即可，不必作为主架构。

---

## 3. 产品与账号怎么选

### 3.1 券商 QMT vs 投研端

| 对比项 | 券商 QMT / MiniQMT | 投研端 |
|--------|-------------------|--------|
| 目的 | 交易 | 研究、仿真 |
| 怎么拿到 | 找券商客户经理开通（有资金门槛） | [投研网站](https://xuntou.net) 注册购买 |
| 登录 | 券商给的账号密码；选「行情+交易」 | 手机号+密码；不要勾「交易通」 |
| 交易 path | `{安装目录}\userdata_mini` | `{安装目录}\userdata` |
| 真金白银 | 可以 | 主要是仿真账户 |

### 3.2 安装与目录（出事后第一时间看这里）

不要装 C 盘（权限坑多）。关键目录：

| 路径 | 干什么 |
|------|--------|
| `{安装目录}\bin.x64` | 客户端、内置 Python 库 |
| `{安装目录}\datadir` | 本地下载的行情数据 |
| `{安装目录}\userdata\log` | 日志（排障必看） |
| `{安装目录}\userdata_mini` | **XtQuant 交易连接路径（券商端）** |

常用日志：

- `XtClient_*.log` — 客户端
- `XtClient_Formula_*.log` — 策略运行
- `XtClient_FormulaOutput_*.log` — 策略 print 输出

### 3.3 首次上手检查清单

1. 开通券商 MiniQMT（确认有 **XtQuant 下单权限**）
2. 安装到非 C 盘，下载客户端 Python 库并重启
3. 登录选 **行情+交易**，不要极简模式（内置策略场景）；**连 XtQuant 时 FAQ 要求极简模式**——以你实际券商版本文档为准，连接失败时按 FAQ 切换验证
4. 行情主站 / 交易中心尽量选带「迅投」字样的服务器
5. 补历史 K 线（界面下载或 `xtdata.download_history_data`）
6. 确认 `userdata_mini` 下存在 `up_queue_xtquant`（没有 = 没开 API 下单权限，找券商）

---

## 4. XtQuant 两大模块（实盘核心）

启动 **MiniQMT** 后，外部 Python 才能用：

```
你的 Python 进程
    │
    ├─ xtdata   ──行情──► MiniQMT ──► 迅投行情服务器
    │
    └─ xttrader ──交易──► MiniQMT ──► 券商柜台
```

### 4.1 `xtdata`：行情与基础数据

**设计哲学：先下载到本地，再读取；要实时就先订阅。**

| 能力 | 典型 API | 和 qlib 的关系 |
|------|----------|----------------|
| 下历史 K 线 | `download_history_data` / `download_history_data2` | 可作盘中/增量行情补充；日频研究仍可用 Tushare→qlib bin |
| 读行情 | `get_market_data` / `get_market_data_ex` | 下单前取最新价、涨跌停 |
| 订阅实时 | `subscribe_quote` + `callback` + `xtdata.run()` | 盘中策略；日频调仓可不用 |
| 全推快照 | `get_full_tick` / 全推订阅 | 监控、涨跌停判断 |
| 合约信息 | `get_instrument_detail` | 涨停价、跌停价、停牌、流通股本 |
| 板块成分 | `download_sector_data` / `get_stock_list_in_sector` | 对照 CSI300 等股票池 |
| 交易日历 | `get_trading_dates` / `get_trading_calendar` | 对齐 qlib 交易日 |
| 财务 | `download_financial_data` / `get_financial_data` | 一般研究仍走 qlib；实盘少用 |
| 指数权重 | `download_index_weight` / `get_index_weight` | 组合归因可选 |

常用周期：`tick`、`1m`、`5m`、`1d` 等。  
复权：`dividend_type`（`none` / `front` / `back`…），**实盘下单用未复权价**。

最小可用示例：

```python
from xtquant import xtdata

code = "600000.SH"
xtdata.download_history_data(code, period="1d", incrementally=True)
data = xtdata.get_market_data_ex([], [code], period="1d", count=5)
print(data)
```

盘中订阅回调：

```python
def on_quote(data):
    # data 为本次触发的增量；完整序列再用 get_market_data_ex 取
    print(data)

xtdata.subscribe_quote("600000.SH", period="1m", count=-1, callback=on_quote)
xtdata.run()  # 必须阻塞，否则进程直接退出
```

**限制提醒：** 非 VIP 订阅数量有上限（教程提到约 300 个同时订阅）。CSI300 全订阅要规划批次或 VIP。

### 4.2 `xttrader`：交易与账户

连接要点：

```python
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant

path = r"D:\你的安装目录\userdata_mini"  # 券商端；投研端用 userdata
session_id = 123456  # 同一时刻多策略不能重复；换 session 间隔 > 3 秒

xt_trader = XtQuantTrader(path, session_id)
xt_trader.register_callback(MyCallback())  # 成交/委托推送
assert xt_trader.connect() == 0

acc = StockAccount("你的资金账号")  # 信用户等可传第二参数
xt_trader.subscribe(acc)
```

| 能力 | 典型 API | 说明 |
|------|----------|------|
| 同步下单 | `order_stock(...)` | 返回 `order_id` |
| 异步下单 | `order_stock_async(...)` | 返回 `seq`，在回调里对账 |
| 撤单 | `cancel_order_stock` / `_async` | |
| 查资产 | `query_stock_asset` | 现金、总资产等 |
| 查委托 | `query_stock_orders` / `query_stock_order` | |
| 查成交 | `query_stock_trades` | |
| 查持仓 | `query_stock_positions` / `query_stock_position` | **T+1 可卖量看持仓字段** |
| 主推回调 | `on_stock_order` / `on_stock_trade` / `on_stock_asset` / `on_stock_position` | 状态机核心 |
| 断线 | `on_disconnected` | 必须重连/告警 |

股票买卖常量：

- 买：`xtconstant.STOCK_BUY`
- 卖：`xtconstant.STOCK_SELL`
- 限价：`xtconstant.FIX_PRICE`
- 市价：上交所/深交所各有一套 `MARKET_*`（**仿真往往不支持市价**）

委托状态（查单时用）：

| 值 | 含义 |
|----|------|
| 50 | 已报 |
| 53 | 部撤 |
| 54 | 已撤 |
| 55 | 部成 |
| 56 | 已成 |
| 57 | 废单 |

`order_remark` 在极简/Mini 端最长约 **24 英文字符**，超出会截断——策略名别写太长。

---

## 5. 股票代码：Qlib ↔ QMT 必须转换

这是对接时最容易踩的坑。

| 系统 | 示例 |
|------|------|
| Qlib instrument | `SH600000`、`SZ000001` |
| QMT / XtQuant | `600000.SH`、`000001.SZ` |
| Tushare `ts_code` | `600000.SH`（与 QMT 同形） |

建议在执行层统一做双向映射：

```python
def qlib_to_qmt(code: str) -> str:
    # SH600000 -> 600000.SH
    return f"{code[2:]}.{code[:2]}"

def qmt_to_qlib(code: str) -> str:
    # 600000.SH -> SH600000
    symbol, market = code.split(".")
    return f"{market}{symbol}"
```

本仓库模拟盘文档里已有 `instrument` ↔ `ts_code` 思路，实盘可直接复用「QMT 格式 = Tushare 格式」。

---

## 6. 推荐架构：Qlib 研究 + QMT 执行

结合现有 `docs/vibe_coding/paper_trading_plan.md`，实盘可演进为：

```
        ┌──────────────┐     日频/定时      ┌─────────────────┐
        │  Tushare 等   │ ───────────────► │  qlib cn_data    │
        └──────────────┘                   └────────┬────────┘
                                                    │
                                           特征 / 模型预测
                                                    │
                                           ┌────────▼────────┐
                                           │  信号生成        │
                                           │  TopkDropout 等  │
                                           └────────┬────────┘
                                                    │ 目标权重/目标持仓
                          ┌─────────────────────────┼─────────────────────────┐
                          ▼                         ▼                         ▼
                 ┌────────────────┐      ┌────────────────┐      ┌────────────────┐
                 │ Paper Executor │      │ 风控 / 校验     │      │ QMT Executor   │
                 │ （现有模拟盘）  │      │ 涨跌停/停牌/    │      │ xttrader 下单  │
                 └────────────────┘      │ 额度/最小单位   │      └────────┬───────┘
                                         └────────────────┘               │
                                                                          ▼
                                                                 ┌────────────────┐
                                                                 │ MiniQMT 客户端  │
                                                                 └────────────────┘
```

**建议分工：**

1. **信号仍由 Qlib 生成**（与回测同模型、同特征，避免「回测一套、实盘另一套」）。
2. **执行层单独模块**（`QmtBroker`）：代码转换、下单、撤单、对账、重试。
3. **行情以「够用」为原则**：
   - 日频调仓：开盘前用本地/qlib 数据算信号；盘中只需最新价、涨跌停、停牌。
   - 日内策略：再用 `subscribe_quote` / tick。
4. **先仿真账户跑通全链路**，再切真实资金；运行模式与「模拟/实盘」开关写死在配置，不要硬编码。

日频调仓伪流程（与模拟盘「先成交昨日信号、再生成今日信号」一致）：

```
09:15  检查 MiniQMT 在线、账户可交易
09:20  query 持仓/资金，对齐本地账户簿
09:25  读取昨日目标持仓 → 生成买卖列表
09:30+ 按规则下单（限价/对手价），监听成交回调
盘后    对账、落库；跑当日预测，写入「明日目标持仓」
```

---

## 7. 和「内置 passorder」相关的知识点（了解即可）

若有人把策略写进 QMT 编辑器：

- 实盘里策略实际按 **tick** 驱动；界面选的「周期」主要影响非快速下单时的行为。
- `passorder(..., quickTrade=0)`：等 K 线走完再下（偏回测对齐）。
- `quickTrade=2`：调用即下，**历史 bar 也可能真下**，慎用。
- 模型交易界面可选 **模拟**（只记信号）/ **实盘**（真下单）。

外部 XtQuant **不走** `passorder`，走 `order_stock`。

---

## 8. 实盘能力清单（以后做对接时逐项打勾）

### 环境与权限

- [ ] 券商 MiniQMT 安装、非 C 盘
- [ ] XtQuant 下单权限（`up_queue_xtquant` 存在）
- [ ] Python 版本与 `xtquant` 库匹配（64 位 3.6–3.12）
- [ ] `userdata_mini` 路径可写、`connect()==0`
- [ ] 行情/交易服务器稳定（带迅投字样优先）

### 数据

- [ ] 日 K / 分钟 K 增量下载
- [ ] 合约详情（涨跌停、停牌）
- [ ] 交易日历
- [ ] 板块/指数成分（与 qlib CSI300 对齐）
- [ ] （可选）VIP：资金流、涨跌停价历史、北向等

### 交易

- [ ] 限价买卖、撤单
- [ ] 异步下单 + 回调对账
- [ ] 资产 / 持仓 / 委托 / 成交查询
- [ ] 委托状态机（部成、废单、撤单）
- [ ] 断线重连与告警
- [ ] 信用账户能力（若用两融，另开清单）

### 与 Qlib 集成

- [ ] `SH600000` ↔ `600000.SH` 映射表
- [ ] 信号文件/DB → 目标持仓
- [ ] 风控：涨跌停不追、停牌跳过、数量 100 股整数倍、现金预留
- [ ] 成交回写本地账户，和 paper trading 共用存储结构
- [ ] 日志与审计（策略名、`order_remark`、原始回报）

---

## 9. 常见坑（按出现频率）

1. **没开 MiniQMT 就 import/connect** → 连接失败。  
2. **path 指错**：券商要用 `userdata_mini`，投研用 `userdata`。  
3. **无 API 权限**：没有 `up_queue_xtquant`。  
4. **C 盘权限**：设置不生效、数据写不进。  
5. **同一 session 重连间隔 < 3 秒**。  
6. **代码格式混用**：Qlib 的 `SH600000` 直接下单必废。  
7. **先 get 后发现没数据**：忘记 `download_history_data`。  
8. **订阅超限**：行情不更新或 OHLC 全一样。  
9. **pandas 报错**：客户端 Python 库损坏，删 `bin.x64/Lib` 重下。  
10. **仿真当实盘测市价**：市价多数只在实盘生效。

---

## 10. 官方文档导航（按需深挖）

| 主题 | 链接 |
|------|------|
| 新人安装与上手 | https://dict.thinktrader.net/freshman/rookie.html |
| 知识库首页 | https://dict.thinktrader.net/ |
| XtQuant 快速开始 | https://dict.thinktrader.net/nativeApi/start_now.html |
| xtdata 行情 API | https://dict.thinktrader.net/nativeApi/xtdata.html |
| xttrader 交易 API | https://dict.thinktrader.net/nativeApi/xttrader.html |
| 完整代码示例 | https://dict.thinktrader.net/nativeApi/code_examples.html |
| XtQuant 常见问题 | https://dict.thinktrader.net/nativeApi/question_function.html |
| 内置 Python 入门 | https://dict.thinktrader.net/innerApi/start_now.html |
| 内置交易函数 passorder | https://dict.thinktrader.net/innerApi/trading_function.html |
| 股票数据字典 | https://dict.thinktrader.net/dictionary/stock.html |

---

## 11. 和本仓库的下一步建议

> **当前主方案（无 MiniQMT）**：设计见  
> [`docs/superpowers/specs/2026-07-11-qmt-live-signal-bridge-design.md`](superpowers/specs/2026-07-11-qmt-live-signal-bridge-design.md)  
> —— qlib 产信号 → 文件桥 → 大 QMT 内置策略 `passorder`。  
> **代码已落地**：`live_trading/`（研究机侧模块与 CLI）+ `live_trading/qmt_strategy/`（QMT 内置策略与部署说明 `README_QMT.md`），测试在 `tests/live_trading/`。

1. **短期**：模拟盘稳定；按上述设计落地 `live_trading/` 信号发布与 QMT 桥接策略。  
2. **中期**：SIMULATE 全链路验收后，小资金 LIVE；确认极速柜台资金划拨（若使用）。  
3. **可选**：MiniQMT 权限恢复后再加 `XtQuantBroker`，信号协议保持不变。  

不必用 QMT 重做 Alpha158；**QMT 只承接「最后一公里」的行情确认与报单。**
