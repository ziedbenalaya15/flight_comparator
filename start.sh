#!/bin/sh
set -e

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL est obligatoire. Ajoutez une référence vers Postgres dans les variables du service Railway." >&2
  exit 1
fi

attempt=1
max_attempts="${DB_MIGRATION_MAX_ATTEMPTS:-10}"
until alembic upgrade head; do
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "Connexion PostgreSQL impossible après ${max_attempts} tentatives." >&2
    exit 1
  fi
  echo "PostgreSQL indisponible (tentative ${attempt}/${max_attempts}), nouvel essai dans 3 s..." >&2
  attempt=$((attempt + 1))
  sleep 3
done

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
