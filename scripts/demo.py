"""Demo control for Part 1 (spec 08 §5): reset, seed, withdraw, recover, audit, evidence, rehearse.

  uv run python scripts/demo.py reset              # wipe + re-anchor the SIMULATED demo market (refuses other DBs)
  uv run python scripts/demo.py seed               # Buyer B on ATL-101 + unaffected Buyer C (Tisya's preseed service)
  uv run python scripts/demo.py withdraw ATL-101   # carrier-authorized, labelled SIMULATED advisory
  uv run python scripts/demo.py recover            # one recovery-worker pass (buyer messages on)
  uv run python scripts/demo.py audit              # transaction invariants; exit 1 on any violation
  uv run python scripts/demo.py evidence           # provenance: ACP, Stripe test payment, solver, holds, hypertable
  uv run python scripts/demo.py rehearse --runs 3  # full loop N times, Buyer A preseeded (labelled rehearsal)

Buyer A in the real demo is booked live in ASI:One (README "Demo runbook"); `rehearse` stands it in with a
preseeded booking so the rest of the loop can be repeated unattended. Neither is a substitute for the other.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from freight_exchange.repository import get_repository  # noqa: E402

FIXTURE_CARRIERS = {c["carrier_id"] for c in json.loads((ROOT / "fixtures" / "demo_carriers.json").read_text())["carriers"]}
SIMULATED_REASON = ("Security incident reported in the transit corridor (advisory risk_sim_0001). "
                    "The carrier withdrew this sailing.")
PRESEED = """
import asyncio, json, sys
from carrier_console.config import load_env
from carrier_console.db import close_pools, get_pool
from carrier_console.preseed import preseed_booking
async def main(plan):
    load_env(); pool = await get_pool()
    try:
        for p in plan:
            r = await preseed_booking(pool, buyer_id=p["buyer"], sailing_id=p["sailing"], quantity=p["qty"],
                                      allow_split=p["split"])
            print(json.dumps({"buyer": p["buyer"], "booking_id": r.booking_id, "sailing": r.sailing_id}))
    finally:
        await close_pools()
asyncio.run(main(json.loads(sys.argv[1])))
"""


def sh(*cmd: str, cwd: Path = ROOT) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, "VIRTUAL_ENV": ""})
    if out.returncode:
        raise SystemExit(f"{' '.join(cmd[:4])}… failed:\n{out.stderr[-1500:]}")
    return out.stdout


async def guard_demo_database(repo) -> None:
    """Only the simulated fixture market may be reset or seeded: every carrier is a fixture carrier and no
    row is marked as external (real) data."""
    async with repo.pool.connection() as conn:
        carriers = {r[0] for r in await (await conn.execute("SELECT carrier_id FROM carriers")).fetchall()}
        external = (await (await conn.execute(
            """SELECT (SELECT count(*) FROM sailings WHERE source_kind <> 'simulated')
                    + (SELECT count(*) FROM bookings WHERE source_kind <> 'simulated')
                    + (SELECT count(*) FROM disruption_events WHERE source_kind <> 'simulated')""")).fetchone())[0]
    if not carriers <= FIXTURE_CARRIERS or external:
        raise SystemExit(f"refusing: not the simulated demo market (carriers {sorted(carriers)}, external rows {external})")


# ---- commands --------------------------------------------------------------------------
async def reset(repo, anchor: str | None = None) -> None:
    await guard_demo_database(repo)
    print(sh(sys.executable, "scripts/reset_demo.py", "--yes", *(["--anchor", anchor] if anchor else []))
          .strip().splitlines()[-1])


BUYER_B_ALLOWANCE_MINOR = 30000  # $300


async def seed(repo, extra: list[dict] | None = None) -> list[dict]:
    """Buyer B on the first departure (ATL-101) and Buyer C on the last, through the console's preseed service."""
    await guard_demo_database(repo)
    print(sh("uv", "run", "--directory", "console", "python", "-m", "carrier_console.preseed_cli").strip())
    async with repo.pool.connection() as conn:  # B auto-rebooks a small increase; live Buyer A sees the approval card
        await conn.execute("UPDATE bookings SET max_additional_cost_minor = %s WHERE buyer_id = 'buyer_b'",
                           (BUYER_B_ALLOWANCE_MINOR,))
    made = []
    if extra:
        out = sh("uv", "run", "--directory", "console", "python", "-c", PRESEED, json.dumps(extra))
        made = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
        for m in made:
            print(f"  {m['booking_id']}  {m['buyer']:>16}  {m['sailing']}  (rehearsal stand-in)")
    else:
        async with repo.pool.connection() as conn:
            arrival = (await (await conn.execute(
                "SELECT arrival_at FROM sailings WHERE sailing_id = 'ATL-101'")).fetchone())[0]
        by = (arrival + timedelta(days=2)).strftime("%b %-d")   # same slack Buyer B was seeded with
        print("\nBuyer A, live in ASI:One with the Hormuz Exchange gateway agent:\n"
              "  say 'I need to ship cargo' -> form: Jebel Ali -> Mumbai, 2 containers, arrive no later than "
              f"{by}, no split, auto-rebook limit 600 -> choose the Atlas ATL-101 offer -> Confirm and pay by card.\n"
              "  Then withdraw ATL-101 at http://localhost:8800: B auto-rebooks, you get an approval card.")
    return made


