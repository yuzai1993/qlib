# B6-M Baseline Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the frozen `rankic-es-lr010` five-seed winner to research baseline B6-M, add official H40 self-evaluation, freeze the selected model artifacts for Phase S, and integrate the completed model-research branch into `main`.

**Architecture:** Keep `model-hyperparam/valid-rankic-search-v1` as the immutable B5-relative experiment and append a new `baseline/b6-m` registry anchor. A dedicated fail-closed promotion script validates the fixed-one-day and self-label artifacts, writes a selected-only B6 freeze manifest that remains verifiable after loser cleanup, appends the baseline row atomically, and regenerates the report. Historical B0–B5 rows and their `baseline_ref` values remain unchanged.

**Tech Stack:** Python 3.12, pytest, Qlib, JSONL registry, SHA-256 provenance, Git linked worktrees.

## Global Constraints

- Fixed seeds are exactly `[42, 1000, 2000, 3000, 4000]`.
- B6-M formal metrics remain fixed-next-day test metrics on `2021-07-16..2026-07-16`; H40 self metrics are diagnostic only.
- B6-M uses CSI1000 training, Alpha158+range, H40 CSRankNorm, `RankICEarlyStoppingDEnsembleModel`, learning rate `0.1`, `epochs=200`, and `early_stopping_rounds=20`.
- Do not rewrite historical baseline rows or historical `baseline_ref` values.
- `live_trading` remains unchanged; Phase S freezes B6-M and initially retains B1-S.
- Use explicit Git pathspecs; do not stage unrelated artifacts or use force push.

---

### Task 1: Correct pre-merge registry semantics

**Files:**
- Modify: `backtest/scripts/register_b5_post2020_forward.py`
- Modify: `backtest/scripts/register_b5_rankic_hyperparams.py`
- Modify: `tests/backtest/test_register_b5_post2020_forward.py`
- Modify: `tests/backtest/test_register_b5_rankic_hyperparams.py`
- Modify: `backtest/experiments/registry.jsonl`
- Regenerate: `backtest/experiments/report.html`

**Interfaces:**
- Consumes: existing five-seed evaluator JSON with `seed_mean.rank_ic_mean_std`.
- Produces: truthful stale-control hypothesis text and registry summaries containing the five-seed RankIC sample standard deviation.

- [ ] **Step 1: Write failing behavior tests**

  Add assertions that the stale control hypothesis describes an unchanged `2020-01-10` cutoff and does not claim it was extended to `2022-12-30`. Add literal assertions that finalized registry summaries preserve `rank_ic_mean_std` from independently recomputed five-seed values.

- [ ] **Step 2: Verify RED**

  Run:

  ```bash
  /opt/anaconda3/envs/qlib/bin/python -m pytest \
    tests/backtest/test_register_b5_post2020_forward.py \
    tests/backtest/test_register_b5_rankic_hyperparams.py -q
  ```

  Expected: failures for the shared treatment hypothesis and missing `rank_ic_mean_std`.

- [ ] **Step 3: Implement the minimal correction**

  Derive separate control/treatment hypothesis strings and validate/copy the evaluator's sample standard deviation without changing the four formal selection metrics.

- [ ] **Step 4: Verify GREEN and repair the existing rows**

  Re-run the focused tests, update only the three affected existing registry rows, and regenerate HTML exclusively through `build_experiment_report.py`.

### Task 2: Add fail-closed B6 promotion

**Files:**
- Create: `backtest/scripts/promote_b6_baseline.py`
- Create: `tests/backtest/test_promote_b6_baseline.py`
- Create: `backtest/experiments/b6_model_freeze.json`
- Modify: `backtest/experiments/registry.jsonl`
- Regenerate: `backtest/experiments/report.html`

**Interfaces:**
- Consumes: `model-hyperparam/valid-rankic-search-v1`, `b5_rankic_hyperparam_selection.json`, `mh_valid_rankic_selected_test_1d.json`, `mh_valid_rankic_selected_test_self.json`, and five retained winner sessions.
- Produces: `promote_b6_baseline(...) -> dict`, selected-only freeze manifest, and exactly one `baseline/b6-m` row.

- [ ] **Step 1: Write failing promotion tests**

  Cover exact five seeds/three pools, fixed-one-day formal metrics, diagnostic self metrics, session/config/model hashes, no duplicate B6 row, preservation of all existing registry bytes, historical B5 reference preservation, atomic failure, and selected-only freeze verification after nonwinner sessions are absent.

- [ ] **Step 2: Verify RED**

  Run:

  ```bash
  /opt/anaconda3/envs/qlib/bin/python -m pytest \
    tests/backtest/test_promote_b6_baseline.py -q
  ```

  Expected: import failure because `promote_b6_baseline.py` does not yet exist.

- [ ] **Step 3: Implement the minimal promotion script**

  Validate and recompute all summaries, normalize paths to repository-relative form in the freeze manifest, record per-seed config/meta/MLflow-link/model hashes and three best iterations, append B6 atomically, and render the report.

- [ ] **Step 4: Verify GREEN**

  Run the focused promotion tests and then execute the real promotion once.

### Task 3: Complete the H40 self-evaluation

**Files:**
- Create: `backtest/experiments/ic/mh_valid_rankic_selected_test_self.json`

**Interfaces:**
- Consumes: the same five frozen winner sessions used by the formal one-day artifact.
- Produces: three-pool `eval_label_role=self` H40 metrics with 1,181 valid days per seed and pool.

- [x] **Step 1: Run the official evaluator**

  The evaluator was run with `--segment test`, all three pools, fixed five seeds, label `Ref($close, -41)/Ref($close, -1)-1`, and `--eval-label-role self`.

- [x] **Step 2: Validate structure and hash**

  Confirm exact seeds/pools, finite/recomputed means, official test bounds, data version `2026-07-31`, and SHA-256 `68598f64dcd8be9c344a26a33842660ba5f449ef52902f713aa9bfddf73e6d4a`.

### Task 4: Freeze Phase M and prepare the Phase S boundary

**Files:**
- Modify: `backtest/EXPERIMENT_STANDARD.md`
- Modify: `tests/backtest/test_cleanup_experiment_artifacts.py`
- Modify: `tests/backtest/test_build_experiment_report.py`

**Interfaces:**
- Consumes: the appended B6 anchor and selected-only freeze manifest.
- Produces: B6 as current Phase M baseline, B6 as frozen Phase S model, B1-S as initial strategy baseline, and explicit prerequisites for strategy-selection valid/test separation.

- [ ] **Step 1: Add failing B6 retention/report tests**

  Assert the latest B6 anchor is the sole retained Phase M group, B5 sessions are cleanup targets, B6 is first in the baseline table, and historical B5-referenced direction tables still inject B5.

- [ ] **Step 2: Verify RED, update standard, and verify GREEN**

  Update the standard to B6-M and Phase S transition language without changing historical experiment references. Re-run focused report/cleanup tests.

- [ ] **Step 3: Dry-run and apply cleanup**

  Require `baseline_exp_id=baseline/b6-m`, `candidate_exp_id=null`, exactly five winner sessions/MLflow experiments kept, no warnings/errors, then apply and repeat dry-run to prove zero pending deletions.

### Task 5: Review, commit, push, and fast-forward main

**Files:**
- All completed model-experiment source, configs, tests, registry/report, and audit JSON files selected with explicit pathspecs.

**Interfaces:**
- Consumes: a clean, reviewed `exp/workspace` tree and fresh remote refs.
- Produces: pushed `origin/exp/workspace` and fast-forwarded/pushed `origin/main`.

- [ ] **Step 1: Run full relevant verification and independent review**

  Run all `tests/backtest` plus repository boundary checks, `git diff --check`, JSON/YAML parsing, registry uniqueness, hash audits, report reproducibility, and cleanup dry-run.

- [ ] **Step 2: Commit with explicit pathspecs and inspect each staged diff**

  Keep experimental diagnostics separate from core protocol commits; never use `git add -A`.

- [ ] **Step 3: Fetch and push the experiment branch**

  Fetch `main` and `exp/workspace`, require no remote branch divergence, then push without force.

- [ ] **Step 4: Fast-forward in the main worktree and re-verify**

  In `/Users/yuxianqi/Project/qlib`, pull `main --ff-only`, merge `exp/workspace --ff-only`, rerun verification, then push `main` without force.
