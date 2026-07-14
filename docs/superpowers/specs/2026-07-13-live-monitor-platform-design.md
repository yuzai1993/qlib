# 实盘策略监控平台 设计方案 v1.0

> **状态：Phase 1/2 已实现**（2026-07-13）。实现计划见 [`docs/superpowers/plans/2026-07-13-live-monitor-platform.md`](../plans/2026-07-13-live-monitor-platform.md)，运维手册见 `live_trading/README.md` §7。
> 日期：2026-07-13
> 上游设计：[QMT 实盘信号桥](2026-07-11-qmt-live-signal-bridge-design.md)
> 参考实现：`paper_trading/`（Web 仪表盘 + 告警的既有范式，仅参考不复用服务）

---

## 1. 背景与目标

实盘链路（Mac qlib 发信号 → QMT 文件桥下单 → 回执导入 SQLite）已经落地，但监控完全靠人工：

- 账本（`live_trading/data/*.db`）只有**最新**持仓和近似现金，没有每日净值、收益、回撤，无法回答"实盘到底赚了多少"；
- `batches` / `fills` 表数据齐全，但没有任何程序消费它们做报表或告警；
- 流程健康靠人每天三次肉眼检查（晚上发布输出、早上 QMT 日志、下午导入输出），漏看一次就可能空跑或漂移。

**目标**：为实盘建一套独立的监控平台，做到——

1. **账户看得见**：每日净值快照、收益 vs 基准、持仓历史、成交流水、对账状态，Web 仪表盘可视化；
2. **流程盯得住**：数据更新 → 信号发布 → 回执导入 → 快照，每个环节按时完成自动检查，缺失/异常即告警；
3. **异常推得到**：微信推送（Server酱 / PushPlus），关键告警 + 每日收盘日报。

**非目标（本期不做）**：

- 盘中准实时监控（QMT 侧盘中回传快照）——留待后续；
- 实盘 vs 模拟盘同信号偏差自动对比——留待后续；
- 滑点/拒单率的专题分析页——fills 数据已留存，后续可加；
- 不改动 QMT 侧 `qmt_signal_bridge.py`（Windows 端零改动）。

## 2. 总体架构

独立服务，代码全部放在 `live_trading/` 下，与 `paper_trading` 完全分离（可参考其代码风格，不 import 其 web/alert 模块）。数据仍写入现有实盘账本 SQLite（同一个 db 文件，新增监控表），保持单一事实源。

```
                      ┌─────────────────────────────────────────┐
                      │  live_trading/data/csi300_topk10_live.db │
                      │  既有: batches / fills / positions /      │
                      │        account_state                     │
                      │  新增: daily_snapshot / position_snapshot │
                      │        pipeline_events / alerts          │
                      └───────▲──────────────▲──────────────▲────┘
                              │写            │写            │读
             ┌────────────────┴───┐   ┌──────┴─────────┐   ┌┴──────────────────┐
             │ run_monitor.py     │   │ 既有链路脚本    │   │ live_trading/web   │
             │ (cron 定时驱动)     │   │ publish/import │   │ FastAPI + ECharts  │
             │ ├ snapshot 估值     │   └────────────────┘   │ (只读仪表盘)        │
             │ ├ pipeline 检查     │                        └────────────────────┘
             │ └ 告警规则+日报      │
             └─────────┬──────────┘
                       │ HTTP
                 ┌─────▼──────┐
                 │ 微信推送    │  Server酱 / PushPlus
                 └────────────┘
```

三个可独立运行/测试的单元：

| 单元 | 职责 | 依赖 |
|------|------|------|
| 监控采集与告警（`run_monitor.py` + modules） | 每日快照估值、流程健康检查、告警规则评估、微信推送 | 实盘 db、qlib（仅估值时取价）、微信 HTTP API |
| Web 仪表盘（`live_trading/web/`） | 只读展示 db 里的一切 | 实盘 db、FastAPI |
| 既有链路脚本 | 不改行为；仅在关键步骤补写 `pipeline_events`（可选增强） | 不变 |