V2_FIXTURE = [  # PRD 9 / spec 05 6, on the seeded market (anchor = tomorrow 00:00 UTC)
    {"buyer": "demo_buyer_a", "sailing": "ATL-101", "qty": 2, "split": False, "deadline_days": 6.99,
     "priority": "fastest", "must_deliver": False, "allowance": 100000},
    {"buyer": "demo_buyer_b", "sailing": "ATL-101", "qty": 1, "split": False, "deadline_days": 3.5,
     "priority": "balanced", "must_deliver": True, "allowance": 50000},
    {"buyer": "demo_buyer_c", "sailing": "ATL-101", "qty": 1, "split": False, "deadline_days": 3.5,
     "priority": "balanced", "must_deliver": False, "allowance": 0},
    {"buyer": "demo_buyer_d", "sailing": "ATL-102", "qty": 1, "split": False, "deadline_days": 8,
     "priority": "balanced", "must_deliver": False, "allowance": 0},
]


async def seed_v2(repo) -> dict[str, str]:
    """A, B (must-deliver), C (zero allowance) on ATL-101 + unaffected D, through the console's preseed
    service; then the buyers' private settings (deadline, priority, must-deliver, allowance)."""
    await guard_demo_database(repo)
    out = sh("uv", "run", "--directory", "console", "python", "-c", PRESEED,
             json.dumps([{k: f[k] for k in ("buyer", "sailing", "qty", "split")} for f in V2_FIXTURE]))
    made = {m["buyer"]: m["booking_id"] for m in (json.loads(x) for x in out.splitlines() if x.startswith("{"))}
    async with repo.pool.connection() as conn:
        anchor = (await (await conn.execute("SELECT min(departure_at)::date FROM sailings")).fetchone())[0]
        for f in V2_FIXTURE:
            bid = made[f["buyer"]]
            await conn.execute(
                """UPDATE cargo_requests r SET latest_arrival = %s::date + make_interval(secs => %s), priority = %s,
                       must_deliver = %s FROM bookings b WHERE b.booking_id = %s AND r.request_id = b.request_id""",
                (anchor, int(f["deadline_days"] * 86400), f["priority"], f["must_deliver"], bid))
            await conn.execute("UPDATE bookings SET must_deliver = %s, max_additional_cost_minor = %s WHERE booking_id = %s",
                               (f["must_deliver"], f["allowance"], bid))
            print(f"  {bid}  {f['buyer']:>13}  {f['sailing']}  {f['qty']}x  priority={f['priority']}  "
                  f"must_deliver={f['must_deliver']}  allowance=${f['allowance'] // 100}")
    return made


