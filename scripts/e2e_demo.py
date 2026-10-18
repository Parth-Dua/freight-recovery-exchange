"""End-to-end check of the finalized judge demo, through the real running services (not ASI:One itself).

Run after ./scripts/run_demo.sh and ./scripts/demo_reset.sh:
    uv run python scripts/e2e_demo.py            # full journey incl. two Stripe TEST card payments (4242, headless Chrome)
    uv run python scripts/e2e_demo.py --no-pay   # stop before paying

Plays Buyer A exactly as in docs/critical_alerts.md: Jebel Ali → Mumbai, 2 containers, arrive by the baseline date,
split off, general / balanced, no budget, auto-rebook limit 600 → choose Atlas ATL-101 → pay → withdraw ATL-101 in the
carrier console (its real HTML form) → approval card → approve → Rebooked + fare difference → pay it. Then checks Tiger,
the console and Mission Control. Chat traffic goes through the internal Exchange API with a test gateway address,
exactly as the gateway uAgent would send it; this is an integration test, not ASI:One evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid

import httpx
import psycopg
from dotenv import dotenv_values, load_dotenv

load_dotenv(".env")
API, CONSOLE, OPS = "http://127.0.0.1:8900", "http://127.0.0.1:8800", "http://127.0.0.1:8820"
GW = "agent1qe2etestgateway"
H = {"x-gateway-token": dotenv_values(".env")["GATEWAY_BACKEND_TOKEN"]}
BUYER, SESSION = f"agent1qe2ebuyer{uuid.uuid4().hex[:8]}", str(uuid.uuid4())
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def say(text: str) -> dict:
    r = httpx.post(f"{API}/internal/chat/intent", headers=H, timeout=120, json={
        "event_id": str(uuid.uuid4()), "sender_agent_address": BUYER, "chat_session_id": SESSION,
        "gateway_agent_address": GW, "gateway_wallet": "", "text": text})
    r.raise_for_status()
    return r.json()


def flat(r: dict):
    yield r
    for f in r.get("followups") or []:
        yield from flat(f)


def wait_for(pred, seconds: int, label: str) -> list[dict]:
    """Collect pushed results for this buyer until `pred(all_so_far)` is true."""
    seen: list[dict] = []
    end = time.time() + seconds
    while time.time() < end:
        items = [i for i in httpx.get(f"{API}/internal/chat/outbox?gateway={GW}", headers=H).json()["items"]
                 if i["recipient"] == BUYER]
        for i in items:
            httpx.post(f"{API}/internal/chat/outbox/{i['outbox_id']}/ack", headers=H, json={"status": "SENT"})
            seen.extend(flat(i["result"]))
        if pred(seen):
            return seen
        time.sleep(2)
    print(f"  (timed out waiting for {label})")
    return seen


def pay(url: str) -> bool:
    out = subprocess.run(["uv", "run", "--with", "playwright", "python", "scripts/pay_test_checkout.py", url],
                         capture_output=True, text=True, timeout=240)
    return out.returncode == 0


def db(sql: str, args=()):
    with psycopg.connect(os.environ["TIGER_DATABASE_URL"]) as c:
        return c.execute(sql, args).fetchall()


def preseed_paid_booking() -> tuple[str, int]:
    """Stand-in for a booking already paid in ASI:One (payment itself is verified separately by a person)."""
    sys.path.insert(0, "scripts")
    from demo import PRESEED
    say("hello")  # binds this test chat so recovery cards are pushed to it
    out = subprocess.run(["uv", "run", "--directory", "console", "python", "-c", PRESEED,
                          json.dumps([{"buyer": BUYER, "sailing": "ATL-101", "qty": 2, "split": False}])],
                         capture_output=True, text=True, env={**os.environ, "VIRTUAL_ENV": ""})
    bk = json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("{")][0])["booking_id"]
    with psycopg.connect(os.environ["TIGER_DATABASE_URL"]) as c:
        c.execute("UPDATE bookings SET max_additional_cost_minor = 60000 WHERE booking_id = %s", (bk,))
        price = c.execute("SELECT total_price_minor FROM bookings WHERE booking_id = %s", (bk,)).fetchone()[0]
    return bk, price


def main(no_pay: bool, preseeded: bool) -> int:
    if preseeded:
        print("1. Paid booking for Buyer A (preseeded stand-in; booking + payment are verified in ASI:One)")
        bk_a, paid_freight = preseed_paid_booking()
        check("Buyer A booking committed on ATL-101", bool(bk_a), bk_a)
        return disruption(bk_a, paid_freight, no_pay=True)
    print("1. Booking (Buyer A)")
    form = say("I need to ship cargo")
    check("form card", form.get("card_kind") == "form")
    fields = {f["name"]: f for f in form["card_payload"]["fields"]}
    deadline = db("SELECT (arrival_at + interval '2 days')::date FROM sailings WHERE sailing_id = 'ATL-101'")[0][0]
    opt = next((o["value"] for o in fields["latest_arrival"]["options"] if o["value"].startswith(str(deadline))), None)
    check("arrive-by option for the baseline date exists", opt is not None, str(deadline))
    submit = {**form["card_payload"]["submit_cta"]["selection"], "origin_port": "AEJEA", "destination_port": "INBOM",
              "quantity": "2", "latest_arrival": opt, "earliest_departure": "", "allow_split": "false",
              "cargo_class": "general", "max_total_price": "", "priority": "balanced", "must_deliver": "false",
              "recovery_allowance": "600"}
    say(json.dumps(submit))
    pushed = wait_for(lambda xs: any(x.get("card_kind") == "carousel" for x in xs), 150, "offers")
    carousel = next((x for x in pushed if x.get("card_kind") == "carousel"), None)
    items = carousel["card_payload"]["items"] if carousel else []
    titles = sorted(i["title"] for i in items)
    check("progress message while negotiating", any(x.get("kind") == "TEXT" and "quoted" in x.get("text", "") for x in pushed))
    check("transcript index card before the offers", any(x.get("card_kind") == "detail" and x["card_payload"]["title"] == "How your agent negotiated" for x in pushed))
    check("offers are Atlas and Beacon only", titles == ["Atlas Lines", "Beacon Maritime"], str(titles))
    atlas = next((i for i in items if i["title"] == "Atlas Lines"), None)
    check("Atlas offer is on ATL-101", bool(atlas) and atlas["secondary_text"].startswith("ATL-101"), atlas and atlas["secondary_text"].split(":")[0])
    if not atlas:
        return 1
    review = say(json.dumps(atlas["primary_cta"]["selection"]))
    check("review card", review.get("card_kind") == "review")
    pr = say(json.dumps(review["card_payload"]["approve_cta"]["selection"]))
    check("carrier hold + card payment request", pr.get("kind") == "PAYMENT_REQUEST" and pr["payment"]["method"] == "stripe",
          f"{pr.get('payment', {}).get('amount')} USD")
    paid_freight = int(round(float(pr["payment"]["amount"]) * 100)) - 2500
    if no_pay:
        print("stopping before payment (--no-pay)")
        return report()
    check("Stripe TEST checkout paid with 4242", pay(pr["payment"]["metadata"]["checkout_url"]))
    got = wait_for(lambda xs: any(x.get("card_kind") == "detail" and x["card_payload"]["title"] == "Booking confirmed" for x in xs), 90, "receipt")
    check("PaymentComplete + Booking confirmed card", any(x.get("kind") == "PAYMENT_COMPLETE" for x in got)
          and any(x.get("card_kind") == "detail" and x["card_payload"]["title"] == "Booking confirmed" for x in got))
    mine = db("SELECT booking_id, total_price_minor FROM bookings WHERE buyer_id = %s AND status = 'COMMITTED'", (BUYER,))
    check("booking committed on ATL-101 in Tiger", len(mine) == 1, mine and mine[0][0])
    return disruption(mine[0][0], paid_freight, no_pay=False)


def disruption(bk_a: str, paid_freight: int, no_pay: bool) -> int:
    print("2. Carrier withdraws ATL-101 in the carrier console")
    page = httpx.get(f"{CONSOLE}/carrier/atlas/sailings/ATL-101").text
    check("sailing page offers 'Review withdrawal'", "Review withdrawal" in page)
    risk = re.search(r'name="risk_event_id" value="([^"]+)"', page)
    rv = httpx.post(f"{CONSOLE}/withdraw/review", data={"sailing_id": "ATL-101", "carrier_id": "atlas", "risk_event_id": risk.group(1)})
    check("review page lists 2 committed bookings", "2</b> committed booking" in rv.text)
    wd = httpx.post(f"{CONSOLE}/withdraw", data={"sailing_id": "ATL-101", "carrier_id": "atlas", "risk_event_id": risk.group(1)},
                    follow_redirects=True)
    check("withdrawal confirmed by the console", "withdrawn" in wd.text.lower())

    print("3. Recovery")
    got = wait_for(lambda xs: any(x.get("card_kind") == "review" and "approve_cta" in x["card_payload"]
                                  and x["card_payload"]["approve_cta"]["selection"].get("action") == "approve_recovery" for x in xs), 150, "approval card")
    alert = next((x for x in got if x.get("card_kind") == "detail" and "cancelled" in x["card_payload"]["title"]), None)
    check("⚠️ cancellation alert card", bool(alert) and "SAILING CANCELLATION ALERT" in alert["narration"])
    appr = next((x for x in got if x.get("card_kind") == "review" and x["card_payload"].get("approve_cta", {}).get("selection", {}).get("action") == "approve_recovery"), None)
    rows = {r["label"]: r["value"] for r in appr["card_payload"]["summary_rows"]} if appr else {}
    check("🔔 approval card offers Beacon BEA-201", "BEA-201" in rows.get("Replacement", ""), rows.get("Replacement"))
    check("approval card states the fare difference", "fare difference" in rows.get("If you approve", ""), rows.get("If you approve"))
    if not appr:
        return report()
    say(json.dumps(appr["card_payload"]["approve_cta"]["selection"]))
    got = wait_for(lambda xs: any(x.get("kind") == "PAYMENT_REQUEST" for x in xs), 150, "rebooked + fare difference")
    reb = next((x for x in got if x.get("card_kind") == "detail" and x["card_payload"]["title"] == "Rebooked"), None)
    check("✅ REBOOKED card", bool(reb) and "REBOOKED" in reb["narration"])
    topup = next((x for x in got if x.get("kind") == "PAYMENT_REQUEST"), None)
    new = db("SELECT booking_id, sailing_id, total_price_minor FROM bookings WHERE replaces_booking_id = %s", (bk_a,))
    diff = new[0][2] - paid_freight if new else None
    check("replacement committed on BEA-201", bool(new) and new[0][1] == "BEA-201", new and f"{new[0][0]} {new[0][2] / 100:,.0f}")
    check("fare-difference request equals new − original freight", bool(topup) and diff is not None
          and int(round(float(topup["payment"]["amount"]) * 100)) == diff, topup and f"{topup['payment']['amount']} vs {diff and diff / 100}")
    if topup and not no_pay:
        check("fare difference paid with 4242", pay(topup["payment"]["metadata"]["checkout_url"]))
        got = wait_for(lambda xs: any(x.get("card_kind") == "detail" and x["card_payload"]["title"] == "Fare difference paid" for x in xs), 90, "fare paid")
        check("'Fare difference paid' card", any(x.get("card_kind") == "detail" and x["card_payload"]["title"] == "Fare difference paid" for x in got))
    b = db("""SELECT b.buyer_id, b.status, n.sailing_id FROM bookings b LEFT JOIN bookings n ON n.replaces_booking_id = b.booking_id
              WHERE b.buyer_id IN ('buyer_b', 'buyer_c') AND b.replaces_booking_id IS NULL ORDER BY b.buyer_id""")
    check("Buyer B auto-rebooked to Cove COV-301", ("buyer_b", "SUPERSEDED", "COV-301") in b, str(b))
    check("Buyer C untouched", ("buyer_c", "COMMITTED", None) in b)

    print("4. Read-only surfaces")
    inc = httpx.get(f"{OPS}/api/ops/incidents").json()["incidents"][0]
    check("Mission Control incident RESOLVED 2/2", inc["stage"] == "RESOLVED" and inc["confirmed"] == inc["affected"] == 2, f"{inc['stage']} {inc['confirmed']}/{inc['affected']}")
    rb = httpx.get(f"{OPS}/api/ops/replay", params={"incident_id": inc["incidentId"]}).json()
    check("Time Machine has a checkpoint and complete coverage", bool(rb["checkpoint"]) and rb["coverage"]["complete"], rb["coverage"].get("reason"))
    for path in ["/carrier/atlas/sailings", "/carrier/atlas/sailings/ATL-101", "/carrier/atlas/risk", "/carrier/atlas/activity",
                 "/carrier/atlas/pricing", "/market", "/carrier/beacon/sailings/BEA-201"]:
        r = httpx.get(f"{CONSOLE}{path}")
        check(f"console {path}", r.status_code == 200 and "Traceback" not in r.text, str(r.status_code))
    audit = subprocess.run(["uv", "run", "python", "scripts/demo.py", "audit"], capture_output=True, text=True)
    check("ledger invariants (demo.py audit)", "audit: PASS" in audit.stdout)
    return report()


def report() -> int:
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed" + ("" if not bad else ": FAILED " + ", ".join(r[0] for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--no-pay", action="store_true", help="stop the booking at the payment request")
    p.add_argument("--preseeded", action="store_true", help="start from a paid booking; test disruption → recovery")
    a = p.parse_args()
    sys.exit(main(a.no_pay, a.preseeded))
