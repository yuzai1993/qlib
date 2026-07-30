# B5 RankIC Early-Stopping Hyperparameter Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fixed-next-day-valid-RankIC early-stopping DoubleEnsemble, select one of four predeclared B5 hyperparameter candidates without seeing test, and evaluate only the frozen winner on the three official test pools.

**Architecture:** A repository-local model subclass adapts the existing H40 training dataset to a separate H1 valid frame and supplies a custom daily cross-sectional RankIC metric to LightGBM. Separate scripts generate the four candidate grids, freeze a valid-only selection manifest, guard final test evaluation, and register the frozen winner. Existing Qlib core classes and live-trading files remain unchanged.

**Tech Stack:** Python 3.12, Qlib, LightGBM 4.6, pandas, pytest, YAML, JSONL registry.

## Global Constraints

- Follow `backtest/EXPERIMENT_STANDARD.md` v1.8.
- Phase M baseline is `B5 v1.0`; strategy stays frozen and no strategy backtest runs.
- Train only on CSI1000 with seeds `[42, 1000, 2000, 3000, 4000]`.
- Train is `2016-01-02..2020-01-10`, valid is `2020-01-13..2021-07-15`, and test is `2021-07-16..2026-07-16`.
- H40 MSE training label and `DropnaLabel + CSRankNorm` remain unchanged.
- Early stopping uses only fixed-next-day valid RankIC through the safe anchor `2021-07-13`.
- No test prediction or metric may be produced before the selection manifest is frozen.
- Final test evaluation uses `backtest/scripts/eval_ic_multi_pool.py` semantics on csi1000/csi300/csi500.
- Do not modify `live_trading/configs/csi300_topk10_live.yaml` or its artifact.
- Run Qlib multiprocessing entry points from files, never heredoc/stdin.
- Use `/opt/anaconda3/envs/qlib/bin/python` and `MLFLOW_ALLOW_FILE_STORE=true`.

---

### Task 1: Daily RankIC Metric and Fixed Valid Frame

**Files:**
- Create: `backtest/models/rankic_early_stop.py`
- Create: `tests/backtest/test_rankic_early_stop.py`

**Interfaces:**
- Produces: `mean_daily_rank_ic(pred: np.ndarray, label: np.ndarray, index: pd.MultiIndex, min_count: int = 20) -> float`
- Produces: `fixed_next_day_valid_frame(dataset: DatasetH) -> pd.DataFrame`
- Uses: `backtest.scripts.eval_protocol.daily_ic`

- [ ] **Step 1: Write failing metric-equivalence tests**

```python
def test_mean_daily_rank_ic_is_equal_weighted_by_day():
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2020-01-13", "2020-01-14"]), ["A", "B", "C"]],
        names=["datetime", "instrument"],
    )
    pred = np.array([1, 2, 3, 1, 2, 3], dtype=float)
    label = np.array([1, 2, 3, 3, 2, 1], dtype=float)
    assert mean_daily_rank_ic(pred, label, index, min_count=3) == pytest.approx(0.0)


def test_mean_daily_rank_ic_matches_daily_ic_with_ties_nan_and_shuffled_rows():
    expected = daily_ic(pred_series, label_series, min_count=3)["rank_ic"].mean()
    actual = mean_daily_rank_ic(
        pred_series.to_numpy(),
        label_series.to_numpy(),
        pred_series.index,
        min_count=3,
    )
    assert actual == pytest.approx(expected)
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_rankic_early_stop.py -q
```

Expected: import failure because `backtest.models.rankic_early_stop` does not exist.

- [ ] **Step 3: Implement the metric through the unified protocol**

```python
def mean_daily_rank_ic(pred, label, index, min_count=20):
    if len(pred) != len(label) or len(pred) != len(index):
        raise ValueError("prediction, label, and index lengths must match")
    pred_s = pd.Series(np.asarray(pred, dtype=float), index=index, name="pred")
    label_s = pd.Series(np.asarray(label, dtype=float), index=index, name="label")
    daily = daily_ic(pred_s, label_s, min_count=min_count)
    values = daily["rank_ic"].dropna()
    if values.empty:
        raise ValueError("valid RankIC contains no finite trading days")
    return float(values.mean())
```

