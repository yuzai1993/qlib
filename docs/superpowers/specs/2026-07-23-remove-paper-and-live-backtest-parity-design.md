# 移除 Paper Trading 并收敛 Live/Backtest 对齐设计

## 背景

`paper_trading/` 已不再承担独立业务，但 Live Trading 仍从该目录导入
`SignalGenerator` 和 `OrderManager`，Live 配置也继承 Paper 配置，股票名称还会回读
Paper SQLite。这种依赖会让实盘关键逻辑看起来属于模拟盘，也使 Live 与 Backtest
的对齐关系无法被清晰验证。

当前审计还发现：同一模型对共同股票给出的预测值一致，但候选集合与同分排序顺序
可能不同；Live 的费用、账户规模和涨跌停策略也没有一份可自动校验的对应回测配置。

## 目标

- 删除 Paper Trading 应用、配置、测试、运行日志和数据库，不保留运行时依赖。
- 将模型推理迁入 `live_trading`，将 TopkDropout 标的选择抽为 Backtest 与 Live
  共用的纯函数。
- Live 配置改为自包含，不再继承其他交易系统配置。
- 对同分股票使用确定性规则：分数降序，其次股票代码升序。
- 为 Live 建立唯一的 parity backtest 配置，并在发布前校验关键参数。
- 保留已发布批次、Live 账本、持仓与成交历史，不自动重发或改写。

## 非目标与保证边界

- 不删除 `live_trading/data`、Live 日志或 QMT bridge 的 archive/outbound 数据。
- 不修改用户正在进行的 CSI500、SoftTopk 或其他策略实验。
- 不在无人值守状态下升级 Signal/QMT 文件协议。
- Backtest 的日线收盘价完整成交，无法逐笔复现实盘 14:45 盘口、部分成交、拒单和
  撤单。严格保证限定为“相同输入得到相同决策和资金公式”；市场成交偏差由回执和
  次日持仓恢复处理。

## 架构

### Live 模块归属

- `paper_trading.modules.signal_generator.SignalGenerator` 迁移为
  `live_trading.modules.signal_generator.SignalGenerator`。
- `paper_trading.modules.order_manager.OrderManager` 迁移为
  `live_trading.modules.order_manager.OrderManager`。
- Live 发布和预测回填脚本只从 `live_trading` 导入。
- Paper 的账户、模拟成交、报告、告警和 Web 不迁移，直接删除。

### 共享 TopkDropout 选择核心

新增 `qlib.contrib.strategy.topk_dropout`，只负责：

1. 清理无效分数并按 `(score DESC, instrument ASC)` 排序；
2. 根据 `topk`、`n_drop` 和真实持仓计算 `sell` / `buy`；
3. 处理 9/10/11/12 只等缺仓和超仓场景；
4. 有效信号为空时闭锁。

Qlib `TopkDropoutStrategy` 在当前实盘使用的 `method_buy=top`、
`method_sell=bottom`、`only_tradable=false` 路径调用该核心；Live OrderManager
调用同一个核心。随机选择和 `only_tradable=true` 的既有 Qlib 行为保持不变。

### Live 推理配置

Live YAML 完整保存 data/model/handler/strategy/exchange 段。Handler 显式设置与模型
训练一致的 `fit_start_time=2006-01-02`、`fit_end_time=2020-01-10` 和
`ProcessInf`。SignalGenerator 不再把 handler 的数据起点误作 fit 起点。

Live 每日生成的预测仍写入 Live SQLite；这是当日决策的不可变信号快照。后续历史
数据源发生修订时，以该快照解释已发布订单，而不是重新推理后覆盖历史。

### 仓位和费用

Live OrderManager 显式读取 `risk_degree=0.95`，预计卖出收入扣除 Live 配置费用后
再等分给买入标的，并按 100 股取整。该公式与 Qlib 回测“卖出后现金 × risk_degree
/ 买入数”一致；发布时仍用 T-1 收盘价计算保护性请求股数，QMT 会根据交易日实际
现金和价格缩单。

由于现有协议只传固定请求股数，价格下跌时 QMT 不会扩大股数。这项成交层差异将由
parity 检查明确报告，但不在本次无人值守改动中升级协议。后续若要进一步收敛，需
同时部署新版 Publisher、Signal schema、FillImporter 与 Windows QMT bridge。

### Parity backtest 与发布门禁

新增 `backtest/configs/csi300_live_parity.yaml`，固定：

- 同一训练 session/model、Alpha158、CSI300 和 T-1 信号；
- `topk=10`、`n_drop=2`、`risk_degree=0.95`、`hold_thresh=1`；
- `only_tradable=false`、`forbid_all_trade_at_limit=false`；
- 账户 1,000 万、交易单位 100；
- 买入成本 0.00021、卖出成本 0.00071、最低费用 5 元；
- 日线 close 作为 14:45 近收盘执行的回测代理。

新增纯配置校验器，把 Live 配置映射到上述 Backtest 字段。发布脚本在生成任何持久化
批次前执行校验；关键字段漂移时失败关闭。研究用的其他 Backtest 配置不受影响，但
不能再作为 Live 的正式对照基线。

## 股票名称和运行数据清理

Live SQLite 已有 5,544 条股票名称，与 Paper SQLite 数量一致。因此：

- Web 只读取 Live SQLite，不再启动时回读 Paper 数据库；
- 新增 Live 专属的 Tushare 名称刷新入口，供未来维护；
- 历史 signal 订单回补脚本不再承担 Paper 名称迁移；
- 删除主工作区 `paper_trading/data`、`paper_trading/logs` 和其余 Paper 文件；
- 当前 crontab 已确认只有数据更新和 Live 任务，无 Paper 定时项需要删除。

## 测试与验收

- 先迁移测试到 `tests/live_trading`，验证旧导入路径失败、新路径尚不存在。
- 用输入顺序置换和边界同分用例证明选择结果稳定。
- 用 9/10/11/12 只持仓场景同时验证共享核心和 Live OrderManager。
- 验证预计卖出费用从买入预算中扣除。
- 验证 Live 配置自包含，且与 parity backtest 关键字段一致；任一字段漂移时门禁失败。
- 验证 Live 源码不存在 `paper_trading` 运行时引用，仓库根不再有 Paper 应用目录。
- 运行完整 `tests/live_trading`、相关 Qlib 策略测试和仓库可行的全量测试。
- `git diff --check` 通过，且提交不包含主工作区中用户未提交的实验改动。

