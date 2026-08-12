from pathlib import Path

from live_trading.scripts.render_qmt_runtime import (
    render_main_source,
    render_pr49_source,
)


def test_main_runtime_render_binds_real_account_without_mutating_template(tmp_path):
    template = tmp_path / "qmt_signal_bridge.py"
    original = (
        'ACCOUNT_ID = ""\n'
        'STRATEGY_NAME = "qlib_bridge"\n'
        'ACCOUNT_ENVIRONMENT = "SIMULATION"\n'
        'ALLOW_REAL_MONEY = False\n'
        'REAL_EXPECTED_INITIAL_CASH = 1000000.0\n'
        'REAL_REQUIRE_EMPTY_POSITIONS = True\n'
        'MAX_ORDER_QUANTITY = 100\n'
    )
    template.write_text(original, encoding="utf-8")

    rendered = render_main_source(
        template.read_text(encoding="utf-8"), "1234567890", 999238.99,
    )

    assert template.read_text(encoding="utf-8") == original
    assert 'ACCOUNT_ID = "1234567890"' in rendered
    assert 'STRATEGY_NAME = "qlib_bridge_main"' in rendered
    assert 'ACCOUNT_ENVIRONMENT = "REAL"' in rendered
    assert "ALLOW_REAL_MONEY = True" in rendered
    assert "REAL_EXPECTED_INITIAL_CASH = 999238.99" in rendered
    assert "REAL_REQUIRE_EMPTY_POSITIONS = False" in rendered
    assert "MAX_ORDER_QUANTITY = 100" in rendered


def test_pr49_runtime_render_only_binds_account():
    source = (
        'ACCOUNT_ID = ""  # local only\n'
        'SUBMIT_START = "15:05:00"\n'
        'SUBMIT_END = "15:25:00"\n'
        'POLL_SECONDS = 3\n'
    )

    rendered = render_pr49_source(source, "1234567890")

    assert 'ACCOUNT_ID = "1234567890"  # local only' in rendered
    assert 'SUBMIT_START = "15:05:00"' in rendered
    assert 'SUBMIT_END = "15:25:00"' in rendered
    assert "POLL_SECONDS = 3" in rendered


def test_renderer_rejects_ambiguous_setting():
    source = 'ACCOUNT_ID = ""\nACCOUNT_ID = ""\n'

    try:
        render_pr49_source(source, "1234567890")
    except ValueError as exc:
        assert "ACCOUNT_ID" in str(exc)
    else:
        raise AssertionError("duplicate runtime setting must fail closed")
