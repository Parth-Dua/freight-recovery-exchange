"""What the buyer reads in ASI:One when their sailing is cancelled and recovered.

Pure text builders: no database, no network. Four messages, in the order a buyer can see
them:

  CANCELLED       the carrier withdrew the sailing; replacements are being sought
  NEEDS_APPROVAL  a replacement exists but costs more than the buyer pre-approved
  REBOOKED        a carrier CONFIRMED the replacement (never sent for a mere proposal)
  NOT_RECOVERED   no replacement could be found

Truthfulness rules (spec 07): a solver proposal is never described as a booking, nothing
claims a refund, and every message says plainly what was and was not booked or charged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from freight_seams import DisruptedBooking, RecoveryAssignment

CANCELLED = "CANCELLED"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
REBOOKED = "REBOOKED"
NOT_RECOVERED = "NOT_RECOVERED"

NO_DEADLINE_YEAR = 2100  # freight_disruption maps "no stated deadline" to 2100-01-01


@dataclass(frozen=True)
class CancellationContext:
    """What the buyer needs to know about the withdrawn sailing."""

    sailing_id: str
    carrier_name: str
    origin: str
    destination: str
    departure_at: datetime
    reason: str | None = None


def money(minor: int, currency: str) -> str:
    return f"{currency} {minor / 100:,.2f}"


def when(moment: datetime) -> str:
    return moment.strftime("%d %b %H:%M UTC")


def first_sentence(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    match = re.match(r"(.+?[.!?])(\s|$)", text.strip())
    return (match.group(1) if match else text.strip()).strip()


def _containers(booking: DisruptedBooking) -> str:
    return f"{booking.quantity} x {booking.container_type}"


def _carrier(carrier_id: str, carrier_names: dict[str, str] | None) -> str:
    return (carrier_names or {}).get(carrier_id) or carrier_id.title()


def _plan(assignments: list[RecoveryAssignment], carrier_names: dict[str, str] | None) -> str:
    if len(assignments) == 1:
        a = assignments[0]
        return f"{a.sailing_id} with {_carrier(a.carrier_id, carrier_names)}, arriving {when(a.arrival_at)}"
    parts = ", ".join(
        f"{a.quantity} on {a.sailing_id} ({_carrier(a.carrier_id, carrier_names)})" for a in assignments
    )
    return f"split across sailings as you allowed: {parts}, all arriving by {when(max(a.arrival_at for a in assignments))}"


def _deadline(booking: DisruptedBooking) -> str:
    if booking.latest_arrival.year >= NO_DEADLINE_YEAR:
        return ""
    return f" Your deadline is {when(booking.latest_arrival)}."


def cancellation_notice(booking: DisruptedBooking, context: CancellationContext) -> str:
    reason = first_sentence(context.reason)
    paragraphs = [
        f"Your sailing {context.sailing_id} from {context.origin} to {context.destination}, "
        f"departing {when(context.departure_at)}, has been cancelled by {context.carrier_name}.",
    ]
    if reason:
        paragraphs.append(f"Reason given: {reason}")
    paragraphs.append(
        f"This affects booking {booking.booking_id} ({_containers(booking)}). I'm asking other "
        f"carriers for replacement space now and will message you here with the result."
        f"{_deadline(booking)}"
    )
    paragraphs.append("Nothing new will be booked or charged without telling you first.")
    return "\n\n".join(paragraphs)


def needs_approval_message(
    booking: DisruptedBooking,
    assignments: list[RecoveryAssignment],
    carrier_names: dict[str, str] | None = None,
) -> str:
    """A proposal that costs more than the buyer pre-approved. Nothing is booked yet."""
    if not assignments:
        raise ValueError(f"{booking.booking_id}: needs_approval_message needs the proposed assignments")
    total = sum(a.quoted_price_minor for a in assignments)
    extra = total - booking.original_price_minor
    allowed = booking.max_additional_cost_minor
    if allowed > 0:
        limit = f"You agreed in advance to up to {money(allowed, booking.currency)} extra"
    else:
        limit = "You haven't approved any extra cost for rebooking"
    return "\n\n".join(
        [
            f"I found replacement space for booking {booking.booking_id} ({_containers(booking)}): "
            f"{_plan(assignments, carrier_names)}.",
            f"It costs {money(total, booking.currency)}, which is {money(extra, booking.currency)} more than "
            f"your original {money(booking.original_price_minor, booking.currency)}. {limit}, "
            "so I need your OK. Nothing is booked yet.",
            f'Reply "approve {booking.booking_id}" to book it, or "decline {booking.booking_id}" to leave it.',
        ]
    )


def rebooked_message(
    booking: DisruptedBooking,
    assignments: list[RecoveryAssignment],
    carrier_names: dict[str, str] | None = None,
) -> str:
    """Only for assignments a carrier has CONFIRMED (a RecoveryOutcome), never a proposal."""
    if not assignments:
        raise ValueError(f"{booking.booking_id}: rebooked_message needs confirmed assignments")
    total = sum(a.quoted_price_minor for a in assignments)
    return "\n\n".join(
        [
            f"You're rebooked. Booking {booking.booking_id} ({_containers(booking)}) now sails on "
            f"{_plan(assignments, carrier_names)}.",
            f"New price {money(total, booking.currency)}, was "
            f"{money(booking.original_price_minor, booking.currency)}. The carrier has confirmed the space.",
        ]
    )


def not_recovered_message(booking: DisruptedBooking, reason: str | None) -> str:
    paragraphs = [f"I couldn't find replacement space for booking {booking.booking_id} ({_containers(booking)})."]
    if reason and reason.strip():
        paragraphs.append(reason.strip())
    paragraphs.append("Your original booking stays on record. Nothing new was booked or charged.")
    return "\n\n".join(paragraphs)


# ---- replies to "approve BK-..." / "decline BK-..." ----------------------------------------

def _reply_hint(booking_id: str) -> str:
    return f'Reply "approve {booking_id}" or "decline {booking_id}".'


def approval_approved(booking_id: str, plan: tuple[dict, ...]) -> str:
    where = ", ".join(f"{p['sailing_id']} with {str(p['carrier_id']).title()}" for p in plan) or "the replacement"
    return (
        f"Approved. I'm confirming {where} for booking {booking_id} with the carrier now and will "
        "message you here once it's confirmed. Nothing is final until the carrier confirms."
    )


def approval_declined(booking_id: str) -> str:
    return (
        f"Declined. I won't book that replacement for {booking_id}. Your original booking stays "
        "on record, and nothing new was booked or charged."
    )


def approval_already(booking_id: str, status: str) -> str:
    verb = "approved" if status == "APPROVED" else "declined"
    return f"You already {verb} the replacement for {booking_id}, so I haven't changed anything."


def approval_expired(booking_id: str) -> str:
    return (
        f"The replacement for {booking_id} expired before you answered, so nothing was booked. "
        "Your original booking stays on record."
    )


def approval_not_found(booking_id: str) -> str:
    return (
        f"I can't find a replacement waiting for your approval under {booking_id}. "
        "Check the booking number in my last message."
    )


def approval_nothing_waiting() -> str:
    return "There's nothing waiting for your approval right now."


def approval_which_one(booking_ids: tuple[str, ...]) -> str:
    return (
        f"You have more than one replacement waiting: {', '.join(booking_ids)}. "
        'Reply "approve BK-..." or "decline BK-..." with the one you mean.'
    )


def approval_ambiguous(booking_id: str) -> str:
    return f"Your reply says both approve and decline. {_reply_hint(booking_id)}"
