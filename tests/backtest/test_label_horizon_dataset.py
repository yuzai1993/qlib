from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.label_design.dataset import PurgedHorizonDataset  # noqa: E402


class _RecordingHandler:
    def __init__(self):
        self.selectors = []

    def fetch(self, selector, **_kwargs):
        self.selectors.append(selector)
        return selector


def _dataset(monkeypatch) -> PurgedHorizonDataset:
    dataset = object.__new__(PurgedHorizonDataset)
    dataset.handler = _RecordingHandler()
    dataset.segments = {
        "train": ("2021-01-01", "2021-01-10"),
        "valid": ("2021-01-11", "2021-01-15"),
        "test": ("2021-01-18", "2021-01-22"),
    }
    dataset.fetch_kwargs = {}
    dataset.label_horizon = 2
    dataset.purge_segments = ("train", "valid")
    monkeypatch.setattr(
        dataset,
        "_calendar",
        lambda start, end: pd.bdate_range(start, end),
    )
    return dataset


def test_prepare_purges_train_by_horizon_plus_one_without_mutating_segments(
    monkeypatch,
):
    dataset = _dataset(monkeypatch)
    original = dict(dataset.segments)

    result = dataset.prepare("train")

    assert result == ("2021-01-01", "2021-01-05")
    assert dataset.segments == original


def test_prepare_purges_each_named_learning_segment(monkeypatch):
    dataset = _dataset(monkeypatch)

    result = dataset.prepare(["train", "valid"])

    assert result == [
        ("2021-01-01", "2021-01-05"),
        ("2021-01-11", "2021-01-12"),
    ]


def test_prepare_leaves_test_and_direct_slices_unchanged(monkeypatch):
    dataset = _dataset(monkeypatch)
    direct = slice("2021-01-04", "2021-01-08")

    assert dataset.prepare("test") == ("2021-01-18", "2021-01-22")
    assert dataset.prepare(direct) == direct
