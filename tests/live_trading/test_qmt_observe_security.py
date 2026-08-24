"""Orderless QMT observation helpers — no broker session required."""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OBSERVE_PATH = REPO_ROOT / "live_trading" / "qmt_strategy" / "qmt_observe_security.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "qmt_observe_security", OBSERVE_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_true_after_hours_flag_is_eligible():
    observe = _load()
    assert observe.eligible_from_detail({"IsAfterHoursTrading": True}) is True
    assert observe.eligible_from_detail({"AfterHoursTrading": 1}) is True
    assert observe.eligible_from_detail({"FixedPriceTrading": "yes"}) is True


def test_missing_flag_is_unknown_not_false():
    observe = _load()
    assert observe.eligible_from_detail({"InstrumentID": "688001.SH"}) is None


def test_timetag_at_or_after_fifteen_is_final():
    observe = _load()
    assert observe.close_is_final({"timetag": "20260825 15:00:00"}) is True
    assert observe.close_is_final({"timetag": "20260825 14:59:59"}) is False
    assert observe.close_is_final({"lastPrice": 10.0}) is None


def test_observe_script_has_no_order_verbs():
    text = OBSERVE_PATH.read_text(encoding="utf-8", errors="ignore").lower()
    for verb in ("passorder", "order_stock", "opentradestock"):
        assert verb not in text
