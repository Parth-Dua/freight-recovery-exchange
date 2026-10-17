"""Plain data crossing a lane boundary.

Frozen dataclasses, no dependencies, so this package installs into the Exchange
runtime, the carrier runtime and the console without touching anyone's resolver.

Money is always integer minor units. Times are always timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DisruptedBooking:
    """One displaced commitment, in the shape the phase 6 solver consumes.

    Field names follow `request_items` in docs/specs/06-joint-recovery-allocation.md
    section 5, so building REQUEST_RECOVERY_CAPACITY from this is a direct mapping.
    """

    booking_id: str
    buyer_id: str
    carrier_id: str
    sailing_id: str
    container_type: str
    quantity: int
    origin_port: str
    destination_port: str
    latest_arrival: datetime
    allow_split: bool
    original_price_minor: int
    currency: str
    max_additional_cost_minor: int = 0

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise ValueError(f"{self.booking_id}: quantity must be >= 1")
        if self.original_price_minor <= 0:
            raise ValueError(f"{self.booking_id}: price must be a positive minor-unit integer")
        if self.latest_arrival.tzinfo is None:
            raise ValueError(f"{self.booking_id}: latest_arrival must be timezone-aware")


@dataclass(frozen=True)
class BookingStatusView:
    """What a buyer is told about one booking. Read model only, never authority."""

    booking_id: str
    buyer_id: str
    status: str
    sailing_id: str
    carrier_name: str
    disrupted_at: datetime | None = None
    replaced_by_booking_id: str | None = None

    @property
    def is_disrupted(self) -> bool:
        return self.status == "DISRUPTED"


@dataclass(frozen=True)
class WithdrawalSummary:
    """Result of a carrier-authorized withdrawal."""

    event_id: str
    sailing_id: str
    carrier_id: str
    already_withdrawn: bool
    disrupted_booking_ids: tuple[str, ...] = ()
    untouched_booking_count: int = 0


@dataclass(frozen=True)
class RecoveryAssignment:
    booking_id: str
    offer_id: str
    sailing_id: str
    carrier_id: str
    quantity: int
    quoted_price_minor: int
    arrival_at: datetime


@dataclass(frozen=True)
class RecoveryOutcome:
    """Truthful result of one recovery run. PROPOSED is not COMMITTED."""

    recovery_run_id: str
    status: str
    assignments: tuple[RecoveryAssignment, ...] = ()
    unrecovered_booking_ids: tuple[str, ...] = ()
    diagnostics: dict[str, str] = field(default_factory=dict)

    @property
    def fully_recovered(self) -> bool:
        return self.status == "COMPLETED" and not self.unrecovered_booking_ids


@dataclass(frozen=True)
class RecoveryOffer:
    """One carrier's replacement capacity, after Parth's message-level validation.

    Mirrors RECOVERY_OFFER in docs/specs/06-joint-recovery-allocation.md section 5.
    `capacity_units` is what the carrier *claims*; AllocationSeam checks it against
    Tiger before trusting it. `total_price_minor` covers all `capacity_units`.
    """

    offer_id: str
    carrier_id: str
    sailing_id: str
    capacity_units: int
    total_price_minor: int
    currency: str
    arrival_at: datetime
    expires_at: datetime
    eligible_booking_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.capacity_units < 1:
            raise ValueError(f"{self.offer_id}: capacity_units must be >= 1")
        if self.total_price_minor <= 0:
            raise ValueError(f"{self.offer_id}: price must be a positive minor-unit integer")
        if self.arrival_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError(f"{self.offer_id}: datetimes must be timezone-aware")

    @property
    def unit_price_minor(self) -> int:
        return self.total_price_minor // self.capacity_units


@dataclass(frozen=True)
class RecoveryProposal:
    """The allocation decision. A proposal, never a booking.

    Mirrors RecoveryProposal in spec 06 section 5, plus what the buyer-facing side
    needs: which bookings need approval, a plain-language reason per booking, and the
    capacity each offer was trusted for after its claim was checked.
    """

    recovery_run_id: str
    assignments: tuple[RecoveryAssignment, ...] = ()
    unrecovered_booking_ids: tuple[str, ...] = ()
    needs_approval_booking_ids: tuple[str, ...] = ()
    explanations: dict[str, str] = field(default_factory=dict)
    verified_capacity: dict[str, int] = field(default_factory=dict)
    input_version: str = ""

    @property
    def recovered_count(self) -> int:
        return len({a.booking_id for a in self.assignments})

    @property
    def total_cost_minor(self) -> int:
        return sum(a.quoted_price_minor for a in self.assignments)
