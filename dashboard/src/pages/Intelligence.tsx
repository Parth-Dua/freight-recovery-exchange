import { useEffect, useState } from "react";
import { Database } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, Kpi } from "../components/common";
import { api } from "../data/api";
import type { Provenance } from "../data/contracts";
import { clock, money } from "../data/format";
import { navigate, useLive, useUi } from "../data/store";

const AXIS = { stroke: "#7b96ae", fontSize: 11, fontFamily: "JetBrains Mono, monospace" };
const tip = { contentStyle: { background: "#101e30", border: "1px solid #28415a", borderRadius: 8, fontSize: 12 }, labelStyle: { color: "#a3b8cd" } };
const tick = (v: string) => clock(v, false);

function Chapter({ n, title, question, provenance, children, insight }: { n: string; title: string; question: string; provenance?: Provenance; children: React.ReactNode; insight?: React.ReactNode }) {
  const { setInspect } = useUi();
  return (
    <section className="panel" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div className="mono muted" style={{ fontSize: 11 }}>{n}</div>
          <h2 style={{ margin: "2px 0 0", fontSize: 18 }}>{title}</h2>
          <div className="secondary" style={{ fontSize: 13 }}>{question}</div>
        </div>
        {provenance && <button className="btn ghost" onClick={() => setInspect({ kind: "provenance", provenance, title })}><Database size={13} />Provenance</button>}
      </div>
      {children}
      {insight && <div style={{ fontSize: 13, borderTop: "1px solid var(--border-soft)", paddingTop: 10 }} className="secondary">{insight}</div>}
    </section>
  );
}

