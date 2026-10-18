"""Market Intelligence, Data Provenance, Time Machine bundles and the Decision Inspector (all read-only).

Every query is a fixed template with bound parameters (no arbitrary SQL from the browser). Each analytics
answer carries its own provenance: SQL template id, grain, source (raw hypertable / continuous aggregate /
aggregate + raw tail), watermark and row counts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from freight_exchange.ops.read_model import (
    buyer_labels, event_context, incident_detail, iso, negotiations, projection, public_events, rows)

GRAINS = {"1m": "1 minute", "5m": "5 minutes", "15m": "15 minutes", "1h": "1 hour"}

SQL = {
    "deals": """
SELECT time_bucket(%(grain)s::interval, s.closed_at) AS bucket, s.kind,
       count(*) AS sessions,
       count(*) FILTER (WHERE s.status = 'ACCEPTED') AS accepted,
       count(*) FILTER (WHERE s.status = 'EXHAUSTED') AS exhausted,
       count(*) FILTER (WHERE s.status = 'REJECTED' AND s.final_offer_id IS NULL) AS declined,
       count(*) FILTER (WHERE s.status = 'REJECTED' AND s.final_offer_id IS NOT NULL) AS counter_refused,
       count(*) FILTER (WHERE s.status = 'EXPIRED') AS timed_out,
       count(*) FILTER (WHERE comparable) AS comparable_sessions,
       avg(opening_minor::numeric / fo.quantity) FILTER (WHERE comparable) AS avg_opening_unit_minor,
       avg(fo.total_price_minor::numeric / fo.quantity) FILTER (WHERE comparable) AS avg_final_unit_minor,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (opening_minor - fo.total_price_minor)::numeric / opening_minor)
           FILTER (WHERE comparable) AS median_reduction_fraction,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM s.closed_at - s.opened_at)) AS median_seconds
FROM negotiation_sessions s
LEFT JOIN offers fo ON fo.offer_id = s.final_offer_id
LEFT JOIN LATERAL (
    SELECT (m.public_terms->>'total_price_minor')::bigint AS opening_minor, m.public_terms->>'offer_id' AS opening_id
    FROM negotiation_messages m WHERE m.session_id = s.session_id AND m.sender_role = 'carrier' AND m.round = 0
    LIMIT 1) op ON true
LEFT JOIN offers oo ON oo.offer_id = op.opening_id
CROSS JOIN LATERAL (SELECT (fo.offer_id IS NOT NULL AND oo.offer_id IS NOT NULL AND oo.sailing_id = fo.sailing_id
                           AND oo.quantity = fo.quantity AND oo.currency = fo.currency) AS comparable) c
WHERE s.closed_at >= %(start)s AND s.closed_at < %(end)s
  AND (%(kind)s::text IS NULL OR s.kind = %(kind)s)
  AND (%(carrier)s::text IS NULL OR s.carrier_id = %(carrier)s)
GROUP BY bucket, s.kind ORDER BY bucket, s.kind""",
    "capacity": """
WITH last_per_sailing AS (
    SELECT DISTINCT ON (time_bucket(%(grain)s::interval, ts), sailing_id)
           time_bucket(%(grain)s::interval, ts) AS bucket, sailing_id, corridor_id, status,
           total_units, committed_units, held_units, available_units
    FROM sailing_capacity_snapshots
    WHERE ts >= %(start)s AND ts < %(end)s
      AND (%(corridor)s::text IS NULL OR corridor_id = %(corridor)s)
      AND (%(carrier)s::text IS NULL OR carrier_id = %(carrier)s)
    ORDER BY time_bucket(%(grain)s::interval, ts), sailing_id, ts DESC)
SELECT bucket, sum(total_units) AS total_units, sum(committed_units) AS committed_units,
       sum(held_units) AS held_units, sum(available_units) AS available_units,
       sum(total_units) FILTER (WHERE status = 'WITHDRAWN') AS withdrawn_total,
       count(*) AS sailings_sampled,
       sum(committed_units)::double precision / NULLIF(sum(total_units), 0) AS committed_fraction,
       sum(held_units)::double precision / NULLIF(sum(total_units), 0) AS held_fraction
FROM last_per_sailing GROUP BY bucket ORDER BY bucket""",
    "activity_cagg": """
