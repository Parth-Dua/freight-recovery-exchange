import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Maximize2 } from "lucide-react";
import { AgentNetwork, SessionLanes } from "../components/AgentNetwork";
import { Empty, Status } from "../components/common";
import { NegotiationTimeline } from "../components/NegotiationTimeline";
import { api } from "../data/api";
import type { IncidentDetail, PublicSession } from "../data/contracts";
import { clock } from "../data/format";
import { navigate, useLive, useUi } from "../data/store";

type Scope = { kind: "latest" } | { kind: "incident"; id: string } | { kind: "request"; id: string } | { kind: "all" };

export function AgentUniverse() {
  const { projection, events, subscribe } = useLive();
  const { route, reduceMotion } = useUi();
  const [scope, setScope] = useState<Scope>(route.params.incident ? { kind: "incident", id: route.params.incident } : { kind: "latest" });
  const [selected, setSelected] = useState<string | null>(route.params.session || null);
  const [buyer, setBuyer] = useState("all");
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(t); }, []);

  const runs = useMemo(() => {
    const ns = projection?.negotiations || [];
    const keyed = new Map<string, { kind: "incident" | "request"; id: string; at: string; n: number }>();
    for (const s of ns) {
      const k = s.incidentId ? `i:${s.incidentId}` : `r:${s.requestId}`;
      const cur = keyed.get(k) || { kind: s.incidentId ? "incident" : "request", id: (s.incidentId || s.requestId) as string, at: s.openedAt || "", n: 0 };
      cur.n++; if ((s.openedAt || "") > cur.at) cur.at = s.openedAt || "";
      keyed.set(k, cur);
    }
    return [...keyed.values()].sort((a, b) => b.at.localeCompare(a.at));
  }, [projection?.negotiations]);

  const effective: Scope = scope.kind === "latest" && runs[0] ? { kind: runs[0].kind, id: runs[0].id } as Scope : scope;
  const sessions: PublicSession[] = useMemo(() => {
    const ns = projection?.negotiations || [];
    let out = ns;
    if (effective.kind === "incident") out = ns.filter((s) => s.incidentId === effective.id);
    else if (effective.kind === "request") out = ns.filter((s) => s.requestId === effective.id && !s.incidentId);
    if (buyer !== "all") out = out.filter((s) => s.buyerNode === buyer);
    return out;
  }, [projection?.negotiations, effective.kind, (effective as any).id, buyer]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (effective.kind !== "incident") { setIncident(null); return; }
    api.incident(effective.id).then(setIncident).catch(() => setIncident(null));
  }, [effective.kind, (effective as any).id, projection?.cursor]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!projection) return <div className="page"><Empty title="Loading…" /></div>;
  const counts = { open: 0, done: 0, rejected: 0, expired: 0 };
  for (const s of sessions) { if (s.status === "OPEN") counts.open++; else if (s.status === "REJECTED") counts.rejected++; else if (s.status === "EXPIRED") counts.expired++; else counts.done++; }
  const buyers = [...new Map(sessions.concat(projection.negotiations.filter((s) => effective.kind === "incident" ? s.incidentId === (effective as any).id : s.requestId === (effective as any).id)).map((s) => [s.buyerNode, s.buyer])).entries()];
  const deadline = incident?.negotiation.deadlineAt;
  const closed = incident?.negotiation.closedAt;
  const remaining = deadline && !closed ? Math.max(0, Math.round((new Date(deadline).getTime() - now) / 1000)) : null;
  const clearing = incident && ["CLEARING"].includes(incident.stage) ? "clearing" : counts.open ? "pairing" : "idle";
  const sel = sessions.find((s) => s.sessionId === selected) || null;
  const carrierName = (id: string) => projection.carriers.find((c) => c.carrierId === id)?.displayName || id;

  return (
    <div className="page">
      <div className="page-head">
        <div style={{ flex: 1 }}>
          <h1 className="page-title">Agent Universe</h1>
          <p className="page-sub">Real buyer-agent ↔ carrier-agent negotiations, in parallel. Every edge is a persisted session; every packet a recorded message.</p>
        </div>
        <select className="select" value={effective.kind === "incident" || effective.kind === "request" ? `${effective.kind}:${(effective as any).id}` : scope.kind}
                onChange={(e) => { const v = e.target.value; setSelected(null); setBuyer("all"); if (v === "all") setScope({ kind: "all" }); else { const [k, id] = v.split(":"); setScope({ kind: k as "incident" | "request", id }); } }}>
          {runs.map((r) => <option key={`${r.kind}:${r.id}`} value={`${r.kind}:${r.id}`}>{r.kind === "incident" ? `Recovery ${r.id}` : `Booking ${r.id}`} · {r.n} sessions · {clock(r.at, false)}</option>)}
          <option value="all">All sessions</option>
        </select>
        <select className="select" value={buyer} onChange={(e) => setBuyer(e.target.value)}>
          <option value="all">All buyers</option>
          {buyers.map(([node, label]) => <option key={node || ""} value={node || ""}>{label}</option>)}
        </select>
        {effective.kind === "incident" && <button className="btn amber" onClick={() => navigate("warroom", { incident: (effective as any).id })}><AlertTriangle size={14} />Jump to War Room</button>}
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <span className="pill">{effective.kind === "incident" ? "RECOVERY RUN" : effective.kind === "request" ? "BOOKING RUN" : "ALL RUNS"}</span>
        <span className="pill live">{counts.open} active</span>
        <span className="pill">{counts.done} finalized</span>
        <span className="pill">{counts.rejected} declined</span>
        <span className="pill">{counts.expired} timed out</span>
        {incident && (closed ? <span className="pill">Negotiations closed {incident.negotiation.closedEarly ? "early" : "at deadline"} · {incident.negotiation.seconds ?? "?"} s of 60 s max</span>
          : remaining !== null && <span className="pill warn">Recovery deadline in {remaining}s (60 s maximum, closes early when all sessions end)</span>)}
      </div>
      <div className="grid-2">
        <div className="panel" style={{ padding: 8 }}>
          {sessions.length ? <AgentNetwork sessions={sessions} carriers={projection.carriers} selected={selected} onSelect={setSelected} subscribe={subscribe}
                                             exchangeActive={clearing as any} reduceMotion={reduceMotion} height={520} />
            : <Empty title="No active negotiations; market ready">Start a cargo request in ASI:One or withdraw a sailing in the carrier console to see agents negotiate.</Empty>}
        </div>
        <div className="panel" style={{ overflow: "auto", maxHeight: 560 }}>
          <div className="panel-title">Negotiation detail</div>
          {sel ? <NegotiationTimeline sessionId={sel.sessionId} carrierName={carrierName(sel.carrierId)} refreshKey={`${sel.status}-${sel.round}-${sel.latestMinor}`} />
            : <div>
              <div className="muted" style={{ marginBottom: 10 }}>Select an edge or a lane to inspect its exact offers and counters.</div>
              <table className="t"><thead><tr><th>Buyer ↔ carrier</th><th>Status</th></tr></thead><tbody>
                {sessions.map((s) => <tr key={s.sessionId} className="click" onClick={() => setSelected(s.sessionId)}><td>{s.buyer} ↔ {carrierName(s.carrierId)}</td><td><Status s={s.status} /></td></tr>)}
              </tbody></table>
            </div>}
        </div>
      </div>
      <div className="panel">
        <div className="panel-title"><Maximize2 size={13} /> Concurrent session lanes <span className="count">· recorded message times; overlap = real parallelism</span></div>
        {sessions.length ? <SessionLanes sessions={sessions} events={events} selected={selected} onSelect={setSelected} /> : <div className="muted">No sessions in this scope.</div>}
      </div>
    </div>
  );
}
