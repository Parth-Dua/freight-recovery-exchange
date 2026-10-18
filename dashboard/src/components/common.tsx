import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { X, ExternalLink, History, Radio, ArrowRight } from "lucide-react";
import { api } from "../data/api";
import type { Decision, InspectTarget, PublicMarketEvent } from "../data/contracts";
import { clock, human, statusClass } from "../data/format";
import { navigate, useUi } from "../data/store";

export const Status = ({ s, label }: { s?: string | null; label?: string }) => (
  <span className={`status ${statusClass(s)}`}>{label ?? human(s)}</span>
);

const SRC: Record<string, [string, string]> = {
  CARRIER_AGENT: ["Carrier agent", "carrier"], BUYER_AGENT: ["Buyer agent", "buyer"], EXCHANGE: ["Exchange", "exchange"],
  CARRIER_OPS: ["Carrier ops", "ops"], PAYMENT_SERVICE: ["Payment service", ""], BOOKING_SERVICE: ["Booking service", ""],
};
export const SourceBadge = ({ kind }: { kind: string }) => { const [l, c] = SRC[kind] || [kind, ""]; return <span className={`src ${c}`}>{l}</span>; };

/** Live market feed: newest first, never steals scroll; a "N new events" bar appears when scrolled away. */
export function Feed({ events, max = 50, onPick, compact = false, emptyText }: {
  events: PublicMarketEvent[]; max?: number; onPick?: (e: PublicMarketEvent) => void; compact?: boolean; emptyText?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(0);
  const [atTop, setAtTop] = useState(true);
  const shown = useMemo(() => [...events].reverse().slice(0, max), [events, max]);
  const newest = shown[0]?.eventId;
  const lastSeen = useRef<string | undefined>(newest);
  useEffect(() => {
    if (!newest || newest === lastSeen.current) return;
    if (!atTop) setPinned((n) => n + 1); else lastSeen.current = newest;
  }, [newest, atTop]);
  const { setInspect } = useUi();
  if (!shown.length) return <div className="empty"><strong>{emptyText || "No market events yet"}</strong><span>The market is ready; events appear here as agents act.</span></div>;
  return (
    <div className="feed" ref={ref} style={{ maxHeight: "100%" }}
         onScroll={(e) => { const top = (e.target as HTMLDivElement).scrollTop < 8; setAtTop(top); if (top) { setPinned(0); lastSeen.current = newest; } }}>
      {pinned > 0 && <div className="feed-banner" onClick={() => { ref.current?.scrollTo({ top: 0, behavior: "smooth" }); setPinned(0); }}>{pinned} new event{pinned > 1 ? "s" : ""} ↑</div>}
      {shown.map((e, i) => (
        <div key={e.eventId} className={`feed-row ${i === 0 && atTop ? "fresh" : ""}`} onClick={() => (onPick ? onPick(e) : setInspect({ kind: "event", id: e.eventId }))}>
          <div className="feed-time">{clock(e.eventTime)}</div>
          <div>
            <div className="feed-text">{e.summary}</div>
            {!compact && <div className="feed-meta"><SourceBadge kind={e.actorKind} />{e.sailingId && <span className="mono muted">{e.sailingId}</span>}{e.incidentId && <span className="mono muted">{e.incidentId}</span>}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Shared evidence drawer (not a page). Every claim comes from /api/ops/decisions; nothing is generated here. */
export function Inspector() {
  const { inspect, setInspect } = useUi();
  const [d, setD] = useState<Decision | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setD(null); setErr(null);
    if (!inspect || inspect.kind === "provenance") return;
    api.decision(inspect.kind, inspect.id).then(setD).catch(() => setErr("Not available: this record is unknown or not visible to this view."));
  }, [inspect]);
  useEffect(() => { const f = (e: KeyboardEvent) => { if (e.key === "Escape") setInspect(null); }; window.addEventListener("keydown", f); return () => window.removeEventListener("keydown", f); }, [setInspect]);
  if (!inspect) return null;
  const go = (l: Decision["links"][number]) => {
    setInspect(null);
    if (l.kind === "warroom") navigate("warroom", { incident: l.incidentId || d?.id });
    else if (l.kind === "replay") navigate("replay", { incident: l.incidentId, at: l.at });
    else if (l.kind === "universe") navigate("universe", { session: l.sessionId });
  };
  return (
    <>
      <div className="drawer-scrim" onClick={() => setInspect(null)} />
      <aside className="drawer" role="dialog" aria-label="Decision Inspector">
        <div className="drawer-head">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div className="drawer-crumb" style={{ flex: 1 }}>Decision Inspector · {inspect.kind}</div>
            <button className="icon-btn" onClick={() => setInspect(null)} aria-label="Close"><X size={16} /></button>
          </div>
          {inspect.kind === "provenance" ? <ProvenanceHead title={inspect.title} /> : d ? (
            <>
              <div className="drawer-title">{d.title}</div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}><Status s={d.status} /><span className="mono muted">{d.id}</span><span className="mono muted">as of {clock(d.asOf)} UTC</span></div>
            </>
          ) : <div className="drawer-title muted">{err || "Loading evidence…"}</div>}
        </div>
        <div className="drawer-body">
          {inspect.kind === "provenance" ? <ProvenanceBody p={inspect.provenance} /> : d && (
            <>
              <section className="drawer-section"><h4>What happened</h4><div>{d.what}</div></section>
              <section className="drawer-section"><h4>Why</h4><div className={d.why ? "" : "muted"}>{d.why || "Reason not recorded"}</div></section>
              {d.facts.length > 0 && <section className="drawer-section"><h4>Facts</h4><dl className="kv">{d.facts.map(([k, v]) => <FactRow key={k} k={k} v={v} />)}</dl></section>}
              {d.evidence.length > 0 && <section className="drawer-section"><h4>Evidence</h4><div className="evidence">{d.evidence.map((e, i) => (
                <div key={i} className="evidence-row"><div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><span>{e.label}</span>{e.at && <span className="mono muted">{clock(e.at)}</span>}</div><div className="mono muted" style={{ marginTop: 2 }}>{e.value}</div></div>))}</div></section>}
              {d.links.length > 0 && <section className="drawer-section"><h4>Navigate</h4><div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{d.links.map((l, i) => (
                <button key={i} className="btn" onClick={() => go(l)}>{l.kind === "replay" ? <History size={14} /> : <ArrowRight size={14} />}{l.label}</button>))}</div></section>}
              <div className="muted" style={{ fontSize: 11.5 }}>Read-only view. Buyer budgets, auto-rebook limits, carrier floors and payment details are never sent to this dashboard.</div>
            </>
          )}
        </div>
      </aside>
    </>
  );
}

const FactRow = ({ k, v }: { k: string; v: string }) => (<><dt>{k}</dt><dd className={/^\d{4}-\d\d-\d\dT/.test(v) ? "mono" : ""}>{/^\d{4}-\d\d-\d\dT/.test(v) ? `${v.slice(0, 10)} ${clock(v)} UTC` : v}</dd></>);

function ProvenanceHead({ title }: { title: string }) {
  return <><div className="drawer-title">{title}</div><div className="muted" style={{ fontSize: 12 }}>Data provenance · exact query, grain and source</div></>;
}
function ProvenanceOverview() {
  const [d, setD] = useState<any>(null);
  useEffect(() => { api.provenance().then(setD).catch(() => setD({ error: true })); }, []);
  if (!d) return <div className="muted">Reading Tiger catalog…</div>;
  if (d.error) return <div className="muted">Provenance unavailable.</div>;
  const ca = d.continuousAggregate || {};
  return (
    <>
      <section className="drawer-section"><h4>Hypertables</h4>
        {d.hypertables.map((h: any) => <div key={h.hypertable_name} className="evidence-row"><span className="mono">{h.hypertable_name}</span><div className="mono muted">time column {h.timeColumn} · chunk {h.chunkInterval} · {h.num_chunks} chunk(s)</div></div>)}
      </section>
      <section className="drawer-section"><h4>Continuous aggregate</h4>
        <dl className="kv"><dt>Name</dt><dd className="mono">{ca.name}</dd><dt>Materialization</dt><dd className="mono">{ca.materializationTable}</dd>
          <dt>Watermark</dt><dd className="mono">{ca.watermark ? `${clock(ca.watermark)} UTC` : "not yet materialized"}</dd>
          <dt>Refresh policy</dt><dd className="mono">every {ca.refreshPolicy?.scheduleInterval} · window {ca.refreshPolicy?.startOffset} → {ca.refreshPolicy?.endOffset}</dd>
          <dt>Last refresh</dt><dd className="mono">{ca.refreshPolicy?.lastSuccessfulFinish ? `${clock(ca.refreshPolicy.lastSuccessfulFinish)} UTC · ${ca.refreshPolicy.lastRunStatus}` : "pending first run"}</dd>
          <dt>Real-time agg.</dt><dd>off — recent tail is read from the raw hypertable after the watermark</dd></dl>
      </section>
      <section className="drawer-section"><h4>Row counts</h4><dl className="kv">{Object.entries(d.rowCounts).map(([k, v]) => <Fragment key={k}><dt className="mono">{k}</dt><dd className="mono">{String(v)}</dd></Fragment>)}</dl></section>
      <div className="muted" style={{ fontSize: 11.5 }}>Each Market Intelligence chart has its own Provenance button with the exact SQL template, grain and reconciliation.</div>
    </>
  );
}

function ProvenanceBody({ p }: { p: any }) {
  if (p.queryId === "overview") return <ProvenanceOverview />;
  return (
    <>
      <dl className="kv">
        <dt>Query id</dt><dd className="mono">{p.queryId}</dd>
        <dt>Source</dt><dd>{p.source}</dd>
        <dt>Value kind</dt><dd>{p.valueKind}</dd>
        <dt>Bucket</dt><dd className="mono">time_bucket('{p.grain}')</dd>
        <dt>Range</dt><dd className="mono">{clock(p.start)} → {clock(p.end)} UTC</dd>
        <dt>Rows</dt><dd className="mono">{p.rows}{p.materializedRows !== undefined ? ` (${p.materializedRows} materialized + ${p.rawTailRows} raw tail)` : ""}</dd>
        {p.rawEventCount !== undefined && <><dt>Reconciled</dt><dd className="mono">{p.bucketedEventCount} bucketed = {p.rawEventCount} raw events {p.bucketedEventCount === p.rawEventCount ? "✓" : "≠"}</dd></>}
        {p.watermark !== undefined && <><dt>Watermark</dt><dd className="mono">{p.watermark ? `${clock(p.watermark)} UTC` : "not yet materialized"}</dd></>}
        {p.aggregate?.refreshPolicy && <><dt>Refresh policy</dt><dd className="mono">every {p.aggregate.refreshPolicy.scheduleInterval} · last {p.aggregate.refreshPolicy.lastSuccessfulFinish ? clock(p.aggregate.refreshPolicy.lastSuccessfulFinish) : "pending"} · {p.aggregate.refreshPolicy.lastRunStatus || "scheduled"}</dd></>}
        {p.sourceTables && <><dt>Tables</dt><dd className="mono">{p.sourceTables.join(", ")}</dd></>}
      </dl>
      {p.sql && <section className="drawer-section"><h4>Prepared SQL template</h4><pre className="mono" style={{ whiteSpace: "pre-wrap", background: "var(--bg)", border: "1px solid var(--border-soft)", borderRadius: 8, padding: 10, fontSize: 11, color: "var(--text-secondary)", margin: 0 }}>{p.sql}</pre></section>}
    </>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return <div className="empty"><strong>{title}</strong>{children && <span>{children}</span>}</div>;
}

export function Kpi({ label, value, note, attention }: { label: string; value: React.ReactNode; note?: string; attention?: boolean }) {
  return <div className={`kpi ${attention ? "attention" : ""}`}><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div>{note && <div className="kpi-note">{note}</div>}</div>;
}

export const LiveIcon = Radio;
export const ExtIcon = ExternalLink;
export type { InspectTarget };
