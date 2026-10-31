# Agent 指南

## 实验规范（强制）

凡涉及模型训练、因子/标签设计、策略调参、回测对比等实验类任务，**必须先阅读并遵循**：

- `backtest/EXPERIMENT_STANDARD.md`（单一事实来源）

不可协商的要点：

1. CSI1000 研究模型基线为 B6-M；研究策略基线为 B4-S（`topk=22,n_drop=2,hold_thresh=2,risk_degree=0.90`，见 `backtest/EXPERIMENT_STANDARD.md`）。CSI1000 历史实盘研究配置 `live_trading/configs/csi1000_b6m_b2s_postclose_real.yaml`（参数已切到 B4-S）与 `live_trading/configs/csi1000_b6m_b2s_postclose.yaml` 仅保留历史账本/配置信息；2026-10-31 已删除两份对应 parity 配置，缺少 parity 时发布门禁会报错，不能直接恢复发布。2026-10-30 起，Mac 当前生产配置为 `alla_v2_top5d1f100_postclose_real_active`（策略 ID `alla_v2_top5d1f100_postclose_real`；历史 v2 五种子、top5/drop1/hold1/f100、risk_degree=1.0；生产根 `/Users/yuxianqi/Project/qlib`），工作日 20:00 调度，平台监控端口 8090。旧 `alla_v4_ladder_k1h5_postclose_real` 保留作历史/回退，不能按活动配置调用；`alla_v4_ladder_k3h5_postclose_real` 与 `csi300_topk10_live` 仅保留历史配置/账本供只读查阅，2026-10-31 已删除对应 parity 配置并移除 live YAML 的 parity 映射，不能直接恢复发布，恢复前须重建并验证配对。parity 配置统一归入 `backtest/experiments/`，`backtest/configs/` 不再维护 parity；旧阶梯 K1 对照档位于 `backtest/experiments/_shared/deployment-parity/alla_v4_ladder_k1h5_parity.yaml`；当前 Top5 的配对仍在 `backtest/experiments/_shared/deployment-parity/alla_v2_top5d1f100_parity.yaml`。Windows 执行器启停另行核验。部署记录见 `live_trading/BT_V5_CUTOVER.md`。Phase M v1（全A）当前模型基线为 **v2 · M0 H20 ES（Top5早停）**（`regime-adapt/m0-h20-t5h5-es-v1`；2026-10-31用户明确选回，登记以最新`current_baseline: true`为准；早停=评估窗`top5_h5_net_ann`，现行评估仍为Top3×H5）。v3/v4保留历史版本与原实验对照。执行层回测基线为 **s5 · 历史模型 v2 M0 H20 ES × top5d1/hold1/f100**（`TopkDropoutStrategy` topk=5、n_drop=1、hold_thresh=1、force_sell_rank=100、risk_degree=0.90；`baseline/phase-m-v1-bt-v5`）。s5 保留 100 万、risk_degree=0.90、原费用和历史选型窗口；模型研究基线为v2，生产Top5 的资金与 risk_degree=1.0 口径单独维护。CSI1000 每个方向表第一行为对应 baseline；Phase M v1 总报告是四块（历史 baseline 版本 / 分年 / 分风格 / 实验记录），第 1 块含全局 RankIC。CSI1000 Phase S 的 baseline 表须附邻域行（与上行同列，取轴向邻域各绝对指标 P25；不单独挂邻域 IR P25 列），晋升前必看。
2. 模型与策略分开迭代：Phase M 只改模型（看 IC/RankIC），Phase S 只改策略（看扣费绝对收益夏普/年化/回撤，以及 Alpha/Beta/基准涨幅；晋升看邻域行绝对指标 P25，不是自身点复制）。
3. 固定 5 种子 [42, 1000, 2000, 3000, 4000]；默认只在基线训练池（CSI1000）训练，在 3 个测试集（csi1000/csi300/csi500）上评估（全A 默认不测）；仅训练样本类实验才更换训练池。
4. 时间划分固定：valid 2020-01-13~2021-07-15，test 2021-07-16~2026-07-16；禁止用 test 调参。
5. 每个实验（含失败的）登记 `backtest/experiments/registry.jsonl` 并更新 HTML 报告。CSI1000 每个方向一张表；Phase M v1 按第 5.1.2 节四块重建 `phase_m_v1_report.html`。
6. 实验结束后清理 `mlruns/` 与 `backtest/result/`：只保留当前 baseline 与最佳合格候选五种子产物，避免磁盘打爆（详见规范 6.3）。

