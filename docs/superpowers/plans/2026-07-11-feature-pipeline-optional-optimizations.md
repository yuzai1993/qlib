# 特征管线可选优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在必选修复（157 维 + ProcessInf，见 `2026-07-11-feature-pipeline-required-fixes.md`）落地之后，做三件锦上添花的事：① 标签 CSRankNorm vs CSZScoreNorm 的 AB 回测；② 季度滚动重训机制；③ 补齐 VWAP 数据（采集 amount → 生成 vwap.day.bin，恢复 158 维真实特征）。

**Architecture:** ① 只改 `learn_processors` 配置跑对照实验，不动代码；② 新增独立重训脚本 `backtest/scripts/retrain_rolling.py`，按"训练截止 = 最新数据 - 6 个月、验证 = 最近 6 个月"的扩张窗口重训，输出 recorder 信息供人工切换模拟盘 yaml；③ 改 Tushare collector 采集 amount 并在 Normalize 算出前复权 vwap，需全量重采数据后才能启用。

**Tech Stack:** qlib、LightGBM、Tushare Pro、pytest。

**依赖与顺序：**
- 前置：必选修复计划已全部完成（模型已是 157 维 + ProcessInf）。
- Task 1（标签 AB）成本最低、独立，先做。
- Task 2（滚动重训）依赖 Task 1 的结论（用胜出的 label processor 配置）。
- Task 3（VWAP 补齐）需要全量重新采集数据（数小时级）+ 重训，收益不确定，放最后，可单独排期甚至放弃。

---

### Task 1: 标签处理 AB 实验（CSZScoreNorm vs CSRankNorm）

**背景：** 当前 label 用截面 z-score（`CSZScoreNorm`）。A 股涨跌停造成收益分布肥尾截断，z-score 对尾部敏感；`CSRankNorm`（截面排名归一化）对异常值更稳健，是 qlib 官方 LightGBM benchmark 的常用替代。用同一份数据、同一套超参各跑 3 次对比。

**Files:**
- Create: `backtest/scripts/ab_label_norm.py`

- [ ] **Step 1: 实现 AB 脚本**

复用 `run_backtest.py` 的配置结构，只让 `learn_processors` 可变：

