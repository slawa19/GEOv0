#!/bin/bash
set -e

# Preflight: ensure alembic_version.version_num can store long revision ids.
# Alembic default is VARCHAR(32), but this repo uses longer revision strings
# (e.g. 011_transactions_payment_payload_btree_indexes).
python - <<'PY'
import asyncio
import os
import re

import asyncpg


def normalize_pg_url(url: str) -> str:
    # Expected: postgresql+asyncpg://user:pass@host:port/db
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql://"):
        return url
    raise SystemExit(f"Unsupported DATABASE_URL scheme: {url.split(':', 1)[0]}")


async def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL is not set")

    pg_url = normalize_pg_url(db_url)

    # Ensure table exists and column is wide enough (idempotent).
    ddl = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'alembic_version'
  ) THEN
    CREATE TABLE alembic_version (
      version_num VARCHAR(128) NOT NULL PRIMARY KEY
    );
  ELSE
    ALTER TABLE alembic_version
      ALTER COLUMN version_num TYPE VARCHAR(128);
  END IF;
END $$;
"""
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute(ddl)
    finally:
        await conn.close()


asyncio.run(main())
PY

# Run migrations
alembic -c migrations/alembic.ini upgrade head

# Load seed data (if needed)
# python scripts/seed_db.py

# Start application
exec uvicorn app.main:app --host 0.0.0.0 --port 8000