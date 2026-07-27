"""TRAModel on the best available torch device.

qlib's pytorch_tra hardcodes a module-global ``device = cuda|cpu``. On this
host CPU LSTM segfaults (libomp clash between lightgbm and torch), while MPS
works and is much faster, so we patch the module global to prefer MPS. The
same patch is applied on unpickle so evaluation processes move batches to the
device the saved tensors live on.
"""

from __future__ import annotations

import torch

import qlib.contrib.data.dataset as _mts_dataset
import qlib.contrib.model.pytorch_tra as _tra


def _select_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def apply_device_patch() -> str:
    """Point both TRA module-global `device` vars at the best device.

    Batch tensors are built in qlib.contrib.data.dataset (`_to_tensor`), model
    parameters in qlib.contrib.model.pytorch_tra — both read their own global.
    """
    dev = _select_device()
    _tra.device = dev
    _mts_dataset.device = dev
    return dev


class TRAModelAuto(_tra.TRAModel):
    def __init__(self, *args, **kwargs):
        apply_device_patch()
        super().__init__(*args, **kwargs)

    def __setstate__(self, state):
        apply_device_patch()
        self.__dict__.update(state)
        # qlib Serializable drops underscore attrs on pickle
        self.__dict__.setdefault("_writer", None)
