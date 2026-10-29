"""Guarded cash protocol using the real memory executor and local broker evidence."""

import copy
import hashlib
import json

import pytest

from test_clipboard_auto import BATCH, STRATEGY, World, auto, signals


GUARDED = "CLOSE_AUCTION_TOPK_CASH_V1"
BUY_CODES = ["600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH"]


def guarded_signals(root, *, buys=BUY_CODES, sells=(), maximum=5, risk=1.0):
    orders = [
        dict(client_order_id="sell" + str(i), stock_code=code, side="SELL", quantity=qty, target_value=0, priority=i)
        for i, (code, qty) in enumerate(sells)
    ] + [
        dict(client_order_id="buy" + str(i), stock_code=code, side="BUY", quantity=0, target_value=0, priority=10 + i)
        for i, code in enumerate(buys)
    ]
    path, header, rows = signals(root, orders=orders)
    for row in rows:
        row.update(price_type=GUARDED, max_positions=maximum, cash_risk_degree=risk)
    return write_rows(path, header, rows)


def write_rows(path, header, rows):
    lines = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]
    checksum = "sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest()
    header["checksum"] = checksum
    path.write_text("\n".join([json.dumps(header)] + lines) + "\n")
    path.with_suffix(".done").write_text(checksum + "\n")
    return path, header, rows


class GuardedWorld(World):
    def __init__(self, auto, root):
        super().__init__(auto, root)
        from qmt_clipboard import Engine

        self.engine = Engine(self.broker.submit, self.broker.query, session="session-a", now=lambda: self.now,
                             snapshot=self.snapshot, capabilities=["topk-cash-v1"])
        self.cash = 244607
        self.holdings = {}
        self.prices.update({code: 10 for code in BUY_CODES})
        self.foreign = []
        self.capability = True
        self.omit_orders = False
        self.missing_quote = set()

    def snapshot(self, symbols):
        result = super().snapshot([code for code in symbols if code not in self.missing_quote])
        active = []
        for row in self.broker.rows:
            if row["status"] in (53, 54, 56, 57):
                continue
            source = next(o for o, remark in self.broker.submissions if remark == row["remark"])
            active.append({key: row[key] for key in ("remark", "broker_order_id", "status", "quantity", "filled_quantity")})
            active[-1].update(side=source["side"], stock_code=source["symbol"])
        if not self.omit_orders:
            result["working_orders"] = copy.deepcopy(active + self.foreign)
        return result

    def exchange(self, request):
        response = super().exchange(request)
        if not self.capability:
            response["capabilities"] = [c for c in response["capabilities"] if c != "topk-cash-v1"]
        return response


def test_guarded_empty_account_builds_five_with_post_sale_cash_and_durable_reservations(auto, tmp_path):
    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    w.at("14:57:05")
    w.runner.step()
    assert len(w.broker.submissions) == 5
    assert [o["symbol"] for o, _ in w.broker.submissions] == BUY_CODES
    assert [o["quantity"] for o, _ in w.broker.submissions] == [4800] * 5
    phase = w.runner.journal["phases"]["BUY"]
    assert phase["cash_plan"]["cash"] == 244607
    assert phase["cash_plan"]["allocation"] == pytest.approx(244607 / 5)
    assert len(phase["cash_plan"]["reserved"]) == 5


def test_guarded_preflight_is_query_only_and_exposes_cash_contract(auto, tmp_path):
    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    w.at("14:57:05")
    result = w.runner.check()
    assert (result["status"], result["available_cash"], result["held_names"]) == ("READY", 244607, 0)
    assert result["max_positions"] == 5
    assert result["cash_risk_degree"] == 1
    assert result["working_orders"] == []
    assert not w.runner.journal["phases"]
    assert all(r["type"] in ("snapshot", "poll") for r in w.requests)
    assert not w.broker.submissions


def test_guarded_foreign_order_appearing_between_buys_stops_remaining_requests(auto, tmp_path):
    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    original = w.exchange

    def changed_account(request):
        response = original(request)
        if request["type"] == "trade":
            w.foreign = [dict(remark="manual", broker_order_id="manual", status=50, quantity=100,
                              filled_quantity=0, side="BUY", stock_code="600099.SH")]
        return response

    w.runner.exchange = changed_account
    w.at("14:57:05")
    with pytest.raises(RuntimeError, match="foreign"):
        w.runner.step()
    assert len(w.broker.submissions) == 1
    plan = copy.deepcopy(w.runner.journal["phases"]["BUY"]["cash_plan"])
    w.foreign = []
    w.cash *= 2
    w.runner.exchange = original
    w.runner.step()
    assert len(w.broker.submissions) == 5
    assert w.runner.journal["phases"]["BUY"]["cash_plan"] == plan


