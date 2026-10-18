import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, History, Network } from "lucide-react";
import { AllocationBoard } from "../components/Capacity";
import { Empty, Feed, Status } from "../components/common";
import { MaritimeMap } from "../components/MaritimeMap";
import { api } from "../data/api";
import type { AffectedBooking, IncidentDetail, IncidentSummary } from "../data/contracts";
import { buyerColor, clock, day, human, money } from "../data/format";
import { navigate, useLive, useUi } from "../data/store";

export const STAGES = [
  ["WITHDRAWN", "Withdrawn"], ["IMPACT_ASSESSED", "Impact assessed"], ["NEGOTIATING", "Negotiating"], ["CLEARING", "Clearing"],
  ["ALLOCATION_PROPOSED", "Allocation proposed"], ["HOLDING", "Holding"], ["AWAITING_APPROVAL", "Awaiting approval"],
  ["CONFIRMING", "Confirming"], ["RESOLVED", "Resolved"],
] as const;

const OBJECTIVE = [
  ["mustDeliverRecovered", "Maximize recovered must-deliver bookings", "max"],
  ["recovered", "Maximize whole recovered bookings", "max"],
  ["approvals", "Minimize required buyer approvals", "min"],
  ["incrementalCostMinor", "Minimize incremental simulated freight cost", "min"],
  ["arrivalRank", "Prefer earlier arrival (rank sum)", "min"],
] as const;

export function IncidentRibbon({ incident }: { incident: IncidentDetail }) {
  const reached = new Map(incident.stages.map((s) => [s.stage, s]));
  if (incident.withdrawal) reached.set("WITHDRAWN", { stage: "WITHDRAWN", at: incident.withdrawal.at, eventId: incident.withdrawal.eventId });
  const terminal = ["PARTIAL", "UNRESOLVED"].includes(incident.stage);
  const list = terminal ? [...STAGES.slice(0, -1), [incident.stage, human(incident.stage)] as const] : STAGES;
  return (
    <div style={{ display: "flex", gap: 4, overflowX: "auto", paddingBottom: 2 }} role="list" aria-label="Incident lifecycle">
      {list.map(([k, label]) => {
        const r = reached.get(k) || (k === incident.stage ? { at: incident.updatedAt } : undefined);
        const current = incident.stage === k;
        const color = k === "RESOLVED" ? "var(--confirmed-green)" : k === "UNRESOLVED" ? "var(--danger)" : k === "CLEARING" || k === "ALLOCATION_PROPOSED" ? "var(--solver-violet)"
          : ["HOLDING", "CONFIRMING"].includes(k) ? "var(--held-blue)" : k === "PARTIAL" ? "var(--text-secondary)" : "var(--withdrawal-amber)";
        return (
          <div key={k} role="listitem" style={{ flex: "1 0 104px", padding: "8px 10px", borderRadius: 9, border: `1px solid ${r ? color : "var(--border-soft)"}`,
                                                 background: current ? "var(--surface-2)" : "transparent", opacity: r ? 1 : 0.45 }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, color: r ? color : "var(--text-muted)" }}>{r ? "●" : "○"} {label}</div>
            <div className="mono muted" style={{ fontSize: 10.5 }}>{r?.at ? clock(r.at) : "no evidence yet"}</div>
          </div>
        );
      })}
    </div>
  );
}

