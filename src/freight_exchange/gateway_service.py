"""Buyer intents arriving through the gateway uAgent: one place that turns chat/card input into business calls.

Returns protocol-neutral results for the gateway to serialize:
  {"kind": "CARD", "narration", "card_kind", "card_payload"} | {"kind": "TEXT", "text"}
  | {"kind": "PAYMENT_REQUEST", "payment": {...}} | {"kind": "PENDING", "text"}
Card actions come back as untrusted text (JSON on a direct @mention, prose via the planner) and are
validated here against server-side state; buyer identity only ever comes from the gateway transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from freight_exchange.gateway_store import ChatContext, GatewayStore
from freight_exchange.observability import log_event
from freight_exchange.repository import Repository, _event

FET_DECIMALS = 18  # 1 FET = 10**18 atestfet on the Fetch stable testnet
SHIP_INTENT = re.compile(
    r"^(start|new shipment|ship cargo|book cargo)$|\b(ship|send|move|book)\b.{0,40}\b(cargo|containers?|freight|shipment)\b"
    r"|\bi (have|need to ship) cargo\b|\b(freight|shipping|container) (quotes?|rates?)\b", re.IGNORECASE)
MENTION = re.compile(r"@[\w.-]+")
PAID = re.compile(r"(i\s*(have|'ve)\s+)?(just\s+)?(paid|payment\s+(done|made|complete|completed|sent)|done paying)[.!]*")


def fet_amount() -> Decimal:
    amount = Decimal(os.getenv("BOOKING_SERVICE_FET_AMOUNT", "0.1"))
    if amount <= 0:
        raise ValueError("BOOKING_SERVICE_FET_AMOUNT must be positive")
    return amount


def to_atestfet(amount: Decimal) -> int:
    units = amount * (Decimal(10) ** FET_DECIMALS)
    if units != units.to_integral_value():
        raise ValueError("FET amount has more than 18 decimals")
    return int(units)


def parse_action(text: str) -> dict | None:
    """A card selection: {"action": ..., plus form values / IDs}. None for ordinary chat text."""
    cleaned = MENTION.sub("", text).strip()
    start = cleaned.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(cleaned[start:cleaned.rfind("}") + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    flat: dict = {}
    for key in ("values", "fields", "form", "data"):
        if isinstance(data.get(key), dict):
            flat.update(data[key])
    selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
    flat.update({k: v for k, v in data.items() if not isinstance(v, dict)})
    flat.update(selection)
    return flat if flat.get("action") else None


def protocol_result(verdict: dict) -> dict:
    """A payment verdict as a chat result the gateway can deliver (CompletePayment/CancelPayment + followups)."""
    action = verdict.get("action")
    kind = {"complete": "PAYMENT_COMPLETE", "cancel": "PAYMENT_CANCEL"}.get(action, "BUNDLE")
    return {"kind": kind, "transaction_id": verdict.get("transaction_id"), "reason": verdict.get("reason", ""),
            "followups": verdict.get("followups") or []}


def card(narration: str, kind: str, payload: dict, *, terminal: bool = False, fallback_text: str = "") -> dict:
    return {"kind": "CARD", "narration": narration, "card_kind": kind, "card_payload": payload,
            "terminal": terminal, "fallback_text": fallback_text or narration}


def text(message: str) -> dict:
    return {"kind": "TEXT", "text": message}


class FormError(ValueError):
    pass


def _bool(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "on", "yes", "1")


def _minor(value, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        raise FormError(f"{field} must be a number")
    if amount < 0:
        raise FormError(f"{field} cannot be negative")
    return int(amount * 100)


def _when(value, field: str, required: bool) -> datetime | None:
    if value in (None, ""):
        if required:
            raise FormError(f"{field} is required")
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise FormError(f"{field} must be a date from the list")
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


IDS = {"offer_id": re.compile(r"\b(off_[a-z]+_[0-9a-f]{6,})\b"), "approval_id": re.compile(r"\b(apr_rec_[0-9a-f]+_BK-[\w-]+)\b"),
       "request_id": re.compile(r"\b(req_[0-9a-f]{6,})\b"), "session_id": re.compile(r"\b(neg_[0-9a-f]{16})\b")}


class GatewayService:
    def __init__(self, repo: Repository, store: GatewayStore | None = None, legacy=None, quotes=None, booking=None,
                 approvals=None):
        self.repo = repo
        self.store = store or GatewayStore(repo.pool)
        self._legacy = legacy  # callable(sender, session, text) -> str; the existing LangGraph booking graph
        self._quotes, self._booking, self._approvals = quotes, booking, approvals
        self._tasks: set[asyncio.Task] = set()

    # ---- dependencies (tests inject fakes) ----------------------------------------------
    def quotes(self):
        if self._quotes is None:
            from freight_exchange.carrier_client import CarrierClient
            from freight_exchange.config import carrier_timeout_seconds
            from freight_exchange.quotes import QuoteService, exchange_identity
            self._quotes = QuoteService(self.repo, CarrierClient(exchange_identity(os.environ["AGENT_URI"]),
                                                                 timeout_s=carrier_timeout_seconds()))
        return self._quotes

    def booking(self):
        if self._booking is None:
            from urllib.parse import urlparse

            from freight_exchange.booking import claim_secret_from
            from freight_exchange.fet_booking import FetBookingService
            from freight_exchange.hold_client import AcpHoldClient
            from freight_exchange.quotes import exchange_identity
            key = os.environ["AGENT_URI"]
            self._booking = FetBookingService(self.repo, AcpHoldClient(exchange_identity(key), audit=self.repo.record_acp_message),
                                              claim_secret_from(urlparse(key).password or ""))
        return self._booking

    def approvals(self):
        if self._approvals is None:
            from freight_exchange.asi.approvals import TigerApprovalStore
            self._approvals = TigerApprovalStore(self.repo.pool)
        return self._approvals

    # ---- entry ----------------------------------------------------------------------
    async def handle(self, ctx: ChatContext) -> dict:
        await self.store.bind(ctx)
        prior = await self.store.claim_event(ctx)
        if prior is not None:
            return prior
        try:
            result = await self._route(ctx)
        except Exception as exc:
            log_event("gateway_intent_failed", logging.ERROR, error_type=type(exc).__name__, event_id=ctx.event_id)
            result = text("Something went wrong on our side; nothing was booked or charged. Please try again.")
        await self.store.finish_event(ctx.event_id, result)
        log_event("gateway_intent", event_id=ctx.event_id, kind=result.get("kind"),
                  card_kind=result.get("card_kind"), sender=ctx.sender, session=ctx.session)
        return result

    async def _route(self, ctx: ChatContext) -> dict:
        action = parse_action(ctx.text)
        if action is not None:
            return await self.on_action(ctx, action)
        plain = MENTION.sub("", ctx.text).strip()
        lowered = plain.lower()
        if SHIP_INTENT.search(plain):  # incl. detailed asks routed by ASI:One: the form collects them precisely
            return await self.cargo_form(ctx)
        if PAID.fullmatch(lowered):
            return await self.check_paid(ctx)
        if lowered in ("payment test", "test payment"):
            return await self.payment_smoke(ctx)
        if lowered in ("notify test", "test notification"):
            return await self.notify_smoke(ctx)
        narrated = await self.narrated_action(ctx, plain)
        if narrated is not None:
            return await self.on_action(ctx, narrated, from_prose=True)
        return text(await self.legacy(ctx, plain))

    async def narrated_action(self, ctx: ChatContext, plain: str) -> dict | None:
        """Planner-mediated clicks arrive as prose that names the IDs; never guess between several options."""
        lowered = plain.lower()
        offer = IDS["offer_id"].search(plain)
        session = IDS["session_id"].search(plain)
        if re.search(r"\b(negotiat\w*|transcript)\b", lowered) and not re.search(r"\brecover", lowered):
            if session:
                return {"action": "view_negotiation", "session_id": session.group(1)}
            if offer:
                return {"action": "view_negotiation", "offer_id": offer.group(1)}
            named = re.search(r"\b(atlas|beacon|cove|meridian|dhow)\b", lowered)
            if named:
                return {"action": "view_negotiation", "carrier": named.group(1)}
        if re.fullmatch(r"(go )?back( to (the )?offers)?[.!]?", lowered):
            return {"action": "back_to_offers"}
        if re.search(r"\brecovery negotiation", lowered):
            return {"action": "view_recovery_negotiations"}
        if offer and re.search(r"\b(select|choose|chose|pick|picked|selected|want|take)\b", lowered):
            return {"action": "select_offer", "offer_id": offer.group(1)}
        if re.fullmatch(r"(yes,? )?(confirm|confirm and pay|pay|book it|confirm booking)[.!]?", lowered):
            return {"action": "confirm_booking"}
        if re.fullmatch(r"(cancel|cancel booking|no,? cancel)[.!]?", lowered):
            return {"action": "cancel_booking"}
        return None

    # ---- cards ----------------------------------------------------------------------
    async def cargo_form(self, ctx: ChatContext, error: str = "") -> dict:
        from freight_exchange.cards import cargo_setup_form
        corridors = await self.repo.corridors()
        async with self.repo.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT departure_at, arrival_at FROM sailings WHERE status = 'ACTIVE' AND departure_at > now() "
                "ORDER BY arrival_at")).fetchall()
        card_id = await self.new_card(ctx, "form", "cargo_form", None)
        return cargo_setup_form(
            origins=sorted({(c.origin.port_code, c.origin.name) for c in corridors}),
            destinations=sorted({(c.destination.port_code, c.destination.name) for c in corridors}),
            arrival_options=[r[1] for r in rows], departure_options=[r[0] for r in rows],
            card_instance_id=card_id, error=error)

    async def new_card(self, ctx_or_buyer, kind: str, purpose: str, resource_id: str | None,
                       session: str | None = None) -> str:
        buyer = ctx_or_buyer.buyer_id if isinstance(ctx_or_buyer, ChatContext) else ctx_or_buyer
        session = session or (ctx_or_buyer.session if isinstance(ctx_or_buyer, ChatContext) else "")
        card_id = f"card_{uuid4().hex[:16]}"
        async with self.repo.pool.connection() as conn:
            await conn.execute(
                """UPDATE card_instances SET status = 'SUPERSEDED' WHERE buyer_id = %s AND purpose = %s
                   AND status = 'ACTIVE' AND (%s::text IS NULL OR resource_id = %s)""",
                (buyer, purpose, resource_id, resource_id))
            await conn.execute(
                "INSERT INTO card_instances (card_instance_id, buyer_id, chat_session_id, card_kind, purpose, resource_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)", (card_id, buyer, session, kind, purpose, resource_id))
        return card_id

    async def use_card(self, ctx: ChatContext, action: dict, purpose: str) -> tuple[str | None, str | None]:
        """Spend one ACTIVE card of this buyer. Returns (card_instance_id, resource_id) or (None, reason).
        Without an ID (planner prose) only the single current card of that purpose can be used."""
        async with self.repo.pool.connection() as conn:
            if action.get("card_instance_id"):
                row = await (await conn.execute(
                    """UPDATE card_instances SET status = 'USED', used_at = now()
                       WHERE card_instance_id = %s AND buyer_id = %s AND purpose = %s AND status = 'ACTIVE'
                       RETURNING card_instance_id, resource_id""",
                    (action["card_instance_id"], ctx.buyer_id, purpose))).fetchone()
            else:
                row = await (await conn.execute(
                    """UPDATE card_instances SET status = 'USED', used_at = now() WHERE card_instance_id = (
                           SELECT card_instance_id FROM card_instances WHERE buyer_id = %s AND purpose = %s
                           AND status = 'ACTIVE' ORDER BY created_at DESC LIMIT 1)
                       RETURNING card_instance_id, resource_id""", (ctx.buyer_id, purpose))).fetchone()
        return (row[0], row[1]) if row else (None, "That card is no longer current (already used, replaced or not "
                                                   "yours), so nothing was changed.")

    async def on_action(self, ctx: ChatContext, action: dict, *, from_prose: bool = False) -> dict:
        name = action.get("action")
        log_event("gateway_card_action", action=name, sender=ctx.sender, session=ctx.session, prose=from_prose)
        handler = {"submit_cargo_request": self.submit_cargo_request, "select_offer": self.select_offer,
                   "confirm_booking": self.confirm_booking, "cancel_booking": self.cancel_booking,
                   "approve_recovery": self.decide_recovery, "decline_recovery": self.decide_recovery,
                   "view_negotiation": self.view_negotiation, "back_to_offers": self.back_to_offers,
                   "view_recovery_negotiations": self.view_recovery_negotiations}.get(name)
        if handler is None:
            return text("That card has expired or isn't one I recognise; nothing was changed.")
        return await handler(ctx, action)

    # ---- request -> negotiation -> offers ------------------------------------------------
    def validate_form(self, form: dict, ports: set[str]) -> dict:
        origin, destination = str(form.get("origin_port", "")).upper(), str(form.get("destination_port", "")).upper()
        if origin not in ports or destination not in ports:
            raise FormError("choose an origin and destination from the list")
        if origin == destination:
            raise FormError("origin and destination must differ")
        try:
            quantity = int(Decimal(str(form.get("quantity", "")).strip()))
        except (InvalidOperation, ValueError):
            raise FormError("quantity must be a whole number")
        if not 1 <= quantity <= 10:
            raise FormError("quantity must be between 1 and 10 containers")
        latest = _when(form.get("latest_arrival"), "arrival deadline", True)
        earliest = _when(form.get("earliest_departure"), "ready date", False)
        if latest <= datetime.now(timezone.utc):
            raise FormError("the arrival deadline must be in the future")
        if earliest and earliest >= latest:
            raise FormError("the ready date must be before the arrival deadline")
        cargo_class = str(form.get("cargo_class") or "general")
        if cargo_class not in ("general", "high_value"):
            raise FormError("cargo class must be general or high value")
        priority = str(form.get("priority") or "balanced")
        if priority not in ("cheapest", "balanced", "fastest"):
            raise FormError("priority must be cheapest, balanced or fastest")
        return {"origin_port": origin, "destination_port": destination, "quantity": quantity, "latest_arrival": latest,
                "earliest_departure": earliest, "allow_split": _bool(form.get("allow_split")),
                "cargo_class": cargo_class, "priority": priority, "must_deliver": _bool(form.get("must_deliver")),
                "max_total_price_minor": _minor(form.get("max_total_price"), "max price"),
                "recovery_allowance_minor": _minor(form.get("recovery_allowance"), "auto-rebook limit") or 0}

    async def submit_cargo_request(self, ctx: ChatContext, action: dict) -> dict:
        card_id, _ = await self.use_card(ctx, action, "cargo_form")
        if card_id is None:
            return await self.cargo_form(ctx, error="That form was already submitted; here is a fresh one.")
        ports = {p.port_code for p in await self.repo.ports()}
        try:
            form = self.validate_form(action, ports)
        except FormError as exc:
            return await self.cargo_form(ctx, error=f"Please fix: {exc}.")
        request_id = f"req_{uuid4().hex[:12]}"
        task = asyncio.create_task(self._negotiate_and_offer(ctx, request_id, form))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return text(f"Request {request_id}: {form['quantity']}×20GP {form['origin_port']} → {form['destination_port']}. "
                    "Your buyer agent is negotiating with the eligible carrier agents now; the final offers will "
                    "appear here shortly.")

    async def _negotiate_and_offer(self, ctx: ChatContext, request_id: str, form: dict) -> None:
        try:
            result = await self.negotiate(ctx, request_id, form)
        except Exception as exc:
            log_event("negotiation_failed", logging.ERROR, request_id=request_id, error_type=type(exc).__name__)
            result = text(f"I couldn't finish the negotiation for {request_id} (a carrier or our database was "
                          "unavailable). Nothing was reserved; say 'ship cargo' to try again.")
        await self.store.enqueue(event_key=f"offers:{request_id}", buyer_id=ctx.buyer_id, recipient=ctx.sender,
                                 session=ctx.session, gateway=ctx.gateway, result=result)

    async def negotiate(self, ctx: ChatContext, request_id: str, form: dict) -> dict:
        if os.getenv("TWO_SIDED_NEGOTIATION_ENABLED", "true").lower() != "false":
            return await self.negotiate_two_sided(ctx, request_id, form)
        return await self.negotiate_legacy(ctx, request_id, form)

    async def negotiate_two_sided(self, ctx: ChatContext, request_id: str, form: dict) -> dict:
        """v2: one durable session per eligible carrier, up to two private buyer counters each."""
        from freight_contracts import CargoSpec

        from freight_exchange.cards import final_offers_carousel
        from freight_exchange.negotiation_sessions import BuyerPrivate, NegotiationService
        cargo = CargoSpec(origin_port=form["origin_port"], destination_port=form["destination_port"],
                          container_type="20GP", quantity=form["quantity"], earliest_departure=form["earliest_departure"],
                          latest_arrival=form["latest_arrival"], max_total_price_minor=None, currency="USD",
                          allow_split=form["allow_split"], cargo_class=form["cargo_class"])
        private = BuyerPrivate(priority=form["priority"], max_total_minor=form["max_total_price_minor"],
                               must_deliver=form["must_deliver"], latest_arrival=form["latest_arrival"])
        quotes = self.quotes()
        async def progress(*, quoted: int, contacted: int) -> None:
            if quoted and os.getenv("NEGOTIATION_PROGRESS_ENABLED", "true").lower() != "false":
                await self.store.enqueue(
                    event_key=f"progress:{request_id}:openings", buyer_id=ctx.buyer_id, recipient=ctx.sender,
                    session=ctx.session, gateway=ctx.gateway,
                    result=text(f"{quoted} of {contacted} carriers quoted. Your agent is countering now; final "
                                "offers in a few seconds."))

        result = await NegotiationService(self.repo, quotes.client).run(
            buyer_id=ctx.buyer_id, chat_session=ctx.session, request_id=request_id, cargo=cargo, private=private,
            extra={"recovery_allowance_minor": form["recovery_allowance_minor"], "gateway": ctx.gateway},
            on_openings=progress)
        if not result.finals:
            why = ("no carrier has a sailing with space on that lane by your deadline" if not result.paired
                   else "no carrier offer fit your constraints")
            return text(f"Negotiation finished for {request_id}: {why}. Nothing was reserved; say 'ship cargo' to try "
                        "different dates.")
        best = min(s.current.total_price_minor for s in result.finals)
        offers = [{"offer_id": s.current.offer_id, "carrier_name": s.carrier.display_name,
                   "sailing_id": s.current.sailing_id, "quantity": s.current.quantity,
                   "total_price_minor": s.current.total_price_minor, "departure_at": s.current.departure_at,
                   "arrival_at": s.current.arrival_at, "expires_at": s.current.expires_at,
                   "best": s.current.total_price_minor == best, "negotiated": s.round > 0,
                   "opening_minor": s.opening_minor if s.opening_comparable else None,
                   "counters": s.round} for s in result.finals]
        card_id = await self.new_card(ctx, "carousel", "offer_carousel", request_id)
        sessions = len(result.sessions)
        carousel = final_offers_carousel(
            request_id=request_id, offers=offers, card_instance_id=card_id,
            header=(f"Your buyer agent negotiated with {sessions} carrier{'s' if sessions != 1 else ''} in parallel "
                    f"and kept your limits private. {len(offers)} final offer{'s' if len(offers) != 1 else ''}, "
                    "ranked for your priority."))
        return await self.prerender_transcripts(ctx, request_id, carousel, card_id, offers)

    # ---- negotiation transparency (UX-NEG-001) ---------------------------------------------
    async def prerender_transcripts(self, ctx: ChatContext, request_id: str, carousel: dict, carousel_card_id: str,
                                    offers: list[dict]) -> dict:
        """Render every carrier's transcript now, while the buyer reads the offers, so opening one is instant.
        Never blocks the offers: any failure here just sends the carousel alone."""
        from freight_exchange import negotiation_trace as nt
        from freight_exchange.cards import negotiation_detail_card, negotiation_index_card
        if not nt.enabled():
            return carousel
        try:
            traces = await nt.request_traces(self.repo.pool, buyer_id=ctx.buyer_id, request_id=request_id)
            if not traces:
                return carousel
            view_id = await self.new_card(ctx, "detail", "negotiation_view", request_id)
            shown = {o["offer_id"] for o in offers}
            for t in traces:
                card_result = negotiation_detail_card(t, request_id=request_id, view_card_id=view_id,
                                                      carousel_card_id=carousel_card_id if t.offer_id in shown else None)
                await nt.save_card(self.repo.pool, trace_key=f"offer:{request_id}:{t.session_id}", buyer_id=ctx.buyer_id,
                                   kind="offer", request_id=request_id, offer_id=t.offer_id, session_id=t.session_id,
                                   card=card_result, trace=t.as_dict())
            await nt.save_card(self.repo.pool, trace_key=f"carousel:{request_id}", buyer_id=ctx.buyer_id,
                               kind="carousel", request_id=request_id, card=carousel,
                               trace={"carousel_card_id": carousel_card_id,
                                      "expires": {o["offer_id"]: o["expires_at"].isoformat() for o in offers}})
            index = negotiation_index_card(traces, request_id=request_id, view_card_id=view_id)
            log_event("negotiation_transcripts_ready", request_id=request_id, transcripts=len(traces))
            return {"kind": "BUNDLE", "followups": [index, carousel]}  # the offers stay the last, actionable card
        except Exception as exc:
            log_event("negotiation_transcripts_failed", logging.WARNING, request_id=request_id,
                      error_type=type(exc).__name__)
            return carousel

    async def _view_card(self, ctx: ChatContext, action: dict, purpose: str) -> str | None:
        """Read-only ownership check (never spends the card): the resource this buyer's view card is about."""
        async with self.repo.pool.connection() as conn:
            if action.get("card_instance_id"):
                row = await (await conn.execute(
                    "SELECT resource_id FROM card_instances WHERE card_instance_id = %s AND buyer_id = %s "
                    "AND purpose = %s", (action["card_instance_id"], ctx.buyer_id, purpose))).fetchone()
            else:
                row = await (await conn.execute(
                    "SELECT resource_id FROM card_instances WHERE buyer_id = %s AND purpose = %s "
                    "ORDER BY created_at DESC LIMIT 1", (ctx.buyer_id, purpose))).fetchone()
        return row[0] if row else None

    async def view_negotiation(self, ctx: ChatContext, action: dict) -> dict:
        """Read-only: serve the pre-rendered transcript (one owner-scoped query); zero writes, zero agent sends."""
        from freight_exchange import negotiation_trace as nt
        stored = await nt.load_offer_views(self.repo.pool, buyer_id=ctx.buyer_id,
                                           view_card_id=action.get("card_instance_id"))
        if not stored or (action.get("request_id") and action["request_id"] != stored[0]["request_id"]):
            return text("I don't have a negotiation transcript for that. Your offers card is above.")
        wanted = [c for c in stored if (action.get("session_id") and c["session_id"] == action["session_id"])
                  or (action.get("offer_id") and c["offer_id"] == action["offer_id"])]
        if not wanted and action.get("carrier"):
            name = str(action["carrier"]).lower()
            wanted = [c for c in stored if c["carrier"].lower().startswith(name) or c["carrier_id"] == name]
        if len(wanted) != 1:
            names = ", ".join(c["carrier"].split()[0] for c in stored)
            return text(f"Which carrier's negotiation? I have transcripts for: {names}.")
        row, card_result = wanted[0], wanted[0]["card"]
        selection_cta = next((c for c in card_result["card_payload"]["ctas"]
                              if c["selection"]["action"] == "select_offer"), None)
        if selection_cta is not None:
            why = {"SUPERSEDED": "These offers were replaced by a newer request",
                   "USED": "You've already chosen an offer for this request"}.get(row["carousel_status"])
            if why is None and not row["offer_live"]:
                why = "This offer has expired"
            if why:  # history stays viewable; choosing from here closes with the offer (select re-checks anyway)
                payload = dict(card_result["card_payload"])
                payload["ctas"] = [c for c in payload["ctas"] if c is not selection_cta]
                payload["summary_rows"] = payload["summary_rows"] + [{"label": "Choosing", "value": why}]
                card_result = {**card_result, "card_payload": payload}
        return card_result

    async def back_to_offers(self, ctx: ChatContext, action: dict) -> dict:
        from freight_exchange import negotiation_trace as nt
        request_id = await self._view_card(ctx, action, "negotiation_view")
        if request_id is None or (action.get("request_id") and action["request_id"] != request_id):
            return text("I don't have offers for that. Say 'ship cargo' to get quotes.")
        stored = await nt.load_cards(self.repo.pool, buyer_id=ctx.buyer_id, kind="carousel", request_id=request_id)
        if not stored:
            return text("Your offers card is above.")
        meta, carousel = stored[0]["trace"], stored[0]["card"]
        async with self.repo.pool.connection() as conn:
            status = await (await conn.execute("SELECT status FROM card_instances WHERE card_instance_id = %s",
                                               (meta["carousel_card_id"],))).fetchone()
        if status is None or status[0] == "SUPERSEDED":
            return text("These offers were replaced by a newer request; its offers card is above.")
        if status[0] == "USED":
            return text("You've already chosen an offer for this request; continue from the review card above.")
        now = datetime.now(timezone.utc)
        live = {oid for oid, exp in meta["expires"].items() if datetime.fromisoformat(exp) > now}
        items = [i for i in carousel["card_payload"]["items"] if i["id"] in live]
        if not items:
            return text("These offers have expired. Say 'ship cargo' for fresh quotes; nothing was reserved.")
        return {**carousel, "card_payload": {**carousel["card_payload"], "items": items}}

    async def view_recovery_negotiations(self, ctx: ChatContext, action: dict) -> dict:
        from freight_exchange import negotiation_trace as nt
        resource = await self._view_card(ctx, action, "recovery_trace")
        if resource is None:
            return text("I don't have a recovery negotiation for that.")
        recovery_id, _, booking_id = resource.partition(":")
        if (action.get("booking_id") and action["booking_id"] != booking_id) or \
                (action.get("recovery_id") and action["recovery_id"] != recovery_id):
            return text("I don't have a recovery negotiation for that.")
        stored = await nt.load_cards(self.repo.pool, buyer_id=ctx.buyer_id, kind="recovery", recovery_id=recovery_id,
                                     booking_id=booking_id)
        return stored[0]["card"] if stored else text("That recovery negotiation isn't available yet.")

    async def negotiate_legacy(self, ctx: ChatContext, request_id: str, form: dict) -> dict:
        """Rollback path: the Part 1 quote round (one counter per carrier)."""
        from freight_contracts import CargoSpec

        from freight_exchange.cards import final_offers_carousel
        cargo = CargoSpec(origin_port=form["origin_port"], destination_port=form["destination_port"],
                          container_type="20GP", quantity=form["quantity"],
                          earliest_departure=form["earliest_departure"], latest_arrival=form["latest_arrival"],
                          max_total_price_minor=None,  # the buyer's private cap never goes to carriers
                          currency="USD", allow_split=form["allow_split"])
        rnd = await self.quotes().run(cargo, buyer_id=ctx.buyer_id, request_id=request_id, session_id=ctx.session)
        await self.save_private(request_id, ctx, form)
        cap = form["max_total_price_minor"]
        finals = [r for r in rnd.ranked if cap is None or r.final_offer.total_price_minor <= cap]
        if not finals:
            why = " within your private max price" if rnd.ranked and cap is not None else ""
            return text(f"No carrier could offer {form['quantity']}×20GP by your deadline{why} ({request_id}). "
                        "Nothing was reserved. Say 'ship cargo' to try different dates.")
        best = min(r.final_offer.total_price_minor for r in finals)
        offers = []
        for r in finals:
            f = r.final_offer
            negotiated = r.counter is not None and r.counter.new_offer is not None
            offers.append({"offer_id": f.offer_id, "carrier_name": r.carrier.display_name, "sailing_id": f.sailing_id,
                           "quantity": f.quantity, "total_price_minor": f.total_price_minor,
                           "departure_at": f.departure_at, "arrival_at": f.arrival_at, "expires_at": f.expires_at,
                           "best": f.total_price_minor == best, "negotiated": negotiated,
                           "opening_minor": r.opening.offer.total_price_minor, "counters": 1 if negotiated else 0})
        card_id = await self.new_card(ctx, "carousel", "offer_carousel", request_id)
        return final_offers_carousel(request_id=request_id, offers=offers, card_instance_id=card_id,
                                     header=f"{len(offers)} final offer(s) for {request_id}. Choose one to review.")

    async def save_private(self, request_id: str, ctx: ChatContext, form: dict) -> None:
        async with self.repo.pool.connection() as conn:
            await conn.execute(
                """UPDATE cargo_requests SET cargo_class = %s, priority = %s, must_deliver = %s,
                       recovery_allowance_minor = %s, max_total_price_minor = %s, gateway_agent_address = %s
                   WHERE request_id = %s AND buyer_id = %s""",
                (form["cargo_class"], form["priority"], form["must_deliver"], form["recovery_allowance_minor"],
                 form["max_total_price_minor"], ctx.gateway, request_id, ctx.buyer_id))

    async def select_offer(self, ctx: ChatContext, action: dict) -> dict:
        from freight_exchange.cards import booking_review
        card_id, request_id = await self.use_card(ctx, action, "offer_carousel")
        if card_id is None:
            return text(request_id)
        offer_id = str(action.get("offer_id") or "")
        if action.get("request_id") and action["request_id"] != request_id:
            return text("That offer belongs to a different request; nothing was changed.")
        presented = {o["offer_id"] for o in await self.repo.presented_offers(request_id)}
        if offer_id not in presented:
            await self._reactivate(card_id)
            return text("I can't match that to one of the offers I showed you. Please pick from the latest offers card.")
        outcome = await self.repo.select_offer(request_id, offer_id, session_id=ctx.session, source="card")
        if outcome["status"] == "OFFER_EXPIRED":
            return text("That offer has expired. Say 'ship cargo' to get fresh offers; nothing was reserved.")
        if outcome["status"] not in ("SELECTED", "ALREADY_SELECTED"):
            return text("That offer can't be selected any more; nothing was reserved.")
        offer = await self.repo.selected_offer(request_id)
        review_id = await self.new_card(ctx, "review", "booking_review", request_id)
        from freight_exchange.fet_booking import payment_mode
        from freight_exchange.payments import booking_fee_minor
        mode = payment_mode()
        return booking_review(request_id=request_id, offer=offer, card_instance_id=review_id,
                              fee_fet=fet_amount() if mode == "fet_testnet" else None,
                              stripe_fee_minor=booking_fee_minor() if mode == "stripe_test" else None)

    async def _reactivate(self, card_id: str) -> None:
        async with self.repo.pool.connection() as conn:
            await conn.execute("UPDATE card_instances SET status = 'ACTIVE', used_at = NULL WHERE card_instance_id = %s",
                               (card_id,))

    async def confirm_booking(self, ctx: ChatContext, action: dict) -> dict:
        card_id, request_id = await self.use_card(ctx, action, "booking_review")
        if card_id is None:
            return text(request_id)
        offer = await self.repo.selected_offer(request_id)
        if offer is None or (action.get("offer_id") and action["offer_id"] != offer["offer_id"]):
            return text("That review no longer matches your selected offer; nothing was reserved.")
        return await self.booking().start(buyer_id=ctx.buyer_id, session_id=ctx.session, request_id=request_id,
                                          offer_id=offer["offer_id"], gateway_wallet=ctx.gateway_wallet)

    async def cancel_booking(self, ctx: ChatContext, action: dict) -> dict:
        card_id, request_id = await self.use_card(ctx, action, "booking_review")
        if card_id is None:
            return text(request_id)
        return text(f"Cancelled. Nothing was reserved or charged for {request_id}. Say 'ship cargo' to start again.")

    # ---- recovery approval cards ---------------------------------------------------------
    async def decide_recovery(self, ctx: ChatContext, action: dict) -> dict:
        from freight_exchange.asi import messages as m
        card_id, approval_id = await self.use_card(ctx, action, "recovery_approval")
        if card_id is None:
            return text(approval_id)
        if action.get("approval_id") and action["approval_id"] != approval_id:
            return text("That approval card doesn't match the replacement waiting for you; nothing was changed.")
        store = self.approvals()
        record = await store.find(approval_id)
        if record is None or record.buyer_id != ctx.buyer_id:
            return text("I can't find a replacement waiting for your approval there; nothing was changed.")
        status = record.status(datetime.now(timezone.utc))
        if status != "PENDING":
            return text(m.approval_expired(record.booking_id) if status == "EXPIRED"
                        else m.approval_already(record.booking_id, status))
        decision = "APPROVED" if action["action"] == "approve_recovery" else "DECLINED"
        final = await store.record_decision(approval_id, decision, ctx.buyer_id)
        if final != decision:
            return text(m.approval_already(record.booking_id, final))
        return text(m.approval_approved(record.booking_id, record.plan) if decision == "APPROVED"
                    else m.approval_declined(record.booking_id))

    # ---- legacy booking graph (text fallback) ----------------------------------------
    async def legacy(self, ctx: ChatContext, message: str) -> str:
        if self._legacy is None:
            from freight_exchange.legacy_graph import run_legacy_graph
            self._legacy = run_legacy_graph
        return await self._legacy(ctx.sender, ctx.session, message)

    # ---- phase 1 protocol smoke tests -------------------------------------------------
    async def payment_smoke(self, ctx: ChatContext) -> dict:
        """A real RequestPayment for 0.1 testnet FET that is never completed (protocol check only)."""
        amount, reference = fet_amount(), f"smoke_{uuid4().hex[:16]}"
        async with self.repo.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO payment_records (payment_id, provider, reference, purpose, amount_minor, currency, status,
                       method, network, funds_amount, funds_currency, sender_agent_address, chat_session_id,
                       gateway_wallet, deadline_at)
                   VALUES (%s, 'fetch_smoke', %s, 'gateway_smoke', %s, 'FET', 'REQUESTED', 'fet_direct', 'stable-testnet',
                           %s, 'FET', %s, %s, %s, now() + interval '5 minutes')""",
                (reference, reference, to_atestfet(amount), str(amount), ctx.sender, ctx.session, ctx.gateway_wallet))
            await _event(conn, "PAYMENT_REQUESTED", "payment", reference, correlation_id=reference,
                         payload={"amount": str(amount), "currency": "FET", "purpose": "gateway_smoke"})
        return {"kind": "PAYMENT_REQUEST", "narration": "Protocol smoke test: this payment request is never "
                "completed and books nothing. Please reject it.",
                "payment": {"reference": reference, "amount": str(amount), "currency": "FET", "method": "fet_direct",
                            "recipient": ctx.gateway_wallet, "deadline_seconds": 300,
                            "description": "Hormuz Exchange gateway smoke test (testnet FET); books nothing.",
                            "metadata": {"fet_network": "stable-testnet", "provider_agent_wallet": ctx.gateway_wallet,
                                         "service": "gateway_smoke"}}}

    async def notify_smoke(self, ctx: ChatContext) -> dict:
        key = f"smoke:{ctx.event_id}"
        await self.store.enqueue(event_key=key, buyer_id=ctx.buyer_id, recipient=ctx.sender, session=ctx.session,
                                 gateway=ctx.gateway, result=text("Background notification test: this message was "
                                                                   "pushed from the outbox into this same chat."))
        return text("Queued a background notification; it should appear here within a few seconds.")

    # ---- Stripe TEST settlement outside CommitPayment ------------------------------------
    async def check_paid(self, ctx: ChatContext) -> dict:
        """Buyer says "paid": ask Stripe now (a chat claim is never proof)."""
        rows = await self.booking().open_stripe_payments(sender=ctx.sender)
        if not rows:
            return text("I don't see a card payment waiting for you. If you just paid, your confirmation is "
                        "already above; otherwise say 'ship cargo' to start a booking.")
        settled = await self.booking().check_stripe(rows[0])
        if settled is None:
            return text("Stripe hasn't confirmed that payment yet. I'll keep checking and post your booking "
                        "confirmation here as soon as it clears; your space stays held meanwhile.")
        return protocol_result(settled)

    async def poll_payments(self) -> int:
        """Background: settle Stripe sessions paid via the hosted link and close lapsed windows; push results."""
        pushed = 0
        for row, verdict in await self.booking().poll_stripe():
            binding = await self.store.latest_binding(row["sender_agent_address"])
            if binding is None:
                continue
            await self.store.enqueue(event_key=f"stripe:{row['reference']}:{verdict.get('action')}",
                                     buyer_id=row["sender_agent_address"], recipient=row["sender_agent_address"],
                                     session=row["chat_session_id"] or binding[1], gateway=binding[2],
                                     result=protocol_result(verdict))
            pushed += 1
        return pushed

    # ---- payment protocol events -----------------------------------------------------
    async def payment_event(self, envelope: dict) -> dict:
        kind, reference = envelope.get("type"), envelope.get("reference") or ""
        if kind == "reject":
            if await self._is_smoke(envelope):
                return await self._smoke_event(envelope)
            return await self.booking().on_reject(envelope)
        if reference.startswith("smoke_"):
            return await self._smoke_event(envelope)
        if kind == "commit":
            return await self.booking().on_commit(envelope)
        if kind == "ledger_result":
            return await self.booking().on_ledger_result(envelope)
        return {"action": "none", "followups": []}

    async def _is_smoke(self, envelope: dict) -> bool:
        async with self.repo.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT provider FROM payment_records WHERE sender_agent_address = %s AND status = 'REQUESTED' "
                "AND provider IN ('fetch', 'fetch_smoke') ORDER BY created_at DESC LIMIT 1",
                (envelope.get("sender_agent_address"),))).fetchone()
        return bool(row) and row[0] == "fetch_smoke"

    async def _smoke_event(self, envelope: dict) -> dict:
        kind = envelope.get("type")
        meta = envelope.get("metadata") or {}
        wallet = meta.get("buyer_fet_wallet") or meta.get("buyer_fet_address")
        async with self.repo.pool.connection() as conn, conn.transaction():
            row = await (await conn.execute(
                "SELECT reference FROM payment_records WHERE provider = 'fetch_smoke' AND sender_agent_address = %s "
                "AND (reference = %s OR %s::text IS NULL) ORDER BY created_at DESC LIMIT 1",
                (envelope.get("sender_agent_address"), envelope.get("reference"), envelope.get("reference")))).fetchone()
            if row is None:
                return {"action": "none", "followups": [text("No smoke-test payment on record; nothing was booked.")]}
            status = "REJECTED" if kind == "reject" else "COMMIT_RECEIVED"
            await conn.execute("UPDATE payment_records SET status = %s, buyer_wallet = coalesce(%s, buyer_wallet), "
                               "updated_at = now() WHERE reference = %s", (status, wallet, row[0]))
            await _event(conn, "PAYMENT_REJECTED" if kind == "reject" else "PAYMENT_COMMIT_RECEIVED", "payment", row[0],
                         correlation_id=row[0], payload={"wallet_metadata_present": bool(wallet),
                                                         "metadata_keys": sorted(meta.keys()),
                                                         "has_tx": bool(envelope.get("transaction_id"))})
        if kind == "reject":
            return {"action": "none", "followups": [text("Smoke-test payment rejected; nothing was charged or booked.")]}
        return {"action": "cancel", "transaction_id": envelope.get("transaction_id"),
                "reason": "Smoke test only: not verified, nothing was booked.",
                "followups": [text("Smoke-test commit received (wallet metadata "
                                   f"{'present' if wallet else 'MISSING'}). I did not complete it; nothing was booked.")]}
