"""Create one private seed per fictional carrier in .env (idempotent; never prints seeds).

Usage: uv run python scripts/init_carrier_identities.py
"""

import json
import os
import secrets
import sys
from pathlib import Path

from uagents_core.identity import Identity

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


def main() -> int:
    carriers = [c["carrier_id"] for c in json.loads((ROOT / "fixtures/demo_carriers.json").read_text())["carriers"]]
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    added = []
    for cid in carriers:
        key = f"CARRIER_{cid.upper()}_SEED"
        idx = next((i for i, line in enumerate(lines) if line.split("=", 1)[0].strip() == key), None)
        if idx is not None and lines[idx].split("=", 1)[1].strip():
            continue  # already has a real seed; never overwrite
        entry = f"{key}={secrets.token_hex(32)}"
        if idx is None:
            lines.append(entry)
        else:  # present but empty (e.g. copied from .env.example): fill in place
            lines[idx] = entry
        added.append(key)
    if added:
        ENV.write_text("\n".join(lines) + "\n")
        os.chmod(ENV, 0o600)

    from dotenv import dotenv_values

    values = dotenv_values(ENV)
    addresses = {cid: Identity.from_seed(values[f"CARRIER_{cid.upper()}_SEED"], 0).address for cid in carriers}
    if len(set(addresses.values())) != len(addresses):
        print("ERROR: carriers share an address; check CARRIER_*_SEED values in .env", file=sys.stderr)
        return 1
    for cid, address in addresses.items():
        print(f"{cid:<8} {address}")
    print(f"added: {added or 'none (all present)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
