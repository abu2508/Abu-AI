"""Messaging tool shapes. Sources: `domain/messaging/analysis.py`, `domain/messaging/service.py`, `agents/tools.py`.

Agent-visible only for conversations the user shared with the assistant (the privacy rule, enforced server-side)."""

from typing import Literal

from pydantic import Field

from services.api.contracts.common import Contract, IsoDateTime, UuidStr


class TheirTone(Contract):
    sentiment: Literal["positive", "negative", "neutral"]
    urgent: bool
    formal: bool
    uses_emoji: bool
    brief: bool


class MyStyle(Contract):
    avg_words: float | None
    uses_emoji: bool


class AnalyseTool(Contract):
    """`message_count: 0` (empty conversation) carries only the four required keys; the rest are optional.
    The two median keys exist only when the conversation has reliable timestamps."""

    message_count: int
    instruction_like_messages: int
    conversation_id: UuidStr
    platform: str
    from_me: int | None = None
    from_them: int | None = None
    first_message_at: IsoDateTime | None = None
    last_message_at: IsoDateTime | None = None
    last_sender: Literal["me", "them"] | None = None
    needs_reply: bool | None = None
    waiting_hours: float | None = None
    last_message_intent: str | None = None
    median_my_response_minutes: float | None = None
    median_their_response_minutes: float | None = None
    topics: list[str] | None = Field(
        default=None, description="Up to 5 words derived from the other person's messages"
    )
    their_tone: TheirTone | None = None
    my_style: MyStyle | None = None


class DraftOut(Contract):
    option: str
    tone: str
    text: str


class SuggestionsTool(Contract):
    intent: str
    tone: str
    drafts: list[DraftOut]
    caution: str | None
    needs_reply: bool
    draft_only: Literal[True]
    send_supported: Literal[False]
    note: str
    conversation_id: UuidStr


class MessagingSummaryTool(Contract):
    """The internal `memory_proposal` offer is stripped by the orchestrator before it reaches `data`."""

    summary: str
