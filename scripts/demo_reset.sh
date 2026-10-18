#!/usr/bin/env bash
# One command before every judge demo: put the simulated market back to the finalized demo baseline.
#
#   ./scripts/demo_reset.sh
#
# What it does (test data only; refuses any non-demo database):
#   1. wipes all bookings, holds, negotiations, recovery runs, payments, chat cards, events and dashboard history
#   2. reactivates every sailing and re-creates the fixture market at a FIXED anchor (DEMO_ANCHOR, default
#      2026-10-19T00:00Z) so the dates are identical for every take: arrive-by "Oct 23", ATL-101 departs Oct 19 12:00 UTC
#   3. seeds Buyer B (1×20GP on ATL-101, $300 auto-rebook limit) and Buyer C (DHW-571, unaffected)
#   4. verifies the baseline and prints the exact demo inputs
# If the fixed anchor is too close (ATL-101 departs within 3 h), it falls back to "tomorrow" and prints the new date.
set -euo pipefail
cd "$(dirname "$0")/.."
unset VIRTUAL_ENV
ANCHOR="${DEMO_ANCHOR:-2026-10-19T00:00:00Z}"
if uv run python -c "
import sys; from datetime import datetime, timedelta, timezone
a = datetime.fromisoformat('$ANCHOR'.replace('Z', '+00:00'))
sys.exit(0 if datetime.now(timezone.utc) < a + timedelta(hours=9) else 1)" 2>/dev/null; then
  echo "anchor: $ANCHOR (fixed)"
else
  echo "!! fixed anchor $ANCHOR is too close to ATL-101's departure; using tomorrow 00:00 UTC — the arrive-by date below changes"
  ANCHOR="auto"
fi
uv run python scripts/demo.py reset --anchor "$ANCHOR" 2>&1 | grep -v VIRTUAL_ENV | tail -1
uv run python scripts/demo.py seed 2>&1 | grep -v VIRTUAL_ENV | grep -E "BK-|arrive no later" || true
uv run python - <<'EOF'
import os, psycopg
from dotenv import load_dotenv
load_dotenv(".env")
with psycopg.connect(os.environ["TIGER_DATABASE_URL"]) as c:
    rows = c.execute("""
        SELECT s.sailing_id, s.status, to_char(s.departure_at, 'Mon DD HH24:MI'), to_char(s.arrival_at, 'Mon DD HH24:MI'),
               count(*) FILTER (WHERE cs.status = 'AVAILABLE') AS free, count(*) AS total
        FROM sailings s JOIN capacity_slots cs USING (sailing_id)
        WHERE s.sailing_id IN ('ATL-101', 'BEA-201', 'COV-301') GROUP BY 1, 2, 3, 4, s.departure_at ORDER BY s.departure_at""").fetchall()
    b = c.execute("SELECT count(*) FROM bookings WHERE status = 'COMMITTED'").fetchone()[0]
    deadline = c.execute("SELECT to_char(arrival_at + interval '2 days', 'Mon FMDD') FROM sailings WHERE sailing_id = 'ATL-101'").fetchone()[0]
    expected = {"ATL-101": 3, "BEA-201": 2, "COV-301": 1}
    ok = all(r[1] == "ACTIVE" and r[4] == expected[r[0]] for r in rows) and b == 2
    print("\nbaseline:", "OK" if ok else "UNEXPECTED — rerun ./scripts/demo_reset.sh")
    for r in rows:
        print(f"  {r[0]}  {r[1]}  departs {r[2]}  arrives {r[3]}  free {r[4]}/{r[5]}")
    print(f"  committed bookings: {b} (Buyer B on ATL-101, Buyer C on DHW-571)")
    print(f"\ndemo inputs: Jebel Ali → Mumbai · 2 containers · arrive no later than {deadline} · cargo ready any time · "
          "split off · general / balanced · budget blank · auto-rebook limit 600 → choose Atlas ATL-101 → pay 4242… → "
          "withdraw ATL-101 at http://localhost:8800")
EOF
