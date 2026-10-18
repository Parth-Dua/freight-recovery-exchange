"""Run the buyer gateway: uv run --project gateway python -m buyer_gateway"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from buyer_gateway.agent import build_gateway
from buyer_gateway.backend_client import BackendClient

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    load_dotenv(ROOT / ".env")
    seed = os.getenv("GATEWAY_AGENT_SEED", "").strip()
    token = os.getenv("GATEWAY_BACKEND_TOKEN", "").strip()
    if not seed or not token:
        print("missing env: GATEWAY_AGENT_SEED and GATEWAY_BACKEND_TOKEN (run scripts/init_gateway_identity.py)",
              file=sys.stderr)
        return 2
    if os.getenv("FET_USE_TESTNET", "true").lower() != "true":
        print("refusing to start: FET_USE_TESTNET must be true (demo is testnet-only)", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO)
    backend = BackendClient(os.getenv("GATEWAY_BACKEND_URL", "http://127.0.0.1:8900"), token)
    build_gateway(seed=seed, port=int(os.getenv("GATEWAY_PORT", "8110")), backend=backend).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
