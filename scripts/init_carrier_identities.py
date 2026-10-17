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
    existing = ENV.read_text() if ENV.exists() else ""
    present = {line.split("=", 1)[0] for line in existing.splitlines() if "=" in line}
    added = []
    lines = []
    for cid in carriers:
        key = f"CARRIER_{cid.upper()}_SEED"
        if key not in present:
            lines.append(f"{key}={secrets.token_hex(32)}")
            added.append(key)
    if lines:
        with ENV.open("a") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("\n".join(lines) + "\n")
        os.chmod(ENV, 0o600)

    from dotenv import dotenv_values

    values = dotenv_values(ENV)
    for cid in carriers:
        address = Identity.from_seed(values[f"CARRIER_{cid.upper()}_SEED"], 0).address
        print(f"{cid:<8} {address}")
    print(f"added: {added or 'none (all present)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
