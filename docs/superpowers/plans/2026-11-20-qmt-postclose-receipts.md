# QMT Postclose Receipts Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement each task with a failing regression first.

**Goal:** Export confirmed fills independently and close verified zero-fill day orders after 17:00.

**Architecture:** QMT supplies raw ORDER/POSITION/DEAL observations. A pure settlement module verifies evidence; AutoRelay persists closure and immutable receipt publications separately from raw broker states.

**Tech Stack:** Python standard library, existing JSON bridge, SQLite ledger and pytest.

**Spec:** `docs/superpowers/specs/2026-11-20-qmt-postclose-receipts-design.md`

## Global constraints

- QMT Python 3.6; relay Python 3.8+.
- No new trade requests during recovery or verification; preserve journals and broker identifiers.
- Cash changes only through fills and existing fees; preserve raw broker statuses.

## Review focus

- Zero fills during an open market must remain nonterminal.
- A broker cache missing a fill must disagree with independent deals/positions and remain unresolved.
- Receipt publication/import crashes must not duplicate trades, cash or fees.
- Missing or inconsistent historical closure evidence must block the next day.
- Source deployment must preserve the local account setting and require observed runtime versions.

## Tasks

- [x] Audit and reconcile today's confirmed fills and zero-fill day order; quarantine incorrect next batch.
- [x] Add and test `clipboard_settlement.py`: pure identity, timing, deal and position reconciliation.
- [x] Add validated DEAL evidence to `qmt_clipboard.py` snapshots, retaining daytime compatibility.
- [x] Integrate settlement baseline, 17:00 closure and independently exported immutable receipts in `clipboard_auto.py`.
- [x] Cover import idempotency, restart/crash behavior and prior-day closure validation with regression tests.
- [x] Rebuild report and next plan from repaired ledger; verify only corrected plan remains active.
- [x] Review and deploy Windows source; preserve audits and remove task temporary material.
- [x] Load new QMT editor source and verify read-only Windows runtime after maintenance.

2026-11-20 validation: all 1638 live-trading tests passed. Independent review found and verified the fix for changed historical working-order evidence; no remaining P1/P2. Windows package deployed with original account and state preserved, Python 3.6/3.8 syntax checked locally and on Windows. Editor reload was deferred during QMT maintenance and completed below.

2026-11-23 completion: user reloaded QMT after maintenance. Real poll/snapshot confirmed the new source and DEAL capability. Four holdings match the ledger; independent deals confirm both fills and no fill for the user-confirmed expired order. Reconnection clears the broker remark; regression-first relay fix permits an empty remark only, retains remaining identity/evidence checks and rejects different nonempty remarks. Independent review passed; 261 related tests passed. Tomorrow's current plan recomputation and offline old-order gate replay passed; this replay does not replace execution-time checks. Audit: `live_trading/logs/qmt_acceptance_20261123.json`. No trade requests sent, original journals preserved.

2026-11-24 pre-commit validation: all 1649 live-trading tests passed. Runtime sources match the accepted Windows package; Python 3.6/3.8 syntax, `git diff --check` and removal of task temporary material verified.
