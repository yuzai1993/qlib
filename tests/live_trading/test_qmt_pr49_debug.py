import json

import pytest

from live_trading.qmt_strategy import qmt_pr49_debug as debug


@pytest.mark.parametrize(
    ("current_date", "current_time", "expected"),
    [
        ("2026-08-11", "23:00:00", "WAIT_DATE"),
        ("2026-08-12", "15:04:59", "WAIT_WINDOW"),
        ("2026-08-12", "15:05:00", "SUBMIT"),
        ("2026-08-12", "15:25:00", "SUBMIT"),
        ("2026-08-12", "15:25:01", "EXPIRE"),
        ("2026-08-13", "09:30:00", "EXPIRE"),
    ],
)
def test_request_action_enforces_trade_date_and_fixed_price_window(
    current_date, current_time, expected,
):
    request = {
        "request_id": "PR49B20260812688223",
        "trade_date": "2026-08-12",
        "side": "BUY",
        "stock_code": "688223.SH",
        "quantity": 100,
    }

    assert debug.request_action(request, current_date, current_time) == expected


def test_before_window_keeps_request_and_does_not_submit(tmp_path, monkeypatch):
    request_path = tmp_path / "request.json"
    event_path = tmp_path / "events.jsonl"
    request_path.write_text(json.dumps({
        "request_id": "PR49B20260812688223",
        "trade_date": "2026-08-12",
        "side": "BUY",
        "stock_code": "688223.SH",
        "quantity": 100,
    }))
    calls = []
    monkeypatch.setattr(debug, "REQUEST", str(request_path))
    monkeypatch.setattr(debug, "EVENT_LOG", str(event_path))
    monkeypatch.setattr(debug, "ACCOUNT_ID", "8890116049")
    monkeypatch.setattr(debug, "_now_parts", lambda: ("2026-08-12", "15:04:59"), raising=False)
    monkeypatch.setattr(debug, "passorder", lambda *args: calls.append(args), raising=False)
    monkeypatch.setattr(debug, "_LAST_WAIT_KEY", None, raising=False)

    debug.handlebar(object())
    debug.handlebar(object())

    assert request_path.is_file()
    assert not (tmp_path / "request.json.PR49B20260812688223.processed").exists()
    assert calls == []
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert [row["event"] for row in events] == ["WAIT_WINDOW"]


def test_pending_request_is_activated_only_by_new_strategy(tmp_path, monkeypatch):
    request_path = tmp_path / "request.json"
    pending_path = tmp_path / "request.pending.json"
    event_path = tmp_path / "events.jsonl"
    pending_path.write_text(json.dumps({
        "request_id": "PR49B20260812688223",
        "trade_date": "2026-08-12",
        "side": "BUY",
        "stock_code": "688223.SH",
        "quantity": 100,
    }))
    calls = []
    monkeypatch.setattr(debug, "REQUEST", str(request_path))
    monkeypatch.setattr(debug, "PENDING_REQUEST", str(pending_path), raising=False)
    monkeypatch.setattr(debug, "EVENT_LOG", str(event_path))
    monkeypatch.setattr(debug, "ACCOUNT_ID", "8890116049")
    monkeypatch.setattr(debug, "_now_parts", lambda: ("2026-08-11", "23:00:00"), raising=False)
    monkeypatch.setattr(debug, "passorder", lambda *args: calls.append(args), raising=False)
    monkeypatch.setattr(debug, "_LAST_WAIT_KEY", None, raising=False)

    debug.handlebar(object())

    assert not pending_path.exists()
    assert request_path.is_file()
    assert calls == []
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert [row["event"] for row in events] == ["REQUEST_ACTIVATED", "WAIT_DATE"]


def test_in_window_submits_once_and_preserves_attempt_arguments(tmp_path, monkeypatch):
    request_path = tmp_path / "request.json"
    event_path = tmp_path / "events.jsonl"
    request_path.write_text(json.dumps({
        "request_id": "PR49B20260812688223",
        "trade_date": "2026-08-12",
        "side": "BUY",
        "stock_code": "688223.SH",
        "quantity": 100,
    }))
    calls = []

    def fake_passorder(*args):
        calls.append(args)
        return 7

    monkeypatch.setattr(debug, "REQUEST", str(request_path))
    monkeypatch.setattr(debug, "EVENT_LOG", str(event_path))
    monkeypatch.setattr(debug, "ACCOUNT_ID", "8890116049")
    monkeypatch.setattr(debug, "_now_parts", lambda: ("2026-08-12", "15:05:01"), raising=False)
    monkeypatch.setattr(debug, "passorder", fake_passorder, raising=False)

    context = object()
    debug.handlebar(context)
    debug.handlebar(context)

    assert len(calls) == 1
    assert calls[0] == (
        23, 1101, "8890116049", "688223.SH", 49, 0, 100,
        "qlib_pr49_debug", 2, "PR49B20260812688223", context,
    )
    assert not request_path.exists()
    assert (tmp_path / "request.json.PR49B20260812688223.processed").is_file()
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert [row["event"] for row in events] == [
        "PASSORDER_ATTEMPT", "PASSORDER_RETURN",
    ]
    assert events[0]["passorder_arguments"] == {
        "account_id_masked": "******6049",
        "op_type": 23,
        "order_type": 1101,
        "price": 0,
        "prType": 49,
        "quantity": 100,
        "quick_trade": 2,
        "remark": "PR49B20260812688223",
        "stock_code": "688223.SH",
        "strategy_name": "qlib_pr49_debug",
    }
    assert events[1]["result"] == "7"


def test_expired_request_is_terminal_without_submission(tmp_path, monkeypatch):
    request_path = tmp_path / "request.json"
    event_path = tmp_path / "events.jsonl"
    request_path.write_text(json.dumps({
        "request_id": "PR49B20260812688223",
        "trade_date": "2026-08-12",
        "side": "BUY",
        "stock_code": "688223.SH",
        "quantity": 100,
    }))
    calls = []
    monkeypatch.setattr(debug, "REQUEST", str(request_path))
    monkeypatch.setattr(debug, "EVENT_LOG", str(event_path))
    monkeypatch.setattr(debug, "ACCOUNT_ID", "8890116049")
    monkeypatch.setattr(debug, "_now_parts", lambda: ("2026-08-12", "15:25:01"), raising=False)
    monkeypatch.setattr(debug, "passorder", lambda *args: calls.append(args), raising=False)

    debug.handlebar(object())

    assert calls == []
    assert not request_path.exists()
    assert (tmp_path / "request.json.PR49B20260812688223.processed").is_file()
    event = json.loads(event_path.read_text().splitlines()[0])
    assert event["event"] == "ERROR"
    assert event["error"] == "request expired outside prType=49 window"
