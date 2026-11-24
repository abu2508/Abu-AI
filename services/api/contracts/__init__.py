"""Documentation-only response contracts (B3).

These Pydantic models DESCRIBE what existing routes and orchestrator tools already return. They are never used to
validate, filter or serialise a live response: routes keep `response_model=None` and their `-> Any` returns. They feed
the OpenAPI document (see `openapi.py`) and the conformance tests, nothing else. Do not import them from request-handling
code.
"""
