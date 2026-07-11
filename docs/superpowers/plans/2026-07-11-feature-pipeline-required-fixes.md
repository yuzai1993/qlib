# 特征管线必选修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除回测/模拟盘之间的训练-推理偏差：去掉模拟盘 `fillna(0)`、剔除恒缺失的 VWAP0 死特征（158→157 维）、训练与推理统一加 `ProcessInf`，并增加复权回溯完整性巡检脚本；最后重训模型并切换模拟盘。

**Architecture:** 特征定义收敛到一个新的 `Alpha158NoVWAP` handler（放在 `qlib/contrib/data/handler.py`，本仓库是私有 fork，回测脚本与模拟盘 yaml 都按类名引用）。推理侧 `SignalGenerator` 改为按 yaml 配置动态实例化 handler，与训练侧共用同一份 processor 配置。数据完整性巡检独立成脚本，不改数据管线。

**Tech Stack:** qlib（DataHandlerLP / Alpha158DL / ProcessInf）、LightGBM、pytest。

**重要前置认知：**
- Task 2/3/4 会把特征从 158 维变为 157 维，**旧模型（recorder `e984aba430c341b9bbf3052a82734d00`）将不可用**，Task 6 的重训是必选收尾，不能只做一半上线。
- `Alpha158` 的 `process_type` 默认 `PTYPE_A`，`infer_processors` 会同时作用于训练数据（DK_L）和推理数据（DK_I），所以只需在 `infer_processors` 加一次 `ProcessInf`，两边自动一致。
- `backtest/scripts/` 下的 `verify_*.py`、`analyze_*.py` 是历史分析脚本，本计划**不**改它们，只改主管线 `run_backtest.py`。

---

### Task 1: 模拟盘推理去掉 `fillna(0)`，保留 NaN 喂给 LightGBM

**背景：** 训练时 LightGBM 原生处理 NaN（学习缺失值的分裂方向）；模拟盘 `fillna(0)` 把缺失变成有含义的数值 0，造成训练/推理偏差。

**Files:**
- Modify: `paper_trading/modules/signal_generator.py`（`predict` 方法，约 85-117 行）
- Test: `tests/paper_trading/test_signal_generator.py`（新建）

- [x] **Step 1: 写失败的测试**

创建 `tests/paper_trading/test_signal_generator.py`：

```python
"""SignalGenerator 推理口径测试：NaN 必须原样传给 LightGBM，不允许 fillna(0)。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from paper_trading.modules.signal_generator import SignalGenerator


class DummyLGB:
    """记录 predict 收到的矩阵。"""

    def __init__(self):
        self.last_X = None

    def predict(self, X):
        self.last_X = np.asarray(X, dtype=float)
        return np.arange(len(X), dtype=float)


def _make_generator():
    gen = SignalGenerator(config={}, project_root=Path("."))
    gen._lgb_model = DummyLGB()
    return gen


def test_nan_features_passed_through_not_filled_with_zero():
    gen = _make_generator()
    df = pd.DataFrame(
        {"F1": [1.0, np.nan], "F2": [np.nan, 2.0]},
        index=pd.Index(["SH600000", "SZ000001"], name="instrument"),
    )
    scores = gen._score_features(df, "2026-07-10")

    assert gen._lgb_model.last_X is not None
    # 核心断言：NaN 不能被替换为 0
    assert np.isnan(gen._lgb_model.last_X).sum() == 2
    assert (gen._lgb_model.last_X == 0).sum() == 0
    assert list(scores.index) == ["SH600000", "SZ000001"]


def test_all_nan_rows_are_dropped():
    gen = _make_generator()
    df = pd.DataFrame(
        {"F1": [1.0, np.nan], "F2": [2.0, np.nan]},
        index=pd.Index(["SH600000", "SZ000001"], name="instrument"),
    )
    scores = gen._score_features(df, "2026-07-10")
    # 全 NaN 行（长期停牌/退市残留）仍应剔除
    assert list(scores.index) == ["SH600000"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/paper_trading/test_signal_generator.py -v`
