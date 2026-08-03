# Account Value Adjustment Design

## Context

The cleared QMT simulation account reports:

- available cash: `9,949,714.06`
- total assets: `9,268,587.08`
- aggregate market value: `-681,126.98`
- ordinary stock positions: empty

QMT exposes available cash and total assets as different account fields. The
existing local ledger can only express `cash + long positions`, so it cannot
preserve both broker values at once. Seeding the ledger with available cash
would overstate NAV and order sizing; seeding it with total assets would cause
a permanent cash reconciliation error.

## Decision

Add an immutable-on-bootstrap account-level `opening_value_adjustment`.

For this simulation account:

```text
cash                      9,949,714.06
account value adjustment   -681,126.98
opening economic value     9,268,587.08
```

The adjustment is not cash, a stock position, an external flow, or a trading
profit/loss. It is a persistent valuation component that lets the local ledger
represent a broker-side aggregate value that has no ordinary POSITION row.

## Accounting Rules

1. Spendable cash remains the ledger `cash` balance and is reconciled against
   QMT available cash.
2. Economic account value is:

   ```text
   cash + listed positions + receivables + pending shares
   - tax provision + account value adjustment
   ```

3. The same economic value is used for TopK target sizing, daily NAV,
   position weights, turnover, and backtest account parity.
4. A constant adjustment does not enter `external_flow`; therefore it does not
   create a false daily return.
5. The adjustment is seeded only into a fresh, unused ledger. A later config
   change must not silently rewrite account history.
6. Config validation accepts any finite adjustment, but requires
   `opening_cash + opening_value_adjustment > 0`.

## Reconciliation

When a LIVE paper batch produces a QMT account snapshot, retain the existing
cash and position checks and add a residual-value check:

```text
broker residual = broker aggregate market value
                  - sum(broker POSITION market values)
```

The broker residual must match the ledger adjustment within the configured
cash tolerance. The check is skipped if QMT omits the required market-value
fields, avoiding false positives from incomplete snapshots. A mismatch is
critical because it means NAV and future target sizing are stale.

## Persistence and Observability

- Store the adjustment in `account_state` under `value_adjustment`.
- Add `account_value_adjustment` to `daily_snapshot`, including a backwards-
  compatible SQLite migration with default zero.
- Show non-zero adjustments in the daily report and expose the current value in
  the monitoring overview.

## Deployment

Update the CSI1000 paper config and its parity backtest account to the exact
economic opening value. Initialize the previously absent durable database,
verify the stored cash and adjustment, install the 20:00/21:30/22:30 cron
schedule, and run Shadow preflight. Do not create `LIVE_OK`, do not set
`LIVE_TRADING_CONFIRM`, and do not submit any QMT order.

## Rejected Representations

- A negative stock position would contaminate strategy sell/hold logic and does
  not correspond to any QMT POSITION row.
- A cash withdrawal would make local spendable cash disagree with QMT.
- Ignoring the gap would over-allocate roughly `681,126.98 * 95%` of risk
  capital and make reported NAV incorrect.
