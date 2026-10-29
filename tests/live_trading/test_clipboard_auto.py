"""Daily automation against the real executor, file protocol and ledger importer."""

import copy
import datetime as dt
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from test_qmt_clipboard import Broker


STRATEGY = "alla_v4_ladder_k1h5_postclose_real"
BATCH = "20261027_" + STRATEGY + "_001"
ZONE = dt.timezone(dt.timedelta(hours=8))


@pytest.fixture
def auto(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "live_trading/qmt_strategy"))
    return importlib.import_module("clipboard_auto")


def signals(root, *, batch=BATCH, date="2026-10-27", orders=None):
    orders = (
        orders
        if orders is not None
        else [
            dict(
                client_order_id="20261027001001S",
                stock_code="301165.SZ",
                side="SELL",
                quantity=600,
                target_value=0,
                priority=10,
            ),
            dict(
                client_order_id="20261027001002B",
                stock_code="300214.SZ",
                side="BUY",
                quantity=0,
                target_value=49969.92,
                priority=20,
            ),
        ]
    )
    rows = [
        dict(
            type="order",
            batch_id=batch,
            price_type="CLOSE_AUCTION_LIMIT",
            limit_price=0,
            instrument_qlib=o["stock_code"][-2:] + o["stock_code"][:6],
            reason="cohort_layer",
            **o,
        )
        for o in orders
    ]
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows]
    checksum = "sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest()
    header = dict(
        type="batch_header",
        schema_version="2.0",
        batch_id=batch,
        strategy_id=STRATEGY,
        trade_date=date,
        signal_date="2026-10-26",
        account_type="STOCK",
        order_count=len(rows),
        checksum=checksum,
        created_at="2026-10-26T21:55:25+08:00",
    )
    folder = root / "inbox"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("signal_" + batch + ".jsonl")
    path.write_text("\n".join([json.dumps(header)] + lines) + "\n")
    path.with_suffix(".done").write_text(checksum + "\n")
    return path, header, rows


class World:
    def __init__(self, auto, root):
        from qmt_clipboard import Engine

        self.now = dt.datetime(2026, 10, 27, 14, 54, tzinfo=ZONE)
        self.broker = Broker()
        self.cash = 9812.65
        self.holdings = {"301165.SZ": 600}
        self.prices = {"301165.SZ": 77, "300214.SZ": 10}
        self.engine = Engine(
            self.broker.submit, self.broker.query, session="session-a", now=lambda: self.now, snapshot=self.snapshot
        )
        self.root = root
        self.requests = []
        self.runner = auto.AutoRelay(root, STRATEGY, root / "local_state", self.exchange, now=lambda: self.now)

    def at(self, value):
        self.now = dt.datetime.fromisoformat("2026-10-27T" + value + "+08:00")

    def snapshot(self, symbols):
        return dict(
            account=dict(
                account_id="local", available_cash=self.cash, total_asset=55000, market_value=45000, frozen_cash=0
            ),
            positions=[
                dict(stock_code=k, shares=v, can_use_volume=v, avg_cost=10, market_value=v * 10)
                for k, v in self.holdings.items()
                if v
            ],
            quotes={
                k: dict(
                    last=self.prices[k],
                    timetag=self.now.strftime("%Y%m%d %H:%M:%S"),
                    upper=self.prices[k] * 1.2,
                    lower=self.prices[k] * 0.8,
                )
                for k in symbols
            },
        )

    def exchange(self, request):
        if request["type"] == "trade":
            # A crash immediately after submission must leave the exact request recoverable.
            journals = list((self.root / "local_state").glob("*.json"))
            assert any(request["request_id"] in path.read_text() for path in journals)
        self.requests.append(copy.deepcopy(request))
        return self.engine.handle(request)

    def fill(self, index, *, quantity=None, status=56):
        order, remark = self.broker.submissions[index]
        qty = quantity if quantity is not None else order["quantity"]
        self.broker.rows.append(
            dict(
                remark=remark,
                broker_order_id="broker-" + str(index),
                quantity=order["quantity"],
                filled_quantity=qty,
                average_price=order["price"] if qty else 0,
                status=status,
                error="",
            )
        )
        self.cash += qty * order["price"] * (1 if order["side"] == "SELL" else -1)
        self.holdings[order["symbol"]] = self.holdings.get(order["symbol"], 0) + qty * (
            1 if order["side"] == "BUY" else -1
        )


