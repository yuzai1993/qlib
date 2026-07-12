# QMT 实盘信号桥接 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 [设计定稿 v1.0](../specs/2026-07-11-qmt-live-signal-bridge-design.md) 的 Phase 0–3：研究机信号发布链路、回执导入、QMT 内置桥接策略脚本。

**Architecture:** 研究机（本仓库 `live_trading/`）产出 JSONL 信号文件到共享目录 `inbox/`，Windows 大 QMT 内置策略消费并 `passorder` 下单、写回执到 `outbound/`，研究机导入回执入 SQLite。协议细节以设计文档 §5 为准（本计划不重复，冲突时以设计文档为准）。

**Tech Stack:** Python 3.12（研究机）、pytest、SQLite、YAML；QMT 内置策略为 Python 3.6 + GBK 兼容子集（仅标准库）。

**约定：**

- 模块风格对齐 `paper_trading/modules/`（logging、`Path`、sqlite3 WAL、`UNIQUE` 约束）
- 测试放 `tests/live_trading/`，用 `REPO_ROOT` sys.path 注入，风格对齐 `tests/paper_trading/`
- 测试命令：`/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading/ -v`
- 每完成一个模块跑一次该模块测试；全部完成后全量跑 `tests/live_trading/`

---

### Task 1: code_map（qlib ↔ QMT 代码转换）

**Files:** Create `live_trading/modules/code_map.py`, `live_trading/modules/__init__.py`, `tests/live_trading/test_code_map.py`

- [ ] 测试：`SH600000→600000.SH`、`SZ000001→000001.SZ`、`BJ835185→835185.BJ`、双向互逆、非法输入（小写、长度错、无市场前缀、已是 QMT 格式）抛 `ValueError`
- [ ] 实现 `qlib_to_qmt` / `qmt_to_qlib`，白名单市场 `{SH, SZ, BJ}`，6 位数字校验
- [ ] 跑测试通过

### Task 2: signal_schema（协议对象 + 校验 + checksum）

**Files:** Create `live_trading/modules/signal_schema.py`, `tests/live_trading/test_signal_schema.py`

- [ ] 数据类 `BatchHeader` / `SignalOrder` / `FillEvent`（dataclass + `to_json_line()` / `from_dict()`）
- [ ] `make_client_order_id(trade_date, seq, side)` → `20260714001S` 格式，断言 ≤24 字符
- [ ] `compute_checksum(order_lines: list[str])` → `sha256:<hex>`，按行原始 UTF-8 字节拼接
- [ ] 校验规则（`validate_order`）：side ∈ {BUY,SELL}、quantity>0 且 %100==0、limit_price>0、stock_code 为 QMT 格式；`validate_batch`：order_count 与实际一致、trade_date 格式
- [ ] FillEvent 校验：`mode` 必填 ∈ {SIMULATE, LIVE}，status ∈ 设计文档枚举（含 EXPIRED）
- [ ] 跑测试通过

### Task 3: order_planner（订单行生成）

**Files:** Create `live_trading/modules/order_planner.py`, `tests/live_trading/test_order_planner.py`

- [ ] 复用 `paper_trading.modules.order_manager.OrderManager` 生成买卖意图，转换为 `SignalOrder` 列表
- [ ] 限价：SELL=prev_close×(1−sell_slippage)，BUY=prev_close×(1+buy_slippage)，四舍五入 2 位小数
- [ ] 非整手向下取整到 100，取整后 0 股丢弃；卖单 priority=10，买单 priority=20
- [ ] 同 code 同向合并；`max_orders_per_day` 上限校验（超限抛错，不静默截断）
- [ ] 测试：建仓日全买、常规日 n_drop 换仓、非整手取整、价格无效跳过
- [ ] 跑测试通过

### Task 4: signal_publisher（原子发布）

**Files:** Create `live_trading/modules/signal_publisher.py`, `tests/live_trading/test_signal_publisher.py`

- [ ] `publish(header, orders, bridge_root)`：写 `inbox/signal_{batch_id}.jsonl.tmp` → fsync → rename；再写 `.done.tmp` → rename，`.done` 内容为 checksum
- [ ] header 的 `order_count` / `checksum` 由 publisher 填充（调用方不手填）
- [ ] 重复 batch_id 已存在 → 抛错拒绝覆盖
- [ ] 测试：文件顺序（jsonl 先于 done）、done 内容等于重算 checksum、重复发布报错、tmp 文件不残留
- [ ] 跑测试通过

### Task 5: fill_importer + live db

**Files:** Create `live_trading/modules/fill_importer.py`, `tests/live_trading/test_fill_importer.py`

- [ ] `LiveRecorder`（同文件或独立）：SQLite 表 `batches` / `signal_orders` / `fills` / `live_positions`，风格对齐 `paper_trading/modules/recorder.py`
- [ ] `import_fills(bridge_root)`：只处理有 `.done` 的 `fills_*.jsonl`；按 `client_order_id` upsert；导入后移入研究机 archive
- [ ] **SIMULATE 隔离**：`mode=SIMULATE` 的 fill 只入 `fills` 表，不更新 `live_positions`
- [ ] LIVE FILLED/PARTIAL 更新 `live_positions`（BUY 加仓、SELL 减仓，量为 filled_qty）
- [ ] 对账：`reconcile(batch_id)` 返回 {planned, terminal, missing} 计数
- [ ] 测试：SIMULATE 不动持仓、LIVE 更新持仓、重复导入幂等、无 done 不读
- [ ] 跑测试通过

### Task 6: 配置与加载器

**Files:** Create `live_trading/configs/csi300_topk10_live.yaml`, `live_trading/modules/live_config.py`, `tests/live_trading/test_live_config.py`

- [ ] live yaml 含 `base_config`（指向 paper yaml 相对路径）+ `live` / `schedule` 段（字段见设计文档 §7.3）
- [ ] `load_live_config(path)`：读 base + live，浅合并（live 覆盖同名键），返回 dict
- [ ] 测试：合并结果同时含 `strategy.topk`（来自 base）与 `live.bridge_root`
- [ ] 跑测试通过

### Task 7: CLI 脚本

**Files:** Create `live_trading/scripts/run_publish_signals.py`, `live_trading/scripts/run_import_fills.py`

- [ ] `run_publish_signals.py --config <id> --trade-date YYYY-MM-DD [--mode SIMULATE|LIVE] [--dry-run]`：qlib init → SignalGenerator.predict(signal_date) → 读 live_positions → OrderPlanner → publish；LIVE 需 env `LIVE_TRADING_CONFIRM=YES`
- [ ] `--dry-run` 只打印订单不落盘（便于无 qlib 数据时联调用假分数走不通，主链路依赖真实数据，不为 CLI 写单测；核心逻辑已被 Task 1–6 覆盖）
- [ ] `run_import_fills.py --config <id>`：import_fills + reconcile + 打印报表
- [ ] 手动冒烟：`--help` 可运行、参数校验生效

### Task 8: QMT 内置策略 + 部署说明

**Files:** Create `live_trading/qmt_strategy/qmt_signal_bridge.py`, `live_trading/qmt_strategy/README_QMT.md`

- [ ] 严格 Python 3.6 + 标准库；文件头 `#coding:gbk`；逻辑按设计文档 §6（认领→过期/重复/checksum 检查→先卖后买→passorder(prType=11, quickTrade=2)→轮询 remark→14:50 撤单→14:55 强制 done）
- [ ] 无法本地运行 QMT API：QMT 相关调用集中在薄封装函数内，纯逻辑（解析、状态机、文件协议）与 API 隔离
- [ ] `README_QMT.md`：Windows 目录准备、导入策略、模拟验收步骤、LIVE_OK 开关、日志位置
- [ ] 用研究机 Python 对该文件做语法检查（`python -m py_compile`，3.6 语法子集人工核对 f-string 避免 3.8+ 特性）

### Task 9: 收尾

- [ ] 全量 `pytest tests/live_trading/ -v` 通过
- [ ] `ReadLints` 检查新文件
- [ ] 更新 `docs/qmt_qlib_live_guide.md` §11 链接实现入口
