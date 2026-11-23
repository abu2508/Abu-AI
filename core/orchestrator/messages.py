"""Static, user-facing texts for outcomes that need no model. Never include user input."""

CAPABILITIES = (
    "I can help with your finances, with drafting replies to messages, and with planning your day."
)

CLARIFY_GENERIC = f"Could you tell me a bit more about what you need? {CAPABILITIES}"

CLARIFY_AMBIGUOUS = {
    "finance": "your finances",
    "messaging": "replying to a message",
    "coaching": "planning your day or goals",
}

UNSUPPORTED = {
    "irreversible_action": (
        "I can't make payments or transfers, or send messages for you. "
        "I can help you review, plan or draft something that you then do yourself."
    ),
    "governance_bypass": "I can't change my own limits, permissions or safety controls.",
    "cross_user": "I can only work with your own information.",
    "default": "I can't help with that request.",
}


def ambiguous_question(candidates: list[str]) -> str:
    names = [CLARIFY_AMBIGUOUS.get(c, c) for c in candidates]
    return f"Do you want help with {' or '.join(names)}?"
