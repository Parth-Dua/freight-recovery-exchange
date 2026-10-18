// Reducer / stream correctness (HX-OPS-001 §15.2) on small synthetic projections.
import { describe, expect, it } from "vitest";
import type { Projection, PublicMarketEvent } from "../data/contracts";
import { applyEvent } from "../data/reducer";

const base = (): Projection => ({
  schemaVersion: 1, asOf: "2026-10-18T10:00:00Z", completeness: "COMPLETE", marketKind: "SIMULATED",
  ports: [], corridors: [], carriers: [{ carrierId: "beacon", displayName: "Beacon", active: true, vessels: 1, kind: "CARRIER_UAGENT" }],
  sailings: [{ sailingId: "BEA-201", carrierId: "beacon", vesselId: "v", corridorId: "AEJEA-INBOM", origin: "AEJEA", destination: "INBOM",
               departureAt: "2026-10-20T06:00:00Z", arrivalAt: "2026-10-22T04:00:00Z", status: "ACTIVE", totalUnits: 4, committedUnits: 0,
               heldUnits: 0, availableUnits: 4, withdrawnUnits: 0, containerType: "20GP", positionKind: "SIMULATED" }],
  holds: [], bookings: [], negotiations: [], incidents: [],
  metrics: { activeCarriers: 1, activeSailings: 1, activeNegotiations: 0, confirmedBookings: 0, incidentsOpen: 0 },
});
let n = 0;
const ev = (eventType: string, extra: Partial<PublicMarketEvent> = {}, payload: Record<string, any> = {}): PublicMarketEvent => ({
  schemaVersion: 1, eventId: `e${++n}`, eventType, sourceType: eventType, eventTime: `2026-10-18T10:00:${String(n).padStart(2, "0")}Z`,
  recordedAt: "", streamCursor: "", actorKind: "EXCHANGE", publicPayload: payload, summary: eventType, ...extra,
});
const strip = (p: Projection) => JSON.stringify({ ...p, asOf: "", cursor: "" });

