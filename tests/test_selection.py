from datetime import datetime, timedelta, timezone

import pytest

from freight_exchange.selection import resolve_choice

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
OFFERS = [
    {"offer_id": "off_cove_aaaaaaaaaaaa", "display_rank": 1, "carrier_id": "cove", "display_name": "Cove Shipping (fictional)",
     "total_price_minor": 485000, "arrival_at": NOW + timedelta(days=5)},
    {"offer_id": "off_atlas_bbbbbbbbbbbb", "display_rank": 2, "carrier_id": "atlas", "display_name": "Atlas Lines (fictional)",
     "total_price_minor": 494000, "arrival_at": NOW + timedelta(days=3)},
    {"offer_id": "off_beacon_cccccccccccc", "display_rank": 3, "carrier_id": "beacon", "display_name": "Beacon Maritime (fictional)",
     "total_price_minor": 528000, "arrival_at": NOW + timedelta(days=4)},
]


@pytest.mark.parametrize("text, offer_id, source", [
    ("select 2", "off_atlas_bbbbbbbbbbbb", "chat:rank"),
    ("2", "off_atlas_bbbbbbbbbbbb", "chat:rank"),
    ("option #3 please", "off_beacon_cccccccccccc", "chat:rank"),
    ("I'll take the first one", "off_cove_aaaaaaaaaaaa", "chat:rank"),
    ("go with Beacon", "off_beacon_cccccccccccc", "chat:carrier"),
    ("the cheapest one", "off_cove_aaaaaaaaaaaa", "chat:cheapest"),
    ("fastest please", "off_atlas_bbbbbbbbbbbb", "chat:fastest"),
    ("select off_atlas_bbbbbbbbbbbb", "off_atlas_bbbbbbbbbbbb", "chat:offer_id"),
    ('{"action": "select_offer", "offer_id": "off_cove_aaaaaaaaaaaa"}', "off_cove_aaaaaaaaaaaa", "card"),
])
def test_resolves_to_a_presented_offer(text, offer_id, source):
    c = resolve_choice(text, OFFERS)
    assert (c.offer_id, c.source, c.problem) == (offer_id, source, "")


@pytest.mark.parametrize("text, problem", [
    ("select 7", "there is no option 7"),
    ("select off_atlas_ffffffffffff", "not in your latest quotes"),
    ('{"offer_id": "off_ghost_000000000000"}', "not in your latest quotes"),
])
def test_never_selects_something_not_presented(text, problem):
    c = resolve_choice(text, OFFERS)
    assert c.offer_id is None and problem in c.problem
