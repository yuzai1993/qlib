"""Exercise real executor/transport logic with a simulated broker and clipboard."""

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "live_trading/qmt_strategy/qmt_clipboard.py"
NOW = dt.datetime(2026, 10, 27, 6, 56, tzinfo=dt.timezone.utc)


@pytest.fixture
def mod():
    assert SOURCE.exists(), "clipboard executor has not been implemented"
    spec = importlib.util.spec_from_file_location("qmt_clipboard_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Broker:
    def __init__(self):
        self.submissions = []
        self.rows = []
        self.submit_error = None
        self.query_error = None

    def submit(self, order, remark):
        self.submissions.append((copy.deepcopy(order), remark))
        if self.submit_error:
            raise self.submit_error

    def query(self):
        if self.query_error:
            raise self.query_error
        return self.rows


class MemoryClipboard:
    def __init__(self, text=""):
        self.text = text

    def read(self):
        return self.text

    def replace(self, expected, text):
        if self.text != expected:
            return False
        self.text = text
        return True


def request(**changes):
    value = {
        "protocol": "qlib-clipboard-v1",
        "type": "trade",
        "session": "session-a",
        "request_id": "req-1",
        "expires_at": "2026-10-27T14:58:00+08:00",
        "orders": [{"id": "20261027-001", "symbol": "600000.SH", "side": "BUY", "quantity": 100, "price": 10.25}],
    }
    value.update(changes)
    return value


def engine(mod, broker):
    return mod.Engine(broker.submit, broker.query, session="session-a", now=lambda: NOW)


def test_snapshot_roundtrip_is_read_only_and_advertised(mod):
    broker = Broker()
    executor = mod.Engine(
        broker.submit,
        broker.query,
        session="session-a",
        now=lambda: NOW,
        snapshot=lambda symbols: {
            "account": {"available_cash": 9812.65},
            "positions": [],
            "quotes": {symbols[0]: {"last": 77}},
        },
    )
    clip = MemoryClipboard(json.dumps(request(type="snapshot", symbols=["301165.SZ"])))
    mod.Runtime(clip, executor).step()
    reply = json.loads(clip.text)
    assert reply["ok"]
    assert "snapshot-v1" in reply["capabilities"]
    assert reply["snapshot"]["account"]["available_cash"] == 9812.65
    assert reply["snapshot"]["quotes"]["301165.SZ"]["last"] == 77
    assert not broker.submissions


def test_runtime_exposes_unserializable_response_in_console_without_repeated_spam(mod, capsys):
    broker = Broker()
    executor = mod.Engine(
        broker.submit, broker.query, session="session-a", now=lambda: NOW, snapshot=lambda symbols: {"bad": object()}
    )
    clip = MemoryClipboard(json.dumps(request(type="snapshot", symbols=[])))
    runtime = mod.Runtime(clip, executor)
    runtime.step()
    first = capsys.readouterr().out
    assert "clipboard polling active" in first
    assert "request=snapshot" in first
    assert "not JSON serializable" in first
    runtime.step()
    assert "not JSON serializable" not in capsys.readouterr().out
    assert not broker.submissions


def test_snapshot_rejects_wrong_account_and_missing_positions(mod, monkeypatch):
    account = SimpleNamespace(m_strAccountID="wrong", m_dAvailable=100)
    monkeypatch.setattr(mod, "get_trade_detail_data", lambda *args: [account], raising=False)
    with pytest.raises(ValueError, match="account"):
        mod.QmtBroker(SimpleNamespace(), "local").snapshot([])
    account.m_strAccountID = "local"
    monkeypatch.setattr(mod, "get_trade_detail_data", lambda *args: [account] if args[-1] == "ACCOUNT" else None)
    with pytest.raises(ValueError, match="POSITION"):
        mod.QmtBroker(SimpleNamespace(), "local").snapshot([])


def test_snapshot_normalizes_qmt_fields(mod, monkeypatch):
    account = SimpleNamespace(
        m_strAccountID="local", m_dAvailable=9812.65, m_dBalance=253727.65, m_dInstrumentValue=243915, m_dFrozenCash=0
    )
    position = SimpleNamespace(
        m_strInstrumentID="301165",
        m_strExchangeID="SZ",
        m_nVolume=600,
        m_nCanUseVolume=600,
        m_nFrozenVolume=0,
        m_dOpenPrice=81.83,
        m_dMarketValue=46200,
    )
    monkeypatch.setattr(
        mod, "get_trade_detail_data", lambda *args: [account] if args[-1] == "ACCOUNT" else [position], raising=False
    )
    context = SimpleNamespace(
        get_full_tick=lambda codes: {codes[0]: {"lastPrice": 77, "timetag": "20261027 14:55:00"}},
        get_instrument_detail=lambda code: {"UpStopPrice": 92.4, "DownStopPrice": 61.6},
    )
    data = mod.QmtBroker(context, "local").snapshot(["301165.SZ"])
    assert data["account"]["available_cash"] == 9812.65
    assert data["positions"][0]["stock_code"] == "301165.SZ"
    assert data["positions"][0]["can_use_volume"] == 600
    assert data["quotes"]["301165.SZ"] == {"last": 77.0, "timetag": "20261027 14:55:00", "upper": 92.4, "lower": 61.6}


@pytest.mark.parametrize("status,filled,expected", [(57, 0, "REJECTED"), (54, 0, "CANCELED"), (53, 40, "CANCELED")])
def test_official_order_volume_field_finalizes_cancels_and_rejections(mod, monkeypatch, status, filled, expected):
    records = []
    monkeypatch.setattr(mod, "get_trade_detail_data", lambda *args: records, raising=False)
    broker = mod.QmtBroker(SimpleNamespace(), "local")
    executor = mod.Engine(lambda *args: None, broker.query, session="session-a", now=lambda: NOW)
    response = executor.handle(request())
    records.append(
        SimpleNamespace(
            m_strRemark=response["orders"][0]["remark"],
            m_strOrderSysID="official",
            m_nVolumeTotalOriginal=100,
            m_nVolumeTraded=filled,
            m_dTradedPrice=10.25 if filled else 0,
            m_nOrderStatus=status,
            m_strErrorMsg="",
        )
    )
    result = executor.handle(request(type="poll"))["orders"][0]
    assert result["status"] == expected
    assert result["filled_quantity"] == filled


def test_new_day_rotates_only_settled_session(mod):
    broker = Broker()
    moment = [NOW]
    executor = mod.Engine(broker.submit, broker.query, session="session-a", now=lambda: moment[0])
    reply = executor.handle(request())
    moment[0] += dt.timedelta(days=1)
    poll = request(type="poll", session=None)
    blocked = executor.handle(poll)
    assert blocked["session"] == "session-a"
    assert blocked["day"] == "2026-10-27"
    broker.rows = [row(reply["orders"][0]["remark"], filled_quantity=100, status=56)]
    executor.handle(poll)
    fresh = executor.handle(poll)
    assert fresh["session"] != "session-a"
    assert fresh["day"] == "2026-10-28"
    assert fresh["orders"] == []


def row(remark, **changes):
    value = dict(
        remark=remark,
        broker_order_id="broker-1",
        quantity=100,
        filled_quantity=40,
        average_price=10.1,
        status=55,
        error="",
    )
    value.update(changes)
    return value


def test_trade_is_once_per_order_even_with_new_request_id(mod):
    broker = Broker()
    runner = engine(mod, broker)
    first = runner.handle(request())
    again = runner.handle(request(request_id="req-2"))
    assert first["ok"] and again["ok"]
    assert len(broker.submissions) == 1
    assert again["orders"][0]["status"] == "SUBMITTED"
    assert again["orders"][0]["filled_quantity"] == 0
    assert len(broker.submissions[0][1]) <= 24


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", 0),
        ("quantity", True),
        ("quantity", 100.5),
        ("price", float("nan")),
        ("price", float("inf")),
        ("price", -1),
        ("price", True),
        ("symbol", "600000"),
        ("side", "HOLD"),
        ("id", ""),
        ("symbol", "600000.SH\n"),
        ("id", "order\n"),
    ],
)
def test_entire_request_is_validated_before_any_submission(mod, field, value):
    broker = Broker()
    runner = engine(mod, broker)
    cmd = request()
    bad = dict(cmd["orders"][0], id="second")
    bad[field] = value
    cmd["orders"].append(bad)
    result = runner.handle(cmd)
    assert not result["ok"]
    assert broker.submissions == []


