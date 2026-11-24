# ruff: noqa: F811
# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,index,type-arg"
"""B3 golden responses: the HTTP behaviour of every covered route, tool result and error path, byte for byte.

The golden file was recorded BEFORE any B3 change (see docs/mobile/B3_REPORT.md for the commit that introduced it) and is
never regenerated to make a test pass. Re-record only deliberately, with ABU_GOLDEN=record, in a reviewed commit.
"""

import json
import os
from pathlib import Path

import pytest

from tests.contracts import scenario
from tests.integration.conftest import Api
from tests.integration.test_orchestrator import (  # noqa: F401 - fixtures
    booted,
    no_external_network,
)

GOLDEN = Path(__file__).resolve().parents[1] / "contracts" / "golden" / "golden.json"


def canonical(items: dict) -> str:
    return json.dumps(items, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


async def test_responses_match_the_pre_b3_golden(
    booted: Api, monkeypatch: pytest.MonkeyPatch, no_external_network: None
) -> None:
    scenario.freeze_clock(monkeypatch)
    items = await scenario.build(booted)
    text = canonical(items)
    if os.environ.get("ABU_GOLDEN") == "record":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
        pytest.skip(f"recorded {len(items)} golden responses")
    assert GOLDEN.exists(), "golden file missing: it must be recorded from the pre-change backend"
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    diffs = [k for k in sorted(set(want) | set(items)) if want.get(k) != json.loads(text).get(k)]
    assert not diffs, f"responses changed for: {diffs}"
    assert text == GOLDEN.read_text(encoding="utf-8")  # byte-for-byte
