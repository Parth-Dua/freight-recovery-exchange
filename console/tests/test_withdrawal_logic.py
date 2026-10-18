"""Pure-logic tests. No database, no network. These are unit tests and nothing more.

Per AGENTS.md, a mock run never counts as integration evidence. The live-Tiger
assertions live in test_withdrawal_live.py and skip unless TIGER_DATABASE_URL is set.
"""

from __future__ import annotations

import dataclasses
import inspect
import re

import pytest

from carrier_console import risk, withdrawal
from carrier_console.queries import SailingRow
from carrier_console.web import _at_risk, _status_tag
from carrier_console.withdrawal import RejectionCode, WithdrawSailing, new_event_id

from datetime import datetime, timezone


def test_risk_fixtures_match_canonical_shape() -> None:
    events = risk.load_risk_events()
    assert len(events) >= 3
    for event in events:
        assert event.source_kind == "simulated"
        assert event.source_provider == "fixture"
        assert event.source_url is None
        assert event.region_code
        assert event.affected_corridors


def test_risk_module_performs_no_writes() -> None:
    """AT-05-01: an advisory must never withdraw anything by itself."""
    source = inspect.getsource(risk)
    assert "INSERT" not in source.upper()
    assert "UPDATE" not in source.upper()
    assert "carrier_console.db" not in source


def test_get_risk_event_unknown_id_returns_none() -> None:
    assert risk.get_risk_event("risk_does_not_exist") is None


def test_event_id_format_matches_exchange_style() -> None:
    assert re.fullmatch(r"evt_[0-9a-f]{12}", new_event_id())
    assert new_event_id() != new_event_id()


def test_command_is_immutable() -> None:
    command = WithdrawSailing(
        sailing_id="ATL-101",
        carrier_id="atlas",
        reason_risk_event_id="risk_sim_0001",
        actor_id="operator:atlas",
        authorization_ref="SIM-ADV-2026-014",
        idempotency_key="k1",
        reason_text="simulated incident",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        command.sailing_id = "BEA-201"  # type: ignore[misc]


def test_rejection_codes_cover_spec_05_cases() -> None:
    codes = {code.value for code in RejectionCode}
    assert "CROSS_CARRIER_WITHDRAWAL" in codes  # AT-05-04
    assert "UNKNOWN_SAILING" in codes


def test_withdrawal_scopes_every_write_by_sailing_id() -> None:
    """AT-05-02: nothing may change a booking on another sailing."""
    from freight_disruption import withdrawal as moved

    source = inspect.getsource(moved)
    for statement in re.findall(r"UPDATE\s+(bookings|capacity_slots)(.+?)\"\"\"", source, re.S):
        assert "sailing_id = %s" in statement[1]


def _row(status: str, committed: int = 2) -> SailingRow:
    moment = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
    return SailingRow(
        sailing_id="ATL-101",
        carrier_id="atlas",
        carrier_name="Carrier Atlas",
        corridor_id="AEJEA-INBOM",
        origin_port="AEJEA",
        destination_port="INBOM",
        departure_at=moment,
        arrival_at=moment,
        status=status,
        sellable_slots=4,
        committed_bookings=committed,
    )


def test_status_tags_use_plain_words() -> None:
    assert "Scheduled" in _status_tag("ACTIVE")
    assert "cds--tag--red" in _status_tag("WITHDRAWN")
    assert "cds--tag--gray" in _status_tag("DEPARTED")  # unknown status falls back to gray


def test_only_active_sailings_on_an_affected_corridor_are_at_risk() -> None:
    from carrier_console.risk import load_risk_events

    events = load_risk_events()
    assert _at_risk(_row("ACTIVE"), events) is not None
    assert _at_risk(_row("WITHDRAWN"), events) is None


def test_review_page_lists_who_will_be_disrupted() -> None:
    from carrier_console.risk import get_risk_event
    from carrier_console.web import _review_body

    html = _review_body(_row("ACTIVE"), get_risk_event("risk_sim_0001"),
                        [("BK-1", "buyer_b"), ("BK-2", "buyer_c")])
    assert "Withdraw ATL-101?" in html
    assert "<b>2</b> committed booking(s)" in html
    assert "BK-1" in html and "buyer_c" in html
    assert 'action="/withdraw"' in html
    assert 'name="risk_event_id" value="risk_sim_0001"' in html
    assert 'href="/">Cancel' in html


def test_review_page_with_nobody_on_board() -> None:
    from carrier_console.risk import get_risk_event
    from carrier_console.web import _review_body

    html = _review_body(_row("ACTIVE", committed=0), get_risk_event("risk_sim_0002"), [])
    assert "nobody is disrupted" in html


def test_banner_redirects_encode_their_message() -> None:
    from carrier_console.web import _back

    response = _back(err="CROSS_CARRIER_WITHDRAWAL: beacon does not own ATL-101")
    location = response.headers["location"]
    assert " " not in location
    assert location.startswith("/?err=CROSS_CARRIER_WITHDRAWAL")


def test_preseed_refuses_buyer_a() -> None:
    """PRD 16.1: Buyer A must be booked live in ASI:One, never preseeded."""
    from carrier_console.preseed import LIVE_ONLY_BUYERS

    assert "buyer_a" in LIVE_ONLY_BUYERS


def test_preseed_signature_is_obviously_not_a_real_one() -> None:
    """A preseeded offer must never be mistaken for a verified carrier signature."""
    from carrier_console.preseed import PRESEED_SIGNATURE

    assert "NOT_A_VERIFIED" in PRESEED_SIGNATURE


def test_preseed_claims_capacity_before_writing_a_booking() -> None:
    """No overselling: slots are claimed under FOR UPDATE and refused if short."""
    import inspect

    from carrier_console import preseed

    source = inspect.getsource(preseed._claim_slots)
    assert "FOR UPDATE" in source
    assert "PreseedRefused" in source
