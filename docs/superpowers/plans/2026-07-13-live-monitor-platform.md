# 实盘策略监控平台 开发手册（实现计划）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **状态：全部任务已完成**（2026-07-13）。测试 `tests/live_trading/` 113 项全通过；已回补 2026-07-13 快照并完成故障注入验收。

**Goal:** 落地 [设计定稿 v1.0](../specs/2026-07-13-live-monitor-platform-design.md)：实盘账户每日快照、流程健康监控、微信告警与日报、只读 Web 仪表盘。

**Architecture:** 全部代码在 `live_trading/` 下，与 paper_trading 完全独立。新增 4 张监控表写入既有实盘 db；`run_monitor.py` 由 cron 在三个时点驱动（postmarket/report/evening）；`live_trading/web/` 为 FastAPI + ECharts 只读仪表盘。协议与表结构以设计文档为准（冲突时以设计文档为准）。

**Tech Stack:** Python 3.12、pytest、SQLite（WAL）、FastAPI + uvicorn、requests、ECharts 5（CDN）、原生 JS SPA。

**约定：**

- 模块风格对齐 `live_trading/modules/fill_importer.py`（logging、`Path`、sqlite3 WAL、contextmanager 连接）
- 测试放 `tests/live_trading/`，测试命令：`/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -v`
- 涉及 qlib 取数的 glue 代码不写单测，纯逻辑必须有单测
- 每完成一个 Task 跑对应测试并 commit；全部完成后全量跑 `tests/live_trading/`
- **密钥只走环境变量**（`SERVERCHAN_SENDKEY` / `PUSHPLUS_TOKEN`），任何密钥出现在代码或 yaml 里都算实现失败
- 触发 qlib 取数的临时脚本严禁 heredoc/stdin 运行（见 `.cursor/rules/qlib-shell-multiprocessing.mdc`），monitor 内 `qlib.init(kernels=1)`

**文件总览：**

| 动作 | 路径 | 职责 |
|------|------|------|
| Create | `live_trading/modules/monitor_store.py` | 4 张监控表读写（MonitorStore） |
| Create | `live_trading/modules/notifier.py` | 微信推送通道（Server酱/PushPlus/none） |
| Create | `live_trading/modules/snapshot.py` | 快照估值纯函数 |
| Create | `live_trading/modules/pipeline_monitor.py` | 流程健康 + 账户告警规则（纯函数） |
| Create | `live_trading/scripts/run_monitor.py` | 监控 CLI（stage 驱动 + qlib glue） |
| Create | `live_trading/scripts/run_web.py` | Web 启动入口 |
| Create | `live_trading/web/{__init__,app,api}.py` + `static/` | 只读仪表盘 |
| Modify | `live_trading/configs/csi300_topk10_live.yaml` | 追加 `monitor` / `web` 段 |
| Modify | `live_trading/README.md` | 追加监控平台运维章节 |
| Create | `tests/live_trading/test_{monitor_store,snapshot,pipeline_monitor,notifier,monitor_web_api}.py` | 单测 |

---

## Phase 1：监控采集 + 告警

### Task 1: monitor_store（监控表读写）

**Files:** Create `live_trading/modules/monitor_store.py`, `tests/live_trading/test_monitor_store.py`

- [ ] 建表 SQL 逐字来自设计文档 §3（`daily_snapshot` / `position_snapshot` / `pipeline_events` / `alerts`，含两个索引/UNIQUE 约束），`MonitorStore(db_path)` 构造时 `executescript` 建表
- [ ] 连接管理复制 `LiveRecorder` 的 `_conn()` contextmanager 模式（WAL、Row、commit/rollback）
- [ ] 方法与签名（设计文档 §4.1）：

```python
class MonitorStore:
    def upsert_daily_snapshot(self, row: dict) -> None: ...          # INSERT OR REPLACE by date
    def upsert_position_snapshots(self, date: str, rows: list[dict]) -> None:
        ...                                                           # 先 DELETE date 再批量插入
    def get_snapshots(self, start: str = None, end: str = None) -> list[dict]: ...
    def get_latest_snapshot(self) -> dict | None: ...
    def get_first_snapshot(self) -> dict | None: ...
    def get_position_snapshots(self, date: str) -> list[dict]: ...
    def record_pipeline_event(self, trade_date, stage, status, message) -> None: ...
    def get_pipeline_events(self, trade_date: str = None, days: int = 10) -> list[dict]: ...
    def try_record_alert(self, trade_date, level, rule, message) -> bool:
        ...  # INSERT OR IGNORE；rowcount==0 返回 False（同日同规则去重）
    def mark_alert_sent(self, trade_date, rule, channel, ok: bool) -> None: ...
    def get_alerts(self, limit: int = 50) -> list[dict]: ...
```

