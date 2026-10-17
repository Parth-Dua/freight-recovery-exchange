"""Real quote round against live carrier agents + Tiger, then prove persistence with SELECTs.

Usage: uv run python scripts/quote_smoke.py ["free-text request"]
Requires: carriers running (see README), .env with AGENT_URI, TIGER_DATABASE_URL, PGPASSWORD.
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from freight_exchange import responses  # noqa: E402
from freight_exchange.carrier_client import CarrierClient  # noqa: E402
from freight_exchange.observability import new_request_id  # noqa: E402
from freight_exchange.quotes import QuoteService, exchange_identity, to_cargo_spec, utcnow  # noqa: E402
from freight_exchange.repository import get_repository  # noqa: E402
from freight_exchange.request_parser import parse_request  # noqa: E402

DEFAULT = ("Move two 20-foot containers from Jebel Ali to Mumbai by October 23. "
           "Don't split them. Maximum simulated freight budget $5,600.")


async def main(text: str) -> None:
    repo = await get_repository()
    identity = exchange_identity(os.environ["AGENT_URI"])
    parsed = parse_request(text, await repo.ports(), utcnow())
    cargo = to_cargo_spec(parsed, utcnow())
    request_id = new_request_id()
    rnd = await QuoteService(repo, CarrierClient(identity)).run(cargo, buyer_id="smoke-test", request_id=request_id)
    print(responses.quotes_reply(parsed, rnd))
    print("\n--- Tiger evidence for", request_id, "/", rnd.correlation_id)
    async with repo.pool.connection() as conn:
        for title, sql in [
            ("cargo_requests", "SELECT request_id, status, quantity, container_type, correlation_id FROM cargo_requests WHERE request_id = %s"),
            ("offers", "SELECT offer_id, carrier_id, sailing_id, total_price_minor, status, negotiation_id, left(carrier_signature, 12) FROM offers WHERE request_id = %s ORDER BY total_price_minor"),
            ("agent_messages", "SELECT direction, message_type, carrier_id, status, latency_ms, payload->'_exchange'->>'resolution' FROM agent_messages WHERE request_id = %s ORDER BY ts, direction DESC"),
            ("market_events", "SELECT to_char(ts, 'HH24:MI:SS.MS'), event_type, coalesce(carrier_id, '-'), entity_id FROM market_events WHERE correlation_id = (SELECT correlation_id FROM cargo_requests WHERE request_id = %s) ORDER BY ts"),
        ]:
            rows = await (await conn.execute(sql, (request_id,))).fetchall()
            print(f"{title}: {len(rows)} rows")
            for row in rows:
                print("  ", row)
    await repo.pool.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT))
