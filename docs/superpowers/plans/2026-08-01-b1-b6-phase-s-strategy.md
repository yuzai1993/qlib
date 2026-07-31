# B1-M / B6-M Phase S Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a preregistered, model-aware Phase S strategy sweep for the frozen B1-M and B6-M single-model artifacts, select on CSI1000 valid only, evaluate the frozen winners through 2026-07-31, and publish a separate strategy HTML report.

**Architecture:** A pure `phase_s_protocol` module owns artifact validation, deterministic grids, prediction manifests, winner selection, and registry row construction. Thin CLIs generate frozen predictions, run pred-only backtests, build the strategy report, and clean artifacts. Existing Qlib backtest execution remains the single execution engine; no model training code is called.

**Tech Stack:** Python 3.11, pandas, PyYAML, Qlib, pytest, JSONL registry, self-contained HTML.

## Global Constraints

- Load Phase S models only from `backtest/models/baselines/<model-ref>/manifest.json`.
- Use one retained model per model ref; do not read `mlruns/` or historical result sessions to find a model.
- Select only on CSI1000 valid `2020-01-13` through `2021-07-15`.
- Open test only after winner freeze; test is `2021-07-16` through `2026-07-31`.
- Final pools are CSI1000/SH000852, CSI300/SH000300, and CSI500/SH000905.
- Account is exactly `500000`; risk degree is `0.95`.
- Costs are open `0.00021`, close `0.00071`, minimum `5`, trade unit `100`, close-price execution, limit `0.095`.
- Registry is the only report data source; never hand-edit HTML.
- Preserve the user's pre-existing untracked `backtest/models/baselines/` contents.

---

### Task 1: Freeze the single-artifact Phase S contract

**Files:**
- Modify: `backtest/EXPERIMENT_STANDARD.md`
- Create: `backtest/scripts/phase_s_protocol.py`
- Create: `tests/backtest/test_phase_s_protocol.py`

**Interfaces:**
- Produces: `FrozenModel(model_ref, manifest_path, model_path, model_sha256, source_config)`.
- Produces: `load_frozen_model(repo_root: Path, model_ref: str) -> FrozenModel`.
- Produces: `strategy_grid(model_ref: str) -> list[dict]`.
- Produces: `select_valid_winner(rows: Sequence[dict]) -> dict`.

- [ ] **Step 1: Write failing artifact-contract tests**

```python
def test_load_frozen_model_rejects_path_outside_baseline_dir(tmp_path):
    baseline = tmp_path / "backtest/models/baselines/b6-m"
    baseline.mkdir(parents=True)
    outside = tmp_path / "elsewhere/model"
    outside.parent.mkdir()
    outside.write_bytes(b"model")
    (baseline / "manifest.json").write_text(json.dumps({
        "baseline_exp_id": "baseline/b6-m",
        "source": {"config": "backtest/configs/b6.yaml"},
        "retained_model": {
            "path": "elsewhere/model",
            "sha256": hashlib.sha256(b"model").hexdigest(),
            "size_bytes": 5,
        },
    }))
    with pytest.raises(ValueError, match="inside baseline directory"):
        protocol.load_frozen_model(tmp_path, "b6-m")

def test_load_frozen_model_verifies_size_and_sha(tmp_path):
    baseline = tmp_path / "backtest/models/baselines/b1-m"
    config = tmp_path / "backtest/configs/b1.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("run: {mode: train_only}\n")
    model = baseline / "seed2000/trained_model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"frozen-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    (baseline / "manifest.json").write_text(json.dumps({
        "baseline_exp_id": "baseline/b1-m",
        "source": {"config": "backtest/configs/b1.yaml"},
        "retained_model": {
            "path": "backtest/models/baselines/b1-m/seed2000/trained_model",
            "sha256": digest,
            "size_bytes": model.stat().st_size,
        },
    }))
    frozen = protocol.load_frozen_model(tmp_path, "b1-m")
    assert frozen.model_sha256 == digest
```

