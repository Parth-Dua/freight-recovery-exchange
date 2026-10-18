// Pure, deterministic projection reducer (HX-OPS-001 §6.6 / §13.1). Used by the live store (between authoritative
// resnapshots) and by Time Machine (checkpoint + ordered events). No I/O, no clocks, no business effects.
import type { IncidentSummary, Projection, PublicBooking, PublicMarketEvent, PublicSailing, PublicSession } from "./contracts";

const RUN_STAGE: Record<string, string> = {
  RECOVERY_RUN_CREATED: "IMPACT_ASSESSED", RECOVERY_NEGOTIATING: "NEGOTIATING", CLEARING_STARTED: "CLEARING",
  CLEARING_DONE: "ALLOCATION_PROPOSED", RECOVERY_PROPOSED: "ALLOCATION_PROPOSED", RECOVERY_HOLDING: "HOLDING",
  RECOVERY_AWAITING_APPROVAL: "AWAITING_APPROVAL", RECOVERY_CONFIRMING: "CONFIRMING", RECOVERY_PARTIAL: "PARTIAL",
  RECOVERY_UNRESOLVED: "UNRESOLVED", RECOVERY_FAILED: "UNRESOLVED", RECOVERY_COMPLETED: "COMPLETED",
};

export function recomputeCapacity(sailings: PublicSailing[], bookings: PublicBooking[], holds: Projection["holds"]): PublicSailing[] {
  const committed = new Map<string, number>();
  for (const b of bookings) if (b.status === "COMMITTED" || b.status === "DISRUPTED") committed.set(b.sailingId, (committed.get(b.sailingId) || 0) + b.quantity);
  const held = new Map<string, number>();
  for (const h of holds) if (h.status === "ACTIVE") held.set(h.sailingId, (held.get(h.sailingId) || 0) + (h.quantity || 0));
  return sailings.map((s) => {
    const c = committed.get(s.sailingId) || 0, h = held.get(s.sailingId) || 0;
    const free = Math.max(0, s.totalUnits - c - h);
    const withdrawn = s.status === "WITHDRAWN";
    return { ...s, committedUnits: c, heldUnits: h, availableUnits: withdrawn ? 0 : free, withdrawnUnits: withdrawn ? free : 0 };
  });
}

export function metricsOf(p: Projection): Projection["metrics"] {
  return {
    activeCarriers: p.carriers.filter((c) => c.active).length,
    activeSailings: p.sailings.filter((s) => s.status === "ACTIVE").length,
    activeNegotiations: p.negotiations.filter((s) => s.status === "OPEN").length,
    confirmedBookings: p.bookings.filter((b) => b.status === "COMMITTED").length,
    incidentsOpen: p.incidents.filter((i) => !["RESOLVED", "PARTIAL", "UNRESOLVED", "COMPLETED"].includes(i.stage)).length,
  };
}

function upsert<T>(list: T[], key: (x: T) => string, id: string, make: () => T, patch: (x: T) => T): T[] {
  const i = list.findIndex((x) => key(x) === id);
  if (i < 0) return [...list, patch(make())];
  const out = list.slice();
  out[i] = patch(out[i]);
  return out;
}

