"""Tiger schema + demo data.

Usage:
  uv run python scripts/db.py migrate          # apply migrations, create hypertables
  uv run python scripts/db.py seed [--anchor ISO-8601]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # src/ is not an installed package (see pyproject note)

from freight_exchange.db import migrate, seed  # noqa: E402

if __name__ == "__main__":
    command = sys.argv.pop(1) if len(sys.argv) > 1 else ""
    if command == "migrate":
        sys.exit(migrate.main())
    if command == "seed":
        sys.exit(seed.main())
    print(__doc__)
    sys.exit(2)
