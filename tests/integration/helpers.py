import uuid
from dataclasses import dataclass

import httpx
from pydantic import SecretStr
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.agent_registry import service as agents
from core.audit.service import ADMIN
from core.governance.service import Governance
from core.permissions.runtime import RunContext, start_run
from database.schemas import User
from database.seed.registry import MOCK_ALIAS, seed_registry
from services.api.auth import service as auth
from services.api.config import Settings

JWT = SecretStr("j" * 48)
HASH = SecretStr("h" * 48)


def make_settings(url: URL | None = None, **overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "env": "test",
        "database_url": SecretStr(url.render_as_string(hide_password=False)) if url else None,
        "jwt_secret": JWT,
        "token_hash_key": HASH,
        "litellm_base_url": "http://127.0.0.1:9",
        "rate_limit_auth_per_minute": 1000,
        "rate_limit_ip_per_minute": 100000,
        "rate_limit_user_per_minute": 100000,
    }
    return Settings(**{**defaults, **overrides})  # type: ignore[arg-type]


@dataclass
class Device:
    user_id: uuid.UUID
    session_id: uuid.UUID
    access: str
    refresh: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


async def make_user(sessions: async_sessionmaker[AsyncSession], name: str = "Abu") -> uuid.UUID:
    async with sessions() as s, s.begin():
        return (await auth.create_user(s, name, ADMIN)).id


async def enroll_device(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
    user_id: uuid.UUID,
    name: str = "iPhone",
) -> Device:
    async with sessions() as s, s.begin():
        code, _ = await auth.create_enrollment_code(s, settings, user_id, ADMIN, name)
    resp = await client.post(
        "/v1/auth/enroll", json={"enrollment_code": code, "device_name": name, "platform": "ios"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return Device(
        user_id, uuid.UUID(body["session_id"]), body["access_token"], body["refresh_token"]
    )


async def runnable_agent(
    session: AsyncSession,
    gov: Governance,
    agent_id: str = "finance_agent",
    aliases: tuple[str, ...] = (MOCK_ALIAS,),
    user_id: uuid.UUID | None = None,
    task_type: str = "test",
    limits: agents.VersionLimits | None = None,
) -> RunContext:
    """Seeds, grants the aliases on the draft, activates, enables, and returns a RunContext.
    Commits first: execution records are written in their own transactions and must see the rows."""
    await seed_registry(session)
    for alias in aliases:
        await agents.grant_model_alias(session, agent_id, 1, alias)
    if limits is not None:
        await agents.update_limits(session, agent_id, 1, limits)
    await agents.activate_version(session, agent_id, 1)
    await agents.set_agent_enabled(session, agent_id, True)
    if user_id is None:
        user = User(display_name="t")
        session.add(user)
        await session.flush()
        user_id = user.id
    await session.commit()
    return await start_run(
        session, user_id=user_id, agent_id=agent_id, governance=gov, task_type=task_type
    )


async def enable_paid(
    gov: Governance,
    monthly: str = "300",
    daily: str = "300",
    rate: str = "100",
    source: str = "test fixture",
) -> None:
    """Operator opt-in for paid spending (what `admin spend ...` does)."""
    from decimal import Decimal

    from core.audit.service import ADMIN
    from core.governance import spending

    await spending.set_budget(gov.factory, ADMIN, Decimal(monthly), Decimal(daily))
    await spending.set_rate(gov.factory, ADMIN, Decimal(rate), source)
    await spending.set_paid_models_enabled(gov.factory, ADMIN, True)
