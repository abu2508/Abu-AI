"""MemoryService: the only way to read or change long-term memory.

Invariants (each has tests):
  * Every query is scoped by caller.user_id, which comes from authentication, never from input.
  * Agents act only inside namespaces their active version was explicitly granted (default deny) and
    only while the kill switch is off. Users manage their own memory regardless of agent grants.
  * Content is ALWAYS envelope-encrypted before it touches the database.
  * Proposals (from an LLM) are validated; sensitive / third-party / procedural / consequential /
    instruction-like proposals - and any change to a CONFIRMED fact - wait for the user.
  * Third-party raw text is used only transiently (copy + injection checks) and never persisted.
  * Embeddings come only from an explicitly approved, PII-redacted summary; never for structured
    data, never from raw third-party text.
  * Deletion removes the record, derived summaries, supersession history, embeddings, scrubs access
    logs, and deletes the source threads' checkpoints.
"""

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.service import Actor, record_event, record_event_durable
from core.governance.service import Governance
from core.memory import namespaces as ns_rules
from core.memory.crypto import Envelope, aad_for
from core.memory.embeddings import Embedder, sensitivity_to_data_class
from core.memory.privacy import (
    TextRejectedError,
    copies_source,
    find_pii,
    injection_flags,
    normalize_text,
)
from core.memory.structured import StructuredRegistry, default_registry
from core.memory.threads import ThreadService
from core.memory.types import (
    SENSITIVITY_ORDER,
    Caller,
    ContextBundle,
    ContextItem,
    DeletionReceipt,
    InvalidStateError,
    MemoryNotFoundError,
    MemoryProposal,
    MemoryView,
    PolicyViolationError,
    ProposalResult,
)
from core.permissions.errors import PermissionDeniedError
from database.schemas import DataClass, MemoryAccessLog, MemoryEmbedding, MemoryRecord

_SENSITIVE_TOPICS = (
    r"\b(password|passcode|pin|otp|salary|income|loan|debt|credit|account|balance|ssn|aadhaar|pan|passport|"
    r"medical|diagnos\w*|health|medication|therapy|religion|sexual\w*|orientation|immigration|lawsuit)\b"
)
import re  # noqa: E402

_SENSITIVE_RX = re.compile(_SENSITIVE_TOPICS, re.I)
_EMBED_MAX = 1000


@dataclass(frozen=True)
class MemoryPolicy:
    proposal_days: int = 2
    third_party_days: int = 90
    episodic_days: int = 180
    superseded_days: int = 30

    @classmethod
    def from_settings(cls, settings: Any) -> "MemoryPolicy":
        return cls(
            settings.retention_proposal_days,
            settings.retention_third_party_days,
            settings.retention_episodic_days,
            settings.retention_superseded_days,
        )