- [ ] **Step 2: Run tests and verify RED**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_phase_s_protocol.py -q`  
Expected: FAIL because `phase_s_protocol` does not exist.

- [ ] **Step 3: Implement the frozen artifact loader and update the standard**

Update Phase S sections 2, 5.2, 6.2, 6.3, and checklist to state that Phase S uses the single manifest-selected artifact under `backtest/models/baselines/`; retain Phase M five-seed requirements unchanged.

Implement path containment, ID, file-size, and SHA validation in `load_frozen_model`.

- [ ] **Step 4: Add deterministic grid and selection tests**

```python
def test_b1_grid_contains_18_unique_candidates_and_baseline():
    rows = protocol.strategy_grid("b1-m")
    assert len(rows) == 18
    assert len({row["candidate_id"] for row in rows}) == 18
    assert protocol.BASELINE_CANDIDATE_ID in {row["candidate_id"] for row in rows}

def test_b6_grid_contains_22_unique_candidates_and_baseline():
    rows = protocol.strategy_grid("b6-m")
    assert len(rows) == 22
    assert len({row["candidate_id"] for row in rows}) == 22

def test_selection_uses_ir_ann_mdd_turnover_then_id():
    rows = [
        {
            "candidate_id": candidate_id,
            "status": "success",
            "excess_with_cost_information_ratio": 1.0,
            "excess_with_cost_annualized_return": 0.2,
            "excess_with_cost_max_drawdown": -0.1,
            "annualized_one_way_turnover": 12.0,
        }
        for candidate_id in ("candidate-b", "candidate-a")
    ]
    winner = protocol.select_valid_winner(rows)
    assert winner["candidate_id"] == "candidate-a"
```

- [ ] **Step 5: Implement exact B1/B6 grids and lexicographic selection tuple**

Use `(-ir, -ann, -mdd, annualized_one_way_turnover, candidate_id)` as the ascending sort key; reject failed or non-finite rows.

- [ ] **Step 6: Run tests and commit**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_phase_s_protocol.py -q`  
Expected: PASS.

Commit: `feat(backtest): define single-artifact Phase S protocol`

---

### Task 2: Add Phase S execution diagnostics without duplicating predictions

**Files:**
- Modify: `backtest/scripts/run_backtest.py`
- Modify: `backtest/scripts/run_pred_backtest.py`
- Create: `tests/backtest/test_phase_s_metrics.py`

**Interfaces:**
- Produces new metric keys: `annualized_one_way_turnover`, `cumulative_trade_cost`, `mean_holding_count` when data is available.
- `run_pred_backtest.py --skip-pred-copy` references the immutable source prediction instead of copying it into every result and MLflow recorder.

- [ ] **Step 1: Write failing diagnostic extraction tests**

```python
def test_extract_metrics_adds_phase_s_diagnostics():
    report = report_frame(turnover=[0.2, 0.4], total_cost=[10.0, 25.0])
    metrics = run_backtest.extract_metrics(analysis_frame(), report)
    assert metrics["annualized_one_way_turnover"] == pytest.approx(37.5)
    assert metrics["cumulative_trade_cost"] == 25.0
```

The annualized one-way formula is `mean(daily turnover) * 250 / 2`.

- [ ] **Step 2: Verify RED, implement diagnostics, verify GREEN**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_phase_s_metrics.py -q`

- [ ] **Step 3: Write failing CLI/meta tests for `--skip-pred-copy`**

Test argument parsing and a pure helper returning `saved_pred=None` while retaining source path and SHA in metadata.

- [ ] **Step 4: Implement skip-copy behavior**

When enabled, do not call `to_pickle` or `recorder.save_objects(local_path=pred_path)`; include `source_pred`, `source_pred_sha256`, and `saved_pred: null` in `meta.json`.

- [ ] **Step 5: Run tests and commit**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_phase_s_metrics.py tests/backtest/test_config_loader_strategy.py -q`  
Expected: PASS.

Commit: `feat(backtest): record Phase S turnover and immutable predictions`

---

### Task 3: Generate and validate frozen predictions

**Files:**
- Create: `backtest/scripts/generate_phase_s_predictions.py`
- Create: `tests/backtest/test_generate_phase_s_predictions.py`
- Create at runtime: `backtest/experiments/strategy/20260801_b1_b6/prediction_manifest.json`
- Create at runtime, ignored: `backtest/experiments/strategy/20260801_b1_b6/predictions/**/*.pkl`

