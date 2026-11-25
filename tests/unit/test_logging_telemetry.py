# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,union-attr,index,attr-defined,assignment,operator,misc,type-arg"
"""Structured logging and telemetry (P1.6): redaction, JSON shape, credential-free access log, anomaly detection."""

import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from services.api.logging_config import JsonFormatter, configure_logging, redact
from services.api.main import create_app
from services.api.telemetry import AnomalyDetector, path_shape
from tests.integration.helpers import make_settings

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXNlY3JldC12YWx1ZQ"


@pytest.fixture
def clean_logging():
    """configure_logging changes process-wide state; restore it so no other test sees a closed stream."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    state = {
        n: (
            logging.getLogger(n).handlers[:],
            logging.getLogger(n).propagate,
            logging.getLogger(n).disabled,
        )
        for n in names
    }
    yield
    root.handlers, root.level = saved[0], saved[1]
    for n, (h, p, d) in state.items():
        lg = logging.getLogger(n)
        lg.handlers, lg.propagate, lg.disabled = h, p, d


# ---------------------------------------------------------------------------------------------------- redaction
@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Authorization: Bearer abcdefghijklmnop12345", "abcdefghijklmnop12345"),
        (f"token {FAKE_JWT} used", FAKE_JWT),
        ("calling with sk-abcdefghij1234567890", "sk-abcdefghij1234567890"),
        ("dsn postgresql+psycopg://abu_app:Sup3rS3cretPw@db:5432/abu_ai failed", "Sup3rS3cretPw"),
        ("password=hunter2hunter2 and secret: 'topsecretvalue'", "hunter2hunter2"),
        ('{"api_key": "AKIA-not-real-1234", "x": 1}', "AKIA-not-real-1234"),
        ("account 123456789012 debited", "123456789012"),
        ("card 4111 1111 1111 1111 declined", "4111 1111 1111 1111"),
        ("user someone@example.com asked", "someone@example.com"),
        (
            "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END PRIVATE KEY-----",
            "MIIEvQIBADANBgkq",
        ),
        ("refresh_token=abc123def456ghi789", "abc123def456ghi789"),
        ("kek=cGxhY2Vob2xkZXItcGxhY2Vob2xkZXI=", "cGxhY2Vob2xkZXItcGxhY2Vob2xkZXI="),
    ],
)
def test_redaction_removes_secrets_and_personal_identifiers(raw: str, secret: str) -> None:
    out = redact(raw)
    assert secret not in out, out
    assert "redacted" in out or "[digits]" in out or "[email]" in out


def test_redaction_keeps_ordinary_operational_text_readable() -> None:
    msg = "http_request GET /v1/finance/summary 200 12.5ms request_id=0f3a status=ok users=3"
    assert redact(msg) == msg
    assert redact("shipping=fast author=ann mapping=1") == "shipping=fast author=ann mapping=1"


def test_the_json_formatter_emits_one_valid_redacted_object_per_record() -> None:
    rec = logging.LogRecord(
        "abu.test", logging.WARNING, __file__, 1, "failed for token=%s", ("abcdef123456XYZ",), None
    )
    rec.abu = {"event": "x", "authorization": "Bearer zzzzzzzzzzzzzzzz", "n": 3}
    line = JsonFormatter().format(rec)
    doc = json.loads(line)
    assert "\n" not in line and doc["level"] == "WARNING" and doc["event"] == "x" and doc["n"] == 3
    assert "abcdef123456XYZ" not in line and "zzzzzzzzzzzzzzzz" not in line


def test_exception_text_is_redacted_too() -> None:
    try:
        raise RuntimeError("connect failed postgresql://u:PasswordInTrace99@h/db")
    except RuntimeError:
        import sys

        rec = logging.LogRecord("abu.test", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
    assert "PasswordInTrace99" not in JsonFormatter().format(rec)


# --------------------------------------------------------------------------------------------- access log
def test_path_shape_collapses_identifiers() -> None:
    u = uuid.uuid4()
    assert (
        path_shape(f"/v1/finance/transactions/{u}/category")
        == "/v1/finance/transactions/{id}/category"
    )
    assert path_shape("/v1/finance/loans/12345/schedule") == "/v1/finance/loans/{id}/schedule"
    assert path_shape("/v1/finance/summary") == "/v1/finance/summary"


def _lines(out: str) -> list[dict]:
    return [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]


def test_the_access_log_records_the_request_without_any_credential_or_content(
    clean_logging, capsys
) -> None:
    app = create_app(make_settings(log_format="json"))
    needle = "needle-in-query-12345"
    with TestClient(app) as c:
        r = c.post(
            f"/v1/finance/transactions/{uuid.uuid4()}/category?token={needle}",
            json={"category": "SECRETBODY-MARKER"},
            headers={"Authorization": f"Bearer {FAKE_JWT}", "Cookie": "session=cookie-marker-9"},
        )
    assert r.status_code == 401
    out = capsys.readouterr().out
    for leaked in (
        FAKE_JWT,
        needle,
        "SECRETBODY-MARKER",
        "cookie-marker-9",
        "testclient",
        "127.0.0.1",
    ):
        assert leaked not in out, f"{leaked!r} reached the log"
    entry = next(e for e in _lines(out) if e.get("event") == "http_request")
    assert entry["status"] == 401 and entry["method"] == "POST"
    assert entry["path"] == "/v1/finance/transactions/{id}/category"
    assert entry["duration_ms"] >= 0 and len(entry["request_id"]) >= 8
    assert r.headers["x-request-id"] == entry["request_id"]


def test_request_id_is_echoed_only_when_it_is_a_safe_token(clean_logging, capsys) -> None:
    app = create_app(make_settings(log_format="json"))
    with TestClient(app) as c:
        good = c.get("/health", headers={"X-Request-ID": "trace-ABC_12345"})
        bad = c.get("/health", headers={"X-Request-ID": "has spaces & <script>"})
    assert good.headers["x-request-id"] == "trace-ABC_12345"
    assert (
        bad.headers["x-request-id"] != "has spaces & <script>"
        and len(bad.headers["x-request-id"]) == 32
    )
    assert "script" not in capsys.readouterr().out


def test_default_text_mode_changes_nothing(capsys) -> None:
    app = create_app(make_settings())  # log_format defaults to "text"
    with TestClient(app) as c:
        r = c.get("/health")
    assert "x-request-id" not in r.headers
    assert "http_request" not in capsys.readouterr().out


def test_a_burst_of_failures_raises_one_auth_anomaly_event(clean_logging, capsys) -> None:
    app = create_app(make_settings(log_format="json", auth_anomaly_threshold=3))
    with TestClient(app) as c:
        for _ in range(5):
            assert (
                c.get("/v1/finance/accounts", headers={"Authorization": "Bearer nope"}).status_code
                == 401
            )
    anomalies = [e for e in _lines(capsys.readouterr().out) if e.get("event") == "auth_anomaly"]
    assert len(anomalies) == 1, "one warning per window, not one per request"
    assert (
        anomalies[0]["level"] == "WARNING"
        and anomalies[0]["counts"]["unauthorised"] >= 3
        and anomalies[0]["threshold"] == 3
    )


def test_anomaly_detector_window_and_rearm() -> None:
    t = [0.0]
    d = AnomalyDetector(threshold=3, window_seconds=60, clock=lambda: t[0])
    assert d.record("unauthorised") is None and d.record("rate_limited") is None
    assert d.record("unauthorised") == {"unauthorised": 2, "rate_limited": 1}
    assert d.record("unauthorised") is None  # already alerted inside this window
    t[0] = 200.0  # old hits age out
    assert d.record("unauthorised") is None
    t[0] = 201.0
    assert d.record("unauthorised") is None and d.record("unauthorised") == {
        "unauthorised": 3
    }  # re-armed


def test_configure_logging_is_idempotent_and_silences_the_uvicorn_access_log(clean_logging) -> None:
    configure_logging("INFO")
    configure_logging("INFO")
    root = logging.getLogger()
    assert [h.get_name() for h in root.handlers].count("abu-json") == 1
    assert logging.getLogger("uvicorn.access").disabled is True
