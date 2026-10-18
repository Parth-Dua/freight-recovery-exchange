"""Phase 6 Exchange side (unit tests: fake transport and in-memory store, real OR-Tools solver).

Not ACP or Tiger evidence. The live run is scripts/recovery_smoke.py.
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from freight_allocation import SolverAllocationSeam, StaticCapacity
from freight_contracts import (
    AgentError, CounterOffer, ErrorCode, ProposeOffer, RecoveryCapacityOffers, RejectOffer, RejectReason,
    payload_digest,
)
from freight_seams import FakeDisruptionSeam, demo_displaced_bookings
from uagents_core.identity import Identity

from freight_exchange.carrier_client import CarrierRef
from freight_exchange.recovery import AcpRecoverySeam, outcome_from_snapshot
from freight_exchange.recovery_client import (
    RecoveryClient, RecoveryReplyOutcome, SailingFacts, build_recovery_request, check_offers, recovery_items,
    validate_recovery_envelope,
)

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
IDS = {c: Identity.generate() for c in ("atlas", "beacon", "cove")}
CARRIERS = {c: CarrierRef(c, c.title(), i.address, None) for c, i in IDS.items()}
# Shift the spec 06 WP5 fixture so its deadlines are in the future relative to NOW.
STRANDED = [replace(b, latest_arrival=NOW + timedelta(days=4 + i)) for i, b in enumerate(demo_displaced_bookings())]
FACTS = {
    "BEA-201": SailingFacts("BEA-201", "beacon", "AEJEA", "INBOM", NOW + timedelta(days=1), NOW + timedelta(days=3), "ACTIVE"),
    "COV-301": SailingFacts("COV-301", "cove", "AEJEA", "INBOM", NOW + timedelta(days=1), NOW + timedelta(days=4), "ACTIVE"),
    "ATL-101": SailingFacts("ATL-101", "atlas", "AEJEA", "INBOM", NOW + timedelta(days=1), NOW + timedelta(days=3), "WITHDRAWN"),
}


def request():
    return build_recovery_request(recovery_run_id="rec_1", withdrawn_sailing_id="ATL-101",
                                  items=recovery_items(STRANDED), correlation_id="rec_1", now=NOW, timeout_s=8)


def terms(sailing_id, units, price, eligible=("BK-1001", "BK-1002"), **overrides):
    f = FACTS[sailing_id]
    fields = dict(offer_id=f"rof_{sailing_id}", sailing_id=sailing_id, vessel_name="V", origin_port=f.origin_port,
                  destination_port=f.destination_port, container_type="20GP", capacity_units=units,
                  total_price_minor=price, currency="USD", departure_at=f.departure_at, arrival_at=f.arrival_at,
                  expires_at=NOW + timedelta(minutes=10), eligible_booking_ids=list(eligible))
    fields.update(overrides)
    return fields


def base(carrier, req, identity=None):
    return dict(message_id=f"m_{carrier}", correlation_id=req.correlation_id, request_id=req.request_id,
                negotiation_id=req.negotiation_id, sent_at=NOW, carrier_id=carrier,
                carrier_address=(identity or IDS[carrier]).address, in_reply_to=req.message_id)


def signed(model, carrier):
    model.signature = IDS[carrier].sign_digest(payload_digest(model))
    return model.json()


def offers_reply(carrier, req, *offers, run_id=None):
    msg = RecoveryCapacityOffers(**base(carrier, req), recovery_run_id=run_id or req.recovery_run_id,
                                 offers=list(offers))
    return signed(msg, carrier)


def outcome_for(raw, carrier="beacon", req=None):
    req = req or request()
    kind, reply, code, detail, verified = validate_recovery_envelope(raw, CARRIERS[carrier], req)
    out = RecoveryReplyOutcome(CARRIERS[carrier], req, kind, 5, reply=reply, error_code=code, detail=detail,
                               signature_verified=verified)
    out.checks = check_offers(out, FACTS, NOW)
    return out


# ---- message checks (recovery step 3) ----------------------------------------

def test_valid_signed_offer_passes_every_check():
    req = request()
    out = outcome_for(offers_reply("beacon", req, terms("BEA-201", 2, 540000)), req=req)
    assert out.kind == "offers" and out.signature_verified
    [offer] = out.valid_offers()
    assert (offer.carrier_id, offer.sailing_id, offer.capacity_units, offer.unit_price_minor) == ("beacon", "BEA-201", 2, 270000)


def test_forged_signature_wrong_run_and_unexpected_type_are_rejected():
    req = request()
    forged = RecoveryCapacityOffers(**base("beacon", req), recovery_run_id="rec_1", offers=[terms("BEA-201", 2, 540000)])
    forged.signature = Identity.generate().sign_digest(payload_digest(forged))
    assert outcome_for(forged.json(), req=req).error_code is ErrorCode.INVALID_SIGNATURE
    other_run = offers_reply("beacon", req, terms("BEA-201", 2, 540000), run_id="rec_other")
    assert outcome_for(other_run, req=req).error_code is ErrorCode.CORRELATION_MISMATCH
    quote = ProposeOffer(**base("beacon", req), offer_id="o", sailing_id="BEA-201", vessel_name="V",
                         origin_port="AEJEA", destination_port="INBOM", container_type="20GP", quantity=2,
                         total_price_minor=1, currency="USD", departure_at=NOW, arrival_at=NOW + timedelta(days=1),
                         expires_at=NOW + timedelta(minutes=1))
    assert outcome_for(signed(quote, "beacon"), req=req).error_code is ErrorCode.MALFORMED_REPLY


def test_decline_and_carrier_error_are_typed_and_carry_no_offers():
    req = request()
    decline = RejectOffer(**base("cove", req), reason_code=RejectReason.NO_ELIGIBLE_SAILING, reason="none")
    out = outcome_for(signed(decline, "cove"), carrier="cove", req=req)
    assert out.kind == "reject" and out.valid_offers() == []
    err = AgentError(**base("cove", req), error_code=ErrorCode.INTERNAL_ERROR, detail="boom")
    assert outcome_for(signed(err, "cove"), carrier="cove", req=req).kind == "carrier_error"


def check_detail(carrier, offer_terms):
    req = request()
    [check] = outcome_for(offers_reply(carrier, req, offer_terms), carrier=carrier, req=req).checks
    return check.error_code, check.detail


def test_offer_terms_are_checked_against_tiger_and_the_request():
    assert "withdrawn sailing" in check_detail("atlas", terms("ATL-101", 2, 500000))[1]
    assert "not owned" in check_detail("cove", terms("BEA-201", 2, 540000))[1]
    late = terms("BEA-201", 2, 540000, arrival_at=NOW + timedelta(days=2))  # claims to arrive earlier than Tiger says
    assert "schedule differs" in check_detail("beacon", late)[1]
    unknown = {**terms("BEA-201", 2, 540000), "sailing_id": "BEA-999"}
    assert "unknown sailing" in check_detail("beacon", unknown)[1]
    assert "subset" in check_detail("beacon", terms("BEA-201", 2, 540000, eligible=("BK-9999",)))[1]
    assert check_detail("beacon", terms("BEA-201", 2, 540000, expires_at=NOW))[0] is ErrorCode.STALE_OFFER


def test_offer_arriving_after_an_eligible_bookings_deadline_is_rejected():
    tight = [replace(STRANDED[0], latest_arrival=NOW + timedelta(days=2))]
    req = build_recovery_request(recovery_run_id="rec_1", withdrawn_sailing_id="ATL-101",
                                 items=recovery_items(tight), correlation_id="rec_1", now=NOW, timeout_s=8)
    [check] = outcome_for(offers_reply("beacon", req, terms("BEA-201", 2, 540000, eligible=("BK-1001",))),
                          req=req).checks
    assert "deadline" in check.detail


def test_same_sailing_twice_in_one_reply_is_rejected():
    req = request()
    dup = offers_reply("beacon", req, terms("BEA-201", 2, 540000), terms("BEA-201", 1, 270000, offer_id="rof_b"))
    out = outcome_for(dup, req=req)
    assert [c.ok for c in out.checks] == [False, False] and out.valid_offers() == []


# ---- orchestration: withdrawal -> one-shot round -> real solver -> persisted proposal ----

class FakeStore:
    """In-memory stand-in for RecoveryStore (unit tests only)."""

    def __init__(self, withdrawn=("ATL-101",)):
        self.withdrawn = set(withdrawn)
        self.runs, self.statuses, self.replies, self.sent = {}, [], [], []

    async def withdrawal_event(self, sailing_id):
        return f"evt_{sailing_id}" if sailing_id in self.withdrawn else None

    async def claim_run(self, *, event_id, sailing_id, triggered_at):
        for rid, run in self.runs.items():
            if run["event"] == event_id:
                return rid, False
        rid = f"rec_{len(self.runs) + 1}"
        self.runs[rid] = {"event": event_id, "status": "CREATED", "snapshot": None, "detail": None}
        return rid, True

    async def set_status(self, recovery_id, status, detail=None):
        self.runs[recovery_id].update(status=status, detail=detail)
        self.statuses.append(status)

    async def active_carriers(self):
        return list(CARRIERS.values())

    async def record_acp_message(self, *, message, carrier, direction, status, **_):
        self.sent.append((carrier.carrier_id, message))

    async def sailing_facts(self, ids):
        return {i: FACTS[i] for i in ids if i in FACTS}

    async def record_reply(self, outcome, exchange_address):
        self.replies.append(outcome)

    async def save_snapshot(self, recovery_id, *, status, snapshot, input_version, event_payload):
        from freight_exchange.recovery import _jsonable
        self.runs[recovery_id].update(status=status, snapshot=_jsonable(snapshot))
        self.statuses.append(status)

    async def load_run(self, recovery_id):
        run = self.runs.get(recovery_id)
        return None if run is None else (run["status"], run["snapshot"], run["detail"])

    # ---- phase 7 ----
    legs_by_key: dict = None

    async def set_attempts(self, recovery_id, attempts):
        self.runs[recovery_id]["attempts"] = attempts

    async def run_attempts(self, recovery_id):
        return self.runs[recovery_id].get("attempts", 1)

    async def runs_to_resume(self):
        return [r for r, run in self.runs.items() if run["status"] in ("AWAITING_APPROVAL", "RECONCILIATION_REQUIRED")]

    async def booking_facts(self, ids):
        from freight_exchange.recovery import BookingFacts
        return {i: BookingFacts(i, f"buyer_{i}", f"req_{i}", None) for i in ids}

    async def create_legs(self, recovery_id, attempt, legs, sources, facts):
        from dataclasses import replace as copy
        self.legs_by_key = self.legs_by_key or {}
        for leg in legs:
            self.legs_by_key.setdefault((recovery_id, attempt, leg.booking_id, leg.recovery_offer_id), copy(leg))

    async def update_leg(self, recovery_id, attempt, leg):
        from dataclasses import replace as copy
        self.legs_by_key[(recovery_id, attempt, leg.booking_id, leg.recovery_offer_id)] = copy(leg)

    async def legs(self, recovery_id, attempt):
        from dataclasses import replace as copy
        found = [copy(v) for (r, a, _, _), v in (self.legs_by_key or {}).items() if (r, a) == (recovery_id, attempt)]
        return sorted(found, key=lambda leg: leg.key)

    async def supersede(self, recovery_id, old_booking_id, replacement_ids):
        self.booking_status[old_booking_id] = "SUPERSEDED"
        self.assignments[old_booking_id] = ("RECOVERED", replacement_ids[0])

    async def set_assignment(self, recovery_id, old_booking_id, status):
        self.assignments[old_booking_id] = (status, None)

    @property
    def booking_status(self):
        self.__dict__.setdefault("_booking_status", {"BK-1001": "DISRUPTED", "BK-1002": "DISRUPTED",
                                                     "BK-C": "COMMITTED"})
        return self.__dict__["_booking_status"]

    @property
    def assignments(self):
        return self.__dict__.setdefault("_assignments", {})


class CarrierNetwork:
    """Answers each carrier's request; records every message the Exchange sends."""

    def __init__(self, answers):
        self.answers = answers
        self.received = []

    async def request(self, carrier, message, sender, timeout_s):
        self.received.append((carrier.carrier_id, message))
        answer = self.answers[carrier.carrier_id]
        if isinstance(answer, Exception):
            raise answer
        return answer(message), "almanac"