@pytest.mark.parametrize(
    "changes",
    [
        {"session": "old-session"},
        {"session": ""},
        {"expires_at": "2026-10-27T14:55:00+08:00"},
        {"expires_at": "2026-10-27T14:58:00"},
        {"orders": []},
        {"request_id": ""},
    ],
)
def test_invalid_envelope_never_submits(mod, changes):
    broker = Broker()
    assert not engine(mod, broker).handle(request(**changes))["ok"]
    assert broker.submissions == []


def test_reused_order_or_request_id_with_changed_content_is_rejected(mod):
    broker = Broker()
    runner = engine(mod, broker)
    runner.handle(request())
    changed = request(request_id="req-2")
    changed["orders"][0]["price"] = 9
    assert not runner.handle(changed)["ok"]
    changed = request()
    changed["orders"][0]["id"] = "other"
    assert not runner.handle(changed)["ok"]
    assert len(broker.submissions) == 1


def test_uncertain_submit_is_not_retried_and_can_later_be_reconciled(mod):
    broker = Broker()
    broker.submit_error = RuntimeError("reply lost after send")
    runner = engine(mod, broker)
    assert runner.handle(request())["orders"][0]["status"] == "UNKNOWN"
    runner.handle(request())
    assert len(broker.submissions) == 1
    remark = broker.submissions[0][1]
    broker.rows = [row(remark, filled_quantity=100, status=56)]
    result = runner.handle(request(type="poll"))
    assert result["orders"][0]["status"] == "FILLED"
    assert result["orders"][0]["filled_quantity"] == 100