def test_timed_daily_flow_publishes_importable_fills_and_snapshot(auto, tmp_path):
    _, header, rows = signals(tmp_path)
    w = World(auto, tmp_path)
    w.runner.step()
    assert not w.broker.submissions
    w.at("14:55:00")
    w.runner.step()
    assert w.broker.submissions[0][0] == dict(
        id="20261027001001S", symbol="301165.SZ", side="SELL", quantity=600, price=76.84
    )
    w.fill(0)
    w.at("14:57:04")
    w.runner.step()
    assert len(w.broker.submissions) == 1
    w.at("14:57:05")
    w.runner.step()
    assert w.broker.submissions[1][0] == dict(
        id="20261027001002B", symbol="300214.SZ", side="BUY", quantity=4900, price=10.02
    )
    w.fill(1)
    w.at("15:00:01")
    w.runner.step()
    receipt = tmp_path / "outbound" / ("fills_" + BATCH + ".jsonl")
    assert receipt.with_suffix(".done").exists()
    events = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert [(e["status"], e["filled_qty"]) for e in events] == [("FILLED", 600), ("FILLED", 4900)]
    from live_trading.modules.signal_schema import FillEvent, validate_fill

    for event in events:
        validate_fill(FillEvent.from_dict(event))
    assert not (tmp_path / "outbound" / ("account_" + BATCH + ".done")).exists()
    w.at("17:00:00")
    w.runner.step()
    assert (tmp_path / "outbound" / ("account_" + BATCH + ".done")).exists()
    receipt.unlink()
    receipt.with_suffix(".done").unlink()
    w.runner.step()
    assert not receipt.exists(), "Mac-archived receipts must not be republished every second"


def test_lost_reply_and_relay_restart_resend_identical_request(auto, tmp_path):
    signals(tmp_path)
    w = World(auto, tmp_path)
    original = w.exchange
    lost = [True]

    def exchange(request):
        reply = original(request)
        if request["type"] == "trade" and lost[0]:
            lost[0] = False
            raise TimeoutError("lost reply")
        return reply

    w.runner.exchange = exchange
    w.at("14:55:00")
    with pytest.raises(TimeoutError):
        w.runner.step()
    restarted = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", original, now=lambda: w.now)
    restarted.step()
    trades = [r for r in w.requests if r["type"] == "trade"]
    assert len(trades) == 2 and trades[0] == trades[1]
    assert len(w.broker.submissions) == 1


def test_qmt_restart_never_rebinds_existing_day(auto, tmp_path):
    signals(tmp_path)
    w = World(auto, tmp_path)
    w.at("14:55:00")
    w.runner.step()
    w.engine.session = "new-session"
    w.at("14:57:05")
    with pytest.raises(RuntimeError, match="session"):
        auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", w.exchange, now=lambda: w.now).step()
    assert len(w.broker.submissions) == 1


def test_no_signal_or_old_signal_never_sends(auto, tmp_path):
    signals(tmp_path, batch=BATCH.replace("20261027", "20261026"), date="2026-10-26")
    w = World(auto, tmp_path)
    w.at("14:55:00")
    w.runner.step()
    assert not w.broker.submissions


def test_incomplete_or_conflicting_batch_is_not_traded(auto, tmp_path):
    path, _, _ = signals(tmp_path)
    w = World(auto, tmp_path)
    w.at("14:55:00")
    path.with_suffix(".done").write_text("bad checksum")
    with pytest.raises(ValueError, match="checksum"):
        w.runner.step()
    assert not w.broker.submissions
    signals(tmp_path)
    signals(tmp_path, batch=BATCH[:-3] + "002")
    with pytest.raises(ValueError, match="multiple"):
        w.runner.step()
    assert not w.broker.submissions


