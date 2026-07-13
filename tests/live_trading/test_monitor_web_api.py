"""监控 Web API 冒烟：临时 db 灌数据，逐端点断言。"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from live_trading.modules.fees import DEFAULT_FEES, order_total_fee
from live_trading.modules.fill_importer import LiveRecorder
from live_trading.modules.monitor_store import MonitorStore
from live_trading.modules.signal_schema import FillEvent
from live_trading.web.app import create_app

BATCH = "20260713_csi300_topk10_001"


def _fill_event(coid, status="FILLED", side="BUY", code="600000.SH",
                qty=800, price=10.5):
    return FillEvent(
        batch_id=BATCH, client_order_id=coid, mode="LIVE", stock_code=code,
        side=side, status=status, requested_qty=qty, filled_qty=qty,
        avg_price=price, qmt_order_id="1", message="", ts="2026-07-13T10:00:00",
    )


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "live.db"
    recorder = LiveRecorder(str(db))
    recorder.set_cash(100000.0)
    recorder.record_batch(BATCH, "2026-07-13", "LIVE", planned_orders=2)
    recorder.apply_fill(_fill_event("20260713001B"))
    recorder.apply_fill(_fill_event("20260713002B", code="000001.SZ", qty=500,
                                    price=12.0))

    store = MonitorStore(str(db))
    store.upsert_daily_snapshot({
        "date": "2026-07-12", "cash": 114600.0, "market_value": 0.0,
        "total_value": 114600.0, "daily_return": None, "cumulative_return": 0.0,
        "benchmark_close": 4000.0, "benchmark_daily_return": None,
        "benchmark_cumulative_return": 0.0, "excess_return": None,
        "position_count": 0, "turnover": None,
    })
    store.upsert_daily_snapshot({
        "date": "2026-07-13", "cash": 100000.0, "market_value": 15050.0,
        "total_value": 115050.0, "daily_return": 0.0039,
        "cumulative_return": 0.0039, "benchmark_close": 4040.0,
        "benchmark_daily_return": 0.01, "benchmark_cumulative_return": 0.01,
        "excess_return": -0.0061, "position_count": 2, "turnover": 0.12,
    })
    store.upsert_position_snapshots("2026-07-13", [
        {"stock_code": "600000.SH", "shares": 800, "avg_cost": 10.5,
         "close_price": 11.0, "market_value": 8800.0, "profit": 400.0,
         "weight": 0.076},
        {"stock_code": "000001.SZ", "shares": 500, "avg_cost": 12.0,
         "close_price": 12.5, "market_value": 6250.0, "profit": 250.0,
         "weight": 0.054},
    ])
    store.record_pipeline_event("2026-07-13", "postmarket", "OK", "")
    store.record_pipeline_event("2026-07-13", "report", "WARN",
                                "PRICE_MISSING: x")
    store.try_record_alert("2026-07-13", "WARN", "PRICE_MISSING", "缺价")
    recorder.save_stock_names([
        {"stock_code": "600000.SH", "instrument": "SH600000", "name": "浦发银行"},
        {"stock_code": "000001.SZ", "instrument": "SZ000001", "name": "平安银行"},
    ])
    recorder.record_orders(BATCH, [
        {
            "client_order_id": "20260713001B",
            "stock_code": "600000.SH",
            "instrument_qlib": "SH600000",
            "side": "BUY",
            "quantity": 800,
            "price_type": "FIX",
            "limit_price": 10.6,
            "priority": 20,
            "reason": "topk_dropout",
        },
        {
            "client_order_id": "20260713002B",
            "stock_code": "000001.SZ",
            "instrument_qlib": "SZ000001",
            "side": "BUY",
            "quantity": 500,
            "price_type": "FIX",
            "limit_price": 12.1,
            "priority": 20,
            "reason": "topk_dropout",
        },
    ])

    recorder.record_cash_flow("2026-07-13", "DIVIDEND", 380.0,
                              stock_code="600000.SH", note="派息")

    config = {
        "live": {"bridge_root": str(tmp_path / "bridge"),
                 "strategy_id": "csi300_topk10", "default_mode": "SIMULATE"},
        "storage": {"db_path": str(db)},
    }
    app = create_app(config, Path("/"))
    return TestClient(app)


def test_overview(client):
    r = client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["snapshot"]["date"] == "2026-07-13"
    # 10 万 - 两笔买入(8400+6000) - 费用 + 分红 380
    fees = (order_total_fee("BUY", 8400.0, DEFAULT_FEES)
            + order_total_fee("BUY", 6000.0, DEFAULT_FEES))
    assert data["cash"] == pytest.approx(85600.0 - fees + 380.0)
    assert data["position_count"] == 2
    assert len(data["recent_alerts"]) == 1


def test_nav(client):
    r = client.get("/api/nav")
    assert r.status_code == 200
    assert [s["date"] for s in r.json()] == ["2026-07-12", "2026-07-13"]
    r = client.get("/api/nav", params={"start": "2026-07-13"})
    assert len(r.json()) == 1


def test_positions(client):
    r = client.get("/api/positions")
    assert r.status_code == 200
    data = r.json()
    assert len(data["positions"]) == 2
    sh = next(p for p in data["positions"] if p["stock_code"] == "600000.SH")
    assert sh["close_price"] == 11.0 and sh["snapshot_date"] == "2026-07-13"


def test_positions_history(client):
    r = client.get("/api/positions/history", params={"date": "2026-07-13"})
    assert r.status_code == 200
    data = r.json()
    assert len(data["positions"]) == 2
    assert "2026-07-13" in data["dates"]


def test_batches_with_reconcile(client):
    r = client.get("/api/batches")
    assert r.status_code == 200
    b = r.json()[0]
    assert b["batch_id"] == BATCH
    assert b["planned"] == 2 and b["terminal"] == 2 and b["missing"] == 0


def test_batch_detail_includes_plan_and_names(client):
    r = client.get(f"/api/batches/{BATCH}")
    assert r.status_code == 200
    data = r.json()
    assert len(data["orders"]) == 2
    assert data["orders"][0]["name"] == "浦发银行"
    assert len(data["fills"]) == 2
    assert data["fills"][0]["name"] in {"浦发银行", "平安银行"}


def test_batch_fills_filtered(client):
    r = client.get(f"/api/batches/{BATCH}/fills")
    assert r.status_code == 200
    assert len(r.json()) == 2
    assert "name" in r.json()[0]
    r = client.get("/api/batches/nonexistent/fills")
    assert r.json() == []


def test_positions_include_name(client):
    r = client.get("/api/positions")
    assert r.status_code == 200
    names = {p["stock_code"]: p["name"] for p in r.json()["positions"]}
    assert names["600000.SH"] == "浦发银行"


def test_pipeline_matrix(client):
    r = client.get("/api/pipeline")
    assert r.status_code == 200
    data = r.json()
    day = data["days"]["2026-07-13"]
    assert day["postmarket"]["status"] == "OK"
    assert day["report"]["status"] == "WARN"


def test_alerts(client):
    r = client.get("/api/alerts")
    assert r.status_code == 200
    assert r.json()[0]["rule"] == "PRICE_MISSING"


def test_cashflows(client):
    r = client.get("/api/cashflows")
    assert r.status_code == 200
    data = r.json()
    assert len(data["flows"]) == 1
    flow = data["flows"][0]
    assert flow["flow_type"] == "DIVIDEND"
    assert flow["amount"] == 380.0
    assert flow["name"] == "浦发银行"
    assert data["cash"] > 0
