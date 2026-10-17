"""Seam contracts between the Exchange lane (Parth) and the Console lane (Tisya).

Import the Protocol you depend on, code against it, and run against the fake until
the other side's real implementation lands. See seams.py for who owes what.
"""

from freight_seams.cards import CardAction, CardSpec
from freight_seams.fakes import (
    FakeBookingSeam,
    FakeCardSeam,
    FakeDisruptionSeam,
    FakeRecoverySeam,
    demo_displaced_bookings,
)
from freight_seams.seams import BookingSeam, CardSeam, DisruptionSeam, RecoverySeam
from freight_seams.types import (
    BookingStatusView,
    DisruptedBooking,
    RecoveryAssignment,
    RecoveryOutcome,
    WithdrawalSummary,
)

__all__ = [
    "BookingSeam",
    "CardAction",
    "CardSeam",
    "CardSpec",
    "BookingStatusView",
    "DisruptedBooking",
    "DisruptionSeam",
    "FakeBookingSeam",
    "FakeCardSeam",
    "FakeDisruptionSeam",
    "FakeRecoverySeam",
    "RecoveryAssignment",
    "RecoveryOutcome",
    "RecoverySeam",
    "WithdrawalSummary",
    "demo_displaced_bookings",
]