def demo_network():
    return CarrierNetwork({
        "atlas": lambda req: signed(RejectOffer(**base("atlas", req), reason_code=RejectReason.NO_ELIGIBLE_SAILING,
                                                reason="nothing else in time"), "atlas"),
        "beacon": lambda req: offers_reply("beacon", req, terms("BEA-201", 2, 540000)),
        "cove": lambda req: offers_reply("cove", req, terms("COV-301", 1, 270000)),
    })


def withdrawn(stranded=STRANDED):
    disruption = FakeDisruptionSeam(stranded)
    asyncio.run(disruption.withdraw(sailing_id="ATL-101", carrier_id="atlas", risk_event_id="risk_1", actor_id="op"))
    return disruption


def seam(network, store=None, capacity=None):
    store = store or FakeStore()
    allocation = SolverAllocationSeam(StaticCapacity(capacity or {"BEA-201": 2, "COV-301": 1}), clock=lambda: NOW)
    return AcpRecoverySeam(store, RecoveryClient(Identity.generate(), network, clock=lambda: NOW),
                           withdrawn(), allocation, clock=lambda: NOW), store


def run(coro):
    return asyncio.run(coro)


def test_at_06_01_via_acp_shaped_round_a_to_beacon_b_to_cove():
    network = demo_network()
    recovery, store = seam(network)
    out = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert out.status == "PROPOSED" and not out.fully_recovered   # a proposal is never a booking
    assert {(a.booking_id, a.sailing_id, a.quantity) for a in out.assignments} == {
        ("BK-1001", "BEA-201", 2), ("BK-1002", "COV-301", 1)}
    assert out.unrecovered_booking_ids == () and out.diagnostics["offers_valid"] == "2"
    assert store.statuses == ["OFFERS_REQUESTED", "PROPOSED"] and store.runs["rec_1"]["status"] == "PROPOSED"
    assert run(recovery.outcome("rec_1")) == out


