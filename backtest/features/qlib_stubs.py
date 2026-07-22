"""
在 Cython 扩展未编译时，为 qlib.data._libs 注入纯 Python stub，
以便表达式解析 / 配置单测可以在无 Python.h 的环境中运行。

仅影响表达式树构建，不用于真实行情计算。
调用方必须在首次 ``import qlib.data`` / ``import qlib.contrib.data`` 之前执行。
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import sys
import types


def _ext_binaries_exist() -> bool:
    """检查 rolling/expanding 编译产物是否存在（不触发 import）。"""
    import qlib

    libs_dir = pathlib.Path(qlib.__file__).parent / "data" / "_libs"
    rolling_bins = list(libs_dir.glob("rolling*.so")) + list(libs_dir.glob("rolling*.pyd"))
    expanding_bins = list(libs_dir.glob("expanding*.so")) + list(libs_dir.glob("expanding*.pyd"))
    return bool(rolling_bins and expanding_bins)


def _make_module(name: str, is_package: bool = False) -> types.ModuleType:
    mod = types.ModuleType(name)
    spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=is_package)
    mod.__spec__ = spec
    mod.__loader__ = None
    if is_package:
        mod.__path__ = []
    sys.modules[name] = mod
    return mod


def install_cython_stubs() -> bool:
    """注入 stub（如需要）并注册全部算子。返回是否注入了 stub。"""
    import qlib
    from qlib.config import C

    injected = False

    if not _ext_binaries_exist() and "qlib.data._libs.rolling" not in sys.modules:

        def _dummy(*args, **kwargs):
            raise RuntimeError("Cython rolling/expanding stubs 仅用于表达式解析，不可用于真实计算")

        libs_dir = str(pathlib.Path(qlib.__file__).parent / "data" / "_libs")
        libs = _make_module("qlib.data._libs", is_package=True)
        libs.__path__ = [libs_dir]

        rolling = _make_module("qlib.data._libs.rolling")
        rolling.rolling_slope = _dummy
        rolling.rolling_rsquare = _dummy
        rolling.rolling_resi = _dummy

        expanding = _make_module("qlib.data._libs.expanding")
        expanding.expanding_slope = _dummy
        expanding.expanding_rsquare = _dummy
        expanding.expanding_resi = _dummy
        injected = True

    from qlib.data.ops import Operators, register_all_ops

    if not Operators._ops:
        register_all_ops(C)
    return injected
