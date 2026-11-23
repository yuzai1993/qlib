"""Receipt publication, verified day closure and recovery against the real relay."""

import copy
import datetime as dt
import json

import pytest

from test_clipboard_auto import BATCH, STRATEGY, auto, signals
from test_clipboard_guarded_auto import GuardedWorld, guarded_signals


class SettlementWorld(GuardedWorld):
    def __init__(self, auto, root):
        super().__init__(auto, root)
        self.engine.capabilities.append("day-close-v1")
        self.deals = []

    def snapshot(self, symbols):
        result = super().snapshot(symbols)
        result.update(deals=copy.deepcopy(self.deals), deals_error="")
        for row in result["working_orders"]:
            row.update(insert_date="20261027", insert_time="14:57:05")
        return result

    def fill(self, index, *, quantity=None, status=56):
        super().fill(index, quantity=quantity, status=status)
        order, _ = self.broker.submissions[index]
        quantity = order["quantity"] if quantity is None else quantity
        if quantity:
            self.deals.append(
                dict(
                    broker_order_id="broker-" + str(index),
                    trade_id="trade-" + str(index),
                    stock_code=order["symbol"],
                    side=order["side"],
                    quantity=quantity,
                    price=order["price"],
                    amount=quantity * order["price"],
                    trade_date="20261027",
                    trade_time="145800",
                )
            )


def settlement_orders(auto, root):
    guarded_signals(root, buys=["600001.SH", "600002.SH"])
    w = SettlementWorld(auto, root)
    w.at("14:57:05")
    w.runner.step()
    w.fill(0)
    w.fill(1, quantity=0, status=50)
    return w


def test_verified_zero_fill_closes_at_17_preserves_raw_state_and_allows_next_day(auto, tmp_path):
    w = settlement_orders(auto, tmp_path)
    w.at("16:59:59")
    w.runner.step()
    assert not w.runner.journal["exported"]
    assert "day_order_reconciliation" not in w.runner.journal
    baseline = copy.deepcopy(w.runner.journal["settlement_baseline"])
    w.at("17:00:00")
    w.runner.step()
    assert w.runner.complete()
    meta = w.runner.journal["day_order_reconciliation"]
    assert meta["method"] == "verified_postclose_day_order_expiry"
    assert meta["zero_fill_orders"][0]["broker_order_id"] == "broker-1"
    assert w.runner.journal["latest"]["orders"][1]["status"] == "ACCEPTED"
    assert w.runner.journal["settlement_baseline"] == baseline
    final = tmp_path / "outbound" / ("fills_" + BATCH + ".jsonl")
    rows = [json.loads(line) for line in final.read_text().splitlines()]
    assert [row["status"] for row in rows] == ["FILLED", "EXPIRED"]
    assert "当日未成交" in rows[1]["message"]
    assert len(w.broker.submissions) == 2
    old_session = w.engine.session
    w.now += dt.timedelta(days=1)
    response = w.engine.handle(dict(protocol=auto.PROTOCOL, type="poll", session=None, request_id="tomorrow"))
    assert response["day"] == "2026-10-28"
    assert response["session"] != old_session


@pytest.mark.parametrize("damage", ["positions", "deals", "baseline", "foreign_deal", "query_error"])
def test_inconsistent_postclose_evidence_remains_unresolved_with_reason(auto, tmp_path, damage):
    w = settlement_orders(auto, tmp_path)
    w.at("15:00:00")
    w.runner.step()
    if damage == "positions":
        w.holdings["600002.SH"] = 100
    elif damage == "deals":
        w.deals = []
    elif damage == "baseline":
        w.runner.journal.pop("settlement_baseline", None)
    elif damage == "foreign_deal":
        w.deals.append(dict(w.deals[0], broker_order_id="foreign", trade_id="foreign"))
    else:
        original = w.engine.snapshot
        w.engine.snapshot = lambda symbols: dict(original(symbols), deals=None, deals_error="unavailable")
    w.at("17:00:00")
    w.runner.step()
    assert not w.runner.complete()
    assert not w.runner.journal["exported"]
    assert w.runner.journal["postclose_reconcile_error"]
    assert "day_order_reconciliation" not in w.runner.journal
    assert not any(request["type"] == "close_day" for request in w.requests)
    assert len(w.broker.submissions) == 2


def test_close_ack_lost_response_replays_same_request_without_trades(auto, tmp_path):
    w = settlement_orders(auto, tmp_path)
    original = w.exchange

    def lose_ack(request):
        reply = original(request)
        if request["type"] == "close_day":
            raise TimeoutError("lost close acknowledgment")
        return reply

    w.runner.exchange = lose_ack
    w.at("17:00:00")
    w.runner.step()
    assert not w.runner.complete()
    w.runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", original, now=lambda: w.now)
    w.runner.step()
    assert w.runner.complete()
    requests = [request for request in w.requests if request["type"] == "close_day"]
    assert len(requests) == 2 and requests[0] == requests[1]
    assert len(w.broker.submissions) == 2


