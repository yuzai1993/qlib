from pathlib import Path

import pytest

from live_trading.scripts.render_qmt_runtime import (
    render_main_source,
    render_pr49_source,
)

# _replace_setting 要求每个设置在源码里恰好出现一次，所以夹具必须列全。
# 这里的值刻意与仓库模板一致（保守形态），断言才能证明渲染真的改变了它们。
MAIN_TEMPLATE = (
    'ACCOUNT_ID = ""\n'
    'STRATEGY_NAME = "qlib_bridge"\n'
    'ACCOUNT_ENVIRONMENT = "SIMULATION"\n'
    'ALLOW_REAL_MONEY = False\n'
    'REAL_EXPECTED_INITIAL_CASH = 1000000.0\n'
    'REAL_REQUIRE_EMPTY_POSITIONS = True\n'
    'EXECUTION_PROFILE = "CLOSE_AUCTION"\n'
    'ENABLE_LADDER_NETTING = False\n'
    'MAX_ORDER_QUANTITY = 100\n'
)


def test_main_runtime_render_binds_real_account_without_mutating_template(tmp_path):
    template = tmp_path / "qmt_signal_bridge.py"
    template.write_text(MAIN_TEMPLATE, encoding="utf-8")

    rendered = render_main_source(
        template.read_text(encoding="utf-8"), "1234567890", 999238.99,
    )

    assert template.read_text(encoding="utf-8") == MAIN_TEMPLATE
    assert 'ACCOUNT_ID = "1234567890"' in rendered
    assert 'STRATEGY_NAME = "qlib_bridge_main"' in rendered
    assert 'ACCOUNT_ENVIRONMENT = "REAL"' in rendered
    assert "ALLOW_REAL_MONEY = True" in rendered
    assert "REAL_EXPECTED_INITIAL_CASH = 999238.99" in rendered
    assert "REAL_REQUIRE_EMPTY_POSITIONS = False" in rendered
    assert "MAX_ORDER_QUANTITY = 0" in rendered


def test_rendered_runtime_turns_netting_on():
    """默认 False 且不渲染的话，计划二的抵销在生产里一行都不会跑。"""
    rendered = render_main_source(MAIN_TEMPLATE, "12345678", 100000.0)
    assert "ENABLE_LADDER_NETTING = True" in rendered
    assert "ENABLE_LADDER_NETTING = False" not in rendered


def test_rendered_runtime_selects_the_after_hours_channel():
    rendered = render_main_source(MAIN_TEMPLATE, "12345678", 100000.0)
    assert 'EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"' in rendered


def test_rendered_runtime_lifts_the_one_lot_cap():
    """真阶梯单笔约 6 万元，一手闸会把每层砍成 100 股。"""
    rendered = render_main_source(MAIN_TEMPLATE, "12345678", 100000.0)
    assert "MAX_ORDER_QUANTITY = 0" in rendered
    assert "MAX_ORDER_QUANTITY = 100" not in rendered


def test_the_channel_can_be_overridden_for_a_rollback_render():
    rendered = render_main_source(
        MAIN_TEMPLATE, "12345678", 100000.0,
        execution_profile="CLOSE_AUCTION", enable_ladder_netting=False,
    )
    assert 'EXECUTION_PROFILE = "CLOSE_AUCTION"' in rendered
    assert "ENABLE_LADDER_NETTING = False" in rendered


def test_an_unknown_execution_profile_is_rejected():
    with pytest.raises(ValueError):
        render_main_source(MAIN_TEMPLATE, "12345678", 100000.0,
                           execution_profile="MARKET_ON_OPEN")


def test_a_negative_order_cap_is_rejected():
    with pytest.raises(ValueError):
        render_main_source(MAIN_TEMPLATE, "12345678", 100000.0,
                           max_order_quantity=-1)


def test_the_real_bridge_template_is_renderable_and_conservative():
    """夹具会漂移，真模板不会。直接拿仓库里的 bridge 源码渲染一遍。

    _replace_setting 要求每个设置恰好出现一次——这条测试同时守住「模板里没有重复
    的模块级赋值」这个前提，那是渲染在装机当天唯一会硬失败的地方。
    """
    bridge = (
        Path(__file__).resolve().parents[2]
        / "live_trading" / "qmt_strategy" / "qmt_signal_bridge.py"
    ).read_text(encoding="utf-8")
    assert 'EXECUTION_PROFILE = "CLOSE_AUCTION"' in bridge
    assert "ENABLE_LADDER_NETTING = False" in bridge
    assert "MAX_ORDER_QUANTITY = 100" in bridge

    rendered = render_main_source(bridge, "12345678", 100000.0)

    assert 'EXECUTION_PROFILE = "AFTER_HOURS_FIXED_PRICE"' in rendered
    assert "ENABLE_LADDER_NETTING = True" in rendered
    assert "MAX_ORDER_QUANTITY = 0" in rendered


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
