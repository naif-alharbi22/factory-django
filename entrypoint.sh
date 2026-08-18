#!/bin/sh
# نقطة الدخول: تهيئة قاعدة البيانات ثم تشغيل الخادم
set -e

echo "[factory] انتظار قاعدة البيانات..."
python - <<'PY'
import os, sys, time
import django
from django.db import connections
from django.db.utils import OperationalError

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

deadline = time.time() + int(os.environ.get("DB_WAIT_SECONDS", "60"))
last = None
while time.time() < deadline:
    try:
        connections["default"].ensure_connection()
        print("[factory] قاعدة البيانات جاهزة")
        sys.exit(0)
    except OperationalError as exc:
        last = exc
        time.sleep(2)
print(f"[factory] تعذّر الاتصال بقاعدة البيانات: {last}", file=sys.stderr)
sys.exit(1)
PY

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "[factory] تطبيق الترحيلات..."
    python manage.py migrate --noinput
fi

echo "[factory] بدء التشغيل"
exec "$@"
