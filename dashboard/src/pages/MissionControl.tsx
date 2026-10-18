import { useEffect, useMemo, useState } from "react";
import { ArrowRight } from "lucide-react";
import { AgentNetwork } from "../components/AgentNetwork";
import { SailingStrip } from "../components/Capacity";
import { Empty, Feed, Kpi } from "../components/common";
import { MaritimeMap } from "../components/MaritimeMap";
import { navigate, useLive, useUi } from "../data/store";

export function MissionControl() {
  const { projection, events, subscribe } = useLive();
  const { setInspect, reduceMotion } = useUi();
  const [selected, setSelected] = useState<string | null>(null);
  const [now, setNow] = useState(new Date().toISOString());
  useEffect(() => { const t = setInterval(() => setNow(new Date().toISOString()), 5000); return () => clearInterval(t); }, []);

  // Preview: the most recent run's sessions (a booking request or a recovery), so the network is real and legible.
  const preview = useMemo(() => {
    if (!projection) return [];
    const ns = projection.negotiations;
    const open = ns.filter((s) => s.status === "OPEN");
    if (open.length) return open;
    const last = [...ns].sort((a, b) => (b.openedAt || "").localeCompare(a.openedAt || ""))[0];
    if (!last) return [];
    return ns.filter((s) => (last.incidentId ? s.incidentId === last.incidentId : s.requestId === last.requestId));
  }, [projection]);

  if (!projection) return <div className="page"><Empty title="Loading the market…">Hydrating the current snapshot from Tiger.</Empty></div>;
  const m = projection.metrics;
  const open = projection.incidents.filter((i) => !["RESOLVED", "PARTIAL", "UNRESOLVED"].includes(i.stage));
  return (
    <div className="page">
      <div className="page-head">
        <div style={{ flex: 1 }}>
          <h1 className="page-title">Mission Control</h1>
          <p className="page-sub">Is the market healthy, what are the agents doing, and is anything disrupted?</p>
        </div>
        {open.length > 0 && <button className="btn amber" onClick={() => navigate("warroom", { incident: open[0].incidentId })}>Incident {open[0].sailingId} · {open[0].stage.replace(/_/g, " ").toLowerCase()} <ArrowRight size={14} /></button>}
      </div>
      <div className="kpis">
        <Kpi label="Active carrier agents" value={m.activeCarriers} note="independent uAgents" />
        <Kpi label="Active sailings" value={m.activeSailings} note={`${projection.sailings.length - m.activeSailings} withdrawn`} />
        <Kpi label="Active negotiations" value={m.activeNegotiations} note={m.activeNegotiations ? "sessions open now" : "market ready"} />
        <Kpi label="Confirmed bookings" value={m.confirmedBookings} note="carrier-confirmed & committed" />
        <Kpi label="Incidents needing attention" value={m.incidentsOpen} attention={m.incidentsOpen > 0} note={m.incidentsOpen ? "recovery in progress" : "none"} />
      </div>
      <div className="grid-2">
        <div className="panel flush" style={{ position: "relative" }}>
          <div style={{ position: "absolute", top: 14, left: 16, zIndex: 1 }} className="panel-title">Maritime market <span className="count">· click a vessel</span></div>
          <MaritimeMap projection={projection} at={now} selectedSailing={selected} onSelect={(s) => { setSelected(s.sailingId); setInspect({ kind: "sailing", id: s.sailingId }); }} />
        </div>
        <div className="panel flush" style={{ display: "flex", flexDirection: "column", minHeight: 360, maxHeight: 560 }}>
          <div className="panel-title" style={{ padding: "14px 16px 6px", margin: 0 }}>Live market feed <span className="count">· {events.length} recent</span></div>
          <div style={{ flex: 1, overflow: "auto" }}><Feed events={events} max={50} /></div>
        </div>
      </div>
      <div className="grid-2-even">
        <div className="panel" style={{ cursor: "pointer" }} onClick={() => navigate("universe")}>
          <div className="panel-title">Agent Universe preview <span className="count">· {preview.length} session{preview.length === 1 ? "" : "s"} · open full view →</span></div>
          <AgentNetwork sessions={preview} carriers={projection.carriers} subscribe={subscribe} reduceMotion={reduceMotion} compact height={300} />
        </div>
        <div className="panel">
          <div className="panel-title">Sailing capacity <span className="count">· simulated 20GP slots</span></div>
          <SailingStrip projection={projection} limit={9} selected={selected} onPick={(s) => { setSelected(s.sailingId); setInspect({ kind: "sailing", id: s.sailingId }); }} />
        </div>
      </div>
    </div>
  );
}
