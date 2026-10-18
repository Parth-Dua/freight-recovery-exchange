import type { Decision, IncidentDetail, IncidentSummary, Projection, PublicMarketEvent, ReplayBundle, SessionDetail } from "./contracts";

// GET only. The dashboard has no way to cancel, book, approve, pay, retry or re-run anything.
async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { accept: "application/json" }, credentials: "same-origin" });
  if (!r.ok) throw new Error(`${r.status} ${path.split("?")[0]}`);
  return r.json() as Promise<T>;
}

const qs = (o: Record<string, string | undefined | null>) =>
  Object.entries(o).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`).join("&");

export const api = {
  state: () => get<Projection>("/api/ops/market/state"),
  events: (after?: string, limit = 300) => get<{ events: PublicMarketEvent[] }>(`/api/ops/events?${qs({ after, limit: String(limit) })}`),
  negotiations: (o: { incident_id?: string; request_id?: string; session_id?: string } = {}) =>
    get<{ sessions: SessionDetail[] }>(`/api/ops/negotiations?${qs(o)}`),
  incidents: () => get<{ incidents: IncidentSummary[] }>("/api/ops/incidents"),
  incident: (id: string) => get<IncidentDetail>(`/api/ops/incidents/${encodeURIComponent(id)}`),
  replay: (o: { incident_id?: string; from?: string; to?: string }) => get<ReplayBundle>(`/api/ops/replay?${qs(o)}`),
  analytics: (metric: string, o: Record<string, string | undefined> = {}) => get<any>(`/api/ops/analytics?${qs({ metric, ...o })}`),
  decision: (kind: string, id: string) => get<Decision>(`/api/ops/decisions/${kind}/${encodeURIComponent(id)}`),
  provenance: () => get<any>("/api/ops/provenance"),
  health: () => get<any>("/api/ops/health"),
};
