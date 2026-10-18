// Time Machine engine (HX-OPS-001 §6.6): checkpoint + ordered events folded up to a virtual time. Pure.
import type { Projection, PublicMarketEvent, ReplayBundle } from "./contracts";
import { applyEvent, byTime, metricsOf } from "./reducer";

export interface PreparedReplay {
  bundle: ReplayBundle;
  events: PublicMarketEvent[];      // de-duplicated, ordered
  start: string;                    // checkpoint time
  end: string;
  frames: { at: string; projection: Projection }[];  // projection after each event (memoised fold)
  base: Projection;
}

export function prepare(bundle: ReplayBundle): PreparedReplay | null {
  if (!bundle.checkpoint) return null;
  const seen = new Set<string>();
  const events = bundle.events.filter((e) => (seen.has(e.eventId) ? false : (seen.add(e.eventId), true))).sort(byTime);
  const base: Projection = { ...bundle.checkpoint.projection, asOf: bundle.checkpoint.capturedAt };
  base.metrics = metricsOf(base);
  const frames: PreparedReplay["frames"] = [];
  let p = base;
  for (const e of events) { p = applyEvent(p, e); frames.push({ at: e.eventTime, projection: p }); }
  // Timeline starts at the requested window (e.g. 90 s before a withdrawal) or at the checkpoint if that is later.
  const start = bundle.checkpoint.capturedAt > bundle.coverage.start ? bundle.checkpoint.capturedAt : bundle.coverage.start;
  const end = events.length && events[events.length - 1].eventTime > bundle.coverage.end ? events[events.length - 1].eventTime : bundle.coverage.end;
  return { bundle, events, start, end, frames, base };
}

/** Projection as of virtual time `at` (inclusive). Events after `at` are invisible: no future knowledge. */
export function projectAt(r: PreparedReplay, at: string): Projection {
  let lo = 0, hi = r.frames.length - 1, idx = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (r.frames[mid].at <= at) { idx = mid; lo = mid + 1; } else hi = mid - 1; }
  const p = idx < 0 ? r.base : r.frames[idx].projection;
  return { ...p, asOf: at };
}

export const eventsUpTo = (r: PreparedReplay, at: string) => r.events.filter((e) => e.eventTime <= at);
export const nextEventAfter = (r: PreparedReplay, at: string) => r.events.find((e) => e.eventTime > at) || null;
export const previousEventBefore = (r: PreparedReplay, at: string) => { const xs = r.events.filter((e) => e.eventTime < at); return xs[xs.length - 1] || null; };

/** Cross-check the reconstructed terminal state against Tiger's current state (§6.5.5). */
export function terminalMismatches(r: PreparedReplay): string[] {
  if (!r.bundle.reference.terminal) return [];
  const end = r.frames.length ? r.frames[r.frames.length - 1].projection : r.base;
  const out: string[] = [];
  for (const ref of r.bundle.reference.bookings) {
    const b = end.bookings.find((x) => x.bookingId === ref.bookingId);
    if (b && b.status !== ref.status) out.push(`${ref.bookingId}: replay ${b.status} vs Tiger ${ref.status}`);
  }
  for (const ref of r.bundle.reference.sailings) {
    const s = end.sailings.find((x) => x.sailingId === ref.sailingId);
    if (s && (s.status !== ref.status || s.committedUnits !== ref.committedUnits)) out.push(`${ref.sailingId}: replay ${s.status}/${s.committedUnits} vs Tiger ${ref.status}/${ref.committedUnits}`);
  }
  return out;
}

export function keyMoments(r: PreparedReplay) {
  const find = (pred: (e: PublicMarketEvent) => boolean) => r.events.find(pred)?.eventTime;
  const last = (pred: (e: PublicMarketEvent) => boolean) => { const xs = r.events.filter(pred); return xs[xs.length - 1]?.eventTime; };
  return [
    { label: "Withdrawal", at: find((e) => e.eventType === "SAILING_WITHDRAWN") },
    { label: "First counter", at: find((e) => e.eventType === "COUNTER_SENT" && e.sessionKind === "RECOVERY") || find((e) => e.eventType === "COUNTER_SENT") },
    { label: "Clear", at: find((e) => e.eventType === "CLEARING_DONE") },
    { label: "First hold", at: find((e) => e.eventType === "HOLD_GRANTED" && !!e.incidentId) || find((e) => e.eventType === "HOLD_GRANTED") },
    { label: "Final confirmation", at: last((e) => e.eventType === "RECOVERY_LEG_CONFIRMED") },
  ].filter((m) => !!m.at) as { label: string; at: string }[];
}

export const PHASES: { key: string; label: string; color: string; match: (e: PublicMarketEvent) => boolean }[] = [
  { key: "withdrawal", label: "Withdrawal", color: "var(--withdrawal-amber)", match: (e) => ["SAILING_WITHDRAWN", "BOOKING_DISRUPTED", "RECOVERY_RUN_CREATED", "RECOVERY_BOARD_POSTED"].includes(e.eventType) },
  { key: "negotiation", label: "Negotiations", color: "var(--agent-cyan)", match: (e) => ["NEGOTIATION_OPENED", "OFFER_SENT", "COUNTER_SENT", "OFFER_ACCEPTED", "OFFER_REJECTED", "NEGOTIATION_CLOSED", "RECOVERY_NEGOTIATING"].includes(e.eventType) },
  { key: "clearing", label: "Clearing", color: "var(--solver-violet)", match: (e) => ["CLEARING_STARTED", "CLEARING_DONE", "RECOVERY_PROPOSED"].includes(e.eventType) },
  { key: "holds", label: "Holds / approvals", color: "var(--held-blue)", match: (e) => ["HOLD_GRANTED", "HOLD_RELEASED", "RECOVERY_HOLDING", "RECOVERY_LEG_HELD", "RECOVERY_APPROVAL_REQUESTED", "APPROVAL_DECIDED", "RECOVERY_AWAITING_APPROVAL"].includes(e.eventType) },
  { key: "confirm", label: "Confirmations", color: "var(--confirmed-green)", match: (e) => ["BOOKING_COMMITTED", "BOOKING_SUPERSEDED", "RECOVERY_LEG_CONFIRMED", "RECOVERY_COMPLETED", "RECOVERY_PARTIAL"].includes(e.eventType) },
];
export const phaseOf = (e: PublicMarketEvent) => PHASES.find((p) => p.match(e));
