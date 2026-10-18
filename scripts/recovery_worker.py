"""Run the recovery trigger: withdrawals -> offers -> proposal -> holds -> approval -> confirmed replacements.

Usage: uv run python scripts/recovery_worker.py [--once] [--interval 10] [--no-notify]
Requires: carriers running, migrations 006-007 applied, .env with AGENT_URI and TIGER_DATABASE_URL.
Sends buyer notifications as the Exchange (never reads its mailbox), so run it on the same machine
as the Exchange. --no-notify skips buyer messages (e.g. on a test database).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from freight_exchange.quotes import exchange_identity  # noqa: E402
from freight_exchange.recovery import build_recovery_seam  # noqa: E402
from freight_exchange.recovery_worker import run_forever, tick  # noqa: E402
from freight_exchange.repository import get_repository  # noqa: E402


async def main(once: bool, interval: float, notify: bool) -> None:
    agent_uri = os.environ["AGENT_URI"]
    repo = await get_repository()
    seam = await build_recovery_seam(exchange_identity(agent_uri), repo.pool,
                                     agent_uri=agent_uri if notify else None)
    print(f"approval seam: {'Tiger (PR #15)' if seam.approval else 'none yet: over-allowance bookings wait'}; "
          f"notifications: {'on' if notify else 'off'}")
    if once:
        for out in await tick(seam):
            print(out.recovery_run_id, out.status, [(a.booking_id, a.sailing_id) for a in out.assignments],
                  "unrecovered:", list(out.unrecovered_booking_ids))
    else:
        await run_forever(seam, interval)
    await repo.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.once, args.interval, not args.no_notify))
