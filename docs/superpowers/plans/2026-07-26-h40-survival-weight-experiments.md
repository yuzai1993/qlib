# H40 Survival Weight Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CSI1000 the experiment-primary test pool and execute three preregistered H40 survival-power label experiments without strategy backtests.

**Architecture:** Extend the pure label helper with a survival-power parameter, freeze a separate follow-up manifest derived only from the existing valid holding diagnostic, and reuse the generic label-config generator plus the unified IC evaluator. Reporting and cleanup share an explicit CSI1000 primary-pool constant while retaining three-pool eligibility.

**Tech Stack:** Python 3.12, pytest, Qlib, pandas, PyYAML, JSONL registry, static HTML report.

## Global Constraints

- Baseline remains `B1 v1.0`; do not update the baseline model.
- Phase M only; every config uses `run.mode=train_only`; do not run portfolio backtests.
- Train on CSI1000 with seeds `[42, 1000, 2000, 3000, 4000]`.
- Evaluate CSI1000, CSI300, and CSI500; CSI1000 is the primary target and appears first.
- Fixed valid/test dates remain unchanged and test must not be opened before candidate freeze.
- H40 uses a 41-trading-day purge.
- Formal comparison uses the fixed one-day label; self-label metrics are diagnostic.
- This is an adaptive follow-up motivated by prior test observations and must be labeled accordingly.

---

### Task 1: Migrate the Primary Test Pool to CSI1000

**Files:**
- Modify: `backtest/EXPERIMENT_STANDARD.md`
- Modify: `backtest/scripts/build_experiment_report.py`
- Modify: `backtest/scripts/cleanup_experiment_artifacts.py`
- Modify: `tests/backtest/test_build_experiment_report.py`
- Modify: `tests/backtest/test_cleanup_experiment_artifacts.py`

**Interfaces:**
- Produces: `PRIMARY_TEST_POOL = "csi1000"` in report and cleanup code.
- Preserves: three-pool eligibility based on `("csi300", "csi500", "csi1000")`.

- [ ] **Step 1: Write failing report and cleanup tests**

Add assertions that report columns begin with CSI1000 and that candidate selection ranks eligible rows by CSI1000 RankIC delta, then CSI1000 RankICIR delta, then mean three-pool RankIC delta.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_build_experiment_report.py \
  tests/backtest/test_cleanup_experiment_artifacts.py -q
```

Expected: failures showing the old CSI300-first column order and mean-delta cleanup ranking.

- [ ] **Step 3: Implement the primary-pool constants and ranking**

Set report pool order to `("csi1000", "csi300", "csi500")`. Keep eligibility across all three pools. Return cleanup score
`(csi1000_rank_delta, csi1000_rank_icir_delta, mean_rank_delta)`.
Update the standard's Phase M reading order, pairwise-pool requirement, Phase S default target, and cleanup tie-break rules to CSI1000 while leaving B1 unchanged.

- [ ] **Step 4: Run focused tests**

Run the command from Step 2. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backtest/EXPERIMENT_STANDARD.md \
  backtest/scripts/build_experiment_report.py \
  backtest/scripts/cleanup_experiment_artifacts.py \
  tests/backtest/test_build_experiment_report.py \
  tests/backtest/test_cleanup_experiment_artifacts.py
git commit -m "feat(backtest): prioritize CSI1000 experiments"
```

---

### Task 2: Add and Freeze the H40 Survival-Power Family

**Files:**
- Modify: `backtest/label_design/horizons.py`
- Create: `backtest/scripts/freeze_h40_survival_weight_manifest.py`
- Modify: `tests/backtest/test_label_horizons.py`
- Create: `tests/backtest/test_h40_survival_weight_manifest.py`

**Interfaces:**
- Produces: `survival_power_weighted_label(survival, *, max_horizon, power) -> tuple[str, dict[int, float]]`.
- Produces: `build_manifest(diagnostic, *, calendar, diagnostic_sha256, generated_at=None) -> dict`.
- Candidate variants: `survival-p05-h40`, `survival-p10-h40`, `survival-p20-h40`.

- [ ] **Step 1: Write failing pure-helper tests**

Test weights proportional to `S(a)^p`, full age coverage, positive finite power, normalization, deterministic expression order, and equivalence between `power=1` and `survival_weighted_label`.

- [ ] **Step 2: Write failing manifest tests**

Assert three exact variants/powers, H40, purge=41, CSI1000 primary pool, `test_metrics_opened=false`, adaptive-follow-up flag, fixed seeds, and common self-evaluation end.

- [ ] **Step 3: Run tests and verify failure**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_label_horizons.py \
  tests/backtest/test_h40_survival_weight_manifest.py -q
```

Expected: import/function failures before implementation.

- [ ] **Step 4: Implement the helper and manifest freezer**

Make `survival_weighted_label` delegate to the power helper with `power=1.0`.
Build all candidates from the existing valid holding diagnostic and use
`common_self_eval_end(..., max_horizon=40)`.

- [ ] **Step 5: Run focused tests**

Run the command from Step 3. Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backtest/label_design/horizons.py \
  backtest/scripts/freeze_h40_survival_weight_manifest.py \
  tests/backtest/test_label_horizons.py \
  tests/backtest/test_h40_survival_weight_manifest.py
git commit -m "feat(backtest): define H40 survival power labels"
```

---

### Task 3: Freeze the Matrix and Generate 15 Train-Only Configs

**Files:**
- Create: `backtest/experiments/h40_survival_weight_manifest.json`
- Create: `backtest/experiments/h40_survival_weight_manifest.sha256`
- Create: `backtest/configs/label-design/survival-p05-h40/*.yaml`
- Create: `backtest/configs/label-design/survival-p10-h40/*.yaml`
- Create: `backtest/configs/label-design/survival-p20-h40/*.yaml`

**Interfaces:**
- Consumes: existing `holding_duration_top30_drop1_valid.json`.
- Consumes: `generate_label_horizon_configs.py --manifest`.
- Produces: frozen manifest SHA256 recorded before test evaluation.

- [ ] **Step 1: Freeze the manifest**

Run the freezer with the existing diagnostic and B1 config. Confirm H40, purge=41, three powers, `test_metrics_opened=false`, and CSI1000 primary pool.

- [ ] **Step 2: Generate configs**

Use `generate_label_horizon_configs.py` with the new manifest and default B1 base config.

- [ ] **Step 3: Validate all configs**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_label_horizon_configs.py \
  tests/backtest/test_phase_m_train_only_configs.py -q
```

Also inspect the generated YAML matrix for 3 variants × 5 exact seeds.

- [ ] **Step 4: Record and verify the manifest hash**

Compute SHA256 and write the digest plus manifest filename to
`backtest/experiments/h40_survival_weight_manifest.sha256` before any test
evaluation. Recompute it immediately before opening test.

- [ ] **Step 5: Commit**

Commit the manifest and 15 configs with message:

```text
exp(backtest): freeze H40 survival matrix
```

---

### Task 4: Train Five Seeds per Candidate and Evaluate Valid

**Files:**
- Create: `backtest/result/<timestamp>_ld_survival_p*_h40_lgbm_s*/`
- Create: `backtest/experiments/ic/ld_survival_p*_h40_valid_1d.json`
- Create: `backtest/experiments/ic/ld_survival_p*_h40_valid_self.json`

**Interfaces:**
- Consumes: 15 frozen configs.
- Produces: three five-seed model groups and six valid metric files.

- [ ] **Step 1: Train all 15 configs**

Run `run_backtest.py --config ...` for every candidate/seed. Confirm each session contains exactly one successful train run and no backtest recorder.

- [ ] **Step 2: Evaluate valid using the fixed one-day label**

For every five-seed group call `eval_ic_multi_pool.py --segment valid` on all three pools.

- [ ] **Step 3: Evaluate valid using each self label**

Use `--eval-label-role self`, the frozen expression, and the common H40 valid cutoff.

- [ ] **Step 4: Select using valid only**

Order candidates by CSI1000 fixed-one-day RankIC, then CSI1000 RankICIR.
Record the full valid table without deleting any candidate.

- [ ] **Step 5: Revalidate the manifest hash**

Stop if it differs from Task 3.

---

### Task 5: Open Test Once, Register Results, Report, and Clean

**Files:**
- Create: `backtest/experiments/ic/ld_survival_p*_h40_test_1d.json`
- Create: `backtest/experiments/ic/ld_survival_p*_h40_test_self.json`
- Modify: `backtest/experiments/registry.jsonl`
- Regenerate: `backtest/experiments/report.html`

**Interfaces:**
- Produces: three registry rows with `metrics_by_eval_label`.
- Produces: CSI1000 pairwise seed comparison against B1.

- [ ] **Step 1: Evaluate all three frozen candidates on test**

For each group generate fixed-one-day and self-label metrics for CSI1000, CSI300, and CSI500.

- [ ] **Step 2: Register all outcomes**

Append one JSONL row per candidate with hypothesis, manifest hash, adaptive-follow-up flag, configs, sessions, metrics, CSI1000 pairwise wins/differences, and conclusion.

- [ ] **Step 3: Regenerate and inspect HTML**

Confirm B1 is first, CSI1000 columns appear first, and every candidate has `eval_1d` followed by `eval_self`.

- [ ] **Step 4: Run cleanup dry-run and apply**

Confirm three-pool eligibility, CSI1000-first candidate selection, ten retained sessions total, and no errors before `--apply`.

- [ ] **Step 5: Verify**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest -q
```

Validate registry JSONL, manifest hash, report contents, retained sessions, and `git diff --check`.

- [ ] **Step 6: Commit**

Commit tracked metrics, registry, and report with message:

```text
exp(backtest): record H40 survival weight results
```