async def approve(repo, booking_id: str, decision: str = "approve") -> None:
    """Stand-in for a preseeded buyer's reply (they have no ASI:One chat): records the decision through the
    same approval handler the gateway uses, as that booking's own buyer. Real buyers use the approval card."""
    from freight_exchange.asi.approvals import TigerApprovalStore, handle_approval_reply
    async with repo.pool.connection() as conn:
        row = await (await conn.execute("SELECT buyer_id FROM bookings WHERE booking_id = %s", (booking_id,))).fetchone()
    if row is None:
        raise SystemExit(f"no booking {booking_id}")
    result = await handle_approval_reply(TigerApprovalStore(repo.pool), row[0], f"{decision} {booking_id}")
    print(f"[stand-in for {row[0]}] {decision} {booking_id}: {result.status if result else 'not an approval'}")


async def withdraw(repo, sailing_id: str) -> None:
    from freight_disruption import TigerDisruptionSeam
    owner = await repo.sailing_owner(sailing_id)
    if owner is None:
        raise SystemExit(f"no sailing {sailing_id}")
    s = await TigerDisruptionSeam(repo.pool).withdraw(sailing_id=sailing_id, carrier_id=owner, risk_event_id="risk_sim_0001",
                                                     actor_id=f"{owner}-ops", reason_text=SIMULATED_REASON)
    print(f"[SIMULATED] {owner} withdrew {sailing_id}: displaced {list(s.disrupted_booking_ids)}, "
          f"untouched elsewhere {s.untouched_booking_count}")


async def recover(repo, notify: bool = True):
    from freight_exchange.quotes import exchange_identity
    from freight_exchange.recovery import build_recovery_seam
    from freight_exchange.recovery_worker import tick
    agent_uri = os.environ["AGENT_URI"]
    seam = await build_recovery_seam(exchange_identity(agent_uri), repo.pool, agent_uri=agent_uri if notify else None)
    outcomes = await tick(seam)
    for o in outcomes:
        print(f"{o.recovery_run_id} {o.status}: " + ", ".join(
            f"{a.booking_id}->{a.sailing_id} ({o.diagnostics.get('replacement:' + a.booking_id)})" for a in o.assignments)
              + (f"; unrecovered {list(o.unrecovered_booking_ids)}" if o.unrecovered_booking_ids else ""))
    return outcomes


AUDITS = [
    ("a slot is in more than one active hold",
     "SELECT slot_id FROM hold_slots WHERE status = 'ACTIVE' GROUP BY slot_id HAVING count(*) > 1"),
    ("a slot is committed to more than one booking",
     "SELECT slot_id FROM booking_slots WHERE status = 'COMMITTED' GROUP BY slot_id HAVING count(*) > 1"),
    ("a COMMITTED slot has no live booking",
     """SELECT c.slot_id FROM capacity_slots c WHERE c.status = 'COMMITTED' AND NOT EXISTS (
            SELECT 1 FROM booking_slots bs JOIN bookings b USING (booking_id)
            WHERE bs.slot_id = c.slot_id AND bs.status = 'COMMITTED' AND b.status IN ('COMMITTED', 'DISRUPTED'))"""),
    ("a HELD slot has no active hold",
     """SELECT c.slot_id FROM capacity_slots c WHERE c.status = 'HELD' AND NOT EXISTS (
            SELECT 1 FROM hold_slots hs JOIN holds h USING (hold_id)
            WHERE hs.slot_id = c.slot_id AND hs.status = 'ACTIVE' AND h.status = 'ACTIVE')"""),
    ("a live booking's slot count differs from its booked quantity",
     """SELECT b.booking_id FROM bookings b JOIN offers o ON o.offer_id = b.offer_id
        LEFT JOIN booking_slots bs ON bs.booking_id = b.booking_id AND bs.status = 'COMMITTED'
        WHERE b.status IN ('COMMITTED', 'DISRUPTED') GROUP BY b.booking_id, o.quantity
        HAVING count(bs.slot_id) <> o.quantity"""),
    ("a superseded booking still claims slots",
     """SELECT DISTINCT b.booking_id FROM bookings b JOIN booking_slots bs USING (booking_id)
        WHERE b.status = 'SUPERSEDED' AND bs.status = 'COMMITTED'"""),
    ("a withdrawn sailing still has sellable or held slots",
     """SELECT c.slot_id FROM capacity_slots c JOIN sailings s USING (sailing_id)
        WHERE s.status = 'WITHDRAWN' AND c.status IN ('AVAILABLE', 'HELD')"""),
    ("a recovered booking is not superseded by a committed replacement",
     """SELECT ra.old_booking_id FROM recovery_assignments ra
        JOIN bookings o ON o.booking_id = ra.old_booking_id
        LEFT JOIN bookings n ON n.booking_id = ra.replacement_booking_id
        WHERE ra.status = 'RECOVERED' AND (o.status <> 'SUPERSEDED' OR n.status IS DISTINCT FROM 'COMMITTED'
                                           OR n.replaces_booking_id IS DISTINCT FROM o.booking_id)"""),
    ("an unsplittable booking has more than one live replacement",
     """SELECT n.replaces_booking_id FROM bookings n JOIN bookings o ON o.booking_id = n.replaces_booking_id
        JOIN cargo_requests r ON r.request_id = o.request_id
        WHERE n.status = 'COMMITTED' AND NOT r.allow_split GROUP BY n.replaces_booking_id HAVING count(*) > 1"""),
    ("a confirmed recovery leg has no replacement booking",
     "SELECT recovery_id, booking_id FROM recovery_legs WHERE status = 'CONFIRMED' AND replacement_booking_id IS NULL"),
    ("a booking was confirmed twice for one hold",
     "SELECT hold_id FROM bookings WHERE hold_id IS NOT NULL GROUP BY hold_id HAVING count(*) > 1"),
    ("a payment is not Stripe test mode",
     "SELECT payment_id FROM payment_records WHERE livemode IS TRUE"),
]