def test_unknown_sell_blocks_buy_and_terminal_export(auto, tmp_path):
    signals(tmp_path)
    w = World(auto, tmp_path)
    w.broker.submit_error = RuntimeError("uncertain submit")
    w.at("14:55:00")
    w.runner.step()
    w.at("14:57:05")
    w.runner.step()
    w.at("15:01:00")
    w.runner.step()
    assert len(w.broker.submissions) == 1
    assert not list((tmp_path / "outbound").glob("fills_*.done"))


def test_late_start_expires_instead_of_trading(auto, tmp_path):
    signals(tmp_path)
    w = World(auto, tmp_path)
    w.at("15:00:00")
    w.runner.step()
    assert not w.broker.submissions
    events = [json.loads(line) for line in next((tmp_path / "outbound").glob("fills_*.jsonl")).read_text().splitlines()]
    assert [e["status"] for e in events] == ["EXPIRED", "EXPIRED"]


def test_partial_remains_open_until_cancel_confirmation(auto, tmp_path):
    signals(
        tmp_path,
        orders=[
            dict(client_order_id="sell", stock_code="301165.SZ", side="SELL", quantity=600, target_value=0, priority=10)
        ],
    )
    w = World(auto, tmp_path)
    w.at("14:55:00")
    w.runner.step()
    w.fill(0, quantity=300, status=55)
    w.at("15:00:00")
    w.runner.step()
    assert not list((tmp_path / "outbound").glob("fills_*.done"))
    w.broker.rows[0]["status"] = 53
    w.runner.step()
    event = json.loads(next((tmp_path / "outbound").glob("fills_*.jsonl")).read_text())
    assert (event["status"], event["filled_qty"]) == ("PARTIAL", 300)


def test_sizing_reserves_cash_fees_and_rejects_stale_prices(auto):
    now = dt.datetime(2026, 10, 27, 14, 57, 5, tzinfo=ZONE)
    orders = [
        dict(client_order_id="a", stock_code="300214.SZ", side="BUY", quantity=0, target_value=50000),
        dict(client_order_id="b", stock_code="688087.SH", side="BUY", quantity=0, target_value=50000),
    ]
    snapshot = dict(
        account={"available_cash": 10025},
        positions=[],
        quotes={
            "300214.SZ": dict(last=10, timetag="20261027 14:57:05", upper=12, lower=8),
            "688087.SH": dict(last=10, timetag="20261027 14:57:05", upper=12, lower=8),
        },
    )
    trades, skipped = auto.prepare_orders(orders, snapshot, now)
    assert [(o["id"], o["quantity"]) for o in trades] == [("a", 900)]  # 1000 costs 10025.1002 including fees
    assert skipped[0]["id"] == "b"
    snapshot["quotes"]["300214.SZ"]["timetag"] = "20261026 14:57:05"
    with pytest.raises(ValueError, match="stale"):
        auto.prepare_orders(orders, snapshot, now)


def test_real_publisher_clipboard_and_mac_ledger_roundtrip(auto, tmp_path):
    from qmt_clipboard import Runtime
    from clipboard_relay import exchange
    from test_qmt_clipboard import MemoryClipboard
    from test_clipboard_relay import Clock
    from live_trading.modules.signal_schema import BatchHeader, SignalOrder
    from live_trading.modules.signal_publisher import SignalPublisher
    from live_trading.modules.fill_importer import FillImporter, LiveRecorder

    seed = tmp_path / "seed"
    _, raw_header, rows = signals(seed)
    header = BatchHeader.from_dict(raw_header)
    orders = [SignalOrder.from_dict(o) for o in rows]
    SignalPublisher(tmp_path).publish(header, orders)
    recorder = LiveRecorder(str(tmp_path / "ledger.db"), opening_cash=9812.65, fees={"commission_rate": 0.0000854})
    recorder.record_batch(BATCH, "2026-10-27", "LIVE", 2, account_environment="REAL")
    recorder.record_orders(BATCH, rows)
    with recorder._conn() as conn:
        conn.execute("INSERT INTO positions (stock_code,shares,avg_cost) VALUES (?,?,?)", ("301165.SZ", 600, 81.83))
    w = World(auto, tmp_path)
    clipboard = MemoryClipboard()
    runtime = Runtime(clipboard, w.engine)
    clock = Clock(runtime.step)
    w.runner.exchange = lambda request: exchange(clipboard, request, timeout=5, clock=clock.now, sleep=clock.sleep)
    w.at("14:55:00")
    w.runner.step()
    w.fill(0)
    w.at("14:57:05")
    w.runner.step()
    w.fill(1)
    w.at("17:00:00")
    w.runner.step()
    importer = FillImporter(tmp_path, recorder)
    assert importer.import_fills() == 2
    assert importer.import_broker_snapshots() == 1
    assert "301165.SZ" not in recorder.get_positions()
    assert recorder.get_positions()["300214.SZ"]["shares"] == 4900
    assert recorder.get_cash() == pytest.approx(6785.598, abs=0.01)
    assert importer.reconcile(BATCH)["missing"] == 0
    w.runner.step()
    assert importer.import_fills() == 0
    assert importer.import_broker_snapshots() == 0


