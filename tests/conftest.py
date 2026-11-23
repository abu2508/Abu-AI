from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from services.api.main import create_app
from tests.integration.helpers import make_settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    # No database or LiteLLM configured: exercises the app without any network.
    with TestClient(create_app(make_settings())) as c:
        yield c