def test_cumulative_fills_do_not_double_count_or_regress(mod):
    broker = Broker()
    runner = engine(mod, broker)
    runner.handle(request())
    remark = broker.submissions[0][1]
    broker.rows = [row(remark), row("unrelated", filled_quantity=100, status=56)]
    for _ in range(2):
        result = runner.handle(request(type="poll"))
        assert result["orders"][0]["filled_quantity"] == 40
    broker.rows = [row(remark, filled_quantity=0, status=50)]
    result = runner.handle(request(type="poll"))
    assert result["orders"][0]["filled_quantity"] == 40
    assert result["orders"][0]["status"] == "PARTIAL"
    broker.query_error = RuntimeError("offline")
    result = runner.handle(request(type="poll"))
    assert result["query_error"] == "offline"
    assert result["orders"][0]["filled_quantity"] == 40


def test_split_children_are_aggregated_and_missing_child_does_not_end_order(mod):
    broker = Broker()
    runner = engine(mod, broker)
    runner.handle(request())
    remark = broker.submissions[0][1]
    broker.rows = [
        row(remark, quantity=50, filled_quantity=50, status=56),
        row(remark, broker_order_id="broker-2", quantity=50, filled_quantity=0, status=50),
    ]
    assert runner.handle(request(type="poll"))["orders"][0]["status"] == "PARTIAL"
    broker.rows = broker.rows[:1]
    result = runner.handle(request(type="poll"))["orders"][0]
    assert result["status"] == "PARTIAL"
    assert result["filled_quantity"] == 50
    broker.rows = [row(remark, broker_order_id="broker-2", quantity=50, filled_quantity=20, status=53)]
    result = runner.handle(request(type="poll"))["orders"][0]
    assert result["status"] == "CANCELED"
    assert result["filled_quantity"] == 70
    assert result["average_price"] == pytest.approx(10.1)


def test_discovery_does_not_trade_and_old_session_cannot_run_after_restart(mod):
    broker = Broker()
    runner = engine(mod, broker)
    poll = dict(protocol="qlib-clipboard-v1", type="poll", request_id="hello")
    assert runner.handle(poll)["session"] == "session-a"
    other = mod.Engine(broker.submit, broker.query, session="session-b", now=lambda: NOW)
    assert not other.handle(request())["ok"]
    assert broker.submissions == []


