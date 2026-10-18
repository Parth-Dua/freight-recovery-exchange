import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw, Radio } from "lucide-react";
import { AgentNetwork, SessionLanes } from "../components/AgentNetwork";
import { CapacityBar } from "../components/Capacity";
import { Empty, Feed, Status } from "../components/common";
import { MaritimeMap } from "../components/MaritimeMap";
import { NegotiationTimeline } from "../components/NegotiationTimeline";
import { api } from "../data/api";
import type { IncidentSummary, Projection, PublicMarketEvent } from "../data/contracts";
import { buyerColor, clock } from "../data/format";
import { PHASES, keyMoments, nextEventAfter, phaseOf, prepare, previousEventBefore, projectAt, terminalMismatches, type PreparedReplay } from "../data/replay";
import { navigate, useLive, useUi } from "../data/store";

type View = "map" | "network" | "capacity" | "split";
const iso = (ms: number) => new Date(ms).toISOString();

function ReplayCapacity({ p, incidentSailing }: { p: Projection; incidentSailing?: string | null }) {
  const involved = new Set<string>(incidentSailing ? [incidentSailing] : []);
  p.bookings.filter((b) => b.replacesBookingId).forEach((b) => involved.add(b.sailingId));
  p.holds.filter((h) => h.status === "ACTIVE").forEach((h) => involved.add(h.sailingId));
  const rows = p.sailings.filter((s) => involved.has(s.sailingId) || (!incidentSailing && s.committedUnits + s.heldUnits > 0));
  if (!rows.length) return <Empty title="No capacity changes yet at this moment" />;
  return (
    <table className="t">
      <thead><tr><th>Sailing</th><th>Status</th><th style={{ width: "34%" }}>20GP slots</th><th>Bookings at this moment</th></tr></thead>
      <tbody>{rows.map((s) => (
        <tr key={s.sailingId}>
          <td className="mono">{s.sailingId}</td><td><Status s={s.status} /></td>
          <td><CapacityBar s={s} /><div className="mono muted" style={{ fontSize: 10.5, marginTop: 3 }}>{s.committedUnits} committed · {s.heldUnits} held · {s.availableUnits} free{s.withdrawnUnits ? ` · ${s.withdrawnUnits} withdrawn` : ""}</div></td>
          <td>{p.bookings.filter((b) => b.sailingId === s.sailingId).map((b) => (
            <div key={b.bookingId} style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>
              <span style={{ color: buyerColor(b.buyerNode), fontWeight: 700 }}>{(b.buyer || "").replace("Buyer ", "")}</span>
              <span className="mono">{b.bookingId}</span><Status s={b.status} />{b.replacesBookingId && <span className="muted">replaces {b.replacesBookingId}</span>}
            </div>))}</td>
        </tr>))}</tbody>
    </table>
  );
}

