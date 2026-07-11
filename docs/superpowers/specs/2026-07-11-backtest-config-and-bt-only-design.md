# 回测配置外置与免重训回测 Design

**Date:** 2026-07-11  
**Status:** Approved for planning  
**Approach:** A — 单 YAML + 双模式（`train_backtest` / `backtest_only`），CLI 仅保留 `--config`

## Goal

1. 将 `run_backtest.py` 内硬编码配置抽到 `backtest/configs/` 下的 YAML。
2. 支持仅改 test/回测日期、复用已有训练结果，不重训模型（`backtest_only`）。
3. CLI 极简：只传 `--config`；`note` / `n_runs` / `from_session` / 日期覆盖等全部进 YAML。

## Non-Goals

- 滚动重训、多 config 批量扫描。
- 从任意 mlruns recorder 路径直接加载（只支持从结果 session 目录加载）。
- 合并模拟盘 yaml 与回测 yaml 为同一套 schema。
- 拆开「信号区间」与「组合回测区间」的独立 CLI/字段（`test_start/end` 同时覆盖两者）。

## Config Layout

目录：`backtest/configs/`  
默认文件：`csi300_lgbm.yaml`（内容从当前脚本硬编码迁出）。

```yaml
run:
  mode: train_backtest          # 或 backtest_only
  note: ""
  n_runs: 1                     # backtest_only 时强制按 1，忽略更大值
  from_session: null            # backtest_only 必填：结果目录名或路径
  from_run: 1
  test_start: null              # 可选；同时覆盖 segments.test[0] 与 backtest.start_time
  test_end: null                # 可选；同时覆盖 segments.test[1] 与 backtest.end_time

data:
  provider_uri: "~/.qlib/qlib_data/cn_data"
  region: "cn"
  instruments: "csi300"
  benchmark: "SH000300"
  handler:
    class: "Alpha158NoVWAP"
    module_path: "qlib.contrib.data.handler"
    start_time: "2003-01-02"
    end_time: "2026-03-10"
    fit_start_time: "2003-01-02"
    fit_end_time: "2020-01-10"
    infer_processors:
      - class: ProcessInf

segments:
  train: ["2003-01-02", "2020-01-10"]
  valid: ["2020-01-13", "2023-09-15"]
  test:  ["2023-09-18", "2026-03-10"]

model:
  class: "LGBModel"
  module_path: "qlib.contrib.model.gbdt"
  kwargs: { ... }

strategy:
  class: "TopkDropoutStrategy"
  module_path: "qlib.contrib.strategy.signal_strategy"
  topk: 50
  n_drop: 5

backtest:
  start_time: "2023-09-18"
  end_time: "2026-03-10"
  account: 1000000
  exchange_kwargs: { ... }
```

### Resolution rules

| 项 | `train_backtest` | `backtest_only` |
|----|------------------|-----------------|
| handler class / 特征维 | 本 YAML `data.handler.class` | **源 session `meta.handler`** 的类名（避免 157/158 错配）；其余 kwargs 用本 YAML `data.handler` |
| model 超参 | 本 YAML `model`（用于训练） | 不训练；加载源 session 的 `trained_model` |
| strategy / exchange / account | 本 YAML | 本 YAML（允许敏感性分析） |
| test / backtest 日期 | YAML，再应用 `run.test_start/end` | 同左；未覆盖则用本 YAML（不是强制用源 session 原区间） |
| 模型文件 | 新训 | `from_session` → `run_XX/mlruns_link.json` → `{train_artifacts}/artifacts/trained_model` 直接反序列化（不依赖 mlruns 注册表按实验名查找） |

覆盖优先级（日期）：`run.test_start/end` > YAML `segments.test` / `backtest.*`。

**日期对齐规则（两种模式通用）**：最终测试区间确定后——

- `segments.test` 与 `backtest.start_time/end_time` 均设为该区间；
- 若 `test_end` 晚于 `data.handler.end_time`，自动将 handler `end_time` 延长到 `test_end`；
- handler `start_time` **保持 YAML 原值，不得收窄**到 `test_start`（Alpha158 滚动窗口最长 60 日，需要测试区间之前的历史数据）。

**特征口径约束（backtest_only）**：handler 会重新实例化，因此 `fit_start_time` / `fit_end_time` / `infer_processors` 必须与源训练一致（即不要在 backtest_only 的 YAML 里改动这些字段）。当前管线只用无状态的 `ProcessInf`，重建无副作用；若将来引入可学习 processor（如 CSZScoreNorm 作用于特征），需另行保存/恢复 handler 状态，属本设计之外。

