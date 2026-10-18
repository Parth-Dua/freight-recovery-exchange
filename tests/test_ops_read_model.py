"""Mission Control read model (HX-OPS-001): privacy allowlists, pseudonyms, legible summaries. Pure unit tests."""

from datetime import datetime, timezone

from freight_exchange.ops.read_model import (
    BuyerLabels, EventContext, booking_state, public_event, public_objective, route, sanitize_check)

T = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)


def ctx():
    labels = BuyerLabels([("demo_buyer_b", T), ("agent1qrealbuyeraddressxxxxxxxxxxxx", T), ("buyer_c", T)])
    sessions = {"neg_1": {"session_id": "neg_1", "kind": "recovery", "request_id": "req_1", "booking_id": "BK-1",
                          "recovery_id": "rec_1", "buyer": "agent1qrealbuyeraddressxxxxxxxxxxxx", "carrier_id": "beacon"}}
    return EventContext(sessions, {"beacon": "Beacon Maritime"}, labels, {}, {"BK-1": "demo_buyer_b"})


def ev(t, entity_type, entity_id, payload, **kw):
    return {"ts": T, "event_id": "e1", "event_type": t, "entity_type": entity_type, "entity_id": entity_id,
            "correlation_id": kw.get("corr"), "carrier_id": kw.get("carrier"), "sailing_id": kw.get("sailing"),
            "corridor_id": None, "payload": payload}


def test_buyer_labels_keep_seeded_letters_and_pseudonymise_real_addresses():
    labels = ctx().labels
    assert labels.label("demo_buyer_b") == "Buyer B" and labels.label("buyer_c") == "Buyer C"
    assert labels.label("agent1qrealbuyeraddressxxxxxxxxxxxx") == "Buyer A"  # first free letter
    assert labels.node("demo_buyer_b") == "buyer-b"


def test_event_payloads_are_allowlisted_and_never_carry_private_fields():
    raw = ev("APPROVAL_REQUESTED", "booking", "BK-1", {
        "buyer_id": "demo_buyer_b", "booking_id": "BK-1", "expires_at": "x", "approval_id": "apr_rec_1_BK-1",
        "max_additional_cost_minor": 50000, "plan": [{"quoted_price_minor": 1}]})
    pe = public_event(raw, ctx())
    assert pe["publicPayload"] == {"booking_id": "BK-1", "expires_at": "x"}
    assert pe["incidentId"] == "rec_1" and pe["buyer"] == "Buyer B"
    assert "demo_buyer_b" not in str(pe) and not sanitize_check(pe)


def test_counter_is_described_as_a_proposal_from_the_pseudonymous_buyer():
    pe = public_event(ev("COUNTER_SENT", "negotiation", "neg_1",
                         {"round": 1, "proposed_total_price_minor": 525000, "public_rationale": "x", "buyer_max": 1}), ctx())
    assert pe["summary"] == "Buyer A agent proposed $5,250 to Beacon Maritime (round 1)"
    assert pe["publicPayload"] == {"round": 1, "proposed_total_price_minor": 525000}
    assert pe["actorKind"] == "BUYER_AGENT" and pe["incidentId"] == "rec_1" and pe["sessionKind"] == "RECOVERY"
    assert "agent1qrealbuyer" not in str(pe)


def test_internal_duplicate_events_are_hidden():
    assert public_event(ev("COUNTER_OFFER_SENT", "offer", "off_1", {}), ctx()) is None
    assert public_event(ev("CHAT_NOTIFICATION_QUEUED", "chat_outbox", "1", {}), ctx()) is None


def test_solver_objective_exposes_only_an_aggregate_must_deliver_count():
    o = public_objective({"must_deliver": 1, "recovered": 3, "approvals": 2, "incremental_cost_minor": 100, "arrival_rank": 9})
    assert o == {"mustDeliverRecovered": 1, "recovered": 3, "approvals": 2, "incrementalCostMinor": 100, "arrivalRank": 9}
    assert not sanitize_check({"objective": o})
    assert sanitize_check({"x": {"must_deliver": True}}) == ["$.x.must_deliver"]


def test_booking_state_needs_evidence_and_never_turns_green_early():
    held = [{"attempt": 1, "status": "HELD", "needs_approval": True}]
    assert booking_state({"status": "DISRUPTED"}, held, "AWAITING_APPROVAL", None) == "AWAITING_APPROVAL"
    assert booking_state({"status": "DISRUPTED"}, held, "AWAITING_APPROVAL", {"decision": "approve"}) == "CONFIRMING"
    confirmed = [{"attempt": 1, "status": "CONFIRMED", "needs_approval": False}]
    assert booking_state({"status": "DISRUPTED"}, confirmed, "CONFIRMING", None) != "CONFIRMED"  # not superseded yet
    assert booking_state({"status": "SUPERSEDED"}, confirmed, "COMPLETED", None) == "CONFIRMED"
    assert booking_state({"status": "DISRUPTED"}, [], "UNRESOLVED", None) == "UNRESOLVED"


def test_routes_pass_through_the_strait_for_gulf_ports():
    pts = route("AEJEA", "INBOM")
    assert pts[0] == [55.061, 25.011] and pts[-1] == [72.84, 18.95]
    assert [56.45, 26.45] in pts  # Strait of Hormuz waypoint
