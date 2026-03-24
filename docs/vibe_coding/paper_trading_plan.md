# 模拟盘系统需求文档

## 一、项目背景

基于已在 notebook (`examples/workflow_by_code_230915_fea_v2_100w.ipynb`) 中训练完成的 LGBModel 模型和 TopkDropoutStrategy 交易策略，搭建一套**自动化模拟盘系统**，实现每日定时更新数据、生成交易信号、模拟执行交易、记录账户状态的完整闭环。

### 现有模型与策略概况


| 配置项  | 值                                       |
| ---- | --------------------------------------- |
| 模型   | LGBModel (LightGBM)                     |
| 特征   | Alpha158 (158个因子)                       |
| 标签   | `Ref($close, -2) / Ref($close, -1) - 1` |
| 股票池  | CSI300 (沪深300成分股)                       |
| 基准   | SH000300                                |
| 策略   | TopkDropoutStrategy (topk=10, n_drop=2) |
| 训练区间 | 2003-01-02 ~ 2020-01-10                 |
| 验证区间 | 2020-01-13 ~ 2023-09-15                 |
| 测试区间 | 2023-09-18 ~ 2026-03-10                 |


### 现有基础设施

- **数据源**：Tushare Pro，增量更新脚本已就绪 (`scripts/data_collector/tushare/collector.py`)
- **数据更新 crontab**：`0 18 * * 1-5 /home/yuzai/qlib/scripts/data_collector/tushare/run_update_to_bin.sh`
- **qlib 数据目录**：`~/.qlib/qlib_data/cn_data`
- **模型产物**：存储在 MLflow 实验 (`examples/mlruns/`) 中

### 仓库现有模拟盘工具评估

qlib 仓库提供了两套在线服务相关模块：

1. `**qlib/workflow/online/`**：OnlineManager + RollingStrategy，偏重在线预测更新与信号生成，适合滚动训练场景。
2. `**qlib/contrib/online/**`：UserManager + Operator，设计了完整的 generate → execute → update 流程，但**依赖缺失**（缺少 `executor` 模块，`update_account` 未定义），实际无法直接运行。

**结论**：现有工具均无法直接满足需求，需要**自建模拟盘系统**，但可复用 qlib 的底层组件（Exchange、Account、SimulatorExecutor、TopkDropoutStrategy 等）。

---

## 二、系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                     定时调度层 (crontab)                      │
│  ┌────────────────┐   ┌─────────────────────────────────┐   │
│  │ 18:00 数据更新  │──→│ 21:00 模拟盘主流程               │   │
│  │ run_update_     │   │ paper_trading.py daily           │   │
│  │ to_bin.sh       │   │                                 │   │
│  └────────────────┘   └─────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
  │  信号生成模块  │   │  交易执行模块  │   │  账户与记录模块    │
  │              │   │              │   │                  │
  │ - 加载模型    │   │ - 订单生成    │   │ - 账户状态管理     │
  │ - 数据准备    │   │ - 涨跌停检查  │   │ - 持仓跟踪        │
  │ - 预测打分    │   │ - 模拟撮合    │   │ - 交易记录        │
  │ - 排序选股    │   │ - 成本计算    │   │ - 盈亏计算        │
  └──────────────┘   └──────────────┘   └──────────────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼
                    ┌──────────────────┐
                    │   持久化存储层     │
                    │                  │
                    │ - SQLite 数据库   │
                    │ - 日志文件        │
                    └──────────────────┘
                              ▲
                              │ 读取
                    ┌──────────────────┐
                    │  Web Dashboard   │
                    │  (FastAPI)       │
                    │                  │
                    │ - 账户概览       │
                    │ - 持仓/交易记录  │
                    │ - 绩效分析图表   │
                    │ - 系统状态监控   │
                    └──────────────────┘
