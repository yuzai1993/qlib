# QMT Clipboard MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver a minimal two-script clipboard trading executor and relay.
**Architecture:** QMT owns memory-only execution and polls broker orders; the Windows relay owns file input/output. One request and response occupy the clipboard at a time.
**Tech Stack:** Python standard library, ctypes Win32 clipboard, pytest with simulated external boundaries.
**Spec:** `docs/superpowers/specs/2026-10-27-qmt-clipboard-mvp-design.md`

## Global Constraints

- QMT Python 3.6 and ASCII source; no QMT file/network I/O.
- Explicit quantity and limit price; immediate one-attempt submission, local account binding.
- No automatic recovery/rebinding, repricing, sizing, cancellation or real trades during development.
- New independent files only; preserve unrelated csindex working changes and existing deployment.

## Review Focus

- Lost responses or repeated commands must not duplicate submission.
- A changed QMT session must stop old instructions, including relay restarts.
- Partial fills and stale broker snapshots must not be counted twice or regress.
- Invalid last orders must not cause earlier orders to execute.
- Clipboard ownership, 64-bit pointer types, lock release and competing writes.

### Task 1: QMT executor

Files: `live_trading/qmt_strategy/qmt_clipboard.py`, `tests/live_trading/test_qmt_clipboard.py`.
Interfaces: `Engine(submit, query, session=None, now=None).handle(request)`; `Clipboard.read()/replace(expected, text)`; `Runtime.step()`; QMT init/timer entry points.

- [x] Write tests for validation, idempotency, session changes, uncertain submissions, cumulative fills and transport races; run and observe missing implementation failures.
- [x] Implement the standard-library executor, broker adapter and bounded native clipboard access.
- [x] Run the new test file and existing bridge tests.

### Task 2: Relay and operator documentation

Files: `live_trading/qmt_strategy/clipboard_relay.py`, `tests/live_trading/test_clipboard_relay.py`, `live_trading/qmt_strategy/README_CLIPBOARD.md`, `live_trading/qmt_strategy/clipboard_order.example.json`.
Interfaces: `exchange(clipboard, request, timeout, interval)`; `save_response(response, output_dir)`; CLI status/send/watch.

- [x] Write relay integration tests for lost responses, session changes and persisted output; run RED.
- [x] Implement relay using the sibling module's clipboard class, preserving trade IDs/session on retries.
- [x] Document deployment, fixed quantities/prices, polling and manual restart reconciliation; include a deliberately expired example.
- [x] Run both new test files, relevant existing tests, formatting and Python 3.6 parsing checks; obtain independent code review.

## Progress / decisions

- User approved the design and development; proceed inline without another approval round.
- Work in the existing checkout, add independent files only, and leave changes uncommitted for review. No install, migration or deployment is required.
- Existing bridge baseline: 197 passed before implementation.
- Task 1 and Task 2 complete. New tests: 45 passed. Each discovered defect was reproduced RED before its fix.
- Independent read-only review found response capacity, stale response acceptance and empty-watch behavior issues; all fixed with regression tests. The review's deliberately excluded recovery/cancellation/sizing/authentication features remain excluded by the user-approved scope; native acceptance remains outstanding. The cosmetic field-count typo was corrected.
- Response handling decision: Unicode JSON avoids diagnostic escaping growth; response capacity is 2 MiB UTF-16 and unexpected overflow returns an explicit error for manual broker reconciliation. No fragmentation or persistent transport was added.
- Initial full `tests/live_trading` run: 1168 passed, 15 failed. All failures are in `test_export_live_model.py`, which depends on absent source artifacts for seeds 42/1000/2000/3000/4000: `test_source_artifact_exists`, `test_export_shrinks_artifact_by_at_least_ten_times`, `test_exported_model_predicts_identically` (five cases each). No training artifacts were regenerated.
- Final regression: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/live_trading -q --disable-warnings --ignore=tests/live_trading/test_export_live_model.py` -> 1169 passed in 36.55s. This excludes the entire artifact-dependent file, including its three passing tests; four new regression cases were added after the initial full run.
- Black and flake8 passed for both scripts and both new test files. Both scripts parse as Python 3.6 and remain ASCII. Example JSON and CLI help validated. No Windows/QMT native execution or real trades performed.