SELECT bucket, event_type, event_count FROM market_activity_1m
WHERE bucket >= %(start)s AND bucket < LEAST(%(end)s, %(watermark)s) ORDER BY bucket""",
    "activity_raw_tail": """
SELECT time_bucket(INTERVAL '1 minute', ts) AS bucket, event_type, count(*)::bigint AS event_count
FROM market_events WHERE ts >= GREATEST(%(start)s, %(watermark)s) AND ts < %(end)s
GROUP BY bucket, event_type ORDER BY bucket""",
    "recovery": """
SELECT r.recovery_id, r.sailing_id, r.status, r.created_at, r.attempts,
       (SELECT d.occurred_at FROM disruption_events d WHERE d.event_id = r.withdrawal_event_id) AS withdrawn_at,
       (SELECT count(DISTINCT l.booking_id) FROM recovery_legs l WHERE l.recovery_id = r.recovery_id) AS legs_bookings,
       (SELECT count(DISTINCT l.booking_id) FROM recovery_legs l JOIN bookings b ON b.booking_id = l.booking_id
         WHERE l.recovery_id = r.recovery_id AND l.status = 'CONFIRMED' AND b.status = 'SUPERSEDED') AS confirmed,
       (SELECT max(e.ts) FROM market_events e WHERE e.correlation_id = r.recovery_id
         AND e.event_type = 'RECOVERY_LEG_CONFIRMED') AS last_confirmed_at,
       (SELECT e.payload FROM market_events e WHERE e.correlation_id = r.recovery_id
         AND e.event_type = 'CLEARING_DONE' ORDER BY e.ts DESC LIMIT 1) AS clearing
