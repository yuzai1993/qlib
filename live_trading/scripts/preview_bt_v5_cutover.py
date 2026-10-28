"""Read-only hypothetical BT v5 intents from an audited ledger snapshot.

This deliberately does not construct a LiveRecorder, SignalPublisher or bridge batch.
BUY values remain deferred until confirmed broker cash is known during activation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backtest/scripts"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-manifest", required=True, type=Path)
    parser.add_argument("--pred", required=True, type=Path)
    parser.add_argument("--st-daily", required=True, type=Path)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    import qlib
    from qlib.data import D
    from live_trading.modules.code_map import qmt_to_qlib
    from live_trading.modules.order_manager import OrderManager
    from qlib.contrib.strategy.topk_dropout import stable_rank_scores
    from universe_filter import parse_universe_filter, filter_pred

    manifest = json.loads(args.snapshot_manifest.read_text())
    snapshot = Path(manifest["snapshot"]["path"])
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if digest != manifest["snapshot"]["sha256"]:
        raise ValueError("snapshot SHA mismatch")
    conn = sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        positions = {
            qmt_to_qlib(row["stock_code"]): dict(row)
            for row in conn.execute("SELECT * FROM positions")
            if row["shares"] > 0
        }
        cash = conn.execute(
            "SELECT value FROM account_state WHERE key='cash'"
        ).fetchone()[0]
    finally:
        conn.close()
    config = yaml.safe_load(
        (
            ROOT / "live_trading/configs/alla_v2_top5d1f100_postclose_real.yaml"
        ).read_text()
    )
    config["universe_filter"]["st_daily"] = str(args.st_daily.resolve())
    qlib.init(
        provider_uri=config["data"]["qlib_dir"],
        region="cn",
        kernels=4,
        expression_cache=None,
        dataset_cache=None,
    )
    prediction = pd.read_pickle(args.pred)
    prediction = prediction.loc[
        prediction.index.get_level_values("datetime") == pd.Timestamp(args.signal_date)
    ]
    if prediction.empty:
        raise ValueError("no prediction for requested signal date")
    filtered, stats = filter_pred(
        prediction, parse_universe_filter(config["universe_filter"])
    )
    scores = filtered.iloc[:, 0].droplevel("datetime")
    prices = D.features(
        list(positions),
        ["$close/$factor"],
        start_time=args.signal_date,
        end_time=args.signal_date,
    )
    close = {
        code: float(group.iloc[-1, 0])
        for code, group in prices.groupby(level="instrument")
    }
    if any(code not in close or not pd.notna(close[code]) for code in positions):
        raise ValueError("missing held-position close price")
    nav = cash + sum(
        position["shares"] * close[code] for code, position in positions.items()
    )
    intents = OrderManager(config).generate_orders(
        scores, positions, cash, close, nav, signal_date=args.signal_date
    )
    ranked = stable_rank_scores(scores)
    ranks = {code: index + 1 for index, code in enumerate(ranked.index)}
    result = {
        "kind": "hypothetical_offline_preview",
        "publishable": False,
        "signal_date": args.signal_date,
        "snapshot_sha256": digest,
        "source_ledger_blockers": manifest["blockers"],
        "cash": cash,
        "mark_to_close_nav": nav,
        "held_ranks": {code: ranks.get(code) for code in positions},
        "intents": intents,
        "universe_filter": stats.as_dict(),
        "note": "Recompute after outstanding old-strategy orders reconcile. No orders or batches were published. BUY target_value=0 means deferred broker-side sizing, not a zero-cash order.",
    }
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {"intents": len(intents), "publishable": False, "output": str(args.output)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
