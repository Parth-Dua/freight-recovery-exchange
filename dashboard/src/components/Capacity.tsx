import type { IncidentDetail, Projection, PublicSailing } from "../data/contracts";
import { buyerColor, day } from "../data/format";
import { Status } from "./common";

/** Capacity bar: explicit committed / held / available / withdrawn counts (never one ambiguous percentage). */
export function CapacityBar({ s }: { s: PublicSailing }) {
  const seg = (n: number, color: string, label: string) => n > 0 && (
    <div title={`${n} ${label}`} style={{ flex: n, background: color, height: "100%" }} />
  );
  return (
    <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", background: "#0b1828", border: "1px solid var(--border-soft)" }}>
      {seg(s.committedUnits, "#4f6f92", "committed")}
      {seg(s.heldUnits, "var(--held-blue)", "held")}
      {seg(s.availableUnits, "rgba(67,217,203,.55)", "available")}
      {seg(s.withdrawnUnits, "rgba(245,174,89,.55)", "withdrawn")}
    </div>
  );
}

export function SailingStrip({ projection, onPick, limit = 12, selected }: { projection: Projection; onPick?: (s: PublicSailing) => void; limit?: number; selected?: string | null }) {
  const carriers = new Map(projection.carriers.map((c) => [c.carrierId, c.displayName]));
  const list = [...projection.sailings].sort((a, b) => (a.status === "WITHDRAWN" ? -1 : 0) - (b.status === "WITHDRAWN" ? -1 : 0) || a.departureAt.localeCompare(b.departureAt)).slice(0, limit);
  return (
    <table className="t">
      <thead><tr><th>Sailing</th><th>Carrier</th><th>Departs</th><th style={{ width: "34%" }}>20GP capacity</th><th>Status</th></tr></thead>
      <tbody>
        {list.map((s) => (
          <tr key={s.sailingId} className="click" onClick={() => onPick?.(s)} style={s.sailingId === selected ? { background: "var(--surface-2)" } : undefined}>
            <td className="mono">{s.sailingId}<div className="muted" style={{ fontSize: 11 }}>{s.origin} → {s.destination}</div></td>
            <td>{carriers.get(s.carrierId)}</td>
            <td className="mono">{day(s.departureAt)}</td>
            <td><CapacityBar s={s} /><div className="mono muted" style={{ fontSize: 10.5, marginTop: 3 }}>{s.committedUnits} committed · {s.heldUnits} held · {s.availableUnits} free{s.withdrawnUnits ? ` · ${s.withdrawnUnits} withdrawn` : ""}</div></td>
            <td><Status s={s.status} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

type Tile = { key: string; state: "committed" | "held-other" | "available" | "stranded" | "proposed" | "held" | "confirmed" | "withdrawn" | "moved"; buyer?: string | null; node?: string | null; note?: string };

const TILE_STYLE: Record<Tile["state"], { fill: string; stroke: string; dash?: string; label: string }> = {
  committed: { fill: "#2b4562", stroke: "#3d5c7f", label: "Committed (other buyers)" },
  "held-other": { fill: "#26406a", stroke: "#3b5f99", label: "Held (other)" },
  available: { fill: "transparent", stroke: "#2c4a66", label: "Available" },
  stranded: { fill: "rgba(245,174,89,.22)", stroke: "#f5ae59", label: "Stranded" },
  moved: { fill: "transparent", stroke: "#5b6f84", dash: "3 3", label: "Moved out" },
  proposed: { fill: "rgba(180,154,240,.10)", stroke: "#b49af0", dash: "4 3", label: "Proposed (not held)" },
  held: { fill: "rgba(111,159,240,.32)", stroke: "#6f9ff0", label: "Held for recovery" },
  confirmed: { fill: "rgba(84,214,155,.35)", stroke: "#54d69b", label: "Confirmed replacement" },
  withdrawn: { fill: "transparent", stroke: "#4a3a26", dash: "2 3", label: "Withdrawn space" },
};

/** One tile per actual 20GP slot. Proposed ≠ held ≠ confirmed; green only after carrier confirmation + commit. */
export function AllocationBoard({ incident, projection }: { incident: IncidentDetail; projection: Projection }) {
  const sailings = new Map(projection.sailings.map((s) => [s.sailingId, s]));
  const carriers = new Map(projection.carriers.map((c) => [c.carrierId, c.displayName]));
  const withdrawn = sailings.get(incident.sailingId);
  const rows: { s: PublicSailing; tiles: Tile[]; role: string }[] = [];

  if (withdrawn) {
    const tiles: Tile[] = [];
    for (const a of incident.affectedBookings) {
      for (let i = 0; i < a.quantity; i++) {
        tiles.push(a.state === "CONFIRMED"
          ? { key: `${a.bookingId}-${i}`, state: "moved", buyer: a.buyer, node: a.buyerNode, note: `→ ${a.replacement?.sailingId}` }
          : { key: `${a.bookingId}-${i}`, state: "stranded", buyer: a.buyer, node: a.buyerNode, note: a.state });
      }
    }
    const rest = Math.max(0, withdrawn.totalUnits - tiles.length);
    for (let i = 0; i < rest; i++) tiles.push({ key: `w-${i}`, state: "withdrawn" });
    rows.push({ s: withdrawn, tiles, role: "Withdrawn" });
  }
  // Candidate / replacement sailings: from executed legs, else from the proposed joint assignment.
  const per = new Map<string, Tile[]>();
  for (const a of incident.affectedBookings) {
    const latest = a.legs.filter((l) => l.attempt === Math.max(...a.legs.map((x) => x.attempt)));
    if (latest.length) {
      for (const l of latest) {
        const state: Tile["state"] = l.status === "CONFIRMED" ? "confirmed" : l.status === "HELD" ? "held" : "proposed";
        if (l.status === "FAILED" || l.status === "RELEASED") continue;
        for (let i = 0; i < l.quantity; i++) per.set(l.sailingId, [...(per.get(l.sailingId) || []), { key: `${a.bookingId}-${l.sailingId}-${i}`, state, buyer: a.buyer, node: a.buyerNode, note: a.state === "AWAITING_APPROVAL" ? "awaiting approval" : undefined }]);
      }
    } else if (incident.clearing) {
      for (const as of incident.clearing.joint.assignments.filter((x) => x.bookingId === a.bookingId)) {
        for (let i = 0; i < as.quantity; i++) per.set(as.sailingId, [...(per.get(as.sailingId) || []), { key: `${a.bookingId}-${as.sailingId}-${i}`, state: "proposed", buyer: a.buyer, node: a.buyerNode }]);
      }
    }
  }
  for (const [sid, mine] of per) {
    const s = sailings.get(sid);
    if (!s) continue;
    const confirmedHere = mine.filter((t) => t.state === "confirmed").length;
    const heldHere = mine.filter((t) => t.state === "held").length;
    const tiles: Tile[] = [...mine];
    for (let i = 0; i < Math.max(0, s.committedUnits - confirmedHere); i++) tiles.push({ key: `c-${i}`, state: "committed" });
    for (let i = 0; i < Math.max(0, s.heldUnits - heldHere); i++) tiles.push({ key: `h-${i}`, state: "held-other" });
    const proposedHere = mine.filter((t) => t.state === "proposed").length;
    for (let i = 0; i < Math.max(0, s.availableUnits - proposedHere); i++) tiles.push({ key: `a-${i}`, state: "available" });
    rows.push({ s, tiles, role: "Replacement" });
  }

  const size = 26, gap = 6;
  return (
    <div>
      {rows.map(({ s, tiles, role }) => (
        <div key={s.sailingId} style={{ display: "grid", gridTemplateColumns: "150px 1fr", gap: 12, alignItems: "center", padding: "8px 0", borderBottom: "1px solid var(--border-soft)" }}>
          <div>
            <div className="mono" style={{ color: role === "Withdrawn" ? "var(--withdrawal-amber)" : "var(--text-primary)" }}>{s.sailingId}</div>
            <div className="muted" style={{ fontSize: 11.5 }}>{carriers.get(s.carrierId)} · {role}</div>
            <div className="muted mono" style={{ fontSize: 10.5 }}>arr {day(s.arrivalAt)}</div>
          </div>
          <svg width="100%" height={size + 18} viewBox={`0 0 ${Math.max(8, tiles.length) * (size + gap)} ${size + 18}`} preserveAspectRatio="xMinYMid meet">
            {tiles.map((t, i) => {
              const st = TILE_STYLE[t.state];
              return (
                <g key={t.key} transform={`translate(${i * (size + gap)},2)`} className="tile">
                  <title>{`${st.label}${t.buyer ? ` · ${t.buyer}` : ""}${t.note ? ` · ${t.note}` : ""}`}</title>
                  <rect width={size} height={size} rx={5} fill={st.fill} stroke={t.node && t.state !== "moved" ? buyerColor(t.node) : st.stroke} strokeWidth={t.node ? 1.6 : 1} strokeDasharray={st.dash} />
                  {t.buyer && <text x={size / 2} y={size / 2 + 4} textAnchor="middle" style={{ fontSize: 11, fontWeight: 700, fill: t.state === "moved" ? "#7b96ae" : buyerColor(t.node), fontFamily: "var(--font)" }}>{t.buyer.replace("Buyer ", "")}</text>}
                  {t.state === "confirmed" && <text x={size - 4} y={9} textAnchor="end" style={{ fontSize: 8, fill: "#54d69b" }}>✓</text>}
                  {t.note && t.state === "moved" && i % 2 === 0 && <text x={0} y={size + 13} className="svg-mono" style={{ fontSize: 9 }}>{t.note}</text>}
                </g>
              );
            })}
          </svg>
        </div>
      ))}
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 10 }}>
        {(["stranded", "proposed", "held", "confirmed", "committed", "available"] as const).map((k) => (
          <span key={k} className="muted" style={{ fontSize: 11.5, display: "inline-flex", gap: 6, alignItems: "center" }}>
            <svg width="14" height="14"><rect x="1" y="1" width="12" height="12" rx="3" fill={TILE_STYLE[k].fill} stroke={TILE_STYLE[k].stroke} strokeDasharray={TILE_STYLE[k].dash} /></svg>{TILE_STYLE[k].label}
          </span>
        ))}
      </div>
    </div>
  );
}