async def audit(repo) -> bool:
    ok = True
    async with repo.pool.connection() as conn:
        for title, sql in AUDITS:
            rows = await (await conn.execute(sql)).fetchall()
            ok &= not rows
            print(f"  {'PASS' if not rows else 'FAIL'}  no case where {title}" + (f": {rows[:5]}" if rows else ""))
    print("audit:", "PASS" if ok else "FAIL")
    return ok


async def evidence(repo) -> None:
    """What the demo must prove (spec 08 §5): ACP traces, Stripe test verification, Tiger bookings and events,
    the OR-Tools decision and the confirmed replacement holds."""
    q = [
        ("Exchange-carrier ACP traffic by type (signed, correlated)",
         """SELECT message_type, direction, count(*), count(DISTINCT carrier_id) AS carriers
            FROM agent_messages GROUP BY 1, 2 ORDER BY 1, 2"""),
        ("Stripe TEST payments (verified server-side; livemode must be false)",
         """SELECT payment_id, status, provider_status, livemode, amount_minor, freight_minor, fee_minor, booking_id
            FROM payment_records ORDER BY created_at"""),
        ("Bookings and lineage",
         """SELECT booking_id, buyer_id, sailing_id, status, total_price_minor, replaces_booking_id,
                   coalesce(payment_reference, '-') FROM bookings ORDER BY created_at"""),
        ("Withdrawals (simulated)",
         "SELECT event_id, carrier_id, sailing_id, source_kind, reason_risk_event_id, left(reason, 60) FROM disruption_events"),
        ("Recovery runs (OR-Tools input_version = solver decision fingerprint)",
         "SELECT recovery_id, sailing_id, status, attempts, input_version FROM recovery_runs ORDER BY created_at"),
        ("Negotiation sessions (booking + recovery; final terms only, never floors or budgets)",
         """SELECT kind, coalesce(booking_id, request_id), carrier_id, status, round, final_total_minor, reason
            FROM negotiation_sessions ORDER BY opened_at"""),
        ("Clearing: five-stage objective vs first-come-first-served baseline (simulated, never executed)",
         """SELECT entity_id, payload->'objective', payload->'baseline'->>'recovered',
                   payload->'baseline'->>'baseline_kind', payload->'negotiation'->>'negotiation_seconds'
            FROM market_events WHERE event_type = 'CLEARING_DONE' ORDER BY ts"""),
        ("Fetch FET testnet fees (Payment Protocol; tx verified on-chain)",
         """SELECT reference, status, funds_amount, funds_currency, left(provider_tx_id, 16), buyer_wallet, booking_id
            FROM payment_records WHERE provider = 'fetch' ORDER BY created_at"""),
        ("Recovery legs (holds -> confirmations)",
         """SELECT recovery_id, booking_id, sailing_id, quantity, quoted_price_minor, needs_approval, status, hold_id,
                   replacement_booking_id FROM recovery_legs ORDER BY recovery_id, booking_id"""),
        ("Tiger hypertable: market_events per minute and type (real execution timestamps)",
         """SELECT to_char(time_bucket('1 minute', ts), 'MM-DD HH24:MI') AS minute, event_type, count(*)
            FROM market_events WHERE ts > now() - interval '6 hours' GROUP BY 1, 2 ORDER BY 1, 2"""),
        ("Hypertables", "SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY 1"),
    ]
    async with repo.pool.connection() as conn:
        for title, sql in q:
            rows = await (await conn.execute(sql)).fetchall()
            print(f"\n== {title}: {len(rows)} rows")
            for row in rows:
                print("  ", tuple(row))