function BuyerCard({ a, carrierName }: { a: AffectedBooking; carrierName: (id: string) => string }) {
  const { setInspect } = useUi();
  const latest = a.legs.filter((l) => l.attempt === Math.max(...a.legs.map((x) => x.attempt), 0));
  const c = buyerColor(a.buyerNode);
  return (
    <div className="panel" style={{ borderColor: a.state === "CONFIRMED" ? "rgba(84,214,155,.45)" : a.state === "AWAITING_APPROVAL" ? "rgba(245,174,89,.5)" : undefined, cursor: "pointer" }}
         onClick={() => setInspect({ kind: "booking", id: a.bookingId })}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ width: 26, height: 26, borderRadius: 7, display: "grid", placeItems: "center", border: `1.5px solid ${c}`, color: c, fontWeight: 700 }}>{(a.buyer || "?").replace("Buyer ", "")}</span>
        <div style={{ flex: 1 }}><div style={{ fontWeight: 600 }}>{a.buyer}</div><div className="mono muted" style={{ fontSize: 11 }}>{a.bookingId}</div></div>
        <Status s={a.state} />
      </div>
      <dl className="kv" style={{ gridTemplateColumns: "92px 1fr" }}>
        <dt>Cargo</dt><dd>{a.quantity}×20GP · was {a.previousSailingId}</dd>
        <dt>Deadline</dt><dd className="mono">{a.deadlineAt ? day(a.deadlineAt) : "—"}</dd>
        {latest.map((l) => (<Fragment key={`${l.sailingId}-${l.attempt}`}><dt>{l.status === "CONFIRMED" ? "Confirmed" : l.status === "HELD" ? "Held" : human(l.status)}</dt>
          <dd>{carrierName(l.carrierId)} {l.sailingId} · {l.quantity}×20GP · {money(l.quotedMinor)}{l.holdExpiresAt && l.status === "HELD" ? ` · hold until ${clock(l.holdExpiresAt, false)}` : ""}</dd></Fragment>))}
        {a.replacement && <><dt>Replacement</dt><dd><span className="mono">{a.replacement.bookingId}</span> committed · original superseded</dd></>}
      </dl>
      {a.state === "AWAITING_APPROVAL" && <div style={{ marginTop: 8, fontSize: 12, color: "var(--withdrawal-amber)" }}>Awaiting buyer in ASI:One{a.approval?.expiresAt ? ` · approval window until ${clock(a.approval.expiresAt, false)} UTC` : ""}. This dashboard cannot approve.</div>}
      {a.reason && <div style={{ marginTop: 8, fontSize: 12, color: "var(--danger)" }}>{a.reason}</div>}
    </div>
  );
}

export function FcfsVsJoint({ incident }: { incident: IncidentDetail }) {
  const cl = incident.clearing;
  if (!cl) return <div className="muted">The solver result appears once the final offer set is frozen and cleared.</div>;
  const card = (title: string, sub: string, n: number, list: { bookingId: string; sailingId: string; quantity: number }[], unrec: string[], color: string) => (
    <div className="panel" style={{ borderColor: color, flex: 1 }}>
      <div style={{ fontSize: 11, letterSpacing: ".1em", fontWeight: 700, color }}>{title}</div>
      <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>{sub}</div>
      <div style={{ fontSize: 30, fontWeight: 700 }} className="num">{n} <span className="muted" style={{ fontSize: 14 }}>/ {incident.affected} proposed</span></div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 6 }}>
        {list.map((a, i) => <div key={i} className="mono" style={{ fontSize: 11.5 }}>{a.bookingId} → {a.sailingId} ×{a.quantity}</div>)}
        {unrec.map((b) => <div key={b} className="mono" style={{ fontSize: 11.5, color: "var(--danger)" }}>{b} → no whole recovery</div>)}
      </div>
    </div>
  );
  return (
    <div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {card("FIRST-COME-FIRST-SERVED", "SIMULATED BASELINE (NOT EXECUTED) · same frozen offers & capacity", cl.baseline.recovered ?? 0, cl.baseline.assignments, cl.baseline.unrecovered, "var(--text-secondary)")}
        {card("JOINT OPTIMIZATION", "Five-stage OR-Tools objective · proposed allocation", cl.joint.recovered ?? 0, cl.joint.assignments, cl.joint.unrecovered, "var(--solver-violet)")}
      </div>
      <div style={{ marginTop: 10, fontSize: 13 }}>
        Actually confirmed (carrier-confirmed and committed): <strong className="num">{incident.confirmed} / {incident.affected}</strong>
        {incident.pending ? <span className="muted"> · {incident.pending} pending</span> : null}{incident.unresolved ? <span style={{ color: "var(--danger)" }}> · {incident.unresolved} unresolved</span> : null}
      </div>
    </div>
  );
}

