"""定因 dry-run 与回测 external_pred.pkl 之间 1.33e-3 的分数差。

合成是「先按日截面 z-score 再等权平均」，所以每只票的最终分数依赖截面成员是谁。
live 先做宇宙过滤再合成（4880 只），回测在更宽的截面上合成（5207 只）。
如果差异全部来自截面成员，那么把 live 的**原始**（z-score 之前）分数放回
回测那个截面上重算，就应该与 external_pred.pkl 逐点相等到浮点噪声。

用原始分数而不是已合成的分数：后者已经被错的截面污染过，无法反推。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

BT_V4_PRED = (
    PROJECT_ROOT / "backtest" / "result"
    / "20260822_233132_phase_s_m0h20rankices_all_ladder_k3h5_ensemble"
    / "external_pred.pkl"
)


def _blend(raw: pd.DataFrame) -> pd.Series:
    """按列 z-score 后等权平均，复刻 blend_score_series 的单日行为。"""
    z = (raw - raw.mean()) / raw.std(ddof=1)
    return z.mean(axis=1)


def main() -> int:
    signal_date = sys.argv[1] if len(sys.argv) > 1 else "2026-07-30"
    dump = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/ladder_dump")

    raw = pd.read_pickle(dump / f"members_raw_{signal_date}.pkl")
    live_blended = pd.read_pickle(dump / f"blended_{signal_date}.pkl")
    live_filtered = pd.read_pickle(dump / f"filtered_{signal_date}.pkl")
    reference = pd.read_pickle(BT_V4_PRED)
    ref_day = reference.xs(pd.Timestamp(signal_date), level="datetime")["score"]

    live_blended = (
        live_blended.droplevel("datetime")
        if isinstance(live_blended.index, pd.MultiIndex) else live_blended
    )
    live_filtered = (
        live_filtered.droplevel("datetime")
        if isinstance(live_filtered.index, pd.MultiIndex) else live_filtered
    )

    print("raw members: %s, shape=%s" % (list(raw.columns), raw.shape))
    print("live blended: %d, live filtered: %d, reference: %d"
          % (len(live_blended), len(live_filtered), len(ref_day)))

    # 1. 自洽性：手算的合成应当复现 dump 出来的 blended。
    mine = _blend(raw)
    common = mine.index.intersection(live_blended.index)
    print("[self] max|hand-blend - dumped blend| = %.3g"
          % np.abs(mine[common] - live_blended[common]).max())

    # 2. 现状：live 的最终分数 vs 参考。
    shared = live_filtered.index.intersection(ref_day.index)
    gap_now = np.abs(live_filtered[shared] - ref_day[shared])
    print("[now]  max = %.6g, median = %.6g, on %d names"
          % (gap_now.max(), gap_now.median(), len(shared)))

    # 3. 假设：把原始分数放回参考的截面上重算。
    on_ref = raw.reindex(ref_day.index).dropna(how="all")
    reblend = _blend(on_ref)
    shared2 = reblend.index.intersection(ref_day.index)
    gap_ref = np.abs(reblend[shared2] - ref_day[shared2])
    print("[on-ref-cross-section] max = %.6g, median = %.6g, on %d names"
          % (gap_ref.max(), gap_ref.median(), len(shared2)))

    # 4. 若仍不等，看差值是否只是一个仿射变换（同一截面下的常数缩放/平移）。
    a, b = np.polyfit(reblend[shared2].values, ref_day[shared2].values, 1)
    resid = np.abs(a * reblend[shared2] + b - ref_day[shared2])
    print("[affine fit] scale=%.9f shift=%.3g residual max=%.3g"
          % (a, b, resid.max()))

    only_ref = ref_day.index.difference(raw.index)
    only_live = raw.index.difference(ref_day.index)
    print("names only in reference: %d %s" % (len(only_ref), list(only_ref[:8])))
    print("names only in live raw:  %d %s" % (len(only_live), list(only_live[:8])))
    if len(only_ref):
        extras = ref_day[only_ref]
        print("their reference scores: %s"
              % ", ".join("%s=%+.3f" % (k, v) for k, v in extras.items()))
        print("reference cross-section: mean=%+.6f std=%.6f; without them: "
              "mean=%+.6f std=%.6f"
              % (ref_day.mean(), ref_day.std(ddof=1),
                 ref_day.drop(only_ref).mean(), ref_day.drop(only_ref).std(ddof=1)))

    # 5. 仿射变换保序，所以真正该问的是排名有没有变，而不是分数差多大。
    live_rank = live_filtered[shared].rank(ascending=False)
    ref_rank = ref_day[shared].rank(ascending=False)
    n_moved = int((live_rank != ref_rank).sum())
    print("[rank] identical on %d/%d names, %d moved, spearman=%.12f"
          % (len(shared) - n_moved, len(shared), n_moved,
             live_filtered[shared].corr(ref_day[shared], method="spearman")))

    # 6. 排名风险的正确尺度是仿射残差，不是原始分数差。
    top = ref_day[shared].sort_values(ascending=False)
    for k in (3, 5, 10):
        margin = float(top.iloc[k - 1] - top.iloc[k])
        print("[margin] rank%d vs rank%d = %.6g  (%.0fx the affine residual)"
              % (k, k + 1, margin, margin / max(resid.max(), 1e-300)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
