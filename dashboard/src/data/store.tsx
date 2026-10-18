import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { InspectTarget, Projection, PublicMarketEvent } from "./contracts";
import { applyEvent, byTime } from "./reducer";

type Conn = "connecting" | "live" | "reconnecting" | "offline";
type Listener = (e: PublicMarketEvent) => void;

interface LiveState {
  projection: Projection | null;
  events: PublicMarketEvent[];
  conn: Conn;
  lastEventAt: string | null;
  lastSnapshotAt: string | null;
  caughtUp: number;
  error: string | null;
  subscribe: (fn: Listener) => () => void;
  resnapshot: () => Promise<void>;
}

const LiveCtx = createContext<LiveState | null>(null);
const MAX_EVENTS = 600;

/** One app-wide live projection: authoritative snapshot + SSE deltas through the pure reducer, re-hydrated every
 *  30 s and after a sleeping tab wakes (never replays a backlog as if it were live). */
export function LiveProvider({ children }: { children: ReactNode }) {
  const [projection, setProjection] = useState<Projection | null>(null);
  const [events, setEvents] = useState<PublicMarketEvent[]>([]);
  const [conn, setConn] = useState<Conn>("connecting");
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);
  const [lastSnapshotAt, setLastSnapshotAt] = useState<string | null>(null);
  const [caughtUp, setCaughtUp] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const listeners = useRef(new Set<Listener>());
  const seen = useRef(new Set<string>());
  const hiddenAt = useRef<number | null>(null);
  const source = useRef<EventSource | null>(null);

  const resnapshot = useCallback(async () => {
    try {
      const p = await api.state();
      setProjection(p);
      setLastSnapshotAt(new Date().toISOString());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const open = useCallback((cursor?: string | null) => {
    source.current?.close();
    if (new URLSearchParams(window.location.search).has("nostream")) { setConn("offline"); return; }  // static capture mode
    const es = new EventSource(`/api/ops/events/stream${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`);
    source.current = es;
    es.onopen = () => setConn("live");
    es.onerror = () => setConn(es.readyState === EventSource.CLOSED ? "offline" : "reconnecting");
    es.addEventListener("resnapshot", () => { void resnapshot(); });
    es.addEventListener("market", (msg) => {
      const e = JSON.parse((msg as MessageEvent).data) as PublicMarketEvent;
      if (seen.current.has(e.eventId)) return;
      seen.current.add(e.eventId);
      setConn("live");
      setLastEventAt(e.eventTime);
      setEvents((xs) => [...xs, e].sort(byTime).slice(-MAX_EVENTS));
      setProjection((p) => (p ? applyEvent(p, e) : p));
      if (hiddenAt.current === null) listeners.current.forEach((fn) => fn(e));
    });
  }, [resnapshot]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [p, ev] = await Promise.all([api.state(), api.events(undefined, 400)]);
        if (!alive) return;
        ev.events.forEach((e) => seen.current.add(e.eventId));
        setProjection(p);
        setEvents(ev.events.slice(-MAX_EVENTS));
        setLastEventAt(ev.events[ev.events.length - 1]?.eventTime || null);
        setLastSnapshotAt(new Date().toISOString());
        open(p.cursor || new Date().toISOString());
      } catch (e) {
        setError(String(e));
        setConn("offline");
      }
    })();
    const timer = window.setInterval(() => { void resnapshot(); }, 30_000);
    const onVis = async () => {
      if (document.hidden) { hiddenAt.current = Date.now(); return; }
      const slept = hiddenAt.current ? Date.now() - hiddenAt.current : 0;
      hiddenAt.current = null;
      if (slept > 60_000) {  // re-hydrate; report how much happened, but don't animate the backlog
        const before = seen.current.size;
        await resnapshot();
        const ev = await api.events(undefined, 400);
        let fresh = 0;
        ev.events.forEach((e) => { if (!seen.current.has(e.eventId)) { fresh++; seen.current.add(e.eventId); } });
        setEvents(ev.events.slice(-MAX_EVENTS));
        setCaughtUp(fresh || seen.current.size - before);
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => { alive = false; window.clearInterval(timer); document.removeEventListener("visibilitychange", onVis); source.current?.close(); };
  }, [open, resnapshot]);

  const subscribe = useCallback((fn: Listener) => { listeners.current.add(fn); return () => { listeners.current.delete(fn); }; }, []);
  const value = useMemo(() => ({ projection, events, conn, lastEventAt, lastSnapshotAt, caughtUp, error, subscribe, resnapshot }),
    [projection, events, conn, lastEventAt, lastSnapshotAt, caughtUp, error, subscribe, resnapshot]);
  return <LiveCtx.Provider value={value}>{children}</LiveCtx.Provider>;
}