Expected: FAIL，`AttributeError: 'SignalGenerator' object has no attribute '_score_features'`

- [ ] **Step 3: 实现 `_score_features` 并让 `predict` 调用它**

修改 `paper_trading/modules/signal_generator.py`，把 `predict` 里的打分逻辑抽为 `_score_features`，删除 `fillna(0)`：

```python
    def _score_features(self, day_features: pd.DataFrame, target_date: str) -> pd.Series:
        """对单日特征打分。NaN 原样传给 LightGBM（与训练口径一致，LGB 原生处理缺失）。"""
        day_features = day_features.dropna(how="all")
        raw_scores = self._lgb_model.predict(day_features.values)
        scores = pd.Series(raw_scores, index=day_features.index, name="score")
        scores = scores.dropna()

        logger.info(
            "Generated predictions for %s: %d instruments, top=%.6f, bottom=%.6f",
            target_date, len(scores), scores.max(), scores.min(),
        )
        return scores

    def predict(self, target_date: str) -> pd.Series:
        """Generate prediction scores for all instruments on target_date.

        Reuses cached handler/features when available.
        """
        self.load_model()
        self._ensure_handler(target_date)

        date_index = self._features.index.get_level_values(0)
        target_ts = pd.Timestamp(target_date)

        if target_ts in date_index:
            day_features = self._features.loc[target_ts]
        else:
            last_date = date_index.max()
            logger.warning(
                "Target date %s not in features, using last available: %s",
                target_date, last_date,
            )
            day_features = self._features.loc[last_date]

        return self._score_features(day_features, target_date)
```

注意：原 `predict` 末尾的 `dropna(how="all")`、`fillna(0)`、日志三段被 `_score_features` 取代，不要遗留重复代码。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/paper_trading/test_signal_generator.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/paper_trading/test_signal_generator.py paper_trading/modules/signal_generator.py
git commit -m "fix(paper_trading): 推理不再 fillna(0)，NaN 原样传给 LGB 与训练口径一致"
```

---

### Task 2: 新增 `Alpha158NoVWAP` handler（157 维，剔除死特征 VWAP0）

**背景：** 本地 bin 数据只有 `open/high/low/close/volume/factor/change`，没有 `vwap.day.bin`，`VWAP0 = $vwap/$close` 恒为 NaN，是死特征。

**Files:**
- Modify: `qlib/contrib/data/handler.py`（文件末尾追加类）
- Test: `tests/misc/test_alpha158_novwap.py`（新建）

- [ ] **Step 1: 写失败的测试**

创建 `tests/misc/test_alpha158_novwap.py`：

```python
"""Alpha158NoVWAP 特征配置测试（纯配置层，不依赖 qlib 数据）。"""


def test_novwap_feature_config_has_157_features_without_vwap0():
    from qlib.contrib.data.handler import Alpha158NoVWAP

    fields, names = Alpha158NoVWAP.get_feature_config(Alpha158NoVWAP)
    assert len(names) == len(fields) == 157
    assert "VWAP0" not in names
    # 其余价格相对值特征仍在
    for kept in ("OPEN0", "HIGH0", "LOW0", "KMID", "ROC5", "VSUMD60"):
        assert kept in names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/misc/test_alpha158_novwap.py -v`
Expected: FAIL，`ImportError: cannot import name 'Alpha158NoVWAP'`

- [ ] **Step 3: 实现 Alpha158NoVWAP**

在 `qlib/contrib/data/handler.py` 末尾（`Alpha158vwap` 类之后）追加：

```python
class Alpha158NoVWAP(Alpha158):
    """Alpha158 去掉 VWAP0（157 维）。

    本仓库数据管线（scripts/data_collector/tushare）未产出 vwap.day.bin，
    VWAP0 恒为 NaN，训练无贡献且在推理侧引入缺失值处理分歧，故剔除。
    """

    def get_feature_config(self):
        conf = {
            "kbar": {},
            "price": {
                "windows": [0],
                "feature": ["OPEN", "HIGH", "LOW"],
            },
            "rolling": {},
        }
        return Alpha158DL.get_feature_config(conf)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/misc/test_alpha158_novwap.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add qlib/contrib/data/handler.py tests/misc/test_alpha158_novwap.py
