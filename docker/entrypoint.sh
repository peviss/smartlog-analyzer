#!/bin/sh
# Apply pending migrations (and optionally seed) before starting the API.
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Running database migrations..."
  alembic upgrade head
fi

if [ "${SEED_ON_START:-false}" = "true" ]; then
  # Only seed an empty database so restarts do not duplicate data.
  if python -c "
import sys
from sqlalchemy import create_engine, text
from api.core.config import get_settings
with create_engine(get_settings().database_url).connect() as c:
    sys.exit(0 if c.execute(text('SELECT count(*) FROM log_entries')).scalar() == 0 else 1)
"; then
    echo "Seeding sample data..."
    python -m db.seed
  else
    echo "Database already has logs; skipping seed."
  fi
fi

exec "$@"
