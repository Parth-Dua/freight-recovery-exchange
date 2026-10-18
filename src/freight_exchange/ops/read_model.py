"""Mission Control read model (HX-OPS-001): an observer over Tiger, never an orchestrator.

Everything here is a read of authoritative business tables (`sailings`, `capacity_slots`, `holds`, `bookings`,
`booking_slots`, `negotiation_sessions`, `negotiation_messages`, `recovery_runs`, `recovery_legs`) and the
`market_events` hypertable, normalised into allowlisted public DTOs. No function in this module writes business
state, sends an agent message, or calls a payment/hold/solver API. The only writes are the observer's own
`sailing_capacity_snapshots` and `market_state_checkpoints` (see `snapshots.py`).

Privacy: buyer identities are pseudonymised ("Buyer A"); buyer budgets, auto-rebook limits, must-deliver flags,
carrier floors/policies, payment metadata and wallets never leave this module (see `FORBIDDEN_KEYS`).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA_VERSION = 1
PROJECTION_SCHEMA = 1

# Public port coordinates (approximate harbour positions) and simulated sea lanes. Market data is simulated;
# vessel positions are interpolated along these lanes and labelled SIMULATED in the UI.
PORTS = {
    "AEJEA": {"name": "Jebel Ali", "country": "AE", "lat": 25.011, "lon": 55.061},
    "SADMM": {"name": "Dammam", "country": "SA", "lat": 26.493, "lon": 50.205},
    "OMSOH": {"name": "Sohar", "country": "OM", "lat": 24.497, "lon": 56.628},
    "INBOM": {"name": "Mumbai", "country": "IN", "lat": 18.950, "lon": 72.840},
    "INMUN": {"name": "Mundra", "country": "IN", "lat": 22.740, "lon": 69.700},
    "PKKHI": {"name": "Karachi", "country": "PK", "lat": 24.820, "lon": 66.970},
}
_EXIT = {  # leaving the Gulf through the Strait of Hormuz, then the Gulf of Oman
    "AEJEA": [(25.75, 55.75), (26.45, 56.45), (25.40, 57.40)],
    "SADMM": [(27.00, 51.30), (26.60, 53.80), (26.20, 55.40), (26.45, 56.45), (25.40, 57.40)],
    "OMSOH": [(24.60, 57.40)],
}
_APPROACH = {
    "INBOM": [(23.00, 63.00), (20.20, 70.50)],
    "INMUN": [(23.60, 63.00), (22.60, 68.60)],
    "PKKHI": [(24.50, 62.50), (24.60, 66.30)],
    "AEJEA": [(25.40, 57.40), (26.45, 56.45), (25.75, 55.75)],
}

FORBIDDEN_KEYS = {"max_total_price_minor", "max_total_minor", "recovery_allowance_minor", "max_additional_cost_minor",
                  "must_deliver", "priority", "buyer_wallet", "buyer_fet_wallet", "gateway_wallet", "policy",
                  "floor", "minimum_price_per_unit", "client_secret", "checkout_url", "provider_tx_id",
                  "claim_code_hash", "carrier_signature", "payment_terms"}

TERMINAL_RUN = {"COMPLETED", "PARTIAL", "UNRESOLVED", "FAILED", "RECONCILIATION_REQUIRED"}


def public_objective(o: dict | None) -> dict | None:
    """Solver stage values. Stage 1 is an aggregate count of must-deliver bookings recovered (never per buyer)."""
    if not o:
        return None
    return {"mustDeliverRecovered": o.get("must_deliver"), "recovered": o.get("recovered"),
            "approvals": o.get("approvals"), "incrementalCostMinor": o.get("incremental_cost_minor"),
            "arrivalRank": o.get("arrival_rank")}


def route(origin: str, destination: str) -> list[list[float]]:
    pts = [(PORTS[origin]["lat"], PORTS[origin]["lon"])] if origin in PORTS else []
    pts += _EXIT.get(origin, []) + _APPROACH.get(destination, [])
    if destination in PORTS:
        pts.append((PORTS[destination]["lat"], PORTS[destination]["lon"]))
    return [[round(lon, 3), round(lat, 3)] for lat, lon in pts]


def iso(ts: datetime | None) -> str | None:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if ts else None


def mask_address(addr: str | None) -> str | None:
    return f"{addr[:10]}…{addr[-6:]}" if addr and len(addr) > 20 else addr


async def rows(conn, sql: str, args: tuple | dict = ()) -> list[dict]:
    cur = await conn.execute(sql, args)
    names = [c.name for c in cur.description]
    return [dict(zip(names, r)) for r in await cur.fetchall()]


# ---- pseudonymous buyer labels ---------------------------------------------------------------------
class BuyerLabels:
    """Stable 'Buyer A' labels: seeded demo buyers keep their letter; others get the next free letters in order
    of first appearance. Raw buyer ids / agent addresses never reach the browser."""

    def __init__(self, first_seen: list[tuple[str, datetime]]):
        self.map: dict[str, str] = {}
        used: set[str] = set()
        for buyer, _ in first_seen:
            m = re.fullmatch(r"(?:demo_|rehearsal_)?buyer_([a-z])", buyer or "")
            if m and m.group(1).upper() not in used:
                self.map[buyer] = m.group(1).upper()
                used.add(m.group(1).upper())
        letters = (chr(c) for c in range(ord("A"), ord("Z") + 1))
        for buyer, _ in first_seen:
            if buyer in self.map:
                continue
            letter = next((x for x in letters if x not in used), None)
            self.map[buyer] = letter or f"{len(self.map) + 1}"
            used.add(self.map[buyer])

    def label(self, buyer: str | None) -> str | None:
        return f"Buyer {self.map[buyer]}" if buyer in self.map else ("Buyer ?" if buyer else None)

    def node(self, buyer: str | None) -> str | None:
        return f"buyer-{self.map[buyer].lower()}" if buyer in self.map else None


async def buyer_labels(conn) -> BuyerLabels:
    seen = await rows(conn, """
        SELECT buyer, min(at) AS at FROM (
            SELECT buyer_id AS buyer, created_at AS at FROM bookings
            UNION ALL SELECT buyer_id, created_at FROM cargo_requests
            UNION ALL SELECT substr(buyer_agent_key, 7), opened_at FROM negotiation_sessions) x
        WHERE buyer IS NOT NULL GROUP BY buyer ORDER BY min(at), buyer""")
    return BuyerLabels([(r["buyer"], r["at"]) for r in seen])


# ---- current projection ------------------------------------------------------------------------------
async def projection(conn, labels: BuyerLabels | None = None) -> dict:
    labels = labels or await buyer_labels(conn)
    cursor = (await (await conn.execute("SELECT max(ts) FROM market_events")).fetchone())[0]
    carriers = await rows(conn, """SELECT k.carrier_id, k.display_name, k.agent_address, k.active,
                                          (SELECT count(*) FROM vessels v WHERE v.carrier_id = k.carrier_id) AS vessels
                                   FROM carriers k ORDER BY k.carrier_id""")
    sailings = await rows(conn, """
        SELECT s.sailing_id, s.carrier_id, s.vessel_id, v.name AS vessel_name, s.origin_port, s.destination_port,
               s.corridor_id, s.departure_at, s.arrival_at, s.status,
               (SELECT count(*) FROM capacity_slots c WHERE c.sailing_id = s.sailing_id) AS total_units,
               (SELECT count(*) FROM capacity_slots c WHERE c.sailing_id = s.sailing_id AND c.status = 'AVAILABLE')
                   AS slot_available
        FROM sailings s LEFT JOIN vessels v ON v.vessel_id = s.vessel_id ORDER BY s.departure_at, s.sailing_id""")
    bookings = await rows(conn, """
        SELECT b.booking_id, b.buyer_id, b.carrier_id, b.sailing_id, b.status, b.total_price_minor, b.currency,
               b.replaces_booking_id, b.created_at, b.confirmed_at,
               (SELECT count(*) FROM booking_slots bs WHERE bs.booking_id = b.booking_id) AS quantity
        FROM bookings b ORDER BY b.created_at, b.booking_id""")
    holds = await rows(conn, """SELECT hold_id, carrier_id, sailing_id, quantity, status, expires_at, created_at,
                                       (recovery_offer_id IS NOT NULL OR EXISTS (SELECT 1 FROM recovery_legs l
                                            WHERE l.hold_id = h.hold_id)) AS recovery
                                FROM holds h ORDER BY created_at""")
    sessions = await session_rows(conn)
    runs = await rows(conn, "SELECT * FROM recovery_runs ORDER BY created_at")

    committed: dict[str, int] = {}
    for b in bookings:
        if b["status"] in ("COMMITTED", "DISRUPTED"):
            committed[b["sailing_id"]] = committed.get(b["sailing_id"], 0) + int(b["quantity"] or 0)
    held: dict[str, int] = {}
    for h in holds:
        if h["status"] == "ACTIVE":
            held[h["sailing_id"]] = held.get(h["sailing_id"], 0) + int(h["quantity"] or 0)
    public_sailings = []
    for s in sailings:
        total, c, hd = int(s["total_units"]), committed.get(s["sailing_id"], 0), held.get(s["sailing_id"], 0)
        withdrawn = s["status"] == "WITHDRAWN"
        public_sailings.append({
            "sailingId": s["sailing_id"], "carrierId": s["carrier_id"], "vesselId": s["vessel_id"],
            "vesselName": s["vessel_name"], "corridorId": s["corridor_id"] or f"{s['origin_port']}-{s['destination_port']}",
            "origin": s["origin_port"], "destination": s["destination_port"],
            "departureAt": iso(s["departure_at"]), "arrivalAt": iso(s["arrival_at"]),
            "status": s["status"] if s["status"] in ("ACTIVE", "WITHDRAWN") else "UNKNOWN",
            "totalUnits": total, "committedUnits": c, "heldUnits": hd,
            "availableUnits": 0 if withdrawn else max(0, total - c - hd),
            "withdrawnUnits": max(0, total - c - hd) if withdrawn else 0,
            "slotAvailableUnits": int(s["slot_available"]),  # Tiger's own slot count, for reconciliation
            "containerType": "20GP", "positionKind": "SIMULATED"})
    corridors = sorted({(s["origin"], s["destination"]) for s in public_sailings})
    incidents = [incident_summary(r, bookings, await legs_for(conn, r["recovery_id"])) for r in runs]
    return {
        "schemaVersion": PROJECTION_SCHEMA, "asOf": iso(datetime.now(timezone.utc)), "cursor": iso(cursor),
        "completeness": "COMPLETE", "marketKind": "SIMULATED",
        "ports": [{"portCode": k, **v} for k, v in PORTS.items()],
        "corridors": [{"corridorId": f"{o}-{d}", "origin": o, "destination": d, "route": route(o, d)}
                      for o, d in corridors],
        "carriers": [{"carrierId": c["carrier_id"], "displayName": c["display_name"],
                      "maskedAddress": mask_address(c["agent_address"]), "active": c["active"],
                      "vessels": c["vessels"], "kind": "CARRIER_UAGENT"} for c in carriers],
        "sailings": public_sailings,
        "holds": [{"holdId": h["hold_id"], "carrierId": h["carrier_id"], "sailingId": h["sailing_id"],
                   "quantity": h["quantity"], "status": h["status"], "expiresAt": iso(h["expires_at"]),
                   "recovery": bool(h["recovery"])} for h in holds],
        "bookings": [public_booking(b, labels) for b in bookings],
        "negotiations": [public_session(s, labels) for s in sessions],
        "incidents": incidents,
        "metrics": {
            "activeCarriers": sum(1 for c in carriers if c["active"]),
            "activeSailings": sum(1 for s in public_sailings if s["status"] == "ACTIVE"),
            "activeNegotiations": sum(1 for s in sessions if s["status"] == "OPEN"),
            "confirmedBookings": sum(1 for b in bookings if b["status"] == "COMMITTED"),
            "incidentsOpen": sum(1 for i in incidents if i["stage"] not in ("RESOLVED", "PARTIAL", "UNRESOLVED")),
        },
    }


def public_booking(b: dict, labels: BuyerLabels) -> dict:
    return {"bookingId": b["booking_id"], "buyer": labels.label(b["buyer_id"]), "buyerNode": labels.node(b["buyer_id"]),
            "carrierId": b["carrier_id"], "sailingId": b["sailing_id"], "quantity": int(b["quantity"] or 0),
            "status": b["status"], "totalMinor": b["total_price_minor"], "currency": b["currency"],
            "replacesBookingId": b["replaces_booking_id"], "createdAt": iso(b["created_at"]),
            "confirmedAt": iso(b["confirmed_at"])}


async def session_rows(conn, where: str = "", args: tuple = ()) -> list[dict]:
    return await rows(conn, f"""
        SELECT s.session_id, s.kind, s.request_id, s.booking_id, s.recovery_id, substr(s.buyer_agent_key, 7) AS buyer,
               s.carrier_id, s.status, s.round, s.rounds_max, s.opened_at, s.closed_at, s.expires_at,
               s.final_offer_id, s.final_total_minor, s.reason,
               (SELECT (m.public_terms->>'total_price_minor')::bigint FROM negotiation_messages m
                 WHERE m.session_id = s.session_id AND m.sender_role = 'carrier' AND m.round = 0 LIMIT 1) AS opening_minor,
               (SELECT o.sailing_id FROM offers o WHERE o.offer_id = s.final_offer_id) AS final_sailing,
               (SELECT o.quantity FROM offers o WHERE o.offer_id = s.final_offer_id) AS final_quantity
        FROM negotiation_sessions s {where} ORDER BY s.opened_at, s.session_id""", args)


def public_session(s: dict, labels: BuyerLabels) -> dict:
    return {"sessionId": s["session_id"], "kind": s["kind"].upper(), "requestId": s["request_id"],
            "bookingId": s["booking_id"], "incidentId": s["recovery_id"], "buyer": labels.label(s["buyer"]),
            "buyerNode": labels.node(s["buyer"]), "carrierId": s["carrier_id"], "status": s["status"],
            "round": s["round"], "roundsMax": s["rounds_max"], "openedAt": iso(s["opened_at"]),
            "closedAt": iso(s["closed_at"]), "deadlineAt": iso(s["expires_at"]),
            "openingMinor": s["opening_minor"], "finalMinor": s["final_total_minor"],
            "finalSailingId": s["final_sailing"], "finalQuantity": s["final_quantity"]}


# ---- incidents ----------------------------------------------------------------------------------------
async def legs_for(conn, recovery_id: str) -> list[dict]:
    return await rows(conn, """SELECT * FROM recovery_legs WHERE recovery_id = %s ORDER BY attempt, booking_id,
                                      sailing_id""", (recovery_id,))


def booking_state(booking: dict | None, legs: list[dict], run_status: str, approval: dict | None) -> str:
    """Per displaced booking. CONFIRMED needs a committed replacement; nothing advances without evidence."""
    latest = [leg for leg in legs if leg["attempt"] == max((x["attempt"] for x in legs), default=0)]
    if booking and booking["status"] == "SUPERSEDED" and any(leg["status"] == "CONFIRMED" for leg in legs):
        return "CONFIRMED"
    if latest and any(leg["status"] == "CONFIRMED" for leg in latest) and not all(
            leg["status"] == "CONFIRMED" for leg in latest):
        return "CONFIRMING"
    if latest and all(leg["status"] == "HELD" for leg in latest):
        if any(leg["needs_approval"] for leg in latest):
            if approval and approval.get("decision") == "approve":
                return "CONFIRMING"
            if approval and approval.get("decision") in ("decline", "expired"):
                return "UNRESOLVED"
            return "AWAITING_APPROVAL"
        return "HELD"
    if run_status in TERMINAL_RUN:
        return "UNRESOLVED"
    if latest and any(leg["status"] == "PROPOSED" for leg in latest):
        return "PROPOSED"
    return {"CREATED": "DISRUPTED", "OFFERS_REQUESTED": "DISRUPTED", "NEGOTIATING": "NEGOTIATING",
            "CLEARING": "CLEARING", "PROPOSED": "PROPOSED", "HOLDING": "HOLDING"}.get(run_status, "DISRUPTED")


STAGE_FOR_RUN = {"CREATED": "IMPACT_ASSESSED", "OFFERS_REQUESTED": "IMPACT_ASSESSED", "NEGOTIATING": "NEGOTIATING",
                 "CLEARING": "CLEARING", "PROPOSED": "ALLOCATION_PROPOSED", "HOLDING": "HOLDING",
                 "AWAITING_APPROVAL": "AWAITING_APPROVAL", "CONFIRMING": "CONFIRMING", "PARTIAL": "PARTIAL",
                 "UNRESOLVED": "UNRESOLVED", "FAILED": "UNRESOLVED", "RECONCILIATION_REQUIRED": "PARTIAL"}


def incident_summary(run: dict, bookings: list[dict], legs: list[dict]) -> dict:
    affected_ids = sorted({leg["booking_id"] for leg in legs} | {
        b["booking_id"] for b in bookings if b["sailing_id"] == run["sailing_id"]
        and b["status"] in ("DISRUPTED", "SUPERSEDED")})
    by_id = {b["booking_id"]: b for b in bookings}
    states = [booking_state(by_id.get(bid), [x for x in legs if x["booking_id"] == bid], run["status"], None)
              for bid in affected_ids]
    confirmed = states.count("CONFIRMED")
    unresolved = states.count("UNRESOLVED")
    stage = STAGE_FOR_RUN.get(run["status"], "IMPACT_ASSESSED")
    if run["status"] == "COMPLETED":
        stage = "RESOLVED" if affected_ids and confirmed == len(affected_ids) else "PARTIAL"
    return {"incidentId": run["recovery_id"], "sailingId": run["sailing_id"], "runStatus": run["status"],
            "stage": stage, "openedAt": iso(run["created_at"]), "updatedAt": iso(run["updated_at"]),
            "affected": len(affected_ids), "confirmed": confirmed, "unresolved": unresolved,
            "pending": len(affected_ids) - confirmed - unresolved, "attempts": run["attempts"]}


async def incidents(conn) -> list[dict]:
    p = await projection(conn)
    return sorted(p["incidents"], key=lambda i: i["openedAt"] or "", reverse=True)


async def incident_detail(conn, recovery_id: str) -> dict | None:
    labels = await buyer_labels(conn)
    run = (await rows(conn, "SELECT * FROM recovery_runs WHERE recovery_id = %s", (recovery_id,)) or [None])[0]
    if run is None:
        return None
    bookings = await rows(conn, """
        SELECT b.*, (SELECT count(*) FROM booking_slots bs WHERE bs.booking_id = b.booking_id) AS quantity
        FROM bookings b""")
    legs = await legs_for(conn, recovery_id)
    events = await rows(conn, """SELECT ts, event_id::text AS event_id, event_type, entity_id, carrier_id, sailing_id,
                                        payload FROM market_events
                                 WHERE correlation_id = %s OR (event_type IN ('APPROVAL_REQUESTED', 'APPROVAL_DECIDED')
                                       AND payload->>'approval_id' LIKE %s)
                                    OR (event_type = 'BOOKING_COMMITTED' AND payload->>'intent_id' = %s)
                                 ORDER BY ts""", (recovery_id, f"apr_{recovery_id}%", recovery_id))
    withdrawal = (await rows(conn, """SELECT d.event_id, d.carrier_id, d.sailing_id, d.authorized_by, d.reason,
                                             d.occurred_at, k.display_name
                                      FROM disruption_events d JOIN carriers k USING (carrier_id)
                                      WHERE d.event_id = %s""", (run["withdrawal_event_id"],)) or [None])[0]
    board = {e["payload"].get("job_ref"): e["payload"] for e in events if e["event_type"] == "RECOVERY_BOARD_POSTED"}
    approvals: dict[str, dict] = {}
    for e in events:
        if e["event_type"] == "APPROVAL_REQUESTED":
            approvals[e["entity_id"]] = {"status": "REQUESTED", "expiresAt": e["payload"].get("expires_at"),
                                        "requestedAt": iso(e["ts"])}
        elif e["event_type"] == "APPROVAL_DECIDED":
            d = e["payload"].get("decision")
            approvals.setdefault(e["entity_id"], {}).update(
                {"status": {"approve": "APPROVED", "decline": "DECLINED"}.get(d, (d or "").upper()),
                 "decidedAt": iso(e["ts"]), "decision": d})
    summary = incident_summary(run, bookings, legs)
    by_id = {b["booking_id"]: b for b in bookings}
    affected_ids = sorted({leg["booking_id"] for leg in legs} | {
        b["booking_id"] for b in bookings if b["sailing_id"] == run["sailing_id"]
        and b["status"] in ("DISRUPTED", "SUPERSEDED")})
    requests = {r["request_id"]: r for r in await rows(
        conn, "SELECT request_id, latest_arrival FROM cargo_requests WHERE request_id = ANY(%s)",
        ([by_id[x]["request_id"] for x in affected_ids if x in by_id],))}
    affected = []
    for bid in affected_ids:
        b = by_id.get(bid, {})
        mine = [x for x in legs if x["booking_id"] == bid]
        appr = approvals.get(bid)
        state = booking_state(b, mine, run["status"], {"decision": appr.get("decision")} if appr else None)
        replacement = next((by_id.get(x["replacement_booking_id"]) for x in mine if x["replacement_booking_id"]), None)
        req = requests.get(b.get("request_id"))
        reason = None
        if state == "UNRESOLVED":
            failed = [x for x in mine if x["status"] in ("FAILED", "RELEASED")]
            reason = ("Approval declined" if appr and appr.get("decision") == "decline" else
                      "Hold unavailable" if failed else "No eligible replacement")
        affected.append({
            "bookingId": bid, "buyer": labels.label(b.get("buyer_id")), "buyerNode": labels.node(b.get("buyer_id")),
            "quantity": int(b.get("quantity") or 0), "previousSailingId": b.get("sailing_id"),
            "previousTotalMinor": b.get("total_price_minor"), "bookingStatus": b.get("status"),
            "deadlineAt": iso(req["latest_arrival"]) if req and req["latest_arrival"] else None,
            "state": state, "reason": reason,
            "approval": {k: v for k, v in (appr or {}).items() if k != "decision"} or None,
            "replacement": {"bookingId": replacement["booking_id"], "sailingId": replacement["sailing_id"],
                            "carrierId": replacement["carrier_id"], "status": replacement["status"],
                            "totalMinor": replacement["total_price_minor"]} if replacement else None,
            "legs": [{"attempt": x["attempt"], "sailingId": x["sailing_id"], "carrierId": x["carrier_id"],
                      "quantity": x["quantity"], "quotedMinor": x["quoted_price_minor"], "status": x["status"],
                      "needsApproval": x["needs_approval"], "holdId": x["hold_id"],
                      "holdExpiresAt": iso(x["hold_expires_at"]), "arrivalAt": iso(x["arrival_at"]),
                      "replacementBookingId": x["replacement_booking_id"]} for x in mine]})
    sessions = [public_session(s, labels) for s in await session_rows(conn, "WHERE s.recovery_id = %s", (recovery_id,))]
    clearing = next((e for e in reversed(events) if e["event_type"] == "CLEARING_DONE"), None)
    proposed = next((e for e in reversed(events) if e["event_type"] == "RECOVERY_PROPOSED"), None)
    stage_events = {}
    for e in events:
        t = e["event_type"]
        key = {"RECOVERY_RUN_CREATED": "IMPACT_ASSESSED", "RECOVERY_NEGOTIATING": "NEGOTIATING",
               "RECOVERY_CLEARING": "CLEARING", "CLEARING_DONE": "ALLOCATION_PROPOSED", "RECOVERY_HOLDING": "HOLDING",
               "RECOVERY_AWAITING_APPROVAL": "AWAITING_APPROVAL", "RECOVERY_LEG_CONFIRMED": "CONFIRMING",
               "RECOVERY_COMPLETED": "RESOLVED", "RECOVERY_PARTIAL": "PARTIAL",
               "RECOVERY_UNRESOLVED": "UNRESOLVED"}.get(t)
        if key and key not in stage_events:
            stage_events[key] = {"stage": key, "at": iso(e["ts"]), "eventId": e["event_id"]}
    cp = clearing["payload"] if clearing else {}
    unaffected = sum(1 for b in bookings if b["status"] == "COMMITTED" and b["booking_id"] not in affected_ids
                     and not b["replaces_booking_id"])
    return {
        **summary,
        "withdrawal": {"eventId": withdrawal["event_id"], "at": iso(withdrawal["occurred_at"]),
                       "carrierId": withdrawal["carrier_id"], "carrierName": withdrawal["display_name"],
                       "sailingId": withdrawal["sailing_id"], "authorizedBy": "carrier operations",
                       "reason": withdrawal["reason"]} if withdrawal else None,
        "affectedBookings": affected, "unaffectedCommitted": unaffected,
        "board": [{"jobRef": k, "quantity": v.get("quantity"), "allowSplit": v.get("allow_split"),
                   "origin": v.get("origin_port"), "destination": v.get("destination_port"),
                   "latestArrivalAt": v.get("latest_arrival_at")} for k, v in sorted(board.items())],
        "negotiation": {"deadlineAt": iso(run["negotiation_deadline"]), "closedAt": iso(run["negotiation_closed_at"]),
                        "timeoutSeconds": 60, "sessions": len(sessions),
                        "byStatus": {st: sum(1 for s in sessions if s["status"] == st)
                                     for st in ("OPEN", "ACCEPTED", "EXHAUSTED", "REJECTED", "EXPIRED")},
                        "closedEarly": (proposed or {}).get("payload", {}).get("negotiation_closed_early"),
                        "seconds": (proposed or {}).get("payload", {}).get("negotiation_seconds")},
        "clearing": {
            "at": iso(clearing["ts"]) if clearing else None, "eventId": clearing["event_id"] if clearing else None,
            "objective": public_objective(cp.get("objective")), "inputVersion": cp.get("input_version"),
            "offersValid": (cp.get("negotiation") or {}).get("offers_valid"),
            "joint": {"recovered": (cp.get("objective") or {}).get("recovered"),
                      "assignments": [{"bookingId": a[0], "sailingId": a[1], "quantity": a[2]}
                                      for a in cp.get("assignments") or []],
                      "needsApproval": cp.get("needs_approval") or [], "unrecovered": cp.get("unrecovered") or []},
            "baseline": {"kind": (cp.get("baseline") or {}).get("baseline_kind"),
                         "recovered": (cp.get("baseline") or {}).get("recovered"),
                         "order": (cp.get("baseline") or {}).get("order"),
                         "assignments": [{"bookingId": k, "sailingId": leg["sailing_id"], "quantity": leg["quantity"]}
                                         for k, v in ((cp.get("baseline") or {}).get("assignments") or {}).items()
                                         for leg in v],
                         "unrecovered": (cp.get("baseline") or {}).get("unrecovered") or []},
        } if clearing else None,
        "stages": list(stage_events.values()),
        "sessionsList": sessions,
    }


# ---- negotiations (sanitised public timelines) --------------------------------------------------------
async def negotiations(conn, *, incident_id: str | None = None, request_id: str | None = None,
                       session_id: str | None = None, limit: int = 200) -> list[dict]:
    labels = await buyer_labels(conn)
    where, args = [], []
    if incident_id:
        where.append("s.recovery_id = %s"); args.append(incident_id)
    if request_id:
        where.append("s.request_id = %s"); args.append(request_id)
    if session_id:
        where.append("s.session_id = %s"); args.append(session_id)
    sessions = await session_rows(conn, ("WHERE " + " AND ".join(where)) if where else "", tuple(args))
    sessions = sessions[-limit:]
    msgs = await rows(conn, """SELECT session_id, message_id, sender_role, round, message_type, public_terms, ts
                               FROM negotiation_messages WHERE session_id = ANY(%s) ORDER BY ts, message_id""",
                      ([s["session_id"] for s in sessions],))
    by: dict[str, list[dict]] = {}
    for m in msgs:
        by.setdefault(m["session_id"], []).append(m)
    out = []
    for s in sessions:
        pub = public_session(s, labels)
        steps = []
        for m in by.get(s["session_id"], []):
            t = m["public_terms"] or {}
            kind = ("OPENING_OFFER" if m["sender_role"] == "carrier" and m["round"] == 0 else
                    "BUYER_COUNTER" if m["sender_role"] == "buyer_agent" else
                    {"ACCEPT_COUNTER": "ACCEPT", "REVISED_OFFER": "CARRIER_COUNTER",
                     "REJECT_COUNTER": "REJECT"}.get(m["message_type"], "NO_REPLY"))
            steps.append({"eventId": f"msg:{m['message_id']}", "at": iso(m["ts"]),
                          "senderKind": "CARRIER_AGENT" if m["sender_role"] == "carrier" else "BUYER_AGENT",
                          "messageKind": kind, "counterRound": m["round"], "offerId": t.get("offer_id"),
                          "amountMinor": t.get("total_price_minor") or t.get("proposed_total_price_minor"),
                          "proposed": m["sender_role"] == "buyer_agent",
                          "explanationCode": {"A comparable verified offer is lower.": "COMPETITOR_COMPARISON",
                                              "We would book now at this price.": "BOOK_NOW_OFFER"}.get(
                              t.get("public_rationale") or "", "COUNTER_STRATEGY" if m["sender_role"] == "buyer_agent"
                              else None),
                          "signedSenderVerified": True if m["sender_role"] == "carrier" else None})
        comparable = bool(s["opening_minor"] and s["final_total_minor"] and s["final_sailing"])
        out.append({**pub, "steps": steps, "termsComparableToOpening": comparable,
                    "eligibility": "Same lane, space on a sailing that arrives by the buyer's deadline, cargo class "
                                   "accepted by the carrier"})
    return out


# ---- public event stream -----------------------------------------------------------------------------
HIDDEN_EVENTS = {"COUNTER_OFFER_SENT", "OFFER_SUBMITTED", "OFFER_REQUESTED", "OFFER_REVISED", "COUNTER_ACCEPTED",
                 "COUNTER_REJECTED", "CHAT_NOTIFICATION_QUEUED", "BOOKING_INTENT_CREATED", "QUOTE_ROUND_COMPLETED",
                 "BUYER_NOTIFIED", "BOOKING_RECEIPT_ISSUED", "RECOVERY_OFFER_SUBMITTED", "RECOVERY_OFFER_DECLINED",
                 "OFFER_DECLINED", "OFFER_EXPIRED"}
PUBLIC_TYPE = {"RECOVERY_CLEARING": "CLEARING_STARTED", "HOLD_CREATED": "HOLD_GRANTED",
               "APPROVAL_REQUESTED": "RECOVERY_APPROVAL_REQUESTED"}
ACTOR = {"OFFER_SENT": "CARRIER_AGENT", "OFFER_ACCEPTED": "CARRIER_AGENT", "OFFER_REJECTED": "CARRIER_AGENT",
         "COUNTER_SENT": "BUYER_AGENT", "SAILING_WITHDRAWN": "CARRIER_OPS", "HOLD_CREATED": "CARRIER_AGENT",
         "HOLD_RELEASED": "CARRIER_AGENT", "HOLD_EXPIRED": "CARRIER_AGENT", "BOOKING_COMMITTED": "CARRIER_AGENT",
         "PAYMENT_REQUESTED": "PAYMENT_SERVICE", "PAYMENT_VERIFIED": "PAYMENT_SERVICE",
         "PAYMENT_REJECTED": "PAYMENT_SERVICE", "APPROVAL_DECIDED": "BUYER_AGENT",
         "CARGO_REQUEST_CREATED": "BUYER_AGENT", "OFFER_SELECTED": "BUYER_AGENT"}
PAYLOAD_ALLOW = {
    "OFFER_SENT": ["round", "offer_id", "total_price_minor"], "OFFER_ACCEPTED": ["round", "offer_id", "total_price_minor"],
    "OFFER_REJECTED": ["round", "offer_id"], "COUNTER_SENT": ["round", "offer_id", "proposed_total_price_minor"],
    "NEGOTIATION_OPENED": ["kind", "rounds_max"], "NEGOTIATION_CLOSED": ["status", "rounds", "final_offer_id", "final_total_minor"],
    "SAILING_WITHDRAWN": ["reason", "disrupted_bookings"], "BOOKING_COMMITTED": ["quantity", "total_price_minor",
                                                                                  "replaces_booking_id", "hold_id"],
    "BOOKING_SUPERSEDED": ["replacement_booking_ids"], "HOLD_CREATED": ["quantity", "ttl_seconds"],
    "HOLD_RELEASED": ["released_slots"], "RECOVERY_NEGOTIATING": ["deadline"],
    "RECOVERY_PROPOSED": ["attempt", "sessions", "stranded", "recovered", "unrecovered", "offers_valid",
                          "sessions_expired", "baseline_recovered", "negotiation_seconds", "negotiation_closed_early"],
    "CLEARING_DONE": ["objective", "input_version"], "RECOVERY_LEG_HELD": ["attempt", "booking_id", "hold_id"],
    "RECOVERY_LEG_CONFIRMED": ["attempt", "booking_id", "replacement_booking_id"],
    "RECOVERY_LEG_FAILED": ["attempt", "booking_id", "detail"], "RECOVERY_LEG_RELEASED": ["attempt", "booking_id", "detail"],
    "APPROVAL_REQUESTED": ["booking_id", "expires_at"], "APPROVAL_DECIDED": ["booking_id", "decision"],
    "RECOVERY_BOARD_POSTED": ["job_ref", "quantity", "allow_split", "origin_port", "destination_port", "latest_arrival_at"],
    "PAYMENT_VERIFIED": ["provider", "purpose"], "PAYMENT_REQUESTED": ["provider", "method", "purpose", "amount_minor",
                                                                       "currency"],
    "CARGO_REQUEST_CREATED": ["quantity", "container_type"], "RECOVERY_RUN_CREATED": [],
    "BOOKING_DISRUPTED": [], "RECOVERY_CLEARING": [], "RECOVERY_HOLDING": [],
}


class EventContext:
    """Lookups needed to make an event legible (session -> buyer/carrier/incident) without exposing raw ids."""

    def __init__(self, sessions: dict[str, dict], carriers: dict[str, str], labels: BuyerLabels,
                 hold_sailing: dict[str, tuple[str, int]], booking_buyer: dict[str, str]):
        self.sessions, self.carriers, self.labels = sessions, carriers, labels
        self.hold_sailing, self.booking_buyer = hold_sailing, booking_buyer


async def event_context(conn) -> EventContext:
    labels = await buyer_labels(conn)
    sess = {s["session_id"]: s for s in await rows(conn, """SELECT session_id, kind, request_id, booking_id, recovery_id,
                                                               substr(buyer_agent_key, 7) AS buyer, carrier_id
                                                            FROM negotiation_sessions""")}
    carriers = {r["carrier_id"]: r["display_name"] for r in await rows(conn, "SELECT carrier_id, display_name FROM carriers")}
    holds = {r["hold_id"]: (r["sailing_id"], r["quantity"]) for r in await rows(
        conn, "SELECT hold_id, sailing_id, quantity FROM holds")}
    buyers = {r["booking_id"]: r["buyer_id"] for r in await rows(conn, "SELECT booking_id, buyer_id FROM bookings")}
    buyers.update({r["request_id"]: r["buyer_id"] for r in await rows(conn, "SELECT request_id, buyer_id FROM cargo_requests")})
    return EventContext(sess, carriers, labels, holds, buyers)


def _money(minor) -> str:
    if minor is None:
        return "—"
    v = int(minor) / 100
    return f"${v:,.0f}" if int(minor) % 100 == 0 else f"${v:,.2f}"


def public_event(e: dict, ctx: EventContext) -> dict | None:
    t = e["event_type"]
    if t in HIDDEN_EVENTS:
        return None
    p = e["payload"] or {}
    sess = ctx.sessions.get(e["entity_id"]) if e["entity_type"] == "negotiation" else None
    carrier = e["carrier_id"] or (sess or {}).get("carrier_id")
    cname = ctx.carriers.get(carrier, carrier or "")
    buyer_raw = (sess or {}).get("buyer") or ctx.booking_buyer.get(p.get("booking_id") or "") or (
        ctx.booking_buyer.get(e["entity_id"]) if e["entity_type"] in ("booking", "cargo_request") else None)
    buyer = ctx.labels.label(buyer_raw)
    incident = (sess or {}).get("recovery_id") or (e["correlation_id"] if (e["correlation_id"] or "").startswith("rec_")
                                                    else None) or (p.get("intent_id") if str(p.get("intent_id", "")).startswith("rec_") else None)
    if t in ("APPROVAL_REQUESTED", "APPROVAL_DECIDED") and str(p.get("approval_id", "")).startswith("apr_rec_"):
        incident = "rec_" + p["approval_id"].split("_")[2]
    sailing = e["sailing_id"]
    if t == "HOLD_CREATED" and not sailing:
        sailing = ctx.hold_sailing.get(e["entity_id"], (None, 0))[0]
    allowed = {k: p[k] for k in PAYLOAD_ALLOW.get(t, []) if k in p}
    if "objective" in allowed:
        allowed["objective"] = public_objective(allowed["objective"])
    if t == "HOLD_RELEASED":
        allowed["sailing_id"] = ctx.hold_sailing.get(e["entity_id"], (sailing, 0))[0]
    summary = summarise(t, e, p, cname, buyer, sess)
    if summary is None:
        return None
    return {
        "schemaVersion": SCHEMA_VERSION, "eventId": e["event_id"], "eventType": PUBLIC_TYPE.get(t, t), "sourceType": t,
        "eventTime": iso(e["ts"]), "recordedAt": iso(e["ts"]), "streamCursor": f"{iso(e['ts'])}|{e['event_id']}",
        "incidentId": incident, "requestId": (sess or {}).get("request_id"),
        "sessionId": e["entity_id"] if sess else None, "bookingId": e["entity_id"] if e["entity_type"] == "booking"
        else p.get("booking_id"), "holdId": e["entity_id"] if e["entity_type"] == "hold" else p.get("hold_id"),
        "sailingId": sailing, "carrierId": carrier, "logicalBuyerId": ctx.labels.node(buyer_raw), "buyer": buyer,
        "sessionKind": ((sess or {}).get("kind") or "").upper() or None,
        "corridorId": e["corridor_id"], "actorKind": ACTOR.get(t, "EXCHANGE"), "publicPayload": allowed,
        "summary": summary, "sourceEventId": e["event_id"]}


def summarise(t: str, e: dict, p: dict, cname: str, buyer: str | None, sess: dict | None) -> str | None:
    who = buyer or "A buyer"
    rnd = p.get("round")
    if t == "NEGOTIATION_OPENED":
        return f"{who} agent opened a {'recovery ' if p.get('kind') == 'recovery' else ''}negotiation with {cname}"
    if t == "OFFER_SENT":
        return (f"{cname} quoted {_money(p.get('total_price_minor'))} to {who}" if not rnd
                else f"{cname} came back at {_money(p.get('total_price_minor'))} to {who} (round {rnd})")
    if t == "COUNTER_SENT":
        return f"{who} agent proposed {_money(p.get('proposed_total_price_minor'))} to {cname} (round {rnd})"
    if t == "OFFER_ACCEPTED":
        return f"{cname} accepted {who}'s counter at {_money(p.get('total_price_minor'))}"
    if t == "OFFER_REJECTED":
        return f"{cname} declined {who}'s counter; its quote stands"
    if t == "NEGOTIATION_CLOSED":
        st = p.get("status")
        return f"{who} ↔ {cname} closed: {st.lower() if st else 'closed'}" + (
            f" at {_money(p.get('final_total_minor'))}" if p.get("final_total_minor") else "")
    if t == "SAILING_WITHDRAWN":
        return f"{cname} withdrew {e['sailing_id']} · {p.get('disrupted_bookings', 0)} bookings affected"
    if t == "BOOKING_DISRUPTED":
        return f"{e['entity_id']} ({who}) displaced from {e['sailing_id']}"
    if t == "RECOVERY_RUN_CREATED":
        return f"Recovery {e['entity_id']} opened for {e['sailing_id']}"
    if t == "RECOVERY_BOARD_POSTED":
        return f"Stranded cargo posted: {p.get('quantity')}×20GP {p.get('origin_port')} → {p.get('destination_port')}"
    if t == "RECOVERY_NEGOTIATING":
        return "Recovery negotiations running in parallel (60 s maximum)"
    if t == "RECOVERY_CLEARING":
        return "Exchange is jointly clearing the final offers"
    if t == "CLEARING_DONE":
        o = p.get("objective") or {}
        return f"Joint allocation found: {o.get('recovered', 0)} bookings recoverable (proposed, not booked)"
    if t == "RECOVERY_PROPOSED":
        return f"Proposal: {p.get('recovered')} of {p.get('stranded')} recoverable · first-come baseline " \
               f"{p.get('baseline_recovered')}"
    if t == "RECOVERY_HOLDING":
        return "Requesting carrier holds for the proposed replacements"
    if t == "HOLD_CREATED":
        return f"{cname} granted a hold for {p.get('quantity')}×20GP" + (f" on {e['sailing_id']}" if e["sailing_id"] else "")
    if t in ("HOLD_RELEASED", "HOLD_EXPIRED"):
        return f"{cname} {'released' if t == 'HOLD_RELEASED' else 'expired'} a hold"
    if t == "APPROVAL_REQUESTED":
        return f"Asked {who} in ASI:One to approve a pricier replacement"
    if t == "APPROVAL_DECIDED":
        return f"{who} {'approved' if p.get('decision') == 'approve' else 'declined'} the replacement"
    if t == "RECOVERY_LEG_HELD":
        return f"Replacement space held for {p.get('booking_id')}"
    if t == "RECOVERY_LEG_CONFIRMED":
        return f"{cname} confirmed replacement {p.get('replacement_booking_id')} for {p.get('booking_id')}"
    if t == "RECOVERY_LEG_FAILED":
        return f"Replacement for {p.get('booking_id')} could not be secured"
    if t == "RECOVERY_LEG_RELEASED":
        return f"Replacement space for {p.get('booking_id')} released"
    if t == "BOOKING_COMMITTED":
        return f"{cname} committed {e['entity_id']}" + (f" (replaces {p['replaces_booking_id']})"
                                                       if p.get("replaces_booking_id") else "") + \
               f" · {p.get('quantity', '?')}×20GP on {e['sailing_id']}"
    if t == "BOOKING_SUPERSEDED":
        return f"{e['entity_id']} superseded by {', '.join(p.get('replacement_booking_ids') or [])}"
    if t.startswith("RECOVERY_") and t[9:] in ("COMPLETED", "PARTIAL", "UNRESOLVED", "AWAITING_APPROVAL",
                                               "CONFIRMING", "FAILED"):
        return f"Recovery {e['entity_id']}: {t[9:].replace('_', ' ').lower()}"
    if t == "PAYMENT_REQUESTED":
        return f"Payment requested: {_money(p.get('amount_minor'))}" if p.get("amount_minor") else "Payment requested"
    if t == "PAYMENT_VERIFIED":
        return "Payment verified with the provider"
    if t == "CARGO_REQUEST_CREATED":
        return f"{who} posted a cargo request: {p.get('quantity')}×20GP"
    if t == "OFFER_SELECTED":
        return f"{who} selected {cname}'s offer"
    return t.replace("_", " ").title()


async def public_events(conn, *, after: datetime | None = None, before: datetime | None = None,
                        incident_id: str | None = None, limit: int = 500, ctx: EventContext | None = None) -> list[dict]:
    ctx = ctx or await event_context(conn)
    where, args = [], []
    if after:
        where.append("ts > %s"); args.append(after)
    if before:
        where.append("ts <= %s"); args.append(before)
    # A recovery run re-asserts its status while it waits (e.g. every settle cycle); only the first of a repeated
    # run status is a transition, so later repeats are dropped here (live stream and replay alike).
    where.append("""NOT (e.entity_type = 'recovery_run' AND e.event_type <> 'RECOVERY_BOARD_POSTED' AND e.event_type IS
                    NOT DISTINCT FROM (SELECT p.event_type FROM market_events p WHERE p.correlation_id = e.correlation_id
                    AND p.entity_type = 'recovery_run' AND p.event_type <> 'RECOVERY_BOARD_POSTED' AND p.ts < e.ts
                    ORDER BY p.ts DESC LIMIT 1))""")
    raw = await rows(conn, f"""SELECT ts, event_id::text AS event_id, event_type, entity_type, entity_id, correlation_id,
                                      carrier_id, sailing_id, corridor_id, payload FROM market_events e
                               WHERE {' AND '.join(where)}
                               ORDER BY ts, event_id LIMIT %s""", (*args, limit))
    out = [x for x in (public_event(e, ctx) for e in raw) if x]
    if incident_id:
        out = [x for x in out if x["incidentId"] == incident_id or x["sourceType"] == "SAILING_WITHDRAWN"
               or x["sourceType"] == "BOOKING_DISRUPTED"]
    return out


def sanitize_check(obj: Any) -> list[str]:
    """Paths of any forbidden key in a DTO (used by tests and as a last-line guard in the API)."""
    found = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in FORBIDDEN_KEYS:
                    found.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
    walk(obj, "$")
    return found


def window(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)
