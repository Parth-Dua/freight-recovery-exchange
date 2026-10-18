"""Exchange Agent — BookingGraph (through offer selection), exported as ``agent`` for langgraph.json.

    start (dedupe + route) ─┬─ quote    : parse → clarify | quote round + one counter per carrier
                            ├─ requote  : re-run the session's latest request
                            ├─ select   : persist OFFER_SELECTED (not a hold, not a booking)
                            ├─ book     : hold → Stripe TEST checkout | verify payment → confirm | cancel
                            ├─ status   : withdrawn sailings + buyer bookings from Tiger (spec 05 WP6)
                            ├─ info     : corridors / capabilities / not-yet-active replies
                            └───────────┴─→ finish (store turn for dedupe, log, reply)

Deterministic (no LLM). Business state lives in Tiger keyed by request_id; the thread
state only carries a pending clarification. When ``AGENT_URI`` is set the Agentverse SDK
exposes the graph over the Agent Chat Protocol (mailbox mode).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

from freight_exchange import responses as r
from freight_exchange.asi.approvals import TigerApprovalStore, handle_approval_reply, is_approval_reply
from freight_exchange.booking import BookingService, claim_secret_from
from freight_exchange.booking_status import TigerBookingSeam
from freight_exchange.hold_client import AcpHoldClient
from freight_exchange.payments import StripeTestPayments
from freight_exchange.carrier_client import CarrierClient
from freight_exchange.config import carrier_timeout_seconds
from freight_exchange.observability import log_event, new_request_id
from freight_exchange.quotes import (
    InvalidCargoRequest, QuoteService, cargo_from_requirements, exchange_identity, to_cargo_spec, utcnow,
)
from freight_exchange.repository import Repository, get_repository
from freight_exchange.request_parser import parse_request
from freight_exchange.selection import resolve_choice

# langgraph-av imports this module before the CLI loads .env, so load it here.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ERROR_REPLY = "Sorry, I couldn't handle that request. It is safe to retry."
SERVICE_UNAVAILABLE_REPLY = "The quote service is temporarily unavailable. Nothing was booked; it is safe to retry."
DUPLICATE_WINDOW_S = 120
DUPLICATE_WAIT_S = 45
DATA_ROUTES = {r.ROUTE_CORRIDORS, r.ROUTE_QUOTES, r.ROUTE_REQUOTE, r.ROUTE_SELECT, r.ROUTE_RECOVERY,
               r.ROUTE_BOOKING, r.ROUTE_PAID, r.ROUTE_CANCEL, "recovery_approval"}
_BOOK_VERB = re.compile(r"\b(book|reserve)\b", re.IGNORECASE)
_ALLOWANCE = re.compile(r"\b(?:auto[- ]?rebook|rebook|recover\w*|extra)\b\D{0,40}?\$\s?(\d[\d,]*)", re.IGNORECASE)


def recovery_allowance_minor(text: str) -> int:
    """Buyer's opt-in recovery envelope from the booking message (D5); default 0 = always ask."""
    m = _ALLOWANCE.search(text)
    return int(m.group(1).replace(",", "")) * 100 if m else 0
_BARE_NUMBER = re.compile(r"^\s*#?\s*(\d{1,2})\s*$")


class ExchangeState(MessagesState):
    pending_text: str | None  # partial quote request waiting for a clarification answer
    turn: dict[str, Any]      # per-turn scratch (reset by `start`)


async def get_services() -> tuple[Repository, QuoteService]:
    """Dependency provider (tests monkeypatch this)."""
    repo = await get_repository()
    identity = exchange_identity(os.environ["AGENT_URI"])
    return repo, QuoteService(repo, CarrierClient(identity, timeout_s=carrier_timeout_seconds()))


async def get_booking_service() -> BookingService:
    """Real booking path (tests monkeypatch this): ACP holds + Stripe TEST checkout."""
    repo = await get_repository()
    key = os.environ["AGENT_URI"]
    holds = AcpHoldClient(exchange_identity(key), audit=repo.record_acp_message)
    from urllib.parse import urlparse

    return BookingService(repo, holds, StripeTestPayments(), claim_secret_from(urlparse(key).password or ""))