def _pack(text_: str | None, data: dict[str, Any] | None) -> bytes:
    return json.dumps(
        {"v": 1, "text": text_, "data": data}, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _unpack(blob: bytes) -> tuple[str | None, dict[str, Any] | None]:
    obj = json.loads(blob)
    return obj.get("text"), obj.get("data")


def sensitivity_floor(caller_kind: str, namespace: str, *texts: str | None) -> str:
    """The LLM declares a sensitivity; the server never trusts it downward."""
    joined = " ".join(t for t in texts if t)
    if find_pii(joined) or _SENSITIVE_RX.search(joined) or namespace.startswith("finance_agent"):
        return "sensitive"
    return "low" if caller_kind == "user" else "personal"


def _max_sensitivity(a: str, b: str) -> str:
    return a if SENSITIVITY_ORDER[a] >= SENSITIVITY_ORDER[b] else b


def _actor(caller: Caller) -> Actor:
    if caller.kind == "admin":
        return Actor.admin()
    if caller.kind == "user":
        return Actor.user(caller.user_id)
    return Actor(
        "system", caller.user_id
    )  # agent / system: the acting agent is named in the detail


class MemoryService:
    def __init__(
        self,
        session: AsyncSession,
        envelope: Envelope,
        embedder: Embedder,
        governance: Governance,
        policy: MemoryPolicy | None = None,
        *,
        threads: ThreadService | None = None,
        structured: StructuredRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._s = session
        self._env = envelope
        self._embedder = embedder
        self._gov = governance
        self._policy = policy or MemoryPolicy()
        self._threads = threads
        self._structured = structured or default_registry()
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ authorisation
    async def _authorize(self, caller: Caller, namespace: str, *, write: bool) -> None:
        ns_rules.validate_format(namespace)
        if caller.kind == "agent":
            if caller.ctx is None:
                raise PermissionDeniedError("agent caller without a run context")
            await (
                self._gov.require_execution_allowed()
            )  # kill switch stops agent-driven memory access
            try:
                caller.ctx.authorize_memory(namespace, write=write)
            except PermissionDeniedError:
                await record_event_durable(
                    self._gov.factory,
                    _actor(caller),
                    "memory.denied",
                    "memory",
                    namespace,
                    {"agent": caller.agent_id or "", "write": write},
                )
                raise
        elif caller.kind not in ("user", "admin", "system"):
            raise PermissionDeniedError("unknown caller")

    async def _own(
        self, caller: Caller, record_id: uuid.UUID, *, for_update: bool = False
    ) -> MemoryRecord:
        stmt = (
            select(MemoryRecord)
            .where(MemoryRecord.id == record_id, MemoryRecord.user_id == caller.user_id)
            .execution_options(populate_existing=True)
        )
        rec = (
            await self._s.execute(stmt.with_for_update() if for_update else stmt)
        ).scalar_one_or_none()
        if rec is None:
            raise MemoryNotFoundError("memory not found")  # also what another user's id looks like
        return rec

    async def _own_readable(self, caller: Caller, record_id: uuid.UUID) -> MemoryRecord:
        rec = await self._own(caller, record_id)
        if caller.kind == "agent":
            try:
                await self._authorize(caller, rec.namespace, write=False)
            except PermissionDeniedError:
                raise MemoryNotFoundError(
                    "memory not found"
                ) from None  # no existence oracle for ids
        return rec

    # ------------------------------------------------------------------ helpers
    def _view(
        self, rec: MemoryRecord, *, similarity: float | None = None, with_embed_text: bool = False
    ) -> MemoryView:
        text_, data = _unpack(
            self._env.open(rec.content_enc, aad_for(rec.user_id, rec.id, "content"))
        )
        embed_text = None
        if with_embed_text and rec.embed_text_enc:
            embed_text = self._env.open(
                rec.embed_text_enc, aad_for(rec.user_id, rec.id, "embed")
            ).decode()
        return MemoryView(
            id=rec.id,
            namespace=rec.namespace,
            memory_type=rec.memory_type,
            status=rec.status,
            key=rec.key,
            schema_name=rec.schema_name,
            text=text_,
            data=data,
            embed_text=embed_text,
            sensitivity=rec.sensitivity,
            source_kind=rec.source_kind,
            source_ref=rec.source_ref,
            created_by=rec.created_by,
            confidence=float(rec.confidence),
            flags=list(rec.flags or []),
            embedding_state=rec.embedding_state,
            created_at=rec.created_at,
            confirmed_at=rec.confirmed_at,
            supersedes_id=rec.supersedes_id,
            retention_expires_at=rec.retention_expires_at,
            similarity=similarity,
        )

    def _retention(
        self, status: str, memory_type: str, source_kind: str, override_days: int | None = None
    ) -> datetime | None:
        now = self._clock()
        if status == "proposed":
            days: int | None = self._policy.proposal_days
        elif status == "superseded":
            days = self._policy.superseded_days
        elif source_kind == "third_party_message":
            days = self._policy.third_party_days
        elif memory_type == "episodic":
            days = self._policy.episodic_days
        else:
            days = None
        if override_days is not None:  # a caller may only SHORTEN retention
            days = override_days if days is None else min(days, override_days)
        return None if days is None else now + timedelta(days=days)

    async def _log(
        self,
        caller: Caller,
        action: str,
        ids: Sequence[uuid.UUID],
        namespace: str | None,
        count: int | None = None,
    ) -> None:
        rows = [
            MemoryAccessLog(
                user_id=caller.user_id,
                actor_type="agent" if caller.kind == "agent" else caller.kind,
                actor_id=caller.actor_id,
                action=action,
                memory_id=mid,
                namespace=namespace,
                execution_id=caller.execution_id,
                result_count=count if count is not None else len(ids),
            )
            for mid in (ids or [None])  # type: ignore[list-item]
        ]
        self._s.add_all(rows)
        await self._s.flush()

    async def _audit(
        self, caller: Caller, event: str, record_id: str, detail: dict[str, Any]
    ) -> None:
        detail = {**detail, "agent": caller.agent_id or ""}
        await record_event(self._s, _actor(caller), event, "memory", record_id, detail)

    # ------------------------------------------------------------------ propose / create
    @staticmethod
    def _check_source(caller: Caller, p: MemoryProposal) -> None:
        if caller.kind == "user":
            if p.source_kind not in ("user_created", "user_statement"):
                raise PolicyViolationError(
                    "a user can only create user_created / user_statement memory"
                )
        elif caller.kind in ("agent", "system"):
            if p.source_kind == "user_created":
                raise PolicyViolationError(
                    "user_created is reserved for memory the user wrote themselves"
                )
        else:
            raise PolicyViolationError("this caller cannot create memory")

    async def _active_by_key(
        self, user_id: uuid.UUID, namespace: str, key: str
    ) -> MemoryRecord | None:
        return (
            await self._s.execute(
                select(MemoryRecord)
                .where(
                    MemoryRecord.user_id == user_id,
                    MemoryRecord.namespace == namespace,
                    MemoryRecord.key == key,
                    MemoryRecord.status == "active",
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _supersede(self, old: MemoryRecord) -> None:
        """Retire a fact: kept encrypted for a short undo window, no longer retrievable, vector removed."""
        old.status = "superseded"
        old.retention_expires_at = self._retention("superseded", old.memory_type, old.source_kind)
        old.updated_at = self._clock()
        await self._s.execute(delete(MemoryEmbedding).where(MemoryEmbedding.memory_id == old.id))
        if old.embedding_state == "embedded":
            old.embedding_state = "approved"
        await self._s.flush()

    async def propose(
        self,
        caller: Caller,
        p: MemoryProposal,
        *,
        raw_source: str | None = None,
        supersedes: MemoryRecord | None = None,
    ) -> ProposalResult:
        """Validate and store a memory. `raw_source` (third-party text) is used ONLY to reject copies
        and is never stored, logged or embedded."""
        self._check_source(caller, p)
        await self._authorize(caller, p.namespace, write=True)
        now = self._clock()

        try:
            text_ = normalize_text(p.text) if p.text else None
            embed_text = normalize_text(p.embed_text, max_len=_EMBED_MAX) if p.embed_text else None
        except TextRejectedError as exc:
            raise PolicyViolationError(str(exc)) from None
        data: dict[str, Any] | None = None
        consequential = False
        if p.memory_type == "structured":
            if not (p.schema_name and p.data is not None and p.key):
                raise PolicyViolationError("structured memory needs schema_name, data and key")
            if embed_text:
                raise PolicyViolationError("structured memory is never embedded")
            data = self._structured.validate(p.schema_name, p.data)
            consequential = self._structured.get(p.schema_name).consequential
        else:
            if text_ is None:
                raise PolicyViolationError("text is required")
            if p.data is not None or p.schema_name is not None:
                raise PolicyViolationError("data/schema_name are for structured memory only")

        if p.source_kind == "third_party_message":
            if p.memory_type == "procedural":
                raise PolicyViolationError(
                    "third-party text can never become an agent rule (procedural memory)"
                )
            try:
                ns_rules.require_conversation_scope(p.namespace)
            except ns_rules.NamespaceError as exc:
                raise PolicyViolationError(str(exc)) from None
            if raw_source is None:
                raise PolicyViolationError(
                    "raw_source is required for third-party content so copies can be rejected"
                )
            if (text_ and copies_source(text_, raw_source)) or (
                embed_text and copies_source(embed_text, raw_source)
            ):
                raise PolicyViolationError(
                    "proposal reproduces the raw third-party message; summarise instead"
                )
        if embed_text:
            kinds = find_pii(embed_text)
            if kinds:
                raise PolicyViolationError(
                    f"embed_text contains personal identifiers ({', '.join(kinds)}); redact it first"
                )

        flags = sorted(set(injection_flags(text_ or "") + injection_flags(embed_text or "")))
        sensitivity = _max_sensitivity(
            p.sensitivity, sensitivity_floor(caller.kind, p.namespace, text_, embed_text)
        )
        if p.source_kind == "third_party_message":
            sensitivity = "third_party"

        parent_id = None
        if p.derived_from is not None:
            parent = await self._own_readable(
                caller, p.derived_from
            )  # same user, readable by caller
            parent_id = parent.id

        existing = supersedes or (
            await self._active_by_key(caller.user_id, p.namespace, p.key) if p.key else None
        )
        replaces_confirmed = existing is not None and (
            existing.confirmed_at is not None or existing.created_by_kind == "user"
        )

        reasons: list[str] = []
        if caller.kind != "user":
            if p.source_kind == "third_party_message":
                reasons.append("third_party_content")
            if sensitivity in ("sensitive", "third_party"):
                reasons.append("sensitive")
            if p.memory_type == "procedural":
                reasons.append("procedural_rule")
            if consequential:
                reasons.append("consequential")
            if flags:
                reasons.append("instruction_like_content")
            if replaces_confirmed:
                reasons.append("replaces_confirmed_fact")
        status = "proposed" if reasons else "active"

        rid = uuid.uuid4()
        user_made = caller.kind == "user"
        rec = MemoryRecord(
            id=rid,
            user_id=caller.user_id,
            namespace=p.namespace,
            memory_type=p.memory_type,
            status=status,
            key=p.key,
            schema_name=p.schema_name if data is not None else None,
            content_enc=self._env.seal(_pack(text_, data), aad_for(caller.user_id, rid, "content")),
            embed_text_enc=self._env.seal(
                embed_text.encode(), aad_for(caller.user_id, rid, "embed")
            )
            if embed_text
            else None,
            embedding_state="none",
            sensitivity=sensitivity,
            source_kind=p.source_kind,
            source_ref=p.source_ref,
            source_thread_id=p.source_thread_id,
            source_execution_id=caller.execution_id,
            created_by="user" if user_made else (caller.agent_id or "system"),
            created_by_kind="user"
            if user_made
            else ("agent" if caller.kind == "agent" else "system"),
            confidence=Decimal("1.00") if user_made else Decimal(str(round(p.confidence, 2))),
            confirmed_at=now if user_made else None,
            supersedes_id=existing.id if existing else None,
            parent_id=parent_id,
            flags=flags,
            retention_expires_at=self._retention(
                status, p.memory_type, p.source_kind, p.retention_days
            ),
        )
        if existing is not None and status == "active":
            await self._supersede(existing)  # frees the unique (user, namespace, key) slot first
        self._s.add(rec)
        await self._s.flush()
        await self._audit(
            caller,
            "memory.proposed" if status == "proposed" else "memory.created",
            str(rid),
            {
                "type": p.memory_type,
                "namespace": p.namespace,
                "status": status,
                "source": p.source_kind,
                "sensitivity": sensitivity,
                "reasons": ",".join(reasons),
                "flags": ",".join(flags),
                "supersedes": str(existing.id) if existing else "",
            },
        )
        await self._log(caller, "create", [rid], p.namespace)
        return ProposalResult(
            rid, status, bool(reasons), reasons, flags, existing.id if existing else None
        )

    # ------------------------------------------------------------------ approve / reject / update
    @staticmethod
    def _require_user(caller: Caller) -> None:
        if caller.kind != "user":
            raise PermissionDeniedError("only the user can approve, reject or edit memory")

    async def approve(
        self,
        caller: Caller,
        record_id: uuid.UUID,
        *,
        allow_embedding: bool = False,
        embed_text: str | None = None,
        text_edit: str | None = None,
    ) -> MemoryView:
        self._require_user(caller)
        rec = await self._own(caller, record_id, for_update=True)
        if rec.status != "proposed":
            raise InvalidStateError("only a proposed memory can be approved")
        if text_edit is not None:
            try:
                new_text = normalize_text(text_edit)
            except TextRejectedError as exc:
                raise PolicyViolationError(str(exc)) from None
            _, data = _unpack(
                self._env.open(rec.content_enc, aad_for(rec.user_id, rec.id, "content"))
            )
            rec.content_enc = self._env.seal(
                _pack(new_text, data), aad_for(rec.user_id, rec.id, "content")
            )
            rec.flags = sorted(set(rec.flags or []) | set(injection_flags(new_text)))
        if rec.supersedes_id is not None:
            old = (
                await self._s.execute(
                    select(MemoryRecord)
                    .where(
                        MemoryRecord.id == rec.supersedes_id, MemoryRecord.user_id == caller.user_id
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if old is not None and old.status == "active":
                await self._supersede(old)
        rec.status = "active"
        rec.confirmed_at = self._clock()
        rec.retention_expires_at = self._retention("active", rec.memory_type, rec.source_kind)
        rec.updated_at = self._clock()
        await self._s.flush()
        if allow_embedding:
            await self._embed(rec, embed_text)
        await self._audit(
            caller,
            "memory.approved",
            str(rec.id),
            {"embedding": allow_embedding, "namespace": rec.namespace},
        )
        await self._log(caller, "approve", [rec.id], rec.namespace)
        return self._view(rec, with_embed_text=True)

    async def reject(self, caller: Caller, record_id: uuid.UUID) -> DeletionReceipt:
        self._require_user(caller)
        rec = await self._own(caller, record_id)
        if rec.status != "proposed":
            raise InvalidStateError(
                "only a proposed memory can be rejected (use delete for stored memory)"
            )
        receipt = await self._delete_ids(
            caller, {rec.id}, purge_sources=False, expand_chain=False, event="memory.rejected"
        )
        return receipt

    async def update(
        self,
        caller: Caller,
        record_id: uuid.UUID,
        p: MemoryProposal,
        *,
        raw_source: str | None = None,
    ) -> ProposalResult:
        """Replace a fact. For a CONFIRMED fact an agent's update only ever creates a pending proposal;
        the old value stays untouched until the user approves."""
        rec = await self._own_readable(caller, record_id)
        if rec.status != "active":
            raise InvalidStateError("only an active memory can be updated")
        if p.namespace != rec.namespace or p.memory_type != rec.memory_type:
            raise PolicyViolationError("an update cannot change namespace or type")
        return await self.propose(
            caller,
            p.model_copy(update={"key": p.key or rec.key}),
            raw_source=raw_source,
            supersedes=rec,
        )

    async def approve_embedding(
        self, caller: Caller, record_id: uuid.UUID, embed_text: str | None = None
    ) -> MemoryView:
        """The user explicitly approves a redacted summary of an already-active memory for embedding."""
        self._require_user(caller)
        rec = await self._own(caller, record_id, for_update=True)
        await self._embed(rec, embed_text)
        await self._audit(
            caller, "memory.embedding_approved", str(rec.id), {"namespace": rec.namespace}
        )
        return self._view(rec, with_embed_text=True)

    async def _embed(self, rec: MemoryRecord, embed_text: str | None) -> None:
        if rec.memory_type == "structured":
            raise PolicyViolationError("structured memory is never embedded")
        if rec.status != "active":
            raise InvalidStateError("only an active memory can be embedded")
        if rec.source_kind == "third_party_message" and rec.confirmed_at is None:
            raise PolicyViolationError(
                "third-party-derived text needs explicit user confirmation before embedding"
            )
        if embed_text is None and rec.embed_text_enc:
            embed_text = self._env.open(
                rec.embed_text_enc, aad_for(rec.user_id, rec.id, "embed")
            ).decode()
        if not embed_text:
            raise PolicyViolationError(
                "no approved embed_text: provide a redacted summary to embed"
            )
        try:
            embed_text = normalize_text(embed_text, max_len=_EMBED_MAX)
        except TextRejectedError as exc:
            raise PolicyViolationError(str(exc)) from None
        kinds = find_pii(embed_text)
        if kinds:
            raise PolicyViolationError(
                f"embed_text contains personal identifiers ({', '.join(kinds)}); redact it first"
            )
        if injection_flags(embed_text):
            raise PolicyViolationError("embed_text looks like instructions and cannot be embedded")
        if self._embedder.max_data_class < sensitivity_to_data_class(rec.sensitivity):
            raise PolicyViolationError(
                f"embedder {self._embedder.id!r} is not approved for {rec.sensitivity} memory"
            )
        vector = (await self._embedder.embed([embed_text]))[0]
        await self._s.execute(delete(MemoryEmbedding).where(MemoryEmbedding.memory_id == rec.id))
        self._s.add(
            MemoryEmbedding(
                memory_id=rec.id,
                user_id=rec.user_id,
                embedder_id=self._embedder.id,
                embedding=vector,
            )
        )
        rec.embed_text_enc = self._env.seal(
            embed_text.encode(), aad_for(rec.user_id, rec.id, "embed")
        )
        rec.embedding_state = "embedded"
        rec.updated_at = self._clock()
        await self._s.flush()

    # ------------------------------------------------------------------ reads
    async def get(self, caller: Caller, record_id: uuid.UUID) -> MemoryView:
        rec = await self._own_readable(caller, record_id)
        rec.last_accessed_at = self._clock()
        await self._log(caller, "read", [rec.id], rec.namespace)
        return self._view(rec, with_embed_text=caller.kind != "agent")

    async def list_memories(
        self,
        caller: Caller,
        *,
        namespace: str | None = None,
        status: str | None = None,
        memory_type: str | None = None,
        limit: int = 100,
    ) -> list[MemoryView]:
        if caller.kind == "agent":
            if namespace is None:
                raise PermissionDeniedError("an agent must name the namespace it reads")
            await self._authorize(caller, namespace, write=False)
        stmt = (
            select(MemoryRecord)
            .where(MemoryRecord.user_id == caller.user_id)
            .execution_options(populate_existing=True)
        )
        if namespace:
            stmt = stmt.where(
                or_(
                    MemoryRecord.namespace == namespace,
                    MemoryRecord.namespace.like(
                        ns_rules.like_prefix(namespace) + "/%", escape="\\"
                    ),
                )
            )
        if caller.kind == "agent":
            stmt = stmt.where(
                MemoryRecord.status == "active"
            )  # agents never see proposals or superseded history
        elif status:
            stmt = stmt.where(MemoryRecord.status == status)
        if memory_type:
            stmt = stmt.where(MemoryRecord.memory_type == memory_type)
        rows = list(
            (
                await self._s.execute(
                    stmt.order_by(MemoryRecord.created_at.desc()).limit(min(limit, 500))
                )
            ).scalars()
        )
        await self._log(caller, "list", [r.id for r in rows], namespace)
        return [self._view(r, with_embed_text=caller.kind != "agent") for r in rows]

    async def get_structured(self, caller: Caller, namespace: str, key: str) -> MemoryView | None:
        """Exact SQL lookup. Never uses vectors."""
        await self._authorize(caller, namespace, write=False)
        rec = (
            await self._s.execute(
                select(MemoryRecord)
                .where(
                    MemoryRecord.user_id == caller.user_id,
                    MemoryRecord.namespace == namespace,
                    MemoryRecord.key == key,
                    MemoryRecord.status == "active",
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if rec is None:
            return None
        await self._log(caller, "read", [rec.id], namespace)
        return self._view(rec)

    async def _search(
        self, caller: Caller, query: str, namespaces: Sequence[str], k: int
    ) -> list[MemoryView]:
        if not namespaces:
            raise PolicyViolationError("name at least one namespace to search")
        for ns in namespaces:
            await self._authorize(caller, ns, write=False)
        if self._embedder.max_data_class < DataClass.SENSITIVE:
            raise PolicyViolationError(
                f"embedder {self._embedder.id!r} is not approved to receive queries about personal data"
            )
        try:
            qtext = normalize_text(query, max_len=_EMBED_MAX)
        except TextRejectedError as exc:
            raise PolicyViolationError(str(exc)) from None
        qvec = (await self._embedder.embed([qtext]))[0]
        now = self._clock()
        distance = MemoryEmbedding.embedding.cosine_distance(qvec).label("distance")
        ns_filter = or_(
            *[
                or_(
                    MemoryRecord.namespace == ns,
                    MemoryRecord.namespace.like(ns_rules.like_prefix(ns) + "/%", escape="\\"),
                )
                for ns in namespaces
            ]
        )
        stmt = (
            select(MemoryRecord, distance)
            .join(MemoryEmbedding, MemoryEmbedding.memory_id == MemoryRecord.id)
            .where(
                MemoryRecord.user_id == caller.user_id,
                MemoryEmbedding.user_id == caller.user_id,
                MemoryEmbedding.embedder_id == self._embedder.id,
                MemoryRecord.status == "active",
                ns_filter,
                or_(
                    MemoryRecord.retention_expires_at.is_(None),
                    MemoryRecord.retention_expires_at > now,
                ),
                # injection-flagged text is only retrievable once the user has reviewed (confirmed) it
                or_(
                    MemoryRecord.confirmed_at.is_not(None),
                    func.jsonb_array_length(MemoryRecord.flags) == 0,
                ),
            )
            .order_by(distance)
            .limit(k)
            .execution_options(populate_existing=True)
        )
        # pgvector >= 0.8: keep scanning the HNSW index until enough rows pass the filters above.
        await self._s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        rows = (await self._s.execute(stmt)).all()
        return [self._view(r, similarity=1.0 - float(d)) for r, d in rows]

    async def search(
        self, caller: Caller, query: str, namespaces: Sequence[str], *, k: int = 5
    ) -> list[MemoryView]:
        hits = await self._search(caller, query, namespaces, k)
        for h in hits:
            await self._s.execute(
                update(MemoryRecord)
                .where(MemoryRecord.id == h.id)
                .values(last_accessed_at=self._clock())
            )
        await self._log(
            caller, "search", [h.id for h in hits], ",".join(namespaces)[:128], len(hits)
        )
        return hits

    async def assemble_context(
        self,
        caller: Caller,
        query: str,
        namespaces: Sequence[str],
        *,
        budget_tokens: int = 1200,
        k: int = 8,
    ) -> ContextBundle:
        """Relevance-ranked, budgeted context. Everything is DATA: see core/memory/context.py render()."""
        hits = await self._search(caller, query, namespaces, k * 2)
        now = self._clock()

        def to_item(v: MemoryView, sim: float | None) -> ContextItem:
            age_days = max(0.0, (now - v.created_at).total_seconds() / 86400)
            half_life = {"episodic": 30.0, "semantic": 365.0, "procedural": 365.0}.get(
                v.memory_type, 365.0
            )
            recency = 0.5 ** (age_days / half_life)
            score = (
                0.55 * max(0.0, sim or 0.0)
                + 0.20 * recency
                + 0.15 * v.confidence
                + 0.10 * (1.0 if v.confirmed_at else 0.0)
            )
            return ContextItem(
                v.id,
                v.namespace,
                v.memory_type,
                v.text or "",
                round(score, 4),
                sim,
                v.confirmed_at is not None,
                v.source_kind,
                v.confidence,
                v.created_at,
            )

        rules: list[ContextItem] = []
        for ns in namespaces:
            for v in await self._confirmed_rules(caller, ns):
                rules.append(to_item(v, None))
        items = sorted(
            (to_item(h, h.similarity) for h in hits if h.memory_type != "procedural"),
            key=lambda i: i.score,
            reverse=True,
        )
        chosen: list[ContextItem] = []
        used = sum(len(r.text) // 4 + 1 for r in rules)
        omitted = 0
        for item in items:
            cost = len(item.text) // 4 + 1
            if len(chosen) < k and used + cost <= budget_tokens:
                chosen.append(item)
                used += cost
            else:
                omitted += 1
        await self._log(
            caller,
            "context",
            [i.id for i in chosen] + [r.id for r in rules],
            ",".join(namespaces)[:128],
            len(chosen),
        )
        basis = "semantic" if self._embedder.is_semantic else "lexical-test"
        return ContextBundle(chosen, rules, omitted, basis, used)

    async def _confirmed_rules(self, caller: Caller, namespace: str) -> list[MemoryView]:
        rows = (
            await self._s.execute(
                select(MemoryRecord)
                .where(
                    MemoryRecord.user_id == caller.user_id,
                    MemoryRecord.memory_type == "procedural",
                    MemoryRecord.status == "active",
                    MemoryRecord.confirmed_at.is_not(None),
                    or_(
                        MemoryRecord.namespace == namespace,
                        MemoryRecord.namespace.like(
                            ns_rules.like_prefix(namespace) + "/%", escape="\\"
                        ),
                    ),
                )
                .order_by(MemoryRecord.created_at)
                .limit(10)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        return [self._view(r) for r in rows]

    # ------------------------------------------------------------------ deletion
    async def _closure(
        self, user_id: uuid.UUID, ids: set[uuid.UUID], expand_chain: bool
    ) -> set[uuid.UUID]:
        """The records that must go together: derived summaries (parent_id) and, when expand_chain,
        the whole supersession history, so a fact cannot survive in an older version."""
        found = set(
            (
                await self._s.execute(
                    select(MemoryRecord.id).where(
                        MemoryRecord.user_id == user_id, MemoryRecord.id.in_(ids)
                    )
                )
            ).scalars()
        )
        frontier = set(found)
        while frontier:
            conds = [MemoryRecord.parent_id.in_(frontier)]
            if expand_chain:
                conds += [
                    MemoryRecord.supersedes_id.in_(frontier),
                    MemoryRecord.id.in_(
                        select(MemoryRecord.supersedes_id).where(MemoryRecord.id.in_(frontier))
                    ),
                ]
            more = (
                set(
                    (
                        await self._s.execute(
                            select(MemoryRecord.id).where(
                                MemoryRecord.user_id == user_id, or_(*conds)
                            )
                        )
                    ).scalars()
                )
                - found
            )
            found |= more
            frontier = more
        return found

    async def _delete_ids(
        self,
        caller: Caller,
        ids: set[uuid.UUID],
        *,
        purge_sources: bool,
        expand_chain: bool,
        event: str = "memory.deleted",
    ) -> DeletionReceipt:
        closure = await self._closure(caller.user_id, ids, expand_chain)
        if not closure:
            return DeletionReceipt(0, 0, 0, 0)
        thread_ids = {
            t
            for t in (
                await self._s.execute(
                    select(MemoryRecord.source_thread_id).where(MemoryRecord.id.in_(closure))
                )
            ).scalars()
            if t
        }
        embeddings = (
            await self._s.execute(
                select(func.count())
                .select_from(MemoryEmbedding)
                .where(MemoryEmbedding.memory_id.in_(closure))
            )
        ).scalar_one()
        threads_deleted = 0
        if purge_sources and thread_ids:
            if self._threads is None:
                raise InvalidStateError(
                    "memory has source threads but no checkpointer is configured to purge them"
                )
            threads_deleted = await self._threads.delete(
                caller.user_id, list(thread_ids)
            )  # checkpoints FIRST
        await self._s.execute(
            delete(MemoryRecord).where(MemoryRecord.id.in_(closure))
        )  # cascades embeddings
        scrubbed = (
            await self._s.execute(
                update(MemoryAccessLog)
                .where(
                    MemoryAccessLog.memory_id.in_(closure),
                    MemoryAccessLog.user_id == caller.user_id,
                )
                .values(memory_id=None)
            )
        ).rowcount  # type: ignore[attr-defined]
        await self._s.flush()
        await record_event(
            self._s,
            _actor(caller),
            event,
            "memory",
            ",".join(sorted(str(i) for i in closure))[:128],
            {
                "records": len(closure),
                "embeddings": embeddings,
                "threads": threads_deleted,
                "purge_sources": purge_sources,
            },
        )
        return DeletionReceipt(len(closure), embeddings, threads_deleted, scrubbed, sorted(closure))

    async def delete_record(
        self, caller: Caller, record_id: uuid.UUID, *, purge_sources: bool = True
    ) -> DeletionReceipt:
        if caller.kind not in ("user", "admin"):
            raise PermissionDeniedError("only the user or the operator can delete memory")
        await self._own(caller, record_id)
        return await self._delete_ids(
            caller, {record_id}, purge_sources=purge_sources, expand_chain=True
        )

    async def delete_namespace(
        self, caller: Caller, namespace: str, *, purge_sources: bool = True
    ) -> DeletionReceipt:
        if caller.kind not in ("user", "admin"):
            raise PermissionDeniedError("only the user or the operator can delete memory")
        ns_rules.validate_format(namespace)
        ids = set(
            (
                await self._s.execute(
                    select(MemoryRecord.id).where(
                        MemoryRecord.user_id == caller.user_id,
                        or_(
                            MemoryRecord.namespace == namespace,
                            MemoryRecord.namespace.like(
                                ns_rules.like_prefix(namespace) + "/%", escape="\\"
                            ),
                        ),
                    )
                )
            ).scalars()
        )
        return await self._delete_ids(caller, ids, purge_sources=purge_sources, expand_chain=True)

    async def erase_user(self, caller: Caller) -> DeletionReceipt:
        """Right to erasure: ALL memory, ALL threads/checkpoints and ALL memory access logs of the user."""
        if caller.kind not in ("user", "admin"):
            raise PermissionDeniedError("only the user or the operator can erase memory")
        ids = set(
            (
                await self._s.execute(
                    select(MemoryRecord.id).where(MemoryRecord.user_id == caller.user_id)
                )
            ).scalars()
        )
        receipt = await self._delete_ids(
            caller, ids, purge_sources=True, expand_chain=True, event="memory.erase_started"
        )
        threads = receipt.threads
        if self._threads is not None:
            remaining = [t.thread_id for t in await self._threads.list_for_user(caller.user_id)]
            threads += await self._threads.delete(caller.user_id, remaining)
        logs = (
            await self._s.execute(
                delete(MemoryAccessLog).where(MemoryAccessLog.user_id == caller.user_id)
            )
        ).rowcount  # type: ignore[attr-defined]
        await record_event(
            self._s,
            _actor(caller),
            "memory.erased",
            "user",
            str(caller.user_id),
            {"records": receipt.records, "threads": threads, "access_logs": logs},
        )
        return DeletionReceipt(
            receipt.records, receipt.embeddings, threads, logs, receipt.record_ids
        )

    async def purge_expired(self, now: datetime | None = None) -> dict[str, int]:
        """Retention enforcement (operator job): expired proposals, third-party summaries, episodic
        memory, superseded history and expired threads."""
        now = now or self._clock()
        rows = (
            await self._s.execute(
                select(MemoryRecord.id, MemoryRecord.user_id).where(
                    MemoryRecord.retention_expires_at <= now
                )
            )
        ).all()
        by_user: dict[uuid.UUID, set[uuid.UUID]] = {}
        for rid, uid in rows:
            by_user.setdefault(uid, set()).add(rid)
        records = embeddings = 0
        for uid, ids in by_user.items():
            r = await self._delete_ids(
                Caller.system(uid),
                ids,
                purge_sources=False,
                expand_chain=False,
                event="memory.purged",
            )
            records, embeddings = records + r.records, embeddings + r.embeddings
        threads = await self._threads.purge_expired(now) if self._threads else 0
        return {"records": records, "embeddings": embeddings, "threads": threads}

    # ------------------------------------------------------------------ operator maintenance
    async def rewrap_batch(self, limit: int = 200) -> int:
        """Key rotation: re-wrap DEKs under the ACTIVE key (content ciphertext untouched)."""
        n = 0
        rows = (
            await self._s.execute(
                select(MemoryRecord)
                .order_by(MemoryRecord.id)
                .limit(limit * 5)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        for rec in rows:
            changed = False
            for col, purpose in (("content_enc", "content"), ("embed_text_enc", "embed")):
                blob = getattr(rec, col)
                if blob is None:
                    continue
                aad = aad_for(rec.user_id, rec.id, purpose)
                if self._env.key_id_of(blob) != self._env._p.active_key_id:  # noqa: SLF001
                    setattr(rec, col, self._env.rewrap(blob, aad))
                    changed = True
            if changed:
                n += 1
                if n >= limit:
                    break
        await self._s.flush()
        return n

    async def reembed_all(self) -> int:
        """Rebuild vectors for every APPROVED embed_text with the CURRENT embedder (explicit, operator-run)."""
        rows = (
            await self._s.execute(
                select(MemoryRecord)
                .where(
                    MemoryRecord.status == "active",
                    MemoryRecord.embed_text_enc.is_not(None),
                    MemoryRecord.embedding_state.in_(("approved", "embedded")),
                )
                .execution_options(populate_existing=True)
            )
        ).scalars()
        n = 0
        for rec in rows:
            await self._embed(rec, None)
            n += 1
        return n

    async def stats(self) -> dict[str, Any]:
        by = (
            await self._s.execute(
                select(MemoryRecord.memory_type, MemoryRecord.status, func.count()).group_by(
                    MemoryRecord.memory_type, MemoryRecord.status
                )
            )
        ).all()
        emb = (
            await self._s.execute(select(func.count()).select_from(MemoryEmbedding))
        ).scalar_one()
        return {
            "records": {f"{t}/{s}": c for t, s, c in by},
            "embeddings": emb,
            "embedder": self._embedder.id,
            "embedder_is_semantic": self._embedder.is_semantic,
        }