async def rehearse(repo, runs: int) -> bool:
    """reset -> seed (B, C + preseeded stand-in for live Buyer A) -> withdraw ATL-101 -> recover -> audit -> check."""
    passed = 0
    for n in range(1, runs + 1):
        started = time.perf_counter()
        print(f"\n######## rehearsal {n}/{runs}")
        await reset(repo)
        [stand_in] = await seed(repo, extra=[{"buyer": "rehearsal_buyer_a", "sailing": "ATL-101", "qty": 2, "split": False}])
        async with repo.pool.connection() as conn:   # "auto-rebook up to $2,000": the fully automatic path
            await conn.execute("UPDATE bookings SET max_additional_cost_minor = 200000 WHERE booking_id = %s",
                               (stand_in["booking_id"],))
            before = dict(await (await conn.execute("SELECT buyer_id, booking_id FROM bookings")).fetchall())
        await withdraw(repo, "ATL-101")
        outcomes = await recover(repo, notify=False)
        audit_ok = await audit(repo)
        async with repo.pool.connection() as conn:
            status = dict(await (await conn.execute("SELECT booking_id, status FROM bookings")).fetchall())
        checks = {
            "one run, COMPLETED": [o.status for o in outcomes] == ["COMPLETED"],
            "Buyer A (stand-in) recovered": status.get(before["rehearsal_buyer_a"]) == "SUPERSEDED",
            "Buyer B recovered": status.get(before["buyer_b"]) == "SUPERSEDED",
            "Buyer C unchanged": status.get(before["buyer_c"]) == "COMMITTED",
            "invariants hold": audit_ok,
        }
        for name, ok in checks.items():
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        passed += all(checks.values())
        print(f"rehearsal {n}: {'PASS' if all(checks.values()) else 'FAIL'} in {time.perf_counter() - started:.0f} s")
    print(f"\nrehearsals passed: {passed}/{runs}")
    return passed == runs


async def main(args) -> int:
    repo = await get_repository()
    try:
        if args.cmd == "reset":
            await reset(repo, args.anchor)
        elif args.cmd == "seed":
            await seed(repo)
        elif args.cmd == "seed-v2":
            await seed_v2(repo)
        elif args.cmd == "approve":
            await approve(repo, args.booking_id, "decline" if args.decline else "approve")
        elif args.cmd == "withdraw":
            await withdraw(repo, args.sailing_id)
        elif args.cmd == "recover":
            await recover(repo, notify=not args.no_notify)
        elif args.cmd == "audit":
            return 0 if await audit(repo) else 1
        elif args.cmd == "evidence":
            await evidence(repo)
        elif args.cmd == "rehearse":
            return 0 if await rehearse(repo, args.runs) else 1
        return 0
    finally:
        await repo.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reset").add_argument("--anchor")
    sub.add_parser("seed")
    sub.add_parser("seed-v2")
    ap = sub.add_parser("approve")
    ap.add_argument("booking_id")
    ap.add_argument("--decline", action="store_true")
    sub.add_parser("withdraw").add_argument("sailing_id")
    sub.add_parser("recover").add_argument("--no-notify", action="store_true")
    sub.add_parser("audit")
    sub.add_parser("evidence")
    sub.add_parser("rehearse").add_argument("--runs", type=int, default=3)
    sys.exit(asyncio.run(main(parser.parse_args())))
