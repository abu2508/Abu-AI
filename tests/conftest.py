from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from services.api.config import Settings
from services.api.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # No database or LiteLLM configured: exercises the app without any network.
    settings = Settings(env="test", database_url=None, litellm_base_url="http://127.0.0.1:9")
    with TestClient(create_app(settings)) as c:
        yield c
