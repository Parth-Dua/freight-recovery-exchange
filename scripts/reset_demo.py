"""Reset the demo market to a clean, freshly anchored state (destructive; test data only).

Usage:
  uv run python scripts/reset_demo.py              # dry run: shows what would be deleted
  uv run python scripts/reset_demo.py --yes        # do it; sailings anchored to tomorrow 00:00 UTC
  uv run python scripts/reset_demo.py --yes --anchor 2026-10-20T00:00:00Z

One transaction: deletes every request, offer, negotiation, hold, payment record, booking,
withdrawal, recovery run, chat turn and market event; puts every sailing back to ACTIVE and
every slot to AVAILABLE; then re-seeds the fixture (ports, carriers, policies, vessels,
sailings, slots) at the new anchor. Schema, migrations and carrier identities are untouched.
Works on whatever TIGER_DATABASE_URL points at, so double-check the host it prints.
"""

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from freight_exchange.config import database_url  # noqa: E402
from freight_exchange.db.seed import load_fixture, resolve_anchor, seed, table_counts  # noqa: E402

# FK-safe order (children before parents).
TRANSACTIONAL = [
    "chat_turns", "booking_intents", "payment_records", "booking_slots", "recovery_assignments",
    "recovery_legs", "recovery_offers", "negotiation_messages", "negotiation_sessions", "recovery_runs", "bookings", "hold_slots", "holds", "offer_selections", "carrier_offer_log",
    "agent_messages", "offers", "cargo_requests", "disruption_events", "market_events", "market_snapshots",
    "negotiation_trace_cards", "card_instances", "chat_outbox", "gateway_events",
    "sailing_capacity_snapshots", "market_state_checkpoints",
]


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="actually delete (otherwise dry run)")
    parser.add_argument("--anchor", default="auto", help="ISO-8601 UTC anchor, or 'auto' = tomorrow 00:00 UTC")
    args = parser.parse_args()

    url = database_url()
    print(f"database host: {urlparse(url).hostname}")
    anchor = resolve_anchor(args.anchor)
    with psycopg.connect(url, connect_timeout=15) as conn:
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TRANSACTIONAL}
        busy = conn.execute(
            "SELECT count(*) FILTER (WHERE status <> 'ACTIVE') FROM sailings").fetchone()[0]
        print("would delete:", {t: n for t, n in counts.items() if n})
        print(f"would reactivate {busy} non-ACTIVE sailing(s); re-anchor sailings at {anchor:%Y-%m-%d %H:%M} UTC")
        if not args.yes:
            print("dry run only — re-run with --yes to reset")
            return 0
        with conn.transaction():
            for table in TRANSACTIONAL:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("UPDATE sailings SET status = 'ACTIVE', updated_at = now() WHERE status <> 'ACTIVE'")
            conn.execute("UPDATE capacity_slots SET status = 'AVAILABLE', version = version + 1 "
                         "WHERE status <> 'AVAILABLE'")
        upserted = seed(conn, load_fixture(), anchor)
        print(f"reset done; seed upserted: {upserted}")
        print(f"table counts: {table_counts(conn)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
