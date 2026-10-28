import pandas as pd
import pytest

from backtest.scripts.run_bt_v5_cutover_diagnostics import (
    combine_frozen_scores,
    validate_frozen_config,
)


def scores(dates, values):
    return pd.DataFrame(
        {"score": values},
        index=pd.MultiIndex.from_arrays(
            [pd.to_datetime(dates), ["SH600000"] * len(dates)],
            names=["datetime", "instrument"],
        ),
    )


def test_frozen_history_wins_and_forward_cannot_replace_selection_window():
    old = scores(["2026-07-30", "2026-07-31", "2026-08-03"], [1, 2, 999])
    new = scores(["2026-07-31", "2026-08-03"], [-99, 3])
    result = combine_frozen_scores(old, new)
    assert result.score.tolist() == [1, 2, 3]
    assert not result.index.has_duplicates


def test_missing_boundary_signal_is_rejected():
    with pytest.raises(ValueError, match="2026-07-31"):
        combine_frozen_scores(scores(["2026-07-30"], [1]), scores(["2026-08-03"], [2]))


def test_diagnostic_rejects_tuned_strategy_before_running():
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load(
        (root / "backtest/configs/alla_v2_top5d1f100_parity.yaml").read_text()
    )
    validate_frozen_config(config)
    config["strategy"]["kwargs"]["force_sell_rank"] = 50
    with pytest.raises(ValueError, match="frozen"):
        validate_frozen_config(config)


def test_registered_metrics_recompute_from_durable_daily_returns():
    import hashlib
    import json
    from pathlib import Path
    from backtest.scripts.strategy_stability_metrics import summarize_period

    root = Path(__file__).resolve().parents[2]
    directory = root / "backtest/experiments/bt_v5_cutover_20261029"
    summary = json.loads((directory / "summary.json").read_text())
    daily = directory / "portfolio_daily.csv"
    assert hashlib.sha256(daily.read_bytes()).hexdigest() == summary["report_sha256"]
    report = pd.read_csv(daily, index_col=0, parse_dates=True)
    for frame, expected in (
        (report.loc[:"2026-07-31"], summary["historical_current_cost"]["full_period"]),
        (report.loc["2026-08-03":], summary["forward_carried_positions"]),
    ):
        actual = summarize_period(frame)
        for key, value in expected.items():
            assert (
                actual[key] == pytest.approx(value, abs=1e-10)
                if value is not None
                else actual[key] is None
            )
    assert len(report.loc["2026-08-03":]) == 57