async def get_booking_seam() -> TigerBookingSeam:
    """Real BookingSeam read side (tests monkeypatch this)."""
    return TigerBookingSeam((await get_repository()).pool)


def _last_human_text(state: ExchangeState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return message.text
    return ""


ROUTE_APPROVAL = "recovery_approval"


def _route_with_context(text: str, pending: str | None) -> tuple[str, str]:
    """Return (route, text to parse). A pending clarification absorbs short follow-ups."""
    if is_approval_reply(text):  # first: "approve the rebooking" must never reach booking or recovery
        return ROUTE_APPROVAL, text
    route = r.classify(text)
    if pending and route in (r.ROUTE_UNSUPPORTED, r.ROUTE_QUOTES, r.ROUTE_CORRIDORS, r.ROUTE_SELECT):
        m = _BARE_NUMBER.match(text)
        if route != r.ROUTE_SELECT or m:
            addition = f"{m.group(1)} containers" if m else text
            return r.ROUTE_QUOTES, f"{pending} {addition}"
    return route, text


# ---- nodes ---------------------------------------------------------------------
async def start(state: ExchangeState, config: RunnableConfig) -> dict:
    metadata = config.get("metadata") or {}
    raw = _last_human_text(state)
    text = r.strip_mentions(raw)
    session = str(metadata.get("agentverse_session") or metadata.get("thread_id") or "local")
    turn = {
        "ref": new_request_id(), "raw_chars": len(raw), "text": text, "session": session,
        "buyer": metadata.get("agentverse_sender") or "local", "started": time.perf_counter(),
        "correlation": {"agentverse_sender": metadata.get("agentverse_sender"),
                        "agentverse_session": metadata.get("agentverse_session"),
                        "thread_id": metadata.get("thread_id")},
        "status": "ok", "turn_key": None, "duplicate_of": None,
    }
    route, parse_text = _route_with_context(text, state.get("pending_text"))
    turn.update(route=route, parse_text=parse_text)

    # ASI:One may deliver the same message twice: answer duplicates with the original reply.
    try:
        repo, _ = await get_services()
        key = hashlib.sha256(f"{session}|{' '.join(text.lower().split())}".encode()).hexdigest()
        existing = await repo.claim_turn(key, session, turn["ref"], DUPLICATE_WINDOW_S)
        if existing is None:
            turn["turn_key"] = key
        else:
            deadline = time.monotonic() + DUPLICATE_WAIT_S
            while existing and existing["status"] != "DONE" and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                existing = await repo.get_turn(key)
            turn.update(route="duplicate", duplicate_of=existing["request_ref"] if existing else None,
                        reply=existing.get("reply") if existing else None)
    except Exception as exc:  # dedupe is best-effort; never block a reply on it
        log_event("dedupe_unavailable", logging.WARNING, error_type=type(exc).__name__, request_id=turn["ref"])
    return {"turn": turn}


def _guarded(fn):
    async def node(state: ExchangeState) -> dict:
        turn = dict(state["turn"])
        try:
            update = await fn(state, turn)
        except Exception as exc:  # retry-safe error instead of crashing the ACP turn
            turn["status"] = "error"
            turn["body"] = SERVICE_UNAVAILABLE_REPLY if turn["route"] in DATA_ROUTES else ERROR_REPLY
            log_event("request_failed", logging.ERROR, error_type=type(exc).__name__, route=turn["route"],
                      request_id=turn["ref"], **turn["correlation"])
            update = {}
        return {"turn": turn, **update}

    node.__name__ = fn.__name__
    return node


@_guarded
async def quote(state: ExchangeState, turn: dict) -> dict:
    repo, quotes = await get_services()
    ports = await repo.ports()
    parsed = parse_request(turn["text"], ports, utcnow())
    if parsed.missing and turn["parse_text"] != turn["text"]:  # combine with the pending clarification
        parsed = parse_request(turn["parse_text"], ports, utcnow())
    corridors = await repo.corridors()
    if parsed.missing:
        turn["body"] = r.clarification_reply(parsed, corridors)
        return {"pending_text": turn["parse_text"]}
    lane = next((c for c in corridors if (c.origin.port_code, c.destination.port_code) ==
                 (parsed.origin.port_code, parsed.destination.port_code)), None)
    if lane is None:
        parsed.origin = parsed.destination = None
        turn["body"] = r.clarification_reply(parsed, corridors)
        return {"pending_text": None}
    if parsed.container_type not in lane.container_types:
        turn["body"] = r.container_not_offered_reply(parsed.container_type, lane)
        return {"pending_text": None}
    try:
        cargo = to_cargo_spec(parsed, utcnow())
    except InvalidCargoRequest as exc:
        turn["body"] = r.invalid_request_reply(str(exc))
        return {"pending_text": None}
    round_ = await quotes.run(cargo, buyer_id=turn["buyer"], request_id=turn["ref"], session_id=turn["session"])
    turn["body"] = r.quotes_reply(parsed, round_)
    return {"pending_text": None}


@_guarded
async def requote(state: ExchangeState, turn: dict) -> dict:
    repo, quotes = await get_services()
    latest = await repo.latest_request(turn["session"])
    if latest is None:
        turn["body"] = r.no_quotes_yet_reply()
        return {}
    cargo = cargo_from_requirements(latest["requirements"])
    if cargo.latest_arrival is not None and cargo.latest_arrival <= utcnow():
        turn["body"] = r.invalid_request_reply("the original arrival deadline has passed; send a new request")
        return {}
    round_ = await quotes.run(cargo, buyer_id=turn["buyer"], request_id=turn["ref"], session_id=turn["session"])
    turn["body"] = r.quotes_reply(cargo, round_)
    return {"pending_text": None}


@_guarded
async def select(state: ExchangeState, turn: dict) -> dict:
    repo, _ = await get_services()
    latest = await repo.latest_request(turn["session"])
    offers = await repo.presented_offers(latest["request_id"]) if latest else []
    if not offers:
        turn["body"] = r.no_quotes_yet_reply()
        return {}
    choice = resolve_choice(turn["text"], offers)
    if choice is None or choice.offer_id is None:
        turn["body"] = r.selection_problem_reply(choice.problem if choice else "say an option number", offers)
        return {}
    offer = next(o for o in offers if o["offer_id"] == choice.offer_id)
    result = await repo.select_offer(latest["request_id"], choice.offer_id, session_id=turn["session"],
                                     source=choice.source)
    if result["status"] in ("SELECTED", "ALREADY_SELECTED"):
        refreshed = next(o for o in await repo.presented_offers(latest["request_id"]) if o["offer_id"] == offer["offer_id"])
        turn["body"] = r.selected_reply(refreshed, latest["request_id"], already=result["status"] == "ALREADY_SELECTED")
        if _BOOK_VERB.search(turn["text"]):  # "book option 2": select, then go straight to the hold
            service = await get_booking_service()
            step = await service.start(session_id=turn["session"], buyer_id=turn["buyer"],
                                       max_additional_cost_minor=recovery_allowance_minor(turn["text"]))
            turn["body"] = r.booking_reply(step)
    elif result["status"] == "OFFER_EXPIRED":
        turn["body"] = r.offer_expired_reply(offer["display_rank"])
    else:
        turn["body"] = r.selection_problem_reply("that offer is no longer selectable", offers)
    return {}


@_guarded
async def book(state: ExchangeState, turn: dict) -> dict:
    service = await get_booking_service()
    if turn["route"] == r.ROUTE_PAID:
        step = await service.check_payment(session_id=turn["session"])
    elif turn["route"] == r.ROUTE_CANCEL:
        step = await service.cancel(session_id=turn["session"])
    else:
        step = await service.start(session_id=turn["session"], buyer_id=turn["buyer"],
                                   max_additional_cost_minor=recovery_allowance_minor(turn["text"]))
    turn["body"] = r.booking_reply(step)
    return {}


@_guarded
async def status(state: ExchangeState, turn: dict) -> dict:
    """Spec 05 WP6: disruption status in ASI:One, read from Tiger (recovery itself is Phase 6/7)."""
    seam = await get_booking_seam()
    turn["body"] = r.disruption_status_reply(await seam.recent_withdrawals(), await seam.bookings_for_buyer(turn["buyer"]))
    return {}


@_guarded
async def approval(state: ExchangeState, turn: dict) -> dict:
    """A buyer's approve/decline for an over-allowance replacement (Tisya's ApprovalSeam). The recovery
    worker confirms or releases the held space on its next pass."""
    repo, _ = await get_services()
    result = await handle_approval_reply(TigerApprovalStore(repo.pool), turn["buyer"], turn["text"])
    turn["body"] = result.text if result is not None else r.static_reply(r.ROUTE_UNSUPPORTED)
    return {}


@_guarded
async def info(state: ExchangeState, turn: dict) -> dict:
    if turn["route"] == r.ROUTE_CORRIDORS:
        repo, _ = await get_services()
        turn["body"] = r.corridors_reply(await repo.corridors())
    else:
        turn["body"] = r.static_reply(turn["route"])
    return {}


async def finish(state: ExchangeState) -> dict:
    turn = state["turn"]
    if turn["route"] == "duplicate":
        content = turn.get("reply") or (
            f"Still working on your previous identical message (Ref: {turn['duplicate_of']}); it is safe to retry.")
    else:
        content = f"{turn.get('body', ERROR_REPLY)}\n{r.STATUS_LINE}\nRef: {turn['ref']}"
        if turn.get("turn_key"):
            try:
                repo, _ = await get_services()
                await repo.complete_turn(turn["turn_key"], turn["route"], content)
            except Exception as exc:
                log_event("dedupe_unavailable", logging.WARNING, error_type=type(exc).__name__, request_id=turn["ref"])
    log_event(
        "request_handled", route=turn["route"], status=turn["status"], input_chars=turn["raw_chars"],
        duplicate_of=turn.get("duplicate_of"), request_id=turn["ref"],
        duration_ms=round((time.perf_counter() - turn["started"]) * 1000, 2), **turn["correlation"],
    )
    return {"messages": [AIMessage(content=content)]}


def _next(state: ExchangeState) -> str:
    route = state["turn"]["route"]
    return {"duplicate": "finish", r.ROUTE_QUOTES: "quote", r.ROUTE_REQUOTE: "requote",
            r.ROUTE_SELECT: "select", r.ROUTE_RECOVERY: "status", r.ROUTE_BOOKING: "book", r.ROUTE_PAID: "book",
            r.ROUTE_CANCEL: "book", ROUTE_APPROVAL: "approval"}.get(route, "info")


_builder = StateGraph(ExchangeState)
for _name, _node in (("start", start), ("quote", quote), ("requote", requote), ("select", select),
                     ("book", book), ("status", status), ("approval", approval), ("info", info),
                     ("finish", finish)):
    _builder.add_node(_name, _node)
_builder.add_edge(START, "start")
_builder.add_conditional_edges("start", _next, ["quote", "requote", "select", "book", "status", "approval", "info",
                                                 "finish"])
for _name in ("quote", "requote", "select", "book", "status", "approval", "info"):
    _builder.add_edge(_name, "finish")
_builder.add_edge("finish", END)
agent = _builder.compile()


def _init_agentverse() -> None:
    agent_uri = os.getenv("AGENT_URI", "").strip()
    if not agent_uri:
        log_event("agentverse_init_skipped", reason="AGENT_URI not set")
        return

    from agentverse_sdk.langgraph import init as agentverse_init

    from freight_exchange.profile import build_profile

    # Mailbox mode (default): the agent polls Agentverse, so no public tunnel is
    # needed. Decision 0001; see docs/decisions.
    mailbox = os.getenv("AGENTVERSE_MAILBOX", "true").strip().lower() not in ("0", "false", "no")

    # The SDK masks the key in its own logs; never log agent_uri here.
    agentverse_init(agent_uri, profile=build_profile(), mailbox=mailbox)
    log_event("agentverse_init_called", mailbox=mailbox)


_init_agentverse()