def test_runtime_ignores_unrelated_clipboard_and_deduplicates_lost_response(mod):
    broker = Broker()
    runner = engine(mod, broker)
    clip = MemoryClipboard("ordinary copy")
    runtime = mod.Runtime(clip, runner)
    runtime.step()
    assert clip.text == "ordinary copy"
    original = json.dumps(request())
    clip.text = original
    runtime.step()
    assert json.loads(clip.text)["type"] == "response"
    clip.text = original
    runtime.step()
    assert len(broker.submissions) == 1


def test_runtime_does_not_overwrite_new_clipboard_content(mod):
    broker = Broker()
    clip = MemoryClipboard(json.dumps(request()))

    def submit(order, remark):
        broker.submit(order, remark)
        clip.text = "user copied something else"

    runtime = mod.Runtime(clip, mod.Engine(submit, broker.query, session="session-a", now=lambda: NOW))
    runtime.step()
    assert clip.text == "user copied something else"
    assert len(broker.submissions) == 1


def test_qmt_adapter_uses_fixed_limit_price_and_exact_broker_fields(mod, monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "passorder", lambda *args: calls.append(args), raising=False)
    raw = SimpleNamespace(
        m_strRemark="remark",
        m_strOrderSysID="b1",
        m_nOrderVolume=100,
        m_nVolumeTraded=40,
        m_dTradedPrice=10.1,
        m_nOrderStatus=55,
        m_strErrorMsg="",
    )
    monkeypatch.setattr(mod, "get_trade_detail_data", lambda *args: [raw], raising=False)
    context = object()
    adapter = mod.QmtBroker(context, "test-account")
    adapter.submit(request()["orders"][0], "remark")
    assert calls[0][:7] == (23, 1101, "test-account", "600000.SH", 11, 10.25, 100)
    assert calls[0][-3:] == (2, "remark", context)
    assert adapter.query() == [row("remark", broker_order_id="b1")]


def test_logs_are_bounded_and_have_increasing_sequence(mod):
    runner = engine(mod, Broker())
    for n in range(150):
        runner.log("INFO", "line %d" % n)
    logs = runner.handle(request(type="poll"))["logs"]
    assert len(logs) == 100
    assert logs[-1]["seq"] - logs[0]["seq"] == 99


def test_expiry_is_rechecked_between_slow_submissions(mod):
    now = [NOW]
    broker = Broker()

    def submit(order, remark):
        broker.submit(order, remark)
        now[0] += dt.timedelta(minutes=3)

    runner = mod.Engine(submit, broker.query, session="session-a", now=lambda: now[0])
    cmd = request()
    cmd["orders"].append(dict(cmd["orders"][0], id="second"))
    response = runner.handle(cmd)
    assert len(broker.submissions) == 1
    assert response["orders"][1]["status"] == "EXPIRED"


def test_nonfinite_broker_values_do_not_poison_result_serialization(mod):
    broker = Broker()
    runner = engine(mod, broker)
    runner.handle(request())
    broker.rows = [row(broker.submissions[0][1], average_price=float("nan"))]
    response = runner.handle(request(type="poll"))
    assert response["query_error"]
    assert response["orders"][0]["filled_quantity"] == 0
    assert mod.dumps(response)


def test_qmt_init_and_timer_work_without_any_file_access(mod, monkeypatch):
    import builtins

    clip = MemoryClipboard()
    monkeypatch.setattr(mod, "ACCOUNT_ID", "test-account")
    monkeypatch.setattr(mod, "Clipboard", lambda: clip)
    monkeypatch.setattr(mod, "get_trade_detail_data", lambda *args: [], raising=False)
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: pytest.fail("QMT attempted file I/O"))

    class Context:
        def set_account(self, account):
            self.account = account

        def schedule_run(self, callback, first, repeat, interval, name):
            self.callback = callback
            self.interval = interval

    context = Context()
    mod.init(context)
    assert context.account == "test-account"
    assert context.interval == dt.timedelta(seconds=1)
    clip.text = json.dumps(dict(protocol="qlib-clipboard-v1", type="poll", request_id="hello"))
    context.callback(context)
    assert json.loads(clip.text)["session"] == mod._runtime.engine.session
    # QMT also expects a handlebar entry point; it must not create a second engine.
    mod.handlebar(context)