git commit -m "feat(data): 新增 Alpha158NoVWAP handler，剔除本地数据不存在的 VWAP0 死特征"
```

---

### Task 3: 训练侧接入 Alpha158NoVWAP + ProcessInf

**Files:**
- Modify: `backtest/scripts/run_backtest.py:44-85`（`DATA_HANDLER_CONFIG` 与 `TASK`）

- [ ] **Step 1: 修改 DATA_HANDLER_CONFIG 与 TASK**

```python
DATA_HANDLER_CONFIG = {
    "start_time": "2003-01-02",
    "end_time": "2026-03-10",
    "fit_start_time": "2003-01-02",
    "fit_end_time": "2020-01-10",
    "instruments": MARKET,
    # ProcessInf 处理除法产生的 inf（替换为当日截面均值）。
    # Alpha158 为 PTYPE_A，infer_processors 同时作用于训练(DK_L)与推理(DK_I)，两边口径一致。
    "infer_processors": [{"class": "ProcessInf"}],
}
```

`TASK["dataset"]["kwargs"]["handler"]` 的 class 改为：

```python
            "handler": {
                "class": "Alpha158NoVWAP",
                "module_path": "qlib.contrib.data.handler",
                "kwargs": DATA_HANDLER_CONFIG,
            },
```

- [ ] **Step 2: 快速验证 handler 可实例化、无 inf、157 列**

不跑全量训练，先用短窗口冒烟（约 1-2 分钟）：

```bash
python - <<'EOF'
import numpy as np
import qlib
from qlib.constant import REG_CN
from qlib.utils import init_instance_by_config
from qlib.data.dataset.handler import DataHandlerLP

qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)
h = init_instance_by_config({
    "class": "Alpha158NoVWAP",
    "module_path": "qlib.contrib.data.handler",
    "kwargs": {
        "instruments": "csi300",
        "start_time": "2025-01-01", "end_time": "2025-06-30",
        "fit_start_time": "2025-01-01", "fit_end_time": "2025-06-30",
        "infer_processors": [{"class": "ProcessInf"}],
    },
})
df = h.fetch(col_set="feature", data_key=DataHandlerLP.DK_I)
print("shape:", df.shape)
assert df.shape[1] == 157, df.shape
assert not np.isinf(df.values).any(), "存在 inf，ProcessInf 未生效"
print("OK: 157 列, 无 inf")
EOF
```

Expected: 打印 `OK: 157 列, 无 inf`

- [ ] **Step 3: Commit**

```bash
git add backtest/scripts/run_backtest.py
git commit -m "feat(backtest): 训练管线切换 Alpha158NoVWAP 并统一加 ProcessInf"
```

---

### Task 4: 模拟盘侧接入（handler 按 yaml 动态实例化 + ProcessInf）

**背景：** `SignalGenerator._ensure_handler` 目前硬编码 `Alpha158` 且硬编码 `fit_end_time="2020-01-10"`。改为读 yaml，保证与训练同一 handler 类、同一 processors。

**Files:**
- Modify: `paper_trading/modules/signal_generator.py`（`_ensure_handler`，约 55-75 行；顶部 import）
- Modify: `paper_trading/configs/csi300_topk10.yaml`（handler 段）
- Modify: `paper_trading/config.yaml`（handler 段，保持模板一致）

- [ ] **Step 1: 修改 `_ensure_handler`**

顶部 import 改动：删除 `from qlib.contrib.data.handler import Alpha158`，增加 `from qlib.utils import init_instance_by_config`。

```python
    def _ensure_handler(self, end_date: str):
        """Create or extend the handler so it covers up to end_date."""
        if self._handler is not None and self._handler_end_date >= end_date:
            return

        handler_cfg = self.config["handler"]
        data_cfg = self.config["data"]

        logger.info(
            "Initializing %s handler (end_date=%s)...", handler_cfg["class"], end_date
        )
        self._handler = init_instance_by_config({
            "class": handler_cfg["class"],
            "module_path": handler_cfg["module"],
            "kwargs": {
                "instruments": data_cfg["instruments"],
                "start_time": handler_cfg["start_time"],
                "end_time": end_date,
                "fit_start_time": handler_cfg["start_time"],
                "fit_end_time": handler_cfg["fit_end_time"],
                # 必须与训练侧 run_backtest.py 完全一致
                "infer_processors": [{"class": "ProcessInf"}],
            },
        })
        self._features = self._handler.fetch(
            col_set="feature", data_key=DataHandlerLP.DK_I
        )
        self._handler_end_date = end_date
        logger.info("Handler initialized, features shape: %s", self._features.shape)
