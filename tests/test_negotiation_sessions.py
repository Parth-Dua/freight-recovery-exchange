"""Private buyer-agent strategy (unit). Live two-carrier sessions: scripts/card_flow_smoke.py."""

from datetime import datetime, timedelta, timezone

from freight_exchange.negotiation_sessions import BuyerPrivate, Quote, buyer_counter, rank_key, urgency

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def q(total, days=3):
    return Quote(total, NOW + days * DAY)


RELAXED = BuyerPrivate(priority="cheapest", latest_arrival=NOW + 10 * DAY)


def test_counter_uses_cheaper_rival_as_leverage_and_says_so():
    c = buyer_counter(q(520000), [q(500000)], RELAXED, 1, 2, NOW)
    assert c.amount_minor < 500000 and c.amount_minor % 1000 == 0 and "lower" in c.rationale


def test_no_rival_still_asks_a_bounded_step():
    c = buyer_counter(q(520000), [], RELAXED, 1, 2, NOW)
    assert 520000 * 0.9 <= c.amount_minor < 520000


def test_urgency_and_must_deliver_make_the_ask_gentler():
    relaxed = buyer_counter(q(520000), [], RELAXED, 1, 2, NOW).amount_minor
    urgent = BuyerPrivate(priority="cheapest", latest_arrival=NOW + 2 * DAY, must_deliver=True)
    assert buyer_counter(q(520000), [], urgent, 1, 2, NOW).amount_minor > relaxed
    assert urgency(urgent, NOW) > urgency(RELAXED, NOW)


def test_second_round_asks_half_as_much():
    first = 520000 - buyer_counter(q(520000), [], RELAXED, 1, 2, NOW).amount_minor
    second = 520000 - buyer_counter(q(520000), [], RELAXED, 2, 2, NOW).amount_minor
    assert second < first


def test_private_cap_bounds_the_counter_and_never_exceeds_the_offer():
    capped = BuyerPrivate(priority="cheapest", max_total_minor=450000, latest_arrival=NOW + 10 * DAY)
    assert buyer_counter(q(520000), [], capped, 1, 2, NOW).amount_minor == 450000
    assert buyer_counter(q(1000), [], RELAXED, 1, 1, NOW) is None or buyer_counter(q(1000), [], RELAXED, 1, 1, NOW).amount_minor < 1000


def test_fastest_buyer_does_not_use_a_slow_cheap_rival_as_full_leverage():
    fastest = BuyerPrivate(priority="fastest", latest_arrival=NOW + 10 * DAY)
    cheapest = BuyerPrivate(priority="cheapest", latest_arrival=NOW + 10 * DAY)
    slow_cheap = [q(480000, days=6)]
    assert buyer_counter(q(520000, 3), slow_cheap, fastest, 1, 2, NOW).amount_minor > \
           buyer_counter(q(520000, 3), slow_cheap, cheapest, 1, 2, NOW).amount_minor


def test_ranking_follows_priority():
    fast, cheap = q(520000, 3), q(500000, 6)
    first = min(fast.arrival_at, cheap.arrival_at)
    by = lambda p: sorted([("fast", fast), ("cheap", cheap)], key=lambda x: rank_key(x[1], x[0], first, BuyerPrivate(priority=p), 2))[0][0]
    assert by("cheapest") == "cheap" and by("fastest") == "fast"
