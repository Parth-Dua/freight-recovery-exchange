import { useMemo } from "react";
import { geoMercator, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import land50 from "world-atlas/land-50m.json";
import type { Projection, PublicSailing } from "../data/contracts";
import { day } from "../data/format";

// Simulated maritime view: real port coordinates, simulated lanes, vessel glyphs at berth until their scheduled
// departure and interpolated along the lane while under way. Labelled SIMULATED; never AIS or satellite data.
const W = 960, H = 540;
const landGeo = feature(land50 as any, (land50 as any).objects.land) as any;
// Fit the projection to the market's ports (Dammam → Mumbai) with room for labels and berth fans.
const proj = geoMercator().fitExtent([[70, 50], [W - 70, H - 60]], {
  type: "MultiPoint", coordinates: [[50.0, 27.4], [73.3, 18.6], [56.8, 26.9], [66.9, 25.2]],
} as any);
const pathGen = geoPath(proj);
const landPath = pathGen(landGeo) || "";

type Pt = [number, number];
function polyline(route: [number, number][]): Pt[] { return route.map(([lon, lat]) => proj([lon, lat]) as Pt); }
function along(pts: Pt[], f: number): { p: Pt; angle: number } {
  const segs = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]));
  const total = segs.reduce((a, b) => a + b, 0);
  let d = Math.max(0, Math.min(1, f)) * total;
  for (let i = 0; i < segs.length; i++) {
    if (d <= segs[i] || i === segs.length - 1) {
      const t = segs[i] ? d / segs[i] : 0, a = pts[i], b = pts[i + 1];
      return { p: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], angle: Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI };
    }
    d -= segs[i];
  }
  return { p: pts[0], angle: 0 };
}

export interface MapProps {
  projection: Projection;
  at: string;                         // live now or Time Machine virtual time
  selectedSailing?: string | null;
  focusSailings?: string[];           // e.g. withdrawn + replacement candidates in the War Room
  onSelect?: (s: PublicSailing) => void;
  height?: number;
  compactLegend?: boolean;
  arcs?: { from: string; to: string; color: string; dashed?: boolean }[]; // sailing → sailing arcs (allocations)
}

