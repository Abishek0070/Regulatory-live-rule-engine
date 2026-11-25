from fastapi.testclient import TestClient
import pytest

from agent.service import app


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



def test_cross_origin_submission_is_rejected():
    response = TestClient(app).post("/v1/jobs/url", headers={"Origin": "https://example.com"},
                                    json={"regulator": "RBI", "source_url": "https://rbi.org.in/test.pdf"})
    assert response.status_code == 403


@pytest.mark.parametrize("mode", ["url", "upload", "lookup"])
def test_preview_explains_missing_database(monkeypatch, mode):
    monkeypatch.delenv("DATABASE_URL", raising=False)
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