```

- [ ] **Step 2: 更新两个 yaml 的 handler 段**

`paper_trading/configs/csi300_topk10.yaml` 与 `paper_trading/config.yaml` 的 handler 段统一改为：

```yaml
# ========== 特征设置 ==========
handler:
  class: "Alpha158NoVWAP"
  module: "qlib.contrib.data.handler"
  start_time: "2003-01-02"
  fit_end_time: "2020-01-10"
```

- [ ] **Step 3: 跑既有测试确认无回归**

Run: `python -m pytest tests/paper_trading/test_signal_generator.py -v`
Expected: 2 passed（`_score_features` 不依赖 handler，Task 1 测试不受影响）

- [ ] **Step 4: handler 初始化冒烟（短窗口，不加载模型）**

```bash
python - <<'EOF'
import sys, yaml
from pathlib import Path
import qlib
from qlib.constant import REG_CN

sys.path.insert(0, str(Path(".").resolve()))
from paper_trading.modules.signal_generator import SignalGenerator

cfg = yaml.safe_load(open("paper_trading/configs/csi300_topk10.yaml"))
cfg["handler"]["start_time"] = "2025-01-01"   # 冒烟用短窗口
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)
gen = SignalGenerator(cfg, Path(".").resolve())
gen._ensure_handler("2025-06-30")
assert gen._features.shape[1] == 157, gen._features.shape
print("OK:", gen._features.shape)
EOF
```

Expected: 打印 `OK: (..., 157)`

- [ ] **Step 5: Commit**

```bash
git add paper_trading/modules/signal_generator.py paper_trading/configs/csi300_topk10.yaml paper_trading/config.yaml
git commit -m "feat(paper_trading): handler 按 yaml 动态实例化，切 Alpha158NoVWAP 并统一 ProcessInf"
```

---

### Task 5: 复权回溯完整性巡检脚本

**背景：** 前复权锚定最后一天（`factor = adj_factor / adj_factor_last`），每次新除权都要求回溯重写历史 bin。若回溯失效，特征会在除权日出现假跳空且无告警。原理：前复权口径下 `close[t]/close[t-1]-1` 应与 `$change`（Tushare pct_chg）在**所有**交易日（含除权日）近似相等；除权日出现大偏差 = 回溯失效。

**Files:**
- Create: `scripts/data_collector/tushare/check_adjust_integrity.py`

- [ ] **Step 1: 实现巡检脚本**

```python
"""前复权回溯完整性巡检。

原理：
  前复权价比值 close[t]/close[t-1] - 1 应与 $change（Tushare pct_chg）在所有
  交易日近似相等（含除权日：复权因子恰好抵消除权跳空）。
  若「除权日」($factor 发生变动的日子) 上出现大偏差，说明该股票的历史
  前复权价没有被增量更新正确回溯重写。

用法：
  python scripts/data_collector/tushare/check_adjust_integrity.py \
      --instruments csi300 --start 2024-01-01 --tol 0.002

退出码：除权日偏差行数 > 0 时返回 1（可接入 cron 告警）。
"""
import argparse
import sys

