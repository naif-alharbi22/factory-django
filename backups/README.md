# Backups

Database exports are written here and are **never committed**. `.gitignore`
excludes everything in this directory except this file.

A dump of this application carries employee ID numbers, salaries, addresses and
phone numbers, customer records, invoices, and password hashes. Publishing one —
this repository is public — exposes personal data belonging to people who did
not consent to it. A file removed from the latest commit also remains readable
in the git history, so the mistake is not undone by deleting it later.

Take a dump with the same tooling the deployment uses, writing into this
directory:

```bash
docker run --rm --env-file .env -v "$PWD/backups:/out" postgres:17-alpine \
  sh -c 'pg_dump "$DIRECT_DATABASE_URL" --schema=public --no-owner --no-privileges \
         -f /out/dump-$(date +%Y%m%d-%H%M).sql'
```

`DIRECT_DATABASE_URL` is the session pooler (port 5432). The transaction pooler
on 6543 does not support the session features `pg_dump` needs.

To restore into an empty database, drop the two statements that clash with a
schema Supabase has already created:

```bash
sed -e '/^CREATE SCHEMA public;$/d' -e '/^COMMENT ON SCHEMA public IS /d' dump.sql \
  | psql "$TARGET_URL" -v ON_ERROR_STOP=1
```

Keep copies off this machine as well: a backup that only exists on the server it
protects is not a backup.