def test_readonly_preflight_never_trades_even_during_window(auto, tmp_path):
    signals(tmp_path)
    w = World(auto, tmp_path)
    w.at("14:55:01")
    info = w.runner.check()
    assert info["batch_id"] == BATCH
    assert info["available_cash"] == 9812.65
    assert info["session"] == "session-a"
    assert not w.broker.submissions
    assert not w.runner.journal["phases"]


@pytest.mark.parametrize("stalled", ["poll", "snapshot"])
def test_check_reports_the_waiting_phase_before_a_timeout(auto, tmp_path, monkeypatch, capsys, stalled):
    import clipboard_relay
    from types import SimpleNamespace

    signals(tmp_path)
    w = World(auto, tmp_path)
    args = SimpleNamespace(
        bridge_root=str(tmp_path),
        strategy=STRATEGY,
        state_dir=str(tmp_path / "local_state"),
        output_dir=str(tmp_path / "output"),
        interval=1,
        timeout=10,
        check=True,
    )

    def communicate(clipboard, request, timeout, interval, pending_requests=()):
        if request["type"] == stalled:
            raise TimeoutError("simulated missing response")
        return w.exchange(request)

    monkeypatch.setattr(clipboard_relay, "exchange", communicate)
    with pytest.raises(TimeoutError):
        clipboard_relay.run_automatic(args, None, now=lambda: w.now)
    diagnostic = capsys.readouterr().err
    assert "[check] waiting for " + stalled in diagnostic
    assert "[check] received " + stalled not in diagnostic
    if stalled == "snapshot":
        assert "[check] received poll" in diagnostic
    assert not w.broker.submissions


def test_scheduled_command_writes_shared_logs_and_reports_completion(auto, tmp_path):
    from types import SimpleNamespace
    from clipboard_relay import run_automatic
    from qmt_clipboard import Runtime
    from test_qmt_clipboard import MemoryClipboard

    signals(tmp_path, orders=[])
    w = World(auto, tmp_path)
    w.at("17:01:00")
    clipboard = MemoryClipboard()
    runtime = Runtime(clipboard, w.engine)
    args = SimpleNamespace(
        bridge_root=str(tmp_path),
        strategy=STRATEGY,
        state_dir=str(tmp_path / "local_state"),
        output_dir=str(tmp_path / "output"),
        interval=0.001,
        timeout=1,
        check=False,
    )
    import clipboard_relay

    original_exchange = clipboard_relay.exchange

    def roundtrip(clip, request, timeout, interval, pending_requests=()):
        from test_clipboard_relay import Clock

        clock = Clock(runtime.step)
        return original_exchange(clip, request, timeout, 0.001, clock=clock.now, sleep=clock.sleep,
                                 pending_requests=pending_requests)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clipboard_relay, "exchange", roundtrip)
        assert run_automatic(args, clipboard, now=lambda: w.now) == 0
    assert (tmp_path / "logs/clipboard/session-a.result.json").exists()
    assert json.loads((tmp_path / "output/automation.status.json").read_text())["status"] == "COMPLETE"
    assert not w.broker.submissions


