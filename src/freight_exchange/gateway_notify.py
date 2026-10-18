"""Recovery notifications as cards, pushed by the gateway into each buyer's existing ASI:One chat.

Buyers who reached us through the gateway get native cards via the durable chat outbox; anyone else
(older chats with the legacy LangGraph identity) keeps the legacy text notifier. Each (booking, kind,
run) is queued at most once, so retries never double-message. Never says "recovered" before the
carrier confirmed.
"""

from __future__ import annotations

from freight_seams import DisruptedBooking, RecoveryOutcome, RecoveryProposal

from freight_exchange import cards
from freight_exchange.asi import messages as m
from freight_exchange.asi.recovery_notify import cancellation_context, carrier_names, plan_recovery_messages
from freight_exchange.gateway_store import GatewayStore
from freight_exchange.observability import log_event


class GatewayRecoveryNotifier:
    def __init__(self, pool, legacy=None):
        self.pool = pool
        self.store = GatewayStore(pool)
        self.legacy = legacy  # AsiRecoveryNotifier for buyers without a gateway conversation

    async def _split(self, stranded: list[DisruptedBooking]):
        bound, other = {}, []
        for b in stranded:
            binding = await self.store.latest_binding(b.buyer_id)
            if binding:
                bound[b.booking_id] = binding
            else:
                other.append(b)
        return bound, other

    async def _push(self, booking: DisruptedBooking, binding, key: str, result: dict) -> None:
        recipient, session, gateway = binding
        queued = await self.store.enqueue(event_key=key, buyer_id=booking.buyer_id, recipient=recipient,
                                          session=session, gateway=gateway, result=result)
        log_event("recovery_card_queued", booking_id=booking.booking_id, key=key, queued=queued)

    async def _fare_difference(self, service, booking: DisruptedBooking, binding, outcome, diff: int, legs,
                               names: dict) -> dict | None:
        """Replacement costs more than the original freight: request the difference after the carrier confirmed.
        Never blocks the Rebooked card; a failure here is logged and the balance can be requested again."""
        replacement = outcome.diagnostics.get(f"replacement:{booking.booking_id}") if outcome else None
        if diff <= 0 or not replacement:
            return None
        label = (f"{booking.booking_id} → {replacement} on "
                 + ", ".join(f"{names.get(a.carrier_id, a.carrier_id)} {a.sailing_id}" for a in legs))
        try:
            return await service.booking().request_topup(
                buyer_id=booking.buyer_id, chat_session=binding[1], original_booking_id=booking.booking_id,
                replacement_booking_id=replacement, amount_minor=diff, currency=booking.currency, label=label)
        except Exception as exc:
            log_event("fare_difference_request_failed", booking_id=booking.booking_id, error_type=type(exc).__name__)
            return None

    async def _recovery_trace_cta(self, service, booking: DisruptedBooking, binding, recovery_run_id: str,
                                  chosen: str | None) -> list[dict]:
        """Pre-render this buyer's own recovery transcript; a button opens it instantly. Never blocks the result."""
        from freight_exchange import negotiation_trace as nt
        if not nt.enabled():
            return []
        try:
            traces = await nt.recovery_traces(self.pool, buyer_id=booking.buyer_id, recovery_id=recovery_run_id,
                                              booking_id=booking.booking_id)
            if not traces:
                return []
            await nt.save_card(self.pool, trace_key=f"recovery:{recovery_run_id}:{booking.booking_id}",
                               buyer_id=booking.buyer_id, kind="recovery", recovery_id=recovery_run_id,
                               booking_id=booking.booking_id,
                               card=cards.recovery_trace_card(traces, booking_id=booking.booking_id, chosen=chosen),
                               trace={"sessions": [t.as_dict() for t in traces]})
            card_id = await service.new_card(booking.buyer_id, "detail", "recovery_trace",
                                             f"{recovery_run_id}:{booking.booking_id}", session=binding[1])
            return [{"label": "See recovery negotiation",
                     "selection": {"action": "view_recovery_negotiations", "recovery_id": recovery_run_id,
                                   "booking_id": booking.booking_id, "card_instance_id": card_id}}]
        except Exception as exc:
            log_event("recovery_trace_failed", booking_id=booking.booking_id, error_type=type(exc).__name__)
            return []

    async def cancelled(self, *, withdrawal_event_id, sailing_id, stranded) -> None:
        bound, other = await self._split(stranded)
        if other and self.legacy:
            await self.legacy.cancelled(withdrawal_event_id=withdrawal_event_id, sailing_id=sailing_id, stranded=other)
        context = await cancellation_context(self.pool, sailing_id)
        for b in stranded:
            if b.booking_id not in bound or context is None:
                continue
            notice = m.cancellation_notice(b, context)
            await self._push(b, bound[b.booking_id], f"cancel:{withdrawal_event_id}:{b.booking_id}", cards.status_detail(
                f"⚠️ Sailing {sailing_id} cancelled", [
                    ("Your booking", b.booking_id), ("Status", "DISRUPTED — negotiating replacement space"),
                    ("Carrier", context.carrier_name), ("Deadline", b.latest_arrival.strftime("%b %-d, %H:%M UTC")),
                    ("Note", "Nothing new is booked or charged without telling you.")],
                f"⚠️ SAILING CANCELLATION ALERT\nTrying to negotiate alternate sailings for {b.booking_id} now.\n\n"
                + notice))

    async def update(self, *, recovery_run_id, stranded, proposal: RecoveryProposal,
                     outcome: RecoveryOutcome | None) -> None:
        bound, other = await self._split(stranded)
        if other and self.legacy:
            await self.legacy.update(recovery_run_id=recovery_run_id, stranded=other, proposal=proposal, outcome=outcome)
        try:
            names = await carrier_names(self.pool)
        except Exception:
            names = {}
        from freight_exchange.asi.approvals import approval_id_for
        from freight_exchange.gateway_service import GatewayService
        from freight_exchange.repository import Repository
        service = GatewayService(Repository(self.pool), self.store)
        topup = None
        for booking, kind, narration in plan_recovery_messages(
                [b for b in stranded if b.booking_id in bound], proposal, outcome, names):
            binding = bound[booking.booking_id]
            key = f"recovery:{recovery_run_id}:{booking.booking_id}:{kind}"
            topup = None
            if kind == m.NEEDS_APPROVAL:
                approval_id = approval_id_for(recovery_run_id, booking.booking_id)
                plan = [a for a in proposal.assignments if a.booking_id == booking.booking_id]
                new_total = sum(a.quoted_price_minor for a in plan)
                card_id = await service.new_card(booking.buyer_id, "review", "recovery_approval", approval_id,
                                                 session=binding[1])
                result = cards.recovery_approval(
                    approval_id=approval_id, booking_id=booking.booking_id, card_instance_id=card_id,
                    narration=narration, rows=[
                        ("Replacement", ", ".join(f"{names.get(a.carrier_id, a.carrier_id)} {a.sailing_id}" for a in plan)),
                        ("Arrives", max(a.arrival_at for a in plan).strftime("%b %-d, %H:%M UTC") if plan else "-"),
                        ("New freight", m.money(new_total, booking.currency)),
                        ("Was", m.money(booking.original_price_minor, booking.currency)),
                        ("Extra vs your auto-rebook limit",
                         f"{m.money(new_total - booking.original_price_minor, booking.currency)} vs "
                         f"{m.money(booking.max_additional_cost_minor, booking.currency)}"),
                        ("If you approve", f"You pay the {m.money(new_total - booking.original_price_minor, booking.currency)} "
                                           "fare difference by card once the carrier confirms"
                         if new_total > booking.original_price_minor else "No extra charge"),
                        ("Status", "PENDING APPROVAL — space is held, nothing is booked yet"),
                        ("Expires", "30 minutes")])
            elif kind == m.REBOOKED:
                rows = [("Original", f"{booking.booking_id} (SUPERSEDED)")]
                for a in (outcome.assignments if outcome else ()):
                    if a.booking_id == booking.booking_id:
                        rows.append(("Replacement", f"{outcome.diagnostics.get('replacement:' + a.booking_id, '?')} on "
                                                    f"{names.get(a.carrier_id, a.carrier_id)} {a.sailing_id}"))
                        rows.append(("Arrives", a.arrival_at.strftime("%b %-d, %H:%M UTC")))
                legs = [a for a in (outcome.assignments if outcome else ()) if a.booking_id == booking.booking_id]
                diff = sum(a.quoted_price_minor for a in legs) - booking.original_price_minor
                if diff > 0:
                    rows.append(("Fare difference", f"{m.money(diff, booking.currency)} more than your original "
                                                    "booking — payment request below"))
                elif diff < 0:
                    rows.append(("Fare difference", f"{m.money(-diff, booking.currency)} less than your original "
                                                    "booking — no extra charge"))
                rows.append(("Status", "RECOVERED — confirmed by the carrier"))
                topup = await self._fare_difference(service, booking, binding, outcome, diff, legs, names)
                chosen = ", ".join(f"{names.get(a.carrier_id, a.carrier_id)} {a.sailing_id} ({a.quantity}×20GP)"
                                   for a in (outcome.assignments if outcome else ())
                                   if a.booking_id == booking.booking_id)
                result = cards.status_detail("Rebooked", rows, narration, ctas=await self._recovery_trace_cta(
                    service, booking, binding, recovery_run_id, chosen or None))
            else:
                result = cards.status_detail("Not recovered", [("Booking", booking.booking_id),
                                                               ("Status", "UNRECOVERED — nothing new booked or charged")],
                                             narration, ctas=await self._recovery_trace_cta(
                                                 service, booking, binding, recovery_run_id, None))
            headline = {m.NEEDS_APPROVAL: "🔔 ACTION NEEDED: approve your replacement sailing",
                        m.REBOOKED: "✅ REBOOKED: your cargo has a new sailing"}.get(kind, "❌ RECOVERY UPDATE")
            result = {**result, "narration": f"{headline}\n\n{result['narration']}"}
            if kind == m.REBOOKED and topup is not None:  # the fare-difference payment follows the Rebooked card
                result = {"kind": "BUNDLE", "followups": [result, topup]}
            await self._push(booking, binding, key, result)
