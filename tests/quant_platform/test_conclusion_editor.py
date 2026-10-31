"""A table edit changes only editorial metadata and rejects stale saves."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from quant_platform.app import create_app
from tests.quant_platform.test_directory_catalog import manifest
from tests.quant_platform.test_research import catalog, row


@pytest.fixture
def editor(tmp_path):
    original = row("model/example", baseline_version="v4", baseline_ref="self", conclusion="原登记结论")
    c = catalog(tmp_path, [original])
    directory = manifest(tmp_path, original["exp_id"], analysis={
        "conclusion": "原人工结论", "evidence": [], "limitations": "原局限",
        "review_status": "reviewed", "updated_at": "2026-10-30",
    })
    path = tmp_path / directory / "metadata.json"
    client = TestClient(create_app(tmp_path, tmp_path / "backtest/diagnostics"), base_url="http://127.0.0.1:8090")
    token = client.get("/api/session").json()["csrf_token"]
    headers = {"Origin": "http://127.0.0.1:8090", "X-CSRF-Token": token}
    return c, path, client, headers


def body(client, **changes):
    record = client.get("/api/research").json()["records"][0]
    return {"id": record["id"], "conclusion": "  相对v3略有改善，保留作当前基线。  ",
            "revision": record.get("analysis_revision", "0" * 64), **changes}


def test_update_persists_only_editorial_conclusion_and_keeps_research_authority(editor):
    c, path, client, headers = editor
    old = json.loads(path.read_text())
    before = client.get("/api/research").json()["records"][0]
    raw_registry = c.registry.read_bytes()
    response = client.put("/api/research/conclusion", json=body(client), headers=headers)
    assert response.status_code == 200, response.text
    saved = response.json()
    new = json.loads(path.read_text())
    assert new["analysis"]["conclusion"] == "相对v3略有改善，保留作当前基线。"
    assert new["analysis"]["review_status"] == "draft"
    assert new["analysis"]["updated_at"]
    for key in ["evidence", "limitations"]:
        assert new["analysis"][key] == old["analysis"][key]
    assert {k: v for k, v in new.items() if k != "analysis"} == {k: v for k, v in old.items() if k != "analysis"}
    assert saved["analysis_revision"] == hashlib.sha256(path.read_bytes()).hexdigest()
    after = client.get("/api/research").json()["records"][0]
    assert after["analysis"] == saved["analysis"]
    assert {k: v for k, v in after.items() if k not in ["analysis", "analysis_revision"]} == {
        k: v for k, v in before.items() if k not in ["analysis", "analysis_revision"]}
    assert c.registry.read_bytes() == raw_registry


def test_revision_matches_the_metadata_payload_used_by_discovery(editor):
    _, path, client, _ = editor
    record = client.get("/api/research").json()["records"][0]
    assert record["analysis_revision"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_stale_edit_cannot_overwrite_a_newer_file(editor):
    _, path, client, headers = editor
    pending = body(client)
    doc = json.loads(path.read_text())
    doc["title"] = "别处更新的名称"
    path.write_text(json.dumps(doc, ensure_ascii=False))
    latest = path.read_bytes()
    response = client.put("/api/research/conclusion", json=pending, headers=headers)
    assert response.status_code == 409
    assert path.read_bytes() == latest


def test_second_editor_cannot_save_an_old_revision(editor):
    _, path, client, headers = editor
    pending = body(client)
    assert client.put("/api/research/conclusion", json=pending, headers=headers).status_code == 200
    latest = path.read_bytes()
    assert client.put("/api/research/conclusion", json={**pending, "conclusion": "覆盖较新内容"}, headers=headers).status_code == 409
    assert path.read_bytes() == latest


def test_simultaneous_saves_with_one_revision_have_only_one_winner(editor):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, path, client, headers = editor
    pending = body(client)
    start = Barrier(2)

    def save(text):
        start.wait(timeout=5)
        return client.put("/api/research/conclusion", json={**pending, "conclusion": text}, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(save, ["第一个编辑者的结论", "第二个编辑者的结论"]))
    assert sorted(r.status_code for r in replies) == [200, 409]
    winner = next(r.json() for r in replies if r.status_code == 200)
    assert json.loads(path.read_text())["analysis"]["conclusion"] == winner["analysis"]["conclusion"]


@pytest.mark.parametrize("changes", [
    {"conclusion": ""}, {"conclusion": "   "}, {"conclusion": "字" * 501}, {"conclusion": None},
    {"revision": ""}, {"revision": "bad"}, {"revision": 3},
    {"id": "../escape"}, {"id": "self/../../escape"}, {"current": True}, {"analysis": {"conclusion": "绕过"}},
])
def test_bad_payload_does_not_modify_metadata(editor, changes):
    _, path, client, headers = editor
    old = path.read_bytes()
    response = client.put("/api/research/conclusion", json=body(client, **changes), headers=headers)
    assert response.status_code in [400, 422], response.text
    assert path.read_bytes() == old


def test_unknown_experiment_is_not_created(editor):
    _, path, client, headers = editor
    old = path.read_bytes()
    assert client.put("/api/research/conclusion", json=body(client, id="unknown/experiment"), headers=headers).status_code == 404
    assert path.read_bytes() == old


def test_editor_requires_same_origin_and_csrf(editor):
    _, path, client, headers = editor
    payload = body(client)
    old = path.read_bytes()
    for bad in [{}, {"Origin": headers["Origin"]}, {**headers, "Origin": "https://example.com"},
                {**headers, "Sec-Fetch-Site": "cross-site"}]:
        assert client.put("/api/research/conclusion", json=payload, headers=bad).status_code == 403
    assert path.read_bytes() == old


def test_symlinked_metadata_cannot_modify_an_outside_file(editor, tmp_path):
    _, path, client, headers = editor
    pending = body(client)
    outside = tmp_path / "outside.json"
    outside.write_bytes(path.read_bytes())
    old = outside.read_bytes()
    path.unlink()
    path.symlink_to(outside)
    response = client.put("/api/research/conclusion", json=pending, headers=headers)
    assert response.status_code in [400, 404]
    assert outside.read_bytes() == old


def test_strategy_and_draft_metadata_can_be_edited_without_scientific_promotion(tmp_path):
    c = catalog(tmp_path, [])
    manifest(tmp_path, "strategy/draft", kind="strategy")
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8090")
    token = client.get("/api/session").json()["csrf_token"]
    headers = {"Origin": "http://127.0.0.1:8090", "X-CSRF-Token": token}
    result = client.put("/api/research/conclusion", json=body(client), headers=headers)
    assert result.status_code == 200
    record = client.get("/api/research").json()["records"][0]
    assert not any(record[k] for k in ["official", "comparable", "is_baseline", "current"])
