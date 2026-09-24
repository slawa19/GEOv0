#!/bin/bash
set -e

# The `alembic_version` precondition (T1535) is NOT spelled here any more. Since T1701 (2026-09-21)
# `migrations/env.py` creates-or-widens `alembic_version.version_num` itself, so every caller of the
# migration entry gets it and this file CALLS that entry instead of carrying a second copy of the DDL.
# What stays here is the one thing the migration entry cannot say as clearly: which URL this container
# was handed, refused before anything connects.
if [ -z "${DATABASE_URL:-}" ]; then
  echo "docker-entrypoint.sh: DATABASE_URL is not set" >&2
  exit 1
fi

case "$DATABASE_URL" in
  # The application's own settings accept postgresql+asyncpg only (017 T1704, app/config.py); a
  # plain postgresql:// would pass here and be refused one step later by the migration entry.
  postgresql+asyncpg://*) ;;
  *)
    # Print the scheme only: the rest of the URL carries credentials.
    echo "docker-entrypoint.sh: unsupported DATABASE_URL scheme: ${DATABASE_URL%%:*}" >&2
    exit 1
    ;;
esac

# Run migrations (this is also what establishes the alembic_version precondition)
alembic -c migrations/alembic.ini upgrade head

# Load seed data (if needed)
# python scripts/seed_db.py

if [ "$#" -eq 0 ]; then
  echo "docker-entrypoint.sh: no command supplied" >&2
  exit 64
fi

# Preserve the image/Compose command after the migration preflight.
exec "$@"
