# Live ledger selection and Top5 production scheduling

The user requests a ledger selector because the monitor still reads the old ladder database while the new October30 batch lives in its independent Top5 ledger. They also explicitly request activating the Mac production signal schedule with the same timing as the previous version.

## Design

Keep all monitoring reads in SQLite read-only mode. Discover existing ledgers only through valid config files under the production config directory, bounded to canonical databases inside `live_trading/data`. Deduplicate aliases of one database, prefer the service's explicit active config, and select ledgers per request through opaque allowlisted identifiers. The browser dropdown remembers valid choices, scopes all monitor calls, and invalidates stale requests, date filters and detail dialogs on switch. A newly initialized recorder may not yet have monitor snapshot tables; show its actual cash and batches without creating monitor tables. Research and analysis remain independent.

Reuse the existing `com.yuxianqi.qlib-live-scheduler` LaunchAgent, weekdays20:00 in the logged-in GUI session, `RunAtLoad=false`, `KeepAlive=false`. Bind it explicitly to `alla_v2_top5d1f100_postclose_real_active`. Preserve postclose→publish→evening order, pinned business date, calendar gating and durable stage receipts. Update wrapper defaults so normal manual recovery targets the same active config. Keep strategy identity distinct from config identity in evening recovery hints.

## Deployment and verification

1. Add failing backend/frontend and production-entrypoint regressions; implement and run affected suites.
2. Back up installed plists and the local environment file with private permissions, validate source plists, inspect existing cron jobs and running scheduler state, and atomically install the new configuration.
3. Enable/bootstrap the scheduler without kickstarting it. Restart only the monitor to load the new selector. Verify installed arguments, weekday20:00 triggers, enabled status and absence of duplicate cron work. Current next trading session after2026-10-30 is2026-11-02, verified with the existing calendar provider.
4. Verify API and browser display the new002 batch, switching back to the old ledger, and switching back without stale data. Hash-check both ledgers before/after UI verification to prove no monitoring writes. Preserve all earlier unrelated working-tree changes and account history.
