"""Round-trip integration tests; the only fakes are clipboard, clock and broker."""

import importlib.util
import json
from pathlib import Path

import pytest

from test_qmt_clipboard import Broker, MemoryClipboard, NOW, request


DIRECTORY = Path(__file__).resolve().parents[2] / "live_trading/qmt_strategy"


@pytest.fixture
def relay(monkeypatch):
    source = DIRECTORY / "clipboard_relay.py"
    assert source.exists(), "clipboard relay has not been implemented"
    monkeypatch.syspath_prepend(str(DIRECTORY))
    spec = importlib.util.spec_from_file_location("clipboard_relay_test", source)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Clock:
    def __init__(self, callback=lambda: None):
        self.value = 0
        self.callback = callback

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds
        self.callback()


def test_round_trip_retries_lost_response_without_a_second_order(relay):
    from qmt_clipboard import Engine, Runtime

    broker = Broker()
    clip = MemoryClipboard()
    runtime = Runtime(clip, Engine(broker.submit, broker.query, session="session-a", now=lambda: NOW))
    count = [0]

    def tick():
        runtime.step()
        count[0] += 1
        if count[0] == 1:
            clip.text = "ordinary text overwrote the first response"

    clock = Clock(tick)
    reply = relay.exchange(clip, request(), timeout=5, interval=1, clock=clock.now, sleep=clock.sleep)
    assert reply["ok"]
    assert reply["orders"][0]["id"] == "20261027-001"
    assert len(broker.submissions) == 1


def test_relay_stops_on_new_session_instead_of_rebinding_trade(relay):
    clip = MemoryClipboard(
        json.dumps(
            dict(protocol="qlib-clipboard-v1", type="response", session="session-b", request_id="hello", ok=True)
        )
    )
    original = clip.text
    with pytest.raises(RuntimeError, match="session"):
        relay.exchange(clip, request())
    assert clip.text == original


def test_other_inflight_request_is_not_overwritten(relay):
    clip = MemoryClipboard(json.dumps(request(request_id="other")))
    original = clip.text
    with pytest.raises(RuntimeError, match="pending"):
        relay.exchange(clip, request())
    assert clip.text == original


@pytest.mark.parametrize("kind", ["poll", "snapshot"])
def test_abandoned_readonly_request_can_be_replaced(relay, kind):
    from qmt_clipboard import Engine, Runtime

    broker = Broker()
    clip = MemoryClipboard(json.dumps(request(type=kind, request_id="timed-out-query")))
    runtime = Runtime(clip, Engine(broker.submit, broker.query, session="session-a", now=lambda: NOW))
    clock = Clock(runtime.step)
    reply = relay.exchange(clip, request(type="poll"), timeout=5, clock=clock.now, sleep=clock.sleep)
    assert reply["ok"]
    assert not broker.submissions


def test_timeout_preserves_request_id_and_reports_uncertain_outcome(relay):
    clip = MemoryClipboard()
    clock = Clock()
    with pytest.raises(TimeoutError, match="unknown"):
        relay.exchange(clip, request(), timeout=2, interval=1, clock=clock.now, sleep=clock.sleep)
    assert json.loads(clip.text)["request_id"] == "req-1"
    assert json.loads(clip.text)["session"] == "session-a"


@pytest.mark.parametrize("kind", ["poll", "snapshot"])
def test_readonly_timeout_identifies_request_without_claiming_trade_uncertainty(relay, kind):
    clip = MemoryClipboard()
    clock = Clock()
    with pytest.raises(TimeoutError) as caught:
        relay.exchange(clip, request(type=kind), timeout=2, clock=clock.now, sleep=clock.sleep)
    message = str(caught.value)
    assert "request=" + kind in message
    assert "published=yes" in message
    assert "last_seen=pending request" in message
    assert "read-only request; no order was submitted by this request" in message
    assert "trade outcome unknown" not in message
    assert json.loads(clip.text)["request_id"] == "req-1"


def test_timeout_distinguishes_clipboard_access_failure_from_missing_qmt_response(relay):
    class BusyClipboard(MemoryClipboard):
        def read(self):
            raise OSError("clipboard is locked")

    clock = Clock()
    with pytest.raises(TimeoutError) as caught:
        relay.exchange(BusyClipboard(), request(type="poll"), timeout=2, clock=clock.now, sleep=clock.sleep)
    assert "published=no" in str(caught.value)
    assert "clipboard is locked" in str(caught.value)


