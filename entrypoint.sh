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
            echo "PostgreSQL is up and accepting connections."
            return 0
        fi
        sleep 1
    done
    echo "PostgreSQL unreachable within timeout."
    exit 1
}

wait_for_postgres

# Execute the main command passed to the container (e.g., uvicorn/fastapi)
exec "$@"