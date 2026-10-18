"""In-memory stand-ins, so neither lane waits for the other.

These are fakes for local development and unit tests. Per AGENTS.md a run against
a fake is never Agentverse, ASI:One, Stripe, ACP or Tiger evidence. Name any test
that uses them as a unit test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import count

from freight_seams.cards import CardAction, CardSpec
from freight_seams.types import (
    BookingStatusView,
    DisruptedBooking,
    RecoveryAssignment,
    RecoveryOffer,
    RecoveryOutcome,
    RecoveryProposal,
    WithdrawalSummary,
)

CORRIDOR_ORIGIN = "AEJEA"
CORRIDOR_DESTINATION = "INBOM"
CONTAINER = "20GP"


def _arrival(days: int) -> datetime:
    return datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc) + timedelta(days=days)


def demo_displaced_bookings() -> list[DisruptedBooking]:
    """The spec 06 work-package-5 fixture: Atlas displaces Buyer A (2, unsplittable)
    and Buyer B (1). Beacon can take 2 and Cove 1, so the feasible answer is A to
    Beacon and B to Cove. Buyer C is on another sailing and never appears here.
    """
    return [
        DisruptedBooking(
            booking_id="BK-1001",
            buyer_id="buyer_a",
            carrier_id="atlas",
            sailing_id="ATL-101",
            container_type=CONTAINER,
            quantity=2,
            origin_port=CORRIDOR_ORIGIN,
            destination_port=CORRIDOR_DESTINATION,
            latest_arrival=_arrival(0),
            allow_split=False,
            original_price_minor=525_000,
            currency="USD",
            max_additional_cost_minor=35_000,
        ),
        DisruptedBooking(
            booking_id="BK-1002",
            buyer_id="buyer_b",
            carrier_id="atlas",
            sailing_id="ATL-101",
            container_type=CONTAINER,
            quantity=1,
            origin_port=CORRIDOR_ORIGIN,
            destination_port=CORRIDOR_DESTINATION,
            latest_arrival=_arrival(1),
            allow_split=True,
            original_price_minor=262_500,
            currency="USD",
            max_additional_cost_minor=20_000,
        ),
    ]


class FakeBookingSeam:
    """Stands in for Parth's phase 3 + 4 until they land."""

    def __init__(self) -> None:
        self._bookings: dict[str, BookingStatusView] = {}
        self._ids = count(2001)

    async def commit_booking(
        self,
        *,
        buyer_id: str,
        sailing_id: str,
        container_type: str,
        quantity: int,
        total_price_minor: int,
        currency: str,
    ) -> str:
        booking_id = f"BK-{next(self._ids)}"
        self._bookings[booking_id] = BookingStatusView(
            booking_id=booking_id,
            buyer_id=buyer_id,
            status="COMMITTED",
            sailing_id=sailing_id,
            carrier_name=sailing_id.split("-")[0].title(),
        )
        return booking_id

    async def bookings_for_buyer(self, buyer_id: str) -> list[BookingStatusView]:
        return [view for view in self._bookings.values() if view.buyer_id == buyer_id]

    def mark_disrupted(self, booking_id: str, at: datetime) -> None:
        """Test helper, not part of the seam."""
        view = self._bookings[booking_id]
        self._bookings[booking_id] = BookingStatusView(
            booking_id=view.booking_id,
            buyer_id=view.buyer_id,
            status="DISRUPTED",
            sailing_id=view.sailing_id,
            carrier_name=view.carrier_name,
            disrupted_at=at,
        )


class FakeDisruptionSeam:
    """Stands in for Tisya's console until it is merged."""

    def __init__(self, displaced: list[DisruptedBooking] | None = None) -> None:
        self._displaced = displaced if displaced is not None else demo_displaced_bookings()
        self._withdrawn: set[str] = set()

    async def withdraw(
        self, *, sailing_id: str, carrier_id: str, risk_event_id: str, actor_id: str
    ) -> WithdrawalSummary:
        already = sailing_id in self._withdrawn
        self._withdrawn.add(sailing_id)
        affected = tuple(b.booking_id for b in self._displaced if b.sailing_id == sailing_id)
        return WithdrawalSummary(
            event_id=f"evt_fake_{sailing_id.lower()}",
            sailing_id=sailing_id,
            carrier_id=carrier_id,
            already_withdrawn=already,
            disrupted_booking_ids=() if already else affected,
            untouched_booking_count=1,
        )

    async def disrupted_bookings(self, sailing_id: str) -> list[DisruptedBooking]:
        if sailing_id not in self._withdrawn:
            return []
        return [b for b in self._displaced if b.sailing_id == sailing_id]


class FakeRecoverySeam:
    """Stands in for Parth's phase 6 + 7 until they land.

    Deliberately returns a PARTIAL outcome by default: the console must render an
    honest partial result correctly before it ever renders a happy path.
    """

    def __init__(self, disruption: FakeDisruptionSeam | None = None) -> None:
        self._disruption = disruption or FakeDisruptionSeam()
        self._runs: dict[str, RecoveryOutcome] = {}

    async def recover(self, *, sailing_id: str, triggered_at: datetime) -> RecoveryOutcome:
        displaced = await self._disruption.disrupted_bookings(sailing_id)
        recovered = displaced[:1]
        unrecovered = tuple(b.booking_id for b in displaced[1:])
        run_id = f"rec_fake_{sailing_id.lower()}"
        outcome = RecoveryOutcome(
            recovery_run_id=run_id,
            status="PARTIAL" if unrecovered else "COMPLETED",
            assignments=tuple(
                RecoveryAssignment(
                    booking_id=b.booking_id,
                    offer_id=f"OFR-{b.booking_id}",
                    sailing_id="BEA-201",
                    carrier_id="beacon",
                    quantity=b.quantity,
                    quoted_price_minor=b.original_price_minor + 15_000,
                    arrival_at=b.latest_arrival,
                )
                for b in recovered
            ),
            unrecovered_booking_ids=unrecovered,
            diagnostics={"source": "FakeRecoverySeam", "solver": "none"},
        )
        self._runs[run_id] = outcome
        return outcome

    async def outcome(self, recovery_run_id: str) -> RecoveryOutcome | None:
        return self._runs.get(recovery_run_id)


