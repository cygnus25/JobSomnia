import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.html").write_text("<html><body>UI</body></html>")
    (tmp_path / "output").mkdir()
    import importlib, server
    importlib.reload(server)
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "STATUS_FILE", tmp_path / "output" / "status.json")
    monkeypatch.setattr(server, "JOBS_FILE", tmp_path / "output" / "jobs.json")
    return TestClient(server.app)


def test_index_returns_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "UI" in res.text


def test_get_jobs_empty_when_no_file(client):
    res = client.get("/api/jobs")
    assert res.status_code == 200
    assert res.json() == []


def test_get_jobs_merges_status(client, tmp_path):
    jobs = [{"title": "Dev", "company": "Co", "url": "https://example.com",
             "score": 85, "verdict": "apply"}]
    (tmp_path / "output" / "jobs.json").write_text(json.dumps(jobs))
    (tmp_path / "output" / "status.json").write_text(
        json.dumps({"https://example.com": "applied"})
    )
    res = client.get("/api/jobs")
    data = res.json()
    assert data[0]["status"] == "applied"


def test_get_jobs_status_defaults_to_none(client, tmp_path):
    jobs = [{"title": "Dev", "url": "https://example.com", "score": 85}]
    (tmp_path / "output" / "jobs.json").write_text(json.dumps(jobs))
    res = client.get("/api/jobs")
    assert res.json()[0]["status"] == "none"


def test_post_status_saves_applied(client, tmp_path):
    res = client.post("/api/status",
                      json={"url": "https://example.com", "status": "applied"})
    assert res.status_code == 200
    data = json.loads((tmp_path / "output" / "status.json").read_text())
    assert data["https://example.com"] == "applied"


def test_post_status_none_removes_entry(client, tmp_path):
    (tmp_path / "output" / "status.json").write_text(
        json.dumps({"https://example.com": "applied"})
    )
    client.post("/api/status", json={"url": "https://example.com", "status": "none"})
    data = json.loads((tmp_path / "output" / "status.json").read_text())
    assert "https://example.com" not in data


def test_post_status_rejects_invalid_value(client):
    res = client.post("/api/status",
                      json={"url": "https://example.com", "status": "maybe"})
    assert res.status_code == 400
