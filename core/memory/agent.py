"""AgentMemory: the ONLY memory handle an agent receives.

It is bound to a RunContext at construction, so the acting identity (user, agent, grants, execution)
comes from the server-built context and an agent cannot pass a different caller, user id or
"user" role. Writes by an agent are proposals; confirmation/approval/deletion are user-only methods
of MemoryService that this class deliberately does not expose.
"""

import re
import uuid
from collections.abc import Sequence
from typing import Any

from core.memory import context as ctx_render
from core.memory.service import MemoryService
from core.memory.threads import ThreadService
from core.memory.types import (
    Caller,
    ContextBundle,
    MemoryProposal,
    MemoryType,
    MemoryView,
    PolicyViolationError,
    ProposalResult,
)
from core.permissions.runtime import RunContext

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class AgentMemory:
    def __init__(
        self, service: MemoryService, ctx: RunContext, threads: ThreadService | None = None
    ) -> None:
        self._svc = service
        self._ctx = ctx
        self._threads = threads
        self._caller = Caller.agent(ctx)

    async def propose(
        self, proposal: MemoryProposal, *, raw_source: str | None = None
    ) -> ProposalResult:
        return await self._svc.propose(self._caller, proposal, raw_source=raw_source)

    async def propose_from_third_party(
        self,
        *,
        platform: str,
        conversation_id: str,
        raw_text: str,
        summary: str,
        memory_type: MemoryType = "episodic",
        confidence: float = 0.5,
        source_thread_id: str | None = None,
    ) -> ProposalResult:
        """Remember a SUMMARY of a third-party message. `raw_text` is used only to reject copies and
        injection and is discarded; the result is always a pending proposal for the user."""
        if not _ID.match(platform) or not _ID.match(conversation_id):
            raise PolicyViolationError(
                "platform and conversation_id must be opaque lowercase ids (never display names)"
            )
        proposal = MemoryProposal(
            namespace=f"{self._ctx.agent_id}/{platform}/{conversation_id}",
            memory_type=memory_type,
            text=summary,
            sensitivity="third_party",
            source_kind="third_party_message",
            confidence=confidence,
            source_ref=f"{platform}:{conversation_id}",
            source_thread_id=source_thread_id,
        )
        return await self._svc.propose(self._caller, proposal, raw_source=raw_text)

    async def update(self, record_id: uuid.UUID, proposal: MemoryProposal) -> ProposalResult:
        return await self._svc.update(self._caller, record_id, proposal)

    async def get(self, record_id: uuid.UUID) -> MemoryView:
        return await self._svc.get(self._caller, record_id)

    async def list_memories(
        self, namespace: str, *, memory_type: str | None = None, limit: int = 50
    ) -> list[MemoryView]:
        return await self._svc.list_memories(
            self._caller, namespace=namespace, memory_type=memory_type, limit=limit
        )

    async def get_structured(self, namespace: str, key: str) -> MemoryView | None:
        return await self._svc.get_structured(self._caller, namespace, key)

    async def search(
        self, query: str, namespaces: Sequence[str], *, k: int = 5
    ) -> list[MemoryView]:
        return await self._svc.search(self._caller, query, namespaces, k=k)

    async def context(
        self, query: str, namespaces: Sequence[str], *, budget_tokens: int = 1200
    ) -> tuple[ContextBundle, str]:
        bundle = await self._svc.assemble_context(
            self._caller, query, namespaces, budget_tokens=budget_tokens
        )
        return bundle, ctx_render.render(bundle)

    async def new_thread(self) -> str:
        if self._threads is None:
            raise PolicyViolationError("working memory (checkpointer) is not configured")
        return await self._threads.create(self._ctx)

    async def open_thread(self, thread_id: str) -> dict[str, Any]:
        if self._threads is None:
            raise PolicyViolationError("working memory (checkpointer) is not configured")
        return await self._threads.open(self._ctx, thread_id)