def test_at_06_06_one_request_per_carrier_and_no_counteroffers():
    network = demo_network()
    recovery, store = seam(network)
    run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert sorted(c for c, _ in network.received) == ["atlas", "beacon", "cove"]
    assert all(m.message_type == "REQUEST_RECOVERY_CAPACITY" for _, m in network.received)
    assert not any(isinstance(m, CounterOffer) for _, m in network.received + store.sent)


def test_duplicate_trigger_returns_the_same_run_without_asking_carriers_again():
    network = demo_network()
    recovery, store = seam(network)
    first = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    second = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW + timedelta(seconds=5)))
    assert second == first and len(network.received) == 3 and len(store.runs) == 1


def test_carrier_timeout_and_invalid_offer_give_an_honest_partial_proposal():
    from freight_exchange.carrier_client import TransportError
    network = demo_network()
    network.answers["cove"] = TransportError(ErrorCode.CARRIER_TIMEOUT, "no reply")
    network.answers["atlas"] = lambda req: offers_reply("atlas", req, terms("ATL-101", 4, 1))  # offers its withdrawn ship
    recovery, store = seam(network)
    out = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert out.status == "PROPOSED"
    # Only Beacon's 2 slots remain valid: one booking fits, the other is reported unrecovered.
    assert {a.sailing_id for a in out.assignments} == {"BEA-201"} and len(out.unrecovered_booking_ids) == 1
    assert {a.booking_id for a in out.assignments} | set(out.unrecovered_booking_ids) == {"BK-1001", "BK-1002"}
    assert out.diagnostics["offers_rejected"] == "1"
    kinds = {r.carrier.carrier_id: r.kind for r in store.replies}
    assert kinds == {"atlas": "offers", "beacon": "offers", "cove": "exchange_error"}


