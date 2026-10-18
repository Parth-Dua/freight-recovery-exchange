"""Drive the buyer card journey through the internal Exchange API exactly as the gateway would (no ASI:One).

Usage: uv run python scripts/card_flow_smoke.py [--api http://127.0.0.1:8900] [--quantity 1] [--pay]
Needs: carriers + Exchange API running. With PAYMENT_MODE=dev on that API the booking completes without
payment (labelled development). With stripe_test (default) it stops at a real RequestPayment carrying two
Stripe TEST Checkout sessions, checks that "paid" before paying is refused, and with --pay pays the hosted
link with the 4242 test card (headless Chrome) and waits for CompletePayment + receipt from the poller.
With fet_testnet it exercises the refusal paths (missing wallet), releasing the hold. Not ASI:One evidence.
"""

import argparse
import json
import time
import uuid

import httpx
from dotenv import dotenv_values

GATEWAY = "agent1qsmokegateway"


def main(api: str, quantity: int, pay: bool = False) -> None:
    headers = {"x-gateway-token": dotenv_values(".env")["GATEWAY_BACKEND_TOKEN"]}
    buyer, session = f"agent1qsmokebuyer{uuid.uuid4().hex[:6]}", str(uuid.uuid4())

    def say(text, who=None):
        r = httpx.post(f"{api}/internal/chat/intent", headers=headers, timeout=120, json={
            "event_id": str(uuid.uuid4()), "sender_agent_address": who or buyer, "chat_session_id": session,
            "gateway_agent_address": GATEWAY, "gateway_wallet": "fetch1n5k4mx8f3svnv9vxl9zjk7cjvglkmfz40wnxw7",
            "text": text})
        r.raise_for_status()
        return r.json()

    def outbox(wait_s=120):
        end = time.time() + wait_s
        while time.time() < end:
            items = httpx.get(f"{api}/internal/chat/outbox?gateway={GATEWAY}", headers=headers).json()["items"]
            mine = [i for i in items if i["recipient"] == buyer]
            for i in mine:
                httpx.post(f"{api}/internal/chat/outbox/{i['outbox_id']}/ack", headers=headers, json={"status": "SENT"})
            if mine:
                return [i["result"] for i in mine]
            time.sleep(2)
        raise SystemExit("no outbox message")

    form = say("I need to ship cargo")
    print("1 form:", form["card_kind"], [f["name"] for f in form["card_payload"]["fields"]])
    fields = {f["name"]: f for f in form["card_payload"]["fields"]}
    deadline = fields["latest_arrival"]["options"][min(3, len(fields["latest_arrival"]["options"]) - 1)]["value"]
    submit = {**form["card_payload"]["submit_cta"]["selection"], "origin_port": "AEJEA", "destination_port": "INBOM",
              "quantity": str(quantity), "latest_arrival": deadline, "earliest_departure": "", "allow_split": "false",
              "cargo_class": "general", "max_total_price": "", "priority": "balanced", "must_deliver": "false",
              "recovery_allowance": "600"}
    bad = say(json.dumps({**submit, "quantity": "-2"}))
    print("2 invalid form ->", bad["kind"], bad.get("card_kind"), "|", bad["narration"].splitlines()[0])
    submit["card_instance_id"] = bad["card_payload"]["submit_cta"]["selection"]["card_instance_id"]
    print("3 submit ->", say(json.dumps(submit))["text"][:110])
    print("  replayed submit ->", say(json.dumps(submit)).get("narration", "")[:70])
    carousel = outbox()[0]
    items = carousel["card_payload"]["items"] if carousel.get("card_kind") == "carousel" else []
    print("4 carousel:", carousel.get("card_kind") or carousel.get("text"),
          [(i["title"], i["subtitle"]) for i in items])
    if not items:
        return
    pick = items[0]["primary_cta"]["selection"]
    print("  other buyer clicks it ->", say(json.dumps(pick), who="agent1qintruder")["text"][:80])
    review = say(json.dumps(pick))
    print("5 review:", review.get("card_kind"), [(r["label"], r["value"]) for r in review["card_payload"]["summary_rows"]][:6])
    second = items[-1]["primary_cta"]["selection"]
    print("  stale second pick ->", say(json.dumps(second))["text"][:80])
    result = say(json.dumps(review["card_payload"]["approve_cta"]["selection"]))
    print("6 confirm ->", result["kind"], result.get("card_kind"), (result.get("narration") or result.get("text") or "")[:120])
    print("  replayed confirm ->", say(json.dumps(review["card_payload"]["approve_cta"]["selection"])).get("text", "")[:80])
    if result["kind"] != "PAYMENT_REQUEST":
        return
    p = result["payment"]
    print("  payment request:", {k: p[k] for k in ("amount", "currency", "method", "recipient", "deadline_seconds")})

    def event(**body):
        return httpx.post(f"{api}/internal/payment/event", headers=headers, timeout=60, json={
            "sender_agent_address": buyer, "chat_session_id": session, **body}).json()
    if p["method"] == "stripe":
        stripe_meta = p["metadata"]["stripe"]
        print("  metadata.stripe keys:", sorted(stripe_meta), "| all strings:",
              all(isinstance(v, str) for v in stripe_meta.values()), "| hosted link:", bool(p["metadata"].get("checkout_url")))
        print("7 'paid' before paying ->", say("paid")["text"][:90])
        funds = {"currency": "USD", "amount": p["amount"], "payment_method": "stripe"}
        v = event(type="commit", reference=p["reference"], transaction_id="cs_test_forged", funds=funds, metadata={})
        print("  forged commit ->", v["action"], "|", (v.get("followups") or [{}])[0].get("text", "")[:80])
        if not pay:
            return
        import subprocess
        out = subprocess.run(["uv", "run", "--with", "playwright", "python", "scripts/pay_test_checkout.py",
                              p["metadata"]["checkout_url"]], capture_output=True, text=True, timeout=180)
        print("8 paid hosted link with 4242 test card ->", (out.stdout or out.stderr).strip().splitlines()[-1][:100])
        pushed = outbox(90)
        for r in pushed:
            print("9 pushed:", r["kind"], "| tx", (r.get("transaction_id") or "")[:14], "|",
                  [(f.get("card_kind"), f.get("narration", "")[:70]) for f in r.get("followups") or []])
        v = event(type="commit", reference=p["reference"], transaction_id=pushed[0].get("transaction_id") or "",
                  funds=funds, metadata={})
        print("10 duplicate commit after settlement ->", v["action"], "(no second booking)")
        return
    funds = {"currency": "FET", "amount": p["amount"], "payment_method": "fet_direct"}
    v = event(type="commit", reference=p["reference"], transaction_id="A" * 64, funds=funds, metadata={})
    print("7 commit without wallet ->", v["action"], "|", v["reason"][:90])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8900")
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--pay", action="store_true", help="pay the Stripe TEST hosted link with the 4242 card")
    args = parser.parse_args()
    main(args.api, args.quantity, args.pay)