## 任务收尾（强制）

- 一次性迁移、修复和核验脚本优先放在仓库外的任务临时目录，并记录其路径；正式运行工具才放入仓库。
- 完成必要验证后，删除本任务创建且已无后续用途的临时脚本、专用 fixture、工作副本和临时备份；不得把这些材料作为任务结果遗留。
- 保留正式运行脚本、可复用测试、审计 JSON、必要文档及实际研究配置、报告、数据和模型；临时工具清理不得替代实验产物保留规则。
- 删除前先检查引用，同步修正文档和测试；可复用逻辑需要保留时，先归入正式模块再删除一次性入口。
- 只清理来源和用途已确认的任务材料，不按宽泛名称或目录批量删除用户原件、其它任务材料或大模型。
- 收尾检查 `git status`、`git diff --check` 和任务临时目录，确认引用有效、必要证据已保存且无临时工具残留。

## 环境注意事项

- 全A模型版本使用`vN`，执行层策略版本展示使用`sN`（当前模型v2、策略s5）。冻结原始登记的`bt_version: vN`与`baseline/phase-m-v1-bt-vN` ID保留审计和兼容用途；平台、CSV及新生成报告使用统一展示字段，禁止把策略版本当作模型版本。

- 2026-10-31 按用户明确要求，`backtest/experiments/` 保留全A模型6个及 `phase-m-v1-bt` 策略源实验17个，共23个源实体、24条登记事件；当前失败策略已删除，后续实验仍须按规范登记失败事件。重复候选已归入s2/s3/s4/s5来源；14个策略变体归5组，另有3条独立策略，平台记录页显示6模型＋8策略入口，Benchmark单独保留4模型＋5策略版本。组元数据为 `entity_type: experiment_group`，成员在 `<规则族>/<组名>/variants/<变体>/`，组本身不进registry、不决定晋升；结论编辑只写组的 `metadata.json.analysis`。审计见 `backtest/experiments/_catalog/audits/20261031-strategy-experiment-groups/`。H1/H5/H10及H20 v1已归入训练目标汇总，v1冻结来源由 `historical_baselines` 保留，供历史对照与模型产物保护；v3重复单种子集成已删除。CSI1000条款保留历史研究定义，不表示旧归档仍存在。历史范围审计见 `backtest/experiments/_catalog/audits/20261031-retained-experiments/validation.json`。
- 模型实验按实际改动放在 `models/training-target/`、`early-stopping/`、`model-structure/`；`metadata.json.archive_path` 为实际位置，原 `exp_id` 与v3逻辑详情路径由显式别名兼容。中文名和类型在元数据中维护；人工分析写 `analysis`，不改写冻结历史JSON或指标，也不能据此晋升。具体登记方式见 `backtest/experiments/README.md`。
- 平台研究源与必要研究原件统一在 `/Users/yuxianqi/Project/qlib`；旧 `qlib_exp` 工作区已移除。历史元数据中的旧路径只保留作来源审计，活动路径以 `backtest/experiments/_catalog/audits/repository_migration_20261030/manifest.json` 与实际配置为准。
- macOS 下禁止用 heredoc/stdin 运行会触发 Qlib 并行取数的代码（详见 `docs/developer/qlib-shell-multiprocessing.md`）。
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