```

---

## 三、核心功能需求

### 3.1 定时调度与流程编排

#### 3.1.1 每日自动执行

- 数据爬取 crontab（已有）：`0 18 * * 1-5`
- 模拟盘主流程 crontab（新增）：`0 21 * * 1-5`，在数据更新完成后执行（数据爬取约需 2+ 小时）
- 模拟盘脚本需有**前置检查**：验证当日数据已更新到 qlib bin 中，否则等待/重试/报错

#### 3.1.2 流程编排（每日 daily_routine）

> **核心原则**：T 日的交易必须使用 T-1 日生成的预测信号，绝不能使用 T 日的预测结果。为确保这一点，每日流程先执行交易（消费昨日信号），再生成新预测（供明日使用）。

每个交易日（T 日 21:00）执行以下步骤：

**阶段一：交易执行（使用 T-1 日的预测信号）**

1. **数据就绪检查**：确认 `calendars/day.txt` 中包含 T 日日期
2. **幂等性检查**：检查 T 日是否已执行过，已执行则跳过
3. **加载 T-1 日预测信号**：从数据库读取昨日保存的预测分数
4. **加载昨日持仓**：获取 T-1 日收盘后的持仓状态
5. **交易决策**：基于 T-1 预测信号，TopkDropoutStrategy 生成买卖订单
6. **模拟撮合**：以 T 日收盘价模拟成交，考虑涨跌停、手续费
7. **账户更新**：更新持仓、资金余额
8. **记录保存**：保存订单、持仓快照、账户摘要到数据库

**阶段二：信号预测（为 T+1 日生成信号）**

9. **准备特征数据**：使用 Alpha158 handler 加载包含 T 日的最新数据
10. **模型预测**：对股票池所有标的打分
11. **保存预测结果**：将 T 日预测信号存入数据库，供 T+1 日交易使用

**阶段三：日终处理**

12. **检查告警条件**：单日亏损、回撤等
13. **生成当日摘要日志**

> **特殊情况：首次建仓日（2026-03-11）**：由于没有 T-1 日的预测信号，init 命令会先执行一次预测并保存信号，然后首日 daily 流程正常使用该信号进行建仓。

#### 3.1.3 容错机制

- 非交易日自动跳过
- 数据更新失败时不执行交易，发送告警
- 执行过程出错时回滚当日操作，保持账户一致性
- 支持**断点续跑**：若某日因故未执行，次日可补跑缺失日期

### 3.2 信号生成模块

#### 3.2.1 模型加载

- 从 MLflow 实验目录加载已训练好的 LGBModel
- 支持指定实验名称和 Recorder ID
- 模型加载后缓存，避免每日重复加载

#### 3.2.2 数据准备

- 使用 `Alpha158` handler 准备最新特征数据
- Alpha158 使用 rolling windows `[5, 10, 20, 30, 60]`，最大回看 60 个交易日
- handler 的 `start_time` 需要在预测目标日期之前留出足够的回看窗口（建议至少 120 个交易日，约半年），与 notebook 中 `start_time: "2003-01-02"` 的做法一致——直接从数据起始日加载即可，无需额外设置 lookback 参数
- 自动对齐 CSI300 成分股列表

#### 3.2.3 预测与排序

- 对股票池内所有标的进行预测打分
- 输出格式：`pd.Series`，index 为 `(datetime, instrument)`，values 为预测分数
- 保存每日预测结果供后续分析

### 3.3 交易执行模块

#### 3.3.1 交易策略

- 使用 `TopkDropoutStrategy`，参数与 notebook 一致：
  - `topk`: 10（持仓上限 10 只）
  - `n_drop`: 2（每日最多换仓 2 只）
- 策略根据当前持仓和最新预测，生成买入/卖出订单

#### 3.3.2 交易约束


| 约束项    | 值      | 说明                    |
| ------ | ------ | --------------------- |
| 成交价    | close  | 收盘价成交                 |
| 涨跌停阈值  | 0.095  | 涨跌幅超过 9.5% 视为涨跌停，不可交易 |
| 开仓费率   | 0.0005 | 买入手续费 0.05%           |
| 平仓费率   | 0.0015 | 卖出手续费 0.15%（含印花税）     |
| 最低手续费  | 5 元    | 单笔最低 5 元              |
| 最小交易单位 | 100 股  | A 股按手交易               |


#### 3.3.3 异常处理

- 停牌股票：跳过，保持持仓不变
- 涨停买不进：跳过买入，资金保留
- 跌停卖不出：跳过卖出，继续持有
- ST / *ST 股票：若已持有可卖出，但不新买入（可选配置）

### 3.4 账户管理模块

#### 3.4.1 账户初始化

- 初始资金：1,000,000 元（100万）
- 建仓日期：2026-03-11
- 初始持仓：空仓

#### 3.4.2 账户状态追踪

每个交易日记录以下信息：


| 字段                          | 说明               |
| --------------------------- | ---------------- |
| date                        | 交易日期             |
| cash                        | 现金余额             |
| total_value                 | 账户总资产（现金 + 持仓市值） |
| market_value                | 持仓总市值            |
| daily_return                | 当日收益率            |
| cumulative_return           | 累计收益率            |
| benchmark_return            | 基准当日收益率          |
| benchmark_cumulative_return | 基准累计收益率          |
| excess_return               | 超额收益（累计）         |
| position_count              | 持仓股票数量           |
| turnover                    | 当日换手率            |


#### 3.4.3 持仓管理

每个交易日记录持仓明细：


| 字段            | 说明          |
| ------------- | ----------- |
| date          | 日期          |
| instrument    | 股票代码        |
| name          | 股票名称        |
| shares        | 持有股数        |
| cost_price    | 成本价（含手续费分摊） |
| current_price | 当前价格        |
| market_value  | 市值          |
| profit        | 浮动盈亏金额      |
| profit_rate   | 浮动盈亏比例      |
| weight        | 占总资产比例      |
| holding_days  | 持有天数        |


### 3.5 交易记录模块

#### 3.5.1 订单记录


| 字段            | 说明                                      |
| ------------- | --------------------------------------- |
| order_id      | 订单唯一ID                                  |
| date          | 订单日期                                    |
| instrument    | 股票代码                                    |
| name          | 股票名称                                    |
| direction     | BUY / SELL                              |
| target_shares | 目标股数                                    |
| filled_shares | 实际成交股数                                  |
| price         | 成交价格                                    |
| amount        | 成交金额                                    |
| commission    | 手续费                                     |
| status        | FILLED / PARTIAL / REJECTED / CANCELLED |
| reject_reason | 拒绝原因（涨跌停/停牌等）                           |


#### 3.5.2 每日交易摘要


| 字段               | 说明       |
| ---------------- | -------- |
| date             | 交易日期     |
| buy_count        | 买入笔数     |
| sell_count       | 卖出笔数     |
| buy_amount       | 买入总金额    |
| sell_amount      | 卖出总金额    |
| total_commission | 总手续费     |
| net_inflow       | 资金净流入/流出 |


### 3.6 持久化存储

#### 3.6.1 SQLite 数据库

数据库文件：`paper_trading/paper_trading.db`

表结构：

- `account_summary`：每日账户摘要
- `positions`：每日持仓明细
- `orders`：订单记录
- `predictions`：每日预测分数
- `trade_summary`：每日交易摘要
- `stock_names`：股票代码 → 名称映射表
- `system_log`：系统运行日志

#### 3.6.2 股票名称映射

- 初始化时通过 Tushare `stock_basic` 接口（`pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')`）拉取全量A股名称映射
- 存入 `stock_names` 表，字段：`ts_code`（Tushare格式如 600000.SH）、`instrument`（qlib格式如 SH600000）、`name`（股票名称如"浦发银行"）
- 定期更新（如每周一次），处理新上市、更名等情况
- Web 页面和报告中所有出现股票代码的位置，均同时展示股票名称

