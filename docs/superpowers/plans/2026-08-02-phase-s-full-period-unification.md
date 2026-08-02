# Phase S Full-Period Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Phase S selection to one continuous CSI1000 2020-01-13 through 2026-07-31 comparison, rerun the 540-point B2-S neighborhood, and publish every Phase S experiment in `strategy_stability_report.html`.

**Architecture:** Keep Phase M contracts untouched. Add an explicit Phase S `full` evaluation contract and a versioned full-period neighborhood runner/finalizer so the immutable valid/test v1 audit remains intact. Extend the existing stability renderer into the only Phase S HTML entry point, sourcing all sections from registry rows and tracked JSON artifacts.

**Tech Stack:** Python 3.12, Qlib frozen-prediction backtests, pandas/numpy, YAML, JSON/JSONL, pytest, BeautifulSoup, static HTML.

## Global Constraints

- Phase M train/valid/test rules and five-seed model evaluation remain unchanged.
- Phase S uses only the tracked B6-M seed-4000 artifact; no model retraining or multi-seed ensemble.
- Phase S research selection period is exactly `2020-01-13` through `2026-07-31` on CSI1000.
- Every Phase S selection result is labeled `evaluation_mode: full_history_in_sample`; it is not an out-of-sample estimate.
- B2-S remains the research strategy baseline and B1/B1-S remains live until the user explicitly promotes a replacement.
- The v1 valid/test neighborhood registry row remains immutable; the full-period rerun uses `strategy-neighborhood/b2-s-local-full-v2`.
- The only active Phase S HTML report is `backtest/experiments/strategy_stability_report.html`.
- Account is CNY 500,000 with the current Phase S fee and benchmark contract.

---

### Task 1: Update the Phase S time and reporting standard

**Files:**
- Modify: `backtest/EXPERIMENT_STANDARD.md`
- Modify: `backtest/scripts/phase_s_protocol.py`
- Modify: `backtest/scripts/run_strategy_sweep.py`
- Test: `tests/backtest/test_phase_s_protocol.py`
- Test: `tests/backtest/test_run_strategy_sweep.py`

**Interfaces:**
- Produces: `FULL_SEGMENT == ("2020-01-13", "2026-07-31")` as the legal Phase S selection interval.
- Produces: `build_sweep_config(..., segment="full")` with CSI1000 full-period backtest bounds.

- [ ] **Step 1: Write failing tests for the new Phase S contract**

```python
def test_phase_s_full_segment_is_the_selection_contract():
    assert protocol.FULL_SEGMENT == ("2020-01-13", "2026-07-31")

def test_sweep_config_supports_full_period_selection():
    config = sweep.build_sweep_config(base, candidate, pool="csi1000", segment="full")
    assert config["segments"]["test"] == ["2020-01-13", "2026-07-31"]
    assert config["phase_s"]["selection_segment"] == "full"
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_phase_s_protocol.py \
  tests/backtest/test_run_strategy_sweep.py -q
```

Expected: failure because `full` is not accepted by `_segment_bounds`.

- [ ] **Step 3: Implement the full-period Phase S contract**

Update `_segment_bounds` so `full` returns `FULL_SEGMENT`. Keep `valid` and `test` readable only for historical audit/reproduction. Update the experiment standard to v2.3 and state:

```text
Phase S：CSI1000 2020-01-13 ~ 2026-07-31 全历史连续区间允许用于策略比较与选型；该结果属于 full_history_in_sample，不得表述为样本外检验。
```

Replace Phase S checklist language about freezing a valid winner and opening test with full-period preregistration, full-period comparison, unified report registration, and explicit non-OOS disclosure.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run the command from Step 2. Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add backtest/EXPERIMENT_STANDARD.md backtest/scripts/phase_s_protocol.py \
  backtest/scripts/run_strategy_sweep.py tests/backtest/test_phase_s_protocol.py \
  tests/backtest/test_run_strategy_sweep.py
git commit -m "feat(backtest): adopt full-period Phase S selection"
```

---

### Task 2: Add a restartable full-period neighborhood runner

**Files:**
- Create: `backtest/scripts/run_strategy_neighborhood_full.py`
- Create: `tests/backtest/test_run_strategy_neighborhood_full.py`
- Reuse: `backtest/scripts/strategy_neighborhood_protocol.py`
- Reuse: `backtest/scripts/run_strategy_neighborhood.py`

**Interfaces:**
- Consumes: one `b6-m/csi1000/full` entry from the stability prediction manifest.
- Produces: `protocol.json` and `full_results.json` under `backtest/experiments/strategy-neighborhood/20260802_b2s_local_full/`.
- Produces: one exact robust winner computed by `score_valid_candidates(rows, grid)` after 540 successful rows.

- [ ] **Step 1: Write failing runner tests**

Cover:

```python
def test_protocol_is_versioned_full_history_in_sample():
    payload = runner.protocol_payload(grid, base_config)
    assert payload["exp_id"] == "strategy-neighborhood/b2-s-local-full-v2"
    assert payload["evaluation_mode"] == "full_history_in_sample"
    assert payload["selection_segment"] == ["2020-01-13", "2026-07-31"]
    assert "test_policy" not in payload

