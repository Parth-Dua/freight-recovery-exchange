"""Live Stripe TEST-mode check of the checkout adapter (no card, nothing paid).

Usage: uv run python scripts/stripe_smoke.py
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from freight_exchange.payments import Expected, StripeTestPayments  # noqa: E402


async def main() -> None:
    pay = StripeTestPayments()
    intent = f"intent_smoke_{uuid4().hex[:10]}"
    kwargs = dict(intent_id=intent, request_id="req_smoke", offer_id="off_smoke", freight_minor=528000,
                  fee_minor=2500, currency="USD", freight_label="Beacon BEA-201, 2x20GP (simulated)",
                  expires_at=datetime.now(timezone.utc) + timedelta(minutes=31))
    first = await pay.create_checkout(**kwargs)
    again = await pay.create_checkout(**kwargs)
    print(f"created {first.session_id[:14]}… amount_total={first.amount_total} url_host={first.url.split('/')[2]}")
    print(f"idempotent create returns same session: {again.session_id == first.session_id}")
    expected = Expected(intent, 530500, "USD")
    print("verify before payment ->", (await pay.verify(first.session_id, expected)).status)
    print("verify with wrong amount ->", (await pay.verify(first.session_id, Expected(intent, 1, "USD"))).status)
    await pay.expire(first.session_id)
    after = await pay.verify(first.session_id, expected)
    print("verify after expire ->", after.status, f"({after.provider_status}, livemode={after.livemode})")


asyncio.run(main())
