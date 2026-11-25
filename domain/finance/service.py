"""Finance service: the only place that reads/writes finance tables. Every method takes the user id of the
AUTHENTICATED caller (or of the governed RunContext) and filters by it; ids from clients are always
re-checked against that user. No method moves money, contacts a bank or sends anything."""

import difflib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.schemas import (
    FinAccount,
    FinBudget,
    FinImportBatch,
    FinLoan,
    FinMerchantRule,
    FinSubscription,
    FinTransaction,
)
from domain.common.crypto import FieldCipher
from domain.finance import analytics as an
from domain.finance import importers
from domain.finance.money import ParseError, q
from domain.finance.normalize import (
    CATEGORIES,
    CONF_USER,
    categorise,
    classify,
    is_low_confidence,
    normalise_merchant,
)
from domain.finance.notifications import parse_notification
from domain.finance.types import ImportFailed, RawTxn
from domain.finance.voice import parse_voice

NOTIF = frozenset({"sms", "email", "shortcut", "share"})
STMT = frozenset({"csv", "xlsx", "pdf"})
FUZZY_DAYS = 2
FUZZY_RATIO = 0.5
TRANSFER_WINDOW_DAYS = (
    3  # both legs of an own-account transfer post within this many days of each other
)
EMI_WINDOW_DAYS = (
    5  # an EMI debit may differ from the schedule's due date by this many days and still match
)
MIN_DESC = "unknown"


class FinanceError(Exception):
    """Safe-to-show failure (no content). `code` is stable."""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Outcome:
    status: str  # inserted | reconciled | duplicate | unparsed | needs_account
    txn_id: uuid.UUID | None = None
    txn_status: str | None = None
    confidence: Decimal | None = None
    reason: str | None = None


