"""UX-NEG-001 trace reconstruction (pure; fixtures shaped like Tiger rows, not live evidence)."""

import json
from datetime import datetime, timedelta, timezone

from freight_exchange import cards
from freight_exchange.negotiation_trace import GENERIC_REASON, build_trace

T0 = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
SESSION = {"session_id": "neg_aaaaaaaaaaaaaaaa", "carrier_id": "atlas", "request_id": "req_000000000001",
           "status": "ACCEPTED", "round": 1, "final_offer_id": "off_atlas_2", "closed_at": T0 + timedelta(seconds=11)}


def offer(oid, total, sailing="ATL-101", qty=2, currency="USD", rank=1):
    return {"offer_id": oid, "sailing_id": sailing, "quantity": qty, "total_price_minor": total, "currency": currency,
            "expires_at": T0 + timedelta(minutes=10), "display_rank": rank}


def msg(mid, role, rnd, mtype, terms, sec):
    return {"message_id": mid, "sender_role": role, "round": rnd, "message_type": mtype, "public_terms": terms,
            "ts": T0 + timedelta(seconds=sec)}


OPEN = msg("m1", "carrier", 0, "PROPOSE_OFFER", {"offer_id": "off_atlas_1", "sailing_id": "ATL-101",
                                                  "total_price_minor": 520000}, 1)
COUNTER1 = msg("m2", "buyer_agent", 1, "COUNTER_OFFER", {"offer_id": "off_atlas_1", "proposed_total_price_minor": 480000,
                                                          "public_rationale": "A comparable verified offer is lower."}, 5)
REVISED1 = msg("m3", "carrier", 1, "REVISED_OFFER", {"offer_id": "off_atlas_2", "total_price_minor": 494000}, 10)


def test_zero_round_trace_has_no_invented_counter():
    t = build_trace({**SESSION, "round": 0, "final_offer_id": "off_atlas_1"}, [OPEN],
                    {"off_atlas_1": offer("off_atlas_1", 520000)}, "Atlas Lines")
    assert [s.kind for s in t.steps] == ["CARRIER_OFFER", "BUYER_ACCEPTED"]
    assert t.price_improvement_minor is None and t.comparable_terms and t.outcome == "Agreed"


def test_one_round_counter_and_revised_offer_with_grounded_improvement():
    t = build_trace(SESSION, [REVISED1, COUNTER1, OPEN],  # arrival order scrambled; persisted order wins
                    {"off_atlas_1": offer("off_atlas_1", 520000), "off_atlas_2": offer("off_atlas_2", 494000)}, "Atlas Lines")
    assert [(s.kind, s.price_minor) for s in t.steps] == [
        ("CARRIER_OFFER", 520000), ("BUYER_COUNTER", 480000), ("CARRIER_COUNTER", 494000), ("BUYER_ACCEPTED", 494000)]
    assert t.steps[1].explanation == "Another carrier quoted you less for comparable space"
    assert t.price_improvement_minor == 26000 and t.improvement_pct == 5.0


def test_two_rounds_exhausted_with_refused_second_counter():
    msgs = [OPEN, COUNTER1, REVISED1,
            msg("m4", "buyer_agent", 2, "COUNTER_OFFER", {"offer_id": "off_atlas_2", "proposed_total_price_minor": 470000,
                                                          "public_rationale": "We would book now at this price."}, 12),
            msg("m5", "carrier", 2, "REJECT_COUNTER", {"offer_id": "off_atlas_2", "outcome": "reject"}, 14)]
    t = build_trace({**SESSION, "status": "EXHAUSTED", "round": 2}, msgs,
                    {"off_atlas_1": offer("off_atlas_1", 520000), "off_atlas_2": offer("off_atlas_2", 494000)}, "Atlas Lines")
    assert [s.kind for s in t.steps] == ["CARRIER_OFFER", "BUYER_COUNTER", "CARRIER_COUNTER", "BUYER_COUNTER",
                                         "CARRIER_HELD", "FINAL_QUOTE"]
    assert t.outcome == "Final quote" and t.final_price_minor == 494000
    assert sum(s.kind == "BUYER_COUNTER" for s in t.steps) == 2  # never more than the two allowed counters


def test_carrier_accepting_the_counter_is_the_final_step():
    accepted = msg("m3", "carrier", 1, "ACCEPT_COUNTER", {"offer_id": "off_atlas_2", "total_price_minor": 480000}, 10)
    t = build_trace(SESSION, [OPEN, COUNTER1, accepted],
                    {"off_atlas_1": offer("off_atlas_1", 520000), "off_atlas_2": offer("off_atlas_2", 480000)}, "Atlas Lines")
    assert t.steps[-1].kind == "CARRIER_ACCEPTED" and t.steps[-1].price_minor == 480000