def test_overstated_claim_is_trusted_only_for_what_tiger_confirms():
    network = demo_network()
    network.answers["beacon"] = lambda req: offers_reply("beacon", req, terms("BEA-201", 4, 1080000))
    network.answers["cove"] = lambda req: signed(RejectOffer(**base("cove", req), reason_code=RejectReason.NO_ELIGIBLE_SAILING,
                                                             reason="full"), "cove")
    recovery, _ = seam(network, capacity={"BEA-201": 2})
    out = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert sum(a.quantity for a in out.assignments if a.sailing_id == "BEA-201") <= 2


def test_no_withdrawal_means_no_recovery_and_no_displaced_bookings_completes_empty():
    recovery, _ = seam(demo_network(), store=FakeStore(withdrawn=()))
    try:
        run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
        raise AssertionError("recovery must not start without a withdrawal")
    except LookupError:
        pass
    network = demo_network()
    empty, _ = seam(network)
    empty.disruption = withdrawn([])
    out = run(empty.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert out.status == "COMPLETED" and out.assignments == () and network.received == []


def test_snapshot_round_trips_to_the_same_outcome():
    out = outcome_from_snapshot("rec_9", "FAILED", None, "boom")
    assert out.status == "FAILED" and out.diagnostics == {"detail": "boom"}


# ---- phase 7: holds -> approval -> confirmation -> lineage (unit: fake carriers, in-memory store) ----

from freight_exchange.booking import ConfirmResult, HoldResult  # noqa: E402


class FakeHolds:
    """Carrier holds and confirmations. `refuse_hold` maps sailing -> attempts on which the hold fails."""

    def __init__(self, refuse_hold=None, confirm_unknown=()):
        self.refuse_hold = refuse_hold or {}
        self.confirm_unknown = set(confirm_unknown)
        self.calls, self.active = [], {}
        self.hold_attempts: dict[str, int] = {}

    async def request_hold(self, carrier, *, offer_id, request_id, buyer_id, quantity, idempotency_key, ttl_seconds,
                           negotiation_id="", correlation_id="", recovery_offer_id=None):
        sailing = offer_id.split("rof_")[1].split(".")[0]
        attempt = int(idempotency_key.split(":")[2])
        self.calls.append(("hold", sailing, offer_id.split(".")[1], attempt))
        if attempt in self.refuse_hold.get(sailing, ()):
            return HoldResult(False, "INSUFFICIENT_CAPACITY", "gone")
        hold_id = f"hold_{sailing}_{attempt}_{offer_id.split('.')[1]}"
        self.active[hold_id] = "ACTIVE"
        return HoldResult(True, "HOLD_CONFIRMED", hold_id=hold_id, expires_at=NOW + timedelta(minutes=40))

    async def release_hold(self, carrier, *, hold_id, request_id="", negotiation_id="", correlation_id=""):
        self.calls.append(("release", hold_id))
        self.active[hold_id] = "RELEASED"
        return HoldResult(True, "HOLD_RELEASED", hold_id=hold_id)

    async def confirm_booking(self, carrier, *, hold_id, idempotency_key, replaces_booking_id, **_):
        self.calls.append(("confirm", hold_id, replaces_booking_id))
        if replaces_booking_id in self.confirm_unknown:
            return ConfirmResult(False, "CARRIER_TIMEOUT", "no reply")
        assert self.active.get(hold_id) == "ACTIVE", "confirmed a hold that is not active"
        self.active[hold_id] = "COMMITTED"
        return ConfirmResult(True, "BOOKING_CONFIRMED", booking_id=f"BK-NEW-{replaces_booking_id[-4:]}",
                             confirmed_at=NOW)


class FakeApproval:
    def __init__(self, decision="PENDING"):
        self.decision, self.requested = decision, []

    async def request_approval(self, *, proposal, booking_id, buyer_id):
        self.requested.append(booking_id)
        return f"apr_{proposal.recovery_run_id}_{booking_id}"

    async def approval_status(self, approval_id):
        return self.decision


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def cancelled(self, *, withdrawal_event_id, sailing_id, stranded):
        self.sent.append(("CANCELLED", tuple(b.booking_id for b in stranded)))

    async def update(self, *, recovery_run_id, stranded, proposal, outcome):
        if outcome is None:
            self.sent.append(("PLAN", proposal.needs_approval_booking_ids, proposal.unrecovered_booking_ids))
        else:
            self.sent.append(("OUTCOME", outcome.status, tuple(a.booking_id for a in outcome.assignments),
                              outcome.unrecovered_booking_ids))


def committing_seam(network=None, *, holds=None, approval=None, store=None, capacity=None, clock=lambda: NOW):
    recovery, store = seam(network or demo_network(), store=store, capacity=capacity)
    recovery.holds, recovery.approval, recovery.notifier = holds or FakeHolds(), approval, FakeNotifier()
    recovery.clock = clock
    return recovery, store


def test_at_07_01_recovered_bookings_have_confirmed_holds_and_new_booking_ids():
    recovery, store = committing_seam()
    out = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert out.status == "COMPLETED" and out.fully_recovered
    assert {(a.booking_id, a.sailing_id) for a in out.assignments} == {("BK-1001", "BEA-201"), ("BK-1002", "COV-301")}
    assert out.diagnostics["replacement:BK-1001"] == "BK-NEW-1001"
    holds = recovery.holds.calls
    assert [c[0] for c in holds] == ["hold", "hold", "confirm", "confirm"]   # every hold before any confirm
    assert [c[1] for c in holds[:2]] == ["BEA-201", "COV-301"]              # fixed (carrier, sailing) order
    assert store.booking_status["BK-1001"] == store.booking_status["BK-1002"] == "SUPERSEDED"
    assert store.assignments["BK-1001"] == ("RECOVERED", "BK-NEW-1001")


def test_at_07_02_unrelated_booking_is_never_touched():
    recovery, store = committing_seam()
    run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert store.booking_status["BK-C"] == "COMMITTED" and "BK-C" not in store.assignments
    assert all(c[2] in ("BK-1001", "BK-1002") for c in recovery.holds.calls if c[0] == "confirm")


def test_at_07_03_duplicate_trigger_after_completion_creates_nothing_new():
    recovery, store = committing_seam()
    first = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    calls = list(recovery.holds.calls)
    second = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW + timedelta(minutes=1)))
    assert second == first and recovery.holds.calls == calls