def _similar(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if MIN_DESC.upper() in (a, b) or not a or not b:
        return 0.6  # nothing to contradict: same amount/date/account is enough to flag
    return difflib.SequenceMatcher(None, a, b).ratio()


class FinanceService:
    def __init__(
        self, session: AsyncSession, cipher: FieldCipher, today: date | None = None
    ) -> None:
        self._s, self._c = session, cipher
        self._today = today

    def today(self) -> date:
        return self._today or datetime.now(UTC).date()

    # ------------------------------------------------------------------ accounts
    async def create_account(
        self,
        user_id: uuid.UUID,
        *,
        kind: str,
        label: str,
        institution: str | None = None,
        last4: str | None = None,
        opening_balance: Decimal = Decimal(0),
        credit_limit: Decimal | None = None,
        statement_day: int | None = None,
        due_day: int | None = None,
        auto_confirm: bool = False,
    ) -> FinAccount:
        if kind == "credit_card" and credit_limit is None:
            raise FinanceError("credit_limit_required", "a credit card needs a credit limit")
        rid = uuid.uuid4()
        row = FinAccount(
            id=rid,
            user_id=user_id,
            kind=kind,
            label_enc=self._c.seal(user_id, rid, "fin.account.label", label[:80]),
            institution=institution,
            last4=last4,
            opening_balance=q(opening_balance),
            credit_limit=credit_limit and q(credit_limit),
            statement_day=statement_day,
            due_day=due_day,
            auto_confirm=auto_confirm,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def accounts(
        self, user_id: uuid.UUID, include_archived: bool = False
    ) -> list[FinAccount]:
        q_ = select(FinAccount).where(FinAccount.user_id == user_id).order_by(FinAccount.created_at)
        if not include_archived:
            q_ = q_.where(FinAccount.archived_at.is_(None))
        return list((await self._s.execute(q_)).scalars())

    async def account(self, user_id: uuid.UUID, account_id: uuid.UUID) -> FinAccount:
        row = (
            await self._s.execute(
                select(FinAccount).where(FinAccount.id == account_id, FinAccount.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise FinanceError("not_found", "account not found", 404)
        return row

    def account_label(self, a: FinAccount) -> str:
        return self._c.open(a.user_id, a.id, "fin.account.label", a.label_enc)

    # ------------------------------------------------------------------ ingestion core
    async def _rule(self, user_id: uuid.UUID, merchant_hash: str | None) -> str | None:
        if not merchant_hash:
            return None
        return (
            await self._s.execute(
                select(FinMerchantRule.category).where(
                    FinMerchantRule.user_id == user_id,
                    FinMerchantRule.merchant_hash == merchant_hash,
                )
            )
        ).scalar_one_or_none()

    async def _candidates(
        self, user_id: uuid.UUID, account_id: uuid.UUID, raw: RawTxn
    ) -> list[FinTransaction]:
        lo, hi = (
            raw.txn_date - timedelta(days=FUZZY_DAYS),
            raw.txn_date + timedelta(days=FUZZY_DAYS),
        )
        return list(
            (
                await self._s.execute(
                    select(FinTransaction)
                    .where(
                        FinTransaction.user_id == user_id,
                        FinTransaction.account_id == account_id,
                        FinTransaction.amount == raw.amount,
                        FinTransaction.direction == raw.direction,
                        FinTransaction.txn_date.between(lo, hi),
                        FinTransaction.status != "rejected",
                    )
                    .order_by(FinTransaction.created_at)
                )
            ).scalars()
        )

    async def add(
        self,
        user_id: uuid.UUID,
        account: FinAccount,
        raw: RawTxn,
        source: str,
        *,
        source_hash: str | None = None,
        confidence: Decimal = Decimal(1),
        batch_id: uuid.UUID | None = None,
        category: str | None = None,
    ) -> Outcome:
        """The single insertion path: normalise -> categorise -> exact dedupe -> fuzzy dedupe/reconcile -> store."""
        if account.user_id != user_id:
            raise FinanceError("not_found", "account not found", 404)
        desc = (raw.description or MIN_DESC).strip()[:300] or MIN_DESC
        norm = normalise_merchant(desc) or MIN_DESC.upper()
        mh = self._c.hash(user_id, "fin.merchant", norm)
        ref_hash = self._c.hash(user_id, "fin.ref", raw.reference) if raw.reference else None
        dedupe = self._c.hash(
            user_id,
            "fin.dedupe",
            str(account.id),
            raw.txn_date.isoformat(),
            str(raw.amount),
            raw.direction,
            raw.reference or "",
        )
        if (
            source_hash
            and (
                await self._s.execute(
                    select(FinTransaction.id).where(
                        FinTransaction.user_id == user_id, FinTransaction.source_hash == source_hash
                    )
                )
            ).first()
        ):
            return Outcome("duplicate", reason="already_ingested")
        if ref_hash and (
            existing := (
                await self._s.execute(
                    select(FinTransaction)
                    .where(
                        FinTransaction.user_id == user_id,
                        FinTransaction.dedupe_key == dedupe,
                        FinTransaction.status != "rejected",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        ):
            if source in STMT and existing.source in NOTIF and existing.reconciled_at is None:
                existing.reconciled_at = datetime.now(UTC)
                return Outcome("reconciled", existing.id, existing.status)
            return Outcome("duplicate", existing.id, existing.status, reason="same_reference")

        dup_of: FinTransaction | None = None
        for cand in await self._candidates(user_id, account.id, raw):
            cdesc = (
                normalise_merchant(
                    self._c.open(user_id, cand.id, "fin.txn.desc", cand.description_enc)
                )
                if cand.description_enc
                else MIN_DESC.upper()
            )
            same_ref = bool(ref_hash and cand.reference_hash == ref_hash)
            if same_ref or _similar(norm, cdesc) >= FUZZY_RATIO:
                if source in STMT and cand.source in NOTIF and cand.reconciled_at is None:
                    cand.reconciled_at = datetime.now(UTC)
                    return Outcome("reconciled", cand.id, cand.status)
                if source in NOTIF and cand.source in STMT:
                    cand.reconciled_at = cand.reconciled_at or datetime.now(UTC)
                    return Outcome("duplicate", cand.id, cand.status, reason="already_in_statement")
                dup_of = cand
                break

        rule = await self._rule(user_id, mh)
        cls = classify(desc, raw.direction, account.kind, category or rule)
        reason: str | None = None
        if dup_of is not None:
            status = "duplicate_suspect"
        else:
            status = (
                "pending_review"
                if source in NOTIF | {"voice"} and not account.auto_confirm
                else "confirmed"
            )
            # Review routing (S2.1A): applies to EVERY source and even to auto_confirm accounts.
            if raw.direction == "credit" and is_low_confidence(cls.confidence):
                status, reason = (
                    "pending_review",
                    "unknown_credit",
                )  # an unknown credit is never silently income
            elif cls.transfer_evidence:
                status, reason = (
                    "pending_review",
                    "unlinked_transfer",
                )  # held until the other leg is found or the user decides
        split = None
        if dup_of is None and cls.kind == "emi" and raw.direction == "debit":
            split = await self._emi_split_for(user_id, raw)
            if split is None:
                status, reason = (
                    "pending_review",
                    "emi_unverified",
                )  # no reliable principal/interest split
        tid = uuid.uuid4()
        row = FinTransaction(
            id=tid,
            user_id=user_id,
            account_id=account.id,
            txn_date=raw.txn_date,
            amount=raw.amount,
            direction=raw.direction,
            category=cls.category,
            kind=cls.kind,
            description_enc=self._c.seal(user_id, tid, "fin.txn.desc", desc),
            merchant_hash=mh,
            reference_hash=ref_hash,
            dedupe_key=dedupe,
            source_hash=source_hash,
            source=source,
            status=status,
            confidence=min(confidence, Decimal(1)),
            duplicate_of=dup_of.id if dup_of else None,
            batch_id=batch_id,
            classification_confidence=cls.confidence,
            review_reason=reason,
        )
        if split is not None:
            loan, inst = split
            row.loan_id, row.emi_principal, row.emi_interest = (
                loan.id,
                inst.principal,
                inst.interest,
            )
        self._s.add(row)
        await self._s.flush()
        if reason == "unlinked_transfer":
            await self._try_link(user_id, row)
        return Outcome("inserted", tid, row.status, confidence)

    # ------------------------------------------------------------------ transfer links (S2.1A)
    @staticmethod
    def _link_eligible(t: FinTransaction) -> bool:
        """A leg that may take part in an automatic link. NEVER a confirmed non-transfer row, a legacy row (NULL
        confidence), a duplicate suspect, a rejected row or an already-linked row. A pending leg must carry its own
        description evidence (confidence below 1.00); a confirmed leg must be an explicit user assertion (1.00)."""
        c = t.classification_confidence
        if (
            c is None
            or t.kind != "transfer"
            or t.transfer_peer_id is not None
            or t.duplicate_of is not None
        ):
            return False
        if t.status == "pending_review":
            return t.review_reason in ("unlinked_transfer", "ambiguous_transfer") and c < CONF_USER
        return t.status == "confirmed" and c == CONF_USER

    async def _partners(self, user_id: uuid.UUID, row: FinTransaction) -> list[FinTransaction]:
        lo = row.txn_date - timedelta(days=TRANSFER_WINDOW_DAYS)
        hi = row.txn_date + timedelta(days=TRANSFER_WINDOW_DAYS)
        evidenced = and_(
            FinTransaction.status == "pending_review",
            FinTransaction.review_reason.in_(("unlinked_transfer", "ambiguous_transfer")),
            FinTransaction.classification_confidence < CONF_USER,
        )
        asserted = and_(
            FinTransaction.status == "confirmed",
            FinTransaction.classification_confidence == CONF_USER,
        )
        res = await self._s.execute(
            select(FinTransaction)
            .where(
                FinTransaction.user_id == user_id,
                FinTransaction.id != row.id,
                FinTransaction.account_id != row.account_id,
                FinTransaction.direction == ("credit" if row.direction == "debit" else "debit"),
                FinTransaction.amount
                == row.amount,  # exact; V1 is INR only so the currency always matches
                FinTransaction.txn_date.between(lo, hi),
                FinTransaction.kind == "transfer",
                FinTransaction.transfer_peer_id.is_(None),
                FinTransaction.duplicate_of.is_(None),
                FinTransaction.classification_confidence.is_not(None),
                or_(evidenced, asserted),
            )
            .order_by(FinTransaction.created_at, FinTransaction.id)
            .with_for_update()
        )
        return list(res.scalars())

    async def _link_pair(self, a: FinTransaction, b: FinTransaction) -> None:
        """Both legs in ONE flush (the request transaction is the atomic unit; a deferred database trigger proves the
        link is reciprocal at COMMIT). Only the peer pointer, status and reason change: amounts, dates, descriptions,
        categories, kinds and confidences of an established row are never rewritten."""
        for x, y in ((a, b), (b, a)):
            x.transfer_peer_id = y.id
            x.kind = "transfer"
            x.status = "confirmed"
            x.review_reason = None
        await self._s.flush()

    async def _try_link(self, user_id: uuid.UUID, t: FinTransaction) -> bool:
        """Link `t` with its counterpart ONLY if exactly one eligible partner exists AND `t` is that partner's only
        eligible partner. Anything else stays under review (ambiguous matches are marked, never guessed)."""
        if not self._link_eligible(t):
            return False
        cands = await self._partners(user_id, t)
        unique = len(cands) == 1
        if unique:
            back = await self._partners(user_id, cands[0])
            unique = len(back) == 1 and back[0].id == t.id
        if not unique:
            if cands and t.status == "pending_review":
                t.review_reason = "ambiguous_transfer"
            return False
        await self._link_pair(t, cands[0])
        return True

    async def _unlink(self, user_id: uuid.UUID, t: FinTransaction) -> None:
        """Detach BOTH legs. A survivor that was confirmed only because of the link goes back to review; a leg the user
        asserted (confidence 1.00) stays confirmed. Nothing is deleted."""
        if t.transfer_peer_id is None:
            return
        peer = await self._s.get(FinTransaction, t.transfer_peer_id)
        t.transfer_peer_id = None
        if peer is not None and peer.user_id == user_id:
            peer.transfer_peer_id = None
            c = peer.classification_confidence
            if (
                peer.status == "confirmed"
                and peer.kind == "transfer"
                and c is not None
                and c < CONF_USER
            ):
                peer.status, peer.review_reason = "pending_review", "unlinked_transfer"
        await self._s.flush()

    # ------------------------------------------------------------------ EMI (S2.1A)
    async def _emi_split_for(
        self, user_id: uuid.UUID, raw: RawTxn
    ) -> tuple[FinLoan, an.Installment] | None:
        """A split is RELIABLE only when exactly one active loan has an instalment whose EMI equals the amount exactly and
        whose due date is within EMI_WINDOW_DAYS of the transaction date."""
        hits = [
            (loan, inst)
            for loan in await self.loans(user_id)
            if loan.status == "active"
            for inst in self.loan_schedule(loan)
            if inst.emi == raw.amount and abs((inst.due - raw.txn_date).days) <= EMI_WINDOW_DAYS
        ]
        return hits[0] if len(hits) == 1 else None

    async def set_emi_split(
        self,
        user_id: uuid.UUID,
        txn_id: uuid.UUID,
        principal: Decimal,
        interest: Decimal,
        loan_id: uuid.UUID | None = None,
    ) -> FinTransaction:
        t = await self._own_txn(user_id, txn_id)
        if (
            t.kind != "emi"
            or t.direction != "debit"
            or t.status not in ("pending_review", "confirmed")
        ):
            raise FinanceError(
                "invalid_emi_split", "only an EMI debit under review or confirmed can be split"
            )
        p, i = q(principal), q(interest)
        if p < 0 or i < 0 or p + i != t.amount:
            raise FinanceError(
                "invalid_emi_split",
                "principal and interest must be non-negative and add up to the amount",
            )
        if loan_id is not None:
            loan = (
                await self._s.execute(
                    select(FinLoan).where(
                        FinLoan.id == loan_id,
                        FinLoan.user_id == user_id,
                        FinLoan.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if loan is None:
                raise FinanceError("invalid_emi_split", "loan not found")
        t.emi_principal, t.emi_interest, t.loan_id = p, i, loan_id
        t.classification_confidence = CONF_USER  # the user decided
        if t.status == "pending_review" and t.review_reason == "emi_unverified":
            t.review_reason = None
        return t

    # ------------------------------------------------------------------ entry channels
    async def add_manual(
        self, user_id: uuid.UUID, account_id: uuid.UUID, raw: RawTxn, category: str | None = None
    ) -> Outcome:
        if category is not None and category not in CATEGORIES:
            raise FinanceError("invalid_category", "unknown category")
        return await self.add(
            user_id, await self.account(user_id, account_id), raw, "manual", category=category
        )

    async def add_voice(
        self, user_id: uuid.UUID, account_id: uuid.UUID, transcript: str
    ) -> Outcome:
        try:
            raw = parse_voice(transcript, self.today())
        except ParseError as exc:
            if str(exc) == "unsupported_currency":
                raise FinanceError(
                    "unsupported_currency", "only INR is supported in this version"
                ) from None
            raise FinanceError(
                "voice_unparsed", f"could not understand the entry ({exc})"
            ) from None
        return await self.add(
            user_id,
            await self.account(user_id, account_id),
            raw,
            "voice",
            confidence=Decimal("0.70"),
        )

    async def ingest_notification(
        self,
        user_id: uuid.UUID,
        channel: str,
        text: str,
        *,
        received_on: date | None = None,
        account_id: uuid.UUID | None = None,
    ) -> Outcome:
        """Raw text is parsed and discarded. Nothing about the sender/device is assumed or stored."""
        if channel not in NOTIF:
            raise FinanceError("invalid_channel", "channel must be sms, email, shortcut or share")
        parsed = parse_notification(text, received_on or self.today())
        if parsed is None:
            return Outcome("unparsed", reason="not_a_transaction_alert")
        accounts = await self.accounts(user_id)
        if account_id is not None:
            account = await self.account(user_id, account_id)
        else:
            hits = [a for a in accounts if a.last4 and a.last4 == parsed.txn.account_last4]
            if len(hits) > 1:
                is_card = bool(re.search(r"\bcard\b", text, re.I))
                hits = [a for a in hits if (a.kind == "credit_card") == is_card] or hits
            if len(hits) != 1:
                return Outcome("needs_account", reason="no_unique_account_match")
            account = hits[0]
        norm_text = re.sub(r"\s+", " ", text).strip().lower()
        return await self.add(
            user_id,
            account,
            parsed.txn,
            channel,
            confidence=parsed.confidence,
            source_hash=self._c.hash(user_id, "fin.notif", norm_text),
        )

    async def import_statement(
        self, user_id: uuid.UUID, account_id: uuid.UUID, kind: str, data: bytes
    ) -> dict[str, Any]:
        account = await self.account(user_id, account_id)
        reader = {
            "csv": importers.import_csv,
            "xlsx": importers.import_xlsx,
            "pdf": importers.import_pdf,
        }.get(kind)
        if reader is None:
            raise FinanceError("invalid_format", "format must be csv, xlsx or pdf")
        try:
            result = reader(data, self.today(), account.kind)
        except importers.AmbiguousDirection:
            raise FinanceError(
                "ambiguous_card_amount_sign",
                "this card statement does not say whether an amount is a purchase or a payment; use Debit/Credit "
                "columns, a Type column or Dr/Cr markers",
            ) from None
        except ImportFailed as exc:
            raise FinanceError("import_failed", f"the file could not be imported ({exc})") from None
        fhash = self._c.hash(user_id, "fin.file", kind, str(account_id), _sha(data))
        if (
            await self._s.execute(
                select(FinImportBatch.id).where(
                    FinImportBatch.user_id == user_id, FinImportBatch.file_hash == fhash
                )
            )
        ).first():
            raise FinanceError("already_imported", "this exact file was already imported", 409)
        batch = FinImportBatch(
            user_id=user_id,
            account_id=account_id,
            source=kind,
            file_hash=fhash,
            parsed=len(result.rows) + result.rejected,
        )
        self._s.add(batch)
        await self._s.flush()
        seen: dict[tuple[object, ...], int] = {}
        counts = {"inserted": 0, "duplicate": 0, "reconciled": 0}
        for raw in result.rows:
            key = (
                raw.txn_date,
                raw.amount,
                raw.direction,
                normalise_merchant(raw.description),
                raw.reference,
            )
            seen[key] = (
                seen.get(key, 0) + 1
            )  # two identical legitimate rows in one file stay distinct
            sh = self._c.hash(
                user_id, "fin.row", str(account_id), *(str(k) for k in key), str(seen[key])
            )
            out = await self.add(user_id, account, raw, kind, source_hash=sh, batch_id=batch.id)
            counts[out.status] += 1
        batch.imported, batch.duplicates, batch.reconciled, batch.rejected = (
            counts["inserted"],
            counts["duplicate"],
            counts["reconciled"],
            result.rejected,
        )
        return {
            "batch_id": batch.id,
            "parsed": batch.parsed,
            "imported": batch.imported,
            "duplicates": batch.duplicates,
            "reconciled": batch.reconciled,
            "rejected": batch.rejected,
            "reject_reasons": result.reasons,
        }

    # ------------------------------------------------------------------ reading / review
    def view(self, t: FinTransaction) -> dict[str, Any]:
        return {
            "id": t.id,
            "account_id": t.account_id,
            "date": t.txn_date,
            "amount": t.amount,
            "direction": t.direction,
            "category": t.category,
            "kind": t.kind,
            "status": t.status,
            "source": t.source,
            "confidence": t.confidence,
            "description": self._c.open(t.user_id, t.id, "fin.txn.desc", t.description_enc)
            if t.description_enc
            else None,
            "duplicate_of": t.duplicate_of,
            "reconciled": t.reconciled_at is not None,
            "classification_confidence": t.classification_confidence,
            "review_reason": t.review_reason,
            "transfer_peer_id": t.transfer_peer_id,
            "loan_id": t.loan_id,
            "emi_principal": t.emi_principal,
            "emi_interest": t.emi_interest,
        }

    async def transactions(
        self,
        user_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        status: str | None = None,
        category: str | None = None,
        account_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FinTransaction]:
        q_ = select(FinTransaction).where(FinTransaction.user_id == user_id)
        for cond, val in (
            (FinTransaction.status == status, status),
            (FinTransaction.category == category, category),
            (FinTransaction.account_id == account_id, account_id),
        ):
            if val is not None:
                q_ = q_.where(cond)
        if start:
            q_ = q_.where(FinTransaction.txn_date >= start)
        if end:
            q_ = q_.where(FinTransaction.txn_date <= end)
        return list(
            (
                await self._s.execute(
                    q_.order_by(FinTransaction.txn_date.desc(), FinTransaction.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars()
        )

    async def _own_txn(self, user_id: uuid.UUID, txn_id: uuid.UUID) -> FinTransaction:
        row = (
            await self._s.execute(
                select(FinTransaction).where(
                    FinTransaction.id == txn_id, FinTransaction.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise FinanceError("not_found", "transaction not found", 404)
        return row

    async def confirm(
        self,
        user_id: uuid.UUID,
        txn_id: uuid.UUID,
        category: str | None = None,
        remember: bool = False,
    ) -> FinTransaction:
        t = await self._own_txn(user_id, txn_id)
        if t.status not in ("pending_review", "duplicate_suspect"):
            raise FinanceError(
                "invalid_state",
                "only a pending or suspected-duplicate transaction can be confirmed",
                409,
            )
        if t.review_reason == "unknown_credit" and category is None:
            raise FinanceError(
                "category_required", "choose what this incoming credit is before confirming it", 422
            )
        asserts_transfer = t.review_reason in ("unlinked_transfer", "ambiguous_transfer")
        t.status = "confirmed"
        t.duplicate_of = None
        t.review_reason = None
        if category is not None:
            await self.set_category(
                user_id, txn_id, category, remember
            )  # records the user's decision (1.00)
        elif asserts_transfer:
            t.classification_confidence = (
                CONF_USER  # the user asserts this is an own-account transfer
            )
            await self._try_link(user_id, t)
        return t

    async def reject(self, user_id: uuid.UUID, txn_id: uuid.UUID) -> FinTransaction:
        t = await self._own_txn(user_id, txn_id)
        await self._unlink(user_id, t)
        t.status = "rejected"
        t.review_reason = None
        return t

    async def set_category(
        self, user_id: uuid.UUID, txn_id: uuid.UUID, category: str, remember: bool = False
    ) -> FinTransaction:
        if category not in CATEGORIES:
            raise FinanceError("invalid_category", "unknown category")
        t = await self._own_txn(user_id, txn_id)
        new_kind = categorise("", t.direction, category).kind
        if t.transfer_peer_id is not None and new_kind != "transfer":
            await self._unlink(user_id, t)  # leaving 'transfers' detaches both legs
        t.category = category
        t.kind = new_kind
        t.classification_confidence = CONF_USER  # an explicit user decision
        if new_kind != "emi":
            t.emi_principal = t.emi_interest = t.loan_id = None
        if t.status == "pending_review":
            t.review_reason = None
        if remember and t.merchant_hash:
            existing = (
                await self._s.execute(
                    select(FinMerchantRule).where(
                        FinMerchantRule.user_id == user_id,
                        FinMerchantRule.merchant_hash == t.merchant_hash,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.category = category
            else:
                self._s.add(
                    FinMerchantRule(
                        user_id=user_id, merchant_hash=t.merchant_hash, category=category
                    )
                )
        await self._s.flush()
        if new_kind == "transfer" and t.status == "confirmed" and t.transfer_peer_id is None:
            await self._try_link(user_id, t)  # an explicit assertion may now find its other leg
        return t

    async def confirm_all_pending(
        self, user_id: uuid.UUID, min_confidence: Decimal = Decimal("0.75")
    ) -> int:
        """Bulk-confirm high-confidence parsed notifications. A row waiting for a REVIEW REASON (unknown credit, unlinked
        or ambiguous transfer, unverified EMI) is never bulk-confirmed. Unknown DEBITS are not routed to review (the
        policy covers credits), so they stay bulk-confirmable exactly as before. A historical row has no reason, so it
        follows the old predicate unchanged: pending to confirmed, nothing else about it is touched."""
        res = await self._s.execute(
            update(FinTransaction)
            .where(
                FinTransaction.user_id == user_id,
                FinTransaction.status == "pending_review",
                FinTransaction.review_reason.is_(None),
                FinTransaction.confidence >= min_confidence,
            )
            .values(status="confirmed")
        )
        return int(res.rowcount or 0)  # type: ignore[attr-defined]

    async def delete_transaction(self, user_id: uuid.UUID, txn_id: uuid.UUID) -> None:
        t = await self._own_txn(user_id, txn_id)
        await self._unlink(user_id, t)  # the surviving leg is demoted in the same transaction
        await self._s.execute(
            delete(FinTransaction).where(
                FinTransaction.id == txn_id, FinTransaction.user_id == user_id
            )
        )

    # ------------------------------------------------------------------ budgets / loans / subscriptions
    async def set_budget(self, user_id: uuid.UUID, category: str, limit: Decimal) -> FinBudget:
        if category != "*" and category not in CATEGORIES:
            raise FinanceError("invalid_category", "unknown category")
        row = (
            await self._s.execute(
                select(FinBudget).where(
                    FinBudget.user_id == user_id, FinBudget.category == category
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = FinBudget(user_id=user_id, category=category, monthly_limit=q(limit))
            self._s.add(row)
        else:
            row.monthly_limit = q(limit)
        await self._s.flush()
        return row

    async def budgets(self, user_id: uuid.UUID) -> list[FinBudget]:
        return list(
            (
                await self._s.execute(
                    select(FinBudget)
                    .where(FinBudget.user_id == user_id)
                    .order_by(FinBudget.category)
                )
            ).scalars()
        )

    async def delete_budget(self, user_id: uuid.UUID, category: str) -> None:
        await self._s.execute(
            delete(FinBudget).where(FinBudget.user_id == user_id, FinBudget.category == category)
        )

    async def create_loan(
        self,
        user_id: uuid.UUID,
        label: str,
        principal: Decimal,
        annual_rate_pct: Decimal,
        tenor_months: int,
        start_date: date,
        emi_amount: Decimal | None = None,
    ) -> FinLoan:
        try:
            emi = (
                q(emi_amount)
                if emi_amount
                else an.emi_amount(principal, annual_rate_pct, tenor_months)
            )
        except ValueError:
            raise FinanceError("invalid_loan", "invalid loan terms") from None
        rid = uuid.uuid4()
        row = FinLoan(
            id=rid,
            user_id=user_id,
            label_enc=self._c.seal(user_id, rid, "fin.loan.label", label[:80]),
            principal=q(principal),
            annual_rate_pct=annual_rate_pct,
            tenor_months=tenor_months,
            start_date=start_date,
            emi_amount=emi,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def loans(self, user_id: uuid.UUID) -> list[FinLoan]:
        return list(
            (
                await self._s.execute(
                    select(FinLoan).where(FinLoan.user_id == user_id).order_by(FinLoan.created_at)
                )
            ).scalars()
        )

    def loan_schedule(self, loan: FinLoan) -> list[an.Installment]:
        return an.amortization(
            loan.principal,
            loan.annual_rate_pct,
            loan.tenor_months,
            loan.start_date,
            loan.emi_amount,
        )

    def loan_summary(self, loan: FinLoan) -> dict[str, Any]:
        pos = an.loan_position(self.loan_schedule(loan), self.today())
        return {
            "id": loan.id,
            "label": self._c.open(loan.user_id, loan.id, "fin.loan.label", loan.label_enc),
            "principal": loan.principal,
            "annual_rate_pct": loan.annual_rate_pct,
            "tenor_months": loan.tenor_months,
            "emi": loan.emi_amount,
            "status": loan.status,
            **pos,
        }

    async def add_subscription(
        self,
        user_id: uuid.UUID,
        label: str,
        amount: Decimal,
        period: str,
        next_due: date,
        merchant_hash: str | None = None,
        origin: str = "manual",
    ) -> FinSubscription:
        rid = uuid.uuid4()
        row = FinSubscription(
            id=rid,
            user_id=user_id,
            label_enc=self._c.seal(user_id, rid, "fin.sub.label", label[:80]),
            amount=q(amount),
            period=period,
            next_due=next_due,
            merchant_hash=merchant_hash,
            origin=origin,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def subscriptions(
        self, user_id: uuid.UUID, status: str | None = "active"
    ) -> list[FinSubscription]:
        q_ = (
            select(FinSubscription)
            .where(FinSubscription.user_id == user_id)
            .order_by(FinSubscription.next_due)
        )
        if status:
            q_ = q_.where(FinSubscription.status == status)
        return list((await self._s.execute(q_)).scalars())

    async def set_subscription_status(
        self, user_id: uuid.UUID, sub_id: uuid.UUID, status: str
    ) -> FinSubscription:
        row = (
            await self._s.execute(
                select(FinSubscription).where(
                    FinSubscription.id == sub_id, FinSubscription.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise FinanceError("not_found", "subscription not found", 404)
        row.status = status
        return row

    # ------------------------------------------------------------------ analytics (confirmed data only)
    async def views(
        self, user_id: uuid.UUID, start: date, end: date, account_id: uuid.UUID | None = None
    ) -> list[an.TxnView]:
        q_ = select(FinTransaction).where(
            FinTransaction.user_id == user_id,
            FinTransaction.status == "confirmed",
            FinTransaction.txn_date.between(start, end),
        )
        if account_id:
            q_ = q_.where(FinTransaction.account_id == account_id)
        return [
            an.TxnView(
                t.txn_date,
                t.amount,
                t.direction,
                t.category or "uncategorized",
                t.kind,
                t.merchant_hash,
                str(t.account_id),
                emi_interest=t.emi_interest,
                # a NEW confirmed EMI without a split is cash outflow, not recognised expense; legacy rows keep today's rule
                unsplit_emi=t.kind == "emi"
                and t.classification_confidence is not None
                and t.emi_principal is None,
            )
            for t in (await self._s.execute(q_)).scalars()
        ]

    async def pending_count(self, user_id: uuid.UUID) -> int:
        rows = (
            await self._s.execute(
                select(FinTransaction.id).where(
                    FinTransaction.user_id == user_id,
                    FinTransaction.status.in_(["pending_review", "duplicate_suspect"]),
                )
            )
        ).all()
        return len(rows)

    async def summary(self, user_id: uuid.UUID, year: int, month: int) -> dict[str, Any]:
        s, e = an.month_bounds(year, month)
        out = an.summarise(await self.views(user_id, s, e), s, e)
        out["pending_review_count"] = await self.pending_count(
            user_id
        )  # NOT included in the numbers above
        return out

    async def cashflow(self, user_id: uuid.UUID, year: int, month: int) -> dict[str, Any]:
        s, e = an.month_bounds(year, month)
        end = (
            min(e, self.today()) if (year, month) == (self.today().year, self.today().month) else e
        )
        return an.cashflow(await self.views(user_id, s, end), s, end)

    async def budget_report(
        self, user_id: uuid.UUID, year: int, month: int
    ) -> list[dict[str, Any]]:
        s, e = an.month_bounds(year, month)
        summary = an.summarise(await self.views(user_id, s, e), s, e)
        spent = dict(summary["expense_by_category"])  # type: ignore[call-overload]
        budgets = {b.category: b.monthly_limit for b in await self.budgets(user_id)}
        return an.budget_status(spent, budgets, summary["expense"])  # type: ignore[arg-type]

    async def card_report(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        out = []
        for a in await self.accounts(user_id):
            if a.kind == "credit_card":
                views = await self.views(user_id, date(1990, 1, 1), self.today(), a.id)
                out.append(
                    {
                        "account_id": a.id,
                        "label": self.account_label(a),
                        **an.card_utilisation(a.opening_balance, views, a.credit_limit),
                    }
                )
        return out

    async def upcoming(self, user_id: uuid.UUID, days: int = 14) -> list[dict[str, Any]]:
        today = self.today()
        horizon = today + timedelta(days=days)
        items: list[dict[str, Any]] = []
        for sub in await self.subscriptions(user_id):
            if today <= sub.next_due <= horizon:
                items.append(
                    {
                        "kind": "subscription",
                        "label": self._c.open(user_id, sub.id, "fin.sub.label", sub.label_enc),
                        "amount": sub.amount,
                        "due": sub.next_due,
                    }
                )
        for loan in await self.loans(user_id):
            if loan.status == "active":
                nxt = an.loan_position(self.loan_schedule(loan), today)["next_due"]
                if isinstance(nxt, date) and nxt <= horizon:
                    items.append(
                        {
                            "kind": "emi",
                            "label": self._c.open(
                                user_id, loan.id, "fin.loan.label", loan.label_enc
                            ),
                            "amount": loan.emi_amount,
                            "due": nxt,
                        }
                    )
        return sorted(items, key=lambda i: (i["due"], i["kind"]))

    async def detected_subscriptions(self, user_id: uuid.UUID) -> list[an.DetectedSubscription]:
        today = self.today()
        return an.detect_subscriptions(
            await self.views(user_id, today - timedelta(days=400), today), today
        )

    async def insights(self, user_id: uuid.UUID, year: int, month: int) -> list[an.Insight]:
        s, e = an.month_bounds(year, month)
        py, pm = (year, month - 1) if month > 1 else (year - 1, 12)
        ps, pe = an.month_bounds(py, pm)
        views = await self.views(user_id, ps, e)
        cur = an.summarise(views, s, e)
        prev = an.summarise(views, ps, pe)
        budgets = await self.budget_report(user_id, year, month)
        return an.insights(
            cur,
            prev if prev["expense"] else None,
            budgets,
            [v for v in views if s <= v.txn_date <= e],
            await self.upcoming(user_id, 7),
            self.today(),
        )

    async def erase(self, user_id: uuid.UUID) -> None:
        for model in (
            FinTransaction,
            FinImportBatch,
            FinMerchantRule,
            FinBudget,
            FinSubscription,
            FinLoan,
            FinAccount,
        ):
            await self._s.execute(delete(model).where(model.user_id == user_id))


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


__all__ = ["FinanceError", "FinanceService", "Outcome", "Sequence"]
