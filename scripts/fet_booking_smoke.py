"""End-to-end paid booking with a REAL Fetch stable-testnet fee, driving the API as the gateway would.

Usage: uv run --project gateway python scripts/fet_booking_smoke.py
Needs carriers + Exchange API (PAYMENT_MODE=fet_testnet) on 127.0.0.1:8900. A throwaway buyer wallet is
funded from the testnet faucet. Steps: form -> offers -> select -> review -> confirm (carrier hold) ->
RequestPayment -> real transfer -> CommitPayment (with buyer_fet_wallet) -> ledger proof -> carrier
confirmation -> receipt; then a replayed commit (same answer, no second booking) and the same tx
reused for a second booking (refused). Not ASI:One evidence (no ASI:One wallet UI involved).
"""

import asyncio
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from cosmpy.aerial.client import LedgerClient, NetworkConfig  # noqa: E402
from cosmpy.aerial.faucet import FaucetApi  # noqa: E402
from cosmpy.aerial.wallet import LocalWallet  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from buyer_gateway.payment_verifier import verify_payment  # noqa: E402

load_dotenv(ROOT / ".env")
API = os.getenv("SMOKE_API", "http://127.0.0.1:8900")
TOKEN = os.environ["GATEWAY_BACKEND_TOKEN"]
GATEWAY_WALLET = "fetch1n5k4mx8f3svnv9vxl9zjk7cjvglkmfz40wnxw7"
GATEWAY = "agent1qsmokegateway"


def call(method, path, body=None):
    req = urllib.request.Request(f"{API}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"x-gateway-token": TOKEN, "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


class Buyer:
    def __init__(self):
        self.addr, self.session = f"agent1qpaidbuyer{uuid.uuid4().hex[:6]}", str(uuid.uuid4())

    def say(self, text):
        return call("POST", "/internal/chat/intent", {"event_id": str(uuid.uuid4()), "sender_agent_address": self.addr,
                                                      "chat_session_id": self.session, "gateway_agent_address": GATEWAY,
                                                      "gateway_wallet": GATEWAY_WALLET, "text": text})

    def outbox(self):
        for _ in range(90):
            items = [i for i in call("GET", f"/internal/chat/outbox?gateway={GATEWAY}")["items"] if i["recipient"] == self.addr]
            for i in items:
                call("POST", f"/internal/chat/outbox/{i['outbox_id']}/ack", {"status": "SENT"})
            if items:
                return items[0]["result"]
            time.sleep(2)
        raise SystemExit("no offers arrived")

    def payment(self, **body):
        return call("POST", "/internal/payment/event", {"sender_agent_address": self.addr,
                                                         "chat_session_id": self.session, **body})

    def book_until_payment(self):
        form = self.say("I need to ship cargo")
        opts = [f for f in form["card_payload"]["fields"] if f["name"] == "latest_arrival"][0]["options"]
        sel = {**form["card_payload"]["submit_cta"]["selection"], "origin_port": "AEJEA", "destination_port": "INBOM",
               "quantity": "1", "latest_arrival": opts[min(4, len(opts) - 1)]["value"], "priority": "cheapest",
               "recovery_allowance": "300"}
        self.say(json.dumps(sel))
        carousel = self.outbox()
        pick = carousel["card_payload"]["items"][0]
        print("  offer:", pick["title"], "|", pick["subtitle"])
        review = self.say(json.dumps(pick["primary_cta"]["selection"]))
        result = self.say(json.dumps(review["card_payload"]["approve_cta"]["selection"]))
        assert result["kind"] == "PAYMENT_REQUEST", result
        return result["payment"]


async def gateway_commit(buyer: Buyer, payment: dict, tx_hash: str, wallet: str) -> dict:
    """What the gateway does on CommitPayment: claim -> backend checks -> ledger proof -> backend verdict."""
    env = {"type": "commit", "reference": payment["reference"], "transaction_id": tx_hash, "recipient": GATEWAY_WALLET,
           "funds": {"currency": "FET", "amount": payment["amount"], "payment_method": "fet_direct"},
           "metadata": {"buyer_fet_wallet": wallet}}
    verdict = buyer.payment(**env)
    if verdict.get("action") == "verify":
        proof = await verify_payment(verdict["expected"])
        verdict = buyer.payment(**{**env, "type": "ledger_result", "verified": proof.verified, "detail": proof.detail,
                                   "proof": proof.proof})
    return verdict


async def main() -> int:
    cfg = NetworkConfig.fetchai_stable_testnet()
    ledger = LedgerClient(cfg)
    wallet = LocalWallet.generate()
    FaucetApi(cfg).get_wealth(wallet.address())
    for _ in range(30):
        if ledger.query_bank_balance(wallet.address(), "atestfet") > 0:
            break
        time.sleep(2)
    print("throwaway buyer wallet:", wallet.address())

    a = Buyer()
    print("1 buyer A books until the payment request")
    pay = a.book_until_payment()
    print("  RequestPayment:", {k: pay[k] for k in ("reference", "amount", "currency", "recipient")})
    units = int(float(pay["amount"]) * 10**18)
    tx = ledger.send_tokens(pay["recipient"], units, "atestfet", wallet).wait_to_complete()
    print("2 real testnet transfer:", tx.tx_hash)
    verdict = await gateway_commit(a, pay, tx.tx_hash, str(wallet.address()))
    receipt = (verdict.get("followups") or [{}])[0]
    print("3 verdict:", verdict["action"], "| receipt:", receipt.get("narration") or receipt.get("text"))
    again = await gateway_commit(a, pay, tx.tx_hash, str(wallet.address()))
    print("4 replayed commit:", again["action"], "| same receipt:",
          (again.get("followups") or [{}])[0].get("narration") == receipt.get("narration"))

    b = Buyer()
    print("5 buyer B tries to reuse A's transaction for a new booking")
    pay_b = b.book_until_payment()
    reuse = await gateway_commit(b, pay_b, tx.tx_hash, str(wallet.address()))
    print("  verdict:", reuse["action"], "|", reuse.get("reason"))
    return 0 if verdict["action"] == "complete" and reuse["action"] == "cancel" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
