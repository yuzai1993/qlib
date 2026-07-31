# Single-Model Phase S Baseline Design

## Goal

Make `backtest/models/baselines/<baseline>/manifest.json` the canonical model-artifact entry point for Phase S. B6-M uses one frozen model, seed 4000, rather than a five-seed prediction ensemble.

## Decisions

- Phase M model comparison remains a fixed-five-seed research protocol; its registry metrics are unchanged.
- Phase S loads only `backtest/models/baselines/b6-m/seed4000/trained_model`.
- Seed 4000 is justified by the pre-test CSI1000 valid RankIC (`0.049698109941383316`), not by formal test performance.
- The baseline manifest stores the retained model path/hash, source config path/hash, selection segment and metric, and source provenance needed to verify the artifact.
- `backtest/experiments/b6_model_freeze.json` is deleted. Phase M evaluation metrics remain in `registry.jsonl`, `report.html`, and `experiments/ic/`.
- `EXPERIMENT_STANDARD.md` distinguishes five-seed Phase M evaluation from single-model Phase S execution.

## Data Flow

Phase S resolves `B6 v1.0` to `backtest/models/baselines/b6-m/manifest.json`, verifies the retained model SHA-256 and config SHA-256, then generates one frozen prediction stream. Strategy experiments reuse that same prediction stream and record its hash in the Phase S registry row.

## Validation

- The B6 manifest must identify seed 4000 and the valid selection segment.
- The retained model and source config hashes must match files on disk.
- Registry and the experiment standard must not reference `b6_model_freeze.json`.
- Existing Phase M metrics and evaluation artifacts must remain unchanged.
