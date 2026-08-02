# Phase S Full-Period Unification — Final Review Fix Report

Date: 2026-08-02
Branch/worktree: `exp/workspace` at `/Users/yuxianqi/Project/qlib_exp`
Review base: `e37f3640fc2f61e6a3bf67761b87edf5c84e8679`

## Scope

This single fix wave resolves all three Important findings from the completed Phase S review:

1. A completed neighborhood-runner resume could rewrite immutable `full_results.json` solely because `updated_at` was regenerated.
2. General `run_strategy_sweep.py` full mode did not read and independently validate the prediction DataFrame's canonical coverage/index/data version.
3. `--resume-summary` reuse was bound only to `model_ref`, so rows from other pools, segments, predictions, or configs could enter a current full comparison.

The deferred v2 naming mismatch was deliberately left unchanged. No completed experiment result, registry row, correction row, generated config, or HTML report was modified.

## Root Causes

### 1. Immutable completed result rewrite

`run_strategy_neighborhood_full.main()` treated `full_complete` like a running checkpoint. With no pending candidates it still rebuilt `completed_results_payload()`, generated a fresh `updated_at`, and atomically overwrote `full_results.json`. The completed registry and correction rows both bind the prior byte SHA, so the harmless-looking rerun invalidated an immutable audit chain.

### 2. General full sweep accepted self-consistent truncation

`run_strategy_sweep.verify_prediction_contract()` checked matching manifest tuple, path, file SHA, and frozen model. It did not deserialize the full prediction or verify exact canonical dates, row/date counts, index SHA, or `data_version`. A truncated artifact and correspondingly edited manifest could therefore be self-consistent and still pass as `full`.

### 3. Resume rows lacked run/config identity

General sweep resume validation checked only top-level `model_ref`. Existing successful rows were retained without verifying pool, segment, evaluation mode, prediction, base config, candidate definition, effective config, or requested candidate set. An all-success resume also converted an empty retry list back into the full grid and reran everything.

## TDD Evidence

All production changes followed a failing behavioral test first.

### Fix 1 red → green

- Added `test_completed_resume_is_a_byte_preserving_noop`.
- RED: `full_results.json` bytes differed only because `updated_at` changed from the frozen value; the test reported the differing artifact bytes.
- GREEN: a valid `full_complete` checkpoint is independently recomputed and validated in memory, then returns with `status: already full_complete; no files rewritten` before any candidate/config/artifact write.
- Added reviewer follow-up `test_completed_resume_with_missing_protocol_fails_before_any_write`.
- RED: the runner recreated missing `protocol.json` and returned successfully.
- GREEN: a completed checkpoint with missing protocol fails read-only before creating the protocol. Explicit `--prepare-only` retains its preparation semantics.

### Fix 2 red → green

- Added actual-pickle validation plus parameterized self-consistent tampering tests for truncation, index mutation, and data-version mutation.
- RED: all three tampered manifests/artifacts were accepted (`DID NOT RAISE`).
- GREEN: new shared `phase_s_prediction_validation.py` verifies:
  - exact `b6-m / csi1000 / full` identity;
  - authoritative tracked manifest path and schema;
  - frozen B6-M seed-4000 model path/SHA;
  - manifest and entry `data_version == 2026-07-31`;
  - one-column, unique `datetime/instrument` MultiIndex DataFrame;
  - exact `2020-01-13..2026-07-31` endpoints;
  - canonical 1,587 dates, 1,584,284 rows, and index SHA `e6336cd92cc988f71f61afe2907980451ad20201388b6b39a542e469c1313abd`.
- Both the dedicated full runner and general sweep now use the shared validator without circular imports. Historical valid/test audit paths retain their prior path/SHA validation behavior.

### Fix 3 red → green

- Added parameterized behavioral resume tests for model, pool, segment, evaluation mode, prediction SHA, base-config SHA, and candidate effective-config SHA mismatch.
- RED: every mismatch except the already-covered model mismatch was accepted; the output also lacked the new run identity.
- GREEN: every run-level or row-level mismatch fails before output/config directory creation or subprocess launch.
- Added valid partial-retry reuse test: a matching successful baseline row is retained while only the failed candidate is run.
- Added all-success reuse test.
- RED: an empty retry list fell back to the full grid, reran both candidates, and destroyed the reusable baseline result.
- GREEN: all matching successful rows are reused with zero subprocess calls and zero generated candidate configs.
- Added reviewer follow-up candidate-set completeness test.
- RED: a summary omitting a requested candidate silently produced a partial winner.
- GREEN: `requested_candidate_ids` is part of the run identity and must exactly match the ordered row candidate set. Explicit historical partial audits must resume with the same explicit candidate set.