def test_declined_and_timed_out_are_labelled_without_fake_offers():
    declined = build_trace({**SESSION, "status": "REJECTED", "round": 0, "final_offer_id": None}, [], {}, "Cove Shipping")
    assert declined.outcome == "Declined" and [s.kind for s in declined.steps] == ["DECLINED"]
    assert declined.final_price_minor is None and declined.price_improvement_minor is None
    expired = build_trace({**SESSION, "status": "EXPIRED", "final_offer_id": None}, [OPEN, COUNTER1],
                          {"off_atlas_1": offer("off_atlas_1", 520000)}, "Atlas Lines")
    assert expired.outcome == "Timed out" and expired.steps[-1].kind == "TIMED_OUT" and expired.final_price_minor is None


def test_no_saving_claimed_for_different_sailing_quantity_or_a_higher_price():
    base = {"off_atlas_1": offer("off_atlas_1", 520000)}
    for final in (offer("off_atlas_2", 494000, sailing="ATL-102"), offer("off_atlas_2", 494000, qty=1),
                  offer("off_atlas_2", 530000)):
        t = build_trace(SESSION, [OPEN, COUNTER1, REVISED1], {**base, "off_atlas_2": final}, "Atlas Lines")
        assert t.price_improvement_minor is None


def test_duplicate_messages_count_once_and_unknown_rationale_is_generic():
    odd = {**COUNTER1, "public_terms": {**COUNTER1["public_terms"], "public_rationale": "free text from somewhere"}}
    t = build_trace(SESSION, [OPEN, OPEN, odd, odd, REVISED1],
                    {"off_atlas_1": offer("off_atlas_1", 520000), "off_atlas_2": offer("off_atlas_2", 494000)}, "Atlas Lines")
    assert [s.sequence for s in t.steps] == [1, 2, 3, 4]
    assert t.steps[1].explanation == GENERIC_REASON


def test_missing_history_is_reported_not_synthesised():
    t = build_trace(SESSION, [], {"off_atlas_2": offer("off_atlas_2", 494000)}, "Atlas Lines")
    assert not t.complete and "Detailed negotiation history unavailable" in t.notes
    assert t.price_improvement_minor is None


def test_detail_card_labels_counters_as_proposals_and_leaks_no_private_fields():
    leaky = {**COUNTER1, "public_terms": {**COUNTER1["public_terms"], "max_total_price_minor": 1, "floor": 2}}
    t = build_trace(SESSION, [OPEN, leaky, REVISED1],
                    {"off_atlas_1": offer("off_atlas_1", 520000), "off_atlas_2": offer("off_atlas_2", 494000)}, "Atlas Lines")
    card = cards.negotiation_detail_card(t, request_id="req_000000000001", carousel_card_id="card_c", view_card_id="card_v")
    rows = {r["label"]: r["value"] for r in card["card_payload"]["summary_rows"]}
    assert rows["Round 1 · your agent proposed"].startswith("$4,800")  # a proposal, never shown as a price paid
    assert rows["Price improvement"] == "$260 (5%) vs this carrier's opening quote"
    blob = json.dumps(card) + json.dumps(t.as_dict())
    for secret in ("max_total", "\"floor\"", "allowance", "must_deliver", "minimum_price", "urgency", "fetch1", "wallet"):
        assert secret not in blob
    assert [c["selection"]["action"] for c in card["card_payload"]["ctas"]] == ["select_offer", "back_to_offers"]
    assert len(json.dumps(card["card_payload"]).encode()) < 64 * 1024


def test_index_card_opens_each_transcript_by_session():
    a = build_trace(SESSION, [OPEN], {"off_atlas_1": offer("off_atlas_1", 520000)}, "Atlas Lines")
    c = build_trace({**SESSION, "session_id": "neg_bbbbbbbbbbbbbbbb", "carrier_id": "cove", "status": "REJECTED",
                     "final_offer_id": None}, [], {}, "Cove Shipping")
    card = cards.negotiation_index_card([a, c], request_id="req_000000000001", view_card_id="card_v")
    sel = [x["selection"] for x in card["card_payload"]["ctas"]]
    assert [s["session_id"] for s in sel] == ["neg_aaaaaaaaaaaaaaaa", "neg_bbbbbbbbbbbbbbbb"]
    assert all(s["action"] == "view_negotiation" for s in sel)


def test_refused_counter_keeps_the_quote_and_says_so():
    refused = msg("m3", "carrier", 1, "REJECT_COUNTER", {"offer_id": "off_atlas_1", "outcome": "reject"}, 10)
    t = build_trace({**SESSION, "status": "REJECTED", "final_offer_id": "off_atlas_1"}, [OPEN, COUNTER1, refused],
                    {"off_atlas_1": offer("off_atlas_1", 520000)}, "Meridian Container Line")
    assert t.outcome == "Quote stands" and t.final_price_minor == 520000 and t.offer_id == "off_atlas_1"
