"""AT-02-03 against LIVE carrier agents: every failure path yields a typed error.

Usage: uv run python scripts/acp_failure_smoke.py   (carriers must be running)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from freight_contracts import CargoSpec  # noqa: E402
from uagents_core.identity import Identity  # noqa: E402
from uagents_core.models import Model  # noqa: E402

from freight_exchange.carrier_client import CarrierClient, CarrierRef, build_request  # noqa: E402
from freight_exchange.quotes import exchange_identity  # noqa: E402
from freight_exchange.repository import get_repository  # noqa: E402

NOW = datetime.now(timezone.utc)
GOOD = CargoSpec(origin_port="AEJEA", destination_port="INBOM", container_type="20GP", quantity=2)


class Garbage(Model):  # a schema no carrier protocol declares
    message_type: str = "REQUEST_OFFER"
    nonsense: int = 1


async def main() -> None:
    repo = await get_repository()
    carriers = {c.carrier_id: c for c in await repo.active_carriers()}
    exchange = CarrierClient(exchange_identity(os.environ["AGENT_URI"]), timeout_s=5)
    atlas = carriers["atlas"]

    def req(cargo=GOOD, **kw):
        now = datetime.now(timezone.utc)  # fresh per case so deadlines are meaningful
        r = build_request(cargo, request_id="req_failsmoke", correlation_id="flow_failsmoke", now=now, timeout_s=5)
        for k, v in kw.items():
            setattr(r, k, v)
        return r

    cases = []
    # 1. Unauthorized requester: a valid signed request from an identity the carrier does not trust
    stranger = CarrierClient(Identity.generate(), timeout_s=5)
    cases.append(("unauthorized requester", await stranger.request_one(atlas, req())))
    # 2. Invalid cargo constraint reaching the carrier (deadline already passed)
    past = CargoSpec(origin_port="AEJEA", destination_port="INBOM", container_type="20GP", quantity=2,
                     latest_arrival=NOW - timedelta(hours=1))
    cases.append(("invalid cargo (past deadline)", await exchange.request_one(atlas, req(past))))
    # 3. Expired request
    cases.append(("expired request", await exchange.request_one(atlas, req(expires_at=NOW - timedelta(seconds=5)))))
    # 4. Unsupported corridor / container -> typed REJECT
    hc = CargoSpec(origin_port="AEJEA", destination_port="INBOM", container_type="40HC", quantity=1)
    cases.append(("unsupported container", await exchange.request_one(atlas, req(hc))))
    # 5. Malformed ACP payload (unknown schema) sent to a live carrier
    raw_transport = exchange.transport
    try:
        payload, _ = await asyncio.wait_for(raw_transport.request(atlas, Garbage(), exchange.identity, 5), 8)
        print(f"{'malformed payload':32} -> unexpected reply {payload[:80]}")
    except Exception as exc:
        print(f"{'malformed payload':32} -> {type(exc).__name__}: {getattr(exc, 'code', '')} {getattr(exc, 'detail', exc)}")
    # 6. Unknown carrier: address not in the registry / not running
    ghost = CarrierRef("ghost", "Ghost Lines", Identity.generate().address, "http://127.0.0.1:8199/submit")
    cases.append(("unknown/unreachable carrier", await exchange.request_one(ghost, req())))
    # 7. Control: the same client and carrier still produce a valid signed offer
    cases.append(("control (valid request)", await exchange.request_one(atlas, req())))

    for name, out in cases:
        code = out.error_code.value if out.error_code else (out.reply.reason_code.value if out.kind == "reject" else "-")
        print(f"{name:32} -> kind={out.kind:15} code={code:28} sig_ok={out.signature_verified} {out.detail[:60]}")
    await repo.pool.close()


asyncio.run(main())