```python
"""标签处理 AB 实验：CSZScoreNorm vs CSRankNorm，各跑 N 次训练+回测。

结果写入 backtest/result/ab_label_norm.csv 与 ab_label_norm_summary.json。
"""
import json
import sys
from copy import deepcopy
from pathlib import Path

import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(QLIB_ROOT / "backtest" / "scripts"))

import qlib
from qlib.constant import REG_CN

# 复用主管线的配置与指标提取，保证两组实验口径一致
from run_backtest import (
    DATA_HANDLER_CONFIG, TASK, PORT_ANALYSIS_CONFIG, PROVIDER_URI,
    extract_metrics, run_single,  # run_single 若不便复用，可仿写，见下方说明
)

N_RUNS_PER_ARM = 3
RESULT_DIR = QLIB_ROOT / "backtest" / "result"

ARMS = {
    "cszscore": [
        {"class": "DropnaLabel"},
        {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}},
    ],
    "csrank": [
        {"class": "DropnaLabel"},
        {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
    ],
}


def build_task(learn_processors):
    task = deepcopy(TASK)
    handler_kwargs = task["dataset"]["kwargs"]["handler"]["kwargs"]
    handler_kwargs = dict(handler_kwargs)  # 不污染原配置
    handler_kwargs["learn_processors"] = learn_processors
    task["dataset"]["kwargs"]["handler"]["kwargs"] = handler_kwargs
    return task


def main():
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    rows = []
    for arm_name, procs in ARMS.items():
        for i in range(1, N_RUNS_PER_ARM + 1):
            task = build_task(procs)
            # 训练+回测逻辑与 run_backtest.run_single 相同，仅 task 不同。
            # 若 run_single 无法直接传入 task，把 run_single 改造为
            # run_single(run_idx, task=TASK) 的带默认参形式（对主管线无行为影响）。
            result = run_single(f"{arm_name}_{i:02d}", task=task)
            result["arm"] = arm_name
            rows.append(result)
            pd.DataFrame(rows).to_csv(RESULT_DIR / "ab_label_norm.csv", index=False)

    df = pd.DataFrame(rows)
    ok = df[df["status"] == "success"]
    summary = {}
    for arm_name in ARMS:
        sub = ok[ok["arm"] == arm_name]
        summary[arm_name] = {
            c: {"mean": float(sub[c].mean()), "std": float(sub[c].std())}
            for c in ("excess_with_cost_annualized_return",
                      "excess_with_cost_information_ratio",
                      "excess_with_cost_max_drawdown")
            if c in sub.columns
        }
    with open(RESULT_DIR / "ab_label_norm_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

配套小改动（`backtest/scripts/run_backtest.py`）：
1. `run_single(run_idx)` 签名改为 `run_single(run_idx, task=None)`，函数体开头加 `task = task or TASK`，内部 `TASK` 引用改为 `task`；
2. AB 脚本传入的 `run_idx` 是字符串（如 `"csrank_01"`），而 `run_single` 内部有 `f"train_model_run{run_idx:02d}"` 这类整数格式化，会对字符串报错——把函数内所有 `{run_idx:02d}` 改为 `{run_idx}`，主流程调用处相应传 `f"{i:02d}"` 保持实验名不变。

改完后跑 `python backtest/scripts/run_backtest.py` 确认主管线行为不变。

- [ ] **Step 2: 冒烟（截断数据快速验证脚本能跑通）**

临时把 `N_RUNS_PER_ARM` 改为 1、`ARMS` 只留 `csrank`，跑通后还原。

Run: `python backtest/scripts/ab_label_norm.py`
Expected: 生成 `backtest/result/ab_label_norm.csv`，status=success

- [ ] **Step 3: 正式运行（2 组 × 3 次，预计 1-3 小时，后台）**

Run: `python backtest/scripts/ab_label_norm.py`
Expected: `ab_label_norm_summary.json` 含两组的均值±标准差

- [ ] **Step 4: 判定并落地**

判定规则（写进结论，避免拍脑袋）：
- `csrank` 的 `information_ratio` 均值高于 `cszscore` 至少 0.05 且标准差不明显更大 → 切换：把 `run_backtest.py` 的 `DATA_HANDLER_CONFIG` 加上 `"learn_processors": [{"class": "DropnaLabel"}, {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}}]`，并触发一次正式重训（复用必选计划 Task 6 的流程切模拟盘）。
- 差异在噪声范围内 → 保持 CSZScoreNorm，在本计划文件底部记录实验结论备查。

- [ ] **Step 5: Commit**

```bash
git add backtest/scripts/ab_label_norm.py backtest/scripts/run_backtest.py backtest/result/ab_label_norm*
git commit -m "exp: 标签 CSRankNorm vs CSZScoreNorm AB 回测及结论"
```

---

### Task 2: 季度滚动重训机制

**背景：** 模拟盘长期使用 fit 到 2020-01-10 的固定模型，特征-收益关系会漂移。建立"每季度用扩张窗口重训一次"的半自动流程：脚本负责重训 + 产出近段回测指标 + 打印切换信息，是否切换由人工确认（避免自动切到一个坏模型）。

**窗口设计（扩张窗口）：**
- train: `2003-01-02` ~ `最新数据日 - 12 个月`
- valid: `train_end + 1 天` ~ `最新数据日 - 1 个月`（供 LGB 早停）
- 评估回测: 最近 24 个月（用新模型对近两年做样本内/外混合回测，仅作 sanity check，指标不做严格比较——train 覆盖了其中一段）

**Files:**
- Create: `backtest/scripts/retrain_rolling.py`
- Modify: `paper_trading/run_daily.sh`（可选，加提醒）

- [ ] **Step 1: 实现重训脚本**

```python
"""季度滚动重训：扩张窗口重训 LGBModel，输出 recorder 信息与近段回测指标。

用法：
  python backtest/scripts/retrain_rolling.py            # 重训并打印切换信息
  python backtest/scripts/retrain_rolling.py --dry-run  # 只打印将使用的时间窗口

切换模拟盘（人工确认后）：
  将打印出的 experiment_id / recorder_id 填入
  paper_trading/configs/csi300_topk10.yaml 的 model 段。
"""
import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pandas as pd

QLIB_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(QLIB_ROOT))
sys.path.insert(0, str(QLIB_ROOT / "backtest" / "scripts"))

import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.utils import init_instance_by_config
from qlib.workflow import R

from run_backtest import TASK, PORT_ANALYSIS_CONFIG, PROVIDER_URI, extract_metrics
from qlib.workflow.record_temp import SignalRecord, PortAnaRecord

RESULT_DIR = QLIB_ROOT / "backtest" / "result"


