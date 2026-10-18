"""Plain-language reasons, one per booking. These are what the recovery card shows,
so every sentence must be true of the solution it describes."""

from __future__ import annotations

from datetime import datetime

from freight_seams import DisruptedBooking, RecoveryOffer
from freight_allocation.model import Solution, ineligibility
from freight_allocation.verify import ClaimCheck


def money(minor: int, currency: str) -> str:
    return f"{currency} {minor / 100:,.2f}"


def _when(moment: datetime) -> str:
    return moment.strftime("%d %b %H:%M UTC")


def _claim_note(check: ClaimCheck) -> str:
    return (
        f" {check.carrier_id} offered {check.claimed} slot(s) on {check.sailing_id}, "
        f"but only {check.sellable_on_sailing} are actually sellable, so no more than that was used."
    )


def _unrecovered_reason(
    booking: DisruptedBooking,
    offers: list[RecoveryOffer],
    checks: dict[str, ClaimCheck],
    as_of: datetime,
) -> str:
    if not offers:
        return "No carrier offered replacement space."

    reasons = {o.offer_id: ineligibility(booking, o, as_of) for o in offers}
    eligible = [o for o in offers if reasons[o.offer_id] is None]
    if not eligible:
        if "too_late" in reasons.values():
            return f"No replacement sailing arrives by your deadline ({_when(booking.latest_arrival)})."
        if all(r == "expired" for r in reasons.values()):
            return "Every replacement offer expired before it could be used."
        return "No carrier offered space that fits this booking."

    overstated = [checks[o.offer_id] for o in eligible
                  if checks[o.offer_id].overstated and checks[o.offer_id].claimed >= booking.quantity]
    note = _claim_note(overstated[0]) if overstated else ""

    if not booking.allow_split and max(checks[o.offer_id].trusted for o in eligible) < booking.quantity:
        return (f"No single replacement sailing has {booking.quantity} slots free, "
                f"and this booking can't be split across sailings.{note}")
    return ("There wasn't enough replacement space for every booking. This allocation "
            f"recovers as many bookings in full as possible.{note}")


def explain(
    stranded: list[DisruptedBooking],
    offers: list[RecoveryOffer],
    checks: dict[str, ClaimCheck],
    solution: Solution,
    as_of: datetime,
) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for booking in sorted(stranded, key=lambda b: b.booking_id):
        bid = booking.booking_id
        mine = [a for a in solution.assignments if a.booking_id == bid]
        if not mine:
            reasons[bid] = _unrecovered_reason(booking, offers, checks, as_of)
            continue

        if len(mine) == 1:
            a = mine[0]
            where = f"Moved to {a.sailing_id} ({a.carrier_id}), arriving {_when(a.arrival_at)}."
        else:
            parts = ", ".join(f"{a.quantity} on {a.sailing_id} ({a.carrier_id})" for a in mine)
            where = f"Split across sailings, as you allowed: {parts}."

        total = solution.cost_by_booking[bid]
        price = f" New price {money(total, booking.currency)}, was {money(booking.original_price_minor, booking.currency)}."
        if bid in solution.needs_approval_booking_ids:
            over = total - booking.original_price_minor - booking.max_additional_cost_minor
            price += (f" That is {money(over, booking.currency)} more than the extra cost you approved, "
                      "so nothing is booked until you approve it.")

        notes = "".join(_claim_note(checks[a.offer_id]) for a in mine if checks[a.offer_id].overstated)
        reasons[bid] = where + price + notes
    return reasons
