#!/usr/bin/env bash
set -euo pipefail

wait_for_postgres() {
  echo "Waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
  for _ in $(seq 1 60); do
    if python -c "
import sys, os, psycopg
try:
    conn = psycopg.connect(
        dbname=os.getenv('POSTGRES_DB'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
        host=os.getenv('POSTGRES_HOST'),
        port=os.getenv('POSTGRES_PORT', '5432'),
        connect_timeout=2
    )
    conn.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
"; then
      echo "PostgreSQL is up and credentials are verified."
      return 0
    fi
    sleep 2
  done
  echo "PostgreSQL unreachable or authentication failed within timeout." >&2
  exit 1
}

case "${1:-api}" in
  api)
    wait_for_postgres
    alembic upgrade head
    exec gunicorn app.main:app \
      --worker-class uvicorn.workers.UvicornWorker \
      --workers "${WEB_CONCURRENCY:-2}" \
      --bind 0.0.0.0:8000 \
      --timeout 120 \
      --graceful-timeout 30 \
      --access-logfile - \
      --error-logfile -
    ;;
  worker)
    wait_for_postgres
    exec arq app.workers.worker.WorkerSettings
    ;;
  migrate)
    wait_for_postgres
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac