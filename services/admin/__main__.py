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
  python -m services.admin kill-switch activate|deactivate --reason "..."   |   kill-switch status
  python -m services.admin spend status
  python -m services.admin spend set-budget --monthly 300 --daily 20   (hard ceiling: 300 INR/month)
  python -m services.admin spend set-rate --rate 88.5 --source "RBI reference rate, 2026-11-23"
  python -m services.admin spend paid on|off      (default off; needs a budget and a rate)
  python -m services.admin spend unfreeze --reason "..."
  python -m services.admin spend resolve <ledger-id> (--confirmed-inr 1.25 | --not-charged)
  python -m services.admin executions list [--limit 20]
  python -m services.admin orchestrator bootstrap-mock [--user-memory finance_agent,...]   (grant mock alias + own memory, activate, enable)
  python -m services.admin orchestrator purge                (scrub expired idempotent replies; delete old run history)
  python -m services.admin memory stats
  python -m services.admin memory purge-expired            (retention: proposals, third-party summaries, threads...)
  python -m services.admin memory add-key --file KEYRING --id k2   (HOST command: adds+activates a new master key)
  python -m services.admin memory rotate-keys              (re-wraps every data key under the active master key)
  python -m services.admin memory reembed                  (explicit re-embed with the configured embedder)
  python -m services.admin memory erase-user --user ID --confirm ID    (right to erasure; irreversible)
