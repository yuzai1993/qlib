"""Memory-lean MTSDatasetH for 16GB hosts.

MTSDatasetH keeps the handler's `_data`/`_infer`/`_learn` frames (float64,
~4GB each for csi1000 2014-2026) alive for the whole training run and makes an
extra full copy during setup. This subclass rebuilds `setup_data` without the
copy and frees the handler frames once the float32 arrays are extracted,
cutting steady-state memory from ~15GB to ~4GB. Sample semantics are identical
to MTSDatasetH (`_learn` data, zero-filled feature NaN, same slices/memory).
"""

from __future__ import annotations

import gc
import warnings

import numpy as np
import pandas as pd

from qlib.contrib.data.dataset import MTSDatasetH, _create_ts_slices
from qlib.data.dataset import DatasetH


class LeanMTSDatasetH(MTSDatasetH):
    def setup_data(self, handler_kwargs: dict = None, **kwargs):
        DatasetH.setup_data(self, **kwargs)

        if handler_kwargs is not None:
            self.handler.setup_data(**handler_kwargs)

        try:
            df = self.handler._learn
        except AttributeError:
            warnings.warn("cannot access `_learn`, will load raw data")
            df = self.handler._data
        # mutate in place instead of copying: the handler frames are freed below
        df.index = df.index.swaplevel()
        df.sort_index(inplace=True)

        self._data = df["feature"].values.astype("float32")
        np.nan_to_num(self._data, copy=False)
        self._label = df["label"].squeeze().values.astype("float32")
        self._index = df.index

        if self.input_size is not None and self.input_size != self._data.shape[1]:
            warnings.warn("the data has different shape from input_size and the data will be reshaped")
            assert self._data.shape[1] % self.input_size == 0, "data mismatch, please check `input_size`"

        self._batch_slices = _create_ts_slices(self._index, self.seq_len)

        daily_slices = {date: [] for date in sorted(self._index.unique(level=1))}
        for i, (code, date) in enumerate(self._index):
            daily_slices[date].append(self._batch_slices[i])
        self._daily_slices = np.array(list(daily_slices.values()), dtype="object")
        self._daily_index = pd.Series(list(daily_slices.keys()))

        if self.memory_mode == "sample":
            self._memory = np.zeros((len(self._data), self.num_states), dtype=np.float32)
        elif self.memory_mode == "daily":
            self._memory = np.zeros((len(self._daily_index), self.num_states), dtype=np.float32)
        else:
            raise ValueError(f"invalid memory_mode `{self.memory_mode}`")

        self._zeros = np.zeros((self.seq_len, max(self.num_states, self._data.shape[1])), dtype=np.float32)

        # free the handler's float64 frames (no longer needed after extraction)
        for attr in ("_learn", "_infer", "_data"):
            if hasattr(self.handler, attr):
                setattr(self.handler, attr, None)
        del df
        gc.collect()
