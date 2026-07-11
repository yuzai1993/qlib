# 回测配置外置与免重训 Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 配置迁入 YAML；CLI 仅 `--config`；支持 `backtest_only` 从结果 session 加载模型不重训。

**Architecture:** `config_loader` 加载/校验/日期对齐；`run_backtest` 按 `run.mode` 分 train_backtest / backtest_only；报告复用 `report_utils`。

**Tech Stack:** PyYAML、qlib workflow、现有 report_utils

---

### Task 1: 默认 YAML + config_loader + 测试
- Create: `backtest/configs/csi300_lgbm.yaml`
- Create: `backtest/configs/csi300_lgbm_bt_only.example.yaml`
- Create: `backtest/scripts/config_loader.py`
- Create: `tests/misc/test_backtest_config_loader.py`

### Task 2: 改造 run_backtest.py
- Modify: `backtest/scripts/run_backtest.py` — 读 config；双模式；去掉 `--note/--n-runs/--handler`

### Task 3: 验证
- 跑单元测试；`--help` 仅剩 `--config`
