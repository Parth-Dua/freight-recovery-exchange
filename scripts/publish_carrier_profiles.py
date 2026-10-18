"""Publish each Carrier Agent's profile to Agentverse so judges can find all five companies.

    uv run --project carriers python scripts/publish_carrier_profiles.py            # dry run: print profiles
    uv run --project carriers python scripts/publish_carrier_profiles.py --publish  # register on Agentverse

Needs AGENTVERSE_API_KEY and the CARRIER_<ID>_SEED values the carriers actually run with (each profile
is signed with its carrier's seed, so it must be the machine that runs the carriers). Only the profile
(name, description, README, protocol) changes: the Almanac endpoint the exchange resolves is untouched,
so negotiation keeps working exactly as before. Never prints seeds or the API key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from uagents_core.identity import Identity
from uagents_core.utils.registration import (
    AgentverseConfig,
    AgentverseRegistrationRequest,
    AgentverseRequestError,
    RegistrationRequestCredentials,
    register_agent,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "carriers"))

from carrier_agent.agent import build_carrier_agent  # noqa: E402
DEFAULT_CORRIDOR = "AEJEA-INBOM"
PORTS = {"AEJEA": "Jebel Ali", "INBOM": "Mumbai (Nhava Sheva)", "SADMM": "Dammam", "INMUN": "Mundra",
         "OMSOH": "Sohar", "PKKHI": "Karachi"}
GOALS = {
    "fill_ship": ("Fill the ship", "Concedes faster when space is empty: an unsold slot earns nothing once the ship sails."),
    "protect_margin": ("Protect margin", "Holds its price and concedes little, even when space is open."),
    "balanced": ("Balanced", "Trades off price against utilization: moves when space is open, firm when it is tight."),
}


def lane(code: str) -> str:
    a, b = code.split("-")
    return f"{PORTS.get(a, a)} → {PORTS.get(b, b)}"


def profile(carrier: dict) -> tuple[str, str]:
    """Short description and README built from the public parts of the fixture (never the price floor)."""
    name, policy = carrier["display_name"], carrier["policy"]
    goal, goal_line = GOALS.get(policy["business_goal"], (policy["business_goal"], ""))
    corridors = sorted({s.get("corridor", DEFAULT_CORRIDOR) for s in carrier["sailings"]})
    vessels = ", ".join(v["name"] for v in carrier["vessels"])
    description = (f"{name}'s pricing agent on the Parley freight exchange. Quotes and negotiates container space "
                   f"under {name}'s own private policy ({goal.lower()}).")
    readme = f"""# {name} · Carrier Agent

{name} is one of five independent ocean carriers on **Parley**, an agent-native freight exchange built for
LA Hacks AI 2026. This agent sells {name}'s container space: it quotes, answers counter-offers and holds
slots, all under a pricing policy only {name} can see.

## Strategy: {goal}
{goal_line}

## Routes and fleet
{chr(10).join(f"- {lane(c)}" for c in corridors)}

Vessels: {vessels}.

## How it negotiates
- Opens with a quote for the buyer's cargo, then answers up to two counter-offers.
- Every offer is signed by this agent's key and checked against live capacity in Tiger Data before it counts.
- Its price floor, concession rules and disruption surcharge stay private. Buyers only ever see offers.
- When a sailing is cancelled, it bids its spare capacity into the exchange's recovery round.

## Who it talks to
Only the Parley exchange agent: requests from any other sender are refused. Buyers reach it through
`@freight-parley` on ASI:One. Payment terms: {policy["payment_terms"]}.

Simulated market: fictional carrier, sailings and prices. No real freight is booked.
"""
    return description, readme


def protocol_digests(carrier_id: str, seed: str, port: int) -> list[str]:
    agent = build_carrier_agent(carrier_id=carrier_id, seed=seed, port=port, inventory=None, allowed_requesters=set())
    return list(agent.protocols)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="register on Agentverse (default: dry run)")
    parser.add_argument("--carrier", action="append", help="only these carrier ids (repeatable)")
    parser.add_argument("--endpoint-host", default="http://127.0.0.1",
                        help="host part of the endpoint shown on the profile; the agent serves <host>:<port>/submit")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    carriers = json.loads((ROOT / "fixtures" / "demo_carriers.json").read_text())["carriers"]
    if args.carrier:
        carriers = [c for c in carriers if c["carrier_id"] in args.carrier]
    api_key = os.environ.get("AGENTVERSE_API_KEY", "").strip()
    if args.publish and not api_key:
        print("missing env: AGENTVERSE_API_KEY (create one at agentverse.ai → Profile → API keys)", file=sys.stderr)
        return 2

    failed = 0
    for carrier in carriers:
        cid = carrier["carrier_id"]
        seed = os.environ.get(f"CARRIER_{cid.upper()}_SEED", "").strip()
        if not seed:
            print(f"{cid}: skipped, CARRIER_{cid.upper()}_SEED is not set")
            failed += 1
            continue
        address = Identity.from_seed(seed, 0).address
        description, readme = profile(carrier)
        request = AgentverseRegistrationRequest(
            name=f"{carrier['display_name']} · Carrier Agent",
            endpoint=f"{args.endpoint_host}:{carrier['port']}/submit",
            protocols=protocol_digests(cid, seed, carrier["port"]),
            description=description,
            readme=readme,
            active=True,
        )
        print(f"\n{cid}: {request.name}  {address}")
        if not args.publish:
            print(f"  {description}\n" + "\n".join(f"  | {line}" for line in readme.splitlines()))
            continue
        try:
            register_agent(request, AgentverseConfig(),
                           RegistrationRequestCredentials(agentverse_api_key=api_key, agent_seed_phrase=seed))
            print(f"  published: https://agentverse.ai/agents/details/{address}/profile")
        except AgentverseRequestError as exc:
            print(f"  FAILED: {exc}")
            failed += 1
    if not args.publish:
        print("\nDry run. Check each address matches the README's agents table, then rerun with --publish.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
