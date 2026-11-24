from fastapi.testclient import TestClient
import pytest

from agent.service import app, owner


def test_console_serves_same_origin_assets_without_credentials():
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "Regulatory test console" in page.text
    assert 'name="expected_sha256"' not in page.text
    assert 'id="token"' not in page.text
    assert 'src="/console/console.js"' in page.text
    script = client.get("/console/console.js")
    assert script.status_code == 200
    assert "/v1/jobs/" in script.text
    assert client.get("/console/console.css").status_code == 200
    assert client.get("/v1/jobs/12345678-1234-1234-1234-123456789012").status_code == 401


def test_local_console_session_is_loopback_only(monkeypatch):
    monkeypatch.setenv("LOCAL_CONSOLE_NO_AUTH", "1")
    job = "/v1/jobs/12345678-1234-1234-1234-123456789012"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    local = TestClient(app, base_url="http://127.0.0.1:8001", client=("127.0.0.1", 1234))
    remote = TestClient(app, base_url="http://127.0.0.1:8001", client=("192.0.2.1", 1234))
    assert local.get(job).status_code == 503
    assert local.get(job, headers={"Origin": "https://example.com"}).status_code == 401
    assert remote.get(job).status_code == 401


@pytest.mark.parametrize("mode", ["url", "upload", "lookup"])
def test_preview_explains_missing_database(monkeypatch, mode):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setitem(app.dependency_overrides, owner, lambda: "test-client")
    client = TestClient(app)
    source = {"regulator": "RBI", "source_url": "https://rbi.org.in/test.pdf"}
    if mode == "url":
        response = client.post("/v1/jobs/url", json=source)
    elif mode == "upload":
        response = client.post("/v1/jobs/upload", params=source, content=b"%PDF-1.7")
    else:
        response = client.get("/v1/jobs/12345678-1234-1234-1234-123456789012")
    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]
