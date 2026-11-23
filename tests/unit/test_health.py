from fastapi.testclient import TestClient

from services.api.main import create_app
from tests.integration.helpers import make_settings


def test_liveness_is_minimal_and_public(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}  # nothing about infrastructure


def test_readiness_requires_authentication(client: TestClient) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 401
    assert "database" not in resp.text and "litellm" not in resp.text


def test_docs_disabled_in_production() -> None:
    app = create_app(make_settings(env="production"))
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
