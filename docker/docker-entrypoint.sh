#!/bin/bash
set -e

# Run migrations
alembic -c migrations/alembic.ini upgrade head

# Load seed data (if needed)
# python scripts/seed_db.py

# Start application
exec uvicorn app.main:app --host 0.0.0.0 --port 8000