import numpy as np
import pandas as pd

import qlib
from qlib.constant import REG_CN
from qlib.data import D


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="~/.qlib/qlib_data/cn_data")
    parser.add_argument("--instruments", default="csi300")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--tol", type=float, default=0.002)
    args = parser.parse_args()

    qlib.init(provider_uri=args.provider, region=REG_CN)
    inst = D.instruments(args.instruments)
    df = D.features(
        inst, ["$close", "$change", "$factor"],
        start_time=args.start, end_time=args.end,
    ).dropna(subset=["$close"])

    ratio = df["$close"].groupby(level="instrument").pct_change()
    diff = (ratio - df["$change"]).abs()
    checked = int(diff.notna().sum())
    bad_mask = diff > args.tol

    # 除权日 = $factor 相对前一日发生变化的日子
    factor_chg = (
        df["$factor"].groupby(level="instrument").pct_change().abs() > 1e-8
    )
    bad_exdiv = df[bad_mask & factor_chg]

    print(f"检查区间: {args.start} ~ {args.end or '最新'}, 股票池: {args.instruments}")
    print(f"有效样本: {checked} 行")
    print(f"比值-涨跌幅偏差 > {args.tol}: {int(bad_mask.sum())} 行 "
          f"({bad_mask.sum() / max(checked, 1):.4%})")
    print(f"其中发生在除权日: {len(bad_exdiv)} 行")

    if len(bad_exdiv) > 0:
        print("\n!!! 除权日偏差明细（前 20 行）—— 前复权回溯可能失效：")
        detail = bad_exdiv[["$close", "$change", "$factor"]].copy()
        detail["ratio_ret"] = ratio[bad_mask & factor_chg]
        print(detail.head(20).to_string())
        return 1

    print("OK: 未发现除权日回溯异常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 运行巡检验证当前数据**

Run: `python scripts/data_collector/tushare/check_adjust_integrity.py --instruments csi300 --start 2024-01-01`
Expected: 打印统计并以 `OK: 未发现除权日回溯异常` 结束（exit 0）。若返回 1，**停下来先排查数据管线**（`update_data_to_bin` 的回溯逻辑），修完数据再继续 Task 6。

- [ ] **Step 3: 接入日度数据更新脚本（可选但推荐）**

在 `scripts/data_collector/tushare/run_update_to_bin.sh` 的 `update_data_to_bin` 命令之后追加一行巡检（沿用该脚本已有的日志重定向方式）：

```bash
python "$QLIB_ROOT/scripts/data_collector/tushare/check_adjust_integrity.py" --instruments csi300 --start "$(date -v-90d +%Y-%m-%d 2>/dev/null || date -d '90 days ago' +%Y-%m-%d)" || echo "[WARN] 复权回溯巡检未通过，请人工检查"
```

- [ ] **Step 4: Commit**

```bash
git add scripts/data_collector/tushare/check_adjust_integrity.py scripts/data_collector/tushare/run_update_to_bin.sh
git commit -m "feat(data): 新增前复权回溯完整性巡检脚本并接入日度更新"
```

---

### Task 6: 重训模型、AB 对比、切换模拟盘

**背景：** 特征从 158→157 维且加了 ProcessInf，旧模型不可用。重训后与 `backtest/result/summary.json` 里的旧基线对比，确认无退化，再把模拟盘 yaml 指向新 recorder。

**Files:**
- Modify: `backtest/scripts/run_backtest.py:30`（`N_RUNS`）
- Modify: `paper_trading/configs/csi300_topk10.yaml`（model 段）

- [ ] **Step 1: 备份旧基线结果**

```bash
cp backtest/result/summary.json backtest/result/summary_baseline_158.json
cp backtest/result/all_runs_results.csv backtest/result/all_runs_results_baseline_158.csv
```

- [ ] **Step 2: 设置 N_RUNS=3 并运行训练+回测**

`run_backtest.py` 中 `N_RUNS = 1` 改为 `N_RUNS = 3`（LGB 有随机性，取 3 次评估稳定性）。

Run: `python backtest/scripts/run_backtest.py`（预计 30-90 分钟，后台运行）
Expected: 正常结束，`backtest/result/summary.json` 更新，`all_runs_results.csv` 含 3 行 success 及各自 `train_recorder_id`。

- [ ] **Step 3: AB 对比**

```bash
python - <<'EOF'
import json
new = json.load(open("backtest/result/summary.json"))
old = json.load(open("backtest/result/summary_baseline_158.json"))
keys = ["excess_with_cost_annualized_return", "excess_with_cost_information_ratio",
        "excess_with_cost_max_drawdown", "excess_cum_return"]
print(f"{'指标':45s} {'旧(158+fillna0)':>18s} {'新(157+ProcessInf)':>18s}")
for k in keys:
    o = old.get("metrics_mean", {}).get(k, float("nan"))
    n = new.get("metrics_mean", {}).get(k, float("nan"))
    print(f"{k:45s} {o:>18.4f} {n:>18.4f}")
EOF
```

判定标准：新配置的 `information_ratio` 不低于旧基线 - 0.1，且 `max_drawdown` 不明显恶化（绝对值增幅 < 3pp）。注意旧基线的回测本身没有 fillna(0) 问题（fillna 只发生在模拟盘），所以这里预期是**微小变化**，大幅退化说明改动有 bug，需排查。

- [ ] **Step 4: 选定新模型 recorder 并更新模拟盘 yaml**

从 `backtest/result/all_runs_results.csv` 里选 `excess_with_cost_information_ratio` 居中的一次 run，取其 `train_recorder_id`。experiment_id 用如下命令查（experiment 名为 `train_model_runXX`）：

```bash
rg -l "train_model_run" examples/mlruns/*/meta.yaml
```

更新 `paper_trading/configs/csi300_topk10.yaml` model 段：

```yaml
model:
  experiment_name: "train_model_run02"        # 按实际选定的 run 填写
  experiment_id: "<上面查到的 experiment_id>"
  recorder_id: "<选定 run 的 train_recorder_id>"
  model_class: "qlib.contrib.model.gbdt.LGBModel"
  mlruns_dir: "examples/mlruns"
```

- [ ] **Step 5: 端到端验证模拟盘信号**

```bash
python - <<'EOF'
import sys, yaml
from pathlib import Path
import qlib
from qlib.constant import REG_CN

sys.path.insert(0, str(Path(".").resolve()))
from paper_trading.modules.signal_generator import SignalGenerator

cfg = yaml.safe_load(open("paper_trading/configs/csi300_topk10.yaml"))
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)
gen = SignalGenerator(cfg, Path(".").resolve())
scores = gen.predict("2026-07-10")   # 用最近一个交易日
assert len(scores) > 200, f"打分数量异常: {len(scores)}"
print(scores.sort_values(ascending=False).head(10))
print("OK")
EOF
```

Expected: 打印 top10 打分并输出 `OK`。

- [ ] **Step 6: 记录切换日（模拟盘连续性）**

模型切换会改变信号，在模拟盘日志/README 中注明切换日期与新旧 recorder_id，方便日后归因净值曲线的口径变化。切换尽量安排在某个交易日收盘后、下一个交易日生效。

- [ ] **Step 7: Commit**

```bash
git add backtest/scripts/run_backtest.py paper_trading/configs/csi300_topk10.yaml backtest/result/
git commit -m "chore: 157维+ProcessInf 重训完成，模拟盘切换新模型（AB 对比通过）"
```
