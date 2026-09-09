#!/usr/bin/env bash
# One-off move of the application data from Supabase into the local `db`
# service, run on the server that hosts the stack:
#
#   ./scripts/migrate_from_supabase.sh
#
# The data goes straight from one database into the other: pg_dump reads the
# public schema over the network and its output is piped into pg_restore in the
# db container. It never lands on disk, so there is no dump file to guard, to
# clean up, or to leak into the repository. The web container is stopped for the
# duration so nothing writes while the copy is in flight, and afterwards the row
# count of every table is compared between the two databases.
#
#   --keep-dump   also write the stream to backups/, as a rollback copy
#   --force       restore even though the target already holds tables
#
# Nothing is deleted on Supabase: the project stays exactly as it is, which is
# what makes rolling back a matter of putting the old DATABASE_URL back.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="${COMPOSE_FILE:-compose.prod.yml}"
DUMP_DIR="backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
DUMP_FILE="$DUMP_DIR/supabase-$STAMP.dump"

FORCE=0
KEEP_DUMP=0
for arg in "$@"; do
    case "$arg" in
        --force)     FORCE=1 ;;
        --keep-dump) KEEP_DUMP=1 ;;
        *) echo "usage: $0 [--force] [--keep-dump]" >&2; exit 2 ;;
    esac
done

# Reading .env with `source` breaks on unquoted values (APP_TITLE holds Arabic
# text with spaces), so pull out single keys instead.
# A key that is simply absent is normal here, so the miss must not trip `set -e`
# through the command substitution that calls this.
env_value() {
    [ -f .env ] || return 0
    grep -m1 "^$1=" .env | cut -d= -f2- \
        | sed -E 's/\r$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/' \
        || true
}

SUPABASE_DUMP_URL="${SUPABASE_DUMP_URL:-$(env_value SUPABASE_DUMP_URL)}"
# Older .env files kept the session-pooler URL under this name.
[ -n "$SUPABASE_DUMP_URL" ] || SUPABASE_DUMP_URL="$(env_value DIRECT_DATABASE_URL)"
POSTGRES_USER="$(env_value POSTGRES_USER)"; POSTGRES_USER="${POSTGRES_USER:-factory}"
POSTGRES_DB="$(env_value POSTGRES_DB)";     POSTGRES_DB="${POSTGRES_DB:-factory}"

if [ -z "$SUPABASE_DUMP_URL" ]; then
    echo "error: set SUPABASE_DUMP_URL in .env — the Supabase connection string" >&2
    echo "       in SESSION mode (port 5432). The transaction pooler on 6543" >&2
    echo "       cannot serve pg_dump." >&2
    exit 1
fi