def test_at_07_04_failed_hold_releases_the_rest_and_resolves_once_then_reports_partial():
    holds = FakeHolds(refuse_hold={"COV-301": (1, 2)})   # Cove's space is gone on both attempts
    network = demo_network()
    recovery, store = committing_seam(network, holds=holds)
    out = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert ("release", "hold_BEA-201_1_BK-1001") in holds.calls       # partial hold released before re-solve
    assert len(network.received) == 6                                  # carriers asked exactly twice, never more
    assert out.status == "PARTIAL"
    assert [a.booking_id for a in out.assignments] == ["BK-1001"] and out.unrecovered_booking_ids == ("BK-1002",)
    assert "INSUFFICIENT_CAPACITY" in out.diagnostics["BK-1002"]
    assert store.booking_status["BK-1002"] == "DISRUPTED" and store.assignments["BK-1002"][0] == "UNRECOVERED"


def test_failed_hold_on_first_attempt_only_recovers_everything_on_the_resolve():
    holds = FakeHolds(refuse_hold={"COV-301": (1,)})
    recovery, _ = committing_seam(holds=holds)
    out = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert out.status == "COMPLETED" and out.diagnostics["attempts"] == "2"


def expensive_beacon():
    network = demo_network()   # 600,000 > BK-1001's 525,000 + 35,000 allowance -> needs approval
    network.answers["beacon"] = lambda req: offers_reply("beacon", req, terms("BEA-201", 2, 600000))
    return network


