"""Run one company Carrier Agent: uv run --project carriers python -m carrier_agent --carrier atlas"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from psycopg_pool import AsyncConnectionPool
from uagents_core.identity import Identity

from carrier_agent.agent import build_carrier_agent
from carrier_agent.inventory import CarrierInventory

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    load_dotenv(ROOT / ".env")
    fixture = json.loads((ROOT / "fixtures" / "demo_carriers.json").read_text())
    ports = {c["carrier_id"]: c["port"] for c in fixture["carriers"]}

    parser = argparse.ArgumentParser()
    parser.add_argument("--carrier", required=True, choices=sorted(ports))
    args = parser.parse_args()
    carrier_id = args.carrier

    seed = os.environ.get(f"CARRIER_{carrier_id.upper()}_SEED", "")
    exchange = os.environ.get("EXCHANGE_AGENT_ADDRESS", "").strip()
    db_url = os.environ.get("TIGER_DATABASE_URL", "")
    missing = [k for k, v in {
        f"CARRIER_{carrier_id.upper()}_SEED": seed, "EXCHANGE_AGENT_ADDRESS": exchange, "TIGER_DATABASE_URL": db_url,
    }.items() if not v]
    if missing:
        print(f"missing env: {', '.join(missing)}", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO)
    address = Identity.from_seed(seed, 0).address

    async def check_registration(pool: AsyncConnectionPool) -> None:
        await pool.open(wait=True, timeout=20)
        registered = await CarrierInventory(pool, carrier_id).registered_address()
        if registered != address:
            raise SystemExit(
                f"{carrier_id}: seed address {address} does not match Tiger carriers.agent_address "
                f"({registered}); run the seed script"
            )

    pool = AsyncConnectionPool(db_url, min_size=1, max_size=4, open=False,
                               kwargs={"connect_timeout": 15}, check=AsyncConnectionPool.check_connection)
    agent = build_carrier_agent(
        carrier_id=carrier_id, seed=seed, port=ports[carrier_id],
        inventory=CarrierInventory(pool, carrier_id), allowed_requesters={exchange},
    )

    @agent.on_event("startup")
    async def _startup(ctx):
        await check_registration(pool)
        ctx.logger.info(f"{carrier_id} carrier ready at {agent.address} (Tiger registration verified)")

    @agent.on_event("shutdown")
    async def _shutdown(ctx):
        await pool.close()

    agent.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