describe("projection reducer", () => {
  it("applies a duplicate event idempotently", () => {
    const e = ev("NEGOTIATION_OPENED", { sessionId: "s1", buyer: "Buyer A", logicalBuyerId: "buyer-a", carrierId: "beacon", sessionKind: "BOOKING" });
    const once = applyEvent(base(), e);
    expect(strip(applyEvent(once, e))).toBe(strip(once));
    expect(once.metrics.activeNegotiations).toBe(1);
  });

  it("keeps different buyers' sessions with the same carrier separate", () => {
    let p = applyEvent(base(), ev("NEGOTIATION_OPENED", { sessionId: "sA", buyer: "Buyer A", carrierId: "beacon" }));
    p = applyEvent(p, ev("NEGOTIATION_OPENED", { sessionId: "sB", buyer: "Buyer B", carrierId: "beacon" }));
    p = applyEvent(p, ev("COUNTER_SENT", { sessionId: "sA", carrierId: "beacon" }, { round: 1, proposed_total_price_minor: 500000 }));
    expect(p.negotiations.find((s) => s.sessionId === "sA")!.lastCounterMinor).toBe(500000);
    expect(p.negotiations.find((s) => s.sessionId === "sB")!.lastCounterMinor).toBeUndefined();
  });

  it("a counter is a proposal: it never becomes the carrier's price", () => {
    let p = applyEvent(base(), ev("OFFER_SENT", { sessionId: "s1", carrierId: "beacon" }, { round: 0, total_price_minor: 520000 }));
    p = applyEvent(p, ev("COUNTER_SENT", { sessionId: "s1", carrierId: "beacon" }, { round: 1, proposed_total_price_minor: 480000 }));
    const s = p.negotiations.find((x) => x.sessionId === "s1")!;
    expect(s.latestMinor).toBe(520000);
    expect(s.openingMinor).toBe(520000);
  });

  it("CLEARING_DONE proposes; it confirms nothing", () => {
    let p = applyEvent(base(), ev("RECOVERY_RUN_CREATED", { incidentId: "rec_1", sailingId: "ATL-101" }));
    p = applyEvent(p, ev("CLEARING_DONE", { incidentId: "rec_1" }, { objective: { recovered: 3 } }));
    expect(p.incidents[0].stage).toBe("ALLOCATION_PROPOSED");
    expect(p.incidents[0].confirmed).toBe(0);
    expect(p.metrics.confirmedBookings).toBe(0);
  });

  it("HOLD_GRANTED changes held capacity, not the confirmed-bookings KPI", () => {
    const p = applyEvent(base(), ev("HOLD_GRANTED", { holdId: "h1", sailingId: "BEA-201", carrierId: "beacon" }, { quantity: 2 }));
    const s = p.sailings[0];
    expect([s.heldUnits, s.availableUnits, s.committedUnits]).toEqual([2, 2, 0]);
    expect(p.metrics.confirmedBookings).toBe(0);
  });

  it("BOOKING_COMMITTED converts the hold; a withdrawal zeroes sellable space", () => {
    let p = applyEvent(base(), ev("HOLD_GRANTED", { holdId: "h1", sailingId: "BEA-201" }, { quantity: 2 }));
    p = applyEvent(p, ev("BOOKING_COMMITTED", { bookingId: "BK-1", sailingId: "BEA-201", carrierId: "beacon" }, { quantity: 2, hold_id: "h1" }));
    expect([p.sailings[0].committedUnits, p.sailings[0].heldUnits, p.sailings[0].availableUnits]).toEqual([2, 0, 2]);
    expect(p.metrics.confirmedBookings).toBe(1);
    p = applyEvent(p, ev("SAILING_WITHDRAWN", { sailingId: "BEA-201" }));
    expect([p.sailings[0].availableUnits, p.sailings[0].withdrawnUnits]).toEqual([0, 2]);
  });

  it("two confirmations with one pending approval is partial progress, never 3/3", () => {
    let p = base();
    p.sailings.push({ ...p.sailings[0], sailingId: "ATL-101", carrierId: "atlas" });
    p = applyEvent(p, ev("RECOVERY_RUN_CREATED", { incidentId: "rec_1", sailingId: "ATL-101" }));
    for (const id of ["BK-A", "BK-B", "BK-C"]) {
      p = applyEvent(p, ev("BOOKING_COMMITTED", { bookingId: id, sailingId: "ATL-101" }, { quantity: 1 }));
      p = applyEvent(p, ev("BOOKING_DISRUPTED", { bookingId: id, sailingId: "ATL-101", incidentId: "rec_1" }));
    }
    for (const id of ["BK-A", "BK-B"]) {
      p = applyEvent(p, ev("BOOKING_COMMITTED", { bookingId: `${id}-R`, sailingId: "BEA-201", incidentId: "rec_1" }, { quantity: 1, replaces_booking_id: id }));
      p = applyEvent(p, ev("BOOKING_SUPERSEDED", { bookingId: id, incidentId: "rec_1" }));
    }
    p = applyEvent(p, ev("RECOVERY_AWAITING_APPROVAL", { incidentId: "rec_1" }));
    const inc = p.incidents[0];
    expect([inc.confirmed, inc.affected]).toEqual([2, 3]);
    expect(inc.stage).toBe("AWAITING_APPROVAL");
  });

  it("different incidents never mix", () => {
    let p = applyEvent(base(), ev("RECOVERY_RUN_CREATED", { incidentId: "rec_1", sailingId: "ATL-101" }));
    p = applyEvent(p, ev("RECOVERY_RUN_CREATED", { incidentId: "rec_2", sailingId: "COV-301" }));
    p = applyEvent(p, ev("CLEARING_DONE", { incidentId: "rec_2" }));
    expect(p.incidents.find((i) => i.incidentId === "rec_1")!.stage).toBe("IMPACT_ASSESSED");
    expect(p.incidents.find((i) => i.incidentId === "rec_2")!.stage).toBe("ALLOCATION_PROPOSED");
  });
});
