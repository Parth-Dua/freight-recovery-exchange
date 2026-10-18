import { useEffect, useState } from "react";
import { api } from "../data/api";
import type { SessionDetail } from "../data/contracts";
import { clock, money } from "../data/format";
import { Status } from "./common";

const KIND: Record<string, string> = {
  OPENING_OFFER: "Opening quote", BUYER_COUNTER: "Buyer agent proposed", CARRIER_COUNTER: "Carrier came back", ACCEPT: "Carrier accepted",
  REJECT: "Carrier declined counter", NO_REPLY: "No valid reply", EXPIRED: "Expired",
};
const REASON: Record<string, string> = { COMPETITOR_COMPARISON: "competitor comparison", BOOK_NOW_OFFER: "offered to book now", COUNTER_STRATEGY: "counter strategy" };

/** Selected session: precise chronological timeline + price ladder from persisted public messages only. */
export function NegotiationTimeline({ sessionId, carrierName, at, replaySteps, refreshKey }: {
  sessionId: string; carrierName?: string; at?: string; replaySteps?: SessionDetail | null; refreshKey?: unknown;
}) {
  const [s, setS] = useState<SessionDetail | null>(replaySteps || null);
  useEffect(() => {
    if (replaySteps) { setS(replaySteps); return; }
    let alive = true;
    api.negotiations({ session_id: sessionId }).then((r) => { if (alive) setS(r.sessions[0] || null); }).catch(() => setS(null));
    return () => { alive = false; };
  }, [sessionId, replaySteps, refreshKey]);
  if (!s) return <div className="muted">Loading session…</div>;
  const steps = s.steps.filter((x) => !at || x.at <= at);
  const amounts = steps.map((x) => x.amountMinor || 0).filter(Boolean);
  const max = Math.max(...amounts, 1), min = Math.min(...amounts, max) * 0.9;
  const status = at && s.closedAt && s.closedAt > at ? "OPEN" : s.status;
  const opening = s.openingMinor, final = s.finalMinor;
  const improved = s.termsComparableToOpening && opening && final && final < opening;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <strong>{s.buyer} agent ↔ {carrierName || s.carrierId}</strong><Status s={status} />
      </div>
      <div className="mono muted" style={{ fontSize: 11 }}>{s.sessionId} · {s.kind.toLowerCase()} · {s.round}/{s.roundsMax ?? 2} counters</div>
      <div className="muted" style={{ fontSize: 12 }}>Eligible: {s.eligibility}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {steps.map((x) => (
          <div key={x.eventId} style={{ display: "grid", gridTemplateColumns: "58px 1fr", gap: 8, alignItems: "center" }}>
            <span className="mono muted" style={{ fontSize: 11 }}>{clock(x.at)}</span>
            <div>
              <div style={{ fontSize: 12.5, display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ color: x.senderKind === "BUYER_AGENT" ? "var(--agent-cyan)" : "var(--carrier-blue)" }}>{KIND[x.messageKind] || x.messageKind}{x.counterRound ? ` · r${x.counterRound}` : ""}</span>
                <span className="mono">{x.amountMinor ? `${x.proposed ? "Proposed " : ""}${money(x.amountMinor)}` : "—"}</span>
              </div>
              {x.amountMinor ? <div style={{ height: 5, borderRadius: 3, marginTop: 4, background: "#0b1828" }}>
                <div style={{ height: "100%", width: `${Math.max(6, ((x.amountMinor - min) / (max - min || 1)) * 100)}%`, borderRadius: 3,
                              background: x.proposed ? "transparent" : x.messageKind === "ACCEPT" ? "var(--confirmed-green)" : "var(--carrier-blue)",
                              border: x.proposed ? "1px dashed var(--agent-cyan)" : undefined }} />
              </div> : null}
              {x.explanationCode && <div className="muted" style={{ fontSize: 11 }}>reason: {REASON[x.explanationCode] || x.explanationCode}{x.signedSenderVerified ? "" : ""}</div>}
              {x.signedSenderVerified && <div className="muted" style={{ fontSize: 11 }}>signed carrier reply · verified</div>}
            </div>
          </div>
        ))}
        {!steps.length && <div className="muted">No messages recorded yet.</div>}
      </div>
      <div className="panel" style={{ padding: 10, background: "var(--bg)" }}>
        <div className="kv" style={{ gridTemplateColumns: "110px 1fr" }}>
          <dt>Opening</dt><dd className="mono">{money(opening)}</dd>
          <dt>Final offer</dt><dd className="mono">{status === "OPEN" ? "pending" : final ? money(final) : "No final eligible offer"}</dd>
          <dt>vs own opening</dt><dd>{status === "OPEN" ? "—" : improved ? `−${money(opening! - final!)} (${(((opening! - final!) / opening!) * 100).toFixed(1)}%)` : "no comparable decrease"}</dd>
        </div>
      </div>
      <div className="muted" style={{ fontSize: 11 }}>Never shown: buyer budget, auto-rebook limit, must-deliver flag, carrier floor or policy.</div>
    </div>
  );
}
