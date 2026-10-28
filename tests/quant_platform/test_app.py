from pathlib import Path
import hashlib
import json

import httpx
from fastapi.testclient import TestClient

from quant_platform.app import create_app


def make_client(tmp_path, handler=None):
    research = tmp_path / "research"
    research.mkdir(exist_ok=True)
    if handler is None:
        handler = lambda request: httpx.Response(200, json={"total_value": 123})
    app = create_app(research, tmp_path / "archive", transport=httpx.MockTransport(handler))
    return TestClient(app, base_url="http://127.0.0.1:8090")


def test_proxy_reads_only_whitelisted_paths(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"total_value": 123})

    with make_client(tmp_path, handler) as client:
        assert client.get("/api/overview").json() == {"total_value": 123}
        assert client.post("/api/overview").status_code in {403, 405}
        assert client.get("/api/publish").status_code == 404
        assert client.get("/api/batches/a%2Fb").status_code == 404
        assert client.get("/api/batches/%2e%2e").status_code == 404
        assert client.get("/api/batches/%2e%2e/fills").status_code == 404
        assert client.get("/api/batches/%2e").status_code == 404
    assert calls == ["http://127.0.0.1:8082/api/overview"]


def test_proxy_does_not_follow_redirects_or_expose_upstream_errors(tmp_path):
    with make_client(
        tmp_path, lambda request: httpx.Response(302, headers={"location": "http://evil.example"})
    ) as client:
        assert client.get("/api/overview").status_code == 503
        assert client.get("/api/analyses").status_code == 200


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
