"""build_snapshot：估值、收益、缺价降级、首日边界、turnover、出入金剔除。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.snapshot import build_snapshot, sum_live_fills_amount


POSITIONS = {
    "600000.SH": {"shares": 800, "avg_cost": 10.0},
    "000001.SZ": {"shares": 500, "avg_cost": 12.0},
}
PRICES = {"600000.SH": 11.0, "000001.SZ": 12.5}
# mv = 800*11 + 500*12.5 = 8800 + 6250 = 15050


def test_valuation_and_weight():
    daily, rows, missing = build_snapshot(
        "2026-07-13", POSITIONS, cash=10000.0, prices=PRICES,
        bench_close=4000.0, prev_snapshot=None, fills_amount=0.0,
    )
    assert missing == []
    assert daily["market_value"] == pytest.approx(15050.0)
    assert daily["total_value"] == pytest.approx(25050.0)
    assert daily["position_count"] == 2
    assert sum(r["weight"] for r in rows) == pytest.approx(15050.0 / 25050.0)
    sh = next(r for r in rows if r["stock_code"] == "600000.SH")
    assert sh["profit"] == pytest.approx(800.0)


def test_first_day_returns():
    daily, _, _ = build_snapshot(
        "2026-07-13", POSITIONS, 10000.0, PRICES, 4000.0,
        prev_snapshot=None, fills_amount=0.0,
    )
    assert daily["daily_return"] is None
    assert daily["cumulative_return"] == 0.0
    assert daily["benchmark_daily_return"] is None
    assert daily["benchmark_cumulative_return"] == 0.0
    assert daily["excess_return"] is None


def test_second_day_returns():
    prev = {
        "total_value": 25050.0,
        "cumulative_return": 0.0,
        "benchmark_close": 4000.0,
        "benchmark_cumulative_return": 0.0,
    }
    daily, _, _ = build_snapshot(
        "2026-07-14", POSITIONS, 10000.0,
        {"600000.SH": 11.55, "000001.SZ": 12.5},  # mv=9240+6250=15490, total=25490
        bench_close=4040.0, prev_snapshot=prev, fills_amount=0.0,
    )
    assert daily["daily_return"] == pytest.approx(25490.0 / 25050.0 - 1)
    assert daily["cumulative_return"] == pytest.approx(25490.0 / 25050.0 - 1)
    assert daily["benchmark_daily_return"] == pytest.approx(0.01)
    assert daily["benchmark_cumulative_return"] == pytest.approx(0.01)
    assert daily["excess_return"] == pytest.approx(
        daily["daily_return"] - 0.01)


def test_cumulative_return_chains_across_days():
    prev = {
        "total_value": 25490.0,
        "cumulative_return": 25490.0 / 25050.0 - 1,
        "benchmark_close": 4040.0,
        "benchmark_cumulative_return": 0.01,
    }
    daily, _, _ = build_snapshot(
        "2026-07-15", POSITIONS, 10000.0,
        {"600000.SH": 11.55, "000001.SZ": 12.5},  # total 不变 25490
        bench_close=4040.0, prev_snapshot=prev, fills_amount=0.0,
    )
    assert daily["daily_return"] == pytest.approx(0.0)
    assert daily["cumulative_return"] == pytest.approx(25490.0 / 25050.0 - 1)


def test_external_flow_excluded_from_returns():
    """入金 10 万当天：总资产涨，但日收益/累计收益不把入金算作业绩。"""
    prev = {
        "total_value": 25050.0,
        "cumulative_return": 0.10,
        "benchmark_close": 4000.0,
        "benchmark_cumulative_return": 0.0,
    }
    daily, _, _ = build_snapshot(
        "2026-07-14", POSITIONS, 110000.0, PRICES,  # 现金多了 10 万入金
        bench_close=4000.0, prev_snapshot=prev, fills_amount=0.0,
        external_flow=100000.0,
    )
    # (125050 - 100000) / 25050 - 1 = 0
    assert daily["daily_return"] == pytest.approx(0.0)
    assert daily["cumulative_return"] == pytest.approx(0.10)
    assert daily["external_flow"] == pytest.approx(100000.0)
    assert daily["total_value"] == pytest.approx(125050.0)


def test_fees_passthrough():
    daily, _, _ = build_snapshot(
        "2026-07-13", POSITIONS, 10000.0, PRICES, 4000.0, None,
        fills_amount=0.0, fees=123.45,
    )
    assert daily["fees"] == pytest.approx(123.45)


def test_corporate_assets_and_tax_provision_affect_nav_not_cash():
    daily, _, missing = build_snapshot(
        "2026-07-15", POSITIONS, 10000.0,
        {**PRICES, "600036.SH": 30.0}, 4000.0, None,
        fills_amount=0.0, receivables=500.0,
        pending_shares={"600036.SH": 100}, tax_provision=100.0,
    )
    assert missing == []
    assert daily["cash"] == pytest.approx(10000.0)
    assert daily["receivables"] == pytest.approx(500.0)
    assert daily["pending_market_value"] == pytest.approx(3000.0)
    assert daily["tax_provision"] == pytest.approx(100.0)
    assert daily["total_value"] == pytest.approx(
        10000.0 + 15050.0 + 500.0 + 3000.0 - 100.0
    )


def test_missing_price_degrades_to_cost():
    daily, rows, missing = build_snapshot(
        "2026-07-13", POSITIONS, 10000.0,
        {"600000.SH": 11.0},  # 000001.SZ 缺价
        4000.0, None, 0.0,
    )
    assert missing == ["000001.SZ"]
    sz = next(r for r in rows if r["stock_code"] == "000001.SZ")
    assert sz["close_price"] is None
    assert sz["market_value"] == pytest.approx(500 * 12.0)
    assert sz["profit"] == 0.0
    assert daily["market_value"] == pytest.approx(8800.0 + 6000.0)


def test_benchmark_missing_keeps_account_fields():
    daily, _, _ = build_snapshot(
        "2026-07-13", POSITIONS, 10000.0, PRICES,
        bench_close=None, prev_snapshot=None, fills_amount=0.0,
    )
    assert daily["benchmark_close"] is None
    assert daily["benchmark_cumulative_return"] is None
    assert daily["total_value"] == pytest.approx(25050.0)


def test_turnover():
    daily, _, _ = build_snapshot(
        "2026-07-13", POSITIONS, 10000.0, PRICES, 4000.0, None,
        fills_amount=5010.0,
    )
    assert daily["turnover"] == pytest.approx(5010.0 / 25050.0)


def test_cash_only_account():
    daily, rows, missing = build_snapshot(
        "2026-07-13", {}, 500000.0, {}, 4000.0, None, 0.0,
    )
    assert rows == [] and missing == []
    assert daily["total_value"] == 500000.0
    assert daily["position_count"] == 0


def test_sum_live_fills_amount():
    fills = [
        {"mode": "LIVE", "status": "FILLED", "filled_qty": 100, "avg_price": 10.0},
        {"mode": "LIVE", "status": "PARTIAL", "filled_qty": 200, "avg_price": 5.0},
        {"mode": "LIVE", "status": "REJECTED", "filled_qty": 0, "avg_price": 0.0},
        {"mode": "SIMULATE", "status": "FILLED", "filled_qty": 100, "avg_price": 10.0},
    ]
    assert sum_live_fills_amount(fills) == pytest.approx(1000.0 + 1000.0)
