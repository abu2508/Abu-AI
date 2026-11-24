"""Contract-test API launcher: the REAL, unchanged backend app, with the Coach service clock frozen in THIS test process only.

Why: the planner starts today's free window at max(work_start, now) (domain/coach/service.py), so a live briefing depends on
the time of day (later in the day, tasks stop fitting and `plan.unscheduled` fills up). The B3 golden responses were recorded
under a frozen clock by tests/contracts/scenario.py::freeze_clock (CoachService.now patched, test process only). This
launcher applies the same technique to the live process the mobile contract tests talk to. No backend source is touched
and production behaviour is unchanged: nothing outside this process is affected, and it refuses to run outside ABU_ENV=test.

Usage (done by mobile/scripts/contract-backend.sh, never by hand against a real database):
    ABU_ENV=test ABU_CONTRACT_NOW=2026-11-24T06:30:00+00:00 python mobile/scripts/contract-api.py --host 127.0.0.1 --port 8765
ABU_CONTRACT_NOW is today's UTC date at 06:30 UTC = 12:00 in the default Asia/Kolkata zone, the same wall-clock time as the golden.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from domain.coach.service import CoachService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if os.environ.get("ABU_ENV") != "test":
        sys.exit("contract-api: refusing to run unless ABU_ENV=test")
    if args.host not in {"127.0.0.1", "localhost"}:
        sys.exit("contract-api: loopback only")
    raw = os.environ.get("ABU_CONTRACT_NOW", "")
    try:
        frozen = datetime.fromisoformat(raw)
    except ValueError:
        sys.exit("contract-api: ABU_CONTRACT_NOW must be an ISO timestamp with an offset, e.g. 2026-11-24T06:30:00+00:00")
    if frozen.tzinfo is None:
        sys.exit("contract-api: ABU_CONTRACT_NOW needs a UTC offset")

    # Same technique as tests/contracts/scenario.py::freeze_clock, applied to the process the contract tests call.
    CoachService.now = lambda self: frozen  # type: ignore[method-assign]
    print(f"contract-api: CoachService clock frozen at {frozen.isoformat()} (test process only)", flush=True)

    uvicorn.run("services.api.main:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