def test_guarded_resume_accepts_exact_working_own_buy_with_durable_reservation(auto, tmp_path):
    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    original = w.exchange

    def interrupted(request):
        reply = original(request)
        if request["type"] == "trade":
            raise TimeoutError("lost")
        return reply

    w.runner.exchange = interrupted
    w.at("14:57:05")
    with pytest.raises(TimeoutError):
        w.runner.step()
    w.fill(0, quantity=0, status=50)
    w.runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", original, now=lambda: w.now)
    w.runner.step()
    assert len(w.broker.submissions) == 5


def test_guarded_altered_frozen_quantity_refuses_resume_before_trade(auto, tmp_path):
    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    original = w.exchange

    def interrupted(request):
        reply = original(request)
        if request["type"] == "trade":
            raise TimeoutError("lost")
        return reply

    w.runner.exchange = interrupted
    w.at("14:57:05")
    with pytest.raises(TimeoutError):
        w.runner.step()
    journal = json.loads(w.runner.journal_path.read_text())
    journal["phases"]["BUY"]["requests"][0]["request"]["orders"][0]["quantity"] += 100
    w.runner.journal_path.write_text(json.dumps(journal))
    w.runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", original, now=lambda: w.now)
    before = len(w.requests)
    with pytest.raises(ValueError, match="original trade request"):
        w.runner.step()
    assert len(w.requests) == before
    assert len(w.broker.submissions) == 1


@pytest.mark.parametrize("status,filled", [(50, 0), (55, 100)])
def test_guarded_accepted_or_partial_sell_blocks_buys(auto, tmp_path, status, filled):
    guarded_signals(tmp_path, sells=[("301165.SZ", 600)])
    w = GuardedWorld(auto, tmp_path)
    w.holdings = {"301165.SZ": 600}
    w.at("14:55:00")
    w.runner.step()
    w.fill(0, quantity=filled, status=status)
    w.at("14:57:05")
    w.runner.step()
    assert len(w.broker.submissions) == 1
    assert "BUY" not in w.runner.journal["phases"]


@pytest.mark.parametrize("status,filled", [(57, 0), (53, 100)])
def test_guarded_terminal_failed_or_partial_sell_keeps_residual_name(auto, tmp_path, status, filled):
    guarded_signals(tmp_path, sells=[("301165.SZ", 600)])
    w = GuardedWorld(auto, tmp_path)
    w.holdings = {"301165.SZ": 600}
    w.at("14:55:00")
    w.runner.step()
    w.fill(0, quantity=filled, status=status)
    w.at("14:57:05")
    w.runner.step()
    assert len(w.broker.submissions) == 5
    assert [o["symbol"] for o, _ in w.broker.submissions[1:]] == BUY_CODES[:4]
    assert w.runner.journal["phases"]["BUY"]["cash_plan"]["held"] == ["301165.SZ"]


@pytest.mark.parametrize("defect", ["capability", "missing_orders", "foreign", "invalid_orders"])
def test_guarded_requires_capability_and_complete_clear_working_order_evidence(auto, tmp_path, defect):
    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    if defect == "capability":
        w.capability = False
    elif defect == "missing_orders":
        w.omit_orders = True
    else:
        w.foreign = [dict(remark="manual", broker_order_id="manual-1", status=50, quantity=100,
                         filled_quantity=0, side="BUY", stock_code="600099.SH")]
        if defect == "invalid_orders":
            w.foreign[0]["quantity"] = True
    w.at("14:57:05")
    with pytest.raises((RuntimeError, ValueError), match="capability|working|foreign"):
        w.runner.step()
    assert not w.broker.submissions


def test_guarded_quote_and_board_lot_failure_never_redistribute_or_replace(auto, tmp_path):
    guarded_signals(tmp_path, buys=BUY_CODES + ["600006.SH"])
    w = GuardedWorld(auto, tmp_path)
    w.cash = 50000
    w.missing_quote = {BUY_CODES[0]}
    w.prices[BUY_CODES[1]] = 1000
    w.prices["600006.SH"] = 10
    w.at("14:57:05")
    w.runner.step()
    assert [o["symbol"] for o, _ in w.broker.submissions] == BUY_CODES[2:]
    assert [o["quantity"] for o, _ in w.broker.submissions] == [900] * 3
    assert w.runner.journal["phases"]["BUY"]["cash_plan"]["allocation"] == 10000


