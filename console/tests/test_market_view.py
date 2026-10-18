"""Market view: renders from a snapshot, and X-ray stays demo-only. No database."""

from __future__ import annotations

from carrier_console import market_view
from carrier_console.market import sample_snapshot
from carrier_console.web import xray_allowed


def _link(**_: str) -> str:
    return "/market"


def test_sample_story_numbers_hold_together() -> None:
    snap = sample_snapshot()
    assert [s.label for s in snap.stranded] == ["A", "B", "C"]
    assert snap.exchange_recovered == 3 and snap.baseline_recovered == 1
    b = snap.booking("BK-0A14FFE2")
    assert b.max_per_container_minor == 580_000  # $5,000 original + $800 pre-approved


def test_default_conversation_is_the_must_deliver_buyer() -> None:
    snap = sample_snapshot()
    assert snap.session(None).session_id == "S-0193"  # Buyer B and Beacon


def test_xray_shows_both_hidden_limits_only_when_on() -> None:
    snap = sample_snapshot()
    on = market_view.negotiation(snap, snap.session(None), True, _link)
    off = market_view.negotiation(snap, snap.session(None), False, _link)
    assert "floor $5,100" in on and "max $5,800" in on
    assert "Outside limits" in on  # Buyer C: $5,000 max under Beacon's $5,100 floor
    assert "floor $5,100" not in off and "Outside limits" not in off


def test_xray_is_demo_only_for_live_data(monkeypatch) -> None:
    monkeypatch.delenv("MARKET_XRAY", raising=False)
    assert xray_allowed(sample=True)
    assert not xray_allowed(sample=False)
    monkeypatch.setenv("MARKET_XRAY", "1")
    assert xray_allowed(sample=False)


def test_allocation_compares_first_come_with_the_exchange() -> None:
    html = market_view.allocation(sample_snapshot())
    assert "1 / 3" in html and "3 / 3" in html
    assert "Asked in ASI:One" in html  # Buyer C waits for approval


def test_disruption_lists_every_stranded_container() -> None:
    html = market_view.disruption(sample_snapshot())
    assert html.count('class="stranded"') == 4
    assert "ATL-101 withdrawn" in html
