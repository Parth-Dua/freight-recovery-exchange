"""Phase 6 live run: withdrawn sailing -> one-shot ACP recovery round -> OR-Tools proposal in Tiger.

Usage: uv run python scripts/recovery_smoke.py ATL-101 [--withdraw]
  --withdraw  first withdraw the sailing through Tisya's DisruptionSeam (marks its bookings DISRUPTED).
Requires: carriers running on this branch, migration 006 applied, .env with AGENT_URI and TIGER_DATABASE_URL.
A second run for the same withdrawal returns the existing run (idempotent); reset the demo to start over.
Nothing is held or booked: the run ends at PROPOSED (phase 7 makes it real).
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

from freight_exchange.quotes import exchange_identity, utcnow  # noqa: E402
from freight_exchange.recovery import build_recovery_seam  # noqa: E402
from freight_exchange.repository import get_repository  # noqa: E402


async def main(sailing_id: str, withdraw: bool) -> None:
    repo = await get_repository()
    seam = await build_recovery_seam(exchange_identity(os.environ["AGENT_URI"]), repo.pool)
    if withdraw:
        owner = await repo.sailing_owner(sailing_id)
        summary = await seam.disruption.withdraw(sailing_id=sailing_id, carrier_id=owner,
                                                 risk_event_id="risk_smoke_phase6", actor_id="recovery_smoke")
        print(f"withdrawn {sailing_id} by {owner}: disrupted {summary.disrupted_booking_ids}, "
              f"already_withdrawn={summary.already_withdrawn}")

    out = await seam.recover(sailing_id=sailing_id, triggered_at=utcnow())
    print(f"\n{out.recovery_run_id}: {out.status}")
    for a in out.assignments:
        print(f"  {a.booking_id} -> {a.sailing_id} ({a.carrier_id}) x{a.quantity} ${a.quoted_price_minor / 100:,.2f}")
    print(f"  unrecovered: {list(out.unrecovered_booking_ids)}")
    for key, value in sorted(out.diagnostics.items()):
        print(f"  {key}: {value}")

    print("\n--- Tiger evidence for", out.recovery_run_id)
    async with repo.pool.connection() as conn:
        for title, sql in [
            ("recovery_runs", "SELECT recovery_id, status, sailing_id, withdrawal_event_id, input_version, detail FROM recovery_runs WHERE recovery_id = %s"),
            ("recovery_offers", "SELECT carrier_id, sailing_id, capacity_units, total_price_minor, eligible_booking_ids, status, rejection_code, left(carrier_signature, 12) FROM recovery_offers WHERE recovery_id = %s ORDER BY carrier_id, sailing_id"),
            ("agent_messages", "SELECT direction, message_type, carrier_id, status, latency_ms FROM agent_messages WHERE correlation_id = %s ORDER BY ts, direction DESC"),
            ("counter messages in this run (must be 0)", "SELECT count(*) FROM agent_messages WHERE correlation_id = %s AND message_type = 'COUNTER_OFFER'"),
            ("market_events", "SELECT to_char(ts, 'HH24:MI:SS.MS'), event_type, coalesce(carrier_id, '-'), entity_id FROM market_events WHERE correlation_id = %s ORDER BY ts"),
        ]:
            rows = await (await conn.execute(sql, (out.recovery_run_id,))).fetchall()
            print(f"{title}: {len(rows)} rows")
            for row in rows:
                print("  ", row)
    await repo.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("sailing_id")
    parser.add_argument("--withdraw", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.sailing_id, args.withdraw))
