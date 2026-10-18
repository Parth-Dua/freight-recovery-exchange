export const money = (minor?: number | null, opts: { simulated?: boolean } = {}) => {
  if (minor === null || minor === undefined) return "—";
  const v = minor / 100;
  const s = v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: minor % 100 === 0 ? 0 : 2 });
  return opts.simulated ? `${s} sim.` : s;
};

export const clock = (iso?: string | null, seconds = true) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toISOString().slice(11, seconds ? 19 : 16);
};

export const day = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }) + " " + d.toISOString().slice(11, 16);
};

export const ago = (iso?: string | null, now = Date.now()) => {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
};

export const statusClass = (s?: string | null) => {
  const k = (s || "").toUpperCase();
  const map: Record<string, string> = {
    ACTIVE: "s-active", OPEN: "s-open", NEGOTIATING: "s-negotiating", WITHDRAWN: "s-withdrawn", DISRUPTED: "s-disrupted",
    AWAITING_APPROVAL: "s-awaiting", ATTENTION: "s-attention", IMPACT_ASSESSED: "s-attention", WITHDRAWN_STAGE: "s-withdrawn",
    PROPOSED: "s-proposed", ALLOCATION_PROPOSED: "s-proposed", CLEARING: "s-clearing", HELD: "s-held", HOLDING: "s-holding",
    CONFIRMING: "s-confirming", CONFIRMED: "s-confirmed", COMMITTED: "s-committed", RESOLVED: "s-resolved", ACCEPTED: "s-accepted",
    FAILED: "s-failed", UNRESOLVED: "s-unresolved", REJECTED: "s-rejected", EXPIRED: "s-expired", SUPERSEDED: "s-superseded",
    EXHAUSTED: "s-exhausted", PARTIAL: "s-partial", RELEASED: "s-muted", APPROVED: "s-confirming", REQUESTED: "s-awaiting",
  };
  return map[k] || "s-muted";
};

export const human = (s?: string | null) => (s || "").replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

export const CARRIER_SHORT: Record<string, string> = { atlas: "Atlas", beacon: "Beacon", cove: "Cove", meridian: "Meridian", dhow: "Dhow" };

export const BUYER_COLORS = ["#43d9cb", "#f0c75e", "#e88ad6", "#8fd16a", "#ff9f80", "#7fc8ff", "#c3a6ff", "#f5d0a9"];
export const buyerColor = (node?: string | null) => {
  if (!node) return "#43d9cb";
  const letter = node.split("-")[1] || "a";
  const i = (letter.charCodeAt(0) - 97) % BUYER_COLORS.length;
  return BUYER_COLORS[(i + BUYER_COLORS.length) % BUYER_COLORS.length];
};
