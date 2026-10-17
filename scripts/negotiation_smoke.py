"""Phase 3 live check against running carriers + Tiger (AT-03-01/02/03/04 evidence).

Usage: uv run python scripts/negotiation_smoke.py ["free-text request"]
"""

import asyncio
import os
import sys
import time
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


def show(title, rows):
    print(f"{title}: {len(rows)} rows")
    for row in rows:
        print("   ", row)


async def main(text: str) -> None:
    repo = await get_repository()
    client = CarrierClient(exchange_identity(os.environ["AGENT_URI"]))
    service = QuoteService(repo, client)
    parsed = parse_request(text, await repo.ports(), utcnow())
    cargo = to_cargo_spec(parsed, utcnow())

    # AT-03-01: one request returns immediately (no batching/timer)
    request_id, started = new_request_id(), time.perf_counter()
    rnd = await service.run(cargo, buyer_id="smoke-test", request_id=request_id, session_id="smoke-session")
    print(f"== negotiated round {request_id} in {time.perf_counter() - started:.1f}s\n")
    print(responses.quotes_reply(parsed, rnd))

    print("\n== AT-03-03: counters cannot be repeated; floor cannot be breached")
    for r in rnd.results:
        if r.counter is None:
            continue
        again = await client.counter_one(r.carrier, r.opening.offer, r.opening.request, 100)
        print(f"  2nd counter on {r.carrier.carrier_id} opening offer -> {again.kind} "
              f"{getattr(again.reply, 'reason_code', None) and again.reply.reason_code.value}")
        if r.counter.new_offer is not None:
            on_new = await client.counter_one(r.carrier, r.counter.new_offer, r.opening.request, 100)
            print(f"  counter on {r.carrier.carrier_id} negotiated offer -> {on_new.kind} "
                  f"{getattr(on_new.reply, 'reason_code', None) and on_new.reply.reason_code.value}")
    os.environ["NEGOTIATION_MODE"] = "off"  # fresh opening offers to probe the floor
    probe = await service.run(cargo, buyer_id="smoke-test", request_id=new_request_id(), session_id="smoke-probe")
    os.environ["NEGOTIATION_MODE"] = "auto"
    for r in probe.results:
        if r.opening.offer is None:
            continue
        low = await client.counter_one(r.carrier, r.opening.offer, r.opening.request, 100000)  # $1,000 for 2 boxes
        print(f"  $1,000 counter to {r.carrier.carrier_id} (opening {responses.money(r.opening.offer.total_price_minor)})"
              f" -> {low.kind} {getattr(low.reply, 'reason_code', None) and low.reply.reason_code.value}")

    print("\n== AT-03-02: select the top-ranked offer, then select again (idempotent)")
    offers = await repo.presented_offers(request_id)
    if offers:
        top = offers[0]
        print("  select 1 ->", await repo.select_offer(request_id, top["offer_id"], session_id="smoke-session",
                                                       source="smoke:rank"))
        print("  select 1 again ->", await repo.select_offer(request_id, top["offer_id"], session_id="smoke-session",
                                                             source="smoke:rank"))

    print("\n== Tiger evidence")
    async with repo.pool.connection() as conn:
        for title, sql in [
            ("cargo_requests", "SELECT request_id, status, session_id FROM cargo_requests WHERE request_id = %s"),
            ("offers", "SELECT carrier_id, round, status, total_price_minor, display_rank, supersedes_offer_id IS NOT NULL "
                       "FROM offers WHERE request_id = %s ORDER BY carrier_id, round"),
            ("carrier_offer_log", "SELECT carrier_id, round, total_price_minor, counters_used FROM carrier_offer_log "
                                  "WHERE request_id = %s ORDER BY carrier_id, round"),
            ("offer_selections", "SELECT request_id, offer_id, source FROM offer_selections WHERE request_id = %s"),
            ("market_events", "SELECT event_type, coalesce(carrier_id, '-') FROM market_events WHERE correlation_id = "
                              "(SELECT correlation_id FROM cargo_requests WHERE request_id = %s) ORDER BY ts"),
        ]:
            show(title, await (await conn.execute(sql, (request_id,))).fetchall())
    await repo.pool.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT))
