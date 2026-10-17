"""Exchange Agent — LangGraph graph, exported as ``agent`` for langgraph.json.

One deterministic async node (no LLM): routes the chat turn, and for quotes
fans out REQUEST_OFFER to the carrier agents and persists everything in Tiger.
When ``AGENT_URI`` is set, the Agentverse SDK exposes the graph over the Agent
Chat Protocol (mailbox mode).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

from freight_exchange import responses as r
from freight_exchange.carrier_client import CarrierClient
from freight_exchange.config import carrier_timeout_seconds
from freight_exchange.observability import log_event, new_request_id
from freight_exchange.quotes import InvalidCargoRequest, QuoteService, exchange_identity, to_cargo_spec, utcnow
from freight_exchange.repository import Repository, get_repository
from freight_exchange.request_parser import parse_request

# langgraph-av imports this module before the CLI loads .env, so load it here.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ERROR_REPLY = "Sorry, I couldn't handle that request. It is safe to retry."
SERVICE_UNAVAILABLE_REPLY = "The quote service is temporarily unavailable. Nothing was booked; it is safe to retry."


async def get_services() -> tuple[Repository, QuoteService]:
    """Dependency provider (tests monkeypatch this)."""
    repo = await get_repository()
    identity = exchange_identity(os.environ["AGENT_URI"])
    return repo, QuoteService(repo, CarrierClient(identity, timeout_s=carrier_timeout_seconds()))


async def handle(text: str, request_id: str, buyer_id: str) -> tuple[str, str]:
    """Return (route, reply text) for one chat turn."""
    route = r.classify(text)
    if route not in (r.ROUTE_CORRIDORS, r.ROUTE_QUOTES):
        return route, r.static_reply(route)

    repo, quotes = await get_services()
    if route == r.ROUTE_CORRIDORS:
        return route, r.corridors_reply(await repo.corridors())

    parsed = parse_request(text, await repo.ports(), utcnow())
    if parsed.missing:
        return route, r.missing_info_reply(parsed, await repo.corridors())
    try:
        cargo = to_cargo_spec(parsed, utcnow())
    except InvalidCargoRequest as exc:
        return route, r.invalid_request_reply(str(exc))
    round_ = await quotes.run(cargo, buyer_id=buyer_id, request_id=request_id)
    return route, r.quotes_reply(parsed, round_.outcomes, request_id)


def _last_human_text(state: MessagesState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return message.text
    return ""


async def respond(state: MessagesState, config: RunnableConfig) -> dict:
    request_id = new_request_id()
    started = time.perf_counter()
    metadata = config.get("metadata") or {}
    correlation = {
        "request_id": request_id,
        "agentverse_sender": metadata.get("agentverse_sender"),
        "agentverse_session": metadata.get("agentverse_session"),
        "thread_id": metadata.get("thread_id"),
    }

    text, route = "", r.ROUTE_UNSUPPORTED
    try:
        text = _last_human_text(state)
        route = r.classify(text)
        route, body = await handle(text, request_id, metadata.get("agentverse_sender") or "local")
        status = "ok"
    except Exception as exc:  # retry-safe error instead of crashing the ACP turn
        status = "error"
        body = SERVICE_UNAVAILABLE_REPLY if route in (r.ROUTE_CORRIDORS, r.ROUTE_QUOTES) else ERROR_REPLY
        log_event("request_failed", logging.ERROR, error_type=type(exc).__name__, route=route, **correlation)

    log_event(
        "request_handled",
        route=route,
        status=status,
        input_chars=len(text),
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        **correlation,
    )
    return {"messages": [AIMessage(content=f"{body}\n{r.STATUS_LINE}\nRef: {request_id}")]}


_builder = StateGraph(MessagesState)
_builder.add_node("respond", respond)
_builder.add_edge(START, "respond")
_builder.add_edge("respond", END)
agent = _builder.compile()


def _init_agentverse() -> None:
    agent_uri = os.getenv("AGENT_URI", "").strip()
    if not agent_uri:
        log_event("agentverse_init_skipped", reason="AGENT_URI not set")
        return

    from agentverse_sdk.langgraph import init as agentverse_init

    from freight_exchange.profile import build_profile

    # Mailbox mode (default): the agent polls Agentverse, so no public tunnel is
    # needed. Decision 2026-10-17 after quick tunnels kept dying; see progress.md.
    mailbox = os.getenv("AGENTVERSE_MAILBOX", "true").strip().lower() not in ("0", "false", "no")

    # The SDK masks the key in its own logs; never log agent_uri here.
    agentverse_init(agent_uri, profile=build_profile(), mailbox=mailbox)
    log_event("agentverse_init_called", mailbox=mailbox)


_init_agentverse()
