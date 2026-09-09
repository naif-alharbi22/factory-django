#!/bin/sh
# Entrypoint: prepare the database, then start the server
set -e

echo "[factory] waiting for the database..."
python - <<'PY'
import os, sys, time
import django
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.db.utils import OperationalError

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
try:
    django.setup()
except ImproperlyConfigured as exc:
    # Connection details are missing — print the message, not a long traceback
    print(f"[factory] {exc}", file=sys.stderr)
    sys.exit(1)

deadline = time.time() + int(os.environ.get("DB_WAIT_SECONDS", "60"))
last = None
while time.time() < deadline:
    try:
        connections["default"].ensure_connection()
        print("[factory] database is ready")
        sys.exit(0)
    except OperationalError as exc:
        last = exc
        time.sleep(2)
print(f"[factory] could not connect to the database: {last}", file=sys.stderr)

# A host that does not resolve is a different problem from one that refuses
# the connection: the database container is not there at all. That happens
# when a new service reaches the server but nothing recreates the stack —
# Watchtower swaps images, it never reads the compose file.
if "name resolution" in str(last) or "could not translate host name" in str(last):
    host = connections["default"].settings_dict.get("HOST") or "?"
    print(
        f"[factory] the host '{host}' does not resolve, so no container is "
        f"answering to that name.\n"
        f"[factory] if '{host}' is a compose service, create it on the server "
        f"with:\n"
        f"[factory]     docker compose -f compose.prod.yml up -d",
        file=sys.stderr,
    )
sys.exit(1)
PY

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "[factory] applying migrations..."
    python manage.py migrate --noinput
fi

echo "[factory] starting up"
exec "$@"
