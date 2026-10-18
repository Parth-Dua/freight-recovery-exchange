import { useEffect, useMemo, useRef, useState } from "react";
import type { PublicCarrier, PublicMarketEvent, PublicSession } from "../data/contracts";
import { buyerColor, money } from "../data/format";

// Directed buyer-agent ↔ carrier-agent network. Edges exist only for real negotiation_sessions; a packet is drawn
// only when its persisted event arrives (never invented), and concurrent events animate concurrently (no shared lock).
const W = 1000, H = 560;
const PACKET_MS = 950, MAX_PACKETS = 28;

type Packet = { id: string; sessionId: string; toCarrier: boolean; color: string; label: string; born: number };

export interface NetworkProps {
  sessions: PublicSession[];
  carriers: PublicCarrier[];
  selected?: string | null;
  onSelect?: (sessionId: string) => void;
  subscribe?: (fn: (e: PublicMarketEvent) => void) => () => void;
  exchangeActive?: "idle" | "pairing" | "clearing";
  reduceMotion?: boolean;
  height?: number | string;
  compact?: boolean;
}

const EDGE: Record<string, { stroke: string; dash?: string; opacity: number; label: string }> = {
  OPEN: { stroke: "#43d9cb", dash: "5 5", opacity: 0.9, label: "Negotiating" },
  ACCEPTED: { stroke: "#54d69b", opacity: 0.85, label: "Agreed" },
  EXHAUSTED: { stroke: "#78afff", opacity: 0.6, label: "Final quote" },
  REJECTED: { stroke: "#f27676", dash: "2 5", opacity: 0.55, label: "Declined" },
  EXPIRED: { stroke: "#f5ae59", dash: "2 5", opacity: 0.6, label: "Timed out" },
};

function bez(t: number, p0: number[], p1: number[], p2: number[], p3: number[]) {
  const u = 1 - t;
  return [0, 1].map((k) => u * u * u * p0[k] + 3 * u * u * t * p1[k] + 3 * u * t * t * p2[k] + t * t * t * p3[k]);
}

