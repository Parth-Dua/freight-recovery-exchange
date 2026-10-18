"""Buyer card builders (official ASI:One schemas: form, carousel, review, detail).

Each builder returns a protocol-neutral result {"kind": "CARD", narration, card_kind, card_payload,
fallback_text}; the gateway serializes it. Actionable CTAs carry `action`, the resource IDs and an opaque
`card_instance_id`; the backend re-validates every one of them. Money is labelled simulated (freight), Stripe TEST
(card checkout) or testnet (the FET fee) so none is mistaken for real money.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from freight_exchange.responses import money


def _when(moment: datetime) -> str:
    return moment.strftime("%b %-d, %H:%M UTC")


def _day(moment: datetime) -> str:
    return moment.strftime("%b %-d")


def result(narration: str, kind: str, payload: dict, *, terminal: bool = False, fallback: str = "") -> dict:
    return {"kind": "CARD", "narration": narration, "card_kind": kind, "card_payload": payload,
            "terminal": terminal, "fallback_text": fallback or narration}


def cargo_setup_form(*, origins: list[tuple[str, str]], destinations: list[tuple[str, str]],
                     arrival_options: list[datetime], departure_options: list[datetime],
                     card_instance_id: str, error: str = "") -> dict:
    """Arrival/departure are selects built from real active sailings (the form has no date picker)."""
    deadlines = sorted({(a + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)
                        for a in arrival_options})
    departures = sorted({d.replace(hour=0, minute=0, second=0, microsecond=0) for d in departure_options})
    fields = [
        {"name": "origin_port", "kind": "select", "label": "Origin", "required": True,
         "options": [{"value": p, "label": f"{n} ({p})"} for p, n in origins]},
        {"name": "destination_port", "kind": "select", "label": "Destination", "required": True,
         "options": [{"value": p, "label": f"{n} ({p})"} for p, n in destinations]},
        {"name": "quantity", "kind": "number", "label": "Number of 20GP containers", "required": True},
        {"name": "latest_arrival", "kind": "select", "label": "Arrive no later than", "required": True,
         "options": [{"value": d.isoformat(), "label": _day(d)} for d in deadlines[:14]]},
        {"name": "earliest_departure", "kind": "select", "label": "Cargo ready from (optional)",
         "options": [{"value": "", "label": "Any time"}]
                    + [{"value": d.isoformat(), "label": _day(d)} for d in departures[:14]]},
        {"name": "allow_split", "kind": "checkbox", "label": "Allow split across sailings"},
        {"name": "cargo_class", "kind": "select", "label": "Cargo class",
         "options": [{"value": "general", "label": "General"}, {"value": "high_value", "label": "High value"}]},
        {"name": "max_total_price", "kind": "number", "label": "Max simulated freight (USD, private to you)"},
        {"name": "priority", "kind": "select", "label": "Negotiation priority",
         "options": [{"value": "balanced", "label": "Balanced"}, {"value": "cheapest", "label": "Cheapest"},
                     {"value": "fastest", "label": "Fastest"}]},
        {"name": "must_deliver", "kind": "checkbox", "label": "Must deliver (priority if a sailing is cancelled)"},
        {"name": "recovery_allowance", "kind": "number",
         "label": "Auto-rebook extra cost limit (simulated USD, 0 = always ask me)"},
    ]
    payload = {"title": "Set up your cargo shipment", "fields": fields,
               "submit_cta": {"label": "Find shipping offers",
                              "selection": {"action": "submit_cargo_request", "card_instance_id": card_instance_id,
                                            "form_revision": "2"}}}
    narration = (f"{error}\n\n" if error else "") + (
        "Tell me about the shipment. Your max price, priority, must-deliver flag and auto-rebook limit stay "
        "private to your buyer agent; carriers only see the cargo, route and dates.")
    return result(narration, "form", payload, fallback=narration + " (Reply with e.g. 'Quote 2x20GP from Jebel "
                  "Ali to Mumbai, arrive by Oct 23'.)")


def _short(moment: datetime) -> str:
    return moment.strftime("%a %b %-d, %H:%M")


def negotiation_line(opening_minor: int | None, final_minor: int, counters: int) -> str:
    """One short buyer-facing line; the full public trace stays in negotiation_messages."""
    if opening_minor and final_minor < opening_minor:
        saved = round((opening_minor - final_minor) * 100 / opening_minor)
        return f"Negotiated {money(opening_minor)} → {money(final_minor)}" + (f" (−{saved}%)" if saved else "")
    if counters:
        return f"Carrier held its price after {counters} counter{'s' if counters != 1 else ''}"
    return "Carrier's opening offer"


def final_offers_carousel(*, request_id: str, offers: list[dict], card_instance_id: str, header: str) -> dict:
    """One fact per field: carrier (title), price (subtitle), schedule + negotiation (two short lines)."""
    fastest = min(o["arrival_at"] for o in offers)
    items = []
    for o in offers:
        badges = [b for b in (
            {"label": "Best price", "variant": "success"} if o.get("best") else None,
            {"label": "Fastest", "variant": "info"} if o["arrival_at"] == fastest and len(offers) > 1 else None,
        ) if b]
        items.append({
            "id": o["offer_id"],
            "title": o["carrier_name"],
            "subtitle": f"{money(o['total_price_minor'])} · {o['quantity']}×20GP",
            "badges": badges,
            "secondary_text": (f"{o['sailing_id']}: {_short(o['departure_at'])} → {_short(o['arrival_at'])}\n"
                               + negotiation_line(o.get("opening_minor"), o["total_price_minor"], o.get("counters", 0))),
            "primary_cta": {"label": "Choose",
                            "selection": {"action": "select_offer", "request_id": request_id,
                                          "offer_id": o["offer_id"], "card_instance_id": card_instance_id}},
        })
    valid = min(o["expires_at"] for o in offers)
    fallback = header + "\n" + "\n".join(
        f"- {o['offer_id']}: {o['carrier_name']} {o['sailing_id']}, {money(o['total_price_minor'])}, arrives "
        f"{_when(o['arrival_at'])}" for o in offers) + "\nReply 'select <offer id>' to choose."
    return result(header, "carousel", {
        "title": "Your offers",
        "subtitle": f"Simulated freight (USD, times UTC) · valid until {valid.strftime('%H:%M')} UTC",
        "items": items}, fallback=fallback)


def booking_review(*, request_id: str, offer: dict, fee_fet: Decimal | None, card_instance_id: str,
                   stripe_fee_minor: int | None = None) -> dict:
    freight = offer["total_price_minor"]
    rows = [
        {"label": "Carrier", "value": offer["display_name"]},
        {"label": "Sailing", "value": f"{offer['sailing_id']} · {offer['origin_port']} → {offer['destination_port']}"},
        {"label": "Departs", "value": _when(offer["departure_at"])},
        {"label": "Arrives", "value": _when(offer["arrival_at"])},
        {"label": "Containers", "value": f"{offer['quantity']}×20GP"},
    ]
    if stripe_fee_minor is not None:
        total = freight + stripe_fee_minor
        rows += [{"label": "Simulated freight", "value": money(freight)},
                 {"label": "Booking service fee", "value": money(stripe_fee_minor)},
                 {"label": "Total by card", "value": f"{money(total)} (Stripe TEST mode, no real money)"}]
        pay_label = f"Confirm and pay {money(total)}"
        narration = (f"{offer['display_name']} {offer['sailing_id']}: {money(total)} by card "
                     "(Stripe TEST mode, no real money moves).")
    else:
        rows.append({"label": "Carrier freight quote", "value": f"{money(freight)} USD — simulated, not charged"})
        if fee_fet is not None:
            rows.append({"label": "Booking coordination service",
                         "value": f"{fee_fet} testnet FET — demonstration payment via Fetch"})
        pay_label = "Confirm and pay" if fee_fet is not None else "Confirm (dev mode)"
        narration = (f"{offer['display_name']} {offer['sailing_id']}: {money(freight)} simulated freight"
                     + (f", plus a {fee_fet} testnet FET booking-coordination fee." if fee_fet is not None else "."))
    rows.append({"label": "Offer valid until", "value": _when(offer["expires_at"])})
    base = {"request_id": request_id, "offer_id": offer["offer_id"], "card_instance_id": card_instance_id}
    payload = {"title": "Review your booking", "summary_rows": rows,
               "approve_cta": {"label": pay_label, "selection": {"action": "confirm_booking", **base}, "primary": True},
               "reject_cta": {"label": "Cancel", "selection": {"action": "cancel_booking", **base}}}
    return result(narration, "review", payload, fallback=narration + " Reply 'confirm' or 'cancel'.")


def booking_receipt(*, booking_id: str, claim_code: str, offer: dict, payment_line: str) -> dict:
    rows = [
        {"label": "Booking", "value": booking_id},
        {"label": "Claim code", "value": claim_code},
        {"label": "Carrier / sailing", "value": f"{offer['display_name']} · {offer['sailing_id']}"},
        {"label": "Route", "value": f"{offer['origin_port']} → {offer['destination_port']}, {offer['quantity']}×20GP"},
        {"label": "Arrives", "value": _when(offer["arrival_at"])},
        {"label": "Simulated freight", "value": money(offer["total_price_minor"])},
        {"label": "Payment", "value": payment_line},
    ]
    narration = f"Booked: {booking_id}, confirmed by {offer['display_name']}. Claim code {claim_code}."
    return result(narration, "detail", {"title": "Booking confirmed", "summary_rows": rows, "ctas": []},
                  terminal=True)


def status_detail(title: str, rows: list[tuple[str, str]], narration: str) -> dict:
    return result(narration, "detail", {"title": title, "summary_rows": [{"label": k, "value": v} for k, v in rows],
                                        "ctas": []}, terminal=True)


def recovery_approval(*, approval_id: str, booking_id: str, rows: list[tuple[str, str]], card_instance_id: str,
                      narration: str) -> dict:
    base = {"approval_id": approval_id, "booking_id": booking_id, "card_instance_id": card_instance_id}
    payload = {"title": f"Approve replacement for {booking_id}?",
               "summary_rows": [{"label": k, "value": v} for k, v in rows],
               "approve_cta": {"label": "Approve replacement", "selection": {"action": "approve_recovery", **base},
                               "primary": True},
               "reject_cta": {"label": "Decline", "selection": {"action": "decline_recovery", **base}}}
    return result(narration, "review", payload,
                  fallback=narration + f" Reply 'approve {booking_id}' or 'decline {booking_id}'.")