export function TimeMachine() {
  const { route, reduceMotion } = useUi();
  const { projection: live } = useLive();
  const [incidents, setIncidents] = useState<IncidentSummary[] | null>(null);
  const [incidentId, setIncidentId] = useState<string | null>(route.params.incident || null);
  const [r, setR] = useState<PreparedReplay | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [at, setAt] = useState<number>(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [skipIdle, setSkipIdle] = useState(true);
  const [view, setView] = useState<View>("split");
  const [selected, setSelected] = useState<string | null>(null);
  const listeners = useRef(new Set<(e: PublicMarketEvent) => void>());
  const subscribe = useCallback((fn: (e: PublicMarketEvent) => void) => { listeners.current.add(fn); return () => { listeners.current.delete(fn); }; }, []);

  useEffect(() => { api.incidents().then((x) => { setIncidents(x.incidents); if (!incidentId && x.incidents[0]) setIncidentId(x.incidents[0].incidentId); }).catch(() => setIncidents([])); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!incidentId) return;
    setR(null); setErr(null); setPlaying(false);
    api.replay({ incident_id: incidentId }).then((b) => {
      const prep = prepare(b);
      if (!prep) { setErr(b.coverage.reason || "Historical state incomplete"); return; }
      setR(prep);
      const deep = route.params.at && route.params.incident === incidentId ? new Date(route.params.at).getTime() : null;
      setAt(deep && deep >= +new Date(prep.start) ? deep : +new Date(prep.start));
    }).catch(() => setErr("Replay unavailable for this incident"));
  }, [incidentId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Playback: virtual clock over business timestamps; events crossed while PLAYING animate, a seek never does.
  useEffect(() => {
    if (!playing || !r) return;
    let last = performance.now(), raf = 0, cur = at;
    const end = +new Date(r.end);
    const tick = (t: number) => {
      const dt = (t - last) * speed; last = t;
      let next = cur + dt;
      const nxt = nextEventAfter(r, iso(cur));
      if (skipIdle && nxt && +new Date(nxt.eventTime) - cur > 6000) next = Math.max(next, +new Date(nxt.eventTime) - 600);  // compression, labelled
      for (const e of r.events) { const ts = +new Date(e.eventTime); if (ts > cur && ts <= next) listeners.current.forEach((fn) => fn(e)); }
      cur = Math.min(next, end);
      setAt(cur);
      if (cur >= end) { setPlaying(false); return; }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, r, speed, skipIdle]); // eslint-disable-line react-hooks/exhaustive-deps

  const p = useMemo(() => (r ? projectAt(r, iso(at)) : null), [r, at]);
  const past = useMemo(() => (r ? r.events.filter((e) => +new Date(e.eventTime) <= at) : []), [r, at]);
  const mismatches = useMemo(() => (r ? terminalMismatches(r) : []), [r]);
  const incidentSessions = useMemo(() => (p ? p.negotiations.filter((s) => (incidentId ? s.incidentId === incidentId : true)) : []), [p, incidentId]);
  const inc = p?.incidents.find((i) => i.incidentId === incidentId);
  const sel = r?.bundle.sessions.find((s) => s.sessionId === selected) || null;

  const head = (
    <div className="page-head">
      <div style={{ flex: 1 }}><h1 className="page-title">Time Machine</h1><p className="page-sub">Read-only historical reconstruction from a stored checkpoint plus the recorded event history. No agent, payment or solver call is ever made.</p></div>
      <select className="select" value={incidentId || ""} onChange={(e) => { setIncidentId(e.target.value); navigate("replay", { incident: e.target.value }); }}>
        {(incidents || []).map((i) => <option key={i.incidentId} value={i.incidentId}>{i.sailingId} withdrawal · {i.incidentId} · {clock(i.openedAt, false)}</option>)}
      </select>
      <button className="btn primary" onClick={() => navigate("mission")}><Radio size={14} />Back to Live</button>
    </div>
  );
  if (incidents && !incidents.length) return <div className="page">{head}<div className="panel"><Empty title="Nothing to replay yet">Once a recorded incident exists, it can be rewound here. Replays need a checkpoint captured before the run (the observer stores one every minute).</Empty></div></div>;
  if (err) return <div className="page">{head}<div className="panel"><Empty title="Historical state incomplete">{err}</Empty></div></div>;
  if (!r || !p) return <div className="page">{head}<Empty title="Loading recorded history…" /></div>;

  const t0 = +new Date(r.start), t1 = Math.max(+new Date(r.end), t0 + 1000);
  const x = (ms: number) => ((ms - t0) / (t1 - t0)) * 100;
  const seek = (ms: number) => { setPlaying(false); setAt(Math.max(t0, Math.min(t1, ms))); };
  const prev = previousEventBefore(r, iso(at)), next = nextEventAfter(r, iso(at));
  const moments = keyMoments(r);
  const bands = PHASES.map((ph) => { const ts = r.events.filter(ph.match).map((e) => +new Date(e.eventTime)); return ts.length ? { ...ph, a: Math.min(...ts), b: Math.max(...ts) } : null; }).filter(Boolean) as (typeof PHASES[number] & { a: number; b: number })[];

  return (
    <div className="page">
      <div className="panel" style={{ borderColor: "rgba(180,154,240,.5)", background: "rgba(180,154,240,.06)", padding: "10px 16px", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <span className="pill replay">REPLAY MODE</span>
        <strong>Historical state as of {clock(iso(at))} UTC</strong><span className="muted">{new Date(at).toISOString().slice(0, 10)} · No live actions · simulated market</span>
        <span style={{ flex: 1 }} />
        {!r.bundle.coverage.complete && <span className="pill warn">{r.bundle.coverage.reason}</span>}
        {r.bundle.reference.terminal && <span className={`pill ${mismatches.length ? "warn" : "live"}`} title={mismatches.join("\n")}>{mismatches.length ? `${mismatches.length} mismatch(es) vs Tiger` : "Final state matches Tiger ✓"}</span>}
      </div>
      {head}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <div className="seg">{(["split", "map", "network", "capacity"] as View[]).map((v) => <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>{v === "split" ? "Split view" : v === "map" ? "Map" : v === "network" ? "Agent network" : "Capacity allocation"}</button>)}</div>
        <span style={{ flex: 1 }} />
        {inc && <><span className="muted">Incident stage at this moment:</span><Status s={inc.stage} /><span className="num muted">{inc.confirmed}/{inc.affected} confirmed</span></>}
      </div>
      <div className="grid-2">
        <div className="panel" style={{ padding: 8 }}>
          {(view === "map" || view === "split") && <MaritimeMap projection={p} at={iso(at)} selectedSailing={inc?.sailingId} compactLegend={view === "split"} />}
          {(view === "network" || view === "split") && <AgentNetwork sessions={incidentSessions} carriers={p.carriers} subscribe={subscribe} selected={selected} onSelect={setSelected}
                                                                       reduceMotion={reduceMotion} exchangeActive={inc?.stage === "CLEARING" ? "clearing" : "idle"} height={view === "split" ? 330 : 520} compact={view === "split"} />}
          {view === "capacity" && <ReplayCapacity p={p} incidentSailing={inc?.sailingId} />}
          {view === "split" && <div style={{ marginTop: 6 }}><ReplayCapacity p={p} incidentSailing={inc?.sailingId} /></div>}
        </div>
        <div className="panel flush" style={{ display: "flex", flexDirection: "column", maxHeight: 820 }}>
          {sel ? <div style={{ padding: 14, borderBottom: "1px solid var(--border-soft)", maxHeight: 420, overflow: "auto" }}>
            <button className="linklike" onClick={() => setSelected(null)}>← events</button>
            <NegotiationTimeline sessionId={sel.sessionId} replaySteps={sel} at={iso(at)} carrierName={p.carriers.find((c) => c.carrierId === sel.carrierId)?.displayName} />
          </div> : null}
          <div className="panel-title" style={{ padding: "14px 16px 6px", margin: 0 }}>Events up to this moment <span className="count">· {past.length} of {r.events.length}; future hidden</span></div>
          <div style={{ overflow: "auto", flex: 1 }}><Feed events={past} max={120} onPick={(e) => { seek(+new Date(e.eventTime)); if (e.sessionId) setSelected(e.sessionId); }} /></div>
        </div>
      </div>
      <div className="panel">
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
          <button className="btn" onClick={() => seek(t0)} title="Restart"><RotateCcw size={14} /></button>
          <button className="btn" disabled={!prev} onClick={() => prev && seek(+new Date(prev.eventTime))} title="Previous event"><ChevronLeft size={14} /></button>
          <button className="btn primary" onClick={() => { if (at >= t1) setAt(t0); setPlaying((v) => !v); }}>{playing ? <Pause size={14} /> : <Play size={14} />}{playing ? "Pause" : "Play"}</button>
          <button className="btn" disabled={!next} onClick={() => next && seek(+new Date(next.eventTime))} title="Next event"><ChevronRight size={14} /></button>
          <div className="seg">{[0.5, 1, 2, 4].map((s) => <button key={s} className={speed === s ? "on" : ""} onClick={() => setSpeed(s)}>{s}×</button>)}</div>
          <label className="muted" style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={skipIdle} onChange={(e) => setSkipIdle(e.target.checked)} />Skip idle periods (playback compression)</label>
          <span style={{ flex: 1 }} />
          {moments.map((m) => <button key={m.label} className="btn ghost" onClick={() => seek(+new Date(m.at))}>{m.label}</button>)}
        </div>
        <div style={{ position: "relative", height: 74, cursor: "pointer", userSelect: "none" }}
             onClick={(e) => { const b = (e.currentTarget as HTMLDivElement).getBoundingClientRect(); seek(t0 + ((e.clientX - b.left) / b.width) * (t1 - t0)); }}>
          {bands.map((b) => <div key={b.key} title={b.label} style={{ position: "absolute", top: 4, height: 14, left: `${x(b.a)}%`, width: `${Math.max(0.6, x(b.b) - x(b.a))}%`, background: b.color, opacity: 0.25, borderRadius: 4 }} />)}
          {bands.map((b) => <div key={`${b.key}-l`} className="muted" style={{ position: "absolute", top: 20, left: `${x(b.a)}%`, fontSize: 10.5, whiteSpace: "nowrap" }}>{b.label}</div>)}
          <div style={{ position: "absolute", top: 44, left: 0, right: 0, height: 2, background: "var(--border)" }} />
          {r.events.map((e) => { const ph = phaseOf(e); return <div key={e.eventId} title={`${clock(e.eventTime)} · ${e.summary}`} style={{ position: "absolute", top: 37, left: `${x(+new Date(e.eventTime))}%`, width: 2, height: 16, background: ph?.color || "var(--text-muted)", opacity: +new Date(e.eventTime) <= at ? 1 : 0.35 }} />; })}
          <div style={{ position: "absolute", top: 0, bottom: 0, left: `${x(at)}%`, width: 2, background: "var(--solver-violet)", boxShadow: "0 0 10px var(--solver-violet)" }} />
          <div className="mono muted" style={{ position: "absolute", bottom: 0, left: 0, fontSize: 10.5 }}>{clock(r.start)}</div>
          <div className="mono muted" style={{ position: "absolute", bottom: 0, right: 0, fontSize: 10.5 }}>{clock(r.end)} UTC</div>
        </div>
        <input type="range" min={t0} max={t1} value={at} step={100} onChange={(e) => seek(Number(e.target.value))} style={{ width: "100%" }} aria-label="Scrub replay time" />
        {at >= t1 && <div style={{ display: "flex", gap: 8, marginTop: 6 }}><button className="btn" onClick={() => { setAt(t0); setPlaying(true); }}>Replay again</button><button className="btn" onClick={() => navigate("mission")}>Return to live</button>{incidentId && <button className="btn violet" onClick={() => navigate("warroom", { incident: incidentId })}>Inspect solver decision</button>}</div>}
      </div>
      {incidentSessions.length > 0 && <div className="panel"><div className="panel-title">Session lanes at this moment</div><SessionLanes sessions={incidentSessions} events={past} selected={selected} onSelect={setSelected} at={iso(at)} /></div>}
      {live && <div className="muted" style={{ fontSize: 11.5 }}>Live market continues in the background ({live.metrics.incidentsOpen} open incident{live.metrics.incidentsOpen === 1 ? "" : "s"}); live alerts are muted inside the replay.</div>}
    </div>
  );
}
