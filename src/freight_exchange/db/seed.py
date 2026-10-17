"""Deterministic, idempotent demo seed (fictional carriers, source_kind='simulated').

Usage: uv run python -m freight_exchange.db.seed [--anchor 2026-10-20T00:00:00Z]

Reference data (ports, carriers, policies, vessels, sailing schedules) is
upserted. Capacity slots are inserted once and never reset here, so live
holds/commitments from later phases are not clobbered by a re-seed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb
from uagents_core.identity import Identity

from freight_exchange.config import PROJECT_ROOT, carrier_seed, database_url

FIXTURE = PROJECT_ROOT / "fixtures" / "demo_carriers.json"
SOURCE = "simulated"


def load_fixture(path: Path = FIXTURE) -> dict[str, Any]:
    return json.loads(path.read_text())


def carrier_address(carrier_id: str) -> str:
    return Identity.from_seed(carrier_seed(carrier_id), 0).address


def _at(anchor: datetime, offset: dict[str, int]) -> datetime:
    return anchor + timedelta(days=offset.get("days", 0), hours=offset.get("hours", 0))


def seed(conn: psycopg.Connection, fixture: dict[str, Any], anchor: datetime) -> dict[str, int]:
    counts = {"ports": 0, "carriers": 0, "sailings": 0, "slots_inserted": 0}
    corridor = fixture["corridor_id"]
    regions = fixture["route_region_ids"]
    origin, destination = (p["port_code"] for p in fixture["ports"])

    with conn.transaction():
        for p in fixture["ports"]:
            conn.execute(
                """INSERT INTO ports (port_code, name, country_code, aliases, source_kind)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (port_code) DO UPDATE
                   SET name = EXCLUDED.name, country_code = EXCLUDED.country_code, aliases = EXCLUDED.aliases""",
                (p["port_code"], p["name"], p["country_code"], p["aliases"], SOURCE),
            )
            counts["ports"] += 1

        for c in fixture["carriers"]:
            cid = c["carrier_id"]
            conn.execute(
                """INSERT INTO carriers (carrier_id, display_name, agent_address, endpoint_url, source_kind)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (carrier_id) DO UPDATE
                   SET display_name = EXCLUDED.display_name, agent_address = EXCLUDED.agent_address,
                       endpoint_url = EXCLUDED.endpoint_url, updated_at = now()""",
                (cid, c["display_name"], carrier_address(cid),
                 f"http://127.0.0.1:{c['port']}/submit", SOURCE),
            )
            conn.execute(
                """INSERT INTO carrier_policies (carrier_id, version, policy) VALUES (%s, 1, %s)
                   ON CONFLICT (carrier_id) DO UPDATE
                   SET policy = EXCLUDED.policy,
                       version = carrier_policies.version
                                 + CASE WHEN carrier_policies.policy = EXCLUDED.policy THEN 0 ELSE 1 END,
                       updated_at = now()""",
                (cid, Jsonb(c["policy"])),
            )
            for v in c["vessels"]:
                conn.execute(
                    """INSERT INTO vessels (vessel_id, carrier_id, name, source_kind) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (vessel_id) DO UPDATE SET name = EXCLUDED.name""",
                    (v["vessel_id"], cid, v["name"], SOURCE),
                )
            for s in c["sailings"]:
                conn.execute(
                    """INSERT INTO sailings (sailing_id, carrier_id, vessel_id, origin_port, destination_port,
                                             corridor_id, route_region_ids, departure_at, arrival_at, status, source_kind)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s)
                       ON CONFLICT (sailing_id) DO UPDATE
                       SET departure_at = EXCLUDED.departure_at, arrival_at = EXCLUDED.arrival_at,
                           route_region_ids = EXCLUDED.route_region_ids, updated_at = now()""",
                    (s["sailing_id"], cid, s["vessel_id"], origin, destination, corridor, regions,
                     _at(anchor, s["depart"]), _at(anchor, s["arrive"]), SOURCE),
                )
                counts["sailings"] += 1
                for n in range(1, s["slots_20gp"] + 1):
                    cur = conn.execute(
                        """INSERT INTO capacity_slots (slot_id, sailing_id, slot_no, container_type, status, source_kind)
                           VALUES (%s, %s, %s, '20GP', 'AVAILABLE', %s)
                           ON CONFLICT (slot_id) DO NOTHING""",
                        (f"{s['sailing_id']}-20GP-{n:02d}", s["sailing_id"], n, SOURCE),
                    )
                    counts["slots_inserted"] += cur.rowcount
            counts["carriers"] += 1
    return counts


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    tables = ("ports", "carriers", "carrier_policies", "vessels", "sailings", "capacity_slots")
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    fixture = load_fixture()
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchor", default=fixture["anchor_date"], help="ISO-8601 UTC anchor for sailing dates")
    args = parser.parse_args()
    anchor = datetime.fromisoformat(args.anchor.replace("Z", "+00:00"))
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        counts = seed(conn, fixture, anchor)
        print(f"seed upserted: {counts}")
        print(f"table counts: {table_counts(conn)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
