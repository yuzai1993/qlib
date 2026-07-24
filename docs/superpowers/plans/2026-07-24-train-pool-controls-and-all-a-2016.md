# Train-Pool Controls and All-A 2016 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run controlled CSI300-2016 and all-A-2016 Phase M experiments, then register and report all outcomes.

**Architecture:** Add two independent config families that change only the training sample definition. Run Qlib training serially to preserve a valid memory test, evaluate successful five-seed model families through the repository's unified multi-pool evaluator, and treat registry JSONL as the sole report source.

**Tech Stack:** YAML, Qlib/pyqlib, LightGBM, `/opt/anaconda3/envs/qlib/bin/python`, macOS `/usr/bin/time -l`, JSONL, HTML report generator.

## Global Constraints

- Follow `backtest/EXPERIMENT_STANDARD.md` v1.0.
- Keep valid `2020-01-13~2021-07-15` and test `2021-07-16~2026-07-16` unchanged.
- Use fixed seeds `[42, 1000, 2000, 3000, 4000]`.
- Evaluate successful variants on `csi300`, `csi500`, and `csi1000` with `backtest/scripts/eval_ic_multi_pool.py`.
- Do not run Qlib training through heredoc or stdin on macOS.
- Do not run two training processes concurrently on the 16GB host.

---

### Task 1: Add CSI300 2016 control configs

**Files:**
- Create: `backtest/configs/train-data/csi300-start2016/td_csi300_start2016_lgbm_s42.yaml`
- Create the equivalent files for seeds `1000`, `2000`, `3000`, and `4000`.

**Interfaces:**
- Consumes: B0 config structure from `backtest/configs/baseline/b0-m/`.
- Produces: Five configs accepted by `backtest/scripts/config_loader.py`.

- [ ] **Step 1: Generate five configs**

Copy each corresponding B0 seed config and change exactly:

```yaml
# exp: train-data/csi300-start2016
run:
  note: td_csi300_start2016_lgbm_s<seed>
data:
  instruments: csi300
  handler:
    fit_start_time: '2016-01-02'
segments:
  train:
  - '2016-01-02'
  - '2020-01-10'
```

- [ ] **Step 2: Validate all configs**

Run a direct-file Python validation command using `/opt/anaconda3/envs/qlib/bin/python -c` to call `load_config` for all five files.

Expected: all configs load, seeds match filenames, and only allowed fields differ from B0.

### Task 2: Add all-A 2016 configs and collect pre-run evidence

**Files:**
- Create: `backtest/configs/train-data/all-start2016/td_all_start2016_lgbm_s42.yaml`
- Create the equivalent files for seeds `1000`, `2000`, `3000`, and `4000`.
- Create if required: `backtest/experiments/diagnostics/all_start2016_memory.md`

**Interfaces:**
- Consumes: Frozen Alpha158/LGBM experiment settings.
- Produces: A separate all-A experiment family without modifying the original failure.

- [ ] **Step 1: Generate five configs**

Generate seed-matched all-A configs with the frozen model settings and set handler start/fit start and train start to `2016-01-02`.

- [ ] **Step 2: Compare configs**

Expected: `instruments: all`, Alpha158, model parameters, valid, test, strategy, and fee settings remain identical to the original all-A configs.

### Task 3: Run CSI300 2016 five-seed training

**Files:**
- Create: five `backtest/result/<timestamp>_td_csi300_start2016_lgbm_s<seed>/` sessions.

**Interfaces:**
- Consumes: Task 1 configs.
- Produces: Five trained model artifacts and session names for Task 5.

- [ ] **Step 1: Run each seed serially**

Run:

```bash
MLFLOW_ALLOW_FILE_STORE=true /opt/anaconda3/envs/qlib/bin/python backtest/scripts/run_backtest.py --config train-data/csi300-start2016/td_csi300_start2016_lgbm_s<seed>.yaml
```

Expected: exit code 0 and `summary.json` reports `success_runs: 1`.

- [ ] **Step 2: Verify session artifacts**

Expected: each session contains a trained model link, metrics, and successful summary.

### Task 4: Test and run all-A 2016 serially

**Files:**
- Create: `backtest/result/<timestamp>_td_all_start2016_lgbm_s<seed>/`.
- Modify: `backtest/experiments/diagnostics/all_start2016_memory.md`.

**Interfaces:**
- Consumes: Task 2 configs.
- Produces: Either five valid model sessions or a measured OOM diagnosis.

- [ ] **Step 1: Run seed 42 with peak-memory measurement**

Run the seed-42 command through `/usr/bin/time -l`, with stdout/stderr captured by the execution harness.

Expected success case: exit code 0 and maximum resident set size recorded.

Expected failure case: non-zero/killed exit plus the last completed Qlib stage and memory evidence recorded.

- [ ] **Step 2: Branch on evidence**

If seed 42 succeeds, run seeds `1000`, `2000`, `3000`, and `4000` serially.

If it fails, stop full-seed execution and construct a file-backed diagnostic script that measures handler initialization and prepared train/valid shapes without changing model settings. Run it directly as a file, compare with CSI1000 2016, and update the diagnostic note.

- [ ] **Step 3: Verify all successful session artifacts**

Expected: every attempted successful seed has `success_runs: 1`; no two training processes overlap.

### Task 5: Run unified multi-pool evaluation

**Files:**
- Create three JSON files per successful five-seed family under `backtest/experiments/ic/`.

**Interfaces:**
- Consumes: Five successful sessions for each family.
- Produces: Standard IC/RankIC summaries for registry.

- [ ] **Step 1: Evaluate CSI300 2016**

Run `eval_ic_multi_pool.py` once per pool with the five session/seed pairs and outputs:

```text
td_csi300_start2016_lgbm_csi300.json
td_csi300_start2016_lgbm_csi500.json
td_csi300_start2016_lgbm_csi1000.json
```

- [ ] **Step 2: Evaluate all-A 2016 if five seeds succeeded**

Produce the corresponding three `td_all_start2016_lgbm_<pool>.json` files.

- [ ] **Step 3: Validate JSON**

Expected: each pool has five seeds, `n_days > 0`, finite four main metrics, and a five-seed mean.

### Task 6: Register experiments and rebuild report

**Files:**
- Modify: `backtest/experiments/registry.jsonl`
- Modify: `backtest/experiments/report.html`

**Interfaces:**
- Consumes: Task 4/5 outcomes.
- Produces: Durable experiment records and report.

- [ ] **Step 1: Append CSI300 2016 registry row**

Use the frozen hypothesis from the design, exact config/result paths, data version, three-pool metrics, and a conclusion that reports comparison without changing B0.

- [ ] **Step 2: Append all-A 2016 registry row**

On success, include five seeds and metrics. On failure, include attempted result/diagnostic paths, empty metrics, `conclusion: fail`, and the measured cause.

- [ ] **Step 3: Rebuild report**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_experiment_report.py
```

Expected: exit code 0 and both new exp ids appear in the generated HTML.

### Task 7: Final verification and interpretation

**Files:**
- Verify all files changed by Tasks 1–6.

**Interfaces:**
- Consumes: All experiment outputs.
- Produces: Evidence-backed user report.

- [ ] **Step 1: Validate configs and registry**

Run YAML parsing, JSONL parsing, `git diff --check`, and report presence checks.

- [ ] **Step 2: Compare results**

Compare CSI300-2016 against B0, CSI500, and CSI1000 on RankIC then RankICIR. State whether recency, pool breadth, or both are supported.

- [ ] **Step 3: Report OOM result**

State whether 2016 alone solved all-A OOM, peak memory if observed, and the smallest evidence-backed next option if not.
