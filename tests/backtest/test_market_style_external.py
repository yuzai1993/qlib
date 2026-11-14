import pandas as pd
from backtest.market_style import external


def test_current_vendor_probe_does_not_prove_historical_release_time(monkeypatch, tmp_path):
    class Vendor:
        def query(self, api, **kwargs):
            return pd.DataFrame({"value": [1.0]})

    monkeypatch.setattr(external, "_client", lambda: Vendor())
    days = pd.to_datetime(["2020-01-02", "2020-01-03"])
    features, audit = external.collect_optional(days, tmp_path, pd.DataFrame(index=days))
    assert features.empty
    assert audit["requests"] == 6
    assert len(audit["sources"]) == 6
    assert all(not value["historical_time_verified"] for value in audit["sources"].values())


from backtest.market_style.external import align_released, aggregate_daily_basic


def test_late_release_not_visible_at_previous_evening():
    signals = pd.to_datetime(["2020-07-30", "2020-07-31", "2020-08-03"])
    records = pd.DataFrame(
        {"available_at": pd.to_datetime(["2020-07-31 09:05", "2020-08-03 09:05"]), "margin": [100, 200]}
    )
    result = align_released(signals, records, max_age_days=10)
    assert pd.isna(result.iloc[0].margin)
    assert result.margin.iloc[1:].tolist() == [100, 200]


def test_expired_record_is_not_carried_forever():
    records = pd.DataFrame({"available_at": pd.to_datetime(["2020-07-01 11:00"]), "shibor": [2.0]})
    result = align_released(pd.to_datetime(["2020-07-02", "2020-07-20"]), records, max_age_days=7)
    assert result.iloc[0].shibor == 2
    assert pd.isna(result.iloc[1].shibor)


def test_market_valuation_excludes_nonpositive_pe():
    rows = pd.DataFrame(
        {
            "total_mv": [10, 20, 30],
            "circ_mv": [9, 18, 27],
            "pe_ttm": [-3, 10, 20],
            "pb": [1, 2, 3],
            "turnover_rate": [1, 2, 3],
            "dv_ttm": [0, 1, 2],
        }
    )
    result = aggregate_daily_basic(rows)
    assert result["pe_ttm_median"] == 15
    assert result["pe_nonpositive_share"] == 1 / 3
