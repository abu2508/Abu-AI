from fastapi.testclient import TestClient

from services.api.config import Settings
from services.api.main import create_app


def test_liveness_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readiness_degraded_when_dependencies_missing(client: TestClient) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "unconfigured"
    assert body["checks"]["litellm"] == "fail"


def test_readiness_does_not_leak_connection_details(client: TestClient) -> None:
    text = client.get("/health/ready").text
    assert "127.0.0.1" not in text
    assert "postgresql" not in text


def test_docs_disabled_in_production() -> None:
    app = create_app(Settings(env="production"))
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
