"""The structured-output contract between the orchestrator and any model.

A model is asked (in a SYSTEM message the orchestrator writes; never user text) to answer with one
JSON object. The orchestrator validates it strictly (core/orchestrator/output.py). The deterministic
mock adapter honours the same contract, so the mock path exercises the same validation a real model will.
"""

import json
import re

MARKER = "ABU-OUTPUT-CONTRACT: json-v1"
_WORKFLOW = re.compile(r"^Workflow: ([a-z0-9_.-]{1,64})$", re.M)


def is_contract_request(system_text: str) -> bool:
    return MARKER in system_text


def mock_contract_reply(system_text: str) -> str:
    """Canned, valid reply. Deliberately does NOT echo any user text, and says plainly that it is a mock."""
    m = _WORKFLOW.search(system_text)
    workflow = m.group(1) if m else "unknown"
    return json.dumps(
        {
            "reply": f"[mock:{workflow}] This is a placeholder response from the foundation workflow. "
            "No domain logic is implemented yet.",
            "needs_clarification": False,
            "clarification_question": None,
            "memory_proposals": [],
        }
    )
