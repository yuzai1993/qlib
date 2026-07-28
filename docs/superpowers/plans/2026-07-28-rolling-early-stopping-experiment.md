# Annual Rolling Early-Stopping Experiment Implementation Plan

> **Execution note:** The user authorized uninterrupted execution after
> approving `early_stopping_rounds=5`.

**Goal:** Run a controlled five-seed annual expanding rolling experiment that
changes only `early_stopping_rounds`, records whether early stopping actually
triggers, and determines whether it improves the existing rolling control.

**Architecture:** Reuse the rolling trainer and canonical three-pool evaluator.
Extend the evaluator with a small model-diagnostic extractor so each loaded
DoubleEnsemble fold model reports all booster `best_iteration` values. Extend
the existing B5 follow-up registry helper with an ES5 specification and direct
comparison against the no-ES rolling control.

**Tech stack:** Python, Qlib, LightGBM, DoubleEnsemble, pandas, YAML/JSONL,
pytest.

---

### Task 1: Add observable best-iteration diagnostics

**Files:**

- Modify: `backtest/scripts/eval_ic_multi_pool.py`
- Modify: `tests/backtest/test_eval_ic_multi_pool.py`

1. Write a failing unit test for extracting three finite positive
   `best_iteration` values from a DoubleEnsemble-like object.
2. Verify the test fails because the helper is absent.
3. Implement the minimal extractor with explicit validation.
4. Write a failing test that diagnostics accumulated across pools are stored
   once per seed/fold, not duplicated per pool.
5. Add diagnostics to `evaluate_rolling` and verify focused tests pass.

### Task 2: Generate and validate the ES5 treatment

**Files:**

- Create:
  `backtest/configs/train-schedule/expanding-annual-es5/ts_expanding_annual_es5_s{seed}.yaml`
- Modify: `backtest/scripts/register_b5_followups.py`
- Modify: `tests/backtest/test_register_b5_followups.py`

1. Add failing tests for the pending ES5 row and direct-control comparison.
2. Implement an ES5 experiment specification with `baseline_ref="B5 v1.0"`
   and `control_ref="train-schedule/expanding-annual"`.
3. Generate five configs by copying the rolling control and adding only
   `early_stopping_rounds: 5`, plus experiment identifiers and notes.
4. Validate all five configs differ from their control only in approved
   metadata and the treatment parameter.
5. Pre-register the pending row before any training starts.

### Task 3: Train and evaluate

1. Run `run_rolling_retrain.py` for all five seeds using the required Qlib
   interpreter and file-store environment.
2. Require five complete parent sessions and 25 successful folds.
3. Run canonical `eval_ic_multi_pool.py --rolling` for CSI1000, CSI300, and
   CSI500.
4. Verify full official test-date coverage and complete best-iteration
   diagnostics.

### Task 4: Finalize, report, and clean up

1. Finalize the registry row with B5 metrics, direct-control deltas, seed
   pairwise wins, yearly/fold diagnostics, and early-stopping trigger rate.
2. Regenerate the HTML report and verify B5 is the first row in the
   `train-schedule` table.
3. Apply the experiment-standard cleanup policy without touching unrelated
   user artifacts or live B1.
4. Run focused and full relevant verification commands.
5. Report whether ES5 triggered, whether it improved the no-ES rolling
   control, and the exact scope of any “can remove early stopping” conclusion.
