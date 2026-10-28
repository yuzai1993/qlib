from pathlib import Path
import hashlib
import json
import sqlite3
import asyncio
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from quant_platform.app import create_app


def make_client(tmp_path, live_config=None):
    research = tmp_path / "research"
    research.mkdir(exist_ok=True)
    app = create_app(research, tmp_path / "archive", live_config=live_config, project_root=tmp_path)
    return TestClient(app, base_url="http://127.0.0.1:8090")


def live_fixture(tmp_path):
    from live_trading.modules.fill_importer import LiveRecorder
    from live_trading.modules.monitor_store import MonitorStore

    db = tmp_path / "ledger.db"
    recorder = LiveRecorder(str(db))
    recorder.set_cash(123)
    MonitorStore(str(db))
    return {"storage": {"db_path": str(db)}, "live": {"bridge_root": str(tmp_path / "bridge"), "strategy_id": "test"}}


def test_monitor_reads_database_directly_without_network_or_initialization(tmp_path, monkeypatch):
    from live_trading.modules.fill_importer import LiveRecorder
    from live_trading.modules.monitor_store import MonitorStore

    config = live_fixture(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("must not initialize storage or send upstream HTTP")

    monkeypatch.setattr(LiveRecorder, "_init_db", forbidden)
    monkeypatch.setattr(MonitorStore, "_init_db", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    with make_client(tmp_path, config) as client:
        assert client.get("/api/overview").json()["total_value"] == 123
        assert client.get("/api/health").json()["monitor_mode"] == "direct-read-only"
        assert client.post("/api/overview").status_code in {403, 405}
        assert client.get("/api/publish").status_code == 404
        assert client.get("/api/batches/a%2Fb").status_code == 404
        token = client.get("/api/session").json()["csrf_token"]
        assert (
            client.post("/api/overview", headers={"X-CSRF-Token": token, "Origin": "http://127.0.0.1:8090"}).status_code
            == 405
        )
    assert not (tmp_path / "bridge").exists()


def test_missing_ledger_does_not_create_it_or_block_research(tmp_path):
    db = tmp_path / "missing/ledger.db"
    config = {"storage": {"db_path": str(db)}, "live": {"bridge_root": str(tmp_path / "bridge")}}
    with make_client(tmp_path, config) as client:
        assert client.get("/api/overview").status_code == 503
        assert client.get("/api/analyses").status_code == 200
        assert client.get("/api/research").status_code == 200
        assert client.get("/api/not-a-monitor-route").status_code == 404
    assert not db.parent.exists()


def test_old_schema_fails_closed_without_migration(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE daily_snapshot (date TEXT)")
    before = db.read_bytes()
    config = {"storage": {"db_path": str(db)}, "live": {"bridge_root": str(tmp_path / "bridge")}}
    with make_client(tmp_path, config) as client:
        assert client.get("/api/health").json()["monitor_status"] == "unavailable"
        response = client.get("/api/overview")
        assert response.status_code == 503
        assert str(db) not in response.text
        assert client.get("/api/analyses").status_code == 200
    assert db.read_bytes() == before


def test_health_detects_a_ledger_removed_after_startup(tmp_path):
    config = live_fixture(tmp_path)
    with make_client(tmp_path, config) as client:
        assert client.get("/api/health").json()["monitor_status"] == "ready"
        Path(config["storage"]["db_path"]).unlink()
        assert client.get("/api/health").json()["monitor_status"] == "unavailable"


def test_monitor_query_and_response_sizes_are_bounded(tmp_path):
    config = live_fixture(tmp_path)
    with sqlite3.connect(config["storage"]["db_path"]) as conn:
        conn.execute(
            "INSERT INTO pipeline_events (trade_date,stage,status,message) VALUES (date('now'),'report','OK',?)",
            ("x" * (9 * 1024 * 1024),),
        )
    with make_client(tmp_path, config) as client:
        assert client.get("/api/nav?unused=" + "x" * 5000).status_code == 400
        assert client.get("/api/pipeline").status_code == 503
        assert client.get("/api/research").status_code == 200


@pytest.mark.parametrize("batch_id", ["x" * 161, "中文批次", "%2e", "%2e%2e", "a%2Fb"])
def test_batch_paths_cannot_bypass_monitor_bounds(tmp_path, batch_id):
    with make_client(tmp_path, live_fixture(tmp_path)) as client:
        assert client.get("/api/batches/" + batch_id + "?unused=" + "x" * 5000).status_code == 404


def test_six_monitor_requests_do_not_block_research_or_admit_a_seventh(tmp_path, monkeypatch):
    from live_trading.modules.fill_importer import LiveRecorder

    config = live_fixture(tmp_path)
    release = threading.Event()
    started = 0
    lock = threading.Lock()

    async def exercise():
        loop = asyncio.get_running_loop()
        all_started = asyncio.Event()

        def blocked_cash(self):
            nonlocal started
            with lock:
                started += 1
                if started == 6:
                    loop.call_soon_threadsafe(all_started.set)
            assert release.wait(5)
            return 123

        monkeypatch.setattr(LiveRecorder, "get_cash", blocked_cash)
        app = make_client(tmp_path, config).app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8090") as client:
            pending = [asyncio.create_task(client.get("/api/cashflows")) for _ in range(6)]
            try:
                await asyncio.wait_for(all_started.wait(), 3)
                assert (await client.get("/api/cashflows")).status_code == 503
                assert (await client.get("/api/research")).status_code == 200
            finally:
                release.set()
                responses = await asyncio.gather(*pending)
            assert all(response.status_code == 200 for response in responses)

    asyncio.run(exercise())


def test_write_requires_same_origin_token_and_valid_host(tmp_path):
    body = dict(
        title="test",
        background="b",
        method="m",
        conclusion="c",
        usage="u",
        codes=[{"name": "x.py", "content": "# x"}],
        tags=[],
        related_experiments=[],
        status="active",
    )
    with make_client(tmp_path) as client:
        assert client.post("/api/analyses", json=body).status_code == 403
        token = client.get("/api/session").json()["csrf_token"]
        headers = {"X-CSRF-Token": token, "Origin": "http://evil.example"}
        assert client.post("/api/analyses", json=body, headers=headers).status_code == 403
        headers["Origin"] = "http://127.0.0.1:8090"
        response = client.post("/api/analyses", json=body, headers=headers)
        assert response.status_code == 201
        row = response.json()
        assert client.get(f"/api/analyses/{row['id']}/export").headers["content-type"].startswith("application/zip")
        assert client.get("/api/session", headers={"Host": "evil.example"}).status_code == 400


def test_no_production_database_created_on_startup(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/api/analyses").json() == {"records": []}
    assert list(tmp_path.rglob("*.db")) == []


def test_registered_html_is_sandboxed_and_arbitrary_file_is_unavailable(tmp_path):
    root = tmp_path / "research/backtest/experiments"
    root.mkdir(parents=True)
    report_path = "backtest/experiments/detail.html"
    (root / "detail.html").write_text("<h1>报告</h1><script>alert(1)</script>")
    (root / "registry.jsonl").write_text(
        json.dumps({"exp_id": "model/a", "phase_m_protocol": "v1", "detail_report": report_path})
    )
    with make_client(tmp_path) as client:
        client.get("/api/research")
        resource_id = hashlib.sha256(report_path.encode()).hexdigest()[:24]
        response = client.get("/api/resources/" + resource_id)
        assert response.status_code == 200
        assert "sandbox" in response.headers["content-security-policy"]
        assert "allow-scripts" not in response.headers["content-security-policy"]
        assert client.get("/api/resources/unregistered").status_code == 404