- [ ] 测试（tmp_path 建临时 db）：建表成功且可重复构造；`upsert_daily_snapshot` 同日期二次写覆盖不报错；`upsert_position_snapshots` 重跑不产生重复行；`try_record_alert` 首次 True、同日同规则第二次 False、不同日期同规则 True；`get_snapshots` 按日期升序
- [ ] 跑测试通过，commit

### Task 2: notifier（微信推送）

**Files:** Create `live_trading/modules/notifier.py`, `tests/live_trading/test_notifier.py`

- [ ] 抽象基类 `Notifier.send(title, content_md) -> bool`；实现 `ServerChanNotifier`（`POST https://sctapi.ftqq.com/{sendkey}.send`，data=`{"title":..., "desp":...}`）、`PushPlusNotifier`（`POST https://www.pushplus.plus/send`，json=`{"token":..., "title":..., "content":..., "template":"markdown"}`）、`NullNotifier`（记日志返回 True）
- [ ] 工厂 `create_notifier(monitor_cfg) -> Notifier`：按 `notify.channel` 分发；serverchan 读 env `SERVERCHAN_SENDKEY`、pushplus 读 env `PUSHPLUS_TOKEN`；channel 有效但 env 缺失 → log ERROR 并降级 NullNotifier（**不抛异常**，监控不能因推送配置缺失而挂）
- [ ] requests 超时 10s；任何异常（网络/非 200/业务 code 非 0）→ log ERROR、返回 False，不重试不抛出
- [ ] 测试（`unittest.mock.patch("requests.post")`）：serverchan URL 含 sendkey 且 data 字段正确；pushplus json 字段正确；HTTP 500 → False 且不抛；`requests.post` 抛 `ConnectionError` → False 且不抛；channel=none → NullNotifier；env 缺失 → NullNotifier
- [ ] 跑测试通过，commit

### Task 3: snapshot（估值纯函数）

**Files:** Create `live_trading/modules/snapshot.py`, `tests/live_trading/test_snapshot.py`

- [ ] 实现设计文档 §4.3 的 `build_snapshot(...)`，返回 `(daily_row, position_rows)`：

```python
def build_snapshot(date, positions, cash, prices, bench_close,
                   prev_snapshot, first_total_value, fills_amount):
    # market_value: Σ shares×close；缺价股票用 avg_cost 估值，close_price/market_value 记 None→
    #   position 行 close_price=None, market_value=shares*avg_cost, profit=0, 并在返回值外由调用方告警
    # daily_return: prev_snapshot 存在时 total/prev_total-1，否则 None
    # cumulative_return: first_total_value 存在时 total/first-1，首个快照日 = 0.0
    # benchmark_*: bench_close 与 prev_snapshot["benchmark_close"] 同理；bench_close 为 None 时全记 None
    # excess_return: 两者都非 None 时相减，否则 None
    # turnover: fills_amount / total_value（total==0 时 None）
    # weight: 每只 market_value / total_value
```

- [ ] 另实现 `sum_live_fills_amount(fills: list[dict]) -> float`：过滤 `mode=="LIVE"` 且 `status in {"FILLED","PARTIAL"}`，Σ `filled_qty × avg_price`
- [ ] 测试用例：两只持仓正常估值与权重和≈1；首日（无 prev）daily_return=None 且 cumulative=0；次日收益计算数值断言；缺价股票按 avg_cost 降级且返回的缺价列表非空；基准缺失不影响账户字段；turnover 计算；空持仓纯现金
- [ ] 跑测试通过，commit

### Task 4: pipeline_monitor（检查规则纯函数）

**Files:** Create `live_trading/modules/pipeline_monitor.py`, `tests/live_trading/test_pipeline_monitor.py`

- [ ] 定义 `Finding = namedtuple("Finding", "rule level message")`，level ∈ {"WARN","CRIT"}；规则标识与级别逐条对齐设计文档 §6 表格
- [ ] `check_evening(next_trade_date, batch, inbox_files) -> list[Finding]`：batch 为 None，或 `signal_{batch_id}.jsonl`/`.done` 任一不在 inbox_files → `PUBLISH_MISSING`(CRIT)
- [ ] `check_postmarket(trade_date, batches, reconciles, fills, reject_rate) -> list[Finding]`：
  - 当日无批次 → 不告警（可能停发，evening 已管）；有批次但 reconcile `missing>0` 或 fills 空 → `FILLS_MISSING`(CRIT)
  - `REJECTED+ERROR` 数 ≥ 订单数×reject_rate → `REJECT_RATE_HIGH`(WARN)
  - fills 汇总卖出量 > 该股导入前持仓（调用方传入矛盾标志）→ `NEGATIVE_POSITION`(CRIT)；简化实现：扫描 import 日志不可靠，直接检查 `positions` 表构造时 clamp 警告由调用方从 fills/positions 推断——本期实现为：任一股票 `Σ LIVE SELL filled` 当日 > 昨日快照 shares → CRIT
