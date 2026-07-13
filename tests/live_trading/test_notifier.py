"""Notifier：payload 正确性、失败不抛异常、工厂降级。"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.notifier import (
    NullNotifier,
    PushPlusNotifier,
    ServerChanNotifier,
    create_notifier,
)


def _resp(status=200, payload=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload or {}
    resp.text = str(payload)
    return resp


@patch("live_trading.modules.notifier.requests.post")
def test_serverchan_payload(mock_post):
    mock_post.return_value = _resp(payload={"code": 0})
    assert ServerChanNotifier("SCTKEY").send("标题", "**内容**")
    url = mock_post.call_args.args[0]
    assert url == "https://sctapi.ftqq.com/SCTKEY.send"
    data = mock_post.call_args.kwargs["data"]
    assert data == {"title": "标题", "desp": "**内容**"}


@patch("live_trading.modules.notifier.requests.post")
def test_pushplus_payload(mock_post):
    mock_post.return_value = _resp(payload={"code": 200})
    assert PushPlusNotifier("TOKEN").send("标题", "内容")
    body = mock_post.call_args.kwargs["json"]
    assert body["token"] == "TOKEN"
    assert body["template"] == "markdown"


@patch("live_trading.modules.notifier.requests.post")
def test_http_error_returns_false(mock_post):
    mock_post.return_value = _resp(status=500)
    assert ServerChanNotifier("K").send("t", "c") is False


@patch("live_trading.modules.notifier.requests.post")
def test_business_error_returns_false(mock_post):
    mock_post.return_value = _resp(payload={"code": 40001})
    assert ServerChanNotifier("K").send("t", "c") is False
    mock_post.return_value = _resp(payload={"code": 600})
    assert PushPlusNotifier("T").send("t", "c") is False


@patch("live_trading.modules.notifier.requests.post")
def test_connection_error_not_raised(mock_post):
    mock_post.side_effect = requests.ConnectionError("boom")
    assert ServerChanNotifier("K").send("t", "c") is False
    assert PushPlusNotifier("T").send("t", "c") is False


def test_factory_none_channel():
    assert isinstance(create_notifier({"notify": {"channel": "none"}}), NullNotifier)
    assert isinstance(create_notifier({}), NullNotifier)


def test_factory_missing_env_degrades(monkeypatch):
    monkeypatch.delenv("SERVERCHAN_SENDKEY", raising=False)
    monkeypatch.delenv("PUSHPLUS_TOKEN", raising=False)
    assert isinstance(
        create_notifier({"notify": {"channel": "serverchan"}}), NullNotifier)
    assert isinstance(
        create_notifier({"notify": {"channel": "pushplus"}}), NullNotifier)


def test_factory_with_env(monkeypatch):
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "K1")
    n = create_notifier({"notify": {"channel": "serverchan"}})
    assert isinstance(n, ServerChanNotifier) and n.sendkey == "K1"
    monkeypatch.setenv("PUSHPLUS_TOKEN", "T1")
    n = create_notifier({"notify": {"channel": "pushplus"}})
    assert isinstance(n, PushPlusNotifier) and n.token == "T1"