#### 3.6.3 日志文件

- 路径：`paper_trading/logs/YYYY-MM-DD.log`
- 记录级别：INFO（正常流程）、WARNING（异常但可继续）、ERROR（需要介入）
- 格式：`[时间] [级别] [模块] 消息`

### 3.7 报告与可视化

#### 3.7.1 每日报告

每日执行完成后输出文本摘要到日志，包含：

- 当日买卖详情
- 账户资产变化
- 持仓概况
- 与基准的对比

#### 3.7.2 绩效报告（按需生成）

提供一个脚本/命令，可随时生成阶段性报告：


| 指标    | 说明                |
| ----- | ----------------- |
| 累计收益率 | 总收益 / 初始资金        |
| 年化收益率 | 按实际运行天数折算         |
| 最大回撤  | 期间最大回撤幅度          |
| 夏普比率  | 风险调整后收益           |
| 信息比率  | 相对基准的超额收益 / 跟踪误差  |
| 胜率    | 盈利交易日 / 总交易日      |
| 盈亏比   | 平均盈利 / 平均亏损       |
| 换手率   | 平均日换手率            |
| 超额收益  | 相对 SH000300 的超额收益 |


可视化图表（Matplotlib / HTML 报告）：

- 净值曲线 vs 基准
- 超额收益曲线
- 回撤曲线
- 持仓集中度变化
- 每日盈亏分布

### 3.8 告警与通知

#### 3.8.1 邮件告警（复用现有机制）