def test_actual_qmt_snapshot_missing_tick_keeps_other_candidates_original_divisor(auto, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import qmt_clipboard

    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    account = SimpleNamespace(m_strAccountID="local", m_dAvailable=244607, m_dBalance=244607,
                              m_dInstrumentValue=0, m_dFrozenCash=0)

    def details(account_id, account_type, kind):
        assert (account_id, account_type) == ("local", "STOCK")
        return [account] if kind == "ACCOUNT" else []

    monkeypatch.setattr(qmt_clipboard, "get_trade_detail_data", details, raising=False)
    context = SimpleNamespace(
        get_full_tick=lambda codes: {code: dict(lastPrice=10, timetag=w.now.strftime("%Y%m%d %H:%M:%S"))
                                     for code in codes if code != BUY_CODES[0]},
        get_instrument_detail=lambda code: dict(UpStopPrice=12, DownStopPrice=8),
    )
    w.engine.snapshot = qmt_clipboard.QmtBroker(context, "local").snapshot
    w.at("14:57:05")
    w.runner.step()
    assert [o["symbol"] for o, _ in w.broker.submissions] == BUY_CODES[1:]
    assert [o["quantity"] for o, _ in w.broker.submissions] == [4800] * 4
    assert w.runner.journal["phases"]["BUY"]["cash_plan"]["allocation"] == pytest.approx(244607 / 5)


def test_guarded_restart_keeps_frozen_cash_prices_quantities_and_request_ids(auto, tmp_path):
    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    original = w.exchange
    lost = [True]

    def exchange(request):
        reply = original(request)
        if request["type"] == "trade" and lost[0]:
            lost[0] = False
            raise TimeoutError("lost buy reply")
        return reply

    w.runner.exchange = exchange
    w.at("14:57:05")
    with pytest.raises(TimeoutError):
        w.runner.step()
    before = copy.deepcopy(w.runner.journal["phases"]["BUY"])
    w.cash *= 2
    w.prices.update({code: 1 for code in BUY_CODES})
    w.runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", original, now=lambda: w.now)
    w.runner.step()
    assert len(w.broker.submissions) == 5
    assert w.runner.journal["phases"]["BUY"]["cash_plan"] == before["cash_plan"]
    trades = [r for r in w.requests if r["type"] == "trade"]
    assert trades[0] == trades[1]
    assert [o["quantity"] for o, _ in w.broker.submissions] == [4800] * 5


def test_guarded_duplicate_broker_children_cannot_reuse_one_cash_reservation(auto, tmp_path):
    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    original = w.exchange

    def lose_reply(request):
        response = original(request)
        if request["type"] == "trade":
            raise TimeoutError("lost")
        return response

    w.runner.exchange = lose_reply
    w.at("14:57:05")
    with pytest.raises(TimeoutError):
        w.runner.step()
    w.fill(0, quantity=0, status=50)
    order, remark = w.broker.submissions[0]
    w.foreign = [dict(remark=remark, broker_order_id="duplicate-order", status=50,
                      quantity=order["quantity"], filled_quantity=0, side="BUY", stock_code=order["symbol"])]
    w.runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", original, now=lambda: w.now)
    with pytest.raises(RuntimeError, match="multiple|duplicate|reservation"):
        w.runner.step()
    assert len(w.broker.submissions) == 1


@pytest.mark.parametrize("changed", ["holdings", "foreign", "cash"])
def test_guarded_crash_before_executor_receives_request_rechecks_fresh_broker_gates(auto, tmp_path, changed):
    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    original = w.exchange

    def interrupted_before_clipboard(request):
        if request["type"] == "trade":
            raise TimeoutError("crash before clipboard write")
        return original(request)

    w.runner.exchange = interrupted_before_clipboard
    w.at("14:57:05")
    with pytest.raises(TimeoutError):
        w.runner.step()
    assert not w.broker.submissions
    first_request = copy.deepcopy(w.runner.journal["phases"]["BUY"]["requests"][0]["request"])
    if changed == "holdings":
        w.holdings = {"60009" + str(i) + ".SH": 100 for i in range(5)}
    elif changed == "foreign":
        w.foreign = [dict(remark="manual", broker_order_id="manual", status=50, quantity=100,
                          filled_quantity=0, side="BUY", stock_code="600099.SH")]
    else:
        w.cash = 0
    w.runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", original, now=lambda: w.now)
    if changed == "foreign":
        with pytest.raises(RuntimeError, match="foreign"):
            w.runner.step()
    else:
        w.runner.step()
    assert not w.broker.submissions
    assert w.runner.journal["phases"]["BUY"]["requests"][0]["request"] == first_request


def test_guarded_real_clipboard_timeout_resumes_exact_durable_pending_request(auto, tmp_path):
    from clipboard_relay import exchange, pending_trade_requests
    from qmt_clipboard import Runtime
    from test_qmt_clipboard import MemoryClipboard
    from test_clipboard_relay import Clock

    guarded_signals(tmp_path)
    w = GuardedWorld(auto, tmp_path)
    clipboard = MemoryClipboard()
    runtime = Runtime(clipboard, w.engine)
    stopped = [True]

    def pump():
        message = json.loads(clipboard.text) if clipboard.text else {}
        if message.get("type") == "trade" and stopped[0]:
            return
        runtime.step()

    clock = Clock(pump)

    def roundtrip(request):
        return exchange(clipboard, request, timeout=2, interval=0.1, clock=clock.now, sleep=clock.sleep,
                        pending_requests=pending_trade_requests(w.runner.journal))

    w.runner.exchange = roundtrip
    w.at("14:57:05")
    with pytest.raises(TimeoutError, match="outcome unknown"):
        w.runner.step()
    original = json.loads(clipboard.text)
    assert original["type"] == "trade"
    assert not w.broker.submissions
    stopped[0] = False
    w.runner = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", roundtrip, now=lambda: w.now)
    w.runner.step()
    assert len(w.broker.submissions) == 5
    assert w.runner.journal["phases"]["BUY"]["requests"][0]["request"] == original
    assert len({o["id"] for o, _ in w.broker.submissions}) == 5


@pytest.mark.parametrize("defect", ["mixed", "duplicate", "pair", "target", "legacy_contract"])
def test_batch_rejects_mixed_or_invalid_guarded_contract_before_orders(auto, tmp_path, defect):
    path, header, rows = guarded_signals(tmp_path)
    if defect == "mixed":
        rows[1]["cash_risk_degree"] = 0.9
    elif defect == "duplicate":
        rows[1]["stock_code"] = rows[0]["stock_code"]
    elif defect == "pair":
        rows[1].update(stock_code=rows[0]["stock_code"], side="SELL", quantity=100)
    elif defect == "target":
        rows[0]["target_value"] = 10000
    else:
        for row in rows:
            row.update(price_type="CLOSE_AUCTION_LIMIT", target_value=10000)
    write_rows(path, header, rows)
    with pytest.raises(ValueError):
        auto.load_batch(path, STRATEGY, "2026-10-27")


def test_guarded_fills_and_skips_import_into_mac_ledger(auto, tmp_path):
    from clipboard_relay import exchange
    from qmt_clipboard import Runtime
    from test_qmt_clipboard import MemoryClipboard
    from test_clipboard_relay import Clock
    from live_trading.modules.fill_importer import FillImporter, LiveRecorder

    _, _, rows = guarded_signals(tmp_path)
    recorder = LiveRecorder(str(tmp_path / "ledger.db"), opening_cash=244607, fees={"commission_rate": 0.0000854})
    recorder.record_batch(BATCH, "2026-10-27", "LIVE", len(rows), account_environment="REAL")
    recorder.record_orders(BATCH, rows)
    w = GuardedWorld(auto, tmp_path)
    w.missing_quote = {BUY_CODES[0]}
    clipboard = MemoryClipboard()
    runtime = Runtime(clipboard, w.engine)
    clock = Clock(runtime.step)
    w.runner.exchange = lambda request: exchange(clipboard, request, timeout=5, clock=clock.now, sleep=clock.sleep)
    w.at("14:57:05")
    w.runner.step()
    for i in range(4):
        w.fill(i)
    w.at("17:00:00")
    w.runner.step()
    importer = FillImporter(tmp_path, recorder)
    assert importer.import_fills() == 5
    assert importer.import_broker_snapshots() == 1
    assert set(recorder.get_positions()) == set(BUY_CODES[1:])
    assert recorder.get_cash() == pytest.approx(52201.07616, abs=0.01)
    assert importer.reconcile(BATCH)["missing"] == 0
    assert importer.import_fills() == 0
    assert importer.import_broker_snapshots() == 0
