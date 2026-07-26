from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backtest" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze_holding_duration as holding  # noqa: E402


def _scores() -> pd.Series:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"])
    values = {
        dates[0]: {"A": 10.0, "B": 9.0, "C": 8.0, "D": 7.0},
        dates[1]: {"A": 10.0, "C": 9.0, "B": 8.0, "D": 7.0},
        dates[2]: {"D": 10.0, "C": 9.0, "A": 8.0, "B": 7.0},
    }
    series = pd.concat(
        {
            date: pd.Series(day_scores, dtype=float)
            for date, day_scores in values.items()
        },
        names=["datetime", "instrument"],
    )
    return series.rename("score")


def test_replay_records_completed_and_right_censored_spells():
    completed, censored = holding.replay_holding_spells(
        _scores(), topk=2, n_drop=1
    )

    assert [(spell.instrument, spell.duration) for spell in completed] == [
        ("B", 1),
        ("A", 2),
    ]
    assert [(spell.instrument, spell.duration) for spell in censored] == [
        ("C", 2),
        ("D", 1),
    ]
    assert all(spell.censored is False for spell in completed)
    assert all(spell.censored is True for spell in censored)


def test_replay_rejects_non_datetime_instrument_index():
    scores = pd.Series([1.0], index=pd.Index(["A"]), name="score")

    with pytest.raises(ValueError, match="datetime.*instrument"):
        holding.replay_holding_spells(scores, topk=30, n_drop=1)


def test_kaplan_meier_survival_is_probability_of_reaching_each_age():
    survival = holding.kaplan_meier_survival(
        completed=[1, 2],
        censored=[1, 2],
    )

    assert survival == {1: 1.0, 2: 0.75}


def test_duration_summary_uses_completed_spells_and_reports_censoring():
    summary = holding.summarize_durations(
        completed=[1, 2, 9, 10],
        censored=[3, 4],
        thresholds=[5, 10],
    )

    assert summary["completed_count"] == 4
    assert summary["censored_count"] == 2
    assert summary["mean"] == 5.5
    assert summary["p50"] == 5.5
    assert summary["p75"] == pytest.approx(9.25)
    assert summary["p90"] == pytest.approx(9.7)
    assert summary["max"] == 10
    assert summary["held_at_least"] == {"5": 0.5, "10": 0.25}
    assert summary["survival"] == {
        "1": 1.0,
        "2": pytest.approx(5 / 6),
        "3": pytest.approx(2 / 3),
        "4": pytest.approx(2 / 3),
        "5": pytest.approx(2 / 3),
        "6": pytest.approx(2 / 3),
        "7": pytest.approx(2 / 3),
        "8": pytest.approx(2 / 3),
        "9": pytest.approx(2 / 3),
        "10": pytest.approx(1 / 3),
    }


def test_duration_summary_requires_a_completed_spell():
    with pytest.raises(ValueError, match="completed"):
        holding.summarize_durations([], [3], thresholds=[5])