class FakeCardSeam:
    """Text-only stand-in, which is also the agreed fallback if the spike fails."""

    def build_cargo_form(self, sailings: list[dict]) -> CardSpec:
        return CardSpec("custom", {"sailings": len(sailings)},
                        "Tell me origin, destination, container count and latest arrival.")

    def build_offer_selection(self, offers: list[dict]) -> CardSpec:
        return CardSpec("carousel", {"items": offers},
                        f"{len(offers)} offer(s) meet your constraints. Reply 'select 1'.")

    def build_approval_review(self, proposal: RecoveryOutcome) -> CardSpec:
        return CardSpec("review", {"run": proposal.recovery_run_id},
                        "This recovery exceeds your approved limits. Approve or decline.")

    def build_recovery_summary(self, outcome: RecoveryOutcome) -> CardSpec:
        return CardSpec("detail", {"run": outcome.recovery_run_id},
                        f"{len(outcome.assignments)} recovered, "
                        f"{len(outcome.unrecovered_booking_ids)} still unrecovered.")

    def parse_card_response(self, text: str) -> CardAction | None:
        cleaned = text.strip()
        if not cleaned:
            return None
        return CardAction(action="submit", values={"text": cleaned}, raw_text=cleaned)


def demo_recovery_offers() -> list[RecoveryOffer]:
    """Replacement space for the spec 06 WP5 fixture: Beacon 2 slots, Cove 1 slot."""
    expires = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    return [
        RecoveryOffer(
            offer_id="ROF-BEA-201",
            carrier_id="beacon",
            sailing_id="BEA-201",
            capacity_units=2,
            total_price_minor=540_000,
            currency="USD",
            arrival_at=datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc),
            expires_at=expires,
        ),
        RecoveryOffer(
            offer_id="ROF-COV-301",
            carrier_id="cove",
            sailing_id="COV-301",
            capacity_units=1,
            total_price_minor=270_000,
            currency="USD",
            arrival_at=datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc),
            expires_at=expires,
        ),
    ]


class FakeAllocationSeam:
    """Stands in for Tisya's allocation until the OR-Tools model lands.

    NOT the solver. It is a largest-booking-first greedy pass that trusts every claimed
    capacity. It happens to get the demo fixture right (A to Beacon, B to Cove) but it
    will fail cases the real model must pass, so never use it as evidence of anything.
    """

    async def allocate(
        self,
        *,
        recovery_run_id: str,
        stranded: list[DisruptedBooking],
        offers: list[RecoveryOffer],
    ) -> RecoveryProposal:
        remaining = {offer.offer_id: offer.capacity_units for offer in offers}
        assignments: list[RecoveryAssignment] = []
        unrecovered: list[str] = []
        needs_approval: list[str] = []
        explanations: dict[str, str] = {}

        for booking in sorted(stranded, key=lambda b: (-b.quantity, b.booking_id)):
            candidates = [
                offer for offer in offers
                if remaining[offer.offer_id] >= booking.quantity
                and offer.arrival_at <= booking.latest_arrival
                and (not offer.eligible_booking_ids or booking.booking_id in offer.eligible_booking_ids)
            ]
            if not candidates:
                unrecovered.append(booking.booking_id)
                explanations[booking.booking_id] = "No replacement sailing had room before the deadline."
                continue

            best = min(candidates, key=lambda o: (o.unit_price_minor * booking.quantity, o.offer_id))
            price = best.unit_price_minor * booking.quantity
            remaining[best.offer_id] -= booking.quantity
            assignments.append(
                RecoveryAssignment(
                    booking_id=booking.booking_id,
                    offer_id=best.offer_id,
                    sailing_id=best.sailing_id,
                    carrier_id=best.carrier_id,
                    quantity=booking.quantity,
                    quoted_price_minor=price,
                    arrival_at=best.arrival_at,
                )
            )
            if price > booking.original_price_minor + booking.max_additional_cost_minor:
                needs_approval.append(booking.booking_id)
            explanations[booking.booking_id] = f"Moved to {best.sailing_id} ({best.carrier_id})."

        return RecoveryProposal(
            recovery_run_id=recovery_run_id,
            assignments=tuple(assignments),
            unrecovered_booking_ids=tuple(unrecovered),
            needs_approval_booking_ids=tuple(needs_approval),
            explanations=explanations,
            verified_capacity={offer.offer_id: offer.capacity_units for offer in offers},
            input_version="fake",
        )


class FakeApprovalSeam:
    """Stands in for Tisya's ASI:One approval flow. Pass the decision you want to test."""

    def __init__(self, decision: str = "APPROVED") -> None:
        self._decision = decision
        self._requests: dict[str, str] = {}

    async def request_approval(self, *, proposal: RecoveryProposal, booking_id: str, buyer_id: str) -> str:
        approval_id = f"apr_{proposal.recovery_run_id}_{booking_id}"
        self._requests[approval_id] = buyer_id
        return approval_id

    async def approval_status(self, approval_id: str) -> str:
        return self._decision if approval_id in self._requests else "EXPIRED"