**旧 session 兼容**：源 session `meta.json` 缺 `handler` 字段时，打印警告并回退用本 YAML 的 `data.handler.class`（由用户自行保证维数匹配）；模型加载后若特征维数与 handler 输出不一致，LightGBM 预测会直接报错，视为正常失败。

`--config` 可为绝对/相对路径，或相对 `backtest/configs/` 的文件名；省略则默认 `csi300_lgbm.yaml`。`run.mode` 缺省时默认 `train_backtest`。

## Dual Mode Behavior

### Mode `train_backtest`

1. 加载 YAML，应用 `test_start/end` 覆盖。
2. 训练 → 回测 → 报告归档（与现逻辑一致）。
3. 支持 `n_runs > 1`。

### Mode `backtest_only`

1. 解析 `from_session`（绝对路径，或相对 `backtest/result/` 的目录名）。
2. 读 `meta.json` 与 `run_{from_run:02d}/mlruns_link.json`。
3. 从 `mlruns_link.train_artifacts` 直接反序列化 `artifacts/trained_model`。
4. 用源 session 的 handler class + 当前 YAML 的 handler kwargs / 日期 / 策略组装 dataset（按上文日期对齐规则）。
5. `SignalRecord` 生成信号 + `PortAnaRecord` 回测。
6. **不**新建 train experiment；新建 backtest experiment + 新 session 目录。
7. `n_runs` 视为 1（LGBM 推理与回测均为确定性，多跑无意义；若配置 >1 打印警告）。

### Failure conditions（立即退出）

- `mode=backtest_only` 但缺少 `from_session` / 目录不存在。
- 缺少 `mlruns_link.json` 或 `trained_model` 不可加载。
- `from_run` 对应目录不存在。
- 覆盖后的日期区间为空、`start > end`，或与数据日历无交集。
- `run.mode` 不是 `train_backtest` / `backtest_only` 之一。

## CLI

```bash
python backtest/scripts/run_backtest.py
python backtest/scripts/run_backtest.py --config csi300_lgbm.yaml
python backtest/scripts/run_backtest.py --config /path/to/bt_only.yaml
```

删除现有 CLI：`--note`、`--n-runs`、`--handler`（以及设计中曾讨论过的 `--from-session` / `--test-*`）。

## Archive / meta

两种模式均写入 `backtest/result/YYYYMMDD_HHMMSS[_note]/`，复用 `report_utils.py`。

`backtest_only` 的 `meta.json` 额外字段：

- `mode: backtest_only`
- `source_session`、`source_run`
- `overrides`（实际生效的 `test_start/end` 等）
- `config_path`

`mlruns_link.json`：`train_*` 指向源 session 的 train recorder；`backtest_*` 为本次新建。

## Code touchpoints

| 路径 | 动作 |
|------|------|
| `backtest/configs/csi300_lgbm.yaml` | 新增（默认配置） |
| `backtest/scripts/config_loader.py` | 新增：加载、解析路径、合并 `test_*` 覆盖、校验 `run` |
| `backtest/scripts/run_backtest.py` | 改造：读 config；拆 `run_train_backtest` / `run_backtest_only` |
| `backtest/scripts/report_utils.py` | 不改 |

可选：额外示例 `backtest/configs/csi300_lgbm_bt_only.example.yaml`（`mode: backtest_only` 模板）。

## Example: backtest-only config

```yaml
# 继承同一套策略/数据描述；只改 run 段即可
run:
  mode: backtest_only
  note: "bt_only_2024起"
  from_session: "20260711_141113_report_archive_vwap_ready"
  from_run: 1
  test_start: "2024-01-01"
  test_end: "2026-03-10"

# 以下可与 csi300_lgbm.yaml 相同
# - handler class 在 backtest_only 时被源 session meta 覆盖
# - model 段在 backtest_only 时被忽略（模型直接从源 session 加载）
data: { ... }
segments: { ... }
model: { ... }
strategy: { ... }
backtest: { ... }
```

## Testing

- 加载默认 yaml，断言关键字段存在且与旧硬编码一致。
- `test_start/end` 合并后 `segments.test` 与 `backtest` 日期一致；`test_end` 超出 handler `end_time` 时自动延长；handler `start_time` 不被改动。
- `backtest_only` 缺少 `from_session` 时退出码非 0。
- `backtest_only` 时 handler class 取自源 session `meta.handler`，即使当前 YAML 写了别的类。
- 冒烟：对已有 session 跑 `backtest_only`（短区间），产出 `index.html` 且未新建 train experiment（train link 指向源 recorder）。
