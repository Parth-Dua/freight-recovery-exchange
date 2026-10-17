import pytest

from freight_exchange.negotiation import counter_target


@pytest.mark.parametrize("own, best, budget, expected", [
    (520000, 520000, 560000, 494000),   # best offer: 5% under itself
    (550000, 520000, 560000, 494000),   # others anchor to the best opening offer
    (520000, 500000, None, 475000),
    (520000, 520000, 480000, 456000),   # budget below offers caps the anchor
    (100, 100, None, None),             # rounds to $0 -> no counter
])
def test_counter_target(own, best, budget, expected):
    assert counter_target(own, best, budget, pct=5) == expected


def test_budget_is_never_exceeded_and_never_above_own_offer():
    for own in range(300000, 700000, 7919):
        t = counter_target(own, 450000, 500000, pct=5)
        assert t is None or (t < own and t <= 500000 and t % 1000 == 0)
