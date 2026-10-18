"""Real Fetch stable-testnet transfer -> the gateway's ledger verifier (TESTNET ONLY, no real value).

Usage: uv run --project gateway python scripts/fet_verify_smoke.py
Creates a throwaway buyer wallet, funds it from the official testnet faucet, sends the configured fee
to the gateway's wallet, then runs buyer_gateway.payment_verifier on that transaction hash (plus
negative checks on the same real tx). Prints addresses and the tx hash only; never keys or seeds.
This proves the verifier against the real ledger; it is not ASI:One evidence.
"""

import asyncio
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from cosmpy.aerial.client import LedgerClient, NetworkConfig  # noqa: E402
from cosmpy.aerial.faucet import FaucetApi  # noqa: E402
from cosmpy.aerial.wallet import LocalWallet  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from cosmpy.crypto.keypairs import PrivateKey  # noqa: E402
from uagents.config import LEDGER_PREFIX  # noqa: E402
from uagents_core.identity import derive_key_from_seed  # noqa: E402

from buyer_gateway.payment_verifier import verify_payment  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


async def main() -> int:
    cfg = NetworkConfig.fetchai_stable_testnet()
    ledger = LedgerClient(cfg)
    gateway = LocalWallet(PrivateKey(derive_key_from_seed(os.environ["GATEWAY_AGENT_SEED"], LEDGER_PREFIX, 0)),
                          prefix=LEDGER_PREFIX)  # same derivation as uagents.Agent.wallet
    units = int(Decimal(os.getenv("BOOKING_SERVICE_FET_AMOUNT", "0.1")) * 10**18)
    buyer = LocalWallet.generate()
    print("gateway wallet:", gateway.address(), "| throwaway buyer wallet:", buyer.address())
    FaucetApi(cfg).get_wealth(buyer.address())
    for _ in range(30):
        if ledger.query_bank_balance(buyer.address(), "atestfet") >= units * 2:
            break
        time.sleep(2)
    print("buyer balance:", ledger.query_bank_balance(buyer.address(), "atestfet"), "atestfet")
    tx = ledger.send_tokens(gateway.address(), units, "atestfet", buyer).wait_to_complete()
    print("transfer tx:", tx.tx_hash)
    expected = {"tx_hash": tx.tx_hash, "sender": str(buyer.address()), "recipient": str(gateway.address()),
                "amount_units": units, "denom": "atestfet", "network": "stable-testnet"}
    ok = await verify_payment(expected)
    print("verify real tx ->", ok.verified, ok.detail, ok.proof)
    for label, change in [("wrong sender", {"sender": "fetch1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"}),
                          ("amount above paid", {"amount_units": units * 2}),
                          ("wrong recipient", {"recipient": str(buyer.address())})]:
        bad = await verify_payment({**expected, **change})
        print(f"verify with {label} ->", bad.verified, bad.detail)
    return 0 if ok.verified else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