"""

import argparse
import asyncio
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.agent_registry import service as agents
from core.audit.service import ADMIN
from core.governance import killswitch, spending
from core.governance.errors import GovernanceError
from core.governance.service import Governance
from core.memory.crypto import KeyRing
from core.memory.runtime import open_runtime
from core.memory.types import Caller
from core.model_registry.errors import RegistryError
from core.orchestrator.bootstrap import bootstrap_mock_agents
from core.orchestrator.idempotency import RunStore
from core.permissions.errors import PermissionDeniedError
from core.permissions.tools import DEFAULT_TOOLS
from database.schemas import Agent, Execution, MemoryMode
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


async def kill_switch_cmd(_: AsyncSession, __: Settings, a: argparse.Namespace) -> None:
    if a.action == "activate":
        snap = await killswitch.activate(a.factory, ADMIN, a.reason)
    elif a.action == "deactivate":
        snap = await killswitch.deactivate(a.factory, ADMIN, a.reason)
    else:
        snap = await killswitch.read_state(a.factory)
    who = (
        f"{snap.activated_by_type}/{snap.activated_via} at {snap.activated_at}"
        if snap.active
        else "-"
    )
    print(
        f"kill switch: {'ACTIVE' if snap.active else 'inactive'}  by={who}  reason={snap.reason or '-'}"
    )


async def spend_cmd(_: AsyncSession, __: Settings, a: argparse.Namespace) -> None:
    gov = Governance(a.factory)
    if a.action == "set-budget":
        await spending.set_budget(a.factory, ADMIN, Decimal(a.monthly), Decimal(a.daily))
    elif a.action == "set-rate":
        await spending.set_rate(a.factory, ADMIN, Decimal(a.rate), a.source)
    elif a.action == "paid":
        await spending.set_paid_models_enabled(a.factory, ADMIN, a.state == "on")
    elif a.action == "unfreeze":
        await spending.unfreeze(a.factory, ADMIN, a.reason)
    elif a.action == "resolve":
        amount = None if a.not_charged else Decimal(a.confirmed_inr)
        await spending.resolve_held(a.factory, ADMIN, uuid.UUID(a.ledger_id), amount)
    cfg, usage = await gov.spend.snapshot(), await gov.spend.usage()
    print(
        f"monthly={cfg.monthly_budget_inr} INR (ceiling 300)  daily={cfg.daily_budget_inr}  "
        f"paid_models={'ON' if cfg.paid_models_enabled else 'off'}  frozen={cfg.paid_frozen}"
        f"{' (' + (cfg.paid_frozen_reason or '') + ')' if cfg.paid_frozen else ''}\n"
        f"rate={cfg.usd_inr_rate} INR/USD (source: {cfg.rate_source or '-'}, set {cfg.rate_set_at})\n"
        f"used today={usage.day_used_inr}  this month={usage.month_used_inr}  of which held/unresolved={usage.held_inr}"
    )


async def executions_cmd(s: AsyncSession, _: Settings, a: argparse.Namespace) -> None:
    rows = (
        await s.execute(select(Execution).order_by(Execution.started_at.desc()).limit(a.limit))
    ).scalars()
    for r in rows:
        print(
            f"{r.id}  {r.status:14} {r.agent_id}:{r.agent_version}  {r.task_type}  calls={r.call_count}  "
            f"confirmed={r.total_confirmed_cost_inr} INR  trace={r.trace_id}  reason={r.failure_reason or '-'}"
        )


async def orchestrator_cmd(s: AsyncSession, cfg: Settings, a: argparse.Namespace) -> None:
    if a.action == "bootstrap-mock":
        grants = [x for x in (a.user_memory or "").split(",") if x]
        result = await bootstrap_mock_agents(s, ADMIN, user_memory_for=grants)
        for agent_id, outcome in result.items():
            print(f"{agent_id}: {outcome} (mock model only; no external provider)")
    elif a.action == "purge":
        runtime = await open_runtime(cfg, with_checkpointer=False)
        if cfg.token_hash_key is None:  # unreachable: require_auth_secrets() ran
            raise SystemExit("token hash key is not configured")
        store = RunStore(
            a.factory, runtime.envelope, cfg.token_hash_key, cfg.orchestrator_response_ttl_hours
        )
        print("scrubbed replies:", await store.scrub_expired())
        print("deleted runs:", await store.purge(cfg.orchestrator_run_retention_days))


async def memory_cmd(_: AsyncSession, cfg: Settings, a: argparse.Namespace) -> None:
    runtime = await open_runtime(cfg)
    try:
        async with a.factory() as s, s.begin():
            svc = runtime.service(s, Governance(a.factory))
            if a.action == "stats":
                print(await svc.stats())
            elif a.action == "purge-expired":
                print("purged:", await svc.purge_expired())
            elif a.action == "rotate-keys":
                total = 0
                while (n := await svc.rewrap_batch()) > 0:
                    total += n
                print(
                    f"re-wrapped {total} record(s) under key {runtime.envelope._p.active_key_id!r}"
                )  # noqa: SLF001
            elif a.action == "reembed":
                print("re-embedded:", await svc.reembed_all(), "with", runtime.embedder.id)
            elif a.action == "erase-user":
                if a.confirm != a.user:
                    raise ValueError("--confirm must repeat the user id exactly")
                r = await svc.erase_user(Caller.admin(uuid.UUID(a.user)))
                print(
                    f"erased: records={r.records} embeddings={r.embeddings} threads={r.threads} access_logs={r.access_log_rows_scrubbed}"
                )
    finally:
        await runtime.close()


def memory_add_key(a: argparse.Namespace) -> None:
    """Host-side: edit the keyring FILE (never touches the database)."""
    import json
    import os

    ring = KeyRing.from_file(a.file).rotated(a.id)
    import base64

    data = {
        "active": ring.active_id,
        "keys": {k: base64.b64encode(v).decode() for k, v in ring.keys.items()},
    }
    tmp = f"{a.file}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, a.file)
    print(
        f"added and activated key {a.id!r}; old keys are kept so existing data stays readable. Now run: memory rotate-keys"
    )


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
    ks = add("kill-switch", kill_switch_cmd)
    ks.add_argument("action", choices=["activate", "deactivate", "status"])
    ks.add_argument("--reason", default="")
    sp = add("spend", spend_cmd)
    sps = sp.add_subparsers(dest="action", required=True)
    sps.add_parser("status")
    b = sps.add_parser("set-budget")
    b.add_argument("--monthly", required=True)
    b.add_argument("--daily", required=True)
    rt = sps.add_parser("set-rate")
    rt.add_argument("--rate", required=True)
    rt.add_argument("--source", required=True)
    sps.add_parser("paid").add_argument("state", choices=["on", "off"])
    sps.add_parser("unfreeze").add_argument("--reason", required=True)
    rv = sps.add_parser("resolve")
    rv.add_argument("ledger_id")
    grp = rv.add_mutually_exclusive_group(required=True)
    grp.add_argument("--confirmed-inr")
    grp.add_argument("--not-charged", action="store_true")
    mem = add("memory", memory_cmd)
    mem.add_argument(
        "action",
        choices=["stats", "purge-expired", "rotate-keys", "reembed", "erase-user", "add-key"],
    )
    mem.add_argument("--user")
    mem.add_argument("--confirm")
    mem.add_argument("--file")
    mem.add_argument("--id")
    orc = add("orchestrator", orchestrator_cmd)
    orc.add_argument("action", choices=["bootstrap-mock", "purge"])
    orc.add_argument(
        "--user-memory", help="comma-separated agent ids to grant READ on the user/ namespace"
    )
    ex = add("executions", executions_cmd)
    ex.add_argument("action", choices=["list"])
    ex.add_argument("--limit", type=int, default=20)
    return p, h


async def run(argv: list[str] | None = None) -> None:
    parser, handlers = build_parser()
    args = parser.parse_args(argv)
    if args.cmd in ("version",) and args.action != "create" and args.number is None:
        parser.error("version number required")
    if args.cmd == "memory" and args.action == "add-key":
        if not (args.file and args.id):
            parser.error("--file and --id are required")
        memory_add_key(args)  # host-side: needs no database or app secrets
        return
    settings = get_settings()
    settings.require_auth_secrets()
    engine = create_engine(settings)
    if engine is None:
        raise SystemExit("ABU_DATABASE_URL is not set")
    try:
        handler = handlers[args.cmd]
        factory = async_sessionmaker(engine, expire_on_commit=False)
        args.factory = factory  # governance commands manage their own transactions
        async with factory() as session, session.begin():
            await handler(session, settings, args)
    except (
        RegistryError,
        PermissionDeniedError,
        GovernanceError,
        auth.NotFoundError,
        ValueError,
        ArithmeticError,
    ) as exc:
        # Expected operator mistakes: a clean message and a non-zero exit, not a traceback.
        raise SystemExit(f"error: {exc}") from None
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
