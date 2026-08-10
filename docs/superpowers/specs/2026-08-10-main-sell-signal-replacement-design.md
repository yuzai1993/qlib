# Main SELL Signal Replacement Design

## Goal

Replace the unclaimed 2026-08-11 main-strategy BUY batch with exactly one
auditable 100-share SELL for `601326.SH`, executed by the QMT close-auction
profile (`CLOSE_AUCTION_LIMIT`, QMT `prType=11`).

## Safety invariants

- QMT main strategy must remain stopped during replacement.
- The original BUY JSONL and marker remain recoverable, but neither remains in
  `inbox` or `processing` after replacement.
- The replacement keeps the main strategy ID, account metadata, trade date,
  signal date, and price type from the original batch.
- The SELL instrument metadata comes from the previously published batch that
  bought `601326.SH`; the quantity is exactly 100 shares.
- SQLite records both batches and marks the original 2026-08-11 BUY batch as
  superseded by the SELL replacement.
- Publication is fail-closed if the original batch has been claimed, the held
  stock cannot be proven from the ledger, or an unexpected same-date artifact
  exists.

## Replacement flow

1. Verify `601326.SH` has at least 100 shares in the live ledger and confirm the
   original 2026-08-11 BUY pair is still wholly in `inbox`.
2. Build and validate a new one-order batch and record it durably in SQLite.
3. Move the exact original BUY pair into a dedicated recoverable superseded
   archive directory.
4. Publish the replacement SELL pair atomically to `inbox` and mark the old
   batch superseded in SQLite.
5. Verify the inbox contains only the replacement for the main strategy/date,
   its checksum validates, and the evening integrity monitor passes.

If a step fails, QMT remains stopped and the artifacts are inspected before any
retry. No PR49 request is created as part of this operation.

## Verification

- Unit tests cover prior-batch instrument lookup, held-quantity rejection,
  same-date metadata, and supersession behavior.
- Operational checks inspect SQLite, bridge directories, and decoded signal
  rows before QMT may be started.