- [ ] `check_report(trade_date, latest_calendar_date, missing_price_codes) -> list[Finding]`：`latest_calendar_date < trade_date` → `DATA_STALE`(CRIT)；missing_price_codes 非空 → `PRICE_MISSING`(WARN)
- [ ] `check_account(snapshots, thresholds) -> list[Finding]`：`DAILY_LOSS` / `MAX_DRAWDOWN`（expanding max 回撤，含首日之前 first_total 为峰值起点）/ `CONSECUTIVE_LOSS`，逻辑参考 `paper_trading/modules/alert.py` 但输入为快照 list[dict]，无 pandas 依赖也可（直接循环）
- [ ] 测试：每条规则至少一个触发 + 一个不触发用例；阈值边界（恰好等于阈值不触发，严格小于才触发，与 paper 一致）；连亏窗口不足 N 天不触发
- [ ] 跑测试通过，commit

### Task 5: run_monitor CLI + 配置扩展

**Files:** Create `live_trading/scripts/run_monitor.py`; Modify `live_trading/configs/csi300_topk10_live.yaml`

- [ ] yaml 追加设计文档 §7 的 `monitor` / `web` 段（既有字段零改动）；确认 `tests/live_trading/test_live_config.py` 仍通过
- [ ] CLI：`--config <id> --stage {postmarket,report,evening} [--date YYYY-MM-DD]`，结构对齐 `run_publish_signals.py`（PROJECT_ROOT sys.path、logging.basicConfig、`load_live_config`）
- [ ] 公共流程：qlib init（`kernels=1`）→ 取日历；`--date`（缺省今天）不是交易日 → log info 后 `sys.exit(0)`
- [ ] `stage=evening`：由日历算下一交易日 → `LiveRecorder.get_batch`-等价查询（按 trade_date 查 batches）+ `Path(bridge_root)/"inbox"` 文件列表 → `check_evening`；bridge 挂载点不存在本身就是 CRIT（`PUBLISH_MISSING`，message 注明 mount missing）
- [ ] `stage=postmarket`：查当日 batches、逐批 `FillImporter.reconcile`、查 fills、取昨日 `position_snapshot` → `check_postmarket`
- [ ] `stage=report`：`check_report`（日历最新日 vs date）→ 通过后取持仓收盘价（复用 `run_publish_signals.get_prev_close` 的 `$close/$factor` 写法，基准 `SH000300` 直接 `$close`）→ `sum_live_fills_amount`（查当日 fills）→ `build_snapshot` → `MonitorStore.upsert_*` → `check_account` → 组装日报 markdown（净值/日收益/累计/超额/持仓数/当日成交笔数/告警列表）→ `notify.daily_report` 为 true 时必发
- [ ] Finding 落库与推送（三个 stage 共用）：每条 Finding → `record_pipeline_event` + `try_record_alert`；`try_record_alert` 返回 True 才调 `notifier.send`，随后 `mark_alert_sent`；单条规则/推送异常 try/except 记 FAIL 事件后继续
- [ ] 全 OK 时也写一条 `pipeline_events(stage, "OK")`；退出码：存在 CRIT/FAIL → 2，仅 WARN → 1，全 OK → 0
- [ ] 冒烟（不依赖交易日）：`--help`；用 `--date` 指定历史交易日跑 `report`，确认 db 出现快照行、`channel: none` 时日志打出日报全文
- [ ] commit

### Task 6: crontab 与运维文档

**Files:** Modify `live_trading/README.md`

- [ ] README 追加「监控平台」章节：环境变量配置（Server酱 sendkey 获取步骤）、三个 stage 含义、crontab 建议（import 与 postmarket 用 `&&` 串联避免竞态）：

