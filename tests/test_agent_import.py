"""Graph-level tests with in-memory fakes (the real Tiger/carrier paths run in scripts/*_smoke.py)."""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import pytest
from agentverse_sdk.langgraph.content import extract_ai_content
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from freight_exchange.quotes import QuoteRound
from freight_exchange.repository import Corridor, Port

REF = re.compile(r"Ref: (req_[0-9a-f]{12})$")
AEJEA = Port("AEJEA", "Jebel Ali", ("jebel ali", "dubai"))
INBOM = Port("INBOM", "Mumbai", ("mumbai",))
NOW = datetime.now(timezone.utc)


class FakeRepo:
    def __init__(self):
        self.turns, self.selections, self.requests = {}, [], {}
        self.offers = [
            {"offer_id": "off_atlas_aaaaaaaaaaaa", "display_rank": 1, "carrier_id": "atlas",
             "display_name": "Atlas Lines (fictional)", "sailing_id": "ATL-101", "quantity": 2,
             "container_type": "20GP", "total_price_minor": 494000, "currency": "USD",
             "departure_at": NOW + timedelta(days=1), "arrival_at": NOW + timedelta(days=3),
             "expires_at": NOW + timedelta(minutes=10), "status": "PROPOSED", "round": 2},
            {"offer_id": "off_beacon_bbbbbbbbbbbb", "display_rank": 2, "carrier_id": "beacon",
             "display_name": "Beacon Maritime (fictional)", "sailing_id": "BEA-201", "quantity": 2,
             "container_type": "20GP", "total_price_minor": 528000, "currency": "USD",
             "departure_at": NOW + timedelta(days=2), "arrival_at": NOW + timedelta(days=4),
             "expires_at": NOW - timedelta(minutes=1), "status": "PROPOSED", "round": 2},  # already expired
        ]

    async def ports(self):
        return [AEJEA, INBOM]

    async def corridors(self):
        return [Corridor(AEJEA, INBOM, ("20GP",), 3, 6, NOW + timedelta(days=1))]

    async def claim_turn(self, key, session, ref, window_s):
        if key in self.turns:
            return self.turns[key]
        self.turns[key] = {"request_ref": ref, "status": "PENDING", "route": None, "reply": None}
        return None

    async def get_turn(self, key):
        return self.turns.get(key)

    async def complete_turn(self, key, route, reply):
        self.turns[key].update(status="DONE", route=route, reply=reply)

    async def latest_request(self, session):
        return self.requests.get(session)

    async def presented_offers(self, request_id):
        return [dict(o) for o in self.offers]

    async def select_offer(self, request_id, offer_id, *, session_id, source):
        offer = next(o for o in self.offers if o["offer_id"] == offer_id)
        if offer["expires_at"] <= datetime.now(timezone.utc):
            return {"status": "OFFER_EXPIRED"}
        self.selections.append((request_id, offer_id, source))
        return {"status": "SELECTED"}


class FakeQuotes:
    def __init__(self, repo):
        self.calls, self.repo = [], repo

    async def run(self, cargo, *, buyer_id, request_id=None, correlation_id=None, session_id=None):
        self.calls.append((cargo, buyer_id, request_id, session_id))
        self.repo.requests[session_id] = {"request_id": request_id, "status": "QUOTED", "correlation_id": "f",
                                          "requirements": {"cargo": json.loads(cargo.json())}}
        return QuoteRound(request_id, "flow_x", cargo, [], [])


class FakeBookingService:
    def __init__(self):
        self.calls = []

    async def start(self, **kw):
        from freight_exchange.booking import BookingStep
        self.calls.append(("start", kw))
        return BookingStep("no_selection")

    async def check_payment(self, **kw):
        from freight_exchange.booking import BookingStep
        self.calls.append(("check_payment", kw))
        return BookingStep("no_intent")

    async def cancel(self, **kw):
        from freight_exchange.booking import BookingStep
        self.calls.append(("cancel", kw))
        return BookingStep("nothing_to_cancel")


@pytest.fixture
def svc(monkeypatch):
    import agent as module

    repo = FakeRepo()
    quotes = FakeQuotes(repo)
    booking = FakeBookingService()

    async def fake_services():
        return repo, quotes

    async def fake_booking():
        return booking

    monkeypatch.setattr(module, "get_services", fake_services)
    monkeypatch.setattr(module, "get_booking_service", fake_booking)
    repo.booking = booking
    monkeypatch.setattr(module, "DUPLICATE_WAIT_S", 1)
    return repo, quotes