def test_late_changed_fill_invalidates_closed_journal_without_overwriting_final(auto, tmp_path):
    w = settlement_orders(auto, tmp_path)
    w.at("17:00:00")
    w.runner.step()
    assert w.runner.complete()
    final = tmp_path / "outbound" / ("fills_" + BATCH + ".jsonl")
    original = final.read_bytes()
    w.broker.rows[1].update(filled_quantity=100, status=55, average_price=10.02)
    w.at("17:00:02")
    w.runner.step()
    assert not w.runner.complete()
    assert "changed" in w.runner.journal["postclose_reconcile_error"]
    assert final.read_bytes() == original
    assert not w.engine._day_closed()


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "holdings",
        "deal",
        "receipt",
        "raw_fill",
        "active_status",
        "active_remark",
        "active_time",
        "active_cleared_remark",
    ],
)
@pytest.mark.parametrize("clear_remark", [False, True])
def test_next_day_recognizes_only_replayable_automatic_closure(auto, tmp_path, damage, clear_remark):
    w = settlement_orders(auto, tmp_path)
    w.at("17:00:00")
    w.runner.step()
    active = copy.deepcopy(
        w.runner.journal["day_order_reconciliation"]["evidence"]["observation"]["snapshot"]["working_orders"][0]
    )
    if clear_remark:
        active["remark"] = ""
    archive = tmp_path / "archive"
    archive.mkdir()
    for path in list((tmp_path / "outbound").iterdir()):
        path.rename(archive / path.name)
    state = copy.deepcopy(w.runner.journal)
    proof = state["day_order_reconciliation"]["evidence"]["observation"]
    if damage == "holdings":
        proof["snapshot"]["positions"][0]["shares"] += 100
    elif damage == "deal":
        proof["snapshot"]["deals"] = []
    elif damage == "receipt":
        (archive / ("fills_" + BATCH + ".jsonl")).write_text("corrupt")
    elif damage == "raw_fill":
        state["latest"]["orders"][1]["filled_quantity"] = 100
    elif damage == "active_status":
        active["status"] = 51
    elif damage == "active_remark":
        active["remark"] = "changed"
    elif damage == "active_cleared_remark":
        active["remark"] = ""
    elif damage == "active_time":
        active["insert_time"] = "13:00:00"
    w.runner.journal_path.write_text(json.dumps(state))
    w.now += dt.timedelta(days=1)
    runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", w.exchange, now=lambda: w.now)
    assert runner.reconciled_prior_order(active, "local") is (damage in (None, "active_cleared_remark"))


def test_pending_partial_intent_is_finished_before_new_final_after_crash(auto, tmp_path, monkeypatch):
    w, _ = mixed_orders(auto, tmp_path)
    original = w.runner.publish

    def fail_before_publish(*args, **kwargs):
        raise OSError("interrupted publication")

    monkeypatch.setattr(w.runner, "publish", fail_before_publish)
    with pytest.raises(OSError):
        w.runner.step()
    assert not list((tmp_path / "outbound").glob("fills_*.done"))
    w.broker.rows.pop()  # Replace the zero-fill cached observation with its late fill.
    w.fill(1)
    monkeypatch.setattr(w.runner, "publish", original)
    w.runner.step()
    publications = w.runner.journal["receipt_exports"].values()
    assert len(publications) == 2 and all(p["published"] for p in publications)
    assert len(list((tmp_path / "outbound").glob("fills_*.done"))) == 2
    assert len(w.broker.submissions) == 2


def test_partial_and_final_use_exact_deals_instead_of_truncated_native_average(auto, tmp_path):
    w = settlement_orders(auto, tmp_path)
    first = w.deals[0]
    quantity = first["quantity"]
    w.deals = [
        dict(first, quantity=100, price=10.01, amount=1001),
        dict(first, trade_id="second-part", quantity=quantity - 100, price=10.02, amount=(quantity - 100) * 10.02),
    ]
    exact_amount = 1001 + (quantity - 100) * 10.02
    w.broker.rows[0]["average_price"] = int(exact_amount / quantity * 1000) / 1000
    w.at("15:00:00")
    w.runner.step()
    partial = next((tmp_path / "outbound").glob("fills_*_partial_*.jsonl"))
    row = json.loads(partial.read_text())
    assert row["avg_price"] * row["filled_qty"] == pytest.approx(exact_amount)
    assert row["avg_price"] != w.broker.rows[0]["average_price"]
    w.at("17:00:00")
    w.runner.step()
    assert w.runner.complete()
    final = tmp_path / "outbound" / ("fills_" + BATCH + ".jsonl")
    rows = [json.loads(line) for line in final.read_text().splitlines()]
    assert rows[0]["avg_price"] == row["avg_price"]