```cron
0  16 * * 1-5  cd /Users/yuxianqi/Project/qlib && /opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_import_fills.py --config csi300_topk10_live >> live_trading/logs/import_cron.log 2>&1 && /opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_monitor.py --config csi300_topk10_live --stage postmarket >> live_trading/logs/monitor_cron.log 2>&1
30 20 * * 1-5  cd /Users/yuxianqi/Project/qlib && /opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_monitor.py --config csi300_topk10_live --stage report >> live_trading/logs/monitor_cron.log 2>&1
0  22 * * 1-5  cd /Users/yuxianqi/Project/qlib && /opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_monitor.py --config csi300_topk10_live --stage evening >> live_trading/logs/monitor_cron.log 2>&1
```

- [ ] 写明出入金/人工 set_cash 当天日报 daily_return 失真的注意事项（设计文档 §4.3）
- [ ] commit（Phase 1 完成）

## Phase 2：Web 仪表盘

### Task 7: 只读 REST API

**Files:** Create `live_trading/web/__init__.py`, `live_trading/web/app.py`, `live_trading/web/api.py`, `tests/live_trading/test_monitor_web_api.py`

- [ ] `app.py`：`create_app(config, project_root)` 工厂，结构复制 `paper_trading/web/app.py`（include_router prefix=`/api`、mount static）；title="Live Trading Monitor"
- [ ] `api.py`：8 个端点逐一实现设计文档 §4.6 表格，数据源 `LiveRecorder` + `MonitorStore` + `FillImporter.reconcile`；单实例即可（暂不做 paper 那套多 instance 切换——live 目前只有一个配置，YAGNI）
- [ ] `/overview` 的"今日环节状态灯"：`get_pipeline_events(trade_date=今天)` 按 stage 取最新一条 status
- [ ] 测试：TestClient + tmp_path 临时 db，用 `LiveRecorder`/`MonitorStore` 灌 2 天假数据（1 批次 2 fills、2 天快照、几条 events/alerts），断言每个端点 200 且关键字段存在、`/nav` 长度为 2、`/batches/{id}/fills` 过滤正确
- [ ] 跑测试通过，commit

### Task 8: 前端 SPA

**Files:** Create `live_trading/web/static/index.html`, `static/js/app.js`, `static/css/style.css`; Create `live_trading/scripts/run_web.py`

- [ ] 骨架参考 `paper_trading/web/static/`（侧边导航 + 页面容器 + ECharts CDN + fetch 封装 + 60s 自动刷新），但页面按设计文档 §4.6 五页实现：
  - **概览**：净值曲线（账户/基准/超额，ECharts line）+ 关键数字卡（总资产/日收益/累计/超额/持仓数）+ 今日 4 环节状态灯 + 最近告警 3 条
  - **持仓**：当前持仓表（代码/数量/成本/现价/市值/盈亏/权重），日期选择器回看 `position_snapshot`
  - **批次与成交**：批次表（batch_id/trade_date/mode 徽标/planned/terminal/missing），点击展开该批 fills 明细
  - **流程健康**：最近 10 个交易日 × 4 stage 的绿黄红格子矩阵（OK/WARN/FAIL），格子 hover 显示 message
  - **告警**：时间倒序表（level 徽标/rule/message/推送状态）
- [ ] `run_web.py`：`--config <id> [--host] [--port]`，读 yaml `web` 段做缺省，uvicorn 启动（参考 `paper_trading/paper_trading.py` 的 web 子命令实现）
- [ ] 手工冒烟：起服务后浏览器过五页；无快照数据时页面显示空态提示而非报错
- [ ] commit

### Task 9: 收尾验收

- [ ] 全量 `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -v` 通过（含既有测试无回归）
- [ ] `ReadLints` 检查全部新文件
- [ ] 端到端演练（可用历史日期）：`run_monitor --stage report --date <历史交易日>` → 起 web → 概览页出现净值点；临时把 channel 切 serverchan 发一条真实测试推送后切回
- [ ] 故障注入验收：改 bridge_root 指向不存在路径跑 evening → 收到 `PUBLISH_MISSING` CRIT、`alerts` 表有记录、重跑不重复推送
- [ ] 更新 `docs/superpowers/specs/2026-07-13-live-monitor-platform-design.md` 状态标注"已实现 Phase 1/2"，commit

---

## 自检记录

- 规格覆盖：§3 四表→Task 1；§4.2→Task 2；§4.3→Task 3；§4.4/§6 九条规则→Task 4；§4.5/§5/§7→Task 5–6；§4.6→Task 7–8；§9 测试与验收→各 Task + Task 9。§10 Phase 3 为 backlog 无任务，符合预期。
- 命名一致性：`MonitorStore`/`Finding`/`build_snapshot`/`create_notifier` 各 Task 间引用一致；规则标识与设计文档 §6 表格逐字一致。
