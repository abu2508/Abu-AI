"""/v1/finance - the user's own finance data. Nothing here moves money or talks to a bank."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from domain.finance import analytics as an
from domain.finance.money import ParseError
from domain.finance.normalize import CATEGORIES
from domain.finance.service import FinanceError, FinanceService, Outcome
from domain.finance.types import RawTxn
from services.api.auth.deps import PrincipalDep, SessionDep
from services.api.contracts.finance import FinanceSummary, InsightOut, UpcomingItem
from services.api.domain_deps import CipherDep, decode_b64, jsonable

router = APIRouter(prefix="/v1/finance", tags=["finance"])
Amount = Annotated[
    Decimal, Field(gt=0, le=Decimal("999999999.99"), max_digits=12, decimal_places=2)
]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountIn(Body):
    kind: Literal["bank", "credit_card", "cash", "wallet"]
    label: str = Field(min_length=1, max_length=80)
    institution: str | None = Field(default=None, pattern=r"^[a-z0-9_]{2,32}$")
    last4: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    opening_balance: Decimal = Field(default=Decimal(0), max_digits=14, decimal_places=2)
    credit_limit: Amount | None = None
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)
    auto_confirm: bool = False


class TxnIn(Body):
    account_id: uuid.UUID
    date: date
    amount: Amount
    direction: Literal["debit", "credit"]
    description: str = Field(min_length=1, max_length=300)
    reference: str | None = Field(default=None, max_length=40)
    category: str | None = None


class VoiceIn(Body):
    account_id: uuid.UUID
    transcript: str = Field(min_length=3, max_length=300)


class IngestIn(Body):
    channel: Literal["sms", "email", "shortcut", "share"]
    text: str = Field(min_length=1, max_length=2000)
    received_on: date | None = None
    account_id: uuid.UUID | None = None


class ImportIn(Body):
    account_id: uuid.UUID
    format: Literal["csv", "xlsx", "pdf"]
    content_base64: str = Field(min_length=4)


class ConfirmIn(Body):
    category: str | None = None
    remember: bool = False


class CategoryIn(Body):
    category: str
    remember: bool = False


class BudgetIn(Body):
    category: str
    monthly_limit: Amount


class LoanIn(Body):
    label: str = Field(min_length=1, max_length=80)
    principal: Amount
    annual_rate_pct: Decimal = Field(ge=0, le=100, max_digits=6, decimal_places=3)
    tenor_months: int = Field(ge=1, le=600)
    start_date: date
    emi_amount: Amount | None = None


class SubscriptionIn(Body):
    label: str = Field(min_length=1, max_length=80)
    amount: Amount
    period: Literal["weekly", "monthly", "yearly"]
    next_due: date


def _fail(exc: FinanceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
    )


def _out(o: Outcome) -> dict[str, Any]:
    return {
        "status": o.status,
        "transaction_id": o.txn_id,
        "transaction_status": o.txn_status,
        "confidence": o.confidence,
        "reason": o.reason,
    }


_json = jsonable


async def _run(session: SessionDep, fn: Any) -> Any:
    try:
        res = await fn()
        await session.commit()
        return res
    except FinanceError as exc:
        await session.rollback()
        return _fail(exc)
    except ParseError as exc:
        await session.rollback()
        return JSONResponse(
            status_code=422, content={"error": {"code": "invalid_value", "message": str(exc)}}
        )


def _acct(s: FinanceService, a: Any) -> dict[str, Any]:
    return {
        "id": a.id,
        "kind": a.kind,
        "label": s.account_label(a),
        "institution": a.institution,
        "last4": a.last4,
        "opening_balance": a.opening_balance,
        "credit_limit": a.credit_limit,
        "auto_confirm": a.auto_confirm,
        "statement_day": a.statement_day,
        "due_day": a.due_day,
        "archived": a.archived_at is not None,
    }


@router.get("/categories")
async def categories(principal: PrincipalDep) -> list[str]:
    del principal
    return list(CATEGORIES)


@router.post("/accounts", status_code=201)
async def create_account(
    body: AccountIn, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)
    return await _run(session, lambda: _create_account(s, principal.user_id, body))


async def _create_account(s: FinanceService, uid: uuid.UUID, b: AccountIn) -> Any:
    a = await s.create_account(uid, **b.model_dump())
    return _json(_acct(s, a))


@router.get("/accounts")
async def list_accounts(principal: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = FinanceService(session, cipher)
    return _json([_acct(s, a) for a in await s.accounts(principal.user_id)])


class AccountPatch(Body):
    auto_confirm: bool | None = None
    archived: bool | None = None


@router.patch("/accounts/{account_id}")
async def patch_account(
    account_id: uuid.UUID,
    body: AccountPatch,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> Any:
        from datetime import UTC, datetime

        a = await s.account(principal.user_id, account_id)
        if body.auto_confirm is not None:
            a.auto_confirm = body.auto_confirm
        if body.archived is not None:
            a.archived_at = datetime.now(UTC) if body.archived else None
        return _json(_acct(s, a))

    return await _run(session, go)


@router.post("/transactions", status_code=201)
async def add_transaction(
    body: TxnIn, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)
    raw = RawTxn(body.date, body.amount, body.direction, body.description, body.reference)
    return await _run(session, lambda: _manual(s, principal.user_id, body, raw))


async def _manual(s: FinanceService, uid: uuid.UUID, body: TxnIn, raw: RawTxn) -> Any:
    return _json(_out(await s.add_manual(uid, body.account_id, raw, body.category)))


@router.post("/transactions/voice", status_code=201)
async def add_voice(
    body: VoiceIn, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)
    return await _run(session, lambda: _voice(s, principal.user_id, body))


async def _voice(s: FinanceService, uid: uuid.UUID, body: VoiceIn) -> Any:
    return _json(_out(await s.add_voice(uid, body.account_id, body.transcript)))


@router.post("/ingest")
async def ingest(
    body: IngestIn, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    """Provider-independent entry for bank SMS / email alerts handed over by the user (an iOS Shortcut, the
    Share Extension, a paste). The raw text is parsed and discarded."""
    s = FinanceService(session, cipher)
    return await _run(session, lambda: _ingest(s, principal.user_id, body))


async def _ingest(s: FinanceService, uid: uuid.UUID, b: IngestIn) -> Any:
    return _json(
        _out(
            await s.ingest_notification(
                uid, b.channel, b.text, received_on=b.received_on, account_id=b.account_id
            )
        )
    )


@router.post("/import")
async def import_statement(
    body: ImportIn,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    request: Request,
) -> Any:
    limit = request.app.state.settings.max_import_bytes
    data = decode_b64(body.content_base64, limit)
    s = FinanceService(session, cipher)
    return await _run(session, lambda: _import(s, principal.user_id, body, data))


async def _import(s: FinanceService, uid: uuid.UUID, b: ImportIn, data: bytes) -> Any:
    return _json(await s.import_statement(uid, b.account_id, b.format, data))


@router.get("/transactions")
async def list_transactions(
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    start: date | None = None,
    end: date | None = None,
    status: Literal["confirmed", "pending_review", "duplicate_suspect", "rejected"] | None = None,
    category: str | None = None,
    account_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    s = FinanceService(session, cipher)
    rows = await s.transactions(
        principal.user_id,
        start=start,
        end=end,
        status=status,
        category=category,
        account_id=account_id,
        limit=limit,
        offset=offset,
    )
    return _json([s.view(t) for t in rows])


@router.get("/review")
async def review_queue(principal: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = FinanceService(session, cipher)
    pend = await s.transactions(principal.user_id, status="pending_review", limit=200)
    dups = await s.transactions(principal.user_id, status="duplicate_suspect", limit=200)
    return _json(
        {
            "pending_review": [s.view(t) for t in pend],
            "duplicate_suspects": [s.view(t) for t in dups],
        }
    )


@router.post("/transactions/{txn_id}/confirm")
async def confirm(
    txn_id: uuid.UUID,
    body: ConfirmIn,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> Any:
        return _json(
            s.view(await s.confirm(principal.user_id, txn_id, body.category, body.remember))
        )

    return await _run(session, go)


@router.post("/transactions/confirm-pending")
async def confirm_pending(principal: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    """Bulk-confirm only HIGH-confidence parsed notifications (>= 0.75)."""
    s = FinanceService(session, cipher)

    async def go() -> Any:
        return {"confirmed": await s.confirm_all_pending(principal.user_id)}

    return await _run(session, go)


@router.post("/transactions/{txn_id}/reject")
async def reject(
    txn_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> Any:
        return _json(s.view(await s.reject(principal.user_id, txn_id)))

    return await _run(session, go)


@router.post("/transactions/{txn_id}/category")
async def set_category(
    txn_id: uuid.UUID,
    body: CategoryIn,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> Any:
        return _json(
            s.view(await s.set_category(principal.user_id, txn_id, body.category, body.remember))
        )

    return await _run(session, go)


@router.delete(
    "/transactions/{txn_id}", status_code=204, response_class=Response, response_model=None
)
async def delete_transaction(
    txn_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> None:
        await s.delete_transaction(principal.user_id, txn_id)

    res = await _run(session, go)
    return res if isinstance(res, JSONResponse) else Response(status_code=204)


@router.put("/budgets")
async def set_budget(
    body: BudgetIn, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> Any:
        b = await s.set_budget(principal.user_id, body.category, body.monthly_limit)
        return _json({"category": b.category, "monthly_limit": b.monthly_limit})

    return await _run(session, go)


@router.get("/budgets")
async def budgets(
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    year: int | None = None,
    month: int | None = Query(default=None, ge=1, le=12),
) -> Any:
    s = FinanceService(session, cipher)
    t = s.today()
    return _json(await s.budget_report(principal.user_id, year or t.year, month or t.month))


@router.delete("/budgets/{category}", status_code=204, response_class=Response, response_model=None)
async def delete_budget(
    category: str, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> None:
        await s.delete_budget(principal.user_id, category)

    await _run(session, go)
    return Response(status_code=204)


@router.post("/loans", status_code=201)
async def create_loan(
    body: LoanIn, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> Any:
        return _json(s.loan_summary(await s.create_loan(principal.user_id, **body.model_dump())))

    return await _run(session, go)


@router.get("/loans")
async def loans(principal: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = FinanceService(session, cipher)
    return _json([s.loan_summary(x) for x in await s.loans(principal.user_id)])


@router.get("/loans/{loan_id}/schedule")
async def loan_schedule(
    loan_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)
    for loan in await s.loans(principal.user_id):
        if loan.id == loan_id:
            return _json([i.__dict__ for i in s.loan_schedule(loan)])
    return _fail(FinanceError("not_found", "loan not found", 404))


@router.post("/subscriptions", status_code=201)
async def add_subscription(
    body: SubscriptionIn, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> Any:
        sub = await s.add_subscription(principal.user_id, **body.model_dump())
        return _json(
            {"id": sub.id, "amount": sub.amount, "period": sub.period, "next_due": sub.next_due}
        )

    return await _run(session, go)


@router.get("/subscriptions")
async def subscriptions(principal: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = FinanceService(session, cipher)
    return _json(
        {
            "active": [
                {
                    "id": x.id,
                    "label": cipher.open(x.user_id, x.id, "fin.sub.label", x.label_enc),
                    "amount": x.amount,
                    "period": x.period,
                    "next_due": x.next_due,
                }
                for x in await s.subscriptions(principal.user_id)
            ],
            "suggested": [x.__dict__ for x in await s.detected_subscriptions(principal.user_id)],
        }
    )


@router.post("/subscriptions/{sub_id}/{action}")
async def subscription_action(
    sub_id: uuid.UUID,
    action: Literal["pause", "cancel", "resume"],
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
) -> Any:
    s = FinanceService(session, cipher)

    async def go() -> Any:
        sub = await s.set_subscription_status(
            principal.user_id,
            sub_id,
            {"pause": "paused", "cancel": "cancelled", "resume": "active"}[action],
        )
        return {"id": sub.id, "status": sub.status}

    return await _run(session, go)


def _ym(s: FinanceService, year: int | None, month: int | None) -> tuple[int, int]:
    t = s.today()
    return year or t.year, month or t.month


@router.get("/summary", responses={200: {"model": FinanceSummary}})
async def summary(
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    year: int | None = None,
    month: int | None = Query(default=None, ge=1, le=12),
) -> Any:
    s = FinanceService(session, cipher)
    return _json(await s.summary(principal.user_id, *_ym(s, year, month)))


@router.get("/cashflow")
async def cashflow(
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    year: int | None = None,
    month: int | None = Query(default=None, ge=1, le=12),
) -> Any:
    s = FinanceService(session, cipher)
    return _json(await s.cashflow(principal.user_id, *_ym(s, year, month)))


@router.get("/cards")
async def cards(principal: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    return _json(await FinanceService(session, cipher).card_report(principal.user_id))


@router.get("/insights", responses={200: {"model": list[InsightOut]}})
async def insights(
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    year: int | None = None,
    month: int | None = Query(default=None, ge=1, le=12),
) -> Any:
    s = FinanceService(session, cipher)
    items = await s.insights(principal.user_id, *_ym(s, year, month))
    return _json(
        [
            {
                "code": i.code,
                "severity": i.severity,
                "params": i.params,
                "text": an.render_insight(i),
            }
            for i in items
        ]
    )


@router.get("/upcoming", responses={200: {"model": list[UpcomingItem]}})
async def upcoming(
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> Any:
    return _json(await FinanceService(session, cipher).upcoming(principal.user_id, days))