def test_delayed_deal_for_one_terminal_does_not_block_other_confirmed_fill(auto, tmp_path):
    w = settlement_orders(auto, tmp_path)
    w.broker.rows.pop()
    w.fill(1)
    late = w.deals.pop()
    w.at("15:00:00")
    w.runner.step()
    partial = next((tmp_path / "outbound").glob("fills_*_partial_*.jsonl"))
    assert json.loads(partial.read_text())["client_order_id"] == "buy0"
    assert not w.runner.journal["exported"]
    assert w.runner.journal["receipt_reconcile_error"]
    w.deals.append(late)
    w.runner.step()
    assert w.runner.journal["exported"]
    assert not w.runner.journal.get("receipt_reconcile_error")


@pytest.mark.parametrize("damage", ["lost_ack", "changed_fill", "query_error"])
def test_next_day_discovery_blocks_unsettled_close_even_after_receipt_export(auto, tmp_path, damage):
    w = settlement_orders(auto, tmp_path)
    w.at("17:00:00")
    w.runner.step()
    assert w.runner.complete()
    if damage == "lost_ack":
        w.runner.journal["day_order_reconciliation"]["acknowledged"] = False
    elif damage == "changed_fill":
        w.runner.journal["latest"]["orders"][1]["filled_quantity"] = 100
    else:
        w.runner.journal["latest"]["query_error"] = "unavailable"
    w.runner.save()
    signals(tmp_path, batch=BATCH.replace("20261027", "20261028"), date="2026-10-28")
    w.now += dt.timedelta(days=1)
    runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", w.exchange, now=lambda: w.now)
    with pytest.raises(RuntimeError, match="previous day has unresolved journal"):
        runner.discover()


def mixed_orders(auto, root):
    _, _, orders = guarded_signals(root, buys=["600001.SH", "600002.SH"])
    w = GuardedWorld(auto, root)
    w.at("14:57:05")
    w.runner.step()
    w.fill(0)
    w.fill(1, quantity=0, status=50)
    w.at("15:00:01")
    return w, orders


def test_confirmed_fill_exports_before_other_order_is_terminal_and_imports_once(auto, tmp_path):
    from live_trading.modules.fill_importer import FillImporter, LiveRecorder

    w, orders = mixed_orders(auto, tmp_path)
    recorder = LiveRecorder(str(tmp_path / "ledger.db"), opening_cash=244607)
    recorder.record_batch(BATCH, "2026-10-27", "LIVE", 2)
    recorder.record_orders(BATCH, orders)
    importer = FillImporter(tmp_path, recorder)
    w.runner.step()
    assert not w.runner.journal["exported"]
    assert importer.import_fills() == 1
    partial_cash = recorder.get_cash()
    assert len(recorder.get_positions()) == 1
    assert importer.reconcile(BATCH)["missing"] == 1
    w.at("15:00:02")
    w.runner.step()
    assert importer.import_fills() == 0
    assert recorder.get_cash() == partial_cash
    w.fill(1)
    w.runner.step()
    assert w.runner.journal["exported"]
    assert importer.import_fills() == 2
    assert importer.reconcile(BATCH)["missing"] == 0
    assert len(recorder.get_positions()) == 2
    assert len(w.broker.submissions) == 2


def test_receipt_publication_crash_reuses_original_bytes_after_mac_archive(auto, tmp_path, monkeypatch):
    w, _ = mixed_orders(auto, tmp_path)
    original_publish = w.runner.publish

    def crash_after_publish(*args, **kwargs):
        original_publish(*args, **kwargs)
        raise OSError("crash after receipt publication")

    monkeypatch.setattr(w.runner, "publish", crash_after_publish)
    with pytest.raises(OSError, match="crash after"):
        w.runner.step()
    files = list((tmp_path / "outbound").glob("fills_*.jsonl"))
    assert len(files) == 1
    wire = files[0].read_bytes()
    archive = tmp_path / "archive"
    archive.mkdir()
    for path in list((tmp_path / "outbound").iterdir()):
        path.rename(archive / path.name)
    w.at("15:00:05")
    w.runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", w.exchange, now=lambda: w.now)
    w.runner.step()
    assert not list((tmp_path / "outbound").glob("fills_*.done"))
    assert (archive / files[0].name).read_bytes() == wire
    assert json.loads(wire)["client_order_id"] == "buy0"
    assert len(w.broker.submissions) == 2
