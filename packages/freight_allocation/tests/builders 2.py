"""Small builders so each test states only what matters to it."""

from __future__ import annotations

from datetime import datetime, timezone

from freight_seams import DisruptedBooking, RecoveryOffer
from freight_allocation import Solution, check_claims, solve

AS_OF = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def booking(
    booking_id: str,
    quantity: int,
    *,
    split: bool = False,
    latest: str = "2026-10-23T08:00",
    price: int | None = None,
    extra: int = 20_000,
    currency: str = "USD",
    container: str = "20GP",
) -> DisruptedBooking:
    return DisruptedBooking(
        booking_id=booking_id,
        buyer_id=f"buyer_{booking_id}",
        carrier_id="atlas",
        sailing_id="ATL-101",
        container_type=container,
        quantity=quantity,
        origin_port="AEJEA",
        destination_port="INBOM",
        latest_arrival=at(latest),
        allow_split=split,
        original_price_minor=price if price is not None else 262_500 * quantity,
        currency=currency,
        max_additional_cost_minor=extra,
    )


def offer(
    offer_id: str,
    sailing_id: str,
    carrier_id: str,
    units: int,
    total: int,
    *,
    arrival: str = "2026-10-22T20:00",
    expires: str = "2026-10-18T12:00",
    eligible: tuple[str, ...] = (),
    currency: str = "USD",
) -> RecoveryOffer:
    return RecoveryOffer(
        offer_id=offer_id,
        sailing_id=sailing_id,
        carrier_id=carrier_id,
        capacity_units=units,
        total_price_minor=total,
        currency=currency,
        arrival_at=at(arrival),
        expires_at=at(expires),
        eligible_booking_ids=eligible,
    )


def run(stranded, offers, sellable: dict[str, int] | None = None, as_of: datetime = AS_OF) -> Solution:
    """Solve. With no `sellable`, every claim is trusted (sum of claims per sailing)."""
    if sellable is None:
        sellable = {}
        for o in offers:
            sellable[o.sailing_id] = sellable.get(o.sailing_id, 0) + o.capacity_units
    return solve(list(stranded), list(offers), check_claims(list(offers), sellable), as_of)


def placed(solution: Solution) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {}
    for a in solution.assignments:
        out.setdefault(a.booking_id, []).append((a.sailing_id, a.quantity))
    return out
