# B2-S + IM 补 β Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 IM 窗口（2022-07-22～2026-07-31）用外部 overlay 把 B2-S 组合 β 补到 1.0（真实 IM 结算价连主力），以扣费绝对夏普（rf=0，`TRADING_DAYS=250`）对照 B2-S；若严格更高则登记并可晋升研究 baseline。本轮不做实盘。

**Architecture:** 股票腿 `run_pred_backtest`（账户 280 万）产出 `report_normal`；`build_im_continuous` 从中金所日结算缓存生成连主力收益；`run_beta_overlay_experiment` 做滞后 60 日 β + `port = net + (1-β̂)·r_IM`，用 `strategy_stability_metrics.summarize_period` 计主指标；registry 方向 `strategy-beta-overlay`。

**Tech Stack:** Python 3 / pandas / pytest；qlib `run_pred_backtest`；已有 `strategy_stability_metrics.py`、`analyze_b2s_beta_alpha.rolling_beta` 模式。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-b2s-im-beta-overlay-design.md`
- 唯一候选：目标 β=1.0，滚动窗口 60，滞后 1 日，始终开，real_IM only
- `evaluation_mode: im_window_in_sample`；窗口 `[2022-07-22, 2026-07-31]`
- 主指标：扣费绝对 `sharpe_ratio`（`strategy_stability_metrics.TRADING_DAYS == 250`）
- 晋升：overlay 夏普 **严格大于** 同窗口 B2-S；需用户二次确认后才写 baseline 锚点
- 账户：`backtest.account: 2800000`；不做实盘代码
- IM **不** dump 进 `~/.qlib/qlib_data` features，不进股票 Exchange
- Python：`/opt/anaconda3/envs/qlib/bin/python`；macOS 禁止 heredoc 跑 Qlib 并行取数（写脚本文件执行）
- 提交：仅在用户明确要求时 `git commit`；计划中的 Commit 步默认跳过或改为「暂存说明」

## File Structure

| 路径 | 职责 |
|---|---|
| `backtest/scripts/build_im_continuous.py` | CFFEX 分合约 → 连主力 CSV |
| `backtest/data/im/im_continuous_daily.csv` | 固化 IM 行情（生成物） |
| `backtest/scripts/beta_overlay_core.py` | 纯函数：滚动 β、overlay、离散手数 |
| `backtest/scripts/run_beta_overlay_experiment.py` | CLI：跑对照、写 artifact、可选 registry |
| `backtest/configs/strategy-beta-overlay/b2s-im-target1-roll60_csi1000_imwindow.yaml` | 280 万 B2-S pred_backtest 配置 |
| `tests/backtest/test_build_im_continuous.py` | 连主力构造测试 |
| `tests/backtest/test_beta_overlay_core.py` | overlay / β / 手数测试 |
| `tests/backtest/test_run_beta_overlay_experiment.py` | runner / registry 行测试 |
| `backtest/experiments/ic/b2s_im_beta_overlay.json` | 主 artifact（路径可在 runner 参数覆盖） |

---

### Task 1: IM 连主力构建器

**Files:**
- Create: `backtest/scripts/build_im_continuous.py`
- Create: `tests/backtest/test_build_im_continuous.py`
- Create: `backtest/data/im/.gitkeep`（若目录需入库；大 CSV 若太大则 gitignore + 文档说明由脚本生成）

**Interfaces:**
- Produces:
  - `select_active_contracts(raw: pd.DataFrame) -> pd.DataFrame`  
    输入列至少含 `date, 合约代码, 成交量, 持仓量, 今结算`；输出 index=`date`，列 `contract, settle, volume, oi`
  - `settle_to_settle_returns(settle_panel: pd.DataFrame, held_contract: pd.Series) -> pd.Series`  
    `held_contract` 为进入日 t 的隔夜合约（即昨日 active）
  - `build_continuous_from_cffex_dir(cffex_dir: Path) -> pd.DataFrame`  
    列：`date, contract, settle, volume, oi, fut_ret, roll`
  - CLI: `--cffex-dir` `--output` `--write-hash`

- [ ] **Step 1: 写失败测试**

```python
# tests/backtest/test_build_im_continuous.py
import pandas as pd
from build_im_continuous import select_active_contracts, settle_to_settle_returns

def test_select_active_picks_higher_volume():
    raw = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
            "合约代码": ["IM2401", "IM2402"],
            "成交量": [100, 500],
            "持仓量": [10, 20],
            "今结算": [5000.0, 4990.0],
        }
    )
    active = select_active_contracts(raw)
    assert list(active["contract"]) == ["IM2402"]

def test_settle_returns_use_held_contract_not_new_active():
    # Day1 hold IM2401; Day2 active switches to IM2402 but return still on IM2401
    panel = pd.DataFrame(
        {"IM2401": [100.0, 102.0, 101.0], "IM2402": [99.0, 100.0, 103.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    held = pd.Series(
        [pd.NA, "IM2401", "IM2402"],
        index=panel.index,
        dtype=object,
    )
    rets = settle_to_settle_returns(panel, held)
    assert abs(rets.iloc[1] - 0.02) < 1e-12  # 102/100-1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/yuxianqi/Project/qlib_exp && /opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_build_im_continuous.py -v`  
Expected: FAIL import error

- [ ] **Step 3: 实现 `build_im_continuous.py`**

要点：
- 从目录读所有 `YYYYMM.csv`，规范化列名与 `IM\d{4}` 过滤（与先前诊断脚本一致）
- `select_active_contracts`：按 date 分组，`成交量` desc、`持仓量` desc、合约代码 asc
- `held = active.contract.shift(1)`；用 pivot 结算价算 `fut_ret`
- `roll = contract != contract.shift(1)`
- `main()` 写 CSV，并可选写旁路 `im_continuous_daily.sha256`

- [ ] **Step 4: 测试通过后，用已有缓存生成数据**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_im_continuous.py \
  --cffex-dir backtest/experiments/ic/cffex_daily \
  --output backtest/data/im/im_continuous_daily.csv \
  --write-hash
```

Expected: 约 975+ 行，`fut_ret` 自 2022-07-25 起非空；打印 path + sha256

- [ ] **Step 5: Commit（仅用户要求时）**

```bash
git add backtest/scripts/build_im_continuous.py tests/backtest/test_build_im_continuous.py backtest/data/im/
# git commit only if user asks
```

---

### Task 2: Overlay 纯函数核心

**Files:**
- Create: `backtest/scripts/beta_overlay_core.py`
- Create: `tests/backtest/test_beta_overlay_core.py`

**Interfaces:**
- Consumes: pandas / numpy
- Produces:
  - `IM_WINDOW = ("2022-07-22", "2026-07-31")`
  - `TARGET_BETA = 1.0`
  - `ROLL_WINDOW = 60`
  - `rolling_beta_lagged(net: pd.Series, bench: pd.Series, window: int = 60) -> pd.Series`
  - `apply_beta_overlay(net, bench, fut_ret, *, target=1.0, window=60) -> pd.DataFrame`  
    列：`net, bench, beta_hat, gap, fut_ret, port`（`port = net + gap * fut_ret`）
  - `discrete_lots(gap, account_value, settle, *, multiplier=200) -> pd.Series`  
    `round(gap * account_value / (settle * multiplier))`
  - `slice_im_window(frame: pd.DataFrame) -> pd.DataFrame`
  - `report_from_port(port: pd.Series, base_report: pd.DataFrame) -> pd.DataFrame`  
    构造 `return=port, cost=0, bench=base.bench, turnover=base.turnover`，供 `summarize_period`

- [ ] **Step 1: 写失败测试**

```python
import numpy as np
import pandas as pd
from beta_overlay_core import (
    apply_beta_overlay,
    discrete_lots,
    rolling_beta_lagged,
    slice_im_window,
)

def test_rolling_beta_is_lagged_one_day():
    idx = pd.bdate_range("2024-01-02", periods=80)
    rng = np.random.default_rng(0)
    bench = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
    net = 0.5 * bench + rng.normal(0, 0.005, len(idx))
    beta = rolling_beta_lagged(net, bench, window=60)
    assert pd.isna(beta.iloc[59])  # 需要 60 点 + shift
    assert pd.notna(beta.iloc[60])

def test_overlay_port_equals_net_plus_gap_times_fut():
    idx = pd.bdate_range("2024-01-02", periods=5)
    net = pd.Series([0.01] * 5, index=idx)
    bench = pd.Series([0.02] * 5, index=idx)
    fut = pd.Series([0.03] * 5, index=idx)
    # force beta_hat via long window unavailable → use manual frame path:
    # test gap arithmetic with apply after injecting: unit-test discrete + formula helper
    from beta_overlay_core import overlay_from_gap
    port = overlay_from_gap(net, gap=pd.Series([0.4] * 5, index=idx), fut_ret=fut)
    assert abs(port.iloc[0] - (0.01 + 0.4 * 0.03)) < 1e-12

def test_discrete_lots_round():
    lots = discrete_lots(
        gap=pd.Series([0.5]),
        account_value=pd.Series([2_800_000.0]),
        settle=pd.Series([7000.0]),
        multiplier=200,
    )
    # 0.5*2.8e6 / (7000*200) = 1.0
    assert int(lots.iloc[0]) == 1

def test_slice_im_window():
    idx = pd.to_datetime(["2022-07-21", "2022-07-22", "2026-07-31", "2026-08-01"])
    frame = pd.DataFrame({"x": [1, 2, 3, 4]}, index=idx)
    out = slice_im_window(frame)
    assert list(out.index.date) == [
        pd.Timestamp("2022-07-22").date(),
        pd.Timestamp("2026-07-31").date(),
    ]
```

实现时若拆出 `overlay_from_gap`，一并导出。

- [ ] **Step 2: 跑测试确认失败**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_beta_overlay_core.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现 `beta_overlay_core.py`**

```python
def rolling_beta_lagged(net, bench, window=60):
    frame = pd.concat([net.rename("net"), bench.rename("bench")], axis=1)
    return (frame["net"].rolling(window).cov(frame["bench"]) / frame["bench"].rolling(window).var()).shift(1)

def overlay_from_gap(net, gap, fut_ret):
    return (net + gap * fut_ret).rename("port")

def apply_beta_overlay(net, bench, fut_ret, *, target=1.0, window=60):
    beta_hat = rolling_beta_lagged(net, bench, window=window)
    gap = (target - beta_hat).rename("gap")
    port = overlay_from_gap(net, gap, fut_ret)
    return pd.concat([net.rename("net"), bench.rename("bench"), beta_hat.rename("beta_hat"),
                      gap, fut_ret.rename("fut_ret"), port], axis=1)

def report_from_port(port, base_report):
    out = base_report.copy()
    out = out.reindex(port.dropna().index)
    out["return"] = port.reindex(out.index).astype(float)
    out["cost"] = 0.0
    return out
```

- [ ] **Step 4: 测试通过**

Run: 同上 pytest  
Expected: PASS

---

### Task 3: 实验 Runner + Registry 行

**Files:**
- Create: `backtest/scripts/run_beta_overlay_experiment.py`
- Create: `tests/backtest/test_run_beta_overlay_experiment.py`
- Modify（如需）: `backtest/scripts/build_strategy_stability_report.py` — 仅当要在稳定性 HTML 增加 `strategy-beta-overlay` 区块；否则 runner 写独立小结进 JSON，并在 `registry` 后手动/小脚本刷新报告。优先：**registry 行 + JSON artifact**；HTML 最小改动：在 `build_strategy_stability_report.py` 增加识别 `direction==strategy-beta-overlay` 的 section（若改动面过大则只保证 JSON + registry，HTML 作为 Task 3b）。

**Interfaces:**
- Consumes: `beta_overlay_core.*`，`strategy_stability_metrics.summarize_period`，`analyze_b2s_style_regime.upsert_registry` / `sha256_of`
- Produces:
  - `run_experiment(report: pd.DataFrame, im: pd.DataFrame, *, account: float = 2_800_000) -> dict`
  - payload 必备键：`evaluation_mode`, `im_window`, `account`, `baseline`（B2-S IM 窗口 `summarize_period`）, `overlay_continuous`, `overlay_discrete`, `promote`（`{"eligible": bool, "reason": str}`）, `basis_note`（若 im 含 basis 列则汇总）
  - `build_registry_row(payload, output_path) -> dict`  
    `exp_id=strategy-beta-overlay/b2s-im-target1-roll60`，`conclusion` 为 `accepted_pending_promotion` / `rejected_vs_baseline` / `complete`（不自动 promote）

- [ ] **Step 1: 写失败测试（registry 与晋升判定）**

```python
from run_beta_overlay_experiment import decide_promotion, build_registry_row

def test_promotion_requires_strictly_higher_sharpe():
    assert decide_promotion(overlay_sharpe=1.5, baseline_sharpe=1.4)["eligible"] is True
    assert decide_promotion(overlay_sharpe=1.4, baseline_sharpe=1.4)["eligible"] is False
    assert decide_promotion(overlay_sharpe=1.3, baseline_sharpe=1.4)["eligible"] is False

def test_registry_row_marks_im_window_mode(tmp_path):
    payload = {
        "evaluation_mode": "im_window_in_sample",
        "im_window": ["2022-07-22", "2026-07-31"],
        "account": 2_800_000,
        "baseline": {"sharpe_ratio": 1.0},
        "overlay_continuous": {"sharpe_ratio": 1.2},
        "promote": {"eligible": True, "reason": "sharpe_ratio 1.2 > 1.0"},
    }
    out = tmp_path / "a.json"
    out.write_text("{}")
    row = build_registry_row(payload, out)
    assert row["evaluation_mode"] == "im_window_in_sample"
    assert row["baseline_ref"] == "B2-S v1.0"
    assert row["direction"] == "strategy-beta-overlay"
    assert row["cleanup_retention_eligible"] is False  # 晋升前不占清理额度
```

- [ ] **Step 2: 实现 runner**

逻辑纲要：
1. 读 `report_normal`：`net = return - cost`
2. 读 IM CSV，align `fut_ret` / `settle`
3. 全样本算 `apply_beta_overlay`，再 `slice_im_window`
4. Baseline 指标：`summarize_period(report_from_port(net_im, report_im))` 或直接对 IM 窗口原 report `summarize_period`
5. Overlay：`summarize_period(report_from_port(port, report_im))`
6. Discrete：`lots = discrete_lots(...)`；`port_d = net + lots * (settle*200) / account * fut_ret` — **更干净的定义**：

```text
notional_frac = lots * settle * 200 / account
port_discrete = net + notional_frac * fut_ret
```

账户净值路径：主结果用固定 `account=2_800_000` 作手数换算分母（与初始资金一致；不模拟保证金路径依赖）。股票 report 若有 `value+cash` 可用逐日总资产代替固定 account（优先逐日 `value+cash`，缺失则常数 280 万）。

7. `decide_promotion` → 写 JSON；`--registry` 时 upsert

CLI：

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_beta_overlay_experiment.py \
  --report backtest/result/<session>/run_01/report_normal.csv \
  --im backtest/data/im/im_continuous_daily.csv \
  --output backtest/experiments/ic/b2s_im_beta_overlay.json \
  --account 2800000 \
  --registry backtest/experiments/registry.jsonl \
  --result-dir backtest/result/<session>
```

- [ ] **Step 3: 单元测试通过**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_run_beta_overlay_experiment.py tests/backtest/test_beta_overlay_core.py -v`  
Expected: PASS

---

### Task 4: 280 万 B2-S 配置与股票回测

**Files:**
- Create: `backtest/configs/strategy-beta-overlay/b2s-im-target1-roll60_csi1000_imwindow.yaml`  
  从 `backtest/configs/baseline-strategy/b2-s/topk-t30-d2-h20_csi1000_full.yaml` 复制，仅改：
  - `backtest.account: 2800000`
  - `run.note: strategy_beta_overlay_b2s_account_2800000`
  - 头部注释指向本 spec
- 确认 pred 路径：与现有 B2-S / b6-m full pred 一致（读 yaml 后半 `phase_s` / 外部 pred 参数；若该 yaml 依赖 CLI `--pred`，在运行命令中显式传入 manifest 中的 full pred）

- [ ] **Step 1: 写配置文件**（account=2800000）

- [ ] **Step 2: 跑 pred_backtest（脚本文件，不用 heredoc）**

先确认 pred 路径：

```bash
/opt/anaconda3/envs/qlib/bin/python -c "import json;from pathlib import Path;m=json.loads(Path('backtest/models/baselines/b6-m/manifest.json').read_text());print([p for p in m['predictions'] if p.get('segment')=='full' and p.get('pool')=='csi1000'])"
```

然后（示例，以实际 CLI 为准，对照 `run_pred_backtest.py -h`）：

```bash
MLFLOW_ALLOW_FILE_STORE=true /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_pred_backtest.py \
  --config backtest/configs/strategy-beta-overlay/b2s-im-target1-roll60_csi1000_imwindow.yaml \
  --pred <manifest_full_pred_path>
```

Expected: 生成 `backtest/result/<timestamp>_.../run_01/report_normal.csv`

- [ ] **Step 3: 跑 overlay 实验**

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_beta_overlay_experiment.py \
  --report backtest/result/<session>/run_01/report_normal.csv \
  --im backtest/data/im/im_continuous_daily.csv \
  --output backtest/experiments/ic/b2s_im_beta_overlay.json \
  --account 2800000 \
  --registry backtest/experiments/registry.jsonl \
  --result-dir backtest/result/<session>
```

Expected: JSON 含双方夏普与 `promote.eligible` true/false

- [ ] **Step 4: 重建报告（若 Task 3 接了 HTML）**

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_strategy_stability_report.py
```

若未接 HTML：在 JSON `hypothesis`/`findings` 写清结论即可，并在回复用户时贴表。

---

### Task 5: 晋升门闩（人工确认，不自动执行）

**Files:**
- Modify: `backtest/scripts/register_phase_s_experiment.py` **仅当**现有 `promote-baseline` 无法表达 overlay 锚点时，增加 `promote-beta-overlay-baseline` 子命令；否则复用并扩展 metadata 字段（`overlay: {...}`, `account: 2800000`, `evaluation_mode: im_window_in_sample`）。
- Test: `tests/backtest/test_register_phase_s_experiment.py` 增补一条 overlay 晋升 schema 测试

- [ ] **Step 1: 若 `promote.eligible` 为 false** — 登记 `conclusion=rejected_vs_baseline`，**停止**，不改 baseline。

- [ ] **Step 2: 若 eligible 为 true** — 向用户展示夏普对照表，等待明确回复「晋升 / 升为 baseline」。

- [ ] **Step 3: 用户确认后** — 写入 `baseline/b3-s-on-b6-m`（或下一可用 slug），`cleanup_retention_eligible=True`，更新 `EXPERIMENT_STANDARD.md` §1.2 仅在用户要求改规范时进行；默认只改 registry + 报告。

- [ ] **Step 4: 验证 cleanup dry-run 接受新锚点**

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/cleanup_experiment_artifacts.py --dry-run
```

---

## Spec Coverage Check

| Spec 要求 | Task |
|---|---|
| 外部 overlay 架构 A | 2–4 |
| real_IM 连主力 / 禁 IM0 | 1 |
| 目标 β=1 / 60 / 始终开 | 2 |
| IM 窗口选型 | 2–3 |
| 绝对夏普主指标 / rf=0 / 对齐稳定性 | 3（`summarize_period`） |
| 账户 280 万 | 4 |
| 离散手数敏感性不晋升 | 2–3 |
| registry + artifact | 3 |
| 晋升需确认 | 5 |
| 不做实盘 | 全局约束 |
| 不进 qlib features | Task 1 输出路径 `backtest/data/im/` |

## Placeholder Scan

无 TBD；CLI 中 `<session>` / `<manifest_full_pred_path>` 在 Task 4 运行时解析替换。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-05-b2s-im-beta-overlay.md`.

两种执行方式：

1. **Subagent-Driven（推荐）** — 每任务新开子代理，任务间复查  
2. **Inline Execution** — 本会话按任务连续做，关键节点停顿  

要哪一种？