export const useLive = () => { const v = useContext(LiveCtx); if (!v) throw new Error("LiveProvider missing"); return v; };

// ---- routing + UI state --------------------------------------------------------------------------
export type Route = { page: "mission" | "universe" | "warroom" | "replay" | "intel"; params: Record<string, string> };

export function parseHash(h = window.location.hash): Route {
  const [path, query] = h.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  const params: Record<string, string> = {};
  new URLSearchParams(query || "").forEach((v, k) => { params[k] = v; });
  const page = (["mission", "universe", "warroom", "replay", "intel"].includes(parts[0]) ? parts[0] : "mission") as Route["page"];
  if (page === "warroom" && parts[1]) params.incident = decodeURIComponent(parts[1]);
  return { page, params };
}

export const href = (page: Route["page"], params: Record<string, string | undefined | null> = {}) => {
  const { incident, ...rest } = params;
  const base = page === "warroom" && incident ? `#/warroom/${encodeURIComponent(incident)}` : `#/${page}`;
  const q = Object.entries(page === "warroom" ? rest : params).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`).join("&");
  return q ? `${base}?${q}` : base;
};
export const navigate = (page: Route["page"], params: Record<string, string | undefined | null> = {}) => { window.location.hash = href(page, params); };

interface UiState {
  route: Route;
  inspect: InspectTarget | null; setInspect: (t: InspectTarget | null) => void;
  reduceMotion: boolean; setReduceMotion: (v: boolean) => void;
  demoFocus: boolean; setDemoFocus: (v: boolean) => void;
  provenanceOpen: boolean; setProvenanceOpen: (v: boolean) => void;
}
const UiCtx = createContext<UiState | null>(null);

export function UiProvider({ children }: { children: ReactNode }) {
  const [route, setRoute] = useState<Route>(parseHash());
  const [inspect, setInspect] = useState<InspectTarget | null>(null);
  const prefersReduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const [reduceMotion, setReduceMotion] = useState<boolean>(() => { try { return localStorage.getItem("hx.reduceMotion") === "1" || !!prefersReduced; } catch { return !!prefersReduced; } });
  const [demoFocus, setDemoFocus] = useState<boolean>(() => { try { return localStorage.getItem("hx.demoFocus") === "1"; } catch { return false; } });
  const [provenanceOpen, setProvenanceOpen] = useState(false);
  useEffect(() => { const f = () => setRoute(parseHash()); window.addEventListener("hashchange", f); return () => window.removeEventListener("hashchange", f); }, []);
  useEffect(() => { try { localStorage.setItem("hx.reduceMotion", reduceMotion ? "1" : "0"); } catch { /* storage unavailable */ } }, [reduceMotion]);
  useEffect(() => { try { localStorage.setItem("hx.demoFocus", demoFocus ? "1" : "0"); } catch { /* storage unavailable */ } }, [demoFocus]);
  const value = useMemo(() => ({ route, inspect, setInspect, reduceMotion, setReduceMotion, demoFocus, setDemoFocus, provenanceOpen, setProvenanceOpen }),
    [route, inspect, reduceMotion, demoFocus, provenanceOpen]);
  return <UiCtx.Provider value={value}>{children}</UiCtx.Provider>;
}
export const useUi = () => { const v = useContext(UiCtx); if (!v) throw new Error("UiProvider missing"); return v; };