@pytest.mark.parametrize("moment,cash,status", [("14:57:05", 0, "SKIPPED"), ("15:00:00", 100000, "EXPIRED")])
def test_unsubmitted_buy_imports_without_changing_positions(auto, tmp_path, moment, cash, status):
    from live_trading.modules.fill_importer import FillImporter, LiveRecorder

    _, _, rows = signals(
        tmp_path,
        orders=[
            dict(client_order_id="buy", stock_code="300214.SZ", side="BUY", quantity=0, target_value=50000, priority=20)
        ],
    )
    recorder = LiveRecorder(str(tmp_path / "ledger.db"), opening_cash=100000)
    recorder.record_batch(BATCH, "2026-10-27", "LIVE", 1)
    recorder.record_orders(BATCH, rows)
    w = World(auto, tmp_path)
    w.cash = cash
    w.at(moment)
    w.runner.step()
    importer = FillImporter(tmp_path, recorder)
    assert importer.import_fills() == 1
    assert recorder.get_cash() == 100000
    assert not recorder.get_positions()
    assert json.loads(next((tmp_path / "archive").glob("fills_*.jsonl")).read_text())["status"] == status
    assert not w.broker.submissions


def test_limit_price_fill_cannot_exceed_mac_target_budget(auto, tmp_path):
    from live_trading.modules.fill_importer import FillImporter, LiveRecorder

    _, _, rows = signals(
        tmp_path,
        orders=[
            dict(client_order_id="buy", stock_code="300214.SZ", side="BUY", quantity=0, target_value=10000, priority=20)
        ],
    )
    recorder = LiveRecorder(str(tmp_path / "ledger.db"), opening_cash=100000)
    recorder.record_batch(BATCH, "2026-10-27", "LIVE", 1)
    recorder.record_orders(BATCH, rows)
    w = World(auto, tmp_path)
    w.cash = 100000
    w.at("14:57:05")
    w.runner.step()
    w.fill(0)
    w.runner.step()
    assert FillImporter(tmp_path, recorder).import_fills() == 1
    assert recorder.get_positions()["300214.SZ"]["shares"] == 900


def test_preflight_session_may_change_before_first_trade(auto, tmp_path):
    signals(tmp_path)
    w = World(auto, tmp_path)
    w.runner.check()
    w.engine.session = "new-session"
    w.at("14:55:00")
    w.runner.step()
    assert len(w.broker.submissions) == 1
    assert w.runner.journal["session"] == "new-session"


def test_next_day_does_not_ignore_unresolved_journal(auto, tmp_path):
    from qmt_clipboard import Engine

    signals(tmp_path)
    w = World(auto, tmp_path)
    w.at("14:55:00")
    w.runner.step()
    tomorrow = dt.datetime(2026, 10, 28, 14, 55, tzinfo=ZONE)
    w.now = tomorrow
    w.engine = Engine(w.broker.submit, w.broker.query, session="fresh-day", now=lambda: tomorrow, snapshot=w.snapshot)
    signals(tmp_path, batch=BATCH.replace("20261027", "20261028"), date="2026-10-28")
    fresh = auto.AutoRelay(tmp_path, STRATEGY, tmp_path / "local_state", w.exchange, now=lambda: tomorrow)
    with pytest.raises(RuntimeError, match="previous"):
        fresh.step()
    assert len(w.broker.submissions) == 1


def test_success_status_with_lagging_volume_waits_for_complete_evidence(auto, tmp_path):
    signals(
        tmp_path,
        orders=[
            dict(client_order_id="sell", stock_code="301165.SZ", side="SELL", quantity=600, target_value=0, priority=10)
        ],
    )
    w = World(auto, tmp_path)
    w.at("14:55:00")
    w.runner.step()
    w.fill(0, quantity=300, status=56)
    w.at("15:00:00")
    w.runner.step()
    assert not list((tmp_path / "outbound").glob("fills_*.done"))
    w.broker.rows[0]["filled_quantity"] = 600
    w.runner.step()
    event = json.loads(next((tmp_path / "outbound").glob("fills_*.jsonl")).read_text())
    assert (event["status"], event["filled_qty"]) == ("FILLED", 600)
