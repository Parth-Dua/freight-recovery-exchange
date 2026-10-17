"""The three promises.

Each Protocol is a commitment by one owner. The other owner codes against the
Protocol and runs against the in-memory fake in `fakes.py` until the real one lands.
Nobody waits for a phase gate to start.

    Seam                Real implementation owner   Lands in
    BookingSeam         Parth                       phase 3 + 4
    DisruptionSeam      Tisya                       phase 5
    RecoverySeam        Parth                       phase 6 + 7

Rules for changing anything in this file: additive only, and the other owner says yes
in chat first. Adding a method to a Protocol breaks every implementer, so new methods
arrive on a new Protocol, not this one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from freight_seams.types import (
    BookingStatusView,
    DisruptedBooking,
    RecoveryOutcome,
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

    Parth's phase 6 solver needs `disrupted_bookings` and nothing else from the
    console. Until the console is merged, `FakeDisruptionSeam` returns the spec 06
    work-package-5 fixture, so the solver can be written and tested today.
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
    """Owner: Parth. Displaced commitments become replacement commitments.

    Tisya's console and the ASI:One summary render whatever this returns. The console
    never infers recovery state from the database; it asks here, so there is one
    source of truth about what was actually committed.
    """

    async def recover(self, *, sailing_id: str, triggered_at: datetime) -> RecoveryOutcome:
        """Run one recovery. Returning PROPOSED must never be shown as booked."""
        ...

    async def outcome(self, recovery_run_id: str) -> RecoveryOutcome | None:
        """Read a previous run, for async approval and for the console timeline."""
        ...
