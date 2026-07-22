# Main Production Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reviewable branch from `main` that contains only the production data pipeline, reusable backtest framework, CSI300 live baseline, full live-trading schedule, diagnostics, and tests.

**Architecture:** Reconstruct the branch from committed snapshots instead of merging `dev`. The data and generic backtest slices come from `a4299b66`; the self-contained Live slice comes from the clean, verified head of `codex/remove-paper-live-parity`. Each slice is audited and committed independently, followed by an integrated cron and baseline-backtest verification.

**Tech Stack:** Python 3.12, Qlib, pandas, LightGBM, PyYAML, SQLite, pytest, Bash, Tushare Pro, QMT file bridge.

## Global Constraints

- The branch starts from `main` and must not include the 55-commit `dev` ancestry.
- Do not copy from the dirty `dev` worktree; use committed Git objects only.
- Keep CSI300 / `SH000300`, `Alpha158`, `LGBModel`, training dates 2006-01-02 through 2020-01-10, and `TopkDropoutStrategy(topk=10, n_drop=2)` as the production baseline.
- Do not include paper-trading runtime code, experimental strategies, parameter sweeps, CSI500/CSI1000 experiment configs, label experiments, ensembles, or uncommitted files.
- Tushare, QMT, notifier, and other credentials must only come from environment variables or ignored local files.
- Preserve production diagnostics, operation manuals, and tests for every included subsystem.
- Verification must not publish a LIVE batch, contact QMT, mutate the production SQLite database, or send notifications.

---

### Task 1: Production Data Collection, Backfill, and Daily Scheduling

**Files:**
- Modify: `.gitignore`
- Modify: `qlib/data/dataset/processor.py`
- Create/modify: `scripts/__init__.py`
- Create/modify: `scripts/data_collector/__init__.py`
- Modify: `scripts/data_collector/base.py`
- Modify: `scripts/data_collector/utils.py`
- Modify: `scripts/data_collector/cn_index/collector.py`
- Create: `scripts/data_collector/csindex_v2/`
- Create: `scripts/data_collector/tushare/README.md`
- Create: `scripts/data_collector/tushare/check_adjust_integrity.py`
- Create: `scripts/data_collector/tushare/check_index_coverage.py`
- Create: `scripts/data_collector/tushare/collector.py`
- Create: `scripts/data_collector/tushare/fill_missing_from_index.py`
- Create: `scripts/data_collector/tushare/requirements.txt`
- Create: `scripts/data_collector/tushare/run_update_to_bin.sh`
- Create: `scripts/data_collector/update_indices_daily.py`
- Modify: `scripts/dump_bin.py`
- Create: `tests/misc/test_csindex_v2.py`
- Create: `tests/misc/test_tushare_credentials.py`
- Create: `tests/misc/test_tushare_vwap.py`

**Interfaces:**
- Consumes: `TUSHARE_TOKEN` and `SERVERCHAN_SENDKEY` from the environment; local Qlib data at `~/.qlib/qlib_data/cn_data`.
- Produces: `python -m scripts.data_collector.update_indices_daily`, Tushare backfill/check CLIs, and `scripts/data_collector/tushare/run_update_to_bin.sh` for cron.

- [ ] **Step 1: Restore only the committed production data slice**

Run:

```bash
git restore --source=a4299b66 -- \
  .gitignore qlib/data/dataset/processor.py scripts/__init__.py \
  scripts/data_collector/__init__.py scripts/data_collector/base.py \
  scripts/data_collector/utils.py scripts/data_collector/cn_index/collector.py \
  scripts/data_collector/csindex_v2 scripts/data_collector/tushare \
  scripts/data_collector/update_indices_daily.py scripts/dump_bin.py \
  tests/misc/test_csindex_v2.py tests/misc/test_tushare_vwap.py
```

Expected: only the listed data, processor, and test paths appear in `git status --short`; there is no `jq_index`, `paper_trading`, or experiment file.

- [ ] **Step 2: Add a failing credential regression test**

Create `tests/misc/test_tushare_credentials.py` with:

```python
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_FILES = (
    ROOT / "scripts/data_collector/utils.py",
    ROOT / "scripts/data_collector/tushare/collector.py",
    ROOT / "scripts/data_collector/tushare/fill_missing_from_index.py",
    ROOT / "scripts/data_collector/csindex_v2/puller_tushare.py",
)


def test_production_collectors_never_assign_tushare_token():
    assignment = re.compile(r"os\.environ\[['\"]TUSHARE_TOKEN['\"]\]\s*=")
    offenders = [str(path.relative_to(ROOT)) for path in PRODUCTION_FILES if assignment.search(path.read_text())]
    assert offenders == []
```