export function WarRoom() {
  const { projection, events, subscribe } = useLive();
  const { route, setInspect } = useUi();
  const id = route.params.incident;
  const [list, setList] = useState<IncidentSummary[] | null>(null);
  const [inc, setInc] = useState<IncidentDetail | null>(null);
  const [now, setNow] = useState(Date.now());
  const pending = useRef<number | null>(null);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  useEffect(() => { api.incidents().then((r) => setList(r.incidents)).catch(() => setList([])); }, [projection?.incidents.length]);
  useEffect(() => {
    if (!id) { setInc(null); return; }
    const load = () => api.incident(id).then(setInc).catch(() => setInc(null));
    load();
    const t = setInterval(load, 10_000);
    const unsub = subscribe((e) => {  // hydrate on real incident events (debounced), never on a timer guess
      if (e.incidentId !== id && e.sailingId !== inc?.sailingId) return;
      if (pending.current) window.clearTimeout(pending.current);
      pending.current = window.setTimeout(load, 600);
    });
    return () => { clearInterval(t); unsub(); };
  }, [id, subscribe]); // eslint-disable-line react-hooks/exhaustive-deps

  const incEvents = useMemo(() => events.filter((e) => e.incidentId === id || (inc && e.sailingId === inc.sailingId && e.eventType === "SAILING_WITHDRAWN")), [events, id, inc]);

  if (!projection) return <div className="page"><Empty title="Loading…" /></div>;
  if (!id) {
    return (
      <div className="page">
        <h1 className="page-title">Disruption War Room</h1>
        <p className="page-sub">How the Exchange turns a carrier withdrawal into governed replacement commitments. Read-only.</p>
        {!list ? <Empty title="Loading incidents…" /> : !list.length ? (
          <div className="panel"><Empty title="No disruptions — the market is calm">A carrier-authorized withdrawal (carrier console) opens an incident here automatically. This view never creates one.</Empty></div>
        ) : (
          <div className="grid-3">{list.map((i) => (
            <div key={i.incidentId} className="panel" style={{ cursor: "pointer" }} onClick={() => navigate("warroom", { incident: i.incidentId })}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}><strong>{i.sailingId} withdrawal</strong><Status s={i.stage} /></div>
              <div className="mono muted" style={{ fontSize: 11 }}>{i.incidentId} · opened {clock(i.openedAt)}</div>
              <div style={{ marginTop: 8 }} className="num">{i.confirmed} confirmed / {i.affected} impacted{i.pending ? ` · ${i.pending} pending` : ""}{i.unresolved ? ` · ${i.unresolved} unresolved` : ""}</div>
            </div>))}</div>
        )}
      </div>
    );
  }
  if (!inc) return <div className="page"><Empty title="Loading incident…">Reading {id} from Tiger.</Empty></div>;
  const carrierName = (cid: string) => projection.carriers.find((c) => c.carrierId === cid)?.displayName || cid;
  const deadline = inc.negotiation.deadlineAt, closed = inc.negotiation.closedAt;
  const remaining = deadline && !closed ? Math.max(0, Math.round((new Date(deadline).getTime() - now) / 1000)) : null;
  const replacementSailings = [...new Set(inc.affectedBookings.flatMap((a) => a.legs.map((l) => l.sailingId)).concat(inc.clearing?.joint.assignments.map((x) => x.sailingId) || []))];
  const arcs = inc.affectedBookings.flatMap((a) => {
    const latest = a.legs.filter((l) => l.attempt === Math.max(...a.legs.map((x) => x.attempt), 0));
    const tgt = latest.length ? latest : (inc.clearing?.joint.assignments.filter((x) => x.bookingId === a.bookingId) || []).map((x) => ({ sailingId: x.sailingId, status: "PROPOSED" }));
    return tgt.map((l: any) => ({ from: inc.sailingId, to: l.sailingId, color: l.status === "CONFIRMED" ? "#54d69b" : l.status === "HELD" ? "#6f9ff0" : "#b49af0", dashed: l.status !== "CONFIRMED" }));
  });
  const headline = inc.stage === "RESOLVED" ? "RESOLVED" : inc.stage === "PARTIAL" ? "PARTIAL" : inc.stage === "UNRESOLVED" ? "UNRESOLVED" : "RUNNING";
  const obj = inc.clearing?.objective;

  return (
    <div className="page">
      <div className="panel" style={{ borderColor: headline === "RESOLVED" ? "rgba(84,214,155,.5)" : "rgba(245,174,89,.5)", background: headline === "RESOLVED" ? "rgba(84,214,155,.05)" : "rgba(245,174,89,.05)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          {headline === "RESOLVED" ? <CheckCircle2 color="var(--confirmed-green)" /> : <AlertTriangle color="var(--withdrawal-amber)" />}
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 22, fontWeight: 700 }}>Disruption: {inc.withdrawal?.carrierName} {inc.sailingId}</div>
            <div className="secondary" style={{ fontSize: 13 }}>
              Carrier-authorized withdrawal at {clock(inc.withdrawal?.at)} UTC · {inc.affected} affected · {inc.unaffectedCommitted} unaffected commitments preserved
            </div>
          </div>
          <Status s={headline} label={headline} />
          <span className="num" style={{ fontSize: 18, fontWeight: 700 }}>{inc.confirmed} / {inc.affected} confirmed</span>
          <button className="btn" onClick={() => navigate("universe", { incident: inc.incidentId })}><Network size={14} />Inspect negotiations</button>
          <button className="btn violet" onClick={() => navigate("replay", { incident: inc.incidentId })}><History size={14} />Replay incident</button>
        </div>
        {inc.withdrawal?.reason && <div className="muted" style={{ marginTop: 8, fontSize: 12.5 }}>Reason given by the carrier: {inc.withdrawal.reason}</div>}
      </div>
      <IncidentRibbon incident={inc} />
      <div className="grid-2">
        <div className="panel">
          <div className="panel-title">Sea + capacity <span className="count">· one tile per 20GP slot</span></div>
          <MaritimeMap projection={projection} at={new Date(now).toISOString()} selectedSailing={inc.sailingId} focusSailings={replacementSailings} arcs={arcs} compactLegend
                       onSelect={(s) => setInspect({ kind: "sailing", id: s.sailingId })} />
          <div style={{ marginTop: 8 }}><AllocationBoard incident={inc} projection={projection} /></div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="panel">
            <div className="panel-title">Recovery timeline</div>
            <ol style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
              <li><strong>Withdrawn & assessed.</strong> {inc.affected} bookings displaced from {inc.sailingId}; {inc.board.length} stranded jobs posted to the public board (no budgets or identities).</li>
              <li><strong>Negotiations in parallel.</strong> {inc.negotiation.sessions} sessions · {Object.entries(inc.negotiation.byStatus).filter(([, n]) => n).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(" · ") || "opening"}.{" "}
                {closed ? <span className="secondary">Closed {inc.negotiation.closedEarly ? "early" : "at the deadline"} after {inc.negotiation.seconds ?? "?"} s (60 s maximum).</span>
                  : remaining !== null ? <span style={{ color: "var(--withdrawal-amber)" }}>Deadline in {remaining}s (maximum, not a wait).</span> : null}</li>
              <li><strong>Offers checked & jointly cleared.</strong> {inc.clearing ? <>{inc.clearing.offersValid ?? "?"} final offers passed validation; cleared at {clock(inc.clearing.at)} UTC.</> : <span className="muted">Waiting for the frozen offer set.</span>}</li>
              <li><strong>Holds / approvals / confirmation.</strong> {inc.confirmed} confirmed · {inc.affectedBookings.filter((a) => a.state === "AWAITING_APPROVAL").length} awaiting ASI:One approval · {inc.affectedBookings.filter((a) => a.state === "HELD" || a.state === "CONFIRMING").length} held/confirming · {inc.unresolved} unresolved.</li>
            </ol>
          </div>
          <div className="panel">
            <div className="panel-title">Solver priorities <span className="count">· lexicographic</span></div>
            {OBJECTIVE.map(([k, label, dir], i) => (
              <div key={k} style={{ display: "grid", gridTemplateColumns: "22px 1fr auto", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--border-soft)", alignItems: "center" }}>
                <span className="mono" style={{ color: "var(--solver-violet)" }}>{i + 1}</span>
                <span style={{ fontSize: 12.5 }}>{label}</span>
                <span className="mono">{obj ? (k === "incrementalCostMinor" ? money((obj as any)[k]) : (obj as any)[k]) : "—"}<span className="muted"> {dir}</span></span>
              </div>
            ))}
            {!obj && <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>Stage values appear when the backend records the solver result; no fake progress is shown.</div>}
          </div>
        </div>
      </div>
      <div className="panel">
        <div className="panel-title">Affected buyers <span className="count">· proposed ≠ held ≠ confirmed</span></div>
        <div className="grid-3">{inc.affectedBookings.map((a) => <BuyerCard key={a.bookingId} a={a} carrierName={carrierName} />)}</div>
      </div>
      <div className="grid-2">
        <div className="panel"><div className="panel-title">Solver comparison</div><FcfsVsJoint incident={inc} /></div>
        <div className="panel flush" style={{ maxHeight: 360, display: "flex", flexDirection: "column" }}>
          <div className="panel-title" style={{ padding: "14px 16px 6px", margin: 0 }}>Audit events <span className="count">· {incEvents.length}</span>
            <button className="linklike" style={{ marginLeft: "auto" }} onClick={() => setInspect({ kind: "incident", id: inc.incidentId })}>evidence <ArrowRight size={11} /></button></div>
          <div style={{ overflow: "auto" }}><Feed events={incEvents} max={200} /></div>
        </div>
      </div>
    </div>
  );
}
