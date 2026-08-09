# A 股赢家画像驱动的最小风格划分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一条可审计、无前视的 A 股历史风格诊断流水线，从 K=2 起寻找满足覆盖、赢家差异、稳定性和简约约束的最小风格数，并输出 2006 年至今逐月状态与 1990～2005 定性映射。

**Architecture:** 数据采集层只负责缓存 PIT 市值、估值、财务、行业与上市状态；`backtest.market_regime` 包负责数据审计、六族股票画像、月度收益系数、持久性聚类、blocked-fold 验证和报告。命令行入口分阶段执行，必须先冻结 2006～2020 development 的 protocol/selection manifest，才允许读取 2021 年后的 audit 结果。

**Tech Stack:** Python 3.12、NumPy、pandas、SciPy、statsmodels、PyArrow、Qlib、Tushare Pro、pytest；不新增 scikit-learn 依赖。

## Global Constraints

- 设计单一事实来源：`docs/superpowers/specs/2026-08-09-a-share-style-regime-design.md`。
- 当前参照为 B6-M；本任务是 `diagnostic_no_selection`，不得训练/晋升模型、修改 B4-S 或改变 `backtest/EXPERIMENT_STANDARD.md`。
- 定量主池为 PIT 全 A；当前成分不得倒灌历史，退市股票不得因今天不可见而删除。
- 财务字段从 `ann_date` 的下一个交易日起可用；股票特征必须早于被解释的月收益。
- K 只允许 `[2, 3, 4, 5]`；找到首个通过 C/W/S/P 的 K 后停止，更高 K 不运行。
- `lambda_multipliers=[0,0.25,0.5,1,2]`，固定种子 `[42,1000,2000,3000,4000]`，每种子 20 次初始化。
- development 为 2006-01～2020-12，audit 为 2021-01～最新完整月份；audit 不得用于修改 K、λ、阈值或特征族。
- macOS 下不得通过 heredoc/stdin 运行触发 Qlib 并行取数的代码；解释器固定 `/opt/anaconda3/envs/qlib/bin/python`，Qlib `kernels=1`。
- 网络取数只读取 `TUSHARE_TOKEN` 环境变量，token 不得写入源码、协议、缓存或日志。
- 每个任务按 TDD 执行；只提交该任务列出的文件，不带入现有 `backtest/experiments/diagnostics/` 未跟踪内容。

## File Structure

| 文件 | 单一职责 |
|---|---|
| `backtest/market_regime/protocol.py` | 冻结常量、协议 dataclass、阶段边界与 JSON 序列化 |
| `scripts/data_collector/tushare/market_regime_backfill.py` | PIT 外部字段预检、断点续传与原子 parquet 缓存 |
| `backtest/market_regime/data.py` | Qlib/外部缓存合并、可见性处理、D0 审计与 manifest |
| `backtest/market_regime/features.py` | 六族股票画像、稳健标准化、行业/规模残差化 |
| `backtest/market_regime/payoffs.py` | 月度 Ridge 风格收益系数与十分位非参数复核 |
| `backtest/market_regime/clustering.py` | 持久性聚类、动态规划分配、中心匹配与 Ward 敏感性 |
| `backtest/market_regime/evaluation.py` | expanding blocked folds、C/W/S/P 门、bootstrap、ARI/OOD |
| `backtest/market_regime/history.py` | 1990～2005 定性映射 schema、来源与置信度校验 |
| `backtest/market_regime/reporting.py` | CSV/JSON/Markdown/HTML 产物和 diagnostic registry 行 |
| `backtest/market_regime/pipeline.py` | 分阶段编排，强制 development freeze 后才可 audit |
| `backtest/scripts/analyze_a_share_style_regimes.py` | CLI 薄入口 |
| `tests/backtest/test_market_regime_*.py` | 各纯函数和端到端协议测试 |
| `tests/misc/test_market_regime_backfill.py` | 外部 API fake-client 与缓存测试 |
| `backtest/experiments/diagnostics/20260809_a_share_style_regimes/` | 冻结协议、manifest、摘要、报告和图 |

---

### Task 1: 冻结协议与类型边界

**Files:**
- Create: `backtest/market_regime/__init__.py`
- Create: `backtest/market_regime/protocol.py`
- Create: `tests/backtest/test_market_regime_protocol.py`

**Interfaces:**
- Produces: `RegimeProtocol`, `load_protocol(Path)`, `write_protocol(Path, RegimeProtocol)`；门控结果类型由 Task 7 定义。
- Consumes: 无；后续所有模块只从 `RegimeProtocol` 读取阈值，不复制常量。

- [ ] **Step 1: 写协议默认值与往返序列化失败测试**

```python
from backtest.market_regime.protocol import RegimeProtocol, load_protocol, write_protocol


def test_protocol_freezes_approved_search_space(tmp_path):
    protocol = RegimeProtocol()
    assert protocol.k_values == (2, 3, 4, 5)
    assert protocol.lambda_multipliers == (0.0, 0.25, 0.5, 1.0, 2.0)
    assert protocol.seeds == (42, 1000, 2000, 3000, 4000)
    assert protocol.development_end == "2020-12"
    assert protocol.audit_start == "2021-01"
    assert protocol.min_training_months == 60
    assert protocol.validation_months == 24

    path = tmp_path / "protocol.json"
    write_protocol(path, protocol)
    assert load_protocol(path) == protocol


def test_protocol_rejects_audit_overlap():
    with pytest.raises(ValueError, match="audit_start"):
        RegimeProtocol(development_end="2021-01", audit_start="2021-01")
```

