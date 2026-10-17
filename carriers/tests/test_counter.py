"""AT-03-03 (carrier side): the private floor is never breached and counters cannot repeat."""

from datetime import datetime, timedelta, timezone

import pytest
from freight_contracts import CounterOffer, ErrorCode
from freight_contracts.messages import RejectReason
from uagents_core.identity import Identity

from carrier_agent.agent import counter_precheck
from carrier_agent.inventory import LoggedOffer
from carrier_agent.policy import Policy, PolicyViolation, decide_counter, enforce_floor

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
ATLAS = Policy("USD", 230000, 260000, 10, 600, "earliest_arrival", concession_pct=10)
BEACON = Policy("USD", 235000, 275000, 5, 600, "earliest_arrival", concession_pct=4)
COVE = Policy("USD", 220000, 250000, 15, 600, "earliest_departure", concession_pct=3)
EXCHANGE = Identity.generate().address


@pytest.mark.parametrize("policy, original, proposed, kind, total", [
    (ATLAS, 520000, 494000, "accept", 494000),    # within Atlas' 10% concession
    (ATLAS, 520000, 465000, "revise", 468000),    # below concession, above floor -> meet at limit
    (ATLAS, 520000, 450000, "reject", None),      # below floor 460000
    (BEACON, 550000, 494000, "revise", 528000),   # Beacon concedes only 4%
    (COVE, 500000, 475000, "revise", 485000),     # Cove concedes 3%
    (COVE, 500000, 600000, "accept", 500000),     # never charge more than our own offer
])
def test_decide_counter(policy, original, proposed, kind, total):
    d = decide_counter(original, 2, proposed, policy)
    assert d.kind == kind and d.total_price_minor == total


@pytest.mark.parametrize("policy", [ATLAS, BEACON, COVE])
@pytest.mark.parametrize("proposed", [1, 100000, 400000, 459999, 460000, 470000, 999999])
def test_floor_is_never_breached(policy, proposed):
    d = decide_counter(policy.target_price_per_unit_minor * 2, 2, proposed, policy)
    if d.total_price_minor is not None:
        assert d.total_price_minor >= policy.min_price_per_unit_minor * 2
    else:
        assert d.reason_code is RejectReason.BELOW_POLICY and "2300" not in d.reason


def test_enforce_floor_refuses_any_code_path():
    with pytest.raises(PolicyViolation):
        enforce_floor(459999, ATLAS, 2)


def counter(**kw):
    base = dict(message_id="m3", correlation_id="c", request_id="r1", negotiation_id="n1", sent_at=NOW,
                offer_id="off_1", proposed_total_price_minor=480000, expires_at=NOW + timedelta(seconds=30))
    return CounterOffer(**{**base, **kw})


def logged(**kw):
    base = dict(offer_id="off_1", negotiation_id="n1", request_id="r1", sailing_id="ATL-101",
                container_type="20GP", quantity=2, total_price_minor=520000, currency="USD", round=1,
                expires_at=NOW + timedelta(minutes=5), counters_used=0)
    return LoggedOffer(**{**base, **kw})


def pre(msg=None, sender=EXCHANGE, log=None, missing=False):
    return counter_precheck(msg or counter(), sender=sender, allowed_requesters={EXCHANGE},
                            logged=None if missing else (log or logged()), now=NOW)


def test_precheck_passes_valid_counter():
    assert pre() is None


@pytest.mark.parametrize("kwargs, kind, code", [
    ({"sender": "agent1qstranger"}, "error", ErrorCode.UNAUTHORIZED_REQUESTER),
    ({"msg": counter(expires_at=NOW - timedelta(seconds=1))}, "error", ErrorCode.REQUEST_EXPIRED),
    ({"missing": True}, "reject", RejectReason.OFFER_NOT_FOUND),
    ({"log": logged(negotiation_id="other")}, "reject", RejectReason.OFFER_NOT_FOUND),
    ({"log": logged(expires_at=NOW - timedelta(seconds=1))}, "reject", RejectReason.OFFER_EXPIRED),
    ({"log": logged(counters_used=1)}, "reject", RejectReason.COUNTER_LIMIT_REACHED),
    ({"log": logged(round=2)}, "reject", RejectReason.COUNTER_LIMIT_REACHED),
    ({"msg": counter(currency="EUR")}, "error", ErrorCode.MALFORMED_REQUEST),
])
def test_precheck_rejections(kwargs, kind, code):
    got_kind, fields = pre(**kwargs)
    assert got_kind == kind and code in (fields.get("error_code"), fields.get("reason_code"))


def test_counter_message_only_allows_round_one():
    with pytest.raises(Exception):
        counter(round=2)
