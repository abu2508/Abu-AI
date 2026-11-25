# Gate G3: LangGraph checkpoint metadata is plaintext

Status: **OPEN. Reviewed, not decided.** This document gives the owner what is needed to decide. It does not accept the
risk, and it does not change the checkpoint design.

## 1. What is stored in plaintext, measured

Working memory (LangGraph checkpoints) lives in the separate schema `lg_checkpoints`. The state values are encrypted
(AES-256-GCM envelope, strict loader that refuses unencrypted data). One column is **not**: `checkpoints.metadata` (JSONB).

Measured on 2026-11-25 by running the application's own graph path against real PostgreSQL
(`tests/integration/test_checkpoint_metadata_tripwire.py`):

| Plaintext column | Contents observed |
|---|---|
| `checkpoints.metadata` | exactly three keys: `step` (integer), `source` (`input`, `loop`, `update` or `fork`), `parents` (`{}`) |
| `thread_id` | opaque 32-character hex id (the owner mapping is in `agent_threads`) |
| `checkpoint_ns`, `checkpoint_id` | LangGraph bookkeeping |

No message text, memory content, name, amount or identifier from the user is in these columns: the marker string the test
writes into every state value never appears in them. The application passes only `{"configurable": {"thread_id": ...}}` to
LangGraph; anything placed in a run's `metadata=` would be copied into the plaintext column, which is why it is forbidden.

## 2. What an attacker with database read access (but not the keyring) learns

- that a thread exists, roughly when it ran and how many steps it took (activity and volume, "traffic analysis");
- nothing about what was said or decided.

That is the same class of information already visible in `agent_threads` timestamps and the `executions` table
(metadata only, by design). It is **not** content.

## 3. Options

| Option | What it means | Cost and risk |
|---|---|---|
| **A. Accept, with a tripwire** (recommended by this review) | Record the residual risk in the production-readiness checklist and keep the existing tripwire test: the build fails if any key other than `step`/`source`/`parents` appears, or if code sets config metadata. | None to build. The exposure stays what section 1 measured. Depends on the tripwire staying in CI. |
| B. Remove the metadata | Override the saver to store an empty object. | LangGraph reads `source` and `step` for listing, filtering and ordering; removing them risks changing checkpoint behaviour. Needs a library-version-pinned saver change and a full regression run. |
| C. Encrypt the metadata | Store it in an encrypted column and decrypt on read. | A deeper change to a library-internal query path (the saver issues the SQL), pinned and re-verified at every LangGraph upgrade. The most work for the least information protected. |

## 4. Interaction with other gates

Disk and volume encryption (G1) protects all of this at rest regardless of option. The keyring (G2) protects the encrypted
state values. G3 only concerns what remains readable without the keyring.

## 5. Decision needed from the owner

> G3: accept A (with the tripwire), or request B or C as a separate, approved piece of work.

Until a decision is recorded here and in `docs/operations/PRODUCTION_READINESS.md`, `security-gates.sh` keeps printing
`BLOCKED checkpoint-metadata` (`ABU_GATE_CHECKPOINT_METADATA=accepted` is not set by anyone but the owner).