- [ ] **Step 3: Run the credential test and confirm the restored snapshot fails**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_tushare_credentials.py -q`

Expected: FAIL listing `scripts/data_collector/utils.py` and `scripts/data_collector/tushare/collector.py`.

- [ ] **Step 4: Remove hard-coded token side effects**

Apply these exact behavioral changes:

```python
# scripts/data_collector/tushare/collector.py
# Delete the module-level os.environ["TUSHARE_TOKEN"] assignment.

# scripts/data_collector/utils.py:get_hs_stock_symbols._get_symbol
# Delete the os.environ["TUSHARE_TOKEN"] assignment and keep:
token = os.environ.get("TUSHARE_TOKEN", "")
if not token:
    raise ValueError("TUSHARE_TOKEN environment variable is not set")

# scripts/data_collector/csindex_v2/puller_tushare.py:_get_pro
import tushare as ts
token = os.environ.get("TUSHARE_TOKEN", "")
if not token:
    raise RuntimeError("TUSHARE_TOKEN 未配置")
return ts.pro_api(token)

# scripts/data_collector/tushare/fill_missing_from_index.py:get_pro
token = os.environ.get("TUSHARE_TOKEN", "")
if not token:
    raise RuntimeError("TUSHARE_TOKEN 未设置")
return ts.pro_api(token)
```

Also remove obsolete comments/imports that claim importing `collector.py` installs a token, and update the Tushare README to show `export TUSHARE_TOKEN=...` or `~/.qlib_live_env` as the only setup methods.

- [ ] **Step 5: Run data tests and shell syntax checks**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_tushare_credentials.py \
  tests/misc/test_tushare_vwap.py \
  tests/misc/test_csindex_v2.py -q
bash -n scripts/data_collector/tushare/run_update_to_bin.sh
/opt/anaconda3/envs/qlib/bin/python -m compileall -q \
  scripts/data_collector/tushare scripts/data_collector/csindex_v2 \
  scripts/data_collector/update_indices_daily.py
```

Expected: all pytest cases pass; `bash -n` and `compileall` exit 0.

- [ ] **Step 6: Commit the data slice**

```bash
git add .gitignore qlib/data/dataset/processor.py scripts tests/misc/test_csindex_v2.py tests/misc/test_tushare_credentials.py tests/misc/test_tushare_vwap.py
git commit -m "feat(data): add production collection and daily update pipeline"
```

### Task 2: YAML Backtest Framework and CSI300 Baseline

**Files:**
- Create: `backtest/configs/csi300_lgbm_train_start_2006.yaml`
- Create: `backtest/configs/csi300_lgbm_bt_only_2006_top10.yaml`
- Create: `backtest/configs/csi300_lgbm_bt_only_2006_top10_from2020.yaml`
- Create: `backtest/scripts/config_loader.py`
- Create: `backtest/scripts/report_utils.py`
- Create: `backtest/scripts/run_backtest.py`
- Modify: `qlib/contrib/evaluate.py`
- Modify: `qlib/contrib/online/operator.py`
- Modify: `qlib/contrib/online/user.py`
- Modify: `qlib/contrib/report/analysis_position/cumulative_return.py`
- Modify: `qlib/contrib/report/analysis_position/report.py`
- Modify: `qlib/contrib/report/analysis_position/risk_analysis.py`
- Modify: `qlib/workflow/record_temp.py`
- Create: `tests/misc/test_backtest_config_loader.py`

**Interfaces:**
- Consumes: Qlib data URI, YAML config, and optional prior session/MLflow artifacts.
- Produces: `load_config(path) -> dict`, Qlib task and portfolio-analysis configs, training/backtest CLI, HTML/PNG/JSON report artifacts.

- [ ] **Step 1: Restore the framework and only the production baseline configs**

Run:

