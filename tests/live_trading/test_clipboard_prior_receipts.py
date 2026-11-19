"""Prior-day broker cache must require independently archived zero-fill receipts."""

import copy
import hashlib
import json

import pytest

from test_clipboard_auto import STRATEGY, auto, signals
from test_clipboard_guarded_auto import GuardedWorld, guarded_signals


def reconciled_prior_order(auto, root):
    batch_id = "20261026_" + STRATEGY + "_001"
    path, _, _ = signals(root, batch=batch_id, date="2026-10-26", orders=[dict(
        client_order_id="yesterday-buy", stock_code="600001.SH", side="BUY",
        quantity=0, target_value=2000, priority=10,
    )])
    active = dict(remark="", broker_order_id="old-broker-id", status=50,
                  quantity=100, filled_quantity=0, side="BUY", stock_code="600001.SH",
                  insert_date="20261026", insert_time="14:57:09")
    receipt = dict(type="fill_event", batch_id=batch_id, client_order_id="yesterday-buy",
                   mode="LIVE", stock_code="600001.SH", side="BUY", status="EXPIRED",
                   requested_qty=100, filled_qty=0, avg_price=0, qmt_order_id="old-broker-id")
    archive = root / "archive" / ("fills_" + batch_id + ".jsonl")
    archive.parent.mkdir(exist_ok=True)
    archive.write_text(json.dumps(receipt) + "\n")
    archive.with_suffix(".done").write_text("done\n")
    previous = dict(
        day="2026-10-26", strategy=STRATEGY, session="previous-session",
        batch=auto.load_batch(path, STRATEGY, "2026-10-26"),
        exported=True, snapshot_exported=True,
        phases=dict(BUY=dict(cash_plan=dict(snapshot=dict(account=dict(account_id="local"))))),
        latest=dict(orders=[dict(id="yesterday-buy", symbol="600001.SH", side="BUY",
                                quantity=100, filled_quantity=0, status="ACCEPTED",
                                broker_orders=[dict(broker_order_id="old-broker-id", quantity=100,
                                                    filled_quantity=0, status=50)])]),
        operator_reconciliation=dict(
            method="operator_confirmed_day_order_expiry", at="2026-10-27T00:01:00+08:00",
            zero_fill_orders=[dict(broker_order_id="old-broker-id", stock_code="600001.SH",
                                   quantity=100, filled_quantity=0, terminal_status="EXPIRED")],
            fill_receipt_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            broker_snapshot=dict(snapshot=dict(account=dict(available_cash=2000))),
        ),
    )
    state = root / "local_state" / ("2026-10-26_" + STRATEGY + ".json")
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps(previous))
    return active, state, archive


def prepared_world(auto, root):
    guarded_signals(root, buys=["600001.SH"])
    active, state, archive = reconciled_prior_order(auto, root)
    world = GuardedWorld(auto, root)
    world.foreign = [active]
    return world, state, archive


@pytest.mark.parametrize("insert_time", ["14:57:09", "145709"])
def test_read_only_check_accepts_exact_reconciled_prior_day_cache_and_keeps_raw_evidence(auto, tmp_path, insert_time):
    w, state, archive = prepared_world(auto, tmp_path)
    w.foreign[0]["insert_time"] = insert_time
    before = state.read_bytes(), archive.read_bytes(), copy.deepcopy(w.foreign)
    report = w.runner.check()
    assert report["status"] == "READY"
    assert report["working_orders"] == w.foreign
    assert report["reconciled_prior_orders"] == w.foreign
    assert not w.broker.submissions
    assert before == (state.read_bytes(), archive.read_bytes(), w.foreign)


def test_buy_can_execute_after_exact_reconciled_old_cache_but_does_not_replay_old_order(auto, tmp_path):
    w, _, _ = prepared_world(auto, tmp_path)
    w.at("14:57:05")
    w.runner.step()
    assert len(w.broker.submissions) == 1
    assert w.broker.submissions[0][0]["id"] == "buy0"
    assert w.broker.submissions[0][0]["symbol"] == "600001.SH"


