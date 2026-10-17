"""Apply SQL migrations, then create Timescale hypertables.

Usage: uv run python -m freight_exchange.db.migrate
Never prints the database URL.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from freight_exchange.config import PROJECT_ROOT, database_url

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
HYPERTABLES = (("market_events", "ts"), ("market_snapshots", "ts"))


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("-")[0].split(".")[:3] if p.isdigit())


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    conn.commit()
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    newly_applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.stem in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        newly_applied.append(path.stem)
    return newly_applied


def timescale_version(conn: psycopg.Connection) -> str | None:
    row = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'").fetchone()
    return row[0] if row else None


def ensure_hypertables(conn: psycopg.Connection) -> str:
    version = timescale_version(conn)
    if version is None:
        conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        conn.commit()
        version = timescale_version(conn)
    if version is None:
        raise RuntimeError("TimescaleDB extension is not available on this database")

    # by_range() dimension builder exists from TimescaleDB 2.13; older uses the legacy form.
    new_api = _version_tuple(version) >= (2, 13)
    with conn.transaction():
        for table, column in HYPERTABLES:
            if new_api:
                conn.execute(
                    "SELECT create_hypertable(%s::regclass, by_range(%s), if_not_exists => TRUE)",
                    (table, column),
                )
            else:
                conn.execute(
                    "SELECT create_hypertable(%s::regclass, %s, if_not_exists => TRUE)",
                    (table, column),
                )
    return version


def list_hypertables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    with psycopg.connect(database_url(), connect_timeout=15) as conn:
        applied = apply_migrations(conn)
        version = ensure_hypertables(conn)
        print(f"migrations applied this run: {applied or 'none (already current)'}")
        print(f"timescaledb {version}; hypertables: {list_hypertables(conn)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