export function MaritimeMap({ projection, at, selectedSailing, focusSailings = [], onSelect, height, compactLegend, arcs = [] }: MapProps) {
  const t = new Date(at).getTime();
  const corridors = useMemo(() => new Map(projection.corridors.map((c) => [c.corridorId, polyline(c.route)])), [projection.corridors]);
  const ports = projection.ports.map((p) => ({ ...p, xy: proj([p.lon, p.lat]) as Pt }));
  const carriers = new Map(projection.carriers.map((c) => [c.carrierId, c.displayName]));

  // Berth fan per origin port (ships waiting for their scheduled departure), positions along the lane when under way.
  const glyphs = useMemo(() => {
    const byOrigin = new Map<string, PublicSailing[]>();
    for (const s of projection.sailings) {
      const dep = new Date(s.departureAt).getTime();
      if (t < dep || s.status === "WITHDRAWN") byOrigin.set(s.origin, [...(byOrigin.get(s.origin) || []), s]);
    }
    const out: { s: PublicSailing; p: Pt; angle: number; mode: "berth" | "underway" | "arrived" }[] = [];
    for (const [origin, list] of byOrigin) {
      const port = ports.find((p) => p.portCode === origin);
      if (!port) continue;
      const seaward = corridors.get(`${origin}-${list[0].destination}`)?.[1] || [port.xy[0] + 30, port.xy[1]];
      const base = Math.atan2(seaward[1] - port.xy[1], seaward[0] - port.xy[0]);
      list.sort((a, b) => a.departureAt.localeCompare(b.departureAt)).forEach((s, i) => {
        const ring = Math.floor(i / 7), k = i % 7;
        const ang = base + (k - 3) * 0.33 + ring * 0.16;
        const r = 30 + ring * 15;
        out.push({ s, p: [port.xy[0] + Math.cos(ang) * r, port.xy[1] + Math.sin(ang) * r], angle: ang * 180 / Math.PI, mode: "berth" });
      });
    }
    for (const s of projection.sailings) {
      const dep = new Date(s.departureAt).getTime(), arr = new Date(s.arrivalAt).getTime();
      if (s.status === "WITHDRAWN" || t < dep) continue;
      const pts = corridors.get(s.corridorId);
      if (!pts) continue;
      const f = (t - dep) / Math.max(1, arr - dep);
      const { p, angle } = along(pts, f);
      out.push({ s, p, angle, mode: f >= 1 ? "arrived" : "underway" });
    }
    return out;
  }, [projection.sailings, corridors, ports, t]);

  const sailingPos = new Map(glyphs.map((g) => [g.s.sailingId, g.p]));
  const selected = projection.sailings.find((s) => s.sailingId === selectedSailing);
  const withdrawnCorridors = new Set(projection.sailings.filter((s) => s.status === "WITHDRAWN").map((s) => s.corridorId));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: height ?? "auto", display: "block" }} role="img" aria-label="Simulated maritime market map">
      <defs>
        <radialGradient id="sea" cx="55%" cy="45%" r="75%"><stop offset="0%" stopColor="#0a1a2d" /><stop offset="100%" stopColor="#050f1b" /></radialGradient>
        <linearGradient id="land" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#1f3550" /><stop offset="100%" stopColor="#182b42" /></linearGradient>
        <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#11263d" strokeWidth="0.6" /></pattern>
        <filter id="glow"><feGaussianBlur stdDeviation="2.4" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <rect width={W} height={H} fill="url(#sea)" />
      <rect width={W} height={H} fill="url(#grid)" opacity="0.7" />
      <path d={landPath} fill="url(#land)" stroke="#3a5a7c" strokeWidth="0.9" />
      {/* lanes */}
      {[...corridors.entries()].map(([id, pts]) => (
        <g key={id}>
          <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke="#43d9cb" strokeOpacity="0.16" strokeWidth="5" strokeLinejoin="round" />
          <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke={withdrawnCorridors.has(id) ? "#6bc9c0" : "#43d9cb"} strokeOpacity="0.55" strokeWidth="1.2"
                    strokeDasharray="2 7" strokeLinecap="round" className="lane-flow" />
        </g>
      ))}
      {selected && corridors.get(selected.corridorId) && (
        <polyline points={corridors.get(selected.corridorId)!.map((p) => p.join(",")).join(" ")} fill="none"
                  stroke={selected.status === "WITHDRAWN" ? "#f5ae59" : "#78afff"} strokeWidth="2.4" strokeDasharray={selected.status === "WITHDRAWN" ? "7 6" : undefined} filter="url(#glow)" />
      )}
      {/* allocation arcs (War Room / Time Machine) */}
      {arcs.map((a, i) => {
        const p1 = sailingPos.get(a.from), p2 = sailingPos.get(a.to);
        if (!p1 || !p2) return null;
        const mx = (p1[0] + p2[0]) / 2, my = Math.min(p1[1], p2[1]) - 40;
        return <path key={i} d={`M${p1[0]},${p1[1]} Q${mx},${my} ${p2[0]},${p2[1]}`} fill="none" stroke={a.color} strokeWidth="1.8" strokeDasharray={a.dashed ? "4 4" : undefined} opacity="0.9" />;
      })}
      {/* ports */}
      {ports.map((p) => (
        <g key={p.portCode} transform={`translate(${p.xy[0]},${p.xy[1]})`}>
          <circle r="4.5" fill="#08121f" stroke="#a3b8cd" strokeWidth="1.4" />
          <text x={p.portCode === "INBOM" || p.portCode === "INMUN" ? 9 : -9} y={p.portCode === "OMSOH" ? 16 : -9}
                textAnchor={p.portCode === "INBOM" || p.portCode === "INMUN" ? "start" : "end"} className="svg-label strong">{p.name}</text>
        </g>
      ))}
      {/* vessels */}
      {glyphs.map(({ s, p, angle, mode }) => {
        const withdrawn = s.status === "WITHDRAWN";
        const focus = focusSailings.includes(s.sailingId) || s.sailingId === selectedSailing;
        const color = withdrawn ? "#f5ae59" : s.availableUnits === 0 && s.heldUnits > 0 ? "#6f9ff0" : "#78afff";
        return (
          <g key={s.sailingId} transform={`translate(${p[0]},${p[1]})`} style={{ cursor: onSelect ? "pointer" : "default" }} onClick={() => onSelect?.(s)}>
            <title>{`${carriers.get(s.carrierId)} · ${s.vesselName || s.vesselId} · ${s.sailingId}\n${s.origin} → ${s.destination} · departs ${day(s.departureAt)} · arrives ${day(s.arrivalAt)}\n20GP: ${s.committedUnits} committed / ${s.heldUnits} held / ${s.availableUnits} available${s.withdrawnUnits ? ` / ${s.withdrawnUnits} withdrawn` : ""}\n${withdrawn ? "WITHDRAWN by carrier" : mode === "berth" ? "At berth until departure" : "Under way"} · simulated position`}</title>
            {withdrawn && <circle r="11" fill="none" stroke="#f5ae59" strokeWidth="1.2" className="ripple" />}
            {focus && <circle r="10" fill="none" stroke={withdrawn ? "#f5ae59" : "#b49af0"} strokeWidth="1.6" filter="url(#glow)" />}
            <g transform={`rotate(${angle})`}>
              <path d="M7,0 L-5,-4 L-3,0 L-5,4 Z" fill={color} fillOpacity={mode === "berth" ? 0.85 : 1} stroke="#08121f" strokeWidth="0.8" />
            </g>
            {(focus || withdrawn) && <text x="10" y="4" className="svg-mono" style={{ fill: withdrawn ? "#f5ae59" : "#cfdcf0" }}>{s.sailingId}</text>}
          </g>
        );
      })}
      <g transform={`translate(16,${H - (compactLegend ? 22 : 54)})`}>
        <rect width={compactLegend ? 268 : 330} height={compactLegend ? 16 : 44} rx="6" fill="#08121f" fillOpacity="0.72" />
        <text x="10" y="12" className="svg-mono">Simulated vessel positions · simulated lanes</text>
        {!compactLegend && <>
          <path transform="translate(14,30)" d="M7,0 L-5,-4 L-3,0 L-5,4 Z" fill="#78afff" /><text x="26" y="34" className="svg-label">Active sailing</text>
          <path transform="translate(118,30)" d="M7,0 L-5,-4 L-3,0 L-5,4 Z" fill="#f5ae59" /><text x="130" y="34" className="svg-label">Withdrawn</text>
          <path transform="translate(210,30)" d="M7,0 L-5,-4 L-3,0 L-5,4 Z" fill="#6f9ff0" /><text x="222" y="34" className="svg-label">Full (held)</text>
        </>}
      </g>
    </svg>
  );
}