def latest_data_date() -> pd.Timestamp:
    cal = D.calendar(freq="day")
    return pd.Timestamp(cal[-1])


def build_segments(latest: pd.Timestamp) -> dict:
    train_end = latest - pd.DateOffset(months=12)
    valid_end = latest - pd.DateOffset(months=1)
    return {
        "train": ("2003-01-02", train_end.strftime("%Y-%m-%d")),
        "valid": ((train_end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                  valid_end.strftime("%Y-%m-%d")),
        "test": ((valid_end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                 latest.strftime("%Y-%m-%d")),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    latest = latest_data_date()
    segments = build_segments(latest)
    print("最新数据日:", latest.date())
    print("窗口:", json.dumps(segments, ensure_ascii=False, indent=2))
    if args.dry_run:
        return

    task = deepcopy(TASK)
    hk = dict(task["dataset"]["kwargs"]["handler"]["kwargs"])
    hk["end_time"] = segments["test"][1]
    hk["fit_end_time"] = segments["train"][1]
    task["dataset"]["kwargs"]["handler"]["kwargs"] = hk
    task["dataset"]["kwargs"]["segments"] = segments

    stamp = datetime.now().strftime("%Y%m%d")
    exp_name = f"retrain_rolling_{stamp}"

    model = init_instance_by_config(task["model"])
    dataset = init_instance_by_config(task["dataset"])
    with R.start(experiment_name=exp_name):
        model.fit(dataset)
        R.save_objects(trained_model=model)
        rec = R.get_recorder()
        train_rid, exp_id = rec.id, rec.experiment_id

    # 近 24 个月 sanity-check 回测（含样本内段，仅看方向不做严格 AB）
    port_cfg = deepcopy(PORT_ANALYSIS_CONFIG)
    port_cfg["backtest"]["start_time"] = (
        latest - pd.DateOffset(months=24)).strftime("%Y-%m-%d")
    port_cfg["backtest"]["end_time"] = latest.strftime("%Y-%m-%d")
    port_cfg["strategy"]["kwargs"]["model"] = model
    port_cfg["strategy"]["kwargs"]["dataset"] = dataset
    with R.start(experiment_name=f"{exp_name}_bt"):
        rec = R.get_recorder()
        SignalRecord(model, dataset, rec).generate()
        PortAnaRecord(rec, port_cfg, "day").generate()
        analysis = rec.load_object("portfolio_analysis/port_analysis_1day.pkl")
        report = rec.load_object("portfolio_analysis/report_normal_1day.pkl")

    metrics = extract_metrics(analysis, report)
    out = {
        "date": stamp, "experiment_name": exp_name,
        "experiment_id": exp_id, "recorder_id": train_rid,
        "segments": segments, "sanity_metrics": metrics,
    }
    out_path = RESULT_DIR / f"retrain_{stamp}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n→ 人工确认指标后，把 experiment_id/recorder_id 填入 "
          f"paper_trading/configs/csi300_topk10.yaml 的 model 段（experiment_name 一并更新）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: dry-run 验证窗口计算**

Run: `python backtest/scripts/retrain_rolling.py --dry-run`
Expected: 打印最新数据日与三段窗口，日期符合"train 到最新-12月、valid 到最新-1月"

- [ ] **Step 3: 完整跑一次（预计 20-60 分钟，后台）**

Run: `python backtest/scripts/retrain_rolling.py`
Expected: 生成 `backtest/result/retrain_YYYYMMDD.json`，含 recorder_id 与 sanity 指标

- [ ] **Step 4: 建立季度节奏**

crontab 增加提醒式任务（只重训产出报告，不自动切换）：

```
0 20 1 1,4,7,10 * cd /Users/yuxianqi/Project/qlib && python backtest/scripts/retrain_rolling.py >> logs/retrain.log 2>&1
```

切换 SOP（写入本计划即为文档）：
1. 查看 `backtest/result/retrain_*.json` 的 sanity 指标，IR 为正且与上季模型同量级；
2. 更新 `paper_trading/configs/csi300_topk10.yaml` 的 model 段三个字段；
3. 在模拟盘日志记录切换日与新旧 recorder_id（净值归因需要）；
4. 保留旧 recorder 不删，可随时回滚。

- [ ] **Step 5: Commit**

```bash
git add backtest/scripts/retrain_rolling.py
git commit -m "feat(backtest): 季度滚动重训脚本（扩张窗口 + sanity 回测 + 人工切换 SOP）"
```

---

### Task 3: 补齐 VWAP 数据（amount → vwap.day.bin，恢复 158 维）

**背景：** Tushare 日线接口本身返回 `amount`（成交额，千元）与 `vol`（成交量，手）。采集 amount 后可算真实日均价 `vwap = amount*1000 / (vol*100)`（元/股），再乘 factor 得前复权 vwap。这样 VWAP0 变成真实特征，还可支持 `deal_price=vwap` 的更真实撮合。
**代价：** 存量 source CSV 没有 amount 列，需**全量重新采集**（数小时 + Tushare 积分限流），然后全量 normalize + dump。收益不确定（VWAP0 只是 158 维之一），建议单独排期。

**Files:**
- Modify: `scripts/data_collector/tushare/collector.py`（`get_data` 约 206 行、`download_index_data` 约 239 行、`TushareNormalize1d.normalize` 约 265-318 行）
- Modify: `scripts/data_collector/tushare/README.md`（字段约定）
- Test: `tests/misc/test_tushare_vwap.py`（新建）

- [ ] **Step 1: 写失败的测试（Normalize 层，不依赖网络）**

创建 `tests/misc/test_tushare_vwap.py`：

```python
"""TushareNormalize1d 的 vwap 计算测试。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "data_collector" / "tushare"))


def _make_normalizer():
    from collector import TushareNormalize1d
    obj = TushareNormalize1d.__new__(TushareNormalize1d)  # 跳过 __init__ 的日历拉取
    obj._date_field_name = "date"
    obj._symbol_field_name = "symbol"
    obj._calendar_list = []
    return obj


def test_vwap_is_amount_over_volume_times_factor():
    norm = _make_normalizer()
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
        "symbol": ["sh600000", "sh600000"],
        "open": [10.0, 10.5], "high": [11.0, 11.0],
        "low": [9.5, 10.0], "close": [10.5, 10.8],
        "volume": [1000.0, 2000.0],          # 手
        "amount": [1060.0, 2140.0],          # 千元
        "adj_factor": [2.0, 2.0],            # 无除权 → factor 恒 1
        "pct_chg": [1.0, 2.857],
    })
    out = norm.normalize(df)
    # raw_vwap = amount*1000 / (volume*100)；factor=1 → vwap = raw_vwap
    expected = np.array([1060.0 * 1000 / (1000 * 100), 2140.0 * 1000 / (2000 * 100)])
    assert "vwap" in out.columns
    np.testing.assert_allclose(out["vwap"].values, expected, rtol=1e-9)


def test_vwap_respects_front_adjustment():
    norm = _make_normalizer()
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
        "symbol": ["sh600000", "sh600000"],
        "open": [10.0, 5.25], "high": [11.0, 5.5],
        "low": [9.5, 5.0], "close": [10.5, 5.4],
        "volume": [1000.0, 4000.0],
        "amount": [1060.0, 2140.0],
        "adj_factor": [1.0, 2.0],            # 除权日 → 前日 factor = 0.5
        "pct_chg": [1.0, 2.857],
    })
    out = norm.normalize(df)
    raw_vwap_day1 = 1060.0 * 1000 / (1000 * 100)
    assert abs(out["vwap"].iloc[0] - raw_vwap_day1 * 0.5) < 1e-9
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/misc/test_tushare_vwap.py -v`
Expected: FAIL（输出无 `vwap` 列）

- [ ] **Step 3: 实现采集与归一化改动**

`get_data`（约 206 行）的列清单加 `amount`：

```python
        cols = ["date", "open", "high", "low", "close", "volume", "amount", "adj_factor", "pct_chg"]
```

`download_index_data`（约 239 行）同步加：

```python
                out_cols = ["date", "open", "high", "low", "close", "volume", "amount", "adj_factor", "pct_chg"]
```

`TushareNormalize1d.normalize`：在"前复权"代码块（`df["factor"] = adj_series / adj_last` 之后、价格乘 factor 的循环之前）插入 vwap 计算：

```python
        # vwap：Tushare amount 单位千元、volume 单位手 → 元/股，再前复权
        if "amount" in df.columns:
            raw_vol_shares = df["volume"] * 100.0
            df["vwap"] = np.where(
                raw_vol_shares > 0,
                df["amount"] * 1000.0 / raw_vol_shares * df["factor"],
                np.nan,
            )
        else:
            df["vwap"] = np.nan
```

同函数末尾输出列加 `vwap`（在 factor 之前）：

```python
        out_cols = [date_col, sym_col, "open", "high", "low", "close", "volume", "vwap", "factor", "change"]
```

停牌置 NaN 的行也要覆盖 vwap，把该行改为：

```python
        df.loc[(df["volume"] <= 0) | df["volume"].isna(), self.COLUMNS + ["change", "vwap"]] = np.nan
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/misc/test_tushare_vwap.py -v`
Expected: 2 passed

- [ ] **Step 5: 更新 README 字段约定**

`scripts/data_collector/tushare/README.md` 约定小节补一行：

```markdown
- **vwap**：前复权日均价 = amount×1000 / (vol×100) × factor（元/股；停牌为空）
```

- [ ] **Step 6: Commit（代码先行，数据重采单独执行）**

```bash
git add scripts/data_collector/tushare/collector.py scripts/data_collector/tushare/README.md tests/misc/test_tushare_vwap.py
git commit -m "feat(data): Tushare 采集 amount 并产出前复权 vwap 字段"
```

- [ ] **Step 7: 全量重采 + 重灌 bin（数小时，注意 Tushare 限流）**

**目录约定（与日常调度一致，重要）**：日常调度 `run_update_to_bin.sh` 每天会用
`./source` + `./normalize` 全量 normalize + dump。回填必须落到**同一目录**，
否则下一次 cron 会用不含 amount 的旧 source 重灌 bin，抹掉历史 vwap。

```bash
cd scripts/data_collector/tushare
# 备份旧数据与旧 source（不放 tushare 目录下，避免被误当作 source 读取）
cp -r ~/.qlib/qlib_data/cn_data ~/.qlib/qlib_data/cn_data_bak_$(date +%Y%m%d)
mv ./source ~/.qlib/tushare_source_bak_$(date +%Y%m%d)
# 全量采集到调度任务使用的默认目录（存量 source 无 amount，必须重采）
python collector.py download_data --source_dir ./source --start 2003-01-02 --end $(date +%Y-%m-%d)
python collector.py normalize_data --source_dir ./source --normalize_dir ./normalize
cd ../../..
python scripts/dump_bin.py dump_all \
  --data_path scripts/data_collector/tushare/normalize \
  --qlib_dir ~/.qlib/qlib_data/cn_data \
  --freq day --date_field_name date --symbol_field_name symbol \
  --exclude_fields symbol,date --file_suffix .csv
```

Expected: `~/.qlib/qlib_data/cn_data/features/sh600000/` 下出现 `vwap.day.bin`

- [ ] **Step 8: 数据校验**

```bash
python - <<'EOF'
import qlib
from qlib.constant import REG_CN
from qlib.data import D
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region=REG_CN)
df = D.features(["SH600000"], ["$vwap", "$low", "$high", "$close"], start_time="2025-01-01")
cover = df["$vwap"].notna().mean()
in_range = ((df["$vwap"] >= df["$low"] * 0.99) & (df["$vwap"] <= df["$high"] * 1.01)).mean()
print(f"vwap 覆盖率: {cover:.2%}, 落在[low,high]内比例: {in_range:.2%}")
assert cover > 0.95 and in_range > 0.99
print("OK")
EOF
```

Expected: 覆盖率 >95%、区间内比例 >99%、输出 OK。另跑一次必选计划 Task 5 的 `check_adjust_integrity.py` 确认重灌后复权一致性无恙。

- [ ] **Step 9: 切回 158 维并重训**

把训练侧（`backtest/scripts/run_backtest.py`）与模拟盘 yaml 的 handler 从 `Alpha158NoVWAP` 改回 `Alpha158`，然后执行必选计划 Task 6 的重训-AB-切换流程。AB 对比对象是 157 维基线：`information_ratio` 无提升则说明 VWAP0 价值有限，可保留数据但继续用 157 维（vwap 数据仍可用于后续 `deal_price=vwap` 实验）。

- [ ] **Step 10: Commit**

```bash
git add backtest/scripts/run_backtest.py paper_trading/configs/csi300_topk10.yaml paper_trading/config.yaml
git commit -m "feat: vwap 数据补齐后恢复 Alpha158 全 158 维（AB 结论见 result）"
```

---

## 附注：分红口径（无代码改动，仅提醒）

回测/模拟盘用前复权价撮合 ≈ 隐含"分红自动再投资、免红利税"。与真实券商账户对账时预期系统性偏乐观约 0.2-0.3%/年（沪深300 股息率 × 税率量级）。做实盘迁移时再考虑现金分红建模，当前阶段不动。