**Interfaces:**
- Consumes: `load_frozen_model` and each manifest's `source.config`.
- Produces: `normalize_prediction(pred) -> pd.Series`.
- Produces: `validate_prediction_index(pred, expected_dates) -> dict`.
- Produces: `build_prediction_manifest_entry(path, model, pool, segment, coverage) -> dict`.
- Produces one valid and one test prediction per model/pool, plus SHA-256 and coverage metadata.

- [ ] **Step 1: Write failing prediction validation tests**

```python
def test_prediction_requires_exact_named_unique_multiindex():
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2020-01-13"), "SH600000")] * 2,
        names=["datetime", "instrument"],
    )
    with pytest.raises(ValueError, match="duplicate"):
        prediction.validate_prediction_index(pd.Series([1.0, 2.0], index=index), [pd.Timestamp("2020-01-13")])

def test_prediction_coverage_rejects_missing_trading_date():
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2020-01-13"), "SH600000")],
        names=["datetime", "instrument"],
    )
    with pytest.raises(ValueError, match="missing"):
        prediction.validate_prediction_index(
            pd.Series([1.0], index=index),
            [pd.Timestamp("2020-01-13"), pd.Timestamp("2020-01-14")],
        )

def test_manifest_records_model_config_prediction_sha_and_data_version(tmp_path):
    pred = tmp_path / "pred.pkl"
    pred.write_bytes(b"prediction")
    entry = prediction.build_prediction_manifest_entry(
        pred,
        SimpleNamespace(model_ref="b1-m", model_sha256="model-sha", source_config=Path("b1.yaml")),
        pool="csi1000",
        segment="valid",
        coverage={"start": "2020-01-13", "end": "2021-07-15", "n_dates": 365},
        data_version="2026-07-31",
    )
    assert entry["model_sha256"] == "model-sha"
    assert entry["prediction_sha256"] == hashlib.sha256(b"prediction").hexdigest()
    assert entry["data_version"] == "2026-07-31"
```

