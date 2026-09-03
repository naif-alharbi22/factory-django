#!/usr/bin/env bash
# Write the Supabase database password into .env safely.
# The password is never echoed to the screen or kept in the shell history.
#
# The project reference and region are read from .env — from
# SUPABASE_PROJECT_REF and SUPABASE_DB_REGION, or from an existing
# DATABASE_URL — and are prompted for only when neither is available.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || cp .env.example .env

read_env() {  # value of one variable in .env (without sourcing the file)
  sed -n "s/^$1=//p" .env | tail -n 1
}

PROJECT_REF="$(read_env SUPABASE_PROJECT_REF)"
REGION="$(read_env SUPABASE_DB_REGION)"
EXISTING_URL="$(read_env DATABASE_URL)"

# Recover whatever is missing from an existing connection URL
if [ -z "$PROJECT_REF" ]; then
  PROJECT_REF=$(printf '%s' "$EXISTING_URL" | sed -n 's#^postgresql://postgres\.\([^:]*\):.*#\1#p')
fi
if [ -z "$REGION" ]; then
  REGION=$(printf '%s' "$EXISTING_URL" | sed -n 's#.*@aws-0-\([^.]*\)\.pooler\.supabase\.com.*#\1#p')
fi

if [ -z "$PROJECT_REF" ]; then
  read -rp "Supabase project reference (Project Settings > General > Reference ID): " PROJECT_REF
fi
if [ -z "$REGION" ]; then
  read -rp "Database region (e.g. ap-northeast-1): " REGION
fi

if [ -z "$PROJECT_REF" ] || [ -z "$REGION" ]; then
  echo "Project reference and region are both required — aborted." >&2
  exit 1
fi

DB_USER="postgres.${PROJECT_REF}"
POOL_HOST="aws-0-${REGION}.pooler.supabase.com"

read -rsp "Supabase database password: " DB_PASSWORD
echo

if [ -z "$DB_PASSWORD" ]; then
  echo "No password entered — aborted." >&2
  exit 1
fi

# Percent-encode special characters so they cannot break the URL
ENC=$(DB_PASSWORD="$DB_PASSWORD" python3 -c '
import os, urllib.parse
print(urllib.parse.quote(os.environ["DB_PASSWORD"], safe=""))
')

RUNTIME="postgresql://${DB_USER}:${ENC}@${POOL_HOST}:6543/postgres"
DIRECT="postgresql://${DB_USER}:${ENC}@${POOL_HOST}:5432/postgres"

python3 - "$RUNTIME" "$DIRECT" "$PROJECT_REF" "$REGION" <<'PY'
import sys, pathlib

runtime, direct, project_ref, region = sys.argv[1:5]
values = {
    "DATABASE_URL": runtime,
    "DIRECT_DATABASE_URL": direct,
    "SUPABASE_PROJECT_REF": project_ref,
    "SUPABASE_DB_REGION": region,
}

p = pathlib.Path(".env")
lines, seen = [], set()
for line in p.read_text(encoding="utf-8").splitlines():
    key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else None
    if key in values:
        lines.append(f"{key}={values[key]}")
        seen.add(key)
    else:
        lines.append(line)

# Keys not present in the file are appended at the end
missing = [f"{k}={v}" for k, v in values.items() if k not in seen]
if missing:
    lines += ["", "# ===== Supabase ====="] + missing

p.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

unset DB_PASSWORD ENC RUNTIME DIRECT
chmod 600 .env
echo "Password written to .env (mode 600)."