def test_at_07_05_over_allowance_waits_for_approval_and_survives_a_new_session():
    approval = FakeApproval("PENDING")
    recovery, store = committing_seam(expensive_beacon(), approval=approval)
    out = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert out.status == "AWAITING_APPROVAL" and approval.requested == ["BK-1001"]
    assert [a.booking_id for a in out.assignments] == ["BK-1002"]        # only the confirmed one
    assert not any(c[0] == "confirm" and c[2] == "BK-1001" for c in recovery.holds.calls)
    assert ("PLAN", ("BK-1001",), ()) in recovery.notifier.sent         # approval asked while the space is held

    # A fresh seam over the same persisted state (the buyer came back later) finishes the run.
    approval.decision = "APPROVED"
    later, _ = committing_seam(expensive_beacon(), approval=approval, store=store, holds=recovery.holds)
    done = run(later.resume("rec_1"))
    assert done.status == "COMPLETED" and {a.booking_id for a in done.assignments} == {"BK-1001", "BK-1002"}


def test_declined_approval_releases_the_hold_and_books_nothing():
    approval = FakeApproval("DECLINED")
    recovery, store = committing_seam(expensive_beacon(), approval=approval)
    out = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert out.status == "PARTIAL" and out.unrecovered_booking_ids == ("BK-1001",)
    assert ("release", "hold_BEA-201_1_BK-1001") in recovery.holds.calls
    assert store.assignments["BK-1001"] == ("DECLINED", None) and store.booking_status["BK-1001"] == "DISRUPTED"