与 `run_update_to_bin.sh` 中的邮件告警一致，在以下情况发送告警：

- 数据更新失败
- 模拟盘脚本执行出错
- 单日亏损超过阈值（如 -3%）
- 最大回撤超过阈值（如 -10%）
- 连续亏损天数超过阈值（如 5 天）

#### 3.8.2 日志级别告警

- ERROR 级别日志自动触发邮件通知

---

## 四、目录结构

```
paper_trading/                          # 模拟盘根目录
├── config.yaml                         # 配置文件
├── paper_trading.py                    # 主程序入口
├── run_daily.sh                        # crontab 调用的 shell 脚本
├── modules/
│   ├── __init__.py
│   ├── signal_generator.py             # 信号生成模块
│   ├── order_manager.py                # 订单管理模块
│   ├── execution_engine.py             # 模拟执行引擎
│   ├── account_manager.py              # 账户管理模块
│   ├── recorder.py                     # 数据记录模块
│   ├── reporter.py                     # 报告生成模块
│   └── alert.py                        # 告警模块
├── data/
│   └── paper_trading.db                # SQLite 数据库
├── logs/                               # 日志目录
│   └── YYYY-MM-DD.log
└── reports/                            # 报告输出目录
    └── report_YYYYMMDD.html
```

---

## 五、配置文件设计

```yaml
# paper_trading/config.yaml

# ========== 基本设置 ==========
paper_trading:
  name: "CSI300_TopkDropout_100w"
  start_date: "2026-03-11"
  initial_cash: 1000000

# ========== 数据设置 ==========
data:
  qlib_dir: "~/.qlib/qlib_data/cn_data"
  region: "cn"
  instruments: "csi300"
  benchmark: "SH000300"

# ========== 模型设置 ==========
model:
  experiment_name: "train_model"
  experiment_id: "745738095260835211"
  recorder_id: "d3f5db20d9af4e70addfefc62fafdf54"
  model_class: "qlib.contrib.model.gbdt.LGBModel"
  mlruns_dir: "examples/mlruns"  # MLflow 实验目录（相对仓库根目录）

# ========== 特征设置 ==========
handler:
  class: "Alpha158"
  module: "qlib.contrib.data.handler"
  # Alpha158 rolling windows: [5, 10, 20, 30, 60]，最大回看60交易日
  # start_time 取数据起始日即可，与 notebook 训练时一致
  start_time: "2003-01-02"

# ========== 股票名称映射 ==========
stock_names:
  source: "tushare"           # 通过 Tushare stock_basic 接口获取
  update_interval_days: 7     # 每 7 天自动更新一次

# ========== 策略设置 ==========
strategy:
  class: "TopkDropoutStrategy"
  topk: 10
  n_drop: 2

# ========== 交易设置 ==========
exchange:
  freq: "day"
  deal_price: "close"
  limit_threshold: 0.095
  open_cost: 0.0005
  close_cost: 0.0015
  min_cost: 5
  trade_unit: 100     # A股最小交易单位

# ========== 存储设置 ==========
storage:
  db_path: "paper_trading/data/paper_trading.db"
  log_dir: "paper_trading/logs"
  report_dir: "paper_trading/reports"

# ========== 告警设置 ==========
alert:
  enabled: true
  email: ${ALERT_EMAIL}
  daily_loss_threshold: -0.03     # 单日亏损 > 3% 告警
  max_drawdown_threshold: -0.10   # 最大回撤 > 10% 告警
  consecutive_loss_days: 5        # 连续亏损天数告警
```

---

## 六、命令行接口

```bash
# 初始化模拟盘（创建数据库、加载模型、建仓）
python paper_trading/paper_trading.py init

# 执行每日例行任务（由 crontab 调用）
python paper_trading/paper_trading.py daily

# 补跑指定日期
python paper_trading/paper_trading.py run --date 2026-03-15

# 批量补跑日期区间
python paper_trading/paper_trading.py run --start 2026-03-11 --end 2026-03-15

# 查看当前账户状态
python paper_trading/paper_trading.py status

# 查看指定日期持仓
python paper_trading/paper_trading.py positions --date 2026-03-15

# 查看交易记录
python paper_trading/paper_trading.py orders --start 2026-03-11 --end 2026-03-15

# 生成绩效报告
python paper_trading/paper_trading.py report --start 2026-03-11

# 导出数据为 CSV
python paper_trading/paper_trading.py export --table account_summary --output account.csv

# 启动 Web Dashboard
python paper_trading/paper_trading.py web

# 指定端口启动
python paper_trading/paper_trading.py web --port 8080
```