class NativeMemory:
    """A tiny handle-owning Win32 boundary, using real ctypes buffers."""

    def __init__(self, text):
        import ctypes

        self.ctypes = ctypes
        self.buffers = {1: ctypes.create_string_buffer(text.encode("utf-16-le") + b"\x00\x00")}
        self.current = 1
        self.closed = 0
        self.destroyed = 0
        self.freed = []
        self.unlocked = []
        self.emptied = 0

    def OpenClipboard(self, owner):
        return True

    def CloseClipboard(self):
        self.closed += 1

    def IsClipboardFormatAvailable(self, fmt):
        return True

    def GetClipboardData(self, fmt):
        return self.current

    def GlobalSize(self, handle):
        # create_string_buffer added one byte after the explicit UTF-16 terminator.
        return len(self.buffers[handle]) - 1

    def GlobalLock(self, handle):
        return self.ctypes.addressof(self.buffers[handle])

    def GlobalUnlock(self, handle):
        self.unlocked.append(handle)

    def CreateWindowExW(self, *args):
        return 99

    def DestroyWindow(self, handle):
        self.destroyed += 1

    def GlobalAlloc(self, flags, size):
        self.buffers[2] = self.ctypes.create_string_buffer(size + 1)
        return 2

    def GlobalFree(self, handle):
        self.freed.append(handle)

    def EmptyClipboard(self):
        self.emptied += 1
        return True

    def SetClipboardData(self, fmt, handle):
        self.current = handle
        return handle


def test_native_clipboard_copies_unicode_and_transfers_memory_ownership(mod):
    native = NativeMemory("你好")
    clip = object.__new__(mod.Clipboard)
    clip.user = clip.kernel = native
    assert clip.read() == "你好"
    assert clip.replace("你好", "成交结果")
    assert clip.read() == "成交结果"
    assert native.freed == []  # Successful SetClipboardData owns the allocation.
    assert native.closed == 3
    assert native.destroyed == 1
    assert 1 in native.unlocked and 2 in native.unlocked


def test_native_compare_replace_preserves_new_content_and_releases_lock(mod):
    native = NativeMemory("new content")
    clip = object.__new__(mod.Clipboard)
    clip.user = clip.kernel = native
    assert not clip.replace("old content", "response")
    assert native.emptied == 0
    assert native.closed == 1
    assert native.destroyed == 1


def test_max_session_with_split_orders_and_unicode_errors_fits_transport(mod):
    broker = Broker()

    def query():
        return [
            row(
                remark,
                broker_order_id="%s-%s" % (index, child),
                quantity=quantity,
                filled_quantity=0,
                average_price=0,
                status=57,
                error="错误" * 150,
            )
            for index, (_, remark) in enumerate(broker.submissions)
            for child, quantity in enumerate((30, 30, 40))
        ]

    class BoundedClipboard(MemoryClipboard):
        def replace(self, expected, text):
            if len(text.encode("utf-16-le")) > mod.MAX_TEXT_CHARS * 2:
                raise ValueError("clipboard output too large")
            return super().replace(expected, text)

    runner = mod.Engine(broker.submit, query, session="session-a", now=lambda: NOW)
    clip = BoundedClipboard()
    runtime = mod.Runtime(clip, runner)
    for batch in range(5):
        cmd = request(request_id="req-%s" % batch)
        cmd["orders"] = [dict(cmd["orders"][0], id="order-%s-%s" % (batch, n)) for n in range(40)]
        clip.text = json.dumps(cmd)
        runtime.step()
    response = json.loads(clip.text)
    assert response["type"] == "response"
    assert response["ok"]
    assert len(response["orders"]) == 200
    assert all(item["status"] == "REJECTED" and len(item["broker_orders"]) == 3 for item in response["orders"])


def test_oversized_snapshot_returns_explicit_error_instead_of_timing_out(mod, monkeypatch):
    broker = Broker()
    runner = engine(mod, broker)
    for _ in range(100):
        runner.log("INFO", "x" * 300)
    monkeypatch.setattr(mod, "MAX_TEXT_CHARS", 4096)
    clip = MemoryClipboard(json.dumps(request()))
    mod.Runtime(clip, runner).step()
    response = json.loads(clip.text)
    assert not response["ok"]
    assert "size" in response["error"]
    assert len(clip.text.encode("utf-16-le")) <= 4096 * 2
