"""Operator CLI. Administration is deliberately NOT part of the app API: it needs shell access to
the server and database credentials, which no enrolled device ever holds.

  python -m services.admin create-user --name Abu
  python -m services.admin enroll --user <user-id> [--label "iPhone"]     # prints a one-time code
  python -m services.admin sessions list --user <user-id>
  python -m services.admin sessions revoke <session-id>
  python -m services.admin agents list
  python -m services.admin enable|disable <agent_id>
  python -m services.admin version create|clone|activate|retire|show <agent_id> [<n>]
  python -m services.admin grant model|tool|memory <agent_id> <n> <value> [--mode read|read_write]
  python -m services.admin revoke model_alias|tool|memory <agent_id> <n> <value>
"""

import argparse
import asyncio
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.agent_registry import service as agents
from core.audit.service import ADMIN
from core.model_registry.errors import RegistryError
from core.permissions.errors import PermissionDeniedError
from core.permissions.tools import DEFAULT_TOOLS
from database.schemas import Agent, MemoryMode
from services.api.auth import service as auth
from services.api.config import Settings, get_settings
from services.api.db import create_engine

Handler = Callable[[AsyncSession, Settings, argparse.Namespace], Awaitable[None]]


async def create_user(s: AsyncSession, _: Settings, a: argparse.Namespace) -> None:
    user = await auth.create_user(s, a.name, ADMIN)
    print(f"user created: {user.id}")


async def enroll(s: AsyncSession, cfg: Settings, a: argparse.Namespace) -> None:
    code, expires = await auth.create_enrollment_code(s, cfg, uuid.UUID(a.user), ADMIN, a.label)
    print(f"enrollment code (shown once, single use, expires {expires.isoformat()}):\n{code}")


async def sessions_cmd(s: AsyncSession, cfg: Settings, a: argparse.Namespace) -> None:
    if a.action == "revoke":
        await sessions_revoke(s, cfg, a)
        return
    for r in await auth.list_sessions(s, uuid.UUID(a.user)):
        state = "REVOKED" if r.revoked_at else "active"
        print(f"{r.id}  {state:8} {r.platform:8} {r.device_name}  last_used={r.last_used_at}")


async def sessions_revoke(s: AsyncSession, _: Settings, a: argparse.Namespace) -> None:
    await auth.revoke_session(s, uuid.UUID(a.session_id), ADMIN, "admin_revoked")
    print("revoked")


async def agents_list(s: AsyncSession, _: Settings, __: argparse.Namespace) -> None:
    for ag in (await s.execute(select(Agent).order_by(Agent.agent_id))).scalars():
        active = await agents.get_active_version(s, ag.agent_id)
        print(
            f"{ag.agent_id:20} enabled={ag.enabled!s:5} active_version={active.version if active else '-'}"
        )


async def agent_toggle(s: AsyncSession, _: Settings, a: argparse.Namespace) -> None:
    await agents.set_agent_enabled(s, a.agent_id, a.cmd == "enable", ADMIN)
    print(f"{a.agent_id}: {a.cmd}d")


async def version_cmd(s: AsyncSession, _: Settings, a: argparse.Namespace) -> None:
    if a.action == "create":
        v = await agents.create_version(s, a.agent_id, agents.VersionLimits(), ADMIN)
    elif a.action == "clone":
        v = await agents.clone_version(s, a.agent_id, a.number, ADMIN)
    elif a.action == "activate":
        v = await agents.activate_version(s, a.agent_id, a.number, ADMIN)
    elif a.action == "retire":
        v = await agents.retire_version(s, a.agent_id, a.number, ADMIN)
    else:
        v = await agents.get_version(s, a.agent_id, a.number)
    print(
        f"{a.agent_id}:{v.version} state={v.state.value} steps={v.max_steps} timeout={v.timeout_seconds}s "
        f"in_tokens={v.max_run_input_tokens} out_tokens={v.max_run_output_tokens} cost_inr={v.max_run_cost_inr}\n"
        f"  models={sorted(x.alias for x in v.model_aliases)}\n"
        f"  tools={ {t.tool_name: t.requires_approval for t in v.tools} }\n"
        f"  memory={ {m.namespace: m.mode.value for m in v.memory} }"
    )


async def grant(s: AsyncSession, _: Settings, a: argparse.Namespace) -> None:
    if a.kind == "model":
        await agents.grant_model_alias(s, a.agent_id, a.number, a.value, ADMIN)
    elif a.kind == "tool":
        await agents.grant_tool(s, a.agent_id, a.number, a.value, DEFAULT_TOOLS, True, ADMIN)
    else:
        await agents.grant_memory(s, a.agent_id, a.number, a.value, MemoryMode(a.mode), ADMIN)
    print("granted")


async def revoke(s: AsyncSession, _: Settings, a: argparse.Namespace) -> None:
    await agents.revoke(s, a.agent_id, a.number, a.kind, a.value, ADMIN)
    print("revoked")


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, Handler]]:
    p = argparse.ArgumentParser(
        prog="services.admin", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    h: dict[str, Handler] = {}

    def add(name: str, handler: Handler) -> argparse.ArgumentParser:
        h[name] = handler
        return sub.add_parser(name)

    add("create-user", create_user).add_argument("--name", required=True)
    e = add("enroll", enroll)
    e.add_argument("--user", required=True)
    e.add_argument("--label")
    sp = add("sessions", sessions_cmd)
    sps = sp.add_subparsers(dest="action", required=True)
    sps.add_parser("list").add_argument("--user", required=True)
    sps.add_parser("revoke").add_argument("session_id")
    add("agents", agents_list).add_argument("action", choices=["list"])
    for name in ("enable", "disable"):
        add(name, agent_toggle)
        sub.choices[name].add_argument("agent_id")
    v = add("version", version_cmd)
    v.add_argument("action", choices=["create", "clone", "activate", "retire", "show"])
    v.add_argument("agent_id")
    v.add_argument("number", type=int, nargs="?")
    g = add("grant", grant)
    g.add_argument("kind", choices=["model", "tool", "memory"])
    g.add_argument("agent_id")
    g.add_argument("number", type=int)
    g.add_argument("value")
    g.add_argument("--mode", choices=["read", "read_write"], default="read")
    r = add("revoke", revoke)
    r.add_argument("kind", choices=["model_alias", "tool", "memory"])
    r.add_argument("agent_id")
    r.add_argument("number", type=int)
    r.add_argument("value")
    return p, h


async def run(argv: list[str] | None = None) -> None:
    parser, handlers = build_parser()
    args = parser.parse_args(argv)
    if args.cmd in ("version",) and args.action != "create" and args.number is None:
        parser.error("version number required")
    settings = get_settings()
    settings.require_auth_secrets()
    engine = create_engine(settings)
    if engine is None:
        raise SystemExit("ABU_DATABASE_URL is not set")
    try:
        handler = handlers[args.cmd]
        async with async_sessionmaker(engine)() as session, session.begin():
            await handler(session, settings, args)
    except (RegistryError, PermissionDeniedError, auth.NotFoundError, ValueError) as exc:
        # Expected operator mistakes: a clean message and a non-zero exit, not a traceback.
        raise SystemExit(f"error: {exc}") from None
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
