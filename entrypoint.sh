#!/usr/bin/env bash
set -euo pipefail

wait_for_postgres() {
  echo "Waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
  for _ in $(seq 1 60); do
    if python -c "
import socket,os,sys
s=socket.socket()
s.settimeout(2)
try:
    s.connect((os.environ['POSTGRES_HOST'], int(os.environ['POSTGRES_PORT'])))
except OSError:
    sys.exit(1)
"; then
      echo "PostgreSQL is up."
      return 0
    fi
    sleep 2
  done
  echo "PostgreSQL did not become reachable in time." >&2
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