Web 与监控采集互不依赖：Web 挂了不影响告警，告警挂了不影响看盘。

## 3. 数据模型（新增 4 张表）

新表由新模块 `monitor_store.py` 负责建表和读写（`CREATE TABLE IF NOT EXISTS`，与 `LiveRecorder` 同风格，写同一个 db 文件）。不修改既有表。

```sql
-- 每日账户快照（净值曲线的数据源）
CREATE TABLE IF NOT EXISTS daily_snapshot (
    date TEXT PRIMARY KEY,              -- YYYY-MM-DD（交易日）
    cash REAL NOT NULL,
    market_value REAL NOT NULL,         -- 持仓按当日收盘价估值
    total_value REAL NOT NULL,          -- cash + market_value
    daily_return REAL,                  -- 相对前一快照；首日为 NULL
    cumulative_return REAL,             -- 相对首日 total_value
    benchmark_close REAL,               -- 基准指数收盘
    benchmark_daily_return REAL,
    benchmark_cumulative_return REAL,
    excess_return REAL,                 -- daily_return - benchmark_daily_return
    position_count INTEGER NOT NULL,
    turnover REAL,                      -- 当日 LIVE 成交额(买+卖)/total_value
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 每日持仓明细快照（positions 表只有最新态，历史靠这张表）
CREATE TABLE IF NOT EXISTS position_snapshot (
    date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    shares INTEGER NOT NULL,
    avg_cost REAL NOT NULL,
    close_price REAL,                   -- 当日未复权收盘价；取价失败为 NULL
    market_value REAL,
    profit REAL,                        -- (close - avg_cost) * shares
    weight REAL,                        -- market_value / total_value
    PRIMARY KEY (date, stock_code)
);

-- 流程健康事件（每个环节每次检查一条）
CREATE TABLE IF NOT EXISTS pipeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,           -- 该事件归属的交易日
    stage TEXT NOT NULL,                -- data_update | publish | fills_import | snapshot
    status TEXT NOT NULL,               -- OK | WARN | FAIL
    message TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pipeline_date ON pipeline_events(trade_date);

-- 告警历史（同日同规则去重的依据）
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    level TEXT NOT NULL,                -- WARN | CRIT
    rule TEXT NOT NULL,                 -- 规则标识，见 §6
    message TEXT NOT NULL,
    channel TEXT,                       -- serverchan | pushplus | none
    sent_ok INTEGER NOT NULL DEFAULT 0, -- 推送是否成功
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE (trade_date, rule)
);
```

**幂等约定**：`daily_snapshot` / `position_snapshot` 按主键 upsert，重跑覆盖；`alerts` 靠 `UNIQUE(trade_date, rule)` 去重——同一天同一规则只推一次微信，重跑 monitor 不会轰炸。

## 4. 模块设计

新增文件全部在 `live_trading/` 下：

```
live_trading/
├── modules/
│   ├── monitor_store.py      # 新表读写（MonitorStore 类）
│   ├── notifier.py           # 微信推送通道抽象
│   ├── snapshot.py           # 快照估值纯函数 + 收益计算
│   └── pipeline_monitor.py   # 流程健康检查 + 账户告警规则
├── scripts/
│   └── run_monitor.py        # 统一 CLI 入口（cron 驱动）
└── web/
    ├── app.py                # FastAPI 应用工厂
    ├── api.py                # 只读 REST API
    └── static/               # SPA 前端（ECharts）
```

### 4.1 `monitor_store.py` — MonitorStore

- 构造入参 `db_path`（与 `LiveRecorder` 指向同一文件），负责建 §3 的 4 张表；
- 提供：`upsert_daily_snapshot(row)`、`upsert_position_snapshots(date, rows)`、`get_snapshots(start, end)`、`get_latest_snapshot()`、`record_pipeline_event(trade_date, stage, status, message)`、`get_pipeline_events(trade_date)`、`try_record_alert(trade_date, level, rule, message) -> bool`（已存在返回 False，实现同日去重）、`mark_alert_sent(trade_date, rule, channel, ok)`、`get_alerts(limit)`；
- 与 `LiveRecorder` 保持连接风格一致（WAL、row_factory、context manager），互不 import。

### 4.2 `notifier.py` — 微信推送

通道抽象 + 两个实现，密钥只走环境变量，**绝不进配置文件或代码**（引以为戒：`paper_trading/modules/alert.py` 目前硬编码了 SMTP 密码，本平台不允许再犯）：

| 通道 | API | 环境变量 |
|------|-----|----------|
| Server酱（推荐） | `POST https://sctapi.ftqq.com/{sendkey}.send`，`title` + `desp`(markdown) | `SERVERCHAN_SENDKEY` |
| PushPlus | `POST https://www.pushplus.plus/send`，`token`/`title`/`content`/`template=markdown` | `PUSHPLUS_TOKEN` |

- 接口：`Notifier.send(title: str, content_md: str) -> bool`；工厂 `create_notifier(monitor_cfg) -> Notifier`，`channel: none` 返回空实现（只记日志），便于测试和干跑；
- 发送失败不抛异常：记 ERROR 日志、`alerts.sent_ok=0`，监控流程继续（推送是尽力而为，db 里的告警记录才是事实源）；
- HTTP 用 `requests`，超时 10s，失败不重试（次日 cron 自然兜底，告警内容也在日报里二次出现）。

### 4.3 `snapshot.py` — 每日估值快照

核心是**纯函数**，方便测试；qlib 取价由 `run_monitor.py` 注入：

```python
def build_snapshot(date, positions, cash, prices, bench_close,
                   prev_snapshot, first_total_value, fills_amount) -> tuple[dict, list[dict]]:
    """返回 (daily_snapshot 行, position_snapshot 行列表)。

    positions: {stock_code: {shares, avg_cost}}  来自 LiveRecorder.get_positions()
    prices:    {stock_code: close_price}         未复权收盘价，缺失的股票 close/market_value 记 NULL
                                                  并在 daily_snapshot.message 外由调用方产生 WARN 事件
    prev_snapshot / first_total_value:            用于 daily_return / cumulative_return
    fills_amount: 当日 LIVE 终态成交额（买+卖绝对值之和），用于 turnover
    """
```

计算规则：

- `market_value = Σ shares × close`；缺价股票按 `avg_cost` 保守估值并触发 WARN（缺价通常意味着数据没更新，本身就是要告警的事）；
- `daily_return = total/prev_total - 1`（无前日快照则 NULL）；`cumulative_return = total/first_total - 1`；
- 基准：qlib 取 `SH000300` 当日 `$close`（指数无需除 factor），同样算日收益与累计；基准的"首日"与账户首个快照日对齐；
- **入金/出金**：本期不建 cash flow 表。人工 `set_cash` 校正或出入金后，当日 `daily_return` 会失真——运维手册要求出入金当天在日报里人工备注，后续如有频繁出入金再建 `cash_flow` 表修正。

### 4.4 `pipeline_monitor.py` — 流程健康 + 账户告警规则

两组纯函数，输入全部显式传参（db 查询结果、当前时刻、配置阈值），返回 `list[Finding]`，`Finding = (stage_or_rule, level, message)`：

- `check_pipeline(stage_ctx) -> list[Finding]`：按运行阶段（§5 的 evening / postmarket / report）检查对应环节；
- `check_account(snapshot_history, thresholds) -> list[Finding]`：日亏、回撤、连亏等账户规则（规则清单见 §6）。

`run_monitor.py` 把 Finding 落成 `pipeline_events` / `alerts`，再交给 notifier。

### 4.5 `run_monitor.py` — CLI 入口

```
python live_trading/scripts/run_monitor.py --config csi300_topk10_live --stage <stage> [--date YYYY-MM-DD]
```

三个 stage 对应三个 cron 时点（详见 §5）：

| stage | 时点 | 做什么 |
|-------|------|--------|
| `postmarket` | T 日 16:00 | 检查 T 日批次回执：已导入、`missing=0`、拒单率；异常即推微信 |
| `report` | T 日 20:30（数据更新后） | 检查 qlib 数据已含 T 日 → 建 T 日快照 → 跑账户告警规则 → 推**每日日报**（无论有无告警） |
| `evening` | T 日 22:00（发布信号后） | 检查下一交易日批次已发布（`batches` 表 + inbox 文件都在）；缺失即推微信 |

- 非交易日（按 qlib 日历）直接退出，只记一条日志；
- `--date` 缺省取当天，可回补历史某天的快照；
- qlib 初始化用 `kernels=1`（只取十几只股票一天的收盘价，量极小；同时规避 stdin/多进程陷阱）；
- 退出码：检查全 OK 返回 0，有 WARN/FAIL 返回非 0（cron 邮件兜底可选）。

### 4.6 `live_trading/web/` — 仪表盘

技术栈与 `paper_trading/web` 一致（FastAPI + uvicorn + 原生 JS SPA + ECharts CDN），**独立副本**，端口默认 8081（paper 用 8080）。只读，不提供任何写操作。

REST API（前缀 `/api`）：

| 端点 | 内容 |
|------|------|
| `GET /overview` | 最新快照（净值/现金/日收益/累计/超额）+ 今日各环节状态灯 + 未读告警数 |
| `GET /nav?start&end` | `daily_snapshot` 序列（净值曲线、基准、超额） |
| `GET /positions` | 当前持仓（`positions` 表 + 最新 `position_snapshot` 的价格/盈亏/权重） |
| `GET /positions/history?date` | 指定日持仓快照 |
| `GET /batches?limit` | 批次列表 + 每批 reconcile（planned/terminal/missing） |
| `GET /batches/{batch_id}/fills` | 单批成交明细 |
| `GET /pipeline?days` | 最近 N 个交易日 × 4 环节的状态矩阵 |
| `GET /alerts?limit` | 告警历史 |

前端 5 个页面：**概览**（净值曲线 vs 基准、今日流程状态灯、持仓饼图）、**持仓**（当前 + 按日回看）、**批次与成交**（批次列表→明细钻取，SIMULATE/LIVE 标签区分）、**流程健康**（日期 × 环节的绿黄红矩阵）、**告警**（历史列表）。

启动方式：`python live_trading/scripts/run_web.py --config csi300_topk10_live [--port 8081]`（薄封装 uvicorn）。

## 5. 每日时序（与既有 cron 合并后的全景）

```cron
# —— 既有 ——
0  18 * * 1-5  <Tushare→qlib 数据更新>
30 21 * * 1-5  run_publish_signals.py --trade-date <T+1> --mode SIMULATE
0  16 * * 1-5  run_import_fills.py

# —— 新增（监控平台）——
0  16 * * 1-5  run_monitor.py --stage postmarket   # 紧随 import 之后（import 15:30 已改 16:00 前完成）
30 20 * * 1-5  run_monitor.py --stage report       # 数据更新完成后、发布信号前
0  22 * * 1-5  run_monitor.py --stage evening      # 发布信号后半小时
```

> `postmarket` 与 `run_import_fills` 的先后：建议把 import 的 cron 保持 16:00，monitor 排 16:10，或在 import cron 行用 `&&` 串联 monitor，避免竞态。开发手册按 `&&` 串联方案落地。

一天的完整故事（T 日）：

1. **16:00** import 回执 → **16:10 postmarket**：T 日批次 missing=0？拒单率正常？→ 异常推微信；
2. **18:00** 数据更新；
3. **20:30 report**：qlib 日历已含 T 日？→ 用 T 日收盘价给持仓估值 → 写 `daily_snapshot` → 日亏/回撤规则 → 推日报微信（净值、日收益、当日成交、告警汇总）；
4. **21:30** 发布 T+1 信号；
5. **22:00 evening**：T+1 批次在 `batches` 表且 inbox 有 `.jsonl + .done`？→ 缺失推微信（当晚还来得及手工补发）。

## 6. 告警规则清单

| 规则标识 | 级别 | 触发条件 | 检查时点 |
|----------|------|----------|----------|
| `PUBLISH_MISSING` | CRIT | 下一交易日无批次记录，或 inbox 缺 `.jsonl`/`.done` | evening |
| `FILLS_MISSING` | CRIT | T 日批次 `reconcile.missing > 0`，或有批次但 fills 为空 | postmarket |
| `REJECT_RATE_HIGH` | WARN | `REJECTED + ERROR` ≥ 当日订单数 × `reject_rate`（默认 0.5） | postmarket |
| `DATA_STALE` | CRIT | qlib 日历最新日期 < T（数据没更新，快照与次日信号都不可信） | report |
| `PRICE_MISSING` | WARN | 快照时有持仓股票取不到收盘价 | report |
| `DAILY_LOSS` | WARN | `daily_return < daily_loss`（默认 -3%） | report |
| `MAX_DRAWDOWN` | CRIT | 快照序列最大回撤 < `max_drawdown`（默认 -10%） | report |
| `CONSECUTIVE_LOSS` | WARN | 连续 `consecutive_loss_days`（默认 5）个交易日亏损 | report |
| `NEGATIVE_POSITION` | CRIT | 账本出现负持仓（导入时已 clamp，此处查 fills 与 positions 矛盾） | postmarket |

- CRIT 一律推微信；WARN 推微信但归并进当日日报（若日报未发时先到，单独推）；
- 同日同规则只推一次（`alerts` 表 UNIQUE 兜底）；
- 阈值全部可在配置 `monitor.thresholds` 覆盖。

## 7. 配置扩展

`live_trading/configs/csi300_topk10_live.yaml` 追加（对既有字段零改动）：

```yaml
monitor:
  benchmark: "SH000300"
  notify:
    channel: "serverchan"        # serverchan | pushplus | none
    daily_report: true           # report 阶段无告警也推日报
  thresholds:
    daily_loss: -0.03
    max_drawdown: -0.10
    consecutive_loss_days: 5
    reject_rate: 0.5

web:
  host: "0.0.0.0"
  port: 8081
```

密钥环境变量（加入 `~/.zshrc`，不进 git）：`SERVERCHAN_SENDKEY` 或 `PUSHPLUS_TOKEN`。

## 8. 错误处理原则

- **监控不得影响交易链路**：monitor / web 任何异常都不能阻塞 publish、import；monitor 内部单条规则抛错时捕获、记 `pipeline_events(FAIL)`、继续跑其余规则；
- **推送尽力而为**：微信 API 失败只记日志和 `sent_ok=0`；db 里的 `alerts` / `pipeline_events` 是事实源，Web 告警页永远可见；
- **快照可重建**：全部快照数据从 `positions`/`fills`/qlib 行情推导而来，`--date` 回补即可重建，误删无损。

## 9. 测试策略

- 纯逻辑单测（pytest，放 `tests/live_trading/`，风格对齐既有测试）：
  - `test_monitor_store.py`：建表、upsert 幂等、告警去重（同日同规则第二次返回 False）；
  - `test_snapshot.py`：估值、日收益/累计/超额、缺价降级、首日边界、turnover；
  - `test_pipeline_monitor.py`：各规则触发/不触发的边界（用假 store 数据和假时钟）；
  - `test_notifier.py`：mock requests，验证 payload、超时、失败不抛异常、`none` 通道；
  - `test_monitor_web_api.py`：FastAPI TestClient + 临时 db 灌数据，冒烟每个端点；
- qlib 取价 glue 不写单测（依赖真实数据），靠 `--date` 手工冒烟；
- 验收：连续跑 3 个真实交易日的三个 stage，微信收到日报、Web 五页有数、故意删掉 inbox 文件能收到 `PUBLISH_MISSING`。

## 10. 分期

| 阶段 | 内容 | 交付判据 |
|------|------|----------|
| Phase 1 | monitor_store + notifier + snapshot + pipeline_monitor + run_monitor CLI + cron | 微信能收到日报和告警，db 里有快照 |
| Phase 2 | Web 仪表盘（API + 前端五页） | 浏览器能看净值曲线、批次明细、流程矩阵 |
| Phase 3（backlog，另立项） | 盘中 QMT 快照回传、实盘 vs 模拟盘偏差对比、滑点/拒单专题分析 | — |

Phase 1 独立可用（没有 Web 也能靠微信 + sqlite3 命令行运维），Phase 2 纯增强。

## 11. 存量问题提示（不在本平台范围内，但应尽快处理）

`paper_trading/modules/alert.py:77` 硬编码了 SMTP 密码明文且已进 git。建议：改环境变量读取、**修改该邮箱授权码**、必要时清理 git 历史。本平台的 notifier 从第一天起密钥只走环境变量。

## 12. 费用与公司行为（2026-07-14 增补，已实现）

### 12.1 交易费用（自动扣减）

规则查证（2026 现行）：佣金双向、与券商协商（含规费）、单笔最低 5 元；印花税 0.05% 仅卖出（2023-08-28 减半）；过户费 0.001% 双向（2022-04-29 起沪深统一）。

实现：`modules/fees.py` 纯函数 `order_total_fee(side, cum_amount, fees)`；`LiveRecorder.apply_fill` 在更新持仓/现金的同一事务里按**订单累计成交额**重算应计费用、扣增量（`fills.applied_fee` 记账）。部分成交多次回执时最低佣金全订单只收一次，重复导入幂等。费率在 live 配置 `fees:` 段。

### 12.2 资金流水（cash_flows 表）

新表 `cash_flows(id, trade_date, flow_type, stock_code, amount, note, dedup_key UNIQUE, created_at)`。`record_cash_flow()` 入流水并同步调 `account_state.cash`，`dedup_key` 保证幂等。

类型语义：只有 `DEPOSIT / WITHDRAW` = 外部出入金（日收益剔除）；`CORRECTION` 是投资相关对账调整，`DIVIDEND / DIVIDEND_TAX / BONUS_SHARES` = 公司行为（计入收益）。手工入口 `scripts/record_cash_flow.py`；Web 提供资金流水（`GET /api/cashflows`）和公司行为子账（`GET /api/corporate-actions`）。

### 12.3 分红/送股与红利税

规则查证（财税〔2015〕101号，2015-09-08 起）：持股 ≤1 月税负 20%、1 月~1 年 10%、>1 年免；派发时不预扣，**卖出时**由中国结算按先进先出计算、券商从资金账户补扣。

实现：`modules/corporate_actions.py` 规范化 Tushare 事件，`LiveRecorder.corporate_actions` 子账原子结算。report 阶段按 `ex_date=当日` 查询实施事件，权益股数只取 `record_date` 的 `position_snapshot`；缺快照发 `CORP_ACTION_ENTITLEMENT_MISSING`，不按除息日当前持仓猜测。除息日确认税前应收、待上市股数与 20% 红利税准备；`pay_date` 才转现金并写 `DIVIDEND`；`div_listdate` 才转普通持仓并写 `BONUS_SHARES`。结算日期缺失时只挂子账并告警，不回退到除息日。券商卖出时实扣税通过 `settle_dividend_tax`/CLI 按事件结算准备金并写 `DIVIDEND_TAX`。事件主键、结算标志和流水 dedup key 保证重跑幂等。

### 12.4 快照口径变化

- `build_snapshot` 新增 `external_flow` / `fees` / `receivables` / `pending_shares` / `tax_provision` 参数：`total_value = cash + market_value + receivables + pending_market_value - tax_provision`，`daily_return = (total_value - external_flow) / prev_total - 1`；
- 累计收益改为按日收益**链式累乘**（出入金只改基数不计业绩），不再用 `total/first_total`；
- `daily_snapshot` 新增 `fees` / `external_flow` / `receivables` / `pending_market_value` / `tax_provision` 列（ALTER TABLE 自动迁移），日报与 Web 概览展示公司行为资产和准备金。
