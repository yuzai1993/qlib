"""SignalGenerator 推理口径测试：NaN 必须原样传给 LightGBM，不允许 fillna(0)。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from paper_trading.modules.signal_generator import SignalGenerator


class DummyLGB:
    """记录 predict 收到的矩阵。"""

    def __init__(self):
        self.last_X = None

    def predict(self, X):
        self.last_X = np.asarray(X, dtype=float)
        return np.arange(len(X), dtype=float)


def _make_generator():
    gen = SignalGenerator(config={}, project_root=Path("."))
    gen._lgb_model = DummyLGB()
    return gen


def test_nan_features_passed_through_not_filled_with_zero():
    gen = _make_generator()
    df = pd.DataFrame(
        {"F1": [1.0, np.nan], "F2": [np.nan, 2.0]},
        index=pd.Index(["SH600000", "SZ000001"], name="instrument"),
    )
    scores = gen._score_features(df, "2026-07-10")

    assert gen._lgb_model.last_X is not None
    # 核心断言：NaN 不能被替换为 0
    assert np.isnan(gen._lgb_model.last_X).sum() == 2
    assert (gen._lgb_model.last_X == 0).sum() == 0
    assert list(scores.index) == ["SH600000", "SZ000001"]


def test_all_nan_rows_are_dropped():
    gen = _make_generator()
    df = pd.DataFrame(
        {"F1": [1.0, np.nan], "F2": [2.0, np.nan]},
        index=pd.Index(["SH600000", "SZ000001"], name="instrument"),
    )
    scores = gen._score_features(df, "2026-07-10")
    # 全 NaN 行（长期停牌/退市残留）仍应剔除
    assert list(scores.index) == ["SH600000"]
