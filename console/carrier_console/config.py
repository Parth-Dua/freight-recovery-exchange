"""Runtime configuration. Reads the repo-root .env, same file the exchange uses."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    pass


def load_env() -> None:
    load_dotenv(REPO_ROOT / ".env")


def database_url() -> str:
    url = os.getenv("TIGER_DATABASE_URL", "").strip()
    if not url:
        raise ConfigError(
            "TIGER_DATABASE_URL is not set. It lives in the repo-root .env, "
            "which is gitignored. Never commit it."
        )
    return url


def console_port() -> int:
    return int(os.getenv("CONSOLE_PORT", "8800"))
