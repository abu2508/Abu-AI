import httpx
import pytest
from pydantic import SecretStr, ValidationError

from core.model_router.adapters import LiteLLMAdapter, MockAdapter, ProviderError
from core.model_router.router import ResolvedModel
from core.model_router.types import CompletionRequest, Message
from database.schemas import DataClass

MODEL = ResolvedModel("m", "prov", "exact-provider-id", "gateway-name", None)


def req() -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="hello")], data_class=DataClass.SYNTHETIC
    )


def test_data_class_is_mandatory() -> None:
    with pytest.raises(ValidationError):
        CompletionRequest(messages=[Message(role="user", content="x")])  # type: ignore[call-arg]


async def test_mock_adapter_does_not_echo_content() -> None:
    res = await MockAdapter().complete(MODEL, req())
    assert "hello" not in res.text
    assert res.input_tokens >= 1


async def test_litellm_adapter_sends_gateway_name_and_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    adapter = LiteLLMAdapter(
        "http://litellm:4000", SecretStr("sk-test"), transport=httpx.MockTransport(handler)
    )
    res = await adapter.complete(MODEL, req())
    assert (res.text, res.input_tokens, res.output_tokens) == ("hi", 3, 2)
    assert seen["auth"] == "Bearer sk-test"
    assert '"model":"gateway-name"' in str(seen["body"]).replace(" ", "")
    assert "exact-provider-id" not in str(seen["body"])


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_litellm_adapter_errors_are_generic(status: int) -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(status, text="secret-provider-detail sk-leak")
    )
    adapter = LiteLLMAdapter("http://litellm:4000", SecretStr("sk-test"), transport=transport)
    with pytest.raises(ProviderError) as exc:
        await adapter.complete(MODEL, req())
    assert "sk-leak" not in str(exc.value) and "secret-provider-detail" not in str(exc.value)


async def test_litellm_adapter_rejects_malformed_payload() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"nope": 1}))
    adapter = LiteLLMAdapter("http://litellm:4000", SecretStr("k"), transport=transport)
    with pytest.raises(ProviderError):
        await adapter.complete(MODEL, req())
