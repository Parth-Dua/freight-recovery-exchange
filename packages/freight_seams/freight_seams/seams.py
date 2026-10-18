"""The three promises.

Each Protocol is a commitment by one owner. The other owner codes against the
Protocol and runs against the in-memory fake in `fakes.py` until the real one lands.
Nobody waits for a phase gate to start.

    Seam                Real implementation owner   Lands in
    BookingSeam         Parth                       phase 3 + 4
    DisruptionSeam      Tisya                       phase 5
    AllocationSeam      Tisya                       phase 6 (allocation)
    ApprovalSeam        Tisya                       phase 7 WP3
    RecoverySeam        Parth                       phase 6 (offers) + 7 (commitments)
    CardSeam            Tisya                       phase 3 WP5 + phase 7 WP5

Recovery in one line: Parth talks to the carriers and makes bookings real; Tisya
decides who goes where, checks the carriers aren't overpromising, and explains it.
The step-by-step table is in OWNERSHIP.md.

Rules for changing anything in this file: additive only, and the other owner says yes
in chat first. Adding a method to a Protocol breaks every implementer, so new methods
arrive on a new Protocol, not this one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from freight_seams.cards import CardAction, CardSpec
from freight_seams.types import (
    BookingStatusView,
    DisruptedBooking,
    RecoveryOffer,
    RecoveryOutcome,
    RecoveryProposal,
    WithdrawalSummary,
)


@runtime_checkable
class BookingSeam(Protocol):
    """Owner: Parth. Committed bookings are the thing a disruption acts on.

    Tisya's console and the ASI:One disruption reply both need these two calls and
    nothing else. Until phase 4 lands, `FakeBookingSeam` stands in, and
    `console/carrier_console/preseed.py` writes real COMMITTED rows for the demo.
    """

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
        """Claim capacity atomically and return the new booking_id.

        Must not oversell: two concurrent calls for the last slot, one wins.
        """
        ...

    async def bookings_for_buyer(self, buyer_id: str) -> list[BookingStatusView]:
        """Every booking this buyer holds, in any status. Drives the ASI:One reply."""
        ...


@runtime_checkable
class DisruptionSeam(Protocol):
    """Owner: Tisya. A sailing closes and its commitments become displaced.

    `disrupted_bookings` is the allocation's input (AllocationSeam, also Tisya's).
    `FakeDisruptionSeam` returns the spec 06 work-package-5 fixture, so recovery can
    be written and tested before the real Tiger-backed implementation lands.
    """

    async def withdraw(
        self,
        *,
        sailing_id: str,
        carrier_id: str,
        risk_event_id: str,
        actor_id: str,
    ) -> WithdrawalSummary:
        """Carrier-authorized withdrawal. Idempotent: calling twice changes nothing."""
        ...

    async def disrupted_bookings(self, sailing_id: str) -> list[DisruptedBooking]:
        """Displaced commitments for one withdrawn sailing, in solver-input shape."""
        ...


@runtime_checkable
class RecoverySeam(Protocol):
    """Owner: Parth. Runs a recovery and makes the result real.

    Orchestrates steps 3, 7 and 8 of the recovery table in OWNERSHIP.md: asks the
    carriers for replacement space over ACP, hands the validated offers to
    `AllocationSeam.allocate` (Tisya), routes over-limit bookings through
    `ApprovalSeam` (Tisya), then reserves and confirms the replacements. If a
    reservation fails it releases the rest and calls `allocate` once more.

    Tisya's console and the ASI:One summary render whatever this returns. They never
    infer recovery state from the database; they ask here, so there is one source of
    truth about what was actually committed.
    """

    async def recover(self, *, sailing_id: str, triggered_at: datetime) -> RecoveryOutcome:
        """Run one recovery. Returning PROPOSED must never be shown as booked."""
        ...

    async def outcome(self, recovery_run_id: str) -> RecoveryOutcome | None:
        """Read a previous run, for async approval and for the console timeline."""
        ...


@runtime_checkable
class CardSeam(Protocol):
    """Owner: Tisya. Everything the buyer sees and touches inside ASI:One.

    The graph calls these and emits whatever comes back; it never builds a card
    itself. That keeps one person responsible for the whole ASI:One surface:
    the payload, the fallback narration, and parsing the submission back.

    Contingency, agreed up front: `progress.md` records that card rendering from an
    external LangGraph agent is NOT yet verified, and that the SDK's
    `extract_ai_content` drops MetadataContent. If the live spike fails, every method
    below keeps its name and signature and returns a text-only CardSpec
    (`card_kind="custom"`, narration carrying the full answer). Call sites do not change.
    """

    def build_cargo_form(self, sailings: list[dict]) -> CardSpec:
        """Collect cargo details. Note: ASI:One form fields have no date kind, so
        arrival dates are a `select` built from real sailing arrivals, not free text."""
        ...

    def build_offer_selection(self, offers: list[dict]) -> CardSpec:
        """Phase 3 WP5. Carousel of real offers; every choice maps to a real offer_id."""
        ...

    def build_approval_review(self, proposal: RecoveryProposal) -> CardSpec:
        """Phase 7. Human-in-the-loop gate when recovery exceeds the buyer envelope."""
        ...

    def build_recovery_summary(self, outcome: RecoveryOutcome) -> CardSpec:
        """Phase 7 WP5. Recovered, pending and unrecovered by real booking id.
        Never render an assignment as booked before carrier confirmation."""
        ...

    def parse_card_response(self, text: str) -> CardAction | None:
        """JSON on a direct @mention, prose via the planner. Both must resolve here."""
        ...


@runtime_checkable
class AllocationSeam(Protocol):
    """Owner: Tisya. The recovery decision: who goes where.

    Called by Parth's recovery orchestration with the stranded bookings and the
    offers that passed his message-level checks. Inside, the real implementation:

      1. Checks each offer's claimed capacity against Tiger (sellable slots on that
         sailing, net of existing holds and commitments). An overstated claim is
         trusted only for what Tiger confirms, and the explanation says so.
      2. Solves the joint allocation with OR-Tools under spec 06's rules: never exceed
         verified capacity, never split a no-split booking, respect arrival deadlines
         and approved extra cost, never use a slot twice, no special-cased buyers.
      3. Objective, in order: most bookings fully recovered, then lowest total cost,
         then a deterministic tie-break.

    Returns a proposal, never a booking. Bookings whose replacement exceeds the
    buyer's approved extra cost are listed in `needs_approval_booking_ids`.
    """

    async def allocate(
        self,
        *,
        recovery_run_id: str,
        stranded: list[DisruptedBooking],
        offers: list[RecoveryOffer],
    ) -> RecoveryProposal:
        ...


@runtime_checkable
class ApprovalSeam(Protocol):
    """Owner: Tisya. Human-in-the-loop when recovery exceeds what the buyer approved.

    Spec 07: outside the buyer's limits, nothing is committed until the buyer approves
    in ASI:One, and that approval must survive the buyer leaving and coming back
    (AT-07-05). Parth's orchestration requests approval and waits on the status; it
    never commits a `needs_approval` booking while the status is not APPROVED.
    """

    async def request_approval(self, *, proposal: RecoveryProposal, booking_id: str, buyer_id: str) -> str:
        """Ask the buyer in ASI:One. Returns an approval_id. Idempotent per booking."""
        ...

    async def approval_status(self, approval_id: str) -> str:
        """One of PENDING, APPROVED, DECLINED, EXPIRED."""
        ...