```bash
git restore --source=a4299b66 -- \
  backtest/scripts/config_loader.py backtest/scripts/report_utils.py \
  backtest/scripts/run_backtest.py \
  backtest/configs/csi300_lgbm_train_start_2006.yaml \
  backtest/configs/csi300_lgbm_bt_only_2006_top10.yaml \
  backtest/configs/csi300_lgbm_bt_only_2006_top10_from2020.yaml \
  qlib/contrib/evaluate.py qlib/contrib/online/operator.py \
  qlib/contrib/online/user.py \
  qlib/contrib/report/analysis_position/cumulative_return.py \
  qlib/contrib/report/analysis_position/report.py \
  qlib/contrib/report/analysis_position/risk_analysis.py \
  qlib/workflow/record_temp.py tests/misc/test_backtest_config_loader.py
```

Expected: no sweep script, timing config, dynamic strategy, CSI500 config, or CSI1000 config is created.

- [ ] **Step 2: Run framework configuration tests**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_backtest_config_loader.py -q
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py --help
```

Expected: all tests pass and the CLI exits 0 after printing `--config` help.

- [ ] **Step 3: Add a baseline-scope regression test**

Append to `tests/misc/test_backtest_config_loader.py`:

```python
def test_production_baseline_identity():
    cfg = load_config("csi300_lgbm_train_start_2006.yaml")
    assert cfg["data"]["instruments"] == "csi300"
    assert cfg["data"]["benchmark"] == "SH000300"
    assert cfg["data"]["handler"]["class"] == "Alpha158"
    assert cfg["segments"]["train"] == ["2006-01-02", "2020-01-10"]
    assert cfg["strategy"]["class"] == "TopkDropoutStrategy"
    assert cfg["strategy"]["topk"] == 10
    assert cfg["strategy"]["n_drop"] == 2
```

- [ ] **Step 4: Run the baseline-scope test**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/misc/test_backtest_config_loader.py -q`

Expected: all configuration tests pass.

- [ ] **Step 5: Commit the backtest slice**

```bash
git add backtest qlib/contrib/evaluate.py qlib/contrib/online \
  qlib/contrib/report/analysis_position qlib/workflow/record_temp.py \
  tests/misc/test_backtest_config_loader.py
git commit -m "feat(backtest): add yaml framework and csi300 baseline"
```

### Task 3: Self-Contained Live Trading and Backtest-Parity Gate

**Files:**
- Create: `live_trading/`
- Create: `tests/live_trading/`
- Modify: `qlib/contrib/strategy/signal_strategy.py`
- Create: `qlib/contrib/strategy/topk_dropout.py`
- Create: `tests/backtest/test_topk_dropout_selection.py`
- Create: `backtest/configs/csi300_live_parity.yaml`
- Create: `docs/qmt_qlib_live_guide.md`

**Interfaces:**
- Consumes: the complete Live YAML, local model artifact, local Qlib data, environment credentials, bridge directory, and SQLite state.
- Produces: deterministic TopkDropout selection, parity validation, signal publication, fill import, monitoring/reporting, Web API, and cron entrypoints without importing `paper_trading`.

- [ ] **Step 1: Verify the prerequisite branch is clean and capture its source commit**

Run:

```bash
test -z "$(git -C /private/tmp/qlib-live-parity status --porcelain)"
git rev-parse codex/remove-paper-live-parity
```

Expected: the first command exits 0 and the second prints the verified source commit.

- [ ] **Step 2: Restore the verified Live snapshot**

Run:

```bash
git restore --source=codex/remove-paper-live-parity -- \
  live_trading tests/live_trading \
  qlib/contrib/strategy/signal_strategy.py \
  qlib/contrib/strategy/topk_dropout.py \
  tests/backtest/test_topk_dropout_selection.py \
  backtest/configs/csi300_live_parity.yaml \
  docs/qmt_qlib_live_guide.md
```

Expected: all Live production modules, QMT scripts, monitoring UI, cron wrappers, diagnostics, and tests are present; `paper_trading/` is absent.

- [ ] **Step 3: Prove repository boundaries and parity contracts**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/live_trading/test_repository_boundaries.py \
  tests/live_trading/test_backtest_parity.py \
  tests/backtest/test_topk_dropout_selection.py -q
rg -n "paper_trading" live_trading tests/live_trading backtest/configs/csi300_live_parity.yaml
```

Expected: all tests pass and `rg` exits 1 with no matches.

- [ ] **Step 4: Run the full Live suite and cron entrypoint checks**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading tests/backtest/test_topk_dropout_selection.py -q
for script in live_trading/run_*_cron.sh; do bash -n "$script"; done
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/check_backtest_parity.py \
  --live-config live_trading/configs/csi300_topk10_live.yaml
/opt/anaconda3/envs/qlib/bin/python -m compileall -q live_trading
```