FROM recovery_runs r WHERE r.created_at >= %(start)s AND r.created_at < %(end)s ORDER BY r.created_at""",
}


def _range(start: str | None, end: str | None, default_minutes: int = 180) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    e = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else now + timedelta(minutes=1)
    s = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else e - timedelta(minutes=default_minutes)
    return s, e


async def cagg_status(conn) -> dict:
    meta = await rows(conn, """
        SELECT ca.mat_hypertable_id, h.table_name AS mat_table
        FROM _timescaledb_catalog.continuous_agg ca
        JOIN _timescaledb_catalog.hypertable h ON h.id = ca.mat_hypertable_id
        WHERE ca.user_view_name = 'market_activity_1m'""")
    if not meta:
        return {"name": "market_activity_1m", "exists": False}
    mat_id = meta[0]["mat_hypertable_id"]
    watermark = None
    for fn in ("_timescaledb_functions", "_timescaledb_internal"):
        try:
            async with conn.transaction():
                watermark = (await (await conn.execute(
                    f"SELECT {fn}.to_timestamp({fn}.cagg_watermark(%s))", (mat_id,))).fetchone())[0]
            break
        except Exception:
            continue
    job = await rows(conn, """
        SELECT j.job_id, j.schedule_interval::text AS schedule_interval, js.last_successful_finish, js.last_run_status,
               js.next_start, j.config
        FROM timescaledb_information.jobs j LEFT JOIN timescaledb_information.job_stats js ON js.job_id = j.job_id
        WHERE j.proc_name = 'policy_refresh_continuous_aggregate'
          AND (j.config->>'mat_hypertable_id')::int = %s""", (mat_id,))
    j = job[0] if job else {}
    return {"name": "market_activity_1m", "exists": True, "materializationTable": meta[0]["mat_table"],
            "watermark": iso(watermark) if isinstance(watermark, datetime) else None,
            "refreshPolicy": {"jobId": j.get("job_id"), "scheduleInterval": j.get("schedule_interval"),
                              "startOffset": (j.get("config") or {}).get("start_offset"),
                              "endOffset": (j.get("config") or {}).get("end_offset"),
                              "lastSuccessfulFinish": iso(j.get("last_successful_finish")),
                              "lastRunStatus": j.get("last_run_status"), "nextStart": iso(j.get("next_start"))},
            "realTimeAggregation": False}


async def analytics(conn, metric: str, *, start: str | None = None, end: str | None = None, grain: str = "1m",
                    corridor: str | None = None, carrier: str | None = None, kind: str | None = None) -> dict:
    s, e = _range(start, end)
    interval = GRAINS.get(grain, "1 minute")
    params = {"start": s, "end": e, "grain": interval, "corridor": corridor, "carrier": carrier, "kind": kind}
    prov = {"metric": metric, "grain": interval, "start": iso(s), "end": iso(e),
            "filters": {k: v for k, v in (("corridor", corridor), ("carrier", carrier), ("kind", kind)) if v}}
    if metric == "deals":
        data = await rows(conn, SQL["deals"], params)
        series = [{"bucket": iso(r["bucket"]), "kind": r["kind"].upper(), "sessions": r["sessions"],
                   "accepted": r["accepted"], "exhausted": r["exhausted"], "declined": r["declined"],
                   "counterRefused": r["counter_refused"], "timedOut": r["timed_out"],
                   "comparableSessions": r["comparable_sessions"],
                   "avgOpeningUnitMinor": float(r["avg_opening_unit_minor"]) if r["avg_opening_unit_minor"] else None,
                   "avgFinalUnitMinor": float(r["avg_final_unit_minor"]) if r["avg_final_unit_minor"] else None,
                   "medianReductionPct": round(float(r["median_reduction_fraction"]) * 100, 1)
                   if r["median_reduction_fraction"] is not None else None,
                   "medianSeconds": round(float(r["median_seconds"]), 1) if r["median_seconds"] is not None else None}
                  for r in data]
        return {"series": series, "provenance": {**prov, "queryId": "deals.v1", "sql": SQL["deals"].strip(),
                                                 "source": "raw tables (negotiation_sessions, negotiation_messages, offers)",
                                                 "rows": len(series), "valueKind": "observed",
                                                 "sourceTables": ["negotiation_sessions", "negotiation_messages", "offers"]}}
    if metric == "capacity":
        data = await rows(conn, SQL["capacity"], params)
        markers = await rows(conn, """SELECT ts, event_id::text AS event_id, sailing_id, carrier_id FROM market_events
                                      WHERE event_type = 'SAILING_WITHDRAWN' AND ts >= %s AND ts < %s ORDER BY ts""", (s, e))
        series = [{"bucket": iso(r["bucket"]), "totalUnits": r["total_units"], "committedUnits": r["committed_units"],
                   "heldUnits": r["held_units"], "availableUnits": r["available_units"],
                   "withdrawnTotal": r["withdrawn_total"] or 0, "sailingsSampled": r["sailings_sampled"],
                   "committedFraction": r["committed_fraction"], "heldFraction": r["held_fraction"]} for r in data]
        return {"series": series,
                "markers": [{"at": iso(m["ts"]), "eventId": m["event_id"], "sailingId": m["sailing_id"],
                             "carrierId": m["carrier_id"], "kind": "SAILING_WITHDRAWN"} for m in markers],
                "provenance": {**prov, "queryId": "capacity.v1", "sql": SQL["capacity"].strip(),
                               "source": "sailing_capacity_snapshots hypertable (DISTINCT ON latest per sailing per bucket)",
                               "rows": len(series), "valueKind": "estimated from snapshots",
                               "sourceTables": ["sailing_capacity_snapshots", "market_events"]}}
    if metric == "activity":
        status = await cagg_status(conn)
        wm = datetime.fromisoformat(status["watermark"].replace("Z", "+00:00")) if status.get("watermark") else s
        params["watermark"] = wm
        mat = await rows(conn, SQL["activity_cagg"], params) if status.get("exists") else []
        tail = await rows(conn, SQL["activity_raw_tail"], params)
        buckets: dict[str, dict] = {}
        for src, data in (("materialized", mat), ("raw_tail", tail)):
            for r in data:
                b = buckets.setdefault(iso(r["bucket"]), {"bucket": iso(r["bucket"]), "total": 0, "byType": {},
                                                          "source": src})
                b["byType"][r["event_type"]] = b["byType"].get(r["event_type"], 0) + int(r["event_count"])
                b["total"] += int(r["event_count"])
        raw_count = (await (await conn.execute("SELECT count(*) FROM market_events WHERE ts >= %s AND ts < %s",
                                               (s, e))).fetchone())[0]
        series = sorted(buckets.values(), key=lambda b: b["bucket"])
        return {"series": series,
                "provenance": {**prov, "queryId": "activity.v1", "sql": SQL["activity_cagg"].strip() + "\n-- UNION (non-overlapping)\n"
                               + SQL["activity_raw_tail"].strip(),
                               "source": "continuous aggregate market_activity_1m + raw market_events tail after the watermark",
                               "aggregate": status, "watermark": status.get("watermark"),
                               "rows": len(series), "materializedRows": len(mat), "rawTailRows": len(tail),
                               "rawEventCount": raw_count, "bucketedEventCount": sum(b["total"] for b in series),
                               "valueKind": "materialized bucket rollup + raw recent tail",
                               "sourceTables": ["market_events", "market_activity_1m"]}}
    if metric == "recovery":
        data = await rows(conn, SQL["recovery"], params)
        detail = []
        for r in data:
            inc = await incident_detail(conn, r["recovery_id"])
            cl = r["clearing"] or {}
            secs = (r["last_confirmed_at"] - r["withdrawn_at"]).total_seconds() \
                if r["last_confirmed_at"] and r["withdrawn_at"] and inc and inc["stage"] in ("RESOLVED", "PARTIAL") else None
            detail.append({"incidentId": r["recovery_id"], "sailingId": r["sailing_id"], "runStatus": r["status"],
                           "stage": inc["stage"] if inc else None, "withdrawnAt": iso(r["withdrawn_at"]),
                           "impacted": inc["affected"] if inc else 0, "confirmed": inc["confirmed"] if inc else 0,
                           "pending": inc["pending"] if inc else 0, "unresolved": inc["unresolved"] if inc else 0,
                           "jointProposed": (cl.get("objective") or {}).get("recovered"),
                           "baselineProposed": (cl.get("baseline") or {}).get("recovered"),
                           "mustDeliverJoint": (cl.get("objective") or {}).get("must_deliver"),
                           "mustDeliverBaseline": (cl.get("baseline") or {}).get("must_deliver_recovered"),
                           "secondsToFinalConfirmation": round(secs, 1) if secs is not None else None,
                           "attempts": r["attempts"]})
        return {"series": detail, "provenance": {**prov, "queryId": "recovery.v1", "sql": SQL["recovery"].strip(),
                                                 "source": "recovery_runs + recovery_legs + bookings + market_events",
                                                 "rows": len(detail), "valueKind": "observed; baseline is a simulated counterfactual",
                                                 "sourceTables": ["recovery_runs", "recovery_legs", "bookings", "market_events"]}}
    raise ValueError("unknown metric")


async def provenance(conn) -> dict:
    hyper = await rows(conn, """SELECT hypertable_name, num_dimensions, num_chunks, compression_enabled
                                FROM timescaledb_information.hypertables ORDER BY hypertable_name""")
    dims = await rows(conn, """SELECT hypertable_name, column_name, time_interval::text AS chunk_interval
                               FROM timescaledb_information.dimensions""")
    counts = {}
    for t in ("market_events", "sailing_capacity_snapshots", "market_state_checkpoints"):
        counts[t] = (await (await conn.execute(f"SELECT count(*) FROM {t}")).fetchone())[0]
    return {"hypertables": [{**h, "timeColumn": next((d["column_name"] for d in dims if d["hypertable_name"] == h["hypertable_name"]), None),
                             "chunkInterval": next((d["chunk_interval"] for d in dims if d["hypertable_name"] == h["hypertable_name"]), None)}
                            for h in hyper],
            "continuousAggregate": await cagg_status(conn), "rowCounts": counts,
            "queries": {k: v.strip() for k, v in SQL.items()}}


# ---- Time Machine -------------------------------------------------------------------------------------
async def replay_bundle(conn, *, incident_id: str | None = None, start: str | None = None,
                        end: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    title, terminal, withdrawn = "Market history", False, None
    if incident_id:
        inc = await incident_detail(conn, incident_id)
        if inc is None:
            raise LookupError("unknown incident")
        withdrawn = datetime.fromisoformat(inc["withdrawal"]["at"].replace("Z", "+00:00")) if inc.get("withdrawal") \
            else datetime.fromisoformat(inc["openedAt"].replace("Z", "+00:00"))
        last = (await (await conn.execute("""SELECT max(ts) FROM market_events WHERE correlation_id = %s
                                             OR payload->>'approval_id' LIKE %s OR payload->>'intent_id' = %s""",
                                          (incident_id, f"apr_{incident_id}%", incident_id))).fetchone())[0]
        s = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else withdrawn - timedelta(seconds=90)
        terminal = inc["runStatus"] in ("COMPLETED", "PARTIAL", "UNRESOLVED", "FAILED")
        e = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else ((last or now) + timedelta(seconds=3)
                                                                            if terminal else now)
        title = f"{inc['withdrawal']['carrierName'] if inc.get('withdrawal') else ''} {inc['sailingId']} withdrawal".strip()
    else:
        s, e = _range(start, end, 60)
    cp = await rows(conn, """SELECT snapshot_id, captured_at, cursor_ts, projection_schema, reason, projection
                             FROM market_state_checkpoints WHERE captured_at <= %s
                             ORDER BY captured_at DESC LIMIT 1""", (s,))
    coverage = {"start": iso(s), "end": iso(e), "complete": True, "reason": None}
    if not cp:  # no checkpoint before the window: start from the earliest one inside it, and say so
        cp = await rows(conn, """SELECT snapshot_id, captured_at, cursor_ts, projection_schema, reason, projection
                                 FROM market_state_checkpoints WHERE captured_at <= %s
                                 ORDER BY captured_at LIMIT 1""", (e,))
        if not cp:
            return {"checkpoint": None, "events": [], "coverage": {**coverage, "complete": False,
                    "reason": "No historical checkpoint covers this time range"}, "schemaVersion": 1,
                    "title": title, "incidentId": incident_id}
        # Starting at a later checkpoint is still complete for an incident if it predates the withdrawal.
        covers = incident_id is not None and cp[0]["captured_at"] <= withdrawn
        coverage.update(complete=covers, start=iso(cp[0]["captured_at"]),
                        reason=None if covers else f"Replay available from {cp[0]['captured_at'].strftime('%H:%M:%S')} "
                                                   "UTC (no earlier checkpoint)")
    c = cp[0]
    after = c["cursor_ts"] or c["captured_at"] - timedelta(days=3650)
    ctx = await event_context(conn)
    events = await public_events(conn, after=after, before=e, limit=5000, ctx=ctx)
    if not terminal and incident_id:
        coverage["reason"] = f"Replay available through {e.strftime('%H:%M:%S')} UTC — later events not yet recorded"
    sessions = await negotiations(conn, incident_id=None)
    current = await projection(conn)
    return {"schemaVersion": 1, "title": title, "incidentId": incident_id,
            "checkpoint": {"snapshotId": c["snapshot_id"], "capturedAt": iso(c["captured_at"]),
                           "cursor": iso(c["cursor_ts"]), "reason": c["reason"], "projection": c["projection"]},
            "events": events, "coverage": coverage, "cursor": events[-1]["streamCursor"] if events else None,
            "sessions": sessions,
            "reference": {"asOf": current["asOf"], "terminal": terminal,
                          "sailings": [{k: s_[k] for k in ("sailingId", "status", "committedUnits", "heldUnits",
                                                           "availableUnits")} for s_ in current["sailings"]],
                          "bookings": [{k: b[k] for k in ("bookingId", "status")} for b in current["bookings"]]}}


# ---- Decision Inspector -------------------------------------------------------------------------------
async def decision(conn, kind: str, ident: str) -> dict | None:
    labels = await buyer_labels(conn)
    if kind == "sailing":
        p = await projection(conn, labels)
        s = next((x for x in p["sailings"] if x["sailingId"] == ident), None)
        if not s:
            return None
        carrier = next((c for c in p["carriers"] if c["carrierId"] == s["carrierId"]), {})
        books = [b for b in p["bookings"] if b["sailingId"] == ident]
        wd = await rows(conn, """SELECT event_id, occurred_at, reason FROM disruption_events WHERE sailing_id = %s
                                 ORDER BY occurred_at DESC LIMIT 1""", (ident,))
        return {"kind": "sailing", "id": ident, "title": f"{carrier.get('displayName', s['carrierId'])} · {ident}",
                "status": s["status"], "asOf": p["asOf"],
                "what": (f"{ident} was withdrawn by its carrier at {iso(wd[0]['occurred_at'])}." if wd and s["status"] == "WITHDRAWN"
                         else f"{ident} is {s['status'].lower()}: {s['availableUnits']} of {s['totalUnits']} 20GP slots sellable."),
                "why": wd[0]["reason"] if wd and s["status"] == "WITHDRAWN" else "Reason not recorded" if s["status"] == "WITHDRAWN" else None,
                "facts": [["Vessel", s["vesselName"] or s["vesselId"]], ["Corridor", s["corridorId"]],
                          ["Departure", s["departureAt"]], ["Arrival", s["arrivalAt"]],
                          ["Capacity", f"{s['committedUnits']} committed · {s['heldUnits']} held · {s['availableUnits']} available"
                                       + (f" · {s['withdrawnUnits']} withdrawn" if s["withdrawnUnits"] else "")],
                          ["Tiger slot check", f"{s['slotAvailableUnits']} AVAILABLE slots in capacity_slots"],
                          ["Position", "Simulated vessel position along a simulated lane"]],
                "evidence": [{"label": f"{b['bookingId']} · {b['buyer']}", "value": f"{b['quantity']}×20GP · {b['status']}"}
                             for b in books] + ([{"label": "Withdrawal event", "value": wd[0]["event_id"]}] if wd else []),
                "links": [{"kind": "warroom", "label": "Open War Room"}] if s["status"] == "WITHDRAWN" else []}
    if kind == "session":
        sess = await negotiations(conn, session_id=ident)
        if not sess:
            return None
        s = sess[0]
        carrier = (await rows(conn, "SELECT display_name FROM carriers WHERE carrier_id = %s", (s["carrierId"],)))[0]
        steps = s["steps"]
        last = next((x for x in reversed(steps) if x["amountMinor"] is not None and not x["proposed"]), None)
        def money(v):
            return "—" if v is None else f"${int(v) / 100:,.0f}"
        return {"kind": "session", "id": ident, "title": f"{s['buyer']} ↔ {carrier['display_name']}",
                "status": s["status"], "asOf": iso(datetime.now(timezone.utc)),
                "what": f"{s['buyer']} agent negotiated with {carrier['display_name']} ({s['kind'].lower()}): "
                        f"{len(steps)} recorded messages, final {money(s['finalMinor'])}.",
                "why": {"ACCEPTED": "The buyer agent or carrier accepted a quote", "EXHAUSTED": "Both counters used; the "
                        "carrier's last quote stands", "REJECTED": "Carrier declined (to quote or the counter)",
                        "EXPIRED": "No final offer before the negotiation deadline",
                        "OPEN": "Still negotiating"}.get(s["status"], "Reason not recorded"),
                "facts": [["Session", ident], ["Kind", s["kind"]], ["Participants", f"{s['buyer']} agent (logical instance, "
                          "shared LangGraph runtime) · " + carrier["display_name"] + " carrier uAgent"],
                          ["Eligibility", s["eligibility"]], ["Opening", money(s["openingMinor"])],
                          ["Final", money(s["finalMinor"]) if s["finalMinor"] else "No final eligible offer"],
                          ["Latest carrier price", money(last["amountMinor"]) if last else "—"],
                          ["Rounds", f"{s['round']} of {s['roundsMax']} counters"]],
                "evidence": [{"label": f"{x['messageKind'].replace('_', ' ').title()} · round {x['counterRound']}",
                              "value": (("Proposed " if x["proposed"] else "") + money(x["amountMinor"]))
                              + (" · signed reply verified" if x["signedSenderVerified"] else ""),
                              "eventId": x["eventId"], "at": x["at"]} for x in steps],
                "links": ([{"kind": "warroom", "label": "Open War Room", "incidentId": s["incidentId"]}] if s["incidentId"] else [])
                         + [{"kind": "universe", "label": "See negotiation", "sessionId": ident}]}
    if kind == "booking":
        b = (await rows(conn, """SELECT b.*, (SELECT count(*) FROM booking_slots bs WHERE bs.booking_id = b.booking_id) AS qty,
                                        k.display_name FROM bookings b JOIN carriers k USING (carrier_id)
                                 WHERE b.booking_id = %s""", (ident,)) or [None])[0]
        if not b:
            return None
        succ = await rows(conn, "SELECT booking_id, sailing_id FROM bookings WHERE replaces_booking_id = %s", (ident,))
        ev = await rows(conn, """SELECT ts, event_id::text AS event_id, event_type FROM market_events
                                 WHERE entity_id = %s ORDER BY ts""", (ident,))
        what = {"COMMITTED": f"Committed with {b['display_name']} on {b['sailing_id']}.",
                "DISRUPTED": f"Displaced: {b['sailing_id']} was withdrawn; recovery is in progress.",
                "SUPERSEDED": f"Superseded by {', '.join(x['booking_id'] for x in succ)} (kept for audit, never deleted)."}.get(
            b["status"], b["status"])
        return {"kind": "booking", "id": ident, "title": f"{ident} · {labels.label(b['buyer_id'])}", "status": b["status"],
                "asOf": iso(datetime.now(timezone.utc)), "what": what,
                "why": f"Replaces {b['replaces_booking_id']} after a carrier withdrawal." if b["replaces_booking_id"] else None,
                "facts": [["Carrier / sailing", f"{b['display_name']} · {b['sailing_id']}"], ["Containers", f"{b['qty']}×20GP"],
                          ["Freight", f"${b['total_price_minor'] / 100:,.0f} simulated USD"],
                          ["Hold", b["hold_id"] or "—"], ["Confirmed at", iso(b["confirmed_at"]) or iso(b["created_at"])]],
                "evidence": [{"label": e["event_type"].replace("_", " ").title(), "value": iso(e["ts"]), "eventId": e["event_id"]}
                             for e in ev], "links": []}
    if kind == "incident":
        inc = await incident_detail(conn, ident)
        if not inc:
            return None
        cl = inc.get("clearing") or {}
        why = None
        if cl:
            j, bl = cl["joint"], cl["baseline"]
            why = (f"Joint clearing proposed {j['recovered']} whole recoveries vs {bl['recovered']} for the first-come "
                   "baseline on the same frozen offers (baseline simulated, never executed).")
        return {"kind": "incident", "id": ident, "title": f"Disruption · {inc['sailingId']}", "status": inc["stage"],
                "asOf": iso(datetime.now(timezone.utc)),
                "what": f"{inc['affected']} bookings displaced; {inc['confirmed']} confirmed, {inc['pending']} pending, "
                        f"{inc['unresolved']} unresolved.", "why": why,
                "facts": [["Objective", "1 must-deliver · 2 whole bookings · 3 fewest approvals · 4 lowest extra cost · "
                           "5 earliest arrival"]] + ([["Stage values", ", ".join(f"{k} {v}" for k, v in (cl.get("objective") or {}).items())]]
                                                     if cl else []),
                "evidence": [{"label": s["stage"].replace("_", " ").title(), "value": s["at"], "eventId": s["eventId"]}
                             for s in inc["stages"]], "links": [{"kind": "warroom", "label": "Open War Room", "incidentId": ident},
                                                                {"kind": "replay", "label": "Replay incident", "incidentId": ident}]}
    if kind == "event":
        ctx = await event_context(conn)
        raw = await rows(conn, """SELECT ts, event_id::text AS event_id, event_type, entity_type, entity_id, correlation_id,
                                         carrier_id, sailing_id, corridor_id, payload FROM market_events
                                  WHERE event_id::text = %s""", (ident,))
        if not raw:
            return None
        from freight_exchange.ops.read_model import public_event
        pe = public_event(raw[0], ctx)
        if not pe:
            return None
        return {"kind": "event", "id": ident, "title": pe["eventType"].replace("_", " ").title(), "status": pe["actorKind"],
                "asOf": pe["eventTime"], "what": pe["summary"], "why": None,
                "facts": [[k, str(v)] for k, v in pe["publicPayload"].items()] + [["Recorded", pe["eventTime"]]],
                "evidence": [{"label": "market_events row", "value": ident, "eventId": ident}],
                "links": ([{"kind": "warroom", "label": "Open War Room", "incidentId": pe["incidentId"]}] if pe["incidentId"] else [])
                         + [{"kind": "replay", "label": "Rewind to event", "at": pe["eventTime"],
                             "incidentId": pe["incidentId"]}]}
    return None