@pytest.mark.parametrize("changes", [
    {"insert_date": "20261027"}, {"insert_date": "20261028"},
    {"insert_date": "20260230"}, {"insert_date": None}, {"insert_date": ""},
    {"insert_time": "20:00:00"}, {"insert_time": "15:00:01"}, {"insert_time": "bad"},
    {"insert_time": None}, {"filled_quantity": 1}, {"quantity": 200},
    {"insert_time": "250000"}, {"insert_time": "146000"}, {"insert_time": "140099"},
    {"insert_time": "150001"}, {"insert_time": "090000"}, {"insert_time": "1457"},
    {"stock_code": "600002.SH"}, {"side": "SELL"}, {"broker_order_id": "another-id"},
])
def test_old_receipt_never_excuses_today_unknown_overnight_changed_or_filled_order(auto, tmp_path, changes):
    w, _, _ = prepared_world(auto, tmp_path)
    w.foreign[0].update(changes)
    with pytest.raises(RuntimeError, match="working order"):
        w.runner.check()
    assert not w.broker.submissions


@pytest.mark.parametrize("damage", ["journal", "receipt", "done", "unexported", "hash", "authorization", "identity"])
def test_prior_order_requires_intact_closed_journal_and_archived_receipt(auto, tmp_path, damage):
    w, state, archive = prepared_world(auto, tmp_path)
    if damage == "journal":
        state.unlink()
    elif damage == "receipt":
        archive.write_text(archive.read_text().replace('"EXPIRED"', '"ACCEPTED"'))
    elif damage == "done":
        archive.with_suffix(".done").unlink()
    else:
        data = json.loads(state.read_text())
        if damage == "unexported":
            data["exported"] = False
        elif damage == "hash":
            data["operator_reconciliation"]["fill_receipt_sha256"] = "bad"
        elif damage == "authorization":
            data["operator_reconciliation"]["zero_fill_orders"] = []
        elif damage == "identity":
            data["strategy"] = "other"
        state.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="working order|unresolved journal"):
        w.runner.check()
    assert not w.broker.submissions


def test_valid_prior_receipt_does_not_hide_another_current_working_order(auto, tmp_path):
    w, _, _ = prepared_world(auto, tmp_path)
    w.foreign.append(dict(w.foreign[0], broker_order_id="manual-today", insert_date="20261027"))
    with pytest.raises(RuntimeError, match="working order"):
        w.runner.check()
    assert not w.broker.submissions


@pytest.mark.parametrize("damage", [
    "account", "same_day_close", "future_close", "naive_close", "snapshot_unexported",
    "parent_fill", "child_id", "extra_child", "planned_side", "duplicate_receipt", "receipt_side",
])
def test_prior_expiry_requires_same_account_consistent_history_and_final_receipt(auto, tmp_path, damage):
    w, state, archive = prepared_world(auto, tmp_path)
    previous = json.loads(state.read_text())
    meta = previous["operator_reconciliation"]
    parent = previous["latest"]["orders"][0]
    if damage == "account":
        previous["phases"]["BUY"]["cash_plan"]["snapshot"]["account"]["account_id"] = "another-account"
    elif damage.endswith("close"):
        meta["at"] = {"same_day_close": "2026-10-26T14:59:00+08:00",
                      "future_close": "2026-10-28T00:00:00+08:00",
                      "naive_close": "2026-10-27T00:01:00"}[damage]
    elif damage == "snapshot_unexported":
        previous["snapshot_exported"] = False
    elif damage == "parent_fill":
        parent["filled_quantity"] = 1
    elif damage == "child_id":
        parent["broker_orders"][0]["broker_order_id"] = "different"
    elif damage == "extra_child":
        parent["broker_orders"].append(dict(parent["broker_orders"][0]))
    elif damage == "planned_side":
        previous["batch"]["orders"][0]["side"] = "SELL"
    else:
        if damage == "duplicate_receipt":
            archive.write_bytes(archive.read_bytes() * 2)
        else:
            row = json.loads(archive.read_text())
            row["side"] = "SELL"
            archive.write_text(json.dumps(row) + "\n")
        meta["fill_receipt_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    state.write_text(json.dumps(previous))
    with pytest.raises(RuntimeError, match="working order"):
        w.runner.check()
    assert not w.broker.submissions


def test_duplicate_old_broker_order_still_blocks(auto, tmp_path):
    w, _, _ = prepared_world(auto, tmp_path)
    w.foreign.append(dict(w.foreign[0]))
    with pytest.raises(RuntimeError, match="multiple working broker orders"):
        w.runner.check()
    assert not w.broker.submissions
