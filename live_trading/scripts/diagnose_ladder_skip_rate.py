#!/usr/bin/env python3
"""量化「封板/停牌导致的名字集合偏离」在测试期出现的频率。

回测的 select_ladder_buys 遇到不可买的候选会顺延取下一名，而实盘按 spec 4.7 不顺延
（封板票照样尝试买入，停牌票让层变薄）。两边的名字集合因此会分叉。回测里这些 skip
是静默的——没有 log、没有计数、不进返回值——所以只能拿同一帧预测在这里重算一遍。

可买判定直接复用回测自己的那套，不做近似：
  - 停牌：`$close` 为 NaN（exchange.check_stock_suspended 的口径）
  - 封板：`$change >= cap`，cap 由 cn_limit.apply_market_cn_limits 按板块与日期给出

对账锚点用 BT v4 实际消费的 external_pred.pkl，而不是重跑一遍合成：后者用的是同一段
代码，比出来永远相等，什么都证明不了。
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

BT_V4_PRED = (
    PROJECT_ROOT / "backtest" / "result"
    / "20260822_233132_phase_s_m0h20rankices_all_ladder_k3h5_ensemble"
    / "external_pred.pkl"
)
TEST_START = "2021-07-16"
TEST_END = "2026-07-16"


def load_scores(path, start, end):
    frame = pd.read_pickle(path)
    scores = frame.iloc[:, 0] if isinstance(frame, pd.DataFrame) else frame
    scores = scores.dropna()
    dates = scores.index.get_level_values("datetime")
    return scores[(dates >= start) & (dates <= end)]


def load_buyable(instruments, start, end):
    """按 (datetime, instrument) 索引返回布尔 Series，True = 当日可买。

    D.features 给的是 (instrument, datetime)，而预测帧是 (datetime, instrument)。
    这里就地换级并排序，好让下游能直接跟分数对齐——用 (date, code) 去查一个
    (code, date) 的索引不会报错，只会安静地全部落空。
    """
    from qlib.backtest.cn_limit import apply_market_cn_limits
    from qlib.data import D

    quotes = D.features(
        sorted(instruments), ["$close", "$change"],
        start_time=start, end_time=end,
    )
    apply_market_cn_limits(quotes)
    buyable = ~quotes["limit_buy"]
    buyable.index = buyable.index.swaplevel()
    return buyable.sort_index()


def align(scores, buyable):
    """把可买标记贴到分数帧上；NaN 表示当日无行情。"""
    frame = scores.to_frame("score")
    frame["buyable"] = buyable.reindex(frame.index)
    return frame


def diagnose(frame, topk):
    days = 0
    days_with_skip = 0
    skipped = 0
    thin_days = 0
    thin_slots = 0
    no_quote = 0

    for _, day in frame.groupby(level="datetime"):
        ranked = day.sort_values("score", ascending=False)
        picked = 0
        skipped_here = 0
        for flag in ranked["buyable"].to_numpy():
            if picked >= topk:
                break
            if flag is None or (isinstance(flag, float) and pd.isna(flag)):
                no_quote += 1
                continue  # 当日没有行情，回测同样取不到，不计入顺延
            if not flag:
                skipped_here += 1
                continue
            picked += 1
        days += 1
        skipped += skipped_here
        if skipped_here:
            days_with_skip += 1
        if picked < topk:
            thin_days += 1
            thin_slots += topk - picked

    return {
        "days": days,
        "days_with_skip": days_with_skip,
        "skipped": skipped,
        "thin_days": thin_days,
        "thin_slots": thin_slots,
        "no_quote": no_quote,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pred", default=str(BT_V4_PRED))
    parser.add_argument("--start", default=TEST_START)
    parser.add_argument("--end", default=TEST_END)
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument(
        "--provider-uri",
        default=str(Path.home() / ".qlib" / "qlib_data" / "cn_data"),
    )
    args = parser.parse_args()

    import qlib

    qlib.init(provider_uri=args.provider_uri, region="cn")

    scores = load_scores(args.pred, args.start, args.end)
    instruments = set(scores.index.get_level_values("instrument"))
    buyable = load_buyable(instruments, args.start, args.end)
    frame = align(scores, buyable)
    covered = int(frame["buyable"].notna().sum())
    if covered == 0:
        raise SystemExit(
            "没有任何候选贴上行情——索引大概又对不上了，别信下面的数字"
        )
    stats = diagnose(frame, args.topk)

    days = max(stats["days"], 1)
    print("=" * 68)
    print("阶梯顺延频率诊断  %s ~ %s  topk=%d"
          % (args.start, args.end, args.topk))
    print("=" * 68)
    print("交易日数                        : %d" % stats["days"])
    print("发生过顺延的交易日              : %d (%.1f%%)"
          % (stats["days_with_skip"], 100.0 * stats["days_with_skip"] / days))
    print("被顺延掉的候选次数              : %d" % stats["skipped"])
    print("平均每日顺延次数                : %.3f"
          % (stats["skipped"] / float(days)))
    print("凑不满 topk 的交易日（层变薄）  : %d (%.1f%%)"
          % (stats["thin_days"], 100.0 * stats["thin_days"] / days))
    print("累计缺失的层位数                : %d" % stats["thin_slots"])
    print("（对照）贴上行情的候选数        : %d" % covered)
    print("（对照）扫到但无行情的候选数    : %d" % stats["no_quote"])
    print()
    print("读法：实盘不顺延。「发生过顺延的交易日」占比是实盘与回测名字集合可能")
    print("分叉的日子占比上界；「凑不满 topk」的日子实盘会直接让层变薄。")


if __name__ == "__main__":
    main()
