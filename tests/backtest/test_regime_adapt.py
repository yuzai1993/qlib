"""Tests for the regime-adapt engineering pieces (eval extension / weights / features)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.features.regime import broadcast_day_features
from backtest.models.rankic_early_stop import load_valid_dates
from backtest.models.regime_adapt import (
    RegimeSingleLGBMModel,
    RegimeWeightedDEnsembleModel,
    compact_sample_reweight,
    compose_day_weights,
    load_day_weights,
    pack_loss_curve_edges,
    top3_h5_net_ann,
    top5_h5_net_ann,
)
from backtest.scripts.eval_ic_multi_pool import (
    EVAL_LABEL_EXPR,
    HEAD_H_CORE,
    HEAD_K_CORE,
    _horizon_label_expr,
    _mean_over_horizons,
    _north_star_ir,
    appraisal,
    daily_head_panel,
    hac_vol,
    daily_topk_excess,
    day_regime_map,
    grid_mean_ir,
    hac_ir,
    load_date_list,
    load_regime_monthly,
    summarize_head_series,
    topk_turnover,
)


def _panel_index(dates, insts):
    return pd.MultiIndex.from_product(
        [pd.to_datetime(dates), insts], names=["datetime", "instrument"]
    )


def test_h1_label_expr_matches_legacy_protocol_exactly():
    """h1 与历史统一评测标签逐字符一致，保证多期限入口的 h1 可对拍。"""
    assert _horizon_label_expr(1) == EVAL_LABEL_EXPR


def test_horizon_label_expr_h40_matches_training_label_form():
    assert _horizon_label_expr(40) == "Ref($close, -41)/Ref($close, -1) - 1"


def test_daily_topk_excess_math():
    idx = _panel_index(["2024-01-02"], list("ABCDE"))
    pred = pd.Series([5, 4, 3, 2, 1], index=idx, dtype=float)
    label = pd.Series([0.10, 0.02, 0.0, 0.0, -0.02], index=idx)

    out = daily_topk_excess(pred, label, 2, min_count=5)

    expected = (0.10 + 0.02) / 2 - label.mean()
    assert out.loc[pd.Timestamp("2024-01-02")] == pytest.approx(expected)


def test_daily_topk_excess_skips_thin_days():
    idx = _panel_index(["2024-01-02"], list("ABC"))
    pred = pd.Series([3, 2, 1], index=idx, dtype=float)
    label = pd.Series([0.1, 0.0, -0.1], index=idx)

    assert daily_topk_excess(pred, label, 2, min_count=5).empty


def test_hac_ir_h1_matches_ordinary_ir():
    e = pd.Series(np.linspace(-0.01, 0.02, 40))
    ordinary = e.mean() / e.std(ddof=0) * np.sqrt(238)
    # hac_ir uses 1/n variance (= ddof=0)
    assert hac_ir(e, 1) == pytest.approx(float(ordinary), rel=1e-6)


def test_hac_ir_returns_none_when_too_short():
    assert hac_ir(pd.Series([0.01] * 10), 1) is None
    # h=10 → lag=9, min_n = max(20, 28) = 28
    assert hac_ir(pd.Series(np.arange(25, dtype=float) / 100), 10) is None


def test_hac_ir_h5_penalizes_perfect_overlap():
    # constant series → var=0 → None
    assert hac_ir(pd.Series([0.01] * 40), 5) is None
    e = pd.Series(np.sin(np.linspace(0, 8 * np.pi, 80)))
    ir1 = hac_ir(e, 1)
    ir5 = hac_ir(e, 5)
    assert ir1 is not None and ir5 is not None
    # overlapping 5-day-like persistence should not inflate IR vs h=1 scale blindly
    assert np.isfinite(ir5)


def test_daily_head_panel_reports_excess_and_selected_names():
    idx = _panel_index(["2024-01-02"], list("ABCDE"))
    pred = pd.Series([5, 4, 3, 2, 1], index=idx, dtype=float)
    label = pd.Series([0.10, 0.02, 0.0, 0.0, -0.02], index=idx)

    out = daily_head_panel(pred, label, [2], min_count=5)

    assert out[2]["excess"].iloc[0] == pytest.approx((0.10 + 0.02) / 2 - label.mean())
    assert out[2]["sets"][pd.Timestamp("2024-01-02")] == frozenset({"A", "B"})


def test_daily_head_panel_drops_untradable_from_picks_and_benchmark():
    """次日涨停/停牌样本必须同时退出 top-k 候选池和等权基准，否则超额不可实现。"""
    idx = _panel_index(["2024-01-02"], list("ABCDE"))
    pred = pd.Series([5, 4, 3, 2, 1], index=idx, dtype=float)
    label = pd.Series([0.10, 0.02, 0.0, 0.0, -0.02], index=idx)
    # A 次日涨停买不到
    tradable = pd.Series([False, True, True, True, True], index=idx)

    out = daily_head_panel(pred, label, [2], min_count=4, tradable=tradable)

    assert out[2]["sets"][pd.Timestamp("2024-01-02")] == frozenset({"B", "C"})
    expected = (0.02 + 0.0) / 2 - label.loc[(slice(None), list("BCDE"))].mean()
    assert out[2]["excess"].iloc[0] == pytest.approx(expected)


def test_topk_turnover_counts_one_way_replacement_at_lag():
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    sets = {
        dates[0]: frozenset({"A", "B"}),
        dates[1]: frozenset({"A", "C"}),
        dates[2]: frozenset({"A", "B"}),
        dates[3]: frozenset({"A", "C"}),
    }
    # lag=1: 每步换掉 1/2
    assert topk_turnover(sets, 2, 1) == pytest.approx(0.5)
    # lag=2: 持仓完全复原 → 0 换手
    assert topk_turnover(sets, 2, 2) == pytest.approx(0.0)
    assert topk_turnover(sets, 2, 9) is None


def test_topk_turnover_days_filter_uses_full_calendar_for_lag():
    """风格切片只筛调仓日，不能把跨块的一对当成整组换仓。"""
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    sets = {
        dates[0]: frozenset({"A", "B"}),
        dates[1]: frozenset({"A", "B"}),
        dates[2]: frozenset({"X", "Y"}),  # 中间是另一风格块
        dates[3]: frozenset({"A", "B"}),
        dates[4]: frozenset({"A", "B"}),
    }
    # 只统计 dates[3]/dates[4]：与各自前一日（dates[2]/dates[3]）比
    got = topk_turnover(sets, 2, 1, days={dates[3], dates[4]})
    assert got == pytest.approx((1.0 + 0.0) / 2)
    # 若错误地先按 days 过滤日历，dates[3] 会与 dates[4] 相比，得 0.0
    assert got != pytest.approx(0.0)


def test_grid_mean_defaults_to_new_core_and_skips_extra_cells():
    head = {
        str(k): {str(h): {"net_sharpe": float(k + h)} for h in HEAD_H_CORE}
        for k in HEAD_K_CORE
    }
    head["5"]["40"] = {"net_sharpe": 999.0}
    expected = np.mean([k + h for k in HEAD_K_CORE for h in HEAD_H_CORE])
    assert _north_star_ir(head) == pytest.approx(expected)


def test_grid_mean_ir_supports_horizon_subsets():
    head = {
        str(k): {str(h): {"net_sharpe": float(k + h)} for h in HEAD_H_CORE}
        for k in HEAD_K_CORE
    }
    only_h5 = [5]
    assert grid_mean_ir(head, HEAD_K_CORE, only_h5) == pytest.approx(
        np.mean([k + 5 for k in HEAD_K_CORE])
    )


def test_appraisal_fits_beta_instead_of_hard_subtracting_benchmark():
    """组合 = 0.5×基准 + 固定 alpha 时，appraisal 应还原 beta=0.5 且残差无基准波动。"""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-02", periods=250, freq="B")
    bench = pd.Series(rng.normal(0, 0.02, len(dates)), index=dates)
    port = 0.5 * bench + 0.001

    got = appraisal(port, bench, 1)

    assert got["beta"] == pytest.approx(0.5, rel=1e-9)
    assert got["ann_alpha"] == pytest.approx(0.001 * 238, rel=1e-9)
    # 残差是常数 → 方差 0 → IR 无定义；关键是它不含基准波动
    assert got["appraisal_ir"] is None


def test_appraisal_ir_beats_hard_subtraction_for_low_beta_portfolio():
    """低 beta 组合被硬减基准时会被注入基准波动、IR 被压低；appraisal 不受影响。"""
    rng = np.random.default_rng(1)
    dates = pd.date_range("2024-01-02", periods=500, freq="B")
    bench = pd.Series(rng.normal(0, 0.02, len(dates)), index=dates)
    noise = pd.Series(rng.normal(0, 0.002, len(dates)), index=dates)
    port = 0.3 * bench + 0.0005 + noise

    appr = appraisal(port, bench, 1)
    hard = hac_ir(port - bench, 1)

    assert appr["beta"] == pytest.approx(0.3, abs=0.05)
    assert appr["appraisal_ir"] > hard


def test_daily_head_panel_exposes_port_and_bench_for_appraisal():
    idx = _panel_index(["2024-01-02"], list("ABCDE"))
    pred = pd.Series([5, 4, 3, 2, 1], index=idx, dtype=float)
    label = pd.Series([0.10, 0.02, 0.0, 0.0, -0.02], index=idx)

    out = daily_head_panel(pred, label, [2], min_count=5)[2]

    assert out["port"].iloc[0] == pytest.approx((0.10 + 0.02) / 2)
    assert out["bench"].iloc[0] == pytest.approx(label.mean())
    assert out["excess"].iloc[0] == pytest.approx(out["port"].iloc[0] - out["bench"].iloc[0])


def test_summarize_head_series_scales_ann_by_horizon_and_nets_cost():
    dates = pd.date_range("2024-01-02", periods=40, freq="B")
    e = pd.Series([0.01] * 40, index=dates)

    h1 = summarize_head_series(e, 1)
    h5 = summarize_head_series(e, 5)
    assert h1["ann_excess"] == pytest.approx(0.01 * 238)
    assert h5["ann_excess"] == pytest.approx(0.01 * 238 / 5)
    assert "turnover" not in h1

    # 全换手：每日持仓完全不重叠 → 日换手 = period/h；h1 年化成本 = 238 × 1 × 0.00092
    sets = {d: frozenset({f"S{i}"}) for i, d in enumerate(dates)}
    with_cost = summarize_head_series(e, 1, sets=sets, k=1)
    assert with_cost["turnover_period"] == pytest.approx(1.0)
    assert with_cost["turnover"] == pytest.approx(1.0)
    assert with_cost["net_ann_excess"] == pytest.approx(0.01 * 238 - 238 * 0.00092)
    h5_cost = summarize_head_series(e, 5, sets=sets, k=1)
    assert h5_cost["turnover_period"] == pytest.approx(1.0)
    assert h5_cost["turnover"] == pytest.approx(0.2)
    # 常数超额的 HAC 方差为 0 → 波动/夏普无定义
    assert with_cost.get("net_ann_vol") is None
    assert with_cost.get("net_sharpe") is None


def test_summarize_head_series_net_sharpe_is_net_ann_over_hac_vol():
    rng = np.random.default_rng(3)
    dates = pd.date_range("2024-01-02", periods=80, freq="B")
    e = pd.Series(0.002 + rng.normal(0, 0.01, len(dates)), index=dates)
    sets = {d: frozenset({f"A{i % 3}", f"B{i % 5}"}) for i, d in enumerate(dates)}

    out = summarize_head_series(e, 1, sets=sets, k=2)
    vol = hac_vol(e, 1)
    assert out["net_ann_vol"] == pytest.approx(vol)
    assert out["net_sharpe"] == pytest.approx(out["net_ann_excess"] / vol)


def test_summarize_head_series_official_ann_uses_port_not_excess():
    """官方年化/波动/夏普用头部绝对收益；超额列只留审计。"""
    dates = pd.date_range("2024-01-02", periods=80, freq="B")
    rng = np.random.default_rng(4)
    port = pd.Series(0.004 + rng.normal(0, 0.01, len(dates)), index=dates)
    bench = pd.Series(0.001 + rng.normal(0, 0.008, len(dates)), index=dates)
    excess = port - bench
    sets = {d: frozenset({f"S{i % 2}"}) for i, d in enumerate(dates)}

    out = summarize_head_series(excess, 1, sets=sets, k=1, port=port, bench=bench)

    assert out["ann"] == pytest.approx(float(port.mean() * 238))
    assert out["ann_excess"] == pytest.approx(float(excess.mean() * 238))
    assert out["net_ann"] == pytest.approx(out["ann"] - out["ann_cost"])
    assert out["net_ann_excess"] == pytest.approx(out["ann_excess"] - out["ann_cost"])
    vol = hac_vol(port, 1)
    assert out["net_ann_vol"] == pytest.approx(vol)
    assert out["net_sharpe"] == pytest.approx(out["net_ann"] / vol)


def test_summarize_head_series_adds_appraisal_when_port_bench_given():
    rng = np.random.default_rng(2)
    dates = pd.date_range("2024-01-02", periods=300, freq="B")
    bench = pd.Series(rng.normal(0, 0.02, len(dates)), index=dates)
    port = 0.6 * bench + 0.001 + pd.Series(rng.normal(0, 0.003, len(dates)), index=dates)

    out = summarize_head_series(port - bench, 1, port=port, bench=bench)

    assert out["beta"] == pytest.approx(0.6, abs=0.05)
    assert out["appraisal_ir"] is not None
    # 硬减基准口径同时保留，供跨口径对照
    assert out["ir"] is not None and out["appraisal_ir"] > out["ir"]


def test_day_regime_map_uses_month_labels():
    monthly = pd.Series(
        ["D", "F"],
        index=pd.to_datetime(["2024-08-31", "2024-09-30"]),
    )
    days = pd.DatetimeIndex(["2024-08-05", "2024-09-24", "2024-09-30"])

    out = day_regime_map(days, monthly)

    assert list(out) == ["D", "F", "F"]


def test_mean_over_horizons_is_equal_weighted():
    out = _mean_over_horizons(
        {
            "h1": {"rank_ic_mean": 0.02, "rank_icir": 0.2},
            "h5": {"rank_ic_mean": 0.04, "rank_icir": 0.4},
        }
    )
    assert out["rank_ic_mean"] == pytest.approx(0.03)
    assert out["rank_icir"] == pytest.approx(0.3)
    assert out["n_horizons"] == 2


def test_load_date_list_and_regime_monthly_skip_comment_header(tmp_path):
    date_csv = tmp_path / "dates.csv"
    date_csv.write_text("# frozen 2026-08-09 | seed=42\ndate,regime\n2024-09-24,F\n2024-09-24,F\n2020-08-03,D\n")
    dates = load_date_list(date_csv)
    assert list(dates) == list(pd.to_datetime(["2020-08-03", "2024-09-24"]))

    monthly_csv = tmp_path / "monthly.csv"
    monthly_csv.write_text("datetime,regime3\n2024-09-30,F\n2024-08-31,D\n")
    monthly = load_regime_monthly(monthly_csv)
    assert monthly.loc[pd.Timestamp("2024-09-30")] == "F"


def test_load_valid_dates_rejects_out_of_segment(tmp_path):
    csv = tmp_path / "valid.csv"
    csv.write_text("date\n2020-08-03\n2027-01-04\n")
    with pytest.raises(ValueError, match="inside the protocol valid segment"):
        load_valid_dates(str(csv), ("2020-08-03", "2026-07-31"))


def test_load_valid_dates_ok(tmp_path):
    csv = tmp_path / "valid.csv"
    csv.write_text("# header\ndate,regime\n2024-09-24,F\n2020-08-03,D\n")
    dates = load_valid_dates(str(csv), ("2020-08-03", "2026-07-31"))
    assert list(dates) == list(pd.to_datetime(["2020-08-03", "2024-09-24"]))


def test_compose_day_weights_multiplies_by_sample_date():
    idx = _panel_index(["2016-01-04", "2016-01-05"], ["A", "B"])
    base = pd.Series(np.array([1.0, 2.0, 1.0, 1.0]))
    day_w = pd.Series(
        [2.0, 3.0], index=pd.to_datetime(["2016-01-04", "2016-01-05"])
    )

    out = compose_day_weights(idx, base, day_w)

    assert list(out) == pytest.approx([2.0, 4.0, 3.0, 3.0])
    assert (out.index == base.index).all()


def test_compose_day_weights_requires_full_date_coverage():
    idx = _panel_index(["2016-01-04", "2016-01-05"], ["A"])
    base = pd.Series(np.ones(2))
    day_w = pd.Series([2.0], index=pd.to_datetime(["2016-01-04"]))

    with pytest.raises(ValueError, match="missing 1 training dates"):
        compose_day_weights(idx, base, day_w)


def test_load_day_weights_rejects_nonpositive(tmp_path):
    csv = tmp_path / "w.csv"
    csv.write_text("date,weight\n2016-01-04,0.0\n")
    with pytest.raises(ValueError, match="positive finite"):
        load_day_weights(str(csv))


def test_broadcast_day_features_multiindex_columns():
    idx = _panel_index(["2024-09-23", "2024-09-24"], ["A", "B"])
    df = pd.DataFrame(
        {("feature", "F0"): np.arange(4, dtype=float)}, index=idx
    )
    day = pd.DataFrame(
        {"amount_surge": [1.1, 2.2], "basis_pct": [-0.01, 0.03]},
        index=pd.to_datetime(["2024-09-23", "2024-09-24"]),
    )

    out = broadcast_day_features(df, day, ["amount_surge", "basis_pct"])

    assert list(out[("feature", "REGIME_amount_surge")]) == pytest.approx([1.1, 1.1, 2.2, 2.2])
    assert list(out[("feature", "REGIME_basis_pct")]) == pytest.approx([-0.01, -0.01, 0.03, 0.03])


def test_broadcast_day_features_missing_column_raises():
    idx = _panel_index(["2024-09-23"], ["A"])
    df = pd.DataFrame({("feature", "F0"): [0.0]}, index=idx)
    day = pd.DataFrame({"x": [1.0]}, index=pd.to_datetime(["2024-09-23"]))

    with pytest.raises(ValueError, match="missing columns"):
        broadcast_day_features(df, day, ["amount_surge"])


def test_top3_h5_net_ann_matches_summarize_head_series():
    dates = pd.date_range("2024-01-02", periods=40, freq="B")
    insts = [f"S{i:02d}" for i in range(25)]
    idx = _panel_index(dates, insts)
    rank = {c: 25 - i for i, c in enumerate(insts)}
    pred = pd.Series([rank[i] for i in idx.get_level_values("instrument")], index=idx)
    lab = {c: 0.05 if int(c[1:]) < 3 else 0.0 for c in insts}
    label = pd.Series([lab[i] for i in idx.get_level_values("instrument")], index=idx)

    score = top3_h5_net_ann(pred.to_numpy(), label.to_numpy(), idx)

    panel = daily_head_panel(pred, label, [3])[3]
    expected = summarize_head_series(
        panel["excess"],
        5,
        sets=panel["sets"],
        k=3,
        port=panel["port"],
        bench=panel["bench"],
    )["net_ann"]
    assert score == pytest.approx(expected)


def test_top5_h5_net_ann_matches_summarize_head_series():
    dates = pd.date_range("2024-01-02", periods=40, freq="B")
    insts = [f"S{i:02d}" for i in range(25)]
    idx = _panel_index(dates, insts)
    rank = {c: 25 - i for i, c in enumerate(insts)}
    pred = pd.Series([rank[i] for i in idx.get_level_values("instrument")], index=idx)
    lab = {c: 0.05 if int(c[1:]) < 5 else 0.0 for c in insts}
    label = pd.Series([lab[i] for i in idx.get_level_values("instrument")], index=idx)

    score = top5_h5_net_ann(pred.to_numpy(), label.to_numpy(), idx)

    panel = daily_head_panel(pred, label, [5])[5]
    expected = summarize_head_series(
        panel["excess"],
        5,
        sets=panel["sets"],
        k=5,
        port=panel["port"],
        bench=panel["bench"],
    )["net_ann"]
    assert score == pytest.approx(expected)


def test_top5_h5_net_ann_drops_untradable_from_picks_and_benchmark():
    dates = pd.date_range("2024-01-02", periods=40, freq="B")
    insts = [f"S{i:02d}" for i in range(25)]
    idx = _panel_index(dates, insts)
    rank = {c: 25 - i for i, c in enumerate(insts)}
    pred = pd.Series([rank[i] for i in idx.get_level_values("instrument")], index=idx)
    lab = {c: 0.10 if c == "S00" else 0.01 for c in insts}
    label = pd.Series([lab[i] for i in idx.get_level_values("instrument")], index=idx)
    tradable = pd.Series(idx.get_level_values("instrument") != "S00", index=idx)

    with_a = top5_h5_net_ann(pred.to_numpy(), label.to_numpy(), idx)
    without_a = top5_h5_net_ann(pred.to_numpy(), label.to_numpy(), idx, tradable=tradable)
    assert without_a != pytest.approx(with_a)

    panel = daily_head_panel(pred, label, [5], tradable=tradable)[5]
    expected = summarize_head_series(
        panel["excess"],
        5,
        sets=panel["sets"],
        k=5,
        port=panel["port"],
        bench=panel["bench"],
    )["net_ann"]
    assert without_a == pytest.approx(expected)
    first_set = next(iter(panel["sets"].values()))
    assert "S00" not in first_set


def test_regime_single_default_es_metric_is_rankic():
    model = RegimeSingleLGBMModel(early_stopping_rounds=5, num_boost_round=8)
    assert model.es_metric == "daily_rank_ic"


def test_regime_single_rejects_unknown_es_metric():
    with pytest.raises(ValueError, match="es_metric"):
        RegimeSingleLGBMModel(es_metric="sharpe", early_stopping_rounds=5)


def _tiny_lgbm_frame(seed: int, days, n_inst: int = 20, n_feat: int = 4):
    insts = [f"S{i:02d}" for i in range(n_inst)]
    idx = _panel_index(days, insts)
    rng = np.random.default_rng(seed)
    feat = rng.normal(size=(len(idx), n_feat))
    label = feat[:, 0] * 0.1 + rng.normal(scale=0.02, size=len(idx))
    cols = pd.MultiIndex.from_tuples(
        [("feature", f"F{i}") for i in range(n_feat)] + [("label", "LABEL0")]
    )
    return pd.DataFrame(np.column_stack([feat, label]), index=idx, columns=cols)


def test_fit_prepared_records_top3_h5_net_ann():
    train = _tiny_lgbm_frame(0, pd.date_range("2019-01-02", periods=30, freq="B"))
    valid = _tiny_lgbm_frame(1, pd.date_range("2020-01-02", periods=40, freq="B"))
    model = RegimeSingleLGBMModel(
        es_metric="top3_h5_net_ann",
        early_stopping_rounds=5,
        num_boost_round=12,
        seed=42,
        num_leaves=8,
        min_data_in_leaf=5,
        num_threads=1,
    )
    model.fit_prepared(train, valid)
    rec = model.rankic_evals_result[0]
    assert rec["es_metric"] == "top3_h5_net_ann"
    assert rec["best_iteration"] >= 1
    assert np.isfinite(rec["best_score"])


def test_fit_prepared_records_top5_h5_net_ann():
    train = _tiny_lgbm_frame(0, pd.date_range("2019-01-02", periods=30, freq="B"))
    valid = _tiny_lgbm_frame(1, pd.date_range("2020-01-02", periods=40, freq="B"))
    model = RegimeSingleLGBMModel(
        es_metric="top5_h5_net_ann",
        early_stopping_rounds=5,
        num_boost_round=12,
        seed=42,
        num_leaves=8,
        min_data_in_leaf=5,
        num_threads=1,
    )
    model.fit_prepared(train, valid)
    rec = model.rankic_evals_result[0]
    assert rec["es_metric"] == "top5_h5_net_ann"
    assert rec["best_iteration"] >= 1
    assert np.isfinite(rec["best_score"])


def test_fit_prepared_default_still_records_daily_rank_ic():
    train = _tiny_lgbm_frame(0, pd.date_range("2019-01-02", periods=20, freq="B"))
    valid = _tiny_lgbm_frame(1, pd.date_range("2020-01-02", periods=20, freq="B"))
    model = RegimeSingleLGBMModel(
        early_stopping_rounds=5,
        num_boost_round=10,
        seed=42,
        num_leaves=8,
        min_data_in_leaf=5,
        num_threads=1,
    )
    model.fit_prepared(train, valid)
    rec = model.rankic_evals_result[0]
    assert rec.get("es_metric", "daily_rank_ic") == "daily_rank_ic"
    assert "best_score" in rec


def _tiny_densemble(**kwargs):
    defaults = dict(
        protocol_id="regime-adapt-v1",
        early_stopping_rounds=5,
        epochs=8,
        num_models=1,
        enable_sr=False,
        enable_fs=False,
        bins_fs=5,
        sample_ratios=[0.8, 0.7, 0.6, 0.5, 0.4],
        sub_weights=[1],
        seed=42,
        num_leaves=8,
        min_data_in_leaf=5,
        num_threads=1,
    )
    defaults.update(kwargs)
    return RegimeWeightedDEnsembleModel(**defaults)


def test_regime_densemble_default_es_metric_is_rankic():
    model = _tiny_densemble()
    assert model.es_metric == "daily_rank_ic"


def test_regime_densemble_rejects_unknown_es_metric():
    with pytest.raises(ValueError, match="es_metric"):
        _tiny_densemble(es_metric="sharpe")


def test_densemble_fit_prepared_records_top3_h5_net_ann():
    train = _tiny_lgbm_frame(0, pd.date_range("2019-01-02", periods=30, freq="B"))
    valid = _tiny_lgbm_frame(1, pd.date_range("2020-01-02", periods=40, freq="B"))
    model = _tiny_densemble(es_metric="top3_h5_net_ann")
    model.fit_prepared(train, valid)
    rec = model.rankic_evals_result[0]
    assert rec["es_metric"] == "top3_h5_net_ann"
    assert rec["best_iteration"] >= 1
    assert np.isfinite(rec["best_score"])


def test_compact_sample_reweight_matches_official_sr():
    from qlib.contrib.model.double_ensemble import DEnsembleModel

    rng = np.random.default_rng(0)
    curve = pd.DataFrame(rng.normal(size=(180, 30)))
    values = pd.Series(rng.random(180))
    dummy = DEnsembleModel.__new__(DEnsembleModel)
    dummy.alpha1 = 1
    dummy.alpha2 = 1
    dummy.bins_sr = 10
    dummy.decay = 0.5
    official = DEnsembleModel.sample_reweight(dummy, curve, values, 2)
    packed, part = pack_loss_curve_edges(curve.to_numpy())
    compact = compact_sample_reweight(
        packed,
        values,
        2,
        part=part,
        alpha1=1,
        alpha2=1,
        bins_sr=10,
        decay=0.5,
    )
    np.testing.assert_allclose(official.to_numpy(), compact.to_numpy(), rtol=1e-10, atol=1e-10)
