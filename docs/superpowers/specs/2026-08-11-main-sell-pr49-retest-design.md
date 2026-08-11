# Main SELL and PR49 Retest Design

## Goal

Prepare two independent real-order retests for 2026-08-12:

- main bridge: sell exactly 100 shares of `601326.SH` with QMT `prType=11`;
- standalone PR49 debug strategy: buy exactly 100 shares of `688223.SH`
  with QMT `prType=49`, submitted only during the fixed-price window.

## Main bridge

The repository's current `qmt_signal_bridge.py`, which ignores legacy
`LIVE_OK` marker files, replaces the stale shared deployment copy. The
unclaimed 2026-08-12 two-BUY batch is replaced with one audited SELL batch by
the existing `override_main_signal.py --replace-source` path. The original BUY
pair remains in `archive/superseded` and SQLite records the supersession.

The Windows operator must copy the shared strategy file into QMT, recheck
account `8890116049`, compile, and restart the main strategy before the trading
window. After the SELL test, the main QMT strategy is stopped so normal BUY
signals cannot start formal portfolio construction without a later decision.

## PR49 debug strategy

The standalone debug script accepts a staged `request.json` but calls
`passorder` only when local QMT time is between `15:05:00` and `15:25:00` on
the request's exact `trade_date`. Before the window it appends a bounded
`WAIT_WINDOW` event and leaves the request unchanged. After the window or on a
date mismatch it records a terminal `ERROR` and moves the request to
`request.json.processed`; it never submits late or on another date.

The request schema requires `request_id`, `trade_date`, `side`, `stock_code`,
and `quantity`. The 2026-08-12 request is BUY `688223.SH`, quantity 100, with a
new request ID distinct from the rejected 2026-08-11 attempt. A successful
attempt records full sanitized passorder arguments, `PASSORDER_RETURN`, and
subsequent QMT callbacks in the persistent event log. The debug strategy stays
independent from the main inbox, ledger, and monitoring service.

## Failure handling

- Any main-batch artifact already in `processing` blocks replacement.
- Any stale PR49 `request.json` or duplicate request ID blocks staging.
- Neither strategy retries a request after `PASSORDER_ATTEMPT`.
- Missing account binding, malformed request, wrong date, or missed PR49 window
  produces explicit evidence and no order.

## Verification and deployment

Unit tests cover PR49 before-window waiting, in-window submission, wrong-date
rejection, after-window rejection, and single-consumption behavior. Existing
live-trading tests cover main-batch replacement. After tests pass, both QMT
source files are copied to `/Volumes/qmt_bridge/strategy`, checksums are
verified, the main SELL replacement is published, and the PR49 request is
staged. No order is submitted by macOS during preparation.