- [ ] **Step 2: Verify RED, implement normalization/coverage/manifest, verify GREEN**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_generate_phase_s_predictions.py -q`

- [ ] **Step 3: Implement Qlib prediction CLI**

Load the retained pickle directly, reconstruct the handler/dataset from the manifest source config, override only pool and inference segment end, and save normalized one-column predictions. The CLI must never call model `fit`.

- [ ] **Step 4: Run unit tests and commit**

Commit: `feat(backtest): freeze Phase S prediction bundles`

---

### Task 4: Make strategy sweep model-aware and valid-only

**Files:**
- Modify: `backtest/scripts/run_strategy_sweep.py`
- Create: `tests/backtest/test_run_strategy_sweep.py`
- Create at runtime: `backtest/configs/strategy-sweep/b1-m/*.yaml`
- Create at runtime: `backtest/configs/strategy-sweep/b6-m/*.yaml`

**Interfaces:**
- Consumes: `strategy_grid(model_ref)` and one immutable prediction path.
- Produces: `build_sweep_config(base, candidate, *, pool, segment) -> dict`.
- Produces: `build_backtest_command(python, script, pred, config, note) -> list[str]`.
- Produces candidate configs with exact account, dates, pool benchmark, costs, and strategy kwargs.
- Produces `valid_results.json` and `selection.json`.

- [ ] **Step 1: Write failing config and command tests**

```python
def test_valid_config_uses_csi1000_dates_500k_and_live_costs():
    cfg = sweep.build_sweep_config({}, protocol.strategy_grid("b1-m")[0], pool="csi1000", segment="valid")
    assert cfg["segments"]["valid"] == ["2020-01-13", "2021-07-15"]
    assert cfg["backtest"]["account"] == 500000
    assert cfg["backtest"]["exchange_kwargs"]["open_cost"] == 0.00021
    assert cfg["data"]["benchmark"] == "SH000852"

def test_soft_topk_impact_limit_is_precomputed_absolute_weight():
    candidate = next(row for row in protocol.strategy_grid("b1-m") if row["candidate_id"] == "soft-t10-i050")
    assert candidate["trade_impact_limit"] == pytest.approx(0.95 / 10 * 0.50)

def test_sweep_invokes_pred_backtest_with_skip_copy(tmp_path):
    command = sweep.build_backtest_command(
        Path(sys.executable), Path("run_pred_backtest.py"), tmp_path / "pred.pkl", tmp_path / "candidate.yaml", "valid-b1",
    )
    assert "--skip-pred-copy" in command
```

- [ ] **Step 2: Verify RED, implement config generation and runner injection, verify GREEN**

- [ ] **Step 3: Remove the obsolete fixed 27-grid and pass/fail gate**

The sweep reports a winner but does not claim strategy adoption. It must include every failed candidate row.

- [ ] **Step 4: Run tests and commit**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_run_strategy_sweep.py tests/backtest/test_phase_s_protocol.py -q`

Commit: `feat(backtest): run model-aware valid strategy sweeps`

---

### Task 5: Preregister and render Phase S results

**Files:**
- Create: `backtest/scripts/register_phase_s_experiment.py`
- Create: `backtest/scripts/build_strategy_report.py`
- Create: `tests/backtest/test_register_phase_s_experiment.py`
- Create: `tests/backtest/test_build_strategy_report.py`
- Modify: `backtest/scripts/build_experiment_report.py`

**Interfaces:**
- Produces/updates exactly two registry rows: `strategy-sweep/b1-m` and `strategy-sweep/b6-m`.
- Supports states `preregistered`, `valid_complete`, and `test_complete` with monotonic transitions.
- `build_strategy_report.build_html(rows)` renders only Phase S rows from the main registry.

- [ ] **Step 1: Write failing preregistration tests**

Assert complete grid, artifact SHA, selection segment/rule, account/costs, and test policy; reject any transition that changes a preregistered candidate or selection rule.

- [ ] **Step 2: Implement atomic registry upsert and state validation**

Write a temporary sibling and `Path.replace`; preserve all unrelated registry rows and exactly one line per experiment ID.

- [ ] **Step 3: Write failing strategy report tests**

Assert B1/B6 sections, baseline-first valid table, winner highlight, three-pool final comparison, yearly IR, artifact SHA, and no Phase M content.

- [ ] **Step 4: Implement standalone report and compatible general-report rendering**

Output path: `backtest/experiments/strategy_report.html`.

- [ ] **Step 5: Run tests and commit**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_register_phase_s_experiment.py tests/backtest/test_build_strategy_report.py tests/backtest/test_build_experiment_report.py -q`

Commit: `feat(backtest): register and report Phase S experiments`

---

### Task 6: Add independent Phase S retention

**Files:**
- Modify: `backtest/scripts/cleanup_experiment_artifacts.py`
- Modify: `tests/backtest/test_cleanup_experiment_artifacts.py`

**Interfaces:**
- Phase M selection remains unchanged.
- Phase S retained sessions are the registered baseline and frozen winner for each completed model group.
- Valid losers and their backtest MLflow recorders are deletable only after registry/report completion.

- [ ] **Step 1: Write failing retention and safety tests**

```python
def phase_s_row(model_ref, *, baseline, winner, state="test_complete"):
    return {
        "exp_id": f"strategy-sweep/{model_ref}",
        "phase": "S",
        "state": state,
        "model_ref": model_ref,
        "retained_result_dirs": [baseline, winner],
    }

def phase_m_baseline():
    metrics = {
        pool: {"rank_ic_mean": 0.03, "rank_icir": 0.2}
        for pool in ("csi300", "csi500", "csi1000")
    }
    return {
        "exp_id": "baseline/b6-m",
        "direction": "baseline",
        "phase": "M",
        "conclusion": "baseline",
        "baseline_ref": "B6 v1.0",
        "seeds": [42, 1000, 2000, 3000, 4000],
        "metrics_summary": metrics,
        "result_dirs": [],
    }

def test_phase_s_retains_baseline_and_frozen_winner_per_model():
    rows = [phase_s_row("b1-m", baseline="base-b1", winner="win-b1"), phase_s_row("b6-m", baseline="base-b6", winner="win-b6")]
    retained = cleanup.select_phase_s_retained_result_paths(rows)
    assert retained == {"base-b1", "win-b1", "base-b6", "win-b6"}

def test_phase_s_never_treats_single_model_as_phase_m_seed_group():
    phase_m = cleanup.select_retained_rows([phase_m_baseline()])
    assert [row["exp_id"] for row in phase_m] == ["baseline/b6-m"]

def test_phase_s_incomplete_bundle_blocks_all_deletion(tmp_path):
    plan = cleanup.build_cleanup_plan(tmp_path, [phase_m_baseline(), phase_s_row("b1-m", state="valid_complete", baseline="missing", winner="missing")])
    assert plan["errors"]
    assert plan["delete_result_dirs"] == []
    assert plan["delete_mlruns_dirs"] == []

def test_baseline_model_directory_is_outside_cleanup_roots(tmp_path):
    model = tmp_path / "backtest/models/baselines/b1-m/seed2000/trained_model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    plan = cleanup.build_cleanup_plan(tmp_path, [phase_m_baseline()])
    assert model not in plan["delete_result_dirs"]
    assert model not in plan["delete_mlruns_dirs"]
```

- [ ] **Step 2: Verify RED, implement separate Phase S selectors and validators, verify GREEN**

- [ ] **Step 3: Run cleanup suite and commit**

Run: `/opt/anaconda3/envs/qlib/bin/python -m pytest tests/backtest/test_cleanup_experiment_artifacts.py -q`

Commit: `feat(backtest): retain Phase S baseline and winners safely`

---

### Task 7: Execute the preregistered experiment

**Files:**
- Modify: `backtest/experiments/registry.jsonl`
- Create: `backtest/experiments/strategy/20260801_b1_b6/protocol.json`
- Create: `backtest/experiments/strategy/20260801_b1_b6/prediction_manifest.json`
- Create: `backtest/experiments/strategy/20260801_b1_b6/b1-m/{valid_results,selection,test_results}.json`
- Create: `backtest/experiments/strategy/20260801_b1_b6/b6-m/{valid_results,selection,test_results}.json`

- [ ] **Step 1: Generate protocol and preregister both rows**

Verify registry rows contain 18 B1 candidates and 22 B6 candidates before any backtest.

- [ ] **Step 2: Generate predictions and bind their SHA values**

Run the prediction CLI for both models and all three pools/segments. Verify the data calendar ends on `2026-07-31` and every manifest entry has exact coverage.

- [ ] **Step 3: Run B1-M and B6-M valid sweeps**

Use only CSI1000 valid prediction files. Persist every success/failure, then freeze exactly one winner per model using `select_valid_winner`.

- [ ] **Step 4: Verify winner freeze before test**

Confirm selection JSON SHA and registry state `valid_complete`; test command must read candidate IDs only from frozen selection files.

- [ ] **Step 5: Run final test matrix**

For each model, run baseline and winner once on CSI1000/CSI300/CSI500 with the corresponding frozen test predictions. Compute yearly IR from each `report_normal.csv`.

- [ ] **Step 6: Finalize registry and generate both HTML reports**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_strategy_report.py
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_experiment_report.py
```

- [ ] **Step 7: Dry-run and apply cleanup**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/cleanup_experiment_artifacts.py
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/cleanup_experiment_artifacts.py --apply
```

Inspect the dry-run plan before apply; abort if it includes retained baseline/winner sessions or `backtest/models/baselines/`.

---

### Task 8: Final verification

**Files:**
- Verify all modified and generated tracked files.

- [ ] **Step 1: Run focused Phase S suite**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_phase_s_protocol.py \
  tests/backtest/test_phase_s_metrics.py \
  tests/backtest/test_generate_phase_s_predictions.py \
  tests/backtest/test_run_strategy_sweep.py \
  tests/backtest/test_register_phase_s_experiment.py \
  tests/backtest/test_build_strategy_report.py \
  tests/backtest/test_build_experiment_report.py \
  tests/backtest/test_cleanup_experiment_artifacts.py -q
```

- [ ] **Step 2: Verify registry, HTML, hashes, and no test-selection leakage**

Run a read-only audit command that asserts both rows are `test_complete`, selection came from valid, test candidate IDs equal frozen selection IDs, all referenced JSON/HTML files exist, and SHA values match.

- [ ] **Step 3: Review diff and commit final artifacts**

Do not stage unrelated user files. Commit only code, tests, configs, registry summaries, JSON manifests, and generated HTML.
