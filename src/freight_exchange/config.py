"""Runtime configuration from environment (.env is loaded by agent.py / CLIs)."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    pass


def database_url() -> str:
    url = os.getenv("TIGER_DATABASE_URL", "").strip()
    if not url:
        raise ConfigError("TIGER_DATABASE_URL is not set (put it in .env; never commit it)")
    return url


def carrier_seed(carrier_id: str) -> str:
    key = f"CARRIER_{carrier_id.upper()}_SEED"
    seed = os.getenv(key, "").strip()
    if not seed:
        raise ConfigError(f"{key} is not set; run `uv run python scripts/init_carrier_identities.py`")
    return seed


def carrier_timeout_seconds() -> float:
    return float(os.getenv("CARRIER_TIMEOUT_SECONDS", "8"))
