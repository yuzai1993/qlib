# Agent 指南

## 实验规范（强制）

凡涉及模型训练、因子/标签设计、策略调参、回测对比等实验类任务，**必须先阅读并遵循**：

- `backtest/EXPERIMENT_STANDARD.md`（单一事实来源）

不可协商的要点：

1. 基线 B0 固定（实盘 `live_trading/configs/csi300_topk10_live.yaml`：CSI300 · Alpha158 · LGBM · TopkDropout(10,2)，实盘费率 0.00021/0.00071），不得自行变更。
2. 模型与策略分开迭代：Phase M 只改模型（看 IC/RankIC），Phase S 只改策略（看扣费超额 IR/年化/最大回撤）。
3. 固定 5 种子 [42, 1000, 2000, 3000, 4000]；默认只在基线训练池（CSI300）训练，在 4 个测试集（csi300/csi500/csi1000/全A）上评估；仅训练样本类实验才更换训练池。
4. 时间划分固定：valid 2020-01-13~2021-07-15，test 2021-07-16~2026-07-16；禁止用 test 调参。
5. 每个实验（含失败的）登记 `backtest/experiments/registry.jsonl` 并更新 HTML 报告（每个方向独立表格）。

## 环境注意事项

- macOS 下禁止用 heredoc/stdin 运行会触发 Qlib 并行取数的代码（详见 `.cursor/rules/qlib-shell-multiprocessing.mdc`）。
- Python 解释器：`/opt/anaconda3/envs/qlib/bin/python`。

## Cursor Cloud specific instructions

Qlib is a pure-Python quantitative-research library (`pyqlib`). There is no long-running
server or GUI; you "run the app" by executing quant workflows/experiments from the terminal
(e.g. `qrun <config>.yaml` or `python qlib/cli/run.py <config>.yaml`) and by running the test
suite. Standard install/lint/test/build commands live in the `Makefile` and `pyproject.toml`
(`[project.optional-dependencies]`); refer to those rather than duplicating them.

The environment snapshot already has all dependencies installed (via `make dev`), the Cython
extensions compiled, and sample data downloaded. The notes below cover only the non-obvious
gotchas discovered during setup.

### Environment gotchas (already applied in the snapshot)

- The `Makefile` invokes `python`, but the base image only ships `python3`. A
  `/usr/bin/python -> python3` symlink is in place. Don't assume a bare `python` on a fresh box.
- System Python is PEP 668 "externally managed". `pip` is configured with
  `global.break-system-packages=true` (in `~/.config/pip/pip.conf`) so `pip install` works. All
  packages install into the user site (`~/.local`), not system site.
- Console scripts (`black`, `flake8`, `pylint`, `mypy`, `nbqa`, `qrun`, `jupyter`) live in
  `~/.local/bin`. That dir is added to `PATH` in `~/.bashrc`. Interactive shells get it; if you
  run a non-login/non-interactive shell, either use `python -m <tool>` or prepend
  `~/.local/bin` to `PATH` yourself.
- `apt` must be forced to IPv4 (`sudo apt-get -o Acquire::ForceIPv4=true ...`); `archive.ubuntu.com`
  only resolves to IPv6 here and hangs otherwise. `security.ubuntu.com` may still be unreachable,
  which is fine.

### Running workflows / experiments

- `MLFLOW_ALLOW_FILE_STORE=true` MUST be set (exported in `~/.bashrc`). The installed mlflow
  (3.x) rejects the filesystem tracking backend that Qlib uses by default; without this env var
  `qrun`/workflows crash with an `MlflowException` about "maintenance mode".
- Hello-world / smoke run (needs the cn sample data below):
  `qrun examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml`
  It trains LightGBM on Alpha158, reports signal IC/ICIR, and runs a backtest with portfolio
  analysis.

### Data (downloaded into the snapshot; not tracked by git)

Workflows and most tests need data that is NOT in the repo. It is downloaded to
`~/.qlib/qlib_data/cn_data` and `tests/.data/rl`. If missing, re-download:

- Daily cn sample data: `python scripts/get_data.py qlib_data --name qlib_data_simple --target_dir ~/.qlib/qlib_data/cn_data --interval 1d --region cn`
- RL test data: `python scripts/get_data.py download_data --file_name rl_data.zip --target_dir tests/.data/rl`

### Testing

- Run from the `tests/` dir: `cd tests && python -m pytest . -m "not slow"`. The `rl/` tests
  require the RL data above.
- `dependency_tests/test_mlflow.py::test_creating_client` is a timing assertion (client creation
  < 10ms) that is flaky under parallel CPU load; it passes when run in isolation. CI retries the
  suite up to 3x for this reason. Treat an isolated pass as green.
- `make black` may report reformat diffs purely due to the installed Black version being newer
  than the version the repo was formatted with (`.pre-commit-config.yaml` pins Black 23.7.0);
  `flake8` is clean. This is version drift, not a code defect.