@pytest.fixture
def chat():
    """A conversation on one thread with a checkpointer, like the LangGraph server."""
    import agent as module

    graph = module._builder.compile(checkpointer=InMemorySaver())
    thread = {"configurable": {"thread_id": "t1"}, "metadata": {"agentverse_session": "s1",
                                                                "agentverse_sender": "agent1qbuyer"}}

    def send(text):
        state = asyncio.run(graph.ainvoke({"messages": [{"role": "human", "content": text}]}, config=thread))
        return state["messages"][-1].content

    return send


def _invoke(text, **metadata):
    from agent import agent

    # Same input shape the Agentverse SDK posts to /runs/wait.
    return asyncio.run(agent.ainvoke(
        {"messages": [{"role": "human", "content": text}]}, config={"metadata": metadata}))


def _sdk_text(state):
    data = {"messages": [m.model_dump() for m in state["messages"]]}
    content = asyncio.run(extract_ai_content(data))
    assert len(content) == 1
    return content[0].text


def test_exported_agent_imports_without_agentverse():  # T01
    import agent as module

    assert isinstance(module.agent, CompiledStateGraph)
    assert set(module.agent.get_graph().nodes) >= {"start", "quote", "requote", "select", "info", "finish"}


def test_corridors_come_from_repository_through_sdk_extraction(svc):
    text = _sdk_text(_invoke("What corridors do you currently support?"))
    assert "Jebel Ali (AEJEA) → Mumbai (INBOM)" in text and REF.search(text)


def test_quote_request_uses_chat_ref_as_tiger_request_id(svc):
    _, quotes = svc
    state = _invoke("Quote 2x20GP from Jebel Ali to Mumbai, budget $5,600", agentverse_sender="agent1qbuyer",
                    agentverse_session="s9")
    ref = REF.search(state["messages"][-1].content).group(1)
    cargo, buyer, request_id, session = quotes.calls[0]
    assert (request_id, buyer, session) == (ref, "agent1qbuyer", "s9")
    assert (cargo.origin_port, cargo.destination_port, cargo.quantity, cargo.max_total_price_minor) == (
        "AEJEA", "INBOM", 2, 560000)


def test_asi_one_mention_reaches_quote_route(svc):
    _invoke("@freight-recovery-exc Quote 2x20GP from Jebel Ali to Mumbai")
    cargo = svc[1].calls[0][0]
    assert (cargo.origin_port, cargo.destination_port, cargo.quantity) == ("AEJEA", "INBOM", 2)


# ---- AT-03-05: clarification, never invented requirements -------------------------------
def test_missing_quantity_is_asked_then_completed_by_follow_up(svc, chat):
    first = chat("Quote from Jebel Ali to Mumbai by Oct 30")
    assert "number of containers" in first and not svc[1].calls
    chat("2")
    cargo = svc[1].calls[0][0]
    assert (cargo.quantity, cargo.origin_port, cargo.latest_arrival.day) == (2, "AEJEA", 30)


def test_unknown_ports_ask_instead_of_calling_carriers(svc):
    text = _invoke("quote 2 containers from shanghai to LA")["messages"][-1].content
    assert "origin port and destination port" in text and not svc[1].calls


def test_unserved_direction_is_clarified_not_sent(svc):
    text = _invoke("quote 2x20GP from Mumbai to Jebel Ali")["messages"][-1].content
    assert "Corridors I serve: Jebel Ali (AEJEA) → Mumbai (INBOM)" in text and not svc[1].calls


def test_container_type_not_offered_is_clarified(svc):
    text = _invoke("quote 1 40ft high cube container from Jebel Ali to Mumbai")["messages"][-1].content
    assert "40HC isn't offered" in text and not svc[1].calls


def test_past_deadline_is_rejected_before_any_carrier_call(svc):
    text = _invoke("Quote 2x20GP from Jebel Ali to Mumbai by Jan 2 2020")["messages"][-1].content
    assert "couldn't send that to carriers" in text and not svc[1].calls


# ---- AT-03-02 / AT-03-04: selection --------------------------------------------------------
def test_select_persists_offer_selected_not_a_booking(svc, chat):
    repo, _ = svc
    chat("Quote 2x20GP from Jebel Ali to Mumbai")
    text = chat("select 1")
    assert repo.selections == [(repo.requests["s1"]["request_id"], "off_atlas_aaaaaaaaaaaa", "chat:rank")]
    assert "Selected: option 1 — Atlas Lines" in text and "not a booking" in text


def test_expired_offer_cannot_be_selected(svc, chat):
    chat("Quote 2x20GP from Jebel Ali to Mumbai")
    text = chat("go with Beacon")
    assert "OFFER_EXPIRED" in text and "requote" in text and not svc[0].selections


def test_select_without_quotes_explains(svc):
    text = _invoke("select 1", agentverse_session="fresh")["messages"][-1].content
    assert "no offers in this conversation" in text