---

## 七、定时任务配置

```crontab
# 每日 18:00 更新数据（已有）
0 18 * * 1-5 /home/yuzai/qlib/scripts/data_collector/tushare/run_update_to_bin.sh

# 每日 21:00 执行模拟盘（新增，预留充足的数据更新时间）
0 21 * * 1-5 /home/yuzai/qlib/paper_trading/run_daily.sh
```

`run_daily.sh` 逻辑：

1. 激活 conda 环境
2. 检查数据更新是否成功（读取当日数据更新日志）
3. 若成功，执行 `paper_trading.py daily`
4. 若失败，发送告警邮件并退出

---

## 八、关键流程时序

### 8.1 初始化（init）

```
1. 读取 config.yaml
2. 初始化 qlib 环境
3. 创建 SQLite 数据库和表
4. 从 MLflow 加载模型（experiment_id=745738095260835211, recorder_id=d3f5db20d9af4e70addfefc62fafdf54）
5. 通过 Tushare stock_basic 接口拉取全量股票名称映射，存入 stock_names 表
6. 记录初始账户状态（100万现金，空仓）
7. 执行首次预测并保存信号（供建仓日使用）
8. 输出初始化成功日志
```

### 8.2 每日执行（daily）

```
--- 前置检查 ---
1. 读取配置，初始化 qlib
2. 检查当日是否为交易日 → 非交易日则跳过
3. 检查 day.txt 中是否包含今日日期 → 数据未就绪则等待/报错
4. 检查是否已执行过今日 → 已执行则跳过（幂等性）
5. 加载模型（若未缓存）

--- 阶段一：交易执行（消费 T-1 信号） ---
6. 从数据库加载 T-1 日的预测信号
7. 加载 T-1 日收盘后的持仓状态
8. TopkDropoutStrategy 根据 T-1 信号生成买卖订单
9. 获取 T 日行情（收盘价），模拟撮合
   - 检查涨跌停
   - 计算实际成交股数（100股整数倍）
   - 计算手续费
10. 更新账户状态
    - 卖出：增加现金，减少持仓
    - 买入：减少现金，增加持仓
    - 更新持仓市值（按 T 日收盘价）
11. 保存订单记录、持仓快照、账户摘要

--- 阶段二：信号预测（为 T+1 生成信号） ---
12. 准备 Alpha158 特征数据（包含 T 日）
13. 模型预测，生成全股票池得分
14. 保存 T 日预测结果到数据库（供 T+1 日使用）

--- 阶段三：日终处理 ---
15. 检查告警条件
16. 生成当日摘要日志
```

### 8.3 补跑（run --date）

```
1. 与 daily 流程相同，但指定具体日期
2. 使用该日期的行情数据进行模拟
3. 支持按日期区间批量补跑，按时间顺序依次执行
```

---

## 九、已确认的设计决策


| 问题            | 决策                                       | 说明                              |
| ------------- | ---------------------------------------- | ------------------------------- |
| 信号时间语义        | T 日 21:00 生成信号 → T+1 日 21:00 先以 T+1 收盘价撮合再生成新信号 | 每日流程先交易（用 T-1 信号），再预测（为 T+1 准备信号） |
| 建仓方式          | 首日（2026-03-11）一次性买入 top 10               | 与 TopkDropoutStrategy 默认行为一致    |
| 模型重训          | 初期使用固定模型，不做滚动训练                          | 后续版本再考虑 rolling retrain         |
| 代码位置          | `paper_trading/`（仓库根目录）                  | —                               |
| Web Dashboard | **首期实现**，提供 Web 页面实时查看                   | 见 3.9 节详细需求                     |


---

## 十、Web Dashboard 需求

### 10.1 技术选型

- **后端**：FastAPI（轻量、async、自带 OpenAPI 文档）
- **前端**：单页应用，使用 Vue 3 + ECharts（或 Plotly.js）
- **部署**：单进程运行，FastAPI 同时提供 API 和静态前端文件
- **端口**：默认 8000，可通过配置文件修改

### 10.2 页面结构

#### 10.2.1 首页 - 账户概览（Dashboard）

核心指标卡片：

- 账户总资产 / 当日盈亏 / 累计收益率
- 持仓数量 / 现金余额 / 最大回撤
- 今日换手率 / 运行天数 / 夏普比率