- [ ] **Step 2: 运行测试确认模块尚不存在**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_protocol.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'backtest.market_regime'`。

- [ ] **Step 3: 实现冻结 dataclass 和原子 JSON 写入**

```python
@dataclass(frozen=True)
class RegimeProtocol:
    quantitative_start: str = "2006-01"
    development_end: str = "2020-12"
    audit_start: str = "2021-01"
    k_values: tuple[int, ...] = (2, 3, 4, 5)
    lambda_multipliers: tuple[float, ...] = (0.0, 0.25, 0.5, 1.0, 2.0)
    seeds: tuple[int, ...] = (42, 1000, 2000, 3000, 4000)
    initializations_per_seed: int = 20
    min_training_months: int = 60
    validation_months: int = 24
    min_month_coverage: float = 0.80
    max_audit_ood_fraction: float = 0.05
    min_state_fraction: float = 0.08
    min_state_months: int = 18
    min_spell_months: int = 3
    winner_ann_gap: float = 0.04
    winner_bootstrap_probability: float = 0.80
    min_bootstrap_ari: float = 0.60
    min_ward_ari: float = 0.50
    bootstrap_block_months: int = 12
```

`__post_init__` 必须检查日期不重叠、K 严格递增、所有比例落在 `[0,1]`、种子无重复。写文件时先写同目录临时文件再 `os.replace`。

- [ ] **Step 4: 运行协议测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_protocol.py -q`

Expected: PASS。

- [ ] **Step 5: 提交协议边界**

```bash
git add backtest/market_regime/__init__.py backtest/market_regime/protocol.py tests/backtest/test_market_regime_protocol.py
git commit -m "feat: define market regime diagnostic protocol"
```

### Task 2: PIT 外部数据缓存与断点续传

**Files:**
- Create: `scripts/data_collector/tushare/market_regime_backfill.py`
- Create: `tests/misc/test_market_regime_backfill.py`

**Interfaces:**
- Consumes: Tushare-compatible client injected as `pro`，Qlib 日历日期列表。
- Produces: `preflight_endpoints(pro) -> dict`, `backfill_monthly_basic(...) -> Path`, `backfill_stock_lifecycle(...) -> Path`, `backfill_financial_indicators(...) -> Path`, `backfill_industry_membership(...) -> Path`, `backfill_daily_limits(...) -> Path | None`, `backfill_st_history(...) -> Path | None`。