def test_without_an_approval_answer_the_hold_lapses_and_nothing_is_confirmed():
    recovery, store = committing_seam(expensive_beacon(), approval=None)
    assert run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW)).status == "AWAITING_APPROVAL"
    recovery.clock = lambda: NOW + timedelta(hours=1)                    # hold expired at +40 min
    out = run(recovery.resume("rec_1"))
    assert out.status == "PARTIAL" and out.unrecovered_booking_ids == ("BK-1001",)
    assert not any(c[0] == "confirm" and c[2] == "BK-1001" for c in recovery.holds.calls)


def test_at_07_06_unanswered_confirmation_is_never_reported_as_recovered_and_resume_settles_it():
    holds = FakeHolds(confirm_unknown={"BK-1002"})
    recovery, store = committing_seam(holds=holds)
    out = run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    assert out.status == "RECONCILIATION_REQUIRED"
    assert [a.booking_id for a in out.assignments] == ["BK-1001"] and "BK-1002" not in out.unrecovered_booking_ids
    assert store.booking_status["BK-1002"] == "DISRUPTED"
    outcomes = [m for m in recovery.notifier.sent if m[0] == "OUTCOME"]
    assert outcomes[-1][2] == ("BK-1001",)                               # buyers hear only what is confirmed
    holds.confirm_unknown.clear()
    done = run(recovery.resume("rec_1"))
    assert done.status == "COMPLETED" and store.booking_status["BK-1002"] == "SUPERSEDED"
    confirms = [c for c in holds.calls if c[0] == "confirm" and c[2] == "BK-1002"]
    assert len(confirms) == 2                                            # same idempotency key, retried once


def test_buyers_are_told_about_the_cancellation_first_and_rebooking_only_after_confirmation():
    recovery, _ = committing_seam()
    run(recovery.recover(sailing_id="ATL-101", triggered_at=NOW))
    kinds = [m[0] for m in recovery.notifier.sent]
    assert kinds[0] == "CANCELLED" and kinds[-1] == "OUTCOME" and kinds.index("PLAN") < kinds.index("OUTCOME")


def test_worker_recovers_new_withdrawals_and_resumes_waiting_runs():
    from types import SimpleNamespace

    from freight_exchange.recovery_worker import tick

    approval = FakeApproval("PENDING")
    recovery, store = committing_seam(expensive_beacon(), approval=approval)

    async def pending_withdrawals():
        return [] if store.runs else [SimpleNamespace(sailing_id="ATL-101")]
    recovery.disruption.pending_withdrawals = pending_withdrawals

    first = run(tick(recovery))
    assert [o.status for o in first] == ["AWAITING_APPROVAL", "AWAITING_APPROVAL"]   # recovered, then resumed
    approval.decision = "APPROVED"
    assert [o.status for o in run(tick(recovery))] == ["COMPLETED"]
    assert run(tick(recovery)) == []