- [ ] **Step 4: Add fixed-valid boundary tests**

Use a fake dataset and patched calendar/data provider to assert:

```python
assert frame.index.get_level_values("datetime").max() == pd.Timestamp("2021-07-13")
assert fake_dataset.prepared_segments == [slice("2020-01-13", "2021-07-13")]
assert "test" not in fake_dataset.prepared_segments
```

Also assert that changed valid/test boundaries, duplicate indices, fewer than 20 valid instruments, or unmatched label indices fail closed.

- [ ] **Step 5: Implement `fixed_next_day_valid_frame()`**

The implementation must:

```python
VALID_SEGMENT = ("2020-01-13", "2021-07-15")
TEST_SEGMENT = ("2021-07-16", "2026-07-16")
SAFE_VALID_END = "2021-07-13"
EVAL_LABEL_EXPR = "Ref($close, -2)/Ref($close, -1)-1"
```

It prepares `slice("2020-01-13", "2021-07-13")` with
`col_set="feature"` and `DataHandlerLP.DK_I`, fetches the H1 label with
`D.features`, aligns by the exact feature index, drops label NaNs, and
returns feature columns plus one `label/LABEL0` column.

- [ ] **Step 6: Run focused tests and confirm GREEN**

Run the Task 1 pytest command. Expected: all tests pass.

### Task 2: RankIC Early-Stopping DoubleEnsemble

**Files:**
- Modify: `backtest/models/rankic_early_stop.py`
- Modify: `tests/backtest/test_rankic_early_stop.py`

**Interfaces:**
- Produces: `RankICEarlyStoppingDEnsembleModel(DEnsembleModel)`
- Persists: `rankic_evals_result: list[dict]`
- Consumes: `fixed_next_day_valid_frame()` and `mean_daily_rank_ic()`

- [ ] **Step 1: Write failing constructor and LightGBM wiring tests**

Patch `lgb.train` and assert:

```python
assert call.kwargs["params"]["objective"] == "mse"
assert call.kwargs["params"]["metric"] == "None"
assert call.kwargs["valid_sets"] == [captured_dvalid]
assert call.kwargs["valid_names"] == ["valid"]
assert call.kwargs["num_boost_round"] == 200
assert call.kwargs["feval"](pred, captured_dvalid)[0] == "daily_rank_ic"
assert call.kwargs["feval"](pred, captured_dvalid)[2] is True
```

Constructor tests must reject non-GBM base models, non-MSE losses,
`early_stopping_rounds <= 0`, and segment changes.

- [ ] **Step 2: Run the model tests and confirm RED**

Run the Task 1 pytest command. Expected: class import or behavior assertions fail.

- [ ] **Step 3: Implement the prepared-frame adapter and model subclass**

`fit()` must prepare the original purged H40 train frame with `DK_L`,
prepare the separate H1 valid frame, then pass an adapter to
`super().fit()`. `train_submodel()` must call:

```python
model = lgb.train(
    {**self.params, "objective": "mse", "metric": "None"},
    dtrain,
    num_boost_round=self.epochs,
    valid_sets=[dvalid],
    valid_names=["valid"],
    feval=rankic_feval,
    callbacks=[
        lgb.log_evaluation(20),
        lgb.record_evaluation(evals_result),
        lgb.early_stopping(
            self.early_stopping_rounds,
            first_metric_only=True,
        ),
    ],
)
```

Append each submodel's best iteration, best score, and valid-day count to
`rankic_evals_result`.

- [ ] **Step 4: Add a three-submodel regression test**

Use small fake train/valid frames and patched boosters to assert three
submodels train, SR/FS receive only H40 train labels, and prediction retains
the parent class index and equal submodel weights.

- [ ] **Step 5: Run focused and nearby tests**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_rankic_early_stop.py \
  tests/backtest/test_eval_ic_multi_pool.py \
  tests/backtest/test_phase_m_train_only_configs.py -q