图表：

- **净值曲线**：账户净值 vs 基准（SH000300），双轴折线图，支持时间范围选择
- **超额收益曲线**：累计超额收益走势
- **回撤曲线**：最大回撤可视化
- **每日盈亏柱状图**：红盈绿亏

#### 10.2.2 持仓页面

- 当前持仓列表（表格）：股票代码（SH600000）、股票名称（浦发银行）、持股数、成本价、现价、浮动盈亏、盈亏比例、持仓占比、持有天数
- 持仓分布饼图：按市值占比
- 历史持仓查询：选择日期查看历史持仓快照

#### 10.2.3 交易记录页面

- 订单列表（表格）：日期、股票代码+名称、方向、目标股数、成交股数、成交价、成交金额、手续费、状态
- 支持按日期范围筛选
- 支持按方向（买入/卖出）筛选
- 每日交易摘要：买卖笔数、金额、手续费

#### 10.2.4 预测信号页面

- 最新一期预测排名（表格）：股票代码+名称、预测分数、排名
- 历史预测准确度（可选）：预测得分 vs 实际收益散点图

#### 10.2.5 绩效分析页面

综合绩效指标表格：

- 累计收益率、年化收益率、最大回撤
- 夏普比率、信息比率、胜率、盈亏比
- 平均日换手率、累计手续费

图表：

- 月度收益热力图
- 收益率分布直方图
- 滚动夏普比率

#### 10.2.6 系统状态页面

- 最近运行日志（最新 N 条）
- 数据更新状态：最后更新日期、更新结果
- crontab 任务状态
- 告警历史记录

### 10.3 API 设计

```
GET  /api/overview                          # 账户概览
GET  /api/account/summary?start=&end=       # 账户摘要时间序列
GET  /api/positions?date=                   # 持仓明细（默认最新）
GET  /api/positions/current                 # 当前持仓
GET  /api/orders?start=&end=&direction=     # 订单列表
GET  /api/predictions?date=                 # 预测结果
GET  /api/performance                       # 绩效指标
GET  /api/performance/monthly               # 月度收益
GET  /api/benchmark?start=&end=             # 基准数据
GET  /api/stock/names                       # 股票名称映射表
GET  /api/logs?limit=                       # 系统日志
GET  /api/system/status                     # 系统状态
```

### 10.4 Web 配置

```yaml
# config.yaml 中新增
web:
  enabled: true
  host: "0.0.0.0"
  port: 8000
  auto_refresh: 60          # 前端自动刷新间隔（秒）
```

### 10.5 启动方式

```bash
# 启动 Web 服务
python paper_trading/paper_trading.py web

# 或指定端口
python paper_trading/paper_trading.py web --port 8080
```

---

## 十一、更新后的目录结构

```
paper_trading/                          # 模拟盘根目录
├── config.yaml                         # 配置文件
├── paper_trading.py                    # 主程序入口（CLI）
├── run_daily.sh                        # crontab 调用的 shell 脚本
├── modules/
│   ├── __init__.py
│   ├── signal_generator.py             # 信号生成模块
│   ├── order_manager.py                # 订单管理模块
│   ├── execution_engine.py             # 模拟执行引擎
│   ├── account_manager.py              # 账户管理模块
│   ├── recorder.py                     # 数据记录模块（SQLite）
│   ├── reporter.py                     # 报告生成模块
│   └── alert.py                        # 告警模块
├── web/
│   ├── __init__.py
│   ├── app.py                          # FastAPI 应用
│   ├── api.py                          # API 路由
│   └── static/                         # 前端静态文件
│       ├── index.html                  # 单页应用入口
│       ├── css/
│       │   └── style.css
│       └── js/
│           └── app.js                  # 前端逻辑 + ECharts 图表
├── data/
│   └── paper_trading.db                # SQLite 数据库
├── logs/                               # 日志目录
│   └── YYYY-MM-DD.log
└── reports/                            # 静态报告输出目录
    └── report_YYYYMMDD.html
```

---

## 十二、后续扩展（非首期需求）

以下功能不在首期实现范围内，但架构设计时需预留扩展点：

1. **多策略支持**：同时运行多个策略，独立账户
2. **模型滚动训练**：定期重新训练模型
3. **实盘对接**：对接券商 API（如通达信、同花顺 iFinD）
4. **风控模块**：仓位上限、行业集中度、个股持仓上限
5. **微信/钉钉通知**：移动端推送