export function AgentNetwork({ sessions, carriers, selected, onSelect, subscribe, exchangeActive = "idle", reduceMotion, height, compact }: NetworkProps) {
  const buyers = useMemo(() => {
    const m = new Map<string, { node: string; label: string; open: number }>();
    for (const s of sessions) {
      const node = s.buyerNode || `buyer-${s.buyer}`;
      const cur = m.get(node) || { node, label: s.buyer || "Buyer", open: 0 };
      if (s.status === "OPEN") cur.open++;
      m.set(node, cur);
    }
    return [...m.values()].sort((a, b) => a.label.localeCompare(b.label));
  }, [sessions]);
  const carrierNodes = carriers.filter((c) => c.active);
  const left = compact ? 170 : 210, right = W - (compact ? 170 : 210);
  const bPos = new Map(buyers.map((b, i) => [b.node, [left, 70 + (i + 0.5) * ((H - 150) / Math.max(1, buyers.length))]]));
  const cPos = new Map(carrierNodes.map((c, i) => [c.carrierId, [right, 60 + (i + 0.5) * ((H - 130) / Math.max(1, carrierNodes.length))]]));
  const ex = [W / 2, H - 34];

  const edges = useMemo(() => {
    const pairCount = new Map<string, number>();
    return sessions.map((s) => {
      const b = bPos.get(s.buyerNode || `buyer-${s.buyer}`), c = cPos.get(s.carrierId);
      if (!b || !c) return null;
      const key = `${s.buyerNode}|${s.carrierId}`;
      const k = pairCount.get(key) || 0; pairCount.set(key, k + 1);
      const off = (k % 2 ? -1 : 1) * Math.ceil(k / 2) * 16;
      const p0 = [b[0] + 26, b[1]], p3 = [c[0] - 26, c[1]];
      const p1 = [p0[0] + 200, p0[1] + off], p2 = [p3[0] - 200, p3[1] + off];
      return { s, p0, p1, p2, p3, labelT: 0.3 + ((sessions.indexOf(s) * 3) % 4) * 0.13 };  // staggered price labels
    }).filter(Boolean) as { s: PublicSession; p0: number[]; p1: number[]; p2: number[]; p3: number[]; labelT: number }[];
  }, [sessions, buyers.length, carrierNodes.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const edgeBySession = useMemo(() => new Map(edges.map((e) => [e.s.sessionId, e])), [edges]);

  const [packets, setPackets] = useState<Packet[]>([]);
  const [overflow, setOverflow] = useState(0);
  const [pulse, setPulse] = useState(0);
  const [, setTick] = useState(0);
  const raf = useRef<number | null>(null);

  useEffect(() => {
    if (!subscribe) return;
    return subscribe((e) => {
      if (e.eventType === "NEGOTIATION_OPENED" || e.eventType === "CLEARING_STARTED" || e.eventType === "CLEARING_DONE") setPulse(Date.now());
      if (!e.sessionId || reduceMotion) return;
      const spec: Record<string, [boolean, string, string]> = {
        NEGOTIATION_OPENED: [true, "#a3b8cd", "RFQ"],
        OFFER_SENT: [false, "#78afff", money(e.publicPayload.total_price_minor)],
        COUNTER_SENT: [true, "#43d9cb", `Proposed ${money(e.publicPayload.proposed_total_price_minor)}`],
        OFFER_ACCEPTED: [false, "#54d69b", `Accepted ${money(e.publicPayload.total_price_minor)}`],
        OFFER_REJECTED: [false, "#f27676", "Declined"],
      };
      const sp = spec[e.eventType];
      if (!sp) return;
      setPackets((ps) => {
        const live = ps.filter((p) => Date.now() - p.born < PACKET_MS);
        if (live.length >= MAX_PACKETS) { setOverflow((n) => n + 1); return live; }
        return [...live, { id: e.eventId, sessionId: e.sessionId!, toCarrier: sp[0], color: sp[1], label: sp[2], born: Date.now() }];
      });
    });
  }, [subscribe, reduceMotion]);

  useEffect(() => {
    if (!packets.length) return;
    const loop = () => {
      setTick((t) => t + 1);
      setPackets((ps) => { const live = ps.filter((p) => Date.now() - p.born < PACKET_MS + 250); if (!live.length) setOverflow(0); return live.length === ps.length ? ps : live; });
      raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);
    return () => { if (raf.current) cancelAnimationFrame(raf.current); };
  }, [packets.length > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  const exColor = exchangeActive === "clearing" ? "#b49af0" : "#7b96ae";
  const pulsing = Date.now() - pulse < 1200;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: height ?? "auto", display: "block" }} role="img" aria-label="Agent negotiation network">
      <defs>
        <filter id="nglow"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        <radialGradient id="nodebg"><stop offset="0%" stopColor="#17283e" /><stop offset="100%" stopColor="#0d1b2c" /></radialGradient>
      </defs>
      <text x={left} y={26} textAnchor="middle" className="svg-mono">BUYER AGENTS · logical instances</text>
      <text x={right} y={26} textAnchor="middle" className="svg-mono">CARRIER uAGENTS</text>
      {/* edges */}
      {edges.map(({ s, p0, p1, p2, p3, labelT }) => {
        const st = EDGE[s.status] || EDGE.EXHAUSTED;
        const sel = s.sessionId === selected;
        const d = `M${p0[0]},${p0[1]} C${p1[0]},${p1[1]} ${p2[0]},${p2[1]} ${p3[0]},${p3[1]}`;
        const mid = bez(labelT, p0, p1, p2, p3);
        const price = s.finalMinor ?? s.latestMinor;
        return (
          <g key={s.sessionId} style={{ cursor: "pointer" }} onClick={() => onSelect?.(s.sessionId)}>
            <path d={d} fill="none" stroke="transparent" strokeWidth="14" />
            <path d={d} fill="none" stroke={st.stroke} strokeOpacity={sel ? 1 : st.opacity} strokeWidth={sel ? 2.6 : 1.4} strokeDasharray={st.dash}
                  className={s.status === "OPEN" ? "edge-flow" : undefined} filter={sel ? "url(#nglow)" : undefined}>
              <title>{`${s.buyer} ↔ ${s.carrierId} · ${st.label}${price ? ` · ${money(price)}` : ""} · ${s.kind.toLowerCase()} session ${s.sessionId}`}</title>
            </path>
            {(sel || (!compact && price && s.status !== "REJECTED")) && (
              <g transform={`translate(${mid[0]},${mid[1]})`}>
                <rect x={-34} y={-10} width={68} height={18} rx={9} fill="#0d1b2c" stroke={st.stroke} strokeOpacity="0.5" />
                <text textAnchor="middle" y={3} className="svg-mono" style={{ fill: "#dbe6f5", fontSize: 10 }}>{price ? money(price) : st.label}</text>
              </g>
            )}
          </g>
        );
      })}
      {/* packets */}
      {packets.map((p) => {
        const e = edgeBySession.get(p.sessionId);
        if (!e) return null;
        const f = Math.min(1, (Date.now() - p.born) / PACKET_MS);
        const t = p.toCarrier ? f : 1 - f;
        const [x, y] = bez(t, e.p0, e.p1, e.p2, e.p3);
        const fade = f >= 1 ? Math.max(0, 1 - (Date.now() - p.born - PACKET_MS) / 250) : 1;
        return (
          <g key={p.id} transform={`translate(${x},${y})`} opacity={fade}>
            <circle r="9" fill={p.color} opacity="0.18" />
            <circle r="4.5" fill={p.color} filter="url(#nglow)" />
            <text y={-12} textAnchor="middle" className="svg-mono" style={{ fill: p.color, fontSize: 10.5 }}>{p.label}</text>
          </g>
        );
      })}
      {overflow > 0 && <text x={W / 2} y={44} textAnchor="middle" className="svg-mono">+{overflow} events (see session timeline)</text>}
      {/* buyer nodes */}
      {buyers.map((b) => {
        const [x, y] = bPos.get(b.node)!;
        const c = buyerColor(b.node);
        return (
          <g key={b.node} transform={`translate(${x},${y})`}>
            {b.open > 0 && <circle r="30" fill="none" stroke={c} strokeOpacity="0.5" className="node-pulse" />}
            <circle r="24" fill="url(#nodebg)" stroke={c} strokeWidth="1.6" />
            <text textAnchor="middle" y="5" style={{ fill: c, fontWeight: 700, fontSize: 15, fontFamily: "var(--font)" }}>{b.label.replace("Buyer ", "")}</text>
            <text x={-34} y={4} textAnchor="end" className="svg-label strong">{b.label} agent</text>
            {!compact && <text x={-34} y={19} textAnchor="end" className="svg-mono">{b.open ? `${b.open} open session${b.open > 1 ? "s" : ""}` : "idle"}</text>}
          </g>
        );
      })}
      {/* carrier nodes */}
      {carrierNodes.map((c) => {
        const [x, y] = cPos.get(c.carrierId)!;
        const open = sessions.some((s) => s.carrierId === c.carrierId && s.status === "OPEN");
        return (
          <g key={c.carrierId} transform={`translate(${x},${y})`}>
            {open && <rect x={-28} y={-28} width={56} height={56} rx={14} fill="none" stroke="#78afff" strokeOpacity="0.45" className="node-pulse" />}
            <rect x={-22} y={-22} width={44} height={44} rx={11} fill="url(#nodebg)" stroke="#78afff" strokeWidth="1.5" />
            <text textAnchor="middle" y="5" style={{ fill: "#78afff", fontWeight: 700, fontSize: 13, fontFamily: "var(--font)" }}>{c.displayName.slice(0, 2).toUpperCase()}</text>
            <text x={32} y={2} className="svg-label strong">{c.displayName}</text>
            {!compact && <text x={32} y={17} className="svg-mono">{c.maskedAddress} · signed</text>}
          </g>
        );
      })}
      {/* exchange referee node */}
      <g transform={`translate(${ex[0]},${ex[1]})`}>
        {(pulsing || exchangeActive !== "idle") && <circle r="24" fill="none" stroke={exColor} strokeOpacity="0.6" className="node-pulse" />}
        <circle r="16" fill="url(#nodebg)" stroke={exColor} strokeWidth="1.5" />
        <text textAnchor="middle" y="4" style={{ fill: exColor, fontWeight: 700, fontSize: 10, fontFamily: "var(--font)" }}>EX</text>
        <text x={24} y={4} className="svg-label">{exchangeActive === "clearing" ? "Exchange · clearing jointly (OR-Tools)" : "Exchange · pairing, validation, clearing"}</text>
      </g>
      {!sessions.length && <text x={W / 2} y={H / 2} textAnchor="middle" className="svg-label">No negotiations in this view — market ready</text>}
    </svg>
  );
}

/** Session lanes: one lane per real session, markers at recorded message times — parallelism made visible. */
export function SessionLanes({ sessions, events, selected, onSelect, at }: {
  sessions: PublicSession[]; events: PublicMarketEvent[]; selected?: string | null; onSelect?: (id: string) => void; at?: string;
}) {
  const rel = events.filter((e) => e.sessionId && sessions.some((s) => s.sessionId === e.sessionId));
  if (!sessions.length) return null;
  const times = [...rel.map((e) => +new Date(e.eventTime)), ...sessions.map((s) => +new Date(s.openedAt || 0)).filter(Boolean)];
  const t0 = Math.min(...times), t1 = Math.max(...times, at ? +new Date(at) : 0, t0 + 1000);
  const x = (iso: string) => 140 + ((+new Date(iso) - t0) / (t1 - t0)) * 820;
  const color: Record<string, string> = { OFFER_SENT: "#78afff", COUNTER_SENT: "#43d9cb", OFFER_ACCEPTED: "#54d69b", OFFER_REJECTED: "#f27676", NEGOTIATION_OPENED: "#a3b8cd", NEGOTIATION_CLOSED: "#7b96ae" };
  const rowH = 18, h = sessions.length * rowH + 26;
  return (
    <svg viewBox={`0 0 1000 ${h}`} style={{ width: "100%", display: "block" }} role="img" aria-label="Concurrent session lanes">
      {sessions.map((s, i) => {
        const y = 14 + i * rowH;
        const mine = rel.filter((e) => e.sessionId === s.sessionId);
        const start = s.openedAt ? x(s.openedAt) : 140, end = s.closedAt ? x(s.closedAt) : at ? x(at) : 960;
        return (
          <g key={s.sessionId} style={{ cursor: "pointer" }} onClick={() => onSelect?.(s.sessionId)} opacity={selected && selected !== s.sessionId ? 0.55 : 1}>
            <text x={130} y={y + 4} textAnchor="end" className="svg-mono" style={{ fill: s.sessionId === selected ? "#f3f7ff" : undefined }}>{(s.buyer || "").replace("Buyer ", "")} ↔ {s.carrierId}</text>
            <line x1={start} x2={end} y1={y} y2={y} stroke={EDGE[s.status]?.stroke || "#78afff"} strokeOpacity="0.45" strokeWidth="3" strokeLinecap="round" />
            {mine.map((e) => <circle key={e.eventId} cx={x(e.eventTime)} cy={y} r="3.6" fill={color[e.eventType] || "#a3b8cd"}><title>{`${e.eventTime.slice(11, 19)} · ${e.summary}`}</title></circle>)}
          </g>
        );
      })}
      <text x={140} y={h - 4} className="svg-mono">{new Date(t0).toISOString().slice(11, 19)}</text>
      <text x={960} y={h - 4} textAnchor="end" className="svg-mono">{new Date(t1).toISOString().slice(11, 19)} UTC</text>
    </svg>
  );
}