官方接口依据：[股票列表](https://tushare.pro/document/1?doc_id=25)、[财务指标](https://tushare.pro/document/2?doc_id=79)、[申万行业历史成分](https://tushare.pro/document/2?doc_id=335)、[每日涨跌停价格](https://tushare.pro/document/2?doc_id=183)、[历史 ST 列表](https://tushare.pro/document/2?doc_id=397)。这些接口均可能受积分、流控或历史起点限制，必须由 preflight 显式记录权限与覆盖结果。

- [ ] **Step 1: 写 fake-client 缓存测试**

```python
def test_monthly_basic_fetches_only_missing_month_ends(tmp_path):
    pro = FakePro()
    dest = tmp_path / "monthly_basic.parquet"
    backfill_monthly_basic(
        pro,
        dest,
        month_ends=["20060125", "20060228"],
        min_rows=2,
        sleep_seconds=0,
    )
    first_calls = list(pro.daily_basic_calls)
    backfill_monthly_basic(
        pro,
        dest,
        month_ends=["20060125", "20060228"],
        min_rows=2,
        sleep_seconds=0,
    )
    assert pro.daily_basic_calls == first_calls
    assert set(pd.read_parquet(dest).columns) == {
        "ts_code", "trade_date", "turnover_rate", "pe_ttm", "pb",
        "ps_ttm", "total_mv", "circ_mv"
    }


def test_financial_cache_keeps_multiple_announcements_for_same_period(tmp_path):
    path = backfill_financial_indicators(
        FakePro(), tmp_path / "financial.parquet", ["000001.SZ"], sleep_seconds=0
    )
    frame = pd.read_parquet(path)
    assert frame[["ts_code", "ann_date", "end_date"]].duplicated().sum() == 0
    assert frame["ann_date"].nunique() == 2
```

另测：API 空响应、缺字段、截面行数过少、重复 key、临时文件失败时不覆盖旧缓存、日志不包含 token；可选的涨跌停/ST 接口不可用时返回 `None` 并把原因写入 preflight，不能伪造空表为“全市场无涨停/ST”。

- [ ] **Step 2: 运行缓存测试确认失败**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest misc/test_market_regime_backfill.py -q`

Expected: FAIL with import error for `market_regime_backfill`。

- [ ] **Step 3: 实现六类缓存**

`daily_basic` 固定字段：

```python
MONTHLY_BASIC_FIELDS = (
    "ts_code,trade_date,turnover_rate,pe_ttm,pb,ps_ttm,"
    "total_mv,circ_mv"
)
FINANCIAL_FIELDS = (
    "ts_code,ann_date,end_date,roe_dt,grossprofit_margin,"
    "or_yoy,netprofit_yoy"
)
LIFECYCLE_FIELDS = "ts_code,symbol,name,market,list_date,delist_date"
LIMIT_FIELDS = "trade_date,ts_code,pre_close,up_limit,down_limit"
ST_FIELDS = "ts_code,name,trade_date,type,type_name"
```

股票生命周期分别请求 `stock_basic(list_status=L/D/P)` 后合并。财务数据按股票请求 `fina_indicator(ts_code, start_date, end_date)`；官方接口单次最多 100 行，返回恰好 100 行时按 10 年日期窗拆分重取，再以 `(ts_code, ann_date, end_date)` 去重。

行业历史使用官方 `index_member_all`：先以 `index_classify(level="L1", src="SW2021")` 取得一级行业代码，再对每个 `l1_code` 分别请求 `index_member_all(l1_code=code, is_new="Y")` 和 `is_new="N"`，合并字段 `l1_code/l1_name/ts_code/in_date/out_date`，输出统一列 `(ts_code, industry_code, industry_name, in_date, out_date, source_version="SW2021")`。若接口权限不足、任一历史月份无有效行业或同股同日多行业冲突，D0 必须 hard fail，不能回填当前行业。

涨跌停价格按交易日请求 `stk_limit(trade_date=...)`，保存 `(trade_date, ts_code, pre_close, up_limit, down_limit)`；ST 历史按交易日请求 `stock_st(trade_date=...)`，保存 `(ts_code, name, trade_date, type, type_name)`。`stock_st` 官方覆盖始于 2016-01-01，因此 2006～2015 只能在 manifest 中标记 `st_history_available=false`，不能把缺失解释为非 ST。两项均属于增强项：接口不可用不触发 D0 hard fail，但关闭对应子特征/排除规则并在最终报告显著披露。

所有缓存每次成功一批即原子落盘，支持重跑。

`preflight_endpoints` 只取一个月末和一只股票，返回每个 endpoint 的 `available/columns/error_type`；不吞掉权限不足错误。

- [ ] **Step 4: 运行缓存测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest misc/test_market_regime_backfill.py -q`

Expected: PASS。

- [ ] **Step 5: 提交缓存实现**

```bash
git add scripts/data_collector/tushare/market_regime_backfill.py tests/misc/test_market_regime_backfill.py
git commit -m "feat: cache point-in-time market regime inputs"
```

### Task 3: PIT 合并、D0 审计与数据 manifest

**Files:**
- Create: `backtest/market_regime/data.py`
- Create: `tests/backtest/test_market_regime_data.py`

**Interfaces:**
- Consumes: Qlib OHLCV frame、monthly basic、financial、lifecycle、industry parquet，以及可选的 daily-limit/ST parquet。
- Produces: `prepare_monthly_panel(...) -> MonthlyPanel`, `audit_monthly_panel(...) -> DataAuditResult`, `write_data_manifest(...) -> dict`。

- [ ] **Step 1: 写无前视与退市覆盖测试**

```python
def test_financial_value_becomes_visible_next_trading_day():
    calendar = pd.DatetimeIndex(["2020-04-29", "2020-04-30", "2020-05-06"])
    financial = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "ann_date": ["20200430"],
        "end_date": ["20191231"],
        "roe_dt": [12.0],
    })
    visible = make_financial_asof(financial, calendar)
    assert pd.isna(visible.loc[("2020-04-30", "000001.SZ"), "roe_dt"])
    assert visible.loc[("2020-05-06", "000001.SZ"), "roe_dt"] == 12.0


def test_universe_keeps_delisted_stock_before_delist_date():
    lifecycle = pd.DataFrame({
        "ts_code": ["000001.SZ"], "list_date": ["20000101"],
        "delist_date": ["20100630"],
    })
    assert "000001.SZ" in active_stocks(lifecycle, pd.Timestamp("2010-06-29"))
    assert "000001.SZ" not in active_stocks(lifecycle, pd.Timestamp("2010-07-01"))


def test_d0_rejects_month_below_complete_six_family_coverage():
    result = audit_monthly_panel(panel_with_coverage(0.79), RegimeProtocol())
    assert result.months.loc["2010-01", "six_family_pass"] is False
```

- [ ] **Step 2: 运行 D0 测试确认失败**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_data.py -q`

Expected: FAIL with missing `backtest.market_regime.data`。

- [ ] **Step 3: 实现月度面板和审计结果**

```python
@dataclass(frozen=True)
class DataAuditResult:
    first_quant_month: str | None
    last_complete_month: str | None
    months: pd.DataFrame
    six_family_months: tuple[str, ...]
    five_family_sensitivity_months: tuple[str, ...]
    hard_fail_reasons: tuple[str, ...]


@dataclass(frozen=True)
class MonthlyPanel:
    exposures: pd.DataFrame  # MultiIndex(month, instrument)，含日线派生量与可选涨停频率
    returns: pd.Series      # same index, month t return
    industry: pd.Series
    lifecycle: pd.DataFrame
```

`prepare_monthly_panel` 必须：用 Qlib 文件式脚本、`kernels=1` 分年加载；保留上市满 60 日且当月仍上市股票；用 `merge_asof(..., direction="backward")` 合并月末 basic；财务先映射到公告日下一交易日再 as-of；有 ST 缓存的日期剔除当日 ST 股票，无历史缓存的日期写 manifest 警告而非静默假定；有涨跌停缓存时以 `close >= up_limit - max(0.01, 1e-6 * up_limit)` 判定触及涨停并计算过去 60 个交易日频率，无缓存时该子特征置空并显式标记不可用。

`write_data_manifest` 记录每个输入绝对路径、SHA-256、行数、列、最早/最晚日期、API 参数和 D0 月度覆盖摘要。

- [ ] **Step 4: 运行 D0 测试与现有 PIT 聚焦测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_data.py test_pit.py -q`

Expected: PASS。

- [ ] **Step 5: 提交数据审计层**

```bash
git add backtest/market_regime/data.py tests/backtest/test_market_regime_data.py
git commit -m "feat: audit point-in-time regime data coverage"
```

### Task 4: 六族股票画像

**Files:**
- Create: `backtest/market_regime/features.py`
- Create: `tests/backtest/test_market_regime_features.py`

**Interfaces:**
- Consumes: `MonthlyPanel.exposures` 与 industry。
- Produces: `build_family_scores(panel) -> pd.DataFrame`，列严格为 `size,value,quality_growth,trend,defensive,speculative`。

- [ ] **Step 1: 写方向、缺失与残差化测试**

```python
def test_defensive_score_rewards_low_beta_low_ivol_low_drawdown():
    raw = pd.DataFrame({
        "beta60": [0.5, 1.5], "idio_vol60": [0.1, 0.4],
        "drawdown120": [-0.05, -0.40],
    }, index=["safe", "risky"])
    score = build_raw_family_scores(raw)
    assert score.loc["safe", "defensive"] > score.loc["risky", "defensive"]


def test_family_requires_half_of_subfeatures():
    raw = one_stock_raw().assign(roe_dt=np.nan, grossprofit_margin=np.nan,
                                 or_yoy=np.nan, netprofit_yoy=10.0)
    assert pd.isna(build_raw_family_scores(raw).loc[0, "quality_growth"])


def test_non_size_families_are_orthogonal_to_size_and_industry():
    scores = build_family_scores(synthetic_panel())
    for column in ["value", "quality_growth", "trend", "defensive", "speculative"]:
        assert abs(scores[column].corr(scores["size"])) < 1e-10
```

- [ ] **Step 2: 运行画像测试确认失败**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_features.py -q`

Expected: FAIL with missing feature module。

- [ ] **Step 3: 实现稳健 z-score、族合成与中性化**

```python
FAMILY_COLUMNS = (
    "size", "value", "quality_growth", "trend", "defensive", "speculative"
)


def robust_z(series: pd.Series) -> pd.Series:
    median = series.median()
    mad = (series - median).abs().median()
    if not np.isfinite(mad) or mad == 0:
        return pd.Series(0.0, index=series.index).where(series.notna())
    return ((series - median) / (1.4826 * mad)).clip(-3.0, 3.0)
```

规模为 `mean(z(log(total_mv)), z(log(circ_mv)))`；估值字段在分母非正或缺失时置空；防御字段按负 beta、负特质波动、负回撤幅度定向；投机字段按高换手、换手加速度、低价、涨停频率和特质偏度定向。`beta60` 的市场收益严格定义为 PIT 全 A 活跃股票日收益等权均值，beta 与特质波动均用过去 60 个有效交易日、至少 40 个共同观测估计，不能事后改换指数。非规模族用带截距的 least-squares 对 size 和行业 dummy 取残差，再重新 robust-z。

- [ ] **Step 4: 运行画像测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_features.py -q`

Expected: PASS。

- [ ] **Step 5: 提交股票画像模块**

```bash
git add backtest/market_regime/features.py tests/backtest/test_market_regime_features.py
git commit -m "feat: build six-family stock winner profiles"
```

### Task 5: 月度风格收益系数与非参数复核

**Files:**
- Create: `backtest/market_regime/payoffs.py`
- Create: `tests/backtest/test_market_regime_payoffs.py`

**Interfaces:**
- Consumes: 月 `t-1` 六族 scores、月 `t` 股票收益、月 `t-1` 行业。
- Produces: `MonthlyPayoffResult(coefficients, decile_spreads, sample_counts, diagnostics)`。

- [ ] **Step 1: 写已知系数与月份对齐测试**

```python
def test_monthly_ridge_recovers_known_style_payoffs():
    scores = orthogonal_six_family_scores(n=1000, seed=42)
    true_beta = pd.Series([0.01, -0.02, 0.03, 0.00, 0.015, -0.01], index=FAMILY_COLUMNS)
    returns = scores @ true_beta
    result = estimate_month_payoff(scores, returns, industry=single_industry(scores.index))
    np.testing.assert_allclose(result.coefficients[FAMILY_COLUMNS], true_beta, atol=5e-4)


def test_payoff_uses_previous_month_exposure_only():
    result = estimate_monthly_payoffs(monthly_scores(), monthly_returns())
    assert result.coefficients.loc["2020-02", "exposure_month"] == "2020-01"
    assert result.coefficients.loc["2020-02", "return_month"] == "2020-02"
```

另测：收益 1%/99% 裁剪、少于 100 只完整股票时失败、行业 dummy drop-first、alpha 固定 1.0、十分位组不少于 10 只。

- [ ] **Step 2: 运行 payoff 测试确认失败**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_payoffs.py -q`

Expected: FAIL with missing payoff module。

- [ ] **Step 3: 实现 Ridge 和 decile spread**

```python
def ridge_fit(x: np.ndarray, y: np.ndarray, alpha: float = 1.0) -> np.ndarray:
    penalty = np.eye(x.shape[1]) * alpha
    penalty[0, 0] = 0.0  # intercept 不惩罚
    return np.linalg.solve(x.T @ x + penalty, x.T @ y)


@dataclass(frozen=True)
class MonthlyPayoffResult:
    coefficients: pd.DataFrame
    decile_spreads: pd.DataFrame
    sample_counts: pd.Series
    diagnostics: dict[str, object]
```

输出 coefficients 六个方向统一，decile spread 为该方向 top decile 减 bottom decile。保存 exposure/return 月份，禁止调用者误用同期特征。

- [ ] **Step 4: 运行 payoff 测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_payoffs.py -q`

Expected: PASS。

- [ ] **Step 5: 提交 payoff 模块**

```bash
git add backtest/market_regime/payoffs.py tests/backtest/test_market_regime_payoffs.py
git commit -m "feat: estimate monthly stock-type payoffs"
```

### Task 6: 持久性聚类、中心匹配与 Ward 敏感性

**Files:**
- Create: `backtest/market_regime/clustering.py`
- Create: `tests/backtest/test_market_regime_clustering.py`

**Interfaces:**
- Consumes: expanding-standardized monthly payoff matrix、K、λ multiplier、seed。
- Produces: `PersistentClusterResult(labels, centroids, objective, lambda_value, distances)`；`match_centroids`；`ward_labels`。

- [ ] **Step 1: 写合成两状态和切换惩罚测试**

```python
def test_persistent_clustering_recovers_two_opposite_states():
    payoffs, expected = two_state_payoffs(spell=24, noise=0.02, seed=42)
    result = fit_persistent_clusters(payoffs, k=2, lambda_multiplier=0.5, seed=42, n_init=20)
    matched = relabel_to_reference(result.labels, expected)
    assert adjusted_rand_index(matched, expected) > 0.95


def test_larger_switch_penalty_never_increases_switch_count():
    payoffs = alternating_noisy_payoffs(seed=42)
    free = fit_persistent_clusters(payoffs, k=2, lambda_multiplier=0.0, seed=42, n_init=20)
    sticky = fit_persistent_clusters(payoffs, k=2, lambda_multiplier=2.0, seed=42, n_init=20)
    assert count_switches(sticky.labels) <= count_switches(free.labels)
```

另测：label permutation、空 cluster 重启、相同 seed 确定性、λ 极大时单状态路径仍受 K 非空约束、Ward 返回 K 个非空标签。

- [ ] **Step 2: 运行聚类测试确认失败**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_clustering.py -q`

Expected: FAIL with missing clustering module。

- [ ] **Step 3: 实现交替优化与动态规划**

动态规划状态转移：

```python
for t in range(1, n_months):
    for state in range(k):
        transition = cost[t - 1] + lambda_value
        transition[state] -= lambda_value
        parent[t, state] = int(np.argmin(transition))
        cost[t, state] = squared_distance[t, state] + transition[parent[t, state]]
```

以 KMeans++ 风格概率初始化中心；在“分配路径→重算中心”之间迭代至标签不变或 200 轮；空 cluster 重新放到当前最大残差月份。中心匹配使用 `scipy.optimize.linear_sum_assignment`，Ward 使用 `scipy.cluster.hierarchy.linkage/fcluster`。ARI 在本模块用组合计数实现，不引入 sklearn。

- [ ] **Step 4: 运行聚类测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_clustering.py -q`

Expected: PASS。

- [ ] **Step 5: 提交聚类模块**

```bash
git add backtest/market_regime/clustering.py tests/backtest/test_market_regime_clustering.py
git commit -m "feat: add persistent style payoff clustering"
```

### Task 7: Blocked folds、C/W/S/P 门与最小 K 选择

**Files:**
- Create: `backtest/market_regime/evaluation.py`
- Create: `tests/backtest/test_market_regime_evaluation.py`

**Interfaces:**
- Consumes: development payoff、scores/returns、protocol、`fit_persistent_clusters`。
- Produces: `CandidateEvaluation`, `SelectionResult`, `evaluate_candidate`, `select_minimum_k`, `audit_frozen_selection`。

- [ ] **Step 1: 写三类合成结论测试**

```python
def test_selector_stops_at_two_when_two_states_pass_all_gates():
    result = select_minimum_k(two_state_research_fixture(), RegimeProtocol())
    assert result.selected_k == 2
    assert result.evaluated_k == (2,)
    assert result.stop_reason == "first_k_passing_CWSP"


def test_selector_advances_to_three_when_k2_fails_winner_gate():
    result = select_minimum_k(three_state_research_fixture(), RegimeProtocol())
    assert result.selected_k == 3
    assert result.candidates[2].gates["W"].passed is False
    assert result.candidates[3].all_passed is True


def test_selector_returns_no_solution_when_k5_fails():
    result = select_minimum_k(no_stable_state_fixture(), RegimeProtocol())
    assert result.selected_k is None
    assert result.evaluated_k == (2, 3, 4, 5)
```

另测 C 门状态月份/episode/OOD，W 门两族反向/余弦/4pp/80%，S 门 spell/bootstrap ARI/Ward ARI/leave-one-family-out，audit OOD 超 5% 时否定覆盖但不重选 K。

- [ ] **Step 2: 运行门控测试确认失败**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_evaluation.py -q`

Expected: FAIL with missing evaluation module。

- [ ] **Step 3: 实现 expanding folds 与门控 dataclass**

```python
@dataclass(frozen=True)
class GateResult:
    passed: bool
    metrics: dict[str, float | int | str | None]
    failures: tuple[str, ...]


@dataclass(frozen=True)
class CandidateEvaluation:
    k: int
    lambda_multiplier: float
    gates: dict[str, GateResult]
    fold_metrics: tuple[dict[str, object], ...]
    all_passed: bool


@dataclass(frozen=True)
class SelectionResult:
    selected_k: int | None
    selected_lambda_multiplier: float | None
    evaluated_k: tuple[int, ...]
    candidates: dict[int, CandidateEvaluation]
    stop_reason: str
```

folds 从首 60 月开始，每次验证之后 24 月再扩窗。12 月 bootstrap 必须抽连续块；winner portfolio 用训练中心乘月末 scores，行业内 top-bottom 后等权汇总，不能在验证月重新挑因子。audit 只调用 `audit_frozen_selection`，不能调用 `select_minimum_k`。

- [ ] **Step 4: 运行门控测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_evaluation.py -q`

Expected: PASS。

- [ ] **Step 5: 提交评价模块**

```bash
git add backtest/market_regime/evaluation.py tests/backtest/test_market_regime_evaluation.py
git commit -m "feat: select minimum stable A-share style count"
```

### Task 8: 历史映射、报告与机器可读产物

**Files:**
- Create: `backtest/market_regime/history.py`
- Create: `backtest/market_regime/reporting.py`
- Create: `tests/backtest/test_market_regime_reporting.py`

**Interfaces:**
- Consumes: selection、audit、centroids、assignments、payoff matrix、history source rows。
- Produces: `validate_history_mapping`, `render_report_bundle(output_dir, payload)`, `build_registry_row(payload)`。

- [ ] **Step 1: 写全时期覆盖与报告一致性测试**

```python
def test_history_mapping_covers_every_month_1990_to_2005():
    rows = synthetic_complete_history_mapping(start="1990-01", end="2005-12")
    validate_history_mapping(rows, start="1990-01", end="2005-12")
    covered = expand_mapping_months(rows)
    assert covered.index.tolist() == pd.period_range("1990-01", "2005-12", freq="M").tolist()


def test_report_bundle_keeps_metrics_csv_and_html_consistent(tmp_path):
    render_report_bundle(tmp_path, minimal_report_payload())
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assignments = pd.read_csv(tmp_path / "regime_assignments.csv")
    html = (tmp_path / "report.html").read_text()
    assert metrics["selected_k"] == assignments["regime_id"].nunique()
    assert "最小风格数" in html
    assert "2019Q1 与 924" in html
```

另测：history source 缺 URL/日期/置信度时报错；2006 年后 assignments 无缺月；K 高于首个通过者时报告标记 not-run；没有 K≤5 时报告不伪造状态名称。

- [ ] **Step 2: 运行报告测试确认失败**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_reporting.py -q`

Expected: FAIL with missing history/reporting modules。

- [ ] **Step 3: 实现独立报告 bundle**

真实 `history_mapping_sources.json` 每行固定字段：

```json
{
  "start_month": "1999-05",
  "end_month": "1999-06",
  "mapped_regime_id": 0,
  "confidence": "medium",
  "evidence_summary": "政策背景下网络科技股主导的快速上涨行情",
  "sources": [{
    "title": "‘5·19’行情的启示",
    "url": "https://paper.people.com.cn/zgjjzk/html/2010-12/20/content_706143.htm?div=-1",
    "published_at": "2010-12-20"
  }]
}
```

测试内的 `synthetic_complete_history_mapping` 只验证 schema 和月度展开；真实映射在 Task 11 状态中心冻结后，通过浏览和权威来源建立，必须覆盖 1990～2005 且无重叠、无空月。报告 bundle 必须原子写入 protocol、manifest、metrics、CSV、Markdown、HTML；HTML 用项目内 CSS 和 stdlib `html`，不新增模板依赖。

- [ ] **Step 4: 运行报告测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_reporting.py -q`

Expected: PASS。

- [ ] **Step 5: 提交历史映射校验与报告层**

```bash
git add backtest/market_regime/history.py backtest/market_regime/reporting.py tests/backtest/test_market_regime_reporting.py
git commit -m "feat: report A-share style regime history"
```

### Task 9: 分阶段流水线、CLI 与 diagnostic registry 兼容

**Files:**
- Create: `backtest/market_regime/pipeline.py`
- Create: `backtest/scripts/analyze_a_share_style_regimes.py`
- Create: `tests/backtest/test_market_regime_pipeline.py`
- Modify: `backtest/scripts/build_experiment_report.py`
- Modify: `tests/backtest/test_build_experiment_report.py`

**Interfaces:**
- Consumes: 前述所有模块、协议路径、缓存目录、输出目录。
- Produces: CLI 子命令 `preflight`, `build-development`, `select`, `audit`, `report`, `all`；registry `phase=DIAGNOSTIC` 行。

- [ ] **Step 1: 写“未冻结不得打开 audit”测试**

```python
def test_audit_refuses_without_frozen_selection_manifest(tmp_path):
    with pytest.raises(RuntimeError, match="selection_manifest"):
        run_audit(protocol_path=tmp_path / "protocol.json", output_dir=tmp_path)


def test_selection_manifest_hash_locks_k_and_centroids(tmp_path):
    manifest = freeze_selection(tmp_path, synthetic_selection())
    assert manifest["selected_k"] == 2
    assert len(manifest["centroids_sha256"]) == 64
    assert manifest["development_end"] == "2020-12"


def test_standard_report_ignores_diagnostic_phase_rows(tmp_path):
    rows = [baseline_m_row(), diagnostic_regime_row()]
    html = build_html(rows)
    assert "market-regime-diagnostic" not in html
    assert "baseline/b6-m" in html
```

- [ ] **Step 2: 运行流水线测试确认失败**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_pipeline.py backtest/test_build_experiment_report.py -q`

Expected: FAIL because pipeline/diagnostic filter 尚未实现。

- [ ] **Step 3: 实现阶段状态机和薄 CLI**

```python
STAGES = ("preflight", "build-development", "select", "audit", "report")


def run_audit(protocol_path: Path, output_dir: Path) -> dict:
    selection_path = output_dir / "selection_manifest.json"
    if not selection_path.is_file():
        raise RuntimeError("selection_manifest missing; audit remains sealed")
    selection = verify_selection_manifest(selection_path, output_dir)
    return audit_frozen_selection(...)
```

`select` 只读取 <=2020-12 的 payoff；完成后写 `selection_manifest.json`，包含 protocol SHA、development payoff SHA、selected K/λ、centroid SHA 和每个已评估 K 的门结果。`audit` 验证所有 SHA 后才读取 >=2021-01 数据；若 `selected_k` 为空，audit 返回 `not_run_no_valid_k`，不得伪造中心或逐月状态。

`pipeline.py` 对外固定提供 `run_build_development`, `run_selection`, `freeze_selection`, `run_audit`, `render_final_report`，供 CLI 与端到端测试共用；CLI 不复制业务逻辑。

CLI 不包含隐式网络取数；`preflight --fetch-missing` 才调用 collector。`all` 在 D0 hard fail、无 selection 或 SHA 不一致时立即停止。

`build_experiment_report.py` 明确过滤 `_phase_of(row) == "DIAGNOSTIC"`，避免把 diagnostic 当 Phase M 空指标表；独立 report 路径保存在 registry 行。

- [ ] **Step 4: 运行流水线和报告构建测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_pipeline.py backtest/test_build_experiment_report.py -q`

Expected: PASS。

- [ ] **Step 5: 提交流水线**

```bash
git add backtest/market_regime/pipeline.py backtest/scripts/analyze_a_share_style_regimes.py tests/backtest/test_market_regime_pipeline.py backtest/scripts/build_experiment_report.py tests/backtest/test_build_experiment_report.py
git commit -m "feat: orchestrate frozen market regime diagnostic"
```

### Task 10: 聚焦回归与合成端到端验收

**Files:**
- Create: `tests/backtest/test_market_regime_end_to_end.py`
- Modify only if a failing test identifies a defect: files created in Tasks 1～9。

**Interfaces:**
- Consumes: 完整 package/CLI。
- Produces: 在临时目录生成完整 synthetic bundle，无外网、无 Qlib 数据依赖。

- [ ] **Step 1: 写合成端到端测试**

```python
def test_end_to_end_selects_smallest_k_and_seals_audit(tmp_path):
    protocol = write_synthetic_protocol(tmp_path)
    write_synthetic_pit_inputs(tmp_path, k=3, audit_ood=False)
    run_build_development(protocol, tmp_path)
    selection = run_selection(protocol, tmp_path)
    assert selection["selected_k"] == 3
    audit = run_audit(protocol, tmp_path)
    assert audit["ood_fraction"] <= 0.05
    render_final_report(protocol, tmp_path)
    assert (tmp_path / "report.html").is_file()
    assert pd.read_csv(tmp_path / "regime_assignments.csv")["month"].is_unique
```

- [ ] **Step 2: 运行端到端测试并修复真实失败**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_end_to_end.py -q`

Expected: 初次运行只允许因真实接口缺陷失败；修复后 PASS。禁止放宽 C/W/S/P 阈值让 fixture 通过，应修正 fixture 或实现。

- [ ] **Step 3: 运行全部 market-regime 聚焦测试**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_*.py misc/test_market_regime_backfill.py -q`

Expected: PASS，0 failed。

- [ ] **Step 4: 运行受影响的既有诊断/报告回归**

Run: `cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_analyze_b2s_style_regime.py backtest/test_build_experiment_report.py misc/test_csindex_hybrid_history.py -q`

Expected: PASS，0 failed。

- [ ] **Step 5: 提交端到端测试与缺陷修复**

```bash
git add tests/backtest/test_market_regime_end_to_end.py backtest/market_regime backtest/scripts/analyze_a_share_style_regimes.py scripts/data_collector/tushare/market_regime_backfill.py tests/backtest/test_market_regime_*.py tests/misc/test_market_regime_backfill.py
git commit -m "test: verify market regime diagnostic end to end"
```

### Task 11: 预登记、真实数据运行与最终研究报告

**Files:**
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/protocol.json`
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/data_manifest.json`
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/selection_manifest.json`
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/history_mapping_sources.json`
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/metrics.json`
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/regime_assignments.csv`
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/regime_centroids.csv`
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/cross_regime_payoff_matrix.csv`
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/report.md`
- Create/Update: `backtest/experiments/diagnostics/20260809_a_share_style_regimes/report.html`
- Modify: `backtest/experiments/registry.jsonl`

**Interfaces:**
- Consumes: 真实 Qlib 数据、经 D0 验证的外部 PIT 缓存、冻结代码。
- Produces: 可审计研究结论与 registry diagnostic 行；不产生模型或策略 winner。

- [ ] **Step 1: 写预登记协议并登记 planned diagnostic**

Run:

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/analyze_a_share_style_regimes.py preflight \
  --output backtest/experiments/diagnostics/20260809_a_share_style_regimes \
  --write-protocol \
  --register-planned
```

Expected: `protocol.json` 先落盘；registry 行包含 `exp_id=market-regime-diagnostic/style-payoff-min-k-v1`、`phase=DIAGNOSTIC`、`baseline_ref=B6-M`、`state=planned`、`conclusion=diagnostic_no_selection`。

- [ ] **Step 2: 运行 API 预检、补缓存和 D0**

先运行不取数预检；缺数据时再显式使用 `--fetch-missing`：

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/analyze_a_share_style_regimes.py preflight \
  --output backtest/experiments/diagnostics/20260809_a_share_style_regimes \
  --fetch-missing
```

Expected: `data_manifest.json` 写明 endpoint 权限、逐月覆盖和 D0 结果。若规模/退市链路 hard fail，停止任务并报告具体缺口；不得继续聚类。

- [ ] **Step 3: 只构建 development 并冻结最小 K 选择**

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/analyze_a_share_style_regimes.py build-development \
  --output backtest/experiments/diagnostics/20260809_a_share_style_regimes
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/analyze_a_share_style_regimes.py select \
  --output backtest/experiments/diagnostics/20260809_a_share_style_regimes
```

Expected: development payoff 截止 2020-12；`selection_manifest.json` 冻结首个通过 K 或“无 K≤5”，并保存所有已评估门结果。检查命令输出确认未读取 2021 年后的收益。

- [ ] **Step 4: 打开 audit、完成历史映射与报告**

```bash
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/analyze_a_share_style_regimes.py audit \
  --output backtest/experiments/diagnostics/20260809_a_share_style_regimes
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/analyze_a_share_style_regimes.py report \
  --output backtest/experiments/diagnostics/20260809_a_share_style_regimes \
  --register-complete
```

Expected: 若存在合格 K，2006 年后每个合格月唯一归类，1990～2005 每月有映射到冻结状态的定性标签与来源；报告包含 K 门、winner payoff matrix、2019Q1/924 对比、OOD 和 lagged matrix。若 audit OOD >5%，报告明确否定“已覆盖”而不返回重选 K。若 K≤5 全部失败，audit 标记 `not_run_no_valid_k`，报告保留完整失败门和历史资料，但不生成伪状态映射。

- [ ] **Step 5: 最终验证并提交可跟踪摘要**

Run:

```bash
cd tests && /opt/anaconda3/envs/qlib/bin/python -m pytest backtest/test_market_regime_*.py misc/test_market_regime_backfill.py backtest/test_build_experiment_report.py -q
/opt/anaconda3/envs/qlib/bin/python backtest/scripts/build_experiment_report.py
git diff --check
```

Expected: 全部 PASS；标准实验报告忽略 diagnostic phase；registry JSONL 可逐行解析；Markdown/HTML/CSV/metrics 数字一致。

提交时只加入协议、manifest、机器可读摘要、CSV、报告、小图和 registry；大 parquet 不进 Git：

```bash
git add backtest/experiments/diagnostics/20260809_a_share_style_regimes backtest/experiments/registry.jsonl backtest/experiments/report.html
git commit -m "research: classify A-share style regimes"
```

## Execution Checkpoints

1. **D0 checkpoint:** 外部 PIT 字段或退市链路不通过即暂停；这是数据阻塞，不允许代理变量绕过。
2. **Development checkpoint:** `selection_manifest.json` 完成并人工检查 SHA、K 门和“未读取 audit”证据后，才运行 audit。
3. **Audit checkpoint:** audit 只能接受或否定冻结划分；不能重新选择 K/λ。
4. **Report checkpoint:** 若 K≤5 全部失败，报告结论必须是“不存在合格简化”，不能选相对最好者。
5. **Application boundary:** 本任务结束于诊断报告；任何市场状态特征、模型路由或策略切换另立实验。
