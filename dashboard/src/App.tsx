import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, BarChart3, Clock3, Gauge, Network, PanelLeftClose, PanelLeftOpen, Waves, X } from "lucide-react";
import { Inspector } from "./components/common";
import { ago } from "./data/format";
import { LiveProvider, UiProvider, href, navigate, useLive, useUi } from "./data/store";
import { MissionControl } from "./pages/MissionControl";
import { AgentUniverse } from "./pages/AgentUniverse";
import { WarRoom } from "./pages/WarRoom";
import { TimeMachine } from "./pages/TimeMachine";
import { Intelligence } from "./pages/Intelligence";
import type { PublicMarketEvent } from "./data/contracts";

const NAV = [
  { page: "mission", n: "01", label: "Mission Control", icon: Gauge },
  { page: "universe", n: "02", label: "Agent Universe", icon: Network },
  { page: "warroom", n: "03", label: "War Room", icon: AlertTriangle },
  { page: "replay", n: "04", label: "Time Machine", icon: Clock3 },
  { page: "intel", n: "05", label: "Intelligence", icon: BarChart3 },
] as const;

function Shell() {
  const { route, reduceMotion, setReduceMotion, demoFocus, setDemoFocus, setInspect } = useUi();
  const live = useLive();
  const [collapsed, setCollapsed] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [beacon, setBeacon] = useState<{ sailingId: string; carrier: string; affected: number; incidentId?: string } | null>(null);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  // Incident Beacon: only on a live, persisted carrier-authorized withdrawal; never navigates unless Demo Focus is on.
  useEffect(() => live.subscribe((e: PublicMarketEvent) => {
    if (e.eventType === "SAILING_WITHDRAWN") {
      const carrier = live.projection?.carriers.find((c) => c.carrierId === e.carrierId)?.displayName || e.carrierId || "";
      setBeacon({ sailingId: e.sailingId || "", carrier, affected: e.publicPayload.disrupted_bookings || 0 });
    }
    if (e.eventType === "RECOVERY_RUN_CREATED" && e.incidentId) {
      setBeacon((b) => (b && b.sailingId === e.sailingId ? { ...b, incidentId: e.incidentId! } : b));
      if (demoFocus) window.setTimeout(() => navigate("warroom", { incident: e.incidentId }), 500);
    }
  }), [live, demoFocus]);

  const openIncidents = live.projection?.metrics.incidentsOpen || 0;
  const replay = route.page === "replay";
  const stale = live.conn !== "live";
  const page = useMemo(() => {
    switch (route.page) {
      case "universe": return <AgentUniverse />;
      case "warroom": return <WarRoom />;
      case "replay": return <TimeMachine />;
      case "intel": return <Intelligence />;
      default: return <MissionControl />;
    }
  }, [route.page]);

  return (
    <div className={`shell ${collapsed ? "collapsed" : ""} ${reduceMotion ? "reduce-motion" : ""}`}>
      <header className="header">
        <div className="brand">
          <div className="brand-mark">HX</div>
          <div><div className="brand-name">HORMUZ EXCHANGE</div><div className="brand-sub">Mission Control · read-only</div></div>
        </div>
        {replay ? <span className="pill replay"><Clock3 size={13} />REPLAY</span>
          : <span className={`pill ${stale ? "warn" : "live"}`}><span className={`dot ${stale ? "" : "pulse"}`} />{stale ? (live.conn === "connecting" ? "CONNECTING" : "UPDATES PAUSED") : "MARKET LIVE"}</span>}
        <span className="pill sim">SIMULATED MARKET</span>
        <div className="header-spacer" />
        <span className="muted" style={{ fontSize: 12 }} title="Time of the last verified market event">
          {stale && live.conn !== "connecting" ? "Reconnecting · " : ""}Last event {ago(live.lastEventAt, now)}
        </span>
        <span className="mono muted">{new Date(now).toISOString().slice(11, 19)} UTC</span>
        <label className="muted" style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center", cursor: "pointer" }} title="Presenter mode: opens the War Room on a new withdrawal (navigation only)">
          <input type="checkbox" checked={demoFocus} onChange={(e) => setDemoFocus(e.target.checked)} />Demo focus
        </label>
        <label className="muted" style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center", cursor: "pointer" }}>
          <input type="checkbox" checked={reduceMotion} onChange={(e) => setReduceMotion(e.target.checked)} />Reduce motion
        </label>
      </header>
      <nav className="nav" aria-label="Main">
        {NAV.map((n) => {
          const Icon = n.icon;
          return (
            <button key={n.page} className={`nav-item ${route.page === n.page ? "active" : ""}`} onClick={() => { window.location.hash = href(n.page); }} aria-current={route.page === n.page}>
              <span className="nav-index">{n.n}</span><Icon size={17} /><span className="nav-label">{n.label}</span>
              {n.page === "warroom" && openIncidents > 0 && <span className="nav-badge" title="Incidents needing attention">{openIncidents}</span>}
            </button>
          );
        })}
        <div className="nav-foot">
          <button className="icon-btn" onClick={() => setCollapsed((c) => !c)} aria-label="Toggle navigation">{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button>
          <div className="nav-foot-text" style={{ marginTop: 10, lineHeight: 1.5 }}>Observer only: no booking, cancel, approval or payment actions exist here.</div>
        </div>
      </nav>
      <main className="main">{page}</main>
      <footer className="footer">
        <Activity size={13} />
        <span>Stream {live.conn}{live.caughtUp ? ` · ${live.caughtUp} events caught up` : ""}</span>
        <span>· snapshot {ago(live.lastSnapshotAt, now)}</span>
        <span className="spacer" />
        <button className="linklike" onClick={() => setInspect({ kind: "provenance", title: "Data provenance", provenance: { queryId: "overview", source: "See Market Intelligence for per-chart provenance" } as any })}>Data provenance</button>
        <span>·</span>
        <span><Waves size={12} style={{ verticalAlign: -2 }} /> Powered by TigerData</span>
      </footer>
      {beacon && !replay && (
        <div className="beacon" role="status">
          <AlertTriangle size={18} color="var(--withdrawal-amber)" />
          <div className="beacon-text"><b>{beacon.carrier} {beacon.sailingId}</b> · carrier-authorized withdrawal · {beacon.affected} affected · Recovery running</div>
          <button className="btn amber" onClick={() => { navigate("warroom", { incident: beacon.incidentId }); setBeacon(null); }}>Open War Room</button>
          <button className="icon-btn" onClick={() => setBeacon(null)} aria-label="Dismiss"><X size={15} /></button>
        </div>
      )}
      <Inspector />
    </div>
  );
}

export default function App() {
  return <UiProvider><LiveProvider><Shell /></LiveProvider></UiProvider>;
}
