"""Create the buyer gateway's private seed and backend token in .env (once). Prints only public addresses.

Usage: uv run --project gateway python scripts/init_gateway_identity.py
"""

import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values
from uagents_core.identity import Identity

ENV = Path(__file__).resolve().parents[1] / ".env"
DEFAULTS = {"GATEWAY_PORT": "8110", "GATEWAY_BACKEND_URL": "http://127.0.0.1:8900", "FET_USE_TESTNET": "true",
            "BOOKING_SERVICE_FET_AMOUNT": "0.1", "PAYMENT_RAIL": "fet_direct", "LEGACY_STRIPE_CHECKOUT_ENABLED": "false"}


def main() -> int:
    values = dotenv_values(ENV)
    additions = {}
    for key in ("GATEWAY_AGENT_SEED", "GATEWAY_BACKEND_TOKEN"):
        if not (values.get(key) or "").strip():
            additions[key] = secrets.token_hex(32)
    for key, default in DEFAULTS.items():
        if not (values.get(key) or "").strip():
            additions[key] = default
    if additions:
        with ENV.open("a") as fh:
            fh.write("\n# --- v2: buyer gateway uAgent + Fetch payment (testnet) ---\n")
            for key, value in additions.items():
                fh.write(f"{key}={value}\n")
    seed = additions.get("GATEWAY_AGENT_SEED") or values["GATEWAY_AGENT_SEED"]
    identity = Identity.from_seed(seed, 0)
    print(f"gateway agent address: {identity.address}")
    print(f"added to .env: {sorted(additions) or 'nothing (already set)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