case "$SUPABASE_DUMP_URL" in
    *:6543/*)
        echo "error: SUPABASE_DUMP_URL points at the transaction pooler (6543)." >&2
        echo "       pg_dump needs the session pooler on port 5432." >&2
        exit 1 ;;
esac

# The image tag has to match the source server's major version, otherwise
# pg_dump refuses to talk to it.
PG_IMAGE="${PG_IMAGE:-postgres:17}"

ERR_LOG="$(mktemp)"
trap 'rm -f "$ERR_LOG"' EXIT

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# Nothing below this point may touch the target until the source has answered.
# An unreachable Supabase used to be discovered after the target schema had
# already been dropped, which left the database emptier than it started.
echo "==> checking Supabase answers"
if ! docker run --rm -i -e PGURL="$SUPABASE_DUMP_URL" "$PG_IMAGE" \
        sh -c 'psql "$PGURL" -tAc "select 1"' >/dev/null 2>"$ERR_LOG"; then
    echo "error: could not read from Supabase. Nothing has been touched." >&2
    sed 's/^/       /' "$ERR_LOG" >&2
    case "$(cat "$ERR_LOG")" in
        *"/var/run/postgresql"*|*"No such file or directory"*)
            echo "" >&2
            echo "       psql fell back to a local socket, which is what happens when the" >&2
            echo "       connection string is empty. Check the SUPABASE_DUMP_URL line in" >&2
            echo "       .env — the URL has to be on that same line, straight after the" >&2
            echo "       '=', with nothing between:" >&2
            echo "         SUPABASE_DUMP_URL=postgresql://postgres.REF:PASSWORD@HOST:5432/postgres" >&2 ;;
    esac
    exit 1
fi
echo "    reachable"

echo "==> checking the target database is empty"
mkdir -p "$DUMP_DIR"
compose up -d db
for _ in $(seq 1 60); do
    compose exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1 && break
    sleep 1
done

existing="$(compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
    "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
     where n.nspname='public' and c.relkind='r'" | tr -d '[:space:]')"
if [ "${existing:-0}" != "0" ] && [ "$FORCE" != "1" ]; then
    echo "error: the target already holds $existing tables in public." >&2
    echo "       Re-run with --force to drop them and restore over the top." >&2
    exit 1
fi

echo "==> stopping the web container so nothing writes during the copy"
compose stop web 2>/dev/null || true

# The dump recreates `public` itself, and the postgres image ships with that
# schema already present — dropping it first keeps the restore error-free
# instead of failing on "schema public already exists".
echo "==> clearing the target schema"
# client_min_messages keeps the DROP from listing all 25 tables it cascades to.
compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 --quiet \
    -c 'SET client_min_messages = warning; DROP SCHEMA IF EXISTS public CASCADE;' >/dev/null

# pg_dump runs in a throwaway container because it has to match the source
# server's major version; the local client may well be older, and pg_dump
# refuses to talk to a newer server than itself.
dump_from_supabase() {
    docker run --rm -i -e PGURL="$SUPABASE_DUMP_URL" "$PG_IMAGE" \
        sh -c 'pg_dump "$PGURL" --schema=public --no-owner --no-privileges --format=custom'
}

# --single-transaction is what makes the pipe safe: if the connection to
# Supabase drops halfway, the restore rolls back rather than leaving the target
# holding half a database.
restore_into_db() {
    compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        --no-owner --no-privileges --single-transaction
}

# Dropping the schema cannot join the restore's transaction, so a copy that
# dies midway leaves the target with no schema at all. Say what that state is
# and how to leave it, rather than letting the next command fail obscurely.
on_copy_failure() {
    echo "" >&2
    echo "error: the copy did not finish. The target now has no schema — the drop" >&2
    echo "       above succeeded and the restore did not. Supabase is untouched." >&2
    echo "       Fix the cause and run this script again; it rebuilds the schema" >&2
    echo "       from scratch. To start the app in the meantime instead, recreate" >&2
    echo "       an empty schema and let migrations fill it:" >&2
    echo "         docker compose -f $COMPOSE_FILE exec -T db \\" >&2
    echo "           psql -U $POSTGRES_USER -d $POSTGRES_DB -c 'CREATE SCHEMA public;'" >&2
    echo "         docker compose -f $COMPOSE_FILE up -d" >&2
    exit 1
}

if [ "$KEEP_DUMP" = "1" ]; then
    echo "==> copying Supabase -> db, keeping a copy in $DUMP_DIR"
    mkdir -p "$DUMP_DIR"
    dump_from_supabase | tee "$DUMP_FILE" | restore_into_db || on_copy_failure
    echo "    kept $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"
    echo "    that file holds employee and customer records — it is covered by"
    echo "    .gitignore, and belongs nowhere near the repository."
else
    echo "==> copying Supabase -> db (streamed; nothing written to disk)"
    dump_from_supabase | restore_into_db || on_copy_failure
fi

echo "==> comparing row counts, table by table"
counts_sql="select string_agg(format('select %L as t, count(*) from %I', relname, relname),
                              ' union all ' order by relname)
            from pg_class c join pg_namespace n on n.oid=c.relnamespace
            where n.nspname='public' and c.relkind='r'"

target_counts="$(compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$counts_sql" \
    | { read -r q; compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F'|' --no-align -t -c "$q"; } \
    | sort)"
source_counts="$(docker run --rm -i -e PGURL="$SUPABASE_DUMP_URL" -e Q="$counts_sql" "$PG_IMAGE" \
    sh -c 'psql "$PGURL" -tAc "$Q" | { read -r q; psql "$PGURL" -F"|" --no-align -t -c "$q"; }' \
    | sort)"

if diff <(echo "$source_counts") <(echo "$target_counts"); then
    echo "    every table matches"
else
    echo "error: row counts differ — see the diff above (< Supabase, > local)." >&2
    echo "       The web container is still stopped; nothing has been switched over." >&2
    exit 1
fi

cat <<'NEXT'

==> copy finished. To switch the application over:

  1. In .env, point DATABASE_URL at the local database and turn SSL off:
       DATABASE_URL=postgresql://factory:PASSWORD@db:5432/factory
       DB_SSL_REQUIRE=0
     Clear DIRECT_DATABASE_URL, SUPABASE_PROJECT_REF and SUPABASE_DB_PASSWORD.

  2. Bring the stack back up:
       docker compose -f compose.prod.yml up -d

  3. Check the log says the migrations are already applied:
       docker compose -f compose.prod.yml logs -f web

Leave the Supabase project running for a week. Rolling back is then only a
matter of restoring the old DATABASE_URL and restarting.
NEXT
