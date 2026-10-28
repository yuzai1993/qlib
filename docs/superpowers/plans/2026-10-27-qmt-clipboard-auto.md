# QMT Clipboard Automatic Daily Flow Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. Track completed tasks here.

**Goal:** Remove daily manual price, quantity, session and relay steps using the existing Mac scheduler and shared files.

**Architecture:** Windows orchestrates the protocol-v2 files and existing clipboard trade executor. QMT adds snapshot queries. Existing Mac fill/account import formats remain unchanged.

**Tech Stack:** Python stdlib (QMT 3.6; Windows 3.8+), Windows Task Scheduler/PowerShell, pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-qmt-clipboard-auto-design.md`

## Constraints and review focus

- No QMT filesystem/network I/O, no new Python dependencies.
- Test times explicitly in UTC+08; no real broker submission or installation of trading tasks during development.
- Atomic journals precede sending. Lost replies, relay restarts, session changes and request expiry must not duplicate orders.
- Reject malformed/checksum-mismatched/wrong-date/multiple batches before first submission.
- Fresh market/account data and per-request cash reservations prevent stale pricing/overspending.
- Open/unknown partial orders must not become terminal fills. Existing importer must reproduce quantities/cash/cohorts.
- Windows Interactive task and shared-root/local-state path distinction must be explicit in deployment.

## Tasks

- [x] 1. Add tests then implement QMT snapshot request/capabilities, account identity, positions and quote normalization; preserve MVP tests and Python 3.6 syntax.
- [x] 2. Add tests then implement `clipboard_auto.py`: strict v2 batch parsing, schedule/sizing, persisted immutable requests, result export and daily lifecycle. Inject transport/clock for real Engine integration tests.
- [x] 3. Wire `clipboard_relay.py auto`, interval polling/error logging, single-client mutex, local state/output and shared exports. Add Windows installer with current user Interactive weekly trigger.
- [x] 4. Test Mac SignalPublisher → simulated QMT/clipboard → FillImporter/LiveRecorder, restarts/uncertainty/partial fills; update README and deployable bundle.
- [x] 5. Run targeted tests, live-trading regression excluding already-known missing model artifacts, syntax/lint, fresh code review and fix significant findings.

## Decisions / progress

- Continue authorized implementation without another design approval round: user explicitly replaced manual daily workflow with scheduled automation.
- Keep prior MVP additions in this workspace; do not reset or replace unrelated changes. No commit/push requested.
- Preserve production Mac schedule and configs: adapt receipts to existing importer rather than introduce a second ledger.
- Verification: 72 clipboard tests pass; live-trading regression 1196 passed in 32.36s, excluding the previously known `test_export_live_model.py` missing historical training artifacts. Black, flake8 (E203 ignored to match Black slices), QMT Python 3.6/Windows Python 3.8 AST and ASCII checks pass.
- Independent reviewer found and verified fixes for zero-quantity legacy receipts, limit-price budget ceilings, official QMT `m_nVolumeTotalOriginal`, and lagging fill volume despite status 56. Each fix reproduced RED then passed GREEN.
- One-time cutover: user confirmed no 2026-10-26 executions. Backed up production ledger and recorded two historical zero-fill receipts with explicit user-confirmation provenance. Cash/aggregate positions unchanged. Cohort tables remain at the pre-cutover state so the already-published 2026-10-27 plan stays unchanged; normal post-close importer advances both dates. Audit/backup: `live_trading/data/repairs/20261027_clipboard_cutover/`.
- Read-only production preview with saved 2026-10-26 predictions succeeds after loading the normal cron environment: SELL 301165.SZ 600; BUY 300214.SZ target 49969.92. Nothing republished or submitted. First calendar attempts were blocked by missing shell env/sandbox network; the normal environment and read-only escalated retry succeeded.
- Remaining deployment validation is native Windows Task Scheduler registration and the enhanced QMT account/quote `auto --check`; neither can be verified on this Mac. No real trade or scheduled trading task was executed during development.