def test_manifest_requires_exact_b6_csi1000_full_prediction():
    entry = runner.full_prediction_entry(manifest)
    assert (entry["model_ref"], entry["pool"], entry["segment"]) == ("b6-m", "csi1000", "full")

def test_checkpoint_reuse_requires_prediction_and_effective_config_sha():
    assert runner.pending_candidates(grid, checkpoint, base=base, prediction_sha256="new") == grid
```

- [ ] **Step 2: Run the new tests and confirm RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_run_strategy_neighborhood_full.py -q
```

Expected: import failure because the module does not exist.

- [ ] **Step 3: Implement the runner**

The runner must:

1. Validate exact prediction coverage and SHA.
2. Freeze the 540-point grid and robust ranking before the first backtest.
3. Render configs with `segment="full"` and names ending `_csi1000_full.yaml`.
4. Run in bounded batches with `ThreadPoolExecutor(max_workers=3)` by default.
5. Atomically checkpoint each completed future on the main thread.
6. Store repository-relative paths, protocol/manifest/base config hashes, source prediction SHA, and effective config SHA.
7. Compute after-cost excess metrics, yearly after-cost excess IR, and absolute portfolio Sharpe/Calmar/volatility using `strategy_stability_metrics.summarize_period`.
8. Stop after freezing the full-period winner; do not open a separate test phase.

- [ ] **Step 4: Run runner tests and existing neighborhood tests**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_run_strategy_neighborhood_full.py \
  tests/backtest/test_run_strategy_neighborhood.py \
  tests/backtest/test_strategy_neighborhood_protocol.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add backtest/scripts/run_strategy_neighborhood_full.py \
  tests/backtest/test_run_strategy_neighborhood_full.py
git commit -m "feat(backtest): add full-period neighborhood runner"
```

---

### Task 3: Add immutable full-period registration and unified Phase S reporting

**Files:**
- Create: `backtest/scripts/finalize_strategy_neighborhood_full.py`
- Create: `tests/backtest/test_finalize_strategy_neighborhood_full.py`
- Modify: `backtest/scripts/build_strategy_stability_report.py`
- Modify: `tests/backtest/test_build_strategy_stability_report.py`
- Modify: `backtest/scripts/build_experiment_report.py`
- Modify: `tests/backtest/test_build_experiment_report.py`

**Interfaces:**
- Consumes: full-period protocol, manifest, and 540-row result checkpoint.
- Produces: immutable registry row `strategy-neighborhood/b2-s-local-full-v2`.
- Produces: unified `strategy_stability_report.html` containing every Phase S registry row.

- [ ] **Step 1: Write failing finalizer and report tests**

Required assertions:

```python
assert row["evaluation_mode"] == "full_history_in_sample"
assert row["selection_segment"] == ["2020-01-13", "2026-07-31"]
assert row["cleanup_retention_eligible"] is False
assert report_first_table_contains("B2-S v1.0")
assert unified_report_contains("strategy-neighborhood/b2-s-local-full-v2")
assert unified_report_contains_all_phase_s_exp_ids(registry_rows)
assert "样本外" not in winner_claim_text
assert not output_targets_include("strategy_neighborhood_report.html")
```

Also test exact 540 unique IDs, independent winner recomputation, protocol/manifest/base/prediction/effective-config SHA checks, and completed-row rewrite rejection.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_finalize_strategy_neighborhood_full.py \
  tests/backtest/test_build_strategy_stability_report.py \
  tests/backtest/test_build_experiment_report.py -q
```

- [ ] **Step 3: Implement finalization and unified rendering**

The finalizer independently calls `score_valid_candidates`, verifies the exact candidate set and every artifact identity, transitions only `preregistered -> complete`, and records:

```json
{
  "exp_id": "strategy-neighborhood/b2-s-local-full-v2",
  "direction": "strategy-neighborhood-b2-s-full",
  "phase": "S",
  "evaluation_mode": "full_history_in_sample",
  "selection_pool": "csi1000",
  "selection_segment": ["2020-01-13", "2026-07-31"],
  "cleanup_retention_eligible": false
}
```

The unified renderer must put B2-S first, retain the current B6-M stability tables, add the full-period neighborhood winner and robust Top 50 table, and append an audit index covering every other Phase S registry row. The canonical experiment report links Phase S readers to `strategy_stability_report.html` instead of rendering competing Phase S selection tables.

- [ ] **Step 4: Run focused report/finalizer tests and confirm GREEN**

Run the Step 2 command. Expected: all pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add backtest/scripts/finalize_strategy_neighborhood_full.py \
  backtest/scripts/build_strategy_stability_report.py \
  backtest/scripts/build_experiment_report.py \
  tests/backtest/test_finalize_strategy_neighborhood_full.py \
  tests/backtest/test_build_strategy_stability_report.py \
  tests/backtest/test_build_experiment_report.py
git commit -m "feat(backtest): unify Phase S stability reporting"
```

---

### Task 4: Execute the 540-point full-period experiment

**Files created during execution:**
- `backtest/configs/strategy-neighborhood/b2-s-local-full/*.yaml`
- `backtest/experiments/strategy-neighborhood/20260802_b2s_local_full/protocol.json`
- `backtest/experiments/strategy-neighborhood/20260802_b2s_local_full/full_results.json`
- Modified: `backtest/experiments/registry.jsonl`
- Modified: `backtest/experiments/strategy_stability_report.html`
- Modified: `backtest/experiments/report.html`

**Interfaces:**
- Consumes: `backtest/experiments/strategy-stability/20260801_full_period/prediction_manifest.json` and the tracked B6-M full-period prediction artifact.
- Produces: a complete, auditable 540-row full-period result and one non-promoted winner.

- [ ] **Step 1: Verify and freeze input artifacts**

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_strategy_neighborhood_full.py \
  --prediction-manifest backtest/experiments/strategy-stability/20260801_full_period/prediction_manifest.json \
  --prepare-only
```

Verify exact coverage, model SHA, prediction SHA, protocol SHA, and 540 candidates.

- [ ] **Step 2: Preregister the new immutable experiment row**

```bash
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/finalize_strategy_neighborhood_full.py preregister
```

- [ ] **Step 3: Run or resume all 540 candidates**

```bash
MLFLOW_ALLOW_FILE_STORE=true MPLCONFIGDIR=/private/tmp/qlib_mpl_cache \
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_strategy_neighborhood_full.py \
  --prediction-manifest backtest/experiments/strategy-stability/20260801_full_period/prediction_manifest.json \
  --workers 3 --max-runtime-hours 5
```

Require 540 successful rows and a finite robust winner score. Failed or invalid rows must be rerun or explicitly retained as failure; an incomplete neighborhood cannot be selected.

- [ ] **Step 4: Finalize registry and both report entry points**

```bash
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/finalize_strategy_neighborhood_full.py finalize
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/build_strategy_stability_report.py
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/build_experiment_report.py
```

- [ ] **Step 5: Clean generated runtime artifacts**

Run the standard cleaner in dry-run mode. If it refuses because an isolated worktree cannot see retained sessions, remove only the explicitly resolved direct children created by this experiment under `backtest/result/` and `mlruns/`. Preserve protocol, JSON summaries, configs, registry, and HTML.

- [ ] **Step 6: Commit execution artifacts**

```bash
git add backtest/configs/strategy-neighborhood/b2-s-local-full \
  backtest/experiments/strategy-neighborhood/20260802_b2s_local_full \
  backtest/experiments/registry.jsonl \
  backtest/experiments/strategy_stability_report.html \
  backtest/experiments/report.html
git commit -m "feat(backtest): record full-period B2-S neighborhood"
```

---

### Task 5: Verify, review, integrate, and push

**Files:**
- Verify all files changed by Tasks 1-4.

**Interfaces:**
- Produces: a clean, pushed `exp/workspace` with the full-period Phase S standard and report.

- [ ] **Step 1: Run focused tests**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_phase_s_protocol.py \
  tests/backtest/test_run_strategy_sweep.py \
  tests/backtest/test_run_strategy_neighborhood_full.py \
  tests/backtest/test_finalize_strategy_neighborhood_full.py \
  tests/backtest/test_build_strategy_stability_report.py \
  tests/backtest/test_build_experiment_report.py -q
```

- [ ] **Step 2: Run the full backtest suite**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest -q
```

- [ ] **Step 3: Verify artifacts and repository hygiene**

Check exact 540 unique successful rows, recomputed winner equality, registry SHA matches, unified report contains every Phase S exp ID, no active report links to `strategy_neighborhood_report.html`, no temporary absolute paths, no retained generated `mlruns`/`backtest/result` sessions, and `git diff --check` passes.

- [ ] **Step 4: Request independent code review**

Ask the reviewer to inspect test leakage disclosure, full-period contract, checkpoint reuse, registry immutability, report completeness, artifact hashes, and cleanup scope. Fix all Critical and Important findings, then rerun Steps 1-3.

- [ ] **Step 5: Merge and push**

Push the implementation branch, merge it into `exp/workspace`, rerun the focused tests in the merged tree, and push `exp/workspace`.

