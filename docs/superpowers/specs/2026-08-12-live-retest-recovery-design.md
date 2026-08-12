# Live Retest Recovery Design

## Goal

Repeat the two failed one-lot validations before any normal portfolio BUY is
allowed:

- main strategy: SELL 100 shares of `601326.SH` with prType 11;
- standalone debug strategy: BUY 100 shares of `688223.SH` with prType 49.

## Root causes

The main failure was deployment drift. The repository template intentionally
defaults to `SIMULATION`/`ALLOW_REAL_MONEY=False`, and the last shared-source
copy overwrote the Windows-local REAL switches. QMT therefore rejected the
REAL batch before reaching `passorder`.

The PR49 strategy depended on `handlebar`. QMT invoked it before the fixed
price window and after a client restart, but never during 15:05–15:25, so the
request expired without a submission attempt.

## Changes

Add a tested runtime renderer that keeps the tracked templates fail-closed but
atomically creates deployment copies using the locally stored real account,
`ACCOUNT_ENVIRONMENT="REAL"`, `ALLOW_REAL_MONEY=True`, the current expected
cash, and `REAL_REQUIRE_EMPTY_POSITIONS=False`. It renders both main and PR49
sources, so future synchronization cannot silently erase runtime binding.

Register an explicit repeating QMT timer in the PR49 strategy. It starts at
15:05:00, runs every three seconds, and calls the existing dated single-use
request handler. Registration success or failure is written to the persistent
event log. `handlebar` remains as a harmless fallback.

## Retest isolation

The normal 2026-08-13 BUY pair is archived as superseded. A new main SELL-only
batch and a new dated PR49 pending request replace the failed test inputs. No
normal BUY batch remains active. Both orders retain the 100-share cap and each
request is consumed at most once.

The macOS side can prepare and verify every artifact, but Windows must still
copy the rendered sources, compile, bind the intended QMT account, and restart
both strategies. The main SELL test must finish successfully before a later
normal BUY batch is restored or published.

## Verification

Tests reproduce both regressions: rendering must emit a REAL deployment
without altering tracked templates, and PR49 init must register the correct
timer whose callback reaches the existing submit path. Run the complete
`tests/live_trading` suite, compare shared-source hashes with rendered output,
decode both staged requests, and confirm no normal BUY signal remains active.
