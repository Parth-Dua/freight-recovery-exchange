"""Buyer card builders (official ASI:One schemas: form, carousel, review, detail).

Each builder returns a protocol-neutral result {"kind": "CARD", narration, card_kind, card_payload,
fallback_text}; the gateway serializes it. Actionable CTAs carry `action`, the resource IDs and an opaque
`card_instance_id`; the backend re-validates every one of them. Money is labelled simulated (freight) or
testnet (the Fetch service fee) so neither is mistaken for the other.
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


def final_offers_carousel(*, request_id: str, offers: list[dict], card_instance_id: str, header: str,
                          trace: str = "") -> dict:
    items = []
    for o in offers:
        rounds = o.get("trace") or ""
        items.append({
            "id": o["offer_id"],
            "title": f"{o['carrier_name']} · {o['sailing_id']}",
            "subtitle": f"{money(o['total_price_minor'])} simulated freight for {o['quantity']}×20GP",
            "badges": [b for b in ({"label": "Best price", "variant": "success"} if o.get("best") else None,
                                   {"label": "Negotiated", "variant": "info"} if o.get("negotiated") else None) if b],
            "secondary_text": (f"Departs {_when(o['departure_at'])}, arrives {_when(o['arrival_at'])}. "
                               f"Offer valid until {_when(o['expires_at'])}." + (f" {rounds}" if rounds else "")),
            "primary_cta": {"label": "Choose this offer",
                            "selection": {"action": "select_offer", "request_id": request_id,
                                          "offer_id": o["offer_id"], "card_instance_id": card_instance_id}},
        })
    narration = header + (f"\n{trace}" if trace else "")
    fallback = narration + "\n" + "\n".join(
        f"- {o['offer_id']}: {o['carrier_name']} {o['sailing_id']}, {money(o['total_price_minor'])}, arrives "
        f"{_when(o['arrival_at'])}" for o in offers) + "\nReply 'select <offer id>' to choose."
    return result(narration, "carousel", {"title": "Final carrier offers", "subtitle": "Simulated freight market",
                                          "items": items}, fallback=fallback)


def booking_review(*, request_id: str, offer: dict, fee_fet: Decimal | None, card_instance_id: str) -> dict:
    rows = [
        {"label": "Carrier", "value": offer["display_name"]},
        {"label": "Sailing", "value": f"{offer['sailing_id']} · {offer['origin_port']} → {offer['destination_port']}"},
        {"label": "Departs / arrives", "value": f"{_when(offer['departure_at'])} / {_when(offer['arrival_at'])}"},
        {"label": "Containers", "value": f"{offer['quantity']}×20GP"},
        {"label": "Carrier freight quote", "value": f"{money(offer['total_price_minor'])} USD — simulated, not charged"},
    ]
    if fee_fet is not None:
        rows.append({"label": "Booking coordination service",
                     "value": f"{fee_fet} testnet FET — demonstration payment via Fetch"})
    rows.append({"label": "Note", "value": "No real freight is purchased or settled."})
    rows.append({"label": "Offer valid until", "value": _when(offer["expires_at"])})
    base = {"request_id": request_id, "offer_id": offer["offer_id"], "card_instance_id": card_instance_id}
    payload = {"title": "Review your booking", "summary_rows": rows,
               "approve_cta": {"label": "Confirm and pay" if fee_fet is not None else "Confirm (dev mode)",
                               "selection": {"action": "confirm_booking", **base}, "primary": True},
               "reject_cta": {"label": "Cancel", "selection": {"action": "cancel_booking", **base}}}
    narration = (f"{offer['display_name']} {offer['sailing_id']}: {money(offer['total_price_minor'])} simulated freight"
                 + (f", plus a {fee_fet} testnet FET booking-coordination fee." if fee_fet is not None else "."))
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