```

Expected: all pass.

### Task 3: Generate and Validate the Four Candidate Grids

**Files:**
- Create: `backtest/scripts/generate_b5_rankic_hyperparams.py`
- Create: `tests/backtest/test_b5_rankic_hyperparams.py`
- Create: `backtest/configs/model-hyperparam/rankic-es-base/*.yaml`
- Create: `backtest/configs/model-hyperparam/rankic-es-l1low/*.yaml`
- Create: `backtest/configs/model-hyperparam/rankic-es-lr010/*.yaml`
- Create: `backtest/configs/model-hyperparam/rankic-es-leaves128/*.yaml`

**Interfaces:**
- Produces: `VARIANTS`, `SEEDS`, and 20 deterministic YAML configs
- Consumes: B5 template `backtest/configs/loss-design/cs-rank-norm/ls_rank_norm_s42.yaml`

- [ ] **Step 1: Write failing config matrix tests**

Assert all 20 configs:

```python
assert cfg["run"]["mode"] == "train_only"
assert cfg["segments"] == B5_SEGMENTS
assert cfg["model"]["class"] == "RankICEarlyStoppingDEnsembleModel"
assert cfg["model"]["module_path"] == "backtest.models.rankic_early_stop"
assert cfg["model"]["kwargs"]["epochs"] == 200
assert cfg["model"]["kwargs"]["early_stopping_rounds"] == 20
```

After removing `run.note`, `seed`, and the one declared override, each
variant must equal the base candidate.

- [ ] **Step 2: Run the focused config tests and confirm RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_b5_rankic_hyperparams.py -q
```

- [ ] **Step 3: Implement deterministic generation**

Define:

```python
VARIANTS = {
    "rankic-es-base": {},
    "rankic-es-l1low": {"lambda_l1": 51.425},
    "rankic-es-lr010": {"learning_rate": 0.1},
    "rankic-es-leaves128": {"num_leaves": 128},
}
SEEDS = [42, 1000, 2000, 3000, 4000]
```

Use `yaml.safe_dump(..., sort_keys=False)` and include the exact experiment
command in each header.

- [ ] **Step 4: Generate configs and confirm GREEN**

Run the generator, then the Task 3 pytest command.

### Task 4: Freeze Valid-Only Selection and Guard Test Evaluation

**Files:**
- Create: `backtest/scripts/eval_b5_rankic_valid.py`
- Create: `backtest/scripts/freeze_b5_rankic_selection.py`
- Create: `backtest/scripts/eval_frozen_b5_rankic.py`
- Create: `tests/backtest/test_freeze_b5_rankic_selection.py`

**Interfaces:**
- Produces: a controlled valid evaluator fixed to CSI1000, H1, `min_count=20`, and safe end `2021-07-13`
- Produces: `select_candidate(valid_results: dict[str, dict], config_paths: dict[str, list[Path]]) -> dict`
- Produces: frozen JSON manifest with `selection_metric`, `tie_breaker`, `selected_candidate`, candidate metrics, five sessions, valid-result hashes, and all 20 config SHA-256 hashes
- Consumes: four valid evaluation JSON files

- [ ] **Step 1: Write failing controlled-valid and selection tests**

The controlled valid CLI exposes only config, five sessions, and output. It
must call the existing evaluator with fixed arguments:

```python
segment = "valid"
pools = ["csi1000"]
eval_label_expr = EVAL_LABEL_EXPR
eval_label_role = "fixed_1d"
eval_end = "2021-07-13"
min_count = 20
```

It must record `min_count=20`, validate the returned protocol, and refuse to
overwrite an existing output. No CLI option may override pool, segment,
label, end date, or minimum count.

Test that selection rejects:

- any artifact whose `eval_segment_name != "valid"`;
- any artifact whose official valid segment is not
  `2020-01-13..2021-07-15` or effective segment is not
  `2020-01-13..2021-07-13`;
- any non-fixed-one-day label;
- any `min_count != 20`;
- any pool other than exactly CSI1000;
- missing or duplicate fixed seeds;
- missing CSI1000 metrics;
- non-finite RankIC;
- test keys or test paths in input.

Test ordering:

```python
assert selected["selected_candidate"] == "rankic-es-l1low"
assert selected["selection_metric"] == "csi1000.valid.rank_ic_mean"
assert selected["tie_breaker"] == ["rank_icir", "candidate_id"]
```

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_freeze_b5_rankic_selection.py -q
```

- [ ] **Step 3: Implement the controlled valid evaluator**

Import and call `eval_ic_multi_pool.evaluate()` directly with the fixed
arguments above. Validate the complete valid protocol before atomically
writing a new output; reject an existing output path.

- [ ] **Step 4: Implement fail-closed selection and hashing**

Use `hashlib.sha256(path.read_bytes()).hexdigest()` for all 20 configs and
all four valid artifacts. Recompute each candidate's five-seed RankIC and
RankICIR rather than trusting `seed_mean`, store the exact five selected
sessions and seeds, and refuse to overwrite an existing manifest.

- [ ] **Step 5: Implement the guarded test evaluator**

`eval_frozen_b5_rankic.py` must re-hash configs, verify exact sessions and
seeds, re-hash valid inputs, recompute the winner, initialize Qlib only after
all guards pass, and call existing evaluator semantics with:

```python
segment = "test"
pools = ["csi1000", "csi300", "csi500"]
eval_label_expr = EVAL_LABEL_EXPR
eval_label_role = "fixed_1d"
eval_end = None
min_count = 20
```

No CLI option may override segment, pools, label, end date, or minimum
count. The output must refuse overwrite and record the selection manifest's
SHA-256 hash.

- [ ] **Step 6: Add guard tests and confirm GREEN**

Patch the evaluator and assert a missing/changed manifest prevents any
Qlib initialization or evaluation call. Also assert CLI override attempts are
rejected and existing manifest/output paths are never overwritten. Run the
Task 4 pytest command.

### Task 5: Pre-Registration and Reporting Support

**Files:**
- Create: `backtest/scripts/register_b5_rankic_hyperparams.py`
- Create: `tests/backtest/test_register_b5_rankic_hyperparams.py`
- Modify: `backtest/experiments/registry.jsonl`
- Modify: `backtest/experiments/report.html`

**Interfaces:**
- Produces pending/final registry row `model-hyperparam/valid-rankic-search-v1`
- Consumes selection manifest and frozen test result

- [ ] **Step 1: Write failing registry tests**

Assert the pending row has:

```python
assert row["baseline_ref"] == "B5 v1.0"
assert row["seeds"] == [42, 1000, 2000, 3000, 4000]
assert row["selection_segment"] == "valid"
assert row["selection_official_segment"] == ["2020-01-13", "2021-07-15"]
assert row["selection_effective_segment"] == ["2020-01-13", "2021-07-13"]
assert row["selection_label_role"] == "fixed_1d"
assert row["selection_min_count"] == 20
assert row["test_pools"] == ["csi1000", "csi300", "csi500"]
assert row["conclusion"] == "pending"
```

Pending registration requires the current Qlib `data_version` as an explicit
argument and refuses an existing row with the same experiment ID. It must
append without rewriting any existing registry line.

Finalization must require exactly one still-pending row, preserve every
pre-registered protocol field, verify the complete manifest hash chain through
the Task 4 guard, reject a test result whose data version, sessions, pools,
label, minimum count, or manifest hash differs from the frozen selection, and
include exact-five-seed pairwise CSI1000 RankIC versus B5. It replaces only the
target pending line and preserves every other registry line byte-for-byte.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_register_b5_rankic_hyperparams.py -q
```

- [ ] **Step 3: Implement pending/final registry commands**

The predeclared hypothesis must state that one of four fixed candidates is
selected only by five-seed CSI1000 valid RankIC, then tested once. Replace
the unique pending row in place during finalization instead of appending a
second row. Use an atomic same-directory write and keep all unrelated raw
lines unchanged.

The descriptive conclusion rule is fixed before test:

- `improve` when all three test-pool RankIC means exceed B5;
- `regress` when CSI1000 RankIC does not exceed B5;
- `inconclusive` when CSI1000 improves but at least one transfer pool does
  not.

- [ ] **Step 4: Pre-register before training**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/register_b5_rankic_hyperparams.py \
  --stage pending --data-version <current-qlib-calendar-version>
```

Rebuild the HTML and assert the `model-hyperparam` table begins with B5.

### Task 6: Execute Valid-Only Search

**Files:**
- Create: `backtest/experiments/ic/mh_<variant>_valid_1d.json` for each variant
- Create: `backtest/experiments/b5_rankic_hyperparam_selection.json`

**Interfaces:**
- Consumes 20 generated configs
- Produces four five-seed valid result artifacts and one frozen manifest

- [ ] **Step 1: Run all 20 train-only sessions sequentially**

For each candidate and seed:

```bash
MLFLOW_ALLOW_FILE_STORE=true /opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/run_backtest.py \
  --config model-hyperparam/<candidate>/mh_<candidate_slug>_s<seed>.yaml
```

Record the resulting session paths and verify each session has exactly one
successful run and a unique MLflow experiment ID.

- [ ] **Step 2: Evaluate each candidate on CSI1000 valid only**

For each candidate, use the controlled wrapper so the last valid prediction
anchor is 2021-07-13 and its next-day label never reads test prices:

```bash
MLFLOW_ALLOW_FILE_STORE=true /opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/eval_b5_rankic_valid.py \
  --config model-hyperparam/<candidate>/mh_<candidate_slug>_s42.yaml \
  --sessions <five session:seed values> \
  --output backtest/experiments/ic/mh_<candidate_slug>_valid_1d.json
```

- [ ] **Step 3: Freeze the winner**

Run the selection script with all four valid JSON artifacts and write
`backtest/experiments/b5_rankic_hyperparam_selection.json`.

- [ ] **Step 4: Audit the frozen manifest**

Assert it contains four candidates, exactly five selected sessions, fixed
seeds, finite valid RankIC/RankICIR, and hashes matching disk.

### Task 7: One-Shot Test Evaluation and Final Registration

**Files:**
- Create: `backtest/experiments/ic/mh_valid_rankic_selected_test_1d.json`
- Modify: `backtest/experiments/registry.jsonl`
- Modify: `backtest/experiments/report.html`

**Interfaces:**
- Consumes frozen selection manifest
- Produces the only new test artifact for this search

- [ ] **Step 1: Run guarded three-pool test evaluation**

```bash
MLFLOW_ALLOW_FILE_STORE=true /opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/eval_frozen_b5_rankic.py \
  --manifest backtest/experiments/b5_rankic_hyperparam_selection.json \
  --output backtest/experiments/ic/mh_valid_rankic_selected_test_1d.json
```

- [ ] **Step 2: Finalize registry and rebuild HTML**

```bash
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/register_b5_rankic_hyperparams.py --stage final
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/build_experiment_report.py
```

- [ ] **Step 3: Verify report ordering and metrics**

Assert `model-hyperparam` first row is `baseline/b5-m`, followed by the
selected experiment, with all 12 Phase M metric cells populated.

### Task 8: Cleanup and Final Verification

**Files:**
- Verify all files above
- Do not modify live-trading files

**Interfaces:**
- Consumes registry-driven cleanup plan
- Produces a clean verified experiment state

- [ ] **Step 1: Run the complete relevant test suite**

```bash
/opt/anaconda3/envs/qlib/bin/python -m pytest \
  tests/backtest/test_rankic_early_stop.py \
  tests/backtest/test_b5_rankic_hyperparams.py \
  tests/backtest/test_freeze_b5_rankic_selection.py \
  tests/backtest/test_register_b5_rankic_hyperparams.py \
  tests/backtest/test_eval_ic_multi_pool.py \
  tests/backtest/test_cleanup_experiment_artifacts.py \
  tests/backtest/test_build_experiment_report.py \
  tests/backtest/test_phase_m_train_only_configs.py -q
```

- [ ] **Step 2: Run cleanup dry-run, inspect, then apply**

```bash
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/cleanup_experiment_artifacts.py
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/cleanup_experiment_artifacts.py --apply
/opt/anaconda3/envs/qlib/bin/python \
  backtest/scripts/cleanup_experiment_artifacts.py
```

The final dry-run must have empty delete lists and no warnings/errors.

- [ ] **Step 3: Run integrity checks**

```bash
git diff --check
git diff --name-only -- live_trading
```

Also verify selection hashes, five seeds, three test pools, one final
registry row, B5-first HTML ordering, and no test artifact timestamp
preceding the frozen manifest.
