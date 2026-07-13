"""TopkDropoutTimingStrategy（均线择时动态仓位）单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_loader as cl  # noqa: E402

from qlib.contrib.strategy.dynamic_position import (  # noqa: E402
    TopkDropoutTimingStrategy,
    compute_risk_degree_series,
    lookup_risk_degree,
)

CONFIGS = ROOT / "backtest" / "configs"
BASE_YAML = CONFIGS / "csi300_lgbm_train_start_2016.yaml"


def _close(values) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


class TestComputeRiskDegreeSeries:
    def test_warmup_is_neutral(self):
        close = _close(np.linspace(100, 200, 100))
        risk = compute_risk_degree_series(close, fast_window=20, slow_window=60)
        # 慢均线未形成前（前 59 个点）一律中性
        assert (risk.iloc[:59] == 0.70).all()

    def test_uptrend_is_bull(self):
        close = _close(np.linspace(100, 200, 100))
        risk = compute_risk_degree_series(close, fast_window=20, slow_window=60)
        # 单调上涨：close > MA20 > MA60
        assert (risk.iloc[60:] == 0.95).all()

    def test_downtrend_is_bear(self):
        close = _close(np.linspace(200, 100, 100))
        risk = compute_risk_degree_series(close, fast_window=20, slow_window=60)
        assert (risk.iloc[60:] == 0.40).all()

    def test_flat_is_neutral(self):
        close = _close(np.full(100, 100.0))
        risk = compute_risk_degree_series(close, fast_window=20, slow_window=60)
        assert (risk == 0.70).all()

    def test_custom_risk_levels(self):
        close = _close(np.linspace(100, 200, 100))
        risk = compute_risk_degree_series(
            close, fast_window=5, slow_window=10, risk_bull=1.0, risk_neutral=0.5, risk_bear=0.1
        )
        assert risk.iloc[-1] == 1.0
        assert risk.iloc[0] == 0.5

    def test_index_preserved(self):
        close = _close(np.linspace(100, 200, 30))
        risk = compute_risk_degree_series(close)
        assert risk.index.equals(close.index)


class TestLookupRiskDegree:
    def test_exact_date(self):
        s = pd.Series([0.5, 0.9], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
        assert lookup_risk_degree(s, pd.Timestamp("2024-01-03"), default=0.7) == 0.9

    def test_uses_latest_available(self):
        s = pd.Series([0.5, 0.9], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
        # 2024-01-05 无值 → 用 01-03
        assert lookup_risk_degree(s, pd.Timestamp("2024-01-05"), default=0.7) == 0.9

    def test_before_first_date_falls_back(self):
        s = pd.Series([0.5], index=pd.to_datetime(["2024-01-02"]))
        assert lookup_risk_degree(s, pd.Timestamp("2023-12-29"), default=0.7) == 0.7

    def test_none_or_empty_falls_back(self):
        assert lookup_risk_degree(None, pd.Timestamp("2024-01-02"), default=0.7) == 0.7
        empty = pd.Series(dtype=float)
        assert lookup_risk_degree(empty, pd.Timestamp("2024-01-02"), default=0.7) == 0.7


class TestStrategyRiskForDate:
    def _make(self, **kwargs) -> TopkDropoutTimingStrategy:
        signal = pd.Series(
            [0.1],
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2024-01-02"), "SH600000")], names=["datetime", "instrument"]
            ),
        )
        return TopkDropoutTimingStrategy(topk=5, n_drop=1, signal=signal, **kwargs)

    def test_injected_risk_series(self):
        risk = pd.Series([0.4, 0.95], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
        st = self._make(risk_series=risk)
        assert st._risk_for_date(pd.Timestamp("2024-01-02")) == 0.4
        assert st._risk_for_date(pd.Timestamp("2024-01-04")) == 0.95

    def test_fallback_to_static_risk_degree(self):
        risk = pd.Series([0.4], index=pd.to_datetime(["2024-06-03"]))
        st = self._make(risk_series=risk, risk_degree=0.8)
        # 早于序列首日 → 回退到静态 risk_degree
        assert st._risk_for_date(pd.Timestamp("2024-01-02")) == 0.8

    def test_default_params(self):
        st = self._make(risk_series=pd.Series(dtype=float))
        assert st.timing_benchmark == "SH000300"
        assert st.fast_window == 20
        assert st.slow_window == 60
        assert st.rebalance_tolerance == pytest.approx(0.02)


class TestConfigLoaderStrategyKwargs:
    def _cfg(self):
        raw = yaml.safe_load(BASE_YAML.read_text(encoding="utf-8"))
        raw["strategy"]["class"] = "TopkDropoutTimingStrategy"
        raw["strategy"]["module_path"] = "qlib.contrib.strategy.dynamic_position"
        raw["strategy"]["kwargs"] = {
            "timing_benchmark": "SH000300",
            "fast_window": 10,
            "risk_bear": 0.3,
        }
        return cl.align_dates_from_segments(cl.validate_run_section(raw))

    def test_kwargs_passthrough(self):
        pac = cl.build_port_analysis_config(self._cfg())
        kw = pac["strategy"]["kwargs"]
        assert kw["topk"] == 20
        assert kw["n_drop"] == 5
        assert kw["fast_window"] == 10
        assert kw["risk_bear"] == 0.3
        assert pac["strategy"]["module_path"] == "qlib.contrib.strategy.dynamic_position"

    def test_no_kwargs_still_works(self):
        raw = yaml.safe_load(BASE_YAML.read_text(encoding="utf-8"))
        cfg = cl.align_dates_from_segments(cl.validate_run_section(raw))
        pac = cl.build_port_analysis_config(cfg)
        assert pac["strategy"]["kwargs"] == {"topk": 20, "n_drop": 5}
