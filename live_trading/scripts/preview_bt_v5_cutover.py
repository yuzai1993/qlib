"""Read-only hypothetical BT v5 intents from an audited ledger snapshot.

This deliberately does not construct a LiveRecorder, SignalPublisher or bridge batch.
BUY values remain deferred until confirmed broker cash is known during activation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backtest/scripts"))


def build_liquidation_inventory(scores, positions, cash, *, topk, risk_degree):
    """Preserve ledger facts; require human liquidation and broker reconciliation.

    The returned inventory has no order quantities, prices or batch identity.
    In particular, marked-to-market proceeds are never treated as available cash.
    """
    from qlib.contrib.strategy.topk_dropout import stable_rank_scores

    if (
        isinstance(cash, bool)
        or not isinstance(cash, (int, float))
        or not math.isfinite(cash)
        or cash < 0
    ):
        raise ValueError("invalid ledger cash")
    if isinstance(topk, bool) or not isinstance(topk, int) or topk < 1:
        raise ValueError("invalid target topk")
    if (
        isinstance(risk_degree, bool)
        or not isinstance(risk_degree, (int, float))
        or not math.isfinite(risk_degree)
        or not 0 < risk_degree <= 1
    ):
        raise ValueError("invalid target risk_degree")
    inventory = []
    for code, position in sorted(positions.items()):
        shares = position.get("shares")
        if (
            isinstance(shares, bool)
            or not isinstance(shares, (int, float))
            or not math.isfinite(shares)
            or shares < 0
            or int(shares) != shares
        ):
            raise ValueError("invalid ledger shares for " + str(code))
        if shares:
            inventory.append(
                {
                    "stock_code": code,
                    "ledger_shares": int(shares),
                    "broker_sellable_shares": None,
                }
            )
    candidates = stable_rank_scores(scores).head(topk).index.tolist()
    return {
        "kind": "manual_liquidation_inventory",
        "publishable": False,
        "ready_for_activation": False,
        "holdings_source": "ledger_snapshot_not_broker_confirmation",
        "cash_snapshot": cash,
        "manual_liquidation_required": bool(inventory),
        "manual_liquidation_inventory": inventory,
        "post_liquidation_plan": {
            "requires_confirmed_zero_holdings": True,
            "requires_no_open_orders": True,
            "requires_actual_fill_import_and_broker_reconciliation": True,
            "risk_degree": risk_degree,
            "topk": topk,
            "buy_candidates": candidates,
            "available_cash": None,
            "cash_source": "fresh broker available cash after confirmed liquidation",
        },
        "note": "Human review inventory only. Do not mark holdings sold or reset cash from this file. Residual holdings block the clean-start path. Refresh signals, import actual fills and reconcile broker state before manual activation; no orders are published by this preview.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-manifest", required=True, type=Path)
    parser.add_argument("--pred", required=True, type=Path)
    parser.add_argument("--st-daily", required=True, type=Path)
    parser.add_argument("--signal-date", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--mode", choices=("rebalance", "manual_liquidation"), default="rebalance"
    )
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
    if args.mode == "manual_liquidation":
        result = build_liquidation_inventory(
            scores,
            positions,
            cash,
            topk=config["strategy"]["topk"],
            risk_degree=config["strategy"]["risk_degree"],
        )
    else:
        positions = {code: row for code, row in positions.items() if row["shares"] > 0}
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
        result = {
            "kind": "hypothetical_offline_preview",
            "publishable": False,
            "cash": cash,
            "mark_to_close_nav": nav,
            "intents": intents,
            "note": "Recompute after outstanding old-strategy orders reconcile. No orders or batches were published. BUY target_value=0 means deferred broker-side sizing, not a zero-cash order.",
        }
    ranked = stable_rank_scores(scores)
    ranks = {code: index + 1 for index, code in enumerate(ranked.index)}
    result.update(
        {
            "signal_date": args.signal_date,
            "snapshot_sha256": digest,
            "snapshot_manifest": str(args.snapshot_manifest.resolve()),
            "prediction_sha256": hashlib.sha256(args.pred.read_bytes()).hexdigest(),
            "source_ledger_blockers": manifest["blockers"],
            "held_ranks": {code: ranks.get(code) for code in positions},
            "universe_filter": stats.as_dict(),
        }
    )
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {"kind": result["kind"], "publishable": False, "output": str(args.output)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
