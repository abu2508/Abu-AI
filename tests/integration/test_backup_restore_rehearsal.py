# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,union-attr,index,attr-defined,assignment,operator,misc,type-arg"
"""ISOLATED RESTORE REHEARSAL (P1.5, gate G2 evidence) on real PostgreSQL with SYNTHETIC data only.

Story: build a database with encrypted finance and memory data -> back it up (encrypted, verified) -> back the keyring
up INDEPENDENTLY -> lose the keyring -> restore into a brand-new database -> show a database backup ALONE cannot be
read -> recover the keyring from its separate backup -> show the restored application returns exactly what the original
returned. No key, password or plaintext is printed. Nothing real is touched: every database here is created and dropped
by this test.
"""

import asyncio
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.memory.crypto import KeyRing
from services.api.main import create_app
from tests.integration.conftest import ADMIN_URL, Api
from tests.integration.helpers import enroll_device, make_settings, make_user
from tests.integration.test_finance import bank, get, post, txn

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "abu_backup", ROOT / "infrastructure/scripts/abu_backup.py"
)
assert spec and spec.loader
ab = importlib.util.module_from_spec(spec)
sys.modules["abu_backup"] = ab
spec.loader.exec_module(ab)
eb = ab.eb

pytestmark = [
    pytest.mark.skipif(ADMIN_URL is None, reason="ABU_TEST_ADMIN_URL not set"),
    pytest.mark.skipif(
        shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
        reason="PostgreSQL client tools (pg_dump, pg_restore) not installed",
    ),
]


def _dsn(url) -> str:
    return (
        make_url(url)
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )


async def _serve(url, keyring_file: str):
    settings = make_settings(make_url(url), memory_kek=None, memory_keyring_file=keyring_file)
    engine = create_async_engine(make_url(url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(settings)
    return app, settings, sessions, engine


def _restore(backup: Path, key_file: str, target_url) -> None:
    """decrypt (authenticated) | pg_restore into the target: exactly the documented operator pipeline."""
    with backup.open("rb") as src:
        dec = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                str(ROOT / "infrastructure/scripts/encrypted_backup.py"),
                "decrypt",
                "--key-file",
                key_file,
            ],
            stdin=src,
            stdout=subprocess.PIPE,
        )
        restore = subprocess.run(  # noqa: S603
            ["pg_restore", "--exit-on-error", "--no-owner", "-d", _dsn(target_url)],
            stdin=dec.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        assert dec.wait() == 0 and restore.returncode == 0, restore.stderr[-500:]


async def test_backup_restore_and_keyring_recovery_rehearsal(
    migrated_url, tmp_path: Path, capsys
) -> None:
    assert ADMIN_URL is not None
    t = {}
    # ---------------------------------------------------------------- 1. a synthetic source database
    ring = tmp_path / "memory-keyring.json"
    KeyRing.generate_file(str(ring))
    secret_b64 = next(iter(json.loads(ring.read_text())["keys"].values()))
    app, settings, sessions, engine = await _serve(migrated_url, str(ring))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 4000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            api = Api(app, client, settings, sessions)
            dev = await enroll_device(
                sessions, settings, client, await make_user(sessions, "Rehearsal")
            )
            a, b = await bank(api, dev), await bank(api, dev, last4="9999")
            await post(
                api,
                dev,
                "/transactions",
                txn(a, 5, "250.00", "debit", "SYNTHETIC GROCERY MARKER"),
                201,
            )
            await post(api, dev, "/transactions", txn(a, 6, "100.00", "debit", "NEFT TO SELF"), 201)
            await post(
                api,
                dev,
                "/transactions",
                txn(b, 6, "100.00", "credit", "SELF TRANSFER FROM X"),
                201,
            )
            for text_ in ("Synthetic memory one", "Synthetic memory two"):
                r = await client.post(
                    "/v1/memory",
                    json={"namespace": "user/profile", "memory_type": "semantic", "text": text_},
                    headers=dev.headers,
                )
                assert r.status_code == 201, r.text
            before_txns = sorted(
                (x["description"], x["amount"], x["transfer_peer_id"] is not None)
                for x in await get(api, dev, "/transactions", limit=100)
            )
            before_mem = sorted(
                m["text"] for m in (await client.get("/v1/memory", headers=dev.headers)).json()
            )
    await engine.dispose()
    assert len(before_txns) == 3 and len(before_mem) == 2
    with psycopg.connect(_dsn(migrated_url)) as c:
        tables = [
            r[0]
            for r in c.execute(
                "SELECT schemaname || '.' || tablename FROM pg_tables WHERE schemaname IN ('public','lg_checkpoints') ORDER BY 1"
            )
        ]
        counts_before = {tb: c.execute(f"SELECT count(*) FROM {tb}").fetchone()[0] for tb in tables}  # noqa: S608
        head_before = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    # ---------------------------------------------------------------- 2. encrypted, verified backups with TWO keys
    k1, k2 = str(tmp_path / "k1.key"), str(tmp_path / "k2.key")
    eb.genkey(k1)
    eb.genkey(k2)
    dest = tmp_path / "backups"
    t0 = time.monotonic()
    res = ab.run_backup(["pg_dump", "-Fc", "--no-owner", "-d", _dsn(migrated_url)], k1, dest)
    t["backup_s"] = time.monotonic() - t0
    assert res.offhost_ok is None and res.toc_entries > 50
    kb, ids = ab.backup_keyring(str(ring), k2, k1, dest)
    assert ids == ["k1"]
    assert ab.check_freshness(dest, 26)[0] == 0
    assert ab.verify_backup(res.path, k1) == res.toc_entries
    raw = res.path.read_bytes()
    assert b"SYNTHETIC GROCERY MARKER" not in raw and b"Synthetic memory" not in raw
    assert secret_b64.encode() not in raw + kb.read_bytes()

    # ---------------------------------------------------------------- 3. disaster: the keyring file is lost
    ring.unlink()

    # ---------------------------------------------------------------- 4. restore into a BRAND-NEW database
    new_db = f"abu_restore_{uuid.uuid4().hex[:10]}"
    admin = make_url(ADMIN_URL)
    restored_url = admin.set(database=new_db)
    with psycopg.connect(_dsn(admin), autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{new_db}"')
    try:
        t0 = time.monotonic()
        await asyncio.to_thread(_restore, res.path, k1, restored_url)
        t["restore_s"] = time.monotonic() - t0

        with psycopg.connect(_dsn(restored_url)) as c:
            assert (
                c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                == head_before
                == "0012"
            )
            counts_after = {
                tb: c.execute(f"SELECT count(*) FROM {tb}").fetchone()[0] for tb in tables
            }  # noqa: S608
            assert counts_after == counts_before, "row counts differ after the restore"
            assert (
                c.execute(
                    "SELECT count(*) FROM pg_trigger WHERE tgname = 'fin_txn_peer_integrity'"
                ).fetchone()[0]
                == 1
            )
            assert (
                c.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'").fetchone()
                is not None
            )

        # ------------------------------------------------------------ 5. a database backup ALONE cannot be read (G2 risk)
        stranger = tmp_path / "stranger-keyring.json"
        KeyRing.generate_file(str(stranger))
        wrong = ab.recovery_check(_dsn(restored_url), str(stranger))
        assert wrong["memory_records"]["decrypted"] == 0 and wrong["memory_records"]["failed"] >= 2
        assert (
            wrong["finance_transactions"]["decrypted"] == 0
            and wrong["finance_transactions"]["failed"] >= 3
        )

        # ------------------------------------------------------------ 6. recover the keyring from its INDEPENDENT backup
        with pytest.raises(ab.BackupToolError):
            ab.restore_keyring(
                kb, k1, tmp_path / "must-not-exist.json"
            )  # the database key cannot open it
        assert not (tmp_path / "must-not-exist.json").exists()
        recovered = tmp_path / "recovered-keyring.json"
        t0 = time.monotonic()
        assert ab.restore_keyring(kb, k2, recovered) == ["k1"]
        t["keyring_s"] = time.monotonic() - t0
        ok = ab.recovery_check(_dsn(restored_url), str(recovered))
        assert ok["memory_records"] == {"decrypted": 2, "failed": 0}
        assert ok["finance_transactions"] == {"decrypted": 3, "failed": 0}
        assert ok["key_ids"] == ["k1"]
        assert secret_b64 not in json.dumps(ok)

        # ------------------------------------------------------------ 7. the restored APPLICATION returns the same data
        app2, settings2, sessions2, engine2 = await _serve(restored_url, str(recovered))
        async with app2.router.lifespan_context(app2):
            transport = httpx.ASGITransport(app=app2, client=("203.0.113.5", 4000))
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                api2 = Api(app2, client, settings2, sessions2)
                after_txns = sorted(
                    (x["description"], x["amount"], x["transfer_peer_id"] is not None)
                    for x in await get(api2, dev, "/transactions", limit=100)
                )
                after_mem = sorted(
                    m["text"] for m in (await client.get("/v1/memory", headers=dev.headers)).json()
                )
        await engine2.dispose()
        assert after_txns == before_txns and after_mem == before_mem
    finally:
        with psycopg.connect(_dsn(admin), autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{new_db}" WITH (FORCE)')

    # ---------------------------------------------------------------- 8. nothing sensitive reached any output
    seen = capsys.readouterr()
    assert secret_b64 not in seen.out + seen.err
    assert not re.search(r"SYNTHETIC GROCERY", seen.out + seen.err)
    print(  # the rehearsal timings, for the report (synthetic, tiny dataset: not a production estimate)
        "REHEARSAL-TIMINGS "
        + json.dumps(
            {k: round(v, 2) for k, v in t.items()}
            | {"tables": len(tables), "backup_bytes": res.bytes}
        )
    )
