#!/usr/bin/env bash
# إضافة كلمة مرور قاعدة بيانات Supabase إلى ملف .env بأمان.
# لا تظهر كلمة المرور على الشاشة ولا تُحفظ في سجل الأوامر.
set -euo pipefail
cd "$(dirname "$0")/.."

DB_USER="postgres.zeiwswzudxgxnttihplc"
POOL_HOST="aws-0-ap-northeast-1.pooler.supabase.com"

read -rsp "كلمة مرور قاعدة بيانات Supabase: " DB_PASSWORD
echo

if [ -z "$DB_PASSWORD" ]; then
  echo "لم تُدخل كلمة مرور — أُلغيت العملية." >&2
  exit 1
fi

# ترميز الرموز الخاصة حتى لا تكسر رابط الاتصال
ENC=$(DB_PASSWORD="$DB_PASSWORD" python3 -c '
import os, urllib.parse
print(urllib.parse.quote(os.environ["DB_PASSWORD"], safe=""))
')

RUNTIME="postgresql://${DB_USER}:${ENC}@${POOL_HOST}:6543/postgres"
DIRECT="postgresql://${DB_USER}:${ENC}@${POOL_HOST}:5432/postgres"

python3 - "$RUNTIME" "$DIRECT" <<'PY'
import sys, pathlib
runtime, direct = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env")
lines = []
for line in p.read_text(encoding="utf-8").splitlines():
    if line.startswith("DATABASE_URL="):
        lines.append(f"DATABASE_URL={runtime}")
    elif line.startswith("DIRECT_DATABASE_URL="):
        lines.append(f"DIRECT_DATABASE_URL={direct}")
    else:
        lines.append(line)
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

unset DB_PASSWORD ENC RUNTIME DIRECT
chmod 600 .env
echo "تمت إضافة كلمة المرور إلى .env (الصلاحيات 600)."
