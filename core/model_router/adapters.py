"""Provider adapters. Adapters only transport; routing and permission decisions are made earlier."""

import time
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from core.model_router import contract
from core.model_router.router import ResolvedModel
from core.model_router.types import CompletionRequest, CompletionResult


class ProviderError(Exception):
    """Generic provider failure. Never includes provider response bodies or request content.

    maybe_charged: True when the provider MAY have processed (and billed) the request - a timeout,
    5xx, dropped connection or unparsable answer. False when it definitely did not: the request
    never left (connect failure) or was rejected outright (4xx).
    """

    def __init__(self, message: str, *, maybe_charged: bool = False) -> None:
        super().__init__(message)
        self.maybe_charged = maybe_charged


class ProviderAdapter(Protocol):
    async def complete(
        self, model: ResolvedModel, request: CompletionRequest
    ) -> CompletionResult: ...


class MockAdapter:
    """Deterministic in-process adapter. No network, no data leaves the process."""

    async def complete(self, model: ResolvedModel, request: CompletionRequest) -> CompletionResult:
        start = time.perf_counter()
        first = request.messages[0]
        if first.role == "system" and contract.is_contract_request(first.content):
            text = contract.mock_contract_reply(
                first.content
            )  # structured-output mode (orchestrator)
        else:
            text = f"[mock:{model.name}] received {len(request.messages)} message(s)"
        in_tokens = sum(len(m.content) for m in request.messages) // 4 + 1
        return CompletionResult(
            text=text,
            input_tokens=in_tokens,
            output_tokens=len(text) // 4 + 1,
            model_name=model.name,
            provider=model.provider,
            latency_ms=(time.perf_counter() - start) * 1000,
        )


class _Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message


class _ChatResponse(BaseModel):
    choices: list[_Choice]
    usage: _Usage | None = None


class LiteLLMAdapter:
    """Talks to the private LiteLLM proxy using the OpenAI-compatible chat endpoint.

    `model` sent to LiteLLM is the registry's gateway_model_name, never caller-supplied text.
    """

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    async def complete(self, model: ResolvedModel, request: CompletionRequest) -> CompletionResult:
        payload = {
            "model": model.gateway_model_name,
            "messages": [m.model_dump() for m in request.messages],
            "max_tokens": request.max_tokens,
        }
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.post(
                    f"{self._base}/v1/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                )
            resp.raise_for_status()
            parsed = _ChatResponse.model_validate(resp.json())
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise ProviderError("gateway unreachable", maybe_charged=False) from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            charged = status >= 500 or status == 408  # 4xx: rejected before processing
            raise ProviderError(f"gateway returned HTTP {status}", maybe_charged=charged) from None
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise ProviderError(
                f"gateway call failed ({type(exc).__name__})", maybe_charged=True
            ) from None
        if not parsed.choices or parsed.choices[0].message.content is None:
            raise ProviderError("gateway returned no content", maybe_charged=True)
        usage = parsed.usage
        reported = (
            usage is not None
            and usage.prompt_tokens is not None
            and usage.completion_tokens is not None
        )
        return CompletionResult(
            text=parsed.choices[0].message.content,
            input_tokens=(usage.prompt_tokens or 0) if usage else 0,
            output_tokens=(usage.completion_tokens or 0) if usage else 0,
            model_name=model.name,
            provider=model.provider,
            latency_ms=(time.perf_counter() - start) * 1000,
            usage_reported=reported,
            provider_cost_usd=_parse_cost(resp.headers.get("x-litellm-response-cost")),
        )


def _parse_cost(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value >= 0 else None
