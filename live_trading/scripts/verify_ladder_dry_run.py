#!/usr/bin/env python3
"""把实盘合成分数与 BT v4 回测实际消费的预测帧逐点对账。

对账锚点选 external_pred.pkl 而不是重跑一遍合成：后者用的是同一段代码，
比出来永远相等，什么都证明不了。external_pred.pkl 是回测当时真正吃进去的
那一帧，它相等才说明实盘信号 = BT v4 官方信号。
"""

import argparse
import resource
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

BT_V4_PRED = (
    PROJECT_ROOT / "backtest" / "result"
    / "20260822_233132_phase_s_m0h20rankices_all_ladder_k3h5_ensemble"
    / "external_pred.pkl"
)


def _dump_members(generator, signal_date: str, blended, filtered, dump_dir: str):
    """落盘每个成员 z-score **之前**的原始分数。

    合成是「先按日截面 z-score 再等权平均」，所以合成结果依赖截面成员是谁。
    只存合成结果没法回答「换个截面会怎样」，必须存 z-score 之前的原始分数。
    """
    from live_trading.modules.signal_generator import _InferenceDataset

    out = Path(dump_dir)
    out.mkdir(parents=True, exist_ok=True)
    day_features = generator._features.loc[pd.Timestamp(signal_date)]
    day_features = day_features.dropna(how="all")
    members = {}
    for index, model in enumerate(generator._models):
        raw = model.predict(_InferenceDataset(day_features), segment="test")
        members[f"member_{index}"] = raw.astype(float)
    pd.DataFrame(members).to_pickle(out / f"members_raw_{signal_date}.pkl")
    blended.to_pickle(out / f"blended_{signal_date}.pkl")
    filtered.to_pickle(out / f"filtered_{signal_date}.pkl")
    print("dumped member/blended/filtered scores to %s" % out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="alla_v4_ladder_k3h5_postclose_real")
    parser.add_argument("--signal-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument(
        "--dump-dir",
        help="把每个成员 z-score 前的原始分数与合成结果落盘。"
        "一次 handler 加载要 20 分钟以上，落盘后所有后续诊断都不必重跑。",
    )
    args = parser.parse_args()

    import qlib

    from live_trading.modules.live_config import load_live_config
    from live_trading.modules.signal_generator import SignalGenerator
    from live_trading.modules.universe_gate import filter_scores

    started = time.monotonic()
    config_path = (
        PROJECT_ROOT / "live_trading" / "configs" / (args.config + ".yaml")
    )
    config = load_live_config(config_path, PROJECT_ROOT)
    qlib.init(
        provider_uri=str(Path(config["data"]["qlib_dir"]).expanduser()),
        region=config["data"]["region"],
    )

    generator = SignalGenerator(config, PROJECT_ROOT)
    generator.load_model()
    blended = generator.predict(args.signal_date, allow_stale=True)
    live, stats = filter_scores(
        blended,
        signal_date=args.signal_date,
        raw_spec=config["universe_filter"],
        project_root=PROJECT_ROOT,
    )
    live = live.dropna()
    elapsed = time.monotonic() - started
    peak_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3

    if args.dump_dir:
        _dump_members(generator, args.signal_date, blended, live, args.dump_dir)

    reference = pd.read_pickle(BT_V4_PRED)
    day = reference.xs(pd.Timestamp(args.signal_date), level="datetime")["score"]

    common = live.index.intersection(day.index)
    max_gap = (
        float((live[common] - day[common]).abs().max())
        if len(common)
        else float("nan")
    )

    live_top = list(live.sort_values(ascending=False).index[: args.topk])
    # 回测在同一份宇宙掩码上选 top-k，所以参照侧也要先对齐到 live 的宇宙。
    day_top = list(day[common].sort_values(ascending=False).index[: args.topk])

    print("universe filter stats:", stats)
    print(
        "live names: %d, reference names on that day: %d, common: %d"
        % (len(live), len(day), len(common))
    )
    print("max |live - reference| on common names: %.12g" % max_gap)
    print("live top%d:      %s" % (args.topk, live_top))
    print("reference top%d: %s" % (args.topk, day_top))
    print("top%d match: %s" % (args.topk, live_top == day_top))
    print("signal wall clock: %.1f s, peak RSS: %.2f GiB" % (elapsed, peak_gib))
    return 0 if live_top == day_top else 1


if __name__ == "__main__":
    raise SystemExit(main())