export function Intelligence() {
  const { projection } = useLive();
  const [range, setRange] = useState("180");
  const [grain, setGrain] = useState("1m");
  const [data, setData] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    const from = new Date(Date.now() - Number(range) * 60_000).toISOString();
    Promise.all(["deals", "capacity", "recovery", "activity"].map((m) => api.analytics(m, { from, bucket: grain }).then((d) => [m, d] as const).catch(() => [m, null] as const)))
      .then((xs) => { if (alive) { setData(Object.fromEntries(xs)); setLoading(false); } });
    return () => { alive = false; };
  }, [range, grain, projection?.cursor]);

  const deals = (data.deals?.series || []) as any[];
  const dealsBook = deals.reduce((acc: Record<string, any>, r) => { const k = r.bucket; acc[k] = acc[k] || { bucket: k }; acc[k][`open_${r.kind}`] = r.avgOpeningUnitMinor ? r.avgOpeningUnitMinor / 100 : null; acc[k][`final_${r.kind}`] = r.avgFinalUnitMinor ? r.avgFinalUnitMinor / 100 : null; return acc; }, {});
  const dealSeries = Object.values(dealsBook).sort((a: any, b: any) => a.bucket.localeCompare(b.bucket));
  const tot = deals.reduce((a, r) => ({ sessions: a.sessions + r.sessions, comparable: a.comparable + r.comparableSessions, accepted: a.accepted + r.accepted, exhausted: a.exhausted + r.exhausted, declined: a.declined + r.declined + r.counterRefused, timedOut: a.timedOut + r.timedOut }), { sessions: 0, comparable: 0, accepted: 0, exhausted: 0, declined: 0, timedOut: 0 });
  const reductions = deals.filter((r) => r.medianReductionPct !== null).map((r) => r.medianReductionPct);
  const medianReduction = reductions.length ? reductions.sort((a, b) => a - b)[Math.floor(reductions.length / 2)] : null;
  const durations = deals.filter((r) => r.medianSeconds !== null).map((r) => r.medianSeconds);
  const cap = (data.capacity?.series || []) as any[];
  const markers = (data.capacity?.markers || []) as any[];
  const rec = (data.recovery?.series || []) as any[];
  const act = (data.activity?.series || []) as any[];
  const actSeries = act.map((b) => ({ bucket: b.bucket, total: b.total, source: b.source }));

  return (
    <div className="page">
      <div className="page-head">
        <div style={{ flex: 1 }}><h1 className="page-title">Market Intelligence</h1><p className="page-sub">How negotiations and recovery change the market over time — every chart is a live Tiger time-series query.</p></div>
        <select className="select" value={range} onChange={(e) => setRange(e.target.value)}>
          <option value="60">Last hour</option><option value="180">Last 3 hours</option><option value="720">Last 12 hours</option><option value="2880">Last 2 days</option>
        </select>
        <select className="select" value={grain} onChange={(e) => setGrain(e.target.value)}>
          <option value="1m">1-minute buckets</option><option value="5m">5-minute buckets</option><option value="15m">15-minute buckets</option><option value="1h">1-hour buckets</option>
        </select>
      </div>
      {loading && !Object.keys(data).length ? <Empty title="Querying Tiger…" /> : <>
        <Chapter n="A" title="Are the agents creating better deals?" question="Average carrier opening vs final quote per 20GP, only where opening and final are on the same sailing, quantity and currency." provenance={data.deals?.provenance}
                 insight={tot.comparable ? <>Across {tot.sessions} sessions ({tot.comparable} with comparable terms) the median final quote was {medianReduction ?? "—"}% below the same carrier's own opening. This is improvement vs each carrier's opening, not market-wide savings.</> : "No comparable negotiated sessions in this range yet."}>
          <div className="kpis" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))" }}>
            <Kpi label="Sessions" value={tot.sessions} note={tot.sessions === 1 ? "1 observed session" : "observed"} />
            <Kpi label="Median reduction" value={medianReduction === null ? "—" : `${medianReduction}%`} note="vs carrier's own opening" />
            <Kpi label="Outcomes" value={<span style={{ fontSize: 15, lineHeight: 1.4, display: "block" }}>{tot.accepted} agreed · {tot.exhausted} final · {tot.declined} declined · {tot.timedOut} timed out</span>} />
            <Kpi label="Median duration" value={durations.length ? `${durations.sort((a, b) => a - b)[Math.floor(durations.length / 2)]}s` : "—"} note="session open → close" />
          </div>
          {dealSeries.length ? <ResponsiveContainer width="100%" height={240}>
            <LineChart data={dealSeries} onClick={(e: any) => e?.activeLabel && navigate("replay", { at: e.activeLabel })}>
              <CartesianGrid stroke="#17283e" /><XAxis dataKey="bucket" tickFormatter={tick} {...AXIS} /><YAxis {...AXIS} domain={["auto", "auto"]} tickFormatter={(v) => `$${Math.round(v)}`} />
              <Tooltip {...tip} labelFormatter={(v) => `${clock(v as string)} UTC bucket`} formatter={(v: any, n: any) => [`${money(Number(v) * 100)} per 20GP (simulated)`, n]} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line dataKey="open_BOOKING" name="Booking · opening" stroke="#78afff" strokeDasharray="4 3" dot connectNulls />
              <Line dataKey="final_BOOKING" name="Booking · final" stroke="#43d9cb" strokeWidth={2} dot connectNulls />
              <Line dataKey="open_RECOVERY" name="Recovery · opening" stroke="#f5ae59" strokeDasharray="4 3" dot connectNulls />
              <Line dataKey="final_RECOVERY" name="Recovery · final" stroke="#b49af0" strokeWidth={2} dot connectNulls />
            </LineChart></ResponsiveContainer> : <Empty title="No comparable prices in this range" />}
        </Chapter>
        <Chapter n="B" title="Is capacity being utilized well?" question="Committed vs held vs available 20GP slots across sailings (latest snapshot per sailing per bucket — no double counting). Amber lines are carrier withdrawals: click to replay that moment." provenance={data.capacity?.provenance}
                 insight={cap.length ? <>Latest bucket: {cap[cap.length - 1].committedUnits} committed · {cap[cap.length - 1].heldUnits} held (not booked) · {cap[cap.length - 1].availableUnits} sellable across {cap[cap.length - 1].sailingsSampled} sailings. Withdrawn sailings count zero sellable capacity.</> : "No capacity snapshots in this range yet (the observer writes one per minute and on every inventory change)."}>
          {cap.length ? <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={cap}>
              <CartesianGrid stroke="#17283e" /><XAxis dataKey="bucket" tickFormatter={tick} {...AXIS} />
              <YAxis yAxisId="l" allowDecimals={false} {...AXIS} label={{ value: "committed + held", angle: -90, position: "insideLeft", fill: "#7b96ae", fontSize: 11 }} />
              <YAxis yAxisId="r" orientation="right" allowDecimals={false} {...AXIS} label={{ value: "available", angle: 90, position: "insideRight", fill: "#7b96ae", fontSize: 11 }} />
              <Tooltip {...tip} labelFormatter={(v) => `${clock(v as string)} UTC · estimated from snapshots`} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Area yAxisId="l" type="stepAfter" dataKey="committedUnits" name="Committed" stackId="1" stroke="#8fb3dc" fill="#4f6f92" fillOpacity={0.7} />
              <Area yAxisId="l" type="stepAfter" dataKey="heldUnits" name="Held (provisional, not booked)" stackId="1" stroke="#6f9ff0" fill="#6f9ff0" fillOpacity={0.5} />
              <Area yAxisId="r" type="stepAfter" dataKey="availableUnits" name="Available (sellable, right axis)" stroke="#43d9cb" fill="none" strokeDasharray="5 4" />
              {markers.map((m) => <ReferenceLine yAxisId="l" key={m.eventId} x={cap.find((c) => c.bucket >= m.at.slice(0, 16))?.bucket} stroke="#f5ae59" strokeDasharray="4 3"
                label={{ value: `${m.sailingId} withdrawn`, fill: "#f5ae59", fontSize: 11, position: "insideTopLeft", cursor: "pointer", onClick: () => navigate("replay", { at: m.at }) } as any} />)}
            </AreaChart></ResponsiveContainer> : <Empty title="No capacity history yet" />}
          {markers.length > 0 && <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{markers.map((m) => <button key={m.eventId} className="btn amber" onClick={() => navigate("replay", { at: m.at, incident: rec.find((r) => r.sailingId === m.sailingId)?.incidentId })}>Replay {m.sailingId} withdrawal at {clock(m.at)}</button>)}</div>}
        </Chapter>
        <Chapter n="C" title="How effectively are disruptions resolved?" question="Per incident: actually confirmed vs pending vs unresolved, next to the proposals of the joint optimizer and of a first-come baseline on the same frozen offers (counterfactual, never executed)." provenance={data.recovery?.provenance}
                 insight={rec.length ? <>{rec.reduce((a, r) => a + r.confirmed, 0)} confirmed of {rec.reduce((a, r) => a + r.impacted, 0)} impacted bookings across {rec.length} incident{rec.length > 1 ? "s" : ""}. {rec.filter((r) => r.secondsToFinalConfirmation !== null).length ? `Median time from withdrawal to final confirmation: ${rec.filter((r) => r.secondsToFinalConfirmation !== null).map((r) => r.secondsToFinalConfirmation).sort((a, b) => a - b)[0]} s (completed incidents only).` : "Time-to-recovery is reported only for completed incidents."}</> : "No disruptions in this range."}>
          {rec.length ? <ResponsiveContainer width="100%" height={240}>
            <BarChart data={rec.map((r) => ({ ...r, label: `${r.sailingId} · ${clock(r.withdrawnAt, false)}` }))} onClick={(e: any) => { const r = rec[e?.activeTooltipIndex ?? -1]; if (r) navigate("warroom", { incident: r.incidentId }); }}>
              <CartesianGrid stroke="#17283e" /><XAxis dataKey="label" {...AXIS} /><YAxis allowDecimals={false} {...AXIS} />
              <Tooltip {...tip} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="confirmed" name="Confirmed (committed)" stackId="a" fill="#54d69b" />
              <Bar dataKey="pending" name="Pending" stackId="a" fill="#6f9ff0" />
              <Bar dataKey="unresolved" name="Unresolved" stackId="a" fill="#f27676" />
              <Bar dataKey="jointProposed" name="Joint optimizer · proposed" fill="#b49af0" fillOpacity={0.55} />
              <Bar dataKey="baselineProposed" name="First-come baseline · simulated" fill="#7b96ae" fillOpacity={0.45} />
            </BarChart></ResponsiveContainer> : <Empty title="No disruptions in this range" />}
        </Chapter>
        <Chapter n="D" title="Market activity (continuous aggregate)" question="Recorded market events per minute: materialized buckets from the market_activity_1m continuous aggregate, plus the raw hypertable tail after its watermark (non-overlapping)." provenance={data.activity?.provenance}
                 insight={data.activity?.provenance ? <>Watermark {data.activity.provenance.watermark ? `${clock(data.activity.provenance.watermark)} UTC` : "not yet materialized"} · {data.activity.provenance.materializedRows} materialized + {data.activity.provenance.rawTailRows} raw-tail rows · {data.activity.provenance.bucketedEventCount} bucketed = {data.activity.provenance.rawEventCount} raw events.</> : null}>
          {actSeries.length ? <ResponsiveContainer width="100%" height={180}>
            <BarChart data={actSeries}>
              <CartesianGrid stroke="#17283e" /><XAxis dataKey="bucket" tickFormatter={tick} {...AXIS} /><YAxis {...AXIS} />
              <Tooltip {...tip} labelFormatter={(v) => `${clock(v as string)} UTC`} formatter={(v: any, _n: any, p: any) => [v, p.payload.source === "materialized" ? "events (continuous aggregate)" : "events (raw tail)"]} />
              <Bar dataKey="total" name="Events" fill="#43d9cb" fillOpacity={0.7} />
            </BarChart></ResponsiveContainer> : <Empty title="No events in this range" />}
        </Chapter>
      </>}
    </div>
  );
}