def test_requote_reruns_the_latest_request(svc, chat):
    chat("Quote 2x20GP from Jebel Ali to Mumbai")
    chat("requote")
    calls = svc[1].calls
    assert len(calls) == 2 and calls[0][0].json() == calls[1][0].json() and calls[0][2] != calls[1][2]


# ---- duplicate delivery (ASI:One sends some messages twice) -------------------------------
def test_duplicate_delivery_returns_the_original_reply(svc):
    first = _invoke("Quote 2x20GP from Jebel Ali to Mumbai", agentverse_session="dup")["messages"][-1].content
    second = _invoke("@freight-recovery-exc  Quote 2x20GP from Jebel Ali to Mumbai",
                     agentverse_session="dup")["messages"][-1].content
    assert second == first and len(svc[1].calls) == 1


def test_book_without_selection_asks_to_select_and_touches_nothing(svc):
    text = _invoke("Can you book two containers tomorrow?")["messages"][-1].content
    assert "Select an offer first" in text and not svc[1].calls
    assert svc[0].booking.calls[0][0] == "start"


@pytest.mark.parametrize("message, call", [("I paid", "check_payment"), ("cancel", "cancel"), ("book it", "start")])
def test_booking_routes_reach_the_booking_service(svc, message, call):
    _invoke(message, agentverse_session="sb")
    assert svc[0].booking.calls[-1][0] == call and svc[0].booking.calls[-1][1]["session_id"] == "sb"


def test_recovery_allowance_is_opt_in():
    import agent as module
    assert module.recovery_allowance_minor("book it") == 0
    assert module.recovery_allowance_minor("book it, auto-rebook up to $300 extra") == 30000


def test_threaded_follow_up_only_returns_new_turn(svc):
    from agent import agent

    first = _invoke("What can you do?")
    second = asyncio.run(agent.ainvoke(
        {"messages": [*first["messages"], {"role": "human", "content": "Book a container"}]}))
    assert "Select an offer first" in _sdk_text(second)


def test_logs_correlate_request_id_without_message_text(caplog, svc):
    logger = logging.getLogger("freight_exchange")
    logger.propagate = True
    try:
        with caplog.at_level(logging.INFO, logger="freight_exchange"):
            state = _invoke("Can you book two containers tomorrow? my email is test@example.com",
                            agentverse_sender="agent1qexample", agentverse_session="session-1")
    finally:
        logger.propagate = False

    request_id = REF.search(state["messages"][-1].content).group(1)
    handled = [r.getMessage() for r in caplog.records if "request_handled" in r.getMessage()]
    assert len(handled) == 1
    line = handled[0]
    assert request_id in line and '"route": "book"' in line and '"status": "ok"' in line
    assert "duration_ms" in line and "agent1qexample" in line
    assert "test@example.com" not in line and "containers" not in line


def test_service_failure_returns_retry_safe_error(monkeypatch):
    import agent as module

    async def broken():
        raise RuntimeError("db password=hunter2")

    monkeypatch.setattr(module, "get_services", broken)
    text = _invoke("What corridors do you support?")["messages"][-1].content
    assert text.startswith(module.SERVICE_UNAVAILABLE_REPLY) and "hunter2" not in text


def test_approval_replies_route_before_booking_and_recovery():  # #15 NEEDS-FROM-PARTH 1
    import agent as module

    assert module._route_with_context("I approve the rebooking for BK-1001", None)[0] == module.ROUTE_APPROVAL
    assert module._route_with_context("@freight-recovery-exc decline bk-1001", "2x20GP")[0] == module.ROUTE_APPROVAL
    assert module._route_with_context("book it", None)[0] != module.ROUTE_APPROVAL


def test_approval_node_answers_with_the_approval_handlers_text(svc, monkeypatch):
    import agent as module
    from freight_exchange.asi.approvals import ApprovalReplyResult

    seen = {}

    async def fake_handle(store, buyer_id, text):
        seen.update(buyer=buyer_id, text=text)
        return ApprovalReplyResult(text="Approved: BK-1001 will be rebooked once the carrier confirms.",
                                   approval_id="apr_x", status="APPROVED")

    monkeypatch.setattr(module, "handle_approval_reply", fake_handle)
    monkeypatch.setattr(module, "TigerApprovalStore", lambda pool: object())
    svc[0].pool = None
    text = _invoke("approve BK-1001", agentverse_sender="agent1qbuyer", agentverse_session="s-appr")["messages"][-1].content
    assert text.startswith("Approved: BK-1001") and seen == {"buyer": "agent1qbuyer", "text": "approve BK-1001"}
