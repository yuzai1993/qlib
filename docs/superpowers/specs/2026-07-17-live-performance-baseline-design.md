# Live Performance Baseline Design

## Goal

Calculate the first formal live-trading day's account return, benchmark return,
cumulative return, and excess return without restoring or displaying monitoring
records dated before 2026-07-16.

## Chosen approach

Store one explicit performance baseline in the live configuration:

```yaml
monitor:
  performance_baseline:
    first_snapshot_date: "2026-07-16"
    opening_total_value: 10000000.0
    benchmark_close: 4786.78271484375
```

The report pipeline will use this baseline as a synthetic previous snapshot only
when both conditions hold:

1. no earlier `daily_snapshot` exists; and
2. the snapshot being built is exactly `first_snapshot_date`.

All later snapshots continue to use the latest real prior snapshot. The baseline
is configuration metadata and is never returned by the monitoring history API,
so the platform still contains no displayed record before 2026-07-16.

## Calculation

For the first snapshot, the synthetic previous snapshot contains:

- `total_value = opening_total_value`;
- `cumulative_return = 0.0`;
- `benchmark_close = performance_baseline.benchmark_close`;
- `benchmark_cumulative_return = 0.0`.

Existing `build_snapshot` formulas then calculate all four metrics without a
special return formula:

- account daily return: `current_total / opening_total_value - 1`;
- account cumulative return: equal to first-day daily return;
- benchmark daily and cumulative return: `current_close / baseline_close - 1`;
- excess return: account daily return minus benchmark daily return.

For the rebuilt 2026-07-16 snapshot, expected rounded values are:

- account daily and cumulative return: `+0.5329%`;
- CSI 300 daily and cumulative return: `-1.8457%`;
- daily excess return: `+2.3786%`.

## Error handling

The baseline is ignored outside its exact first snapshot date. A partial or
invalid baseline must fail configuration use clearly instead of silently
producing a misleading return. Existing behavior remains unchanged when the
entire `performance_baseline` section is absent.

## Tests and operational update

Tests cover first-day baseline selection, real prior-snapshot precedence,
date mismatch, missing configuration, and the resulting return values. After
the tests pass, rebuild only the 2026-07-16 local snapshot with external daily
notification disabled, then verify the database and monitoring API values.
