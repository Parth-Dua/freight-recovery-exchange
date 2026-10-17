import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import pytest
from agentverse_sdk.langgraph.content import extract_ai_content
from langgraph.graph.state import CompiledStateGraph

from freight_exchange.repository import Corridor, Port
from freight_exchange.quotes import QuoteRound

REF = re.compile(r"Ref: (req_[0-9a-f]{12})$")
AEJEA = Port("AEJEA", "Jebel Ali", ("jebel ali", "dubai"))
INBOM = Port("INBOM", "Mumbai", ("mumbai",))


class FakeRepo:
    async def ports(self):
        return [AEJEA, INBOM]

    async def corridors(self):
        return [Corridor(AEJEA, INBOM, ("20GP",), 3, 6, datetime.now(timezone.utc) + timedelta(days=1))]


class FakeQuotes:
    def __init__(self):
        self.calls = []

    async def run(self, cargo, *, buyer_id, request_id=None, correlation_id=None):
        self.calls.append((cargo, buyer_id, request_id))
        return QuoteRound(request_id, "flow_x", cargo, [])


@pytest.fixture
def services(monkeypatch):
    import agent as module

    quotes = FakeQuotes()

    async def fake_services():
        return FakeRepo(), quotes

    monkeypatch.setattr(module, "get_services", fake_services)
    return quotes


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


def test_corridors_come_from_repository_through_sdk_extraction(services):
    text = _sdk_text(_invoke("What corridors do you currently support?"))
    assert "Jebel Ali (AEJEA) → Mumbai (INBOM)" in text and "3 carrier agents" in text
    assert REF.search(text)


def test_quote_request_uses_chat_ref_as_tiger_request_id(services):
    state = _invoke("Quote 2x20GP from Jebel Ali to Mumbai, budget $5,600", agentverse_sender="agent1qbuyer")
    ref = REF.search(state["messages"][-1].content).group(1)
    cargo, buyer, request_id = services.calls[0]
    assert request_id == ref and buyer == "agent1qbuyer"
    assert (cargo.origin_port, cargo.destination_port, cargo.quantity, cargo.max_total_price_minor) == (
        "AEJEA", "INBOM", 2, 560000)


def test_quote_without_ports_asks_instead_of_calling_carriers(services):
    text = _invoke("quote 2 containers from shanghai to LA")["messages"][-1].content
    assert "origin port and destination port" in text and not services.calls


def test_past_deadline_is_rejected_before_any_carrier_call(services):
    text = _invoke("Quote 2x20GP from Jebel Ali to Mumbai by Jan 2 2020")["messages"][-1].content
    assert "couldn't send that to carriers" in text and not services.calls


def test_booking_still_declined_without_touching_services(services):
    text = _invoke("Can you book two containers tomorrow?")["messages"][-1].content
    assert "Booking is not active yet" in text and not services.calls


def test_threaded_follow_up_only_returns_new_turn(services):
    from agent import agent

    first = _invoke("What can you do?")
    second = asyncio.run(agent.ainvoke(
        {"messages": [*first["messages"], {"role": "human", "content": "Book a container"}]}))
    assert "Booking is not active yet" in _sdk_text(second)


def test_logs_correlate_request_id_without_message_text(caplog, services):
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
    assert request_id in line and '"route": "booking_not_implemented"' in line and '"status": "ok"' in line
    assert "duration_ms" in line and "agent1qexample" in line
    assert "test@example.com" not in line and "containers" not in line


def test_service_failure_returns_retry_safe_error(monkeypatch):
    import agent as module

    async def broken():
        raise RuntimeError("db password=hunter2")

    monkeypatch.setattr(module, "get_services", broken)
    text = _invoke("What corridors do you support?")["messages"][-1].content
    assert text.startswith(module.SERVICE_UNAVAILABLE_REPLY) and "hunter2" not in text


def test_asi_one_mention_reaches_quote_route(services):
    _invoke("@freight-recovery-exc Quote 2x20GP from Jebel Ali to Mumbai")
    cargo, _, _ = services.calls[0]
    assert (cargo.origin_port, cargo.destination_port, cargo.quantity) == ("AEJEA", "INBOM", 2)