export function applyEvent(p: Projection, e: PublicMarketEvent): Projection {
  const pl = e.publicPayload || {};
  let { sailings, bookings, holds, negotiations, incidents } = p;
  let capacity = false;
  const sess = (patch: (s: PublicSession) => PublicSession) => {
    if (!e.sessionId) return;
    negotiations = upsert(negotiations, (s) => s.sessionId, e.sessionId, () => ({
      sessionId: e.sessionId!, kind: (e.sessionKind || "BOOKING") as PublicSession["kind"], buyer: e.buyer, buyerNode: e.logicalBuyerId,
      carrierId: e.carrierId || "", status: "OPEN", round: 0, openedAt: e.eventTime, incidentId: e.incidentId, requestId: e.requestId,
    }), patch);
  };
  switch (e.eventType) {
    case "NEGOTIATION_OPENED": sess((s) => s); break;
    case "OFFER_SENT":
      sess((s) => ({ ...s, latestMinor: pl.total_price_minor, openingMinor: !pl.round ? pl.total_price_minor : s.openingMinor,
                     round: Math.max(s.round, pl.round || 0) }));
      break;
    case "COUNTER_SENT": sess((s) => ({ ...s, lastCounterMinor: pl.proposed_total_price_minor, round: Math.max(s.round, pl.round || 0) })); break;
    case "OFFER_ACCEPTED": sess((s) => ({ ...s, latestMinor: pl.total_price_minor })); break;
    case "NEGOTIATION_CLOSED":
      sess((s) => ({ ...s, status: pl.status || "EXHAUSTED", finalMinor: pl.final_total_minor ?? null, closedAt: e.eventTime,
                     round: Math.max(s.round, pl.rounds || 0) }));
      break;
    case "SAILING_WITHDRAWN":
      sailings = sailings.map((s) => (s.sailingId === e.sailingId ? { ...s, status: "WITHDRAWN" } : s));
      capacity = true;
      break;
    case "BOOKING_COMMITTED": {
      const id = e.bookingId || "";
      bookings = upsert(bookings, (b) => b.bookingId, id, () => ({
        bookingId: id, buyer: e.buyer, buyerNode: e.logicalBuyerId, carrierId: e.carrierId || "", sailingId: e.sailingId || "",
        quantity: pl.quantity || 0, status: "COMMITTED", totalMinor: pl.total_price_minor, replacesBookingId: pl.replaces_booking_id,
        createdAt: e.eventTime, confirmedAt: e.eventTime,
      }), (b) => ({ ...b, status: "COMMITTED" }));
      if (pl.hold_id) holds = holds.map((h) => (h.holdId === pl.hold_id ? { ...h, status: "COMMITTED" } : h));
      capacity = true;
      break;
    }
    case "BOOKING_DISRUPTED":
      bookings = bookings.map((b) => (b.bookingId === e.bookingId ? { ...b, status: "DISRUPTED" } : b)); capacity = true; break;
    case "BOOKING_SUPERSEDED":
      bookings = bookings.map((b) => (b.bookingId === e.bookingId ? { ...b, status: "SUPERSEDED" } : b)); capacity = true; break;
    case "HOLD_GRANTED":
      if (e.holdId && e.sailingId) {
        holds = upsert(holds, (h) => h.holdId, e.holdId, () => ({ holdId: e.holdId!, carrierId: e.carrierId || undefined,
          sailingId: e.sailingId!, quantity: pl.quantity || 0, status: "ACTIVE", recovery: !!e.incidentId }), (h) => h);
        capacity = true;
      }
      break;
    case "HOLD_RELEASED": case "HOLD_EXPIRED":
      holds = holds.map((h) => (h.holdId === e.holdId ? { ...h, status: e.eventType === "HOLD_RELEASED" ? "RELEASED" : "EXPIRED" } : h));
      capacity = true;
      break;
    default: break;
  }
  if (e.incidentId && (RUN_STAGE[e.eventType] || e.eventType.startsWith("RECOVERY_LEG_"))) {
    incidents = upsert(incidents, (i) => i.incidentId, e.incidentId, (): IncidentSummary => ({
      incidentId: e.incidentId!, sailingId: e.sailingId || "", runStatus: "CREATED", stage: "IMPACT_ASSESSED",
      openedAt: e.eventTime, affected: 0, confirmed: 0, unresolved: 0, pending: 0,
    }), (i) => {
      const stage = RUN_STAGE[e.eventType];
      const next = { ...i, updatedAt: e.eventTime };
      if (e.eventType === "RECOVERY_RUN_CREATED" && e.sailingId) next.sailingId = e.sailingId;
      if (stage) { next.stage = stage; next.runStatus = e.eventType.replace("RECOVERY_", ""); }
      return next;
    });
  }
  if (e.incidentId && (e.eventType === "RECOVERY_RUN_CREATED" || e.eventType === "BOOKING_SUPERSEDED" || e.eventType.startsWith("RECOVERY_"))) {
    incidents = incidents.map((i) => (i.incidentId === e.incidentId ? withCounts(i, bookings, sailingsOf(i, sailings)) : i));
  }
  if (capacity) sailings = recomputeCapacity(sailings, bookings, holds);
  const next = { ...p, sailings, bookings, holds, negotiations, incidents, asOf: e.eventTime, cursor: e.eventTime };
  next.incidents = next.incidents.map((i) => (i.stage === "COMPLETED" ? { ...i, stage: i.affected && i.confirmed === i.affected ? "RESOLVED" : "PARTIAL" } : i));
  next.metrics = metricsOf(next);
  return next;
}

function sailingsOf(i: IncidentSummary, sailings: PublicSailing[]) { return sailings.find((s) => s.sailingId === i.sailingId); }

function withCounts(i: IncidentSummary, bookings: PublicBooking[], _s?: PublicSailing): IncidentSummary {
  const displaced = bookings.filter((b) => b.sailingId === i.sailingId && (b.status === "DISRUPTED" || b.status === "SUPERSEDED"));
  const confirmed = displaced.filter((b) => b.status === "SUPERSEDED" && bookings.some((r) => r.replacesBookingId === b.bookingId && r.status === "COMMITTED")).length;
  const terminal = ["PARTIAL", "UNRESOLVED", "COMPLETED", "RESOLVED"].includes(i.stage);
  const unresolved = terminal ? displaced.length - confirmed : 0;
  return { ...i, affected: Math.max(i.affected, displaced.length), confirmed, unresolved, pending: Math.max(0, displaced.length - confirmed - unresolved) };
}

/** Order used everywhere: business time, then event id (stable tie-break). */
export const byTime = (a: PublicMarketEvent, b: PublicMarketEvent) =>
  a.eventTime < b.eventTime ? -1 : a.eventTime > b.eventTime ? 1 : a.eventId < b.eventId ? -1 : a.eventId > b.eventId ? 1 : 0;