Expected: the Live and Topk tests pass, every shell script parses, parity reports success, and compileall exits 0.

- [ ] **Step 5: Commit the Live slice**

```bash
git add live_trading tests/live_trading tests/backtest/test_topk_dropout_selection.py \
  qlib/contrib/strategy/signal_strategy.py qlib/contrib/strategy/topk_dropout.py \
  backtest/configs/csi300_live_parity.yaml docs/qmt_qlib_live_guide.md
git commit -m "feat(live): add self-contained production trading pipeline"
```

### Task 4: Integrated Audit, Cron Verification, and Baseline Smoke Test

**Files:**
- Modify: `docs/superpowers/specs/2026-07-23-main-production-baseline-design.md` only if verification uncovers a factual mismatch.
- Modify: included production files only when a failing verification demonstrates a defect.

**Interfaces:**
- Consumes: all prior tasks and local ignored data/artifacts.
- Produces: a clean branch whose diff from `main` is production-only and whose test evidence covers the daily schedule and baseline backtest.

- [ ] **Step 1: Audit the final file set for excluded content and credentials**

Run:

```bash
git diff --name-only main...HEAD
test ! -e paper_trading
test ! -e qlib/contrib/strategy/dynamic_position.py
test -z "$(find backtest/scripts -maxdepth 1 -type f \( -name '*sweep*' -o -name '*ensemble*' -o -name '*multiseed*' -o -name '*pred_backtest*' \) -print)"
test -z "$(find backtest/configs -maxdepth 1 -type f \( -name 'csi500*' -o -name 'csi1000*' -o -name '*timing*' -o -name '*ensemble*' -o -name '*multiseed*' \) -print)"
! rg -n "os\.environ\[['\"]TUSHARE_TOKEN['\"]\]\s*=|paper_trading" \
  scripts/data_collector live_trading tests/live_trading
git diff --check main...HEAD
```

Expected: every assertion exits 0, `rg` finds nothing, and `git diff --check` is clean.

- [ ] **Step 2: Verify every current cron command up to its external boundary**

Run:

```bash
bash -n scripts/data_collector/tushare/run_update_to_bin.sh
for script in live_trading/run_import_cron.sh live_trading/run_monitor_cron.sh \
  live_trading/run_publish_cron.sh live_trading/run_publish_catchup_cron.sh; do
  bash -n "$script"
done
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_import_fills.py --help
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_monitor.py --help
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/run_publish_signals.py --help
/opt/anaconda3/envs/qlib/bin/python live_trading/scripts/check_backtest_parity.py \
  --live-config live_trading/configs/csi300_topk10_live.yaml
```

Expected: every command exits 0 without accessing the production DB, bridge, notifier, or broker.

- [ ] **Step 3: Link existing ignored artifacts into the isolated worktree**

Run:

```bash
ln -sfn /Users/yuxianqi/Project/qlib/mlruns mlruns
mkdir -p backtest/result
ln -sfn /Users/yuxianqi/Project/qlib/backtest/result/20260711_223223_train_start_2006 \
  backtest/result/20260711_223223_train_start_2006
```

Expected: the baseline model and prior session resolve without copying or tracking local artifacts.

- [ ] **Step 4: Run the actual baseline backtest smoke test**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py \
  --config csi300_live_parity.yaml
```

Expected: Qlib loads recorder `40a17c74aa7b4d97a4caa35015aaead5`, completes the CSI300 TopkDropout backtest, and writes a new ignored result session with no exception.

- [ ] **Step 5: Run the consolidated regression suite**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/misc/test_tushare_credentials.py \
  tests/misc/test_tushare_vwap.py \
  tests/misc/test_csindex_v2.py \
  tests/misc/test_backtest_config_loader.py \
  tests/live_trading \
  tests/backtest/test_topk_dropout_selection.py -q
```

Expected: all selected production tests pass with zero failures.

- [ ] **Step 6: Commit any evidence-driven fixes and confirm cleanliness**

If verification required a code or documentation fix:

```bash
git add -u
git commit -m "fix: complete production baseline verification"
```

Final verification fixes are restricted to already tracked production files; do not create a new source or test path in this task.

Then run:

```bash
git status --short --branch
git log --oneline main..HEAD
```

Expected: no tracked or untracked production source changes remain; ignored local symlinks/artifacts do not appear; the branch contains the design, plan, data, backtest, Live, and optional verification-fix commits only.
