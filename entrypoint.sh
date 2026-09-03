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
sys.exit(1)
PY

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "[factory] applying migrations..."
    python manage.py migrate --noinput
fi

echo "[factory] starting up"
exec "$@"