## Implementation Summary

- `backtest/scripts/phase_s_prediction_validation.py`
  - New dependency-neutral authoritative full-prediction validator shared by both Phase S full entry points.
- `backtest/scripts/run_strategy_neighborhood_full.py`
  - Reuses shared prediction validation.
  - Validates completed checkpoint content and all artifact identities in memory.
  - Returns a clear byte-preserving no-op for valid completion.
  - Fails read-only when a completed checkpoint's protocol is missing.
- `backtest/scripts/run_strategy_sweep.py`
  - General full mode invokes the shared actual-DataFrame validator.
  - Adds run identity: model, pool, segment, evaluation mode, prediction path/SHA, base-config path/SHA, requested candidate IDs.
  - Adds per-row candidate-definition, prediction-SHA, and effective-config-SHA validation.
  - Stores effective-config SHA on every new row.
  - Correctly handles partial retry and all-success reuse.
- Tests updated/added in:
  - `tests/backtest/test_run_strategy_neighborhood_full.py`
  - `tests/backtest/test_run_strategy_sweep.py`
  - `tests/backtest/test_finalize_strategy_neighborhood_full.py` (canonical test-authority fixture only)

## Verification

### Baseline before fixes

```text
tests/backtest/test_run_strategy_neighborhood_full.py
tests/backtest/test_run_strategy_sweep.py
41 passed in 1.46s
```

### Final focused runner/sweep/protocol/finalizer/report set

```text
135 passed in 12.18s
```

The focused set included:

- `test_phase_s_protocol.py`
- `test_run_strategy_sweep.py`
- `test_run_strategy_neighborhood_full.py`
- `test_run_strategy_neighborhood.py`
- `test_strategy_neighborhood_protocol.py`
- `test_finalize_strategy_neighborhood_full.py`
- `test_build_strategy_stability_report.py`
- `test_build_experiment_report.py`

### Final full backtest suite

```text
537 passed, 1 skipped, 2 warnings in 34.84s
```

The two warnings are pre-existing:

- Qlib `Mean of empty slice` in `test_file_strategy.py`.
- Expected fallback warning for a fixture whose source-session meta lacks a handler in `test_freeze_b5_rankic_selection.py`.

### Production artifact-path checks

- General sweep independently validated the real tracked full prediction:

```text
prediction SHA: 951f5fa34fd5641217041edf30fc549931bc1fb07e5d5fdacefa7573bee4ae1f
coverage: 2020-01-13..2026-07-31, 1587 dates, 1584284 rows
index SHA: e6336cd92cc988f71f61afe2907980451ad20201388b6b39a542e469c1313abd
```

- The documented completed resume command was run against the formal artifact and printed:

```text
status: already full_complete; no files rewritten
```

- Python compilation of all changed source/tests exited 0.
- `git diff --check` exited 0.

## Immutable Artifact Preservation

Formal result:

```text
path: backtest/experiments/strategy-neighborhood/20260802_b2s_local_full/full_results.json
bytes: 1776140
SHA-256 before fixes/resume verification: ef801689b5b84b8af74ce250a1048d727d93367c416f1293f78df1ffdd2ffd77
SHA-256 after final tests/resume verification: ef801689b5b84b8af74ce250a1048d727d93367c416f1293f78df1ffdd2ffd77
```

Registry bindings checked after the final full suite:

- Completed row `strategy-neighborhood/b2-s-local-full-v2`: exact SHA match.
- Correction row `strategy-neighborhood/b2-s-local-full-v2-correction-v1`: exact SHA match.
- Reference count: 2; both match.

## Independent Review

An independent read-only reviewer initially identified two additional Important edge cases: missing-protocol writes on completed resume, and omitted resume candidate rows. Both were fixed with new red/green behavioral tests. Follow-up review reported:

```text
Unresolved Critical/Important issues: None.
Ready: yes.
```

## Remaining Concerns

- The explicitly deferred naming mismatch remains unchanged, as required.
- Existing valid/test summaries produced before identity binding do not contain enough provenance to be safely reused; they now fail closed and must be rerun or regenerated. Historical valid/test execution modes themselves remain available.
- No baseline promotion, live-strategy switch, registry rewrite, correction rewrite, or experiment artifact cleanup was performed in this fix wave.