def test_busy_clipboard_is_retried_without_changing_message(relay):
    from qmt_clipboard import Engine, Runtime

    class BusyClipboard(MemoryClipboard):
        reads = 0

        def read(self):
            self.reads += 1
            if self.reads == 1:
                raise OSError("busy")
            return super().read()

    clip = BusyClipboard()
    broker = Broker()
    runtime = Runtime(clip, Engine(broker.submit, broker.query, session="session-a", now=lambda: NOW))
    clock = Clock(runtime.step)
    assert relay.exchange(clip, request(), timeout=5, interval=1, clock=clock.now, sleep=clock.sleep)["ok"]
    assert len(broker.submissions) == 1


def test_output_is_snapshot_plus_deduplicated_log_records(relay, tmp_path):
    response = dict(
        protocol="qlib-clipboard-v1",
        type="response",
        session="session-a",
        request_id="req-1",
        ok=True,
        orders=[dict(id="o1", filled_quantity=40)],
        logs=[dict(seq=1, message="first"), dict(seq=2, message="second")],
    )
    relay.save_response(response, tmp_path)
    relay.save_response(response, tmp_path)
    response["orders"][0]["filled_quantity"] = 100
    response["logs"].append(dict(seq=3, message="filled"))
    relay.save_response(response, tmp_path)
    assert json.loads((tmp_path / "session-a.result.json").read_text())["orders"][0]["filled_quantity"] == 100
    logs = [json.loads(line) for line in (tmp_path / "session-a.log.jsonl").read_text().splitlines()]
    assert [line["seq"] for line in logs] == [1, 2, 3]


def test_send_requires_explicit_session_in_file_before_clipboard_access(relay, tmp_path, monkeypatch):
    cmd = request()
    del cmd["session"]
    source = tmp_path / "orders.json"
    source.write_text(json.dumps(cmd))

    def forbidden():
        pytest.fail("must reject unbound trade before touching clipboard")

    monkeypatch.setattr(relay, "Clipboard", forbidden)
    assert relay.main(["send", str(source), "--output-dir", str(tmp_path)]) == 1


def test_watch_requires_all_requested_orders_and_no_query_error(relay):
    snapshot = dict(orders=[dict(id="a", status="FILLED")], query_error="")
    assert not relay.finished(snapshot, ["a", "b"])
    assert relay.finished(snapshot, ["a"])
    snapshot["query_error"] = "offline"
    assert not relay.finished(snapshot, ["a"])


def test_leftover_response_cannot_hide_request_id_reuse_with_changed_orders(relay):
    from qmt_clipboard import Engine, Runtime

    broker = Broker()
    engine = Engine(broker.submit, broker.query, session="session-a", now=lambda: NOW)
    old_response = engine.handle(request())
    clip = MemoryClipboard(json.dumps(old_response))
    runtime = Runtime(clip, engine)
    clock = Clock(runtime.step)
    changed = request()
    changed["orders"][0]["id"] = "different-order"
    response = relay.exchange(clip, changed, timeout=5, interval=1, clock=clock.now, sleep=clock.sleep)
    assert not response["ok"]
    assert "request id reused" in response["error"]
    assert len(broker.submissions) == 1


def test_watch_empty_session_completes_without_waiting(relay, tmp_path, monkeypatch):
    from contextlib import nullcontext

    response = dict(
        protocol="qlib-clipboard-v1",
        type="response",
        session="session-a",
        request_id="hello",
        ok=True,
        orders=[],
        logs=[],
        query_error="",
    )
    monkeypatch.setattr(relay, "single_client", nullcontext)
    monkeypatch.setattr(relay, "Clipboard", MemoryClipboard)
    monkeypatch.setattr(relay, "exchange", lambda *args: response)
    monkeypatch.setattr(relay.time, "sleep", lambda _: pytest.fail("empty watch must finish immediately"))
    assert relay.main(["watch", "--session", "session-a", "--output-dir", str(tmp_path)]) == 0
