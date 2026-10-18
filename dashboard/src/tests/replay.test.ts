// Time Machine exactness (HX-OPS-001 §15.3) on a REAL recorded incident bundle (rec_af10aeb14a41: Atlas withdrew
// ATL-101, 3 buyers negotiated with carrier agents, joint clearing, holds, 2 ASI:One-style approvals, confirmations).
import { describe, expect, it } from "vitest";
import bundleJson from "./fixtures/replay_bundle.json";
import type { ReplayBundle } from "../data/contracts";
import { applyEvent } from "../data/reducer";
import { keyMoments, nextEventAfter, prepare, previousEventBefore, projectAt, terminalMismatches } from "../data/replay";

const bundle = bundleJson as unknown as ReplayBundle;
const r = prepare(bundle)!;
const just = (iso: string, ms: number) => new Date(new Date(iso).getTime() + ms).toISOString();
const first = (t: string, pred: (e: any) => boolean = () => true) => r.events.find((e) => e.eventType === t && pred(e))!;

describe("replay of a recorded incident", () => {
  it("has a checkpoint and complete coverage", () => {
    expect(r).toBeTruthy();
    expect(bundle.coverage.complete).toBe(true);
    expect(r.events.length).toBeGreaterThan(20);
  });

  it("reconstructs a terminal state that matches Tiger", () => {
    expect(bundle.reference.terminal).toBe(true);
    expect(terminalMismatches(r)).toEqual([]);
  });

  it("is deterministic: repeated seeks and playback-vs-seek agree", () => {
    const t = first("CLEARING_DONE").eventTime;
    const a = projectAt(r, t), b = projectAt(r, t);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
    let p = r.base;
    for (const e of r.events) { if (e.eventTime > t) break; p = applyEvent(p, e); }  // "play" to t
    expect(JSON.stringify({ ...p, asOf: t })).toBe(JSON.stringify(a));
    const back = projectAt(r, r.start), fwd = projectAt(r, t);  // scrub back then forward: no drift
    expect(JSON.stringify(projectAt(r, t))).toBe(JSON.stringify(fwd));
    expect(back.sailings.find((s) => s.sailingId === "ATL-101")!.status).toBe("ACTIVE");
  });

  it("hides a quote before it was sent and shows it right after", () => {
    const offer = first("OFFER_SENT", (e) => e.sessionKind === "RECOVERY");
    const before = projectAt(r, just(offer.eventTime, -1)).negotiations.find((s) => s.sessionId === offer.sessionId);
    const after = projectAt(r, offer.eventTime).negotiations.find((s) => s.sessionId === offer.sessionId)!;
    expect(before?.latestMinor ?? null).toBeNull();
    expect(after.latestMinor).toBe(offer.publicPayload.total_price_minor);
  });

  it("shows no solver allocation before CLEARING_DONE", () => {
    const clear = first("CLEARING_DONE");
    const inc = (at: string) => projectAt(r, at).incidents.find((i) => i.incidentId === bundle.incidentId)!;
    expect(["IMPACT_ASSESSED", "NEGOTIATING", "CLEARING"]).toContain(inc(just(clear.eventTime, -1)).stage);
    expect(inc(clear.eventTime).stage).toBe("ALLOCATION_PROPOSED");
  });

  it("after a hold but before the commit, capacity is held and nothing is green", () => {
    const hold = first("HOLD_GRANTED", (e) => !!e.incidentId);
    const commit = first("BOOKING_COMMITTED", (e) => !!e.publicPayload.replaces_booking_id);
    expect(hold.eventTime < commit.eventTime).toBe(true);
    const p = projectAt(r, hold.eventTime);
    expect(p.sailings.find((s) => s.sailingId === hold.sailingId)!.heldUnits).toBeGreaterThan(0);
    expect(p.bookings.some((b) => b.replacesBookingId)).toBe(false);
    expect(p.incidents.find((i) => i.incidentId === bundle.incidentId)!.confirmed).toBe(0);
  });

  it("after the commit, the replacement is COMMITTED and the original SUPERSEDED", () => {
    const sup = first("BOOKING_SUPERSEDED");
    const p = projectAt(r, sup.eventTime);
    const original = p.bookings.find((b) => b.bookingId === sup.bookingId)!;
    expect(original.status).toBe("SUPERSEDED");
    expect(p.bookings.some((b) => b.replacesBookingId === original.bookingId && b.status === "COMMITTED")).toBe(true);
  });

  it("never claims 3/3 while an approval is pending", () => {
    const firstConfirm = first("RECOVERY_LEG_CONFIRMED");
    const inc = projectAt(r, firstConfirm.eventTime).incidents.find((i) => i.incidentId === bundle.incidentId)!;
    expect(inc.stage).not.toBe("RESOLVED");
    const end = projectAt(r, r.end).incidents.find((i) => i.incidentId === bundle.incidentId)!;
    expect(end.stage).toBe("RESOLVED");
    expect(end.confirmed).toBe(end.affected);
  });

  it("steps between events and exposes key moments in order", () => {
    // Walk the whole history with "next event": strictly increasing times, ending at the last event.
    let at = just(r.events[0].eventTime, -1), steps = 0, last = "";
    for (let e = nextEventAfter(r, at); e; e = nextEventAfter(r, e.eventTime)) { expect(e.eventTime > last).toBe(true); last = e.eventTime; steps++; }
    expect(last).toBe(r.events[r.events.length - 1].eventTime);
    expect(steps).toBe(new Set(r.events.map((e) => e.eventTime)).size);
    const w = first("SAILING_WITHDRAWN");
    expect(previousEventBefore(r, just(w.eventTime, 1))!.eventTime <= w.eventTime).toBe(true);
    const moments = keyMoments(r).map((m) => m.at);
    expect([...moments].sort()).toEqual(moments);
  });

  it("contains no private fields anywhere in the bundle", () => {
    const blob = JSON.stringify(bundleJson);
    for (const k of ["max_total_price_minor", "recovery_allowance_minor", "max_additional_cost_minor", "\"must_deliver\"", "buyer_wallet",
                     "client_secret", "\"policy\"", "minimum_price_per_unit", "demo_buyer_", "carrier_signature"]) expect(blob).not.toContain(k);
  });
});
