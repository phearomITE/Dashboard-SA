"""Docker health check: verify the bot process can reach PostgreSQL."""
from __future__ import annotations

import sys

from sqlalchemy import text

from app.database.database import engine


def main() -> int:
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT 1")).scalar() != 1:
                return 1
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
