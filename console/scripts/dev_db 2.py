"""Your local dev database: a TimescaleDB container on this machine.

    cd console
    uv run python scripts/dev_db.py up       # first time: create it; later: start it
    uv run python scripts/dev_db.py reset    # wipe it, then migrate, seed, preseed B and C

Same software as Tiger Cloud (TimescaleDB), so behavior matches Parth's database.

Safety: it only ever touches a database on 127.0.0.1 or localhost, and refuses to run
otherwise, so it can never wipe the shared Tiger Cloud database used for the demo.
Secrets are generated into the gitignored repo-root .env and never printed.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import psycopg

CONSOLE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CONSOLE_DIR.parent
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

CONTAINER = "freight-tiger-dev"
VOLUME = "freight-tiger-dev-data"
IMAGE = "timescale/timescaledb:latest-pg17"
PORT = 5433
LOCAL_URL = f"postgres://tsdbadmin@127.0.0.1:{PORT}/tsdb?sslmode=disable"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def update_env(updates: dict[str, str]) -> None:
    """Set keys in place, append missing ones, keep everything else untouched."""
    remaining = dict(updates)
    lines = []
    for line in ENV_FILE.read_text().splitlines():
        key, sep, _ = line.partition("=")
        if sep and key.strip() in remaining:
            line = f"{key.strip()}={remaining.pop(key.strip())}"
        lines.append(line)
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)


def ensure_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        shutil.copy(ENV_EXAMPLE, ENV_FILE)
        print(".env created from .env.example")

    values = read_env()
    updates: dict[str, str] = {}
    if not values.get("TIGER_DATABASE_URL"):
        updates["TIGER_DATABASE_URL"] = LOCAL_URL
    if not values.get("PGPASSWORD"):
        updates["PGPASSWORD"] = secrets.token_hex(16)
    for key, value in values.items():
        # .env.example ships these keys empty; an empty seed makes every carrier the same agent.
        if key.startswith("CARRIER_") and key.endswith("_SEED") and not value:
            updates[key] = secrets.token_hex(32)
    if updates:
        update_env(updates)
        print(f".env filled: {sorted(updates)} (values not printed)")
    return read_env()


def require_local(url: str) -> None:
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        sys.exit(f"Refusing: TIGER_DATABASE_URL points at {host!r}, not this machine. "
                 "dev_db.py only manages the local dev database.")


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True)


def container_state() -> str | None:
    result = docker("inspect", "-f", "{{.State.Status}}", CONTAINER, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def wait_until_ready(url: str, password: str, timeout_s: int = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(url, password=password, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            time.sleep(2)  # the image restarts Postgres once after first init
            with psycopg.connect(url, password=password, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            time.sleep(2)
    sys.exit("Database did not become ready in time. Check: docker logs " + CONTAINER)


def run_repo(*args: str, cwd: Path = REPO_ROOT) -> None:
    # Drop the console's VIRTUAL_ENV so `uv run` uses the target project's own env.
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    subprocess.run(["uv", "run", *args], cwd=cwd, check=True, env=env)


def up() -> None:
    values = ensure_env()
    url = values["TIGER_DATABASE_URL"]
    require_local(url)

    state = container_state()
    if state is None:
        docker(
            "run", "-d", "--name", CONTAINER,
            "-p", f"127.0.0.1:{PORT}:5432",
            "-e", "POSTGRES_USER=tsdbadmin",
            "-e", f"POSTGRES_PASSWORD={values['PGPASSWORD']}",
            "-e", "POSTGRES_DB=tsdb",
            "-v", f"{VOLUME}:/var/lib/postgresql/data",
            IMAGE,
        )
        print(f"created container {CONTAINER}")
    elif state != "running":
        docker("start", CONTAINER)
        print(f"started container {CONTAINER}")
    else:
        print(f"container {CONTAINER} already running")

    wait_until_ready(url, values["PGPASSWORD"])
    run_repo("python", "scripts/db.py", "migrate")
    run_repo("python", "scripts/db.py", "seed")


def reset() -> None:
    values = ensure_env()
    require_local(values["TIGER_DATABASE_URL"])
    docker("rm", "-f", CONTAINER, check=False)
    docker("volume", "rm", VOLUME, check=False)
    print("wiped the local dev database")
    up()
    run_repo("python", "-m", "carrier_console.preseed_cli", cwd=CONSOLE_DIR)


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    commands = {"up": up, "reset": reset}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
