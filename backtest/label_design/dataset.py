"""Dataset split wrapper that purges future-label leakage at boundaries."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from qlib.data.dataset import DatasetH


class PurgedHorizonDataset(DatasetH):
    """Purge H+1 dates from train/valid while preserving official segments."""

    def __init__(
        self,
        *args,
        label_horizon: int,
        purge_segments: Sequence[str] = ("train", "valid"),
        **kwargs,
    ):
        self.label_horizon = int(label_horizon)
        if self.label_horizon <= 0:
            raise ValueError("label_horizon must be positive")
        self.purge_segments = tuple(purge_segments)
        super().__init__(*args, **kwargs)

    @staticmethod
    def _calendar(start, end) -> pd.DatetimeIndex:
        from qlib.data import D

        return pd.DatetimeIndex(D.calendar(start_time=start, end_time=end))

    def _purged_segment(self, name: str):
        segment = self.segments[name]
        if name not in self.purge_segments:
            return segment
        if not isinstance(segment, (list, tuple)) or len(segment) != 2:
            raise ValueError(f"segment {name!r} must contain [start, end]")
        start, end = segment
        calendar = self._calendar(start, end)
        offset = self.label_horizon + 1
        if len(calendar) <= offset:
            raise ValueError(
                f"segment {name!r} is too short for horizon "
                f"{self.label_horizon}"
            )
        return (start, str(pd.Timestamp(calendar[-1 - offset]).date()))

    def prepare(
        self,
        segments,
        col_set="__all",
        data_key="infer",
        **kwargs,
    ):
        if isinstance(segments, str) and segments in self.segments:
            return super().prepare(
                self._purged_segment(segments),
                col_set=col_set,
                data_key=data_key,
                **kwargs,
            )
        if (
            isinstance(segments, (list, tuple))
            and segments
            and all(
                isinstance(name, str) and name in self.segments
                for name in segments
            )
        ):
            return [
                super(PurgedHorizonDataset, self).prepare(
                    self._purged_segment(name),
                    col_set=col_set,
                    data_key=data_key,
                    **kwargs,
                )
                for name in segments
            ]
        return super().prepare(
            segments,
            col_set=col_set,
            data_key=data_key,
            **kwargs,
        )
