# Project Cost Management System

A Django web application for tracking project budgets, labour hours, invoices,
payments and expenses, with a right-to-left Arabic interface.

Built with **Django + HTMX + Tailwind CSS / DaisyUI**.

---

## Features

- Project budgets with live cost and utilisation tracking
- Employee records with automatic hourly and overtime rate calculation
- Timesheet entry and labour cost roll-up per project
- Invoices, payments and miscellaneous expenses
- Dashboard with aggregate statistics
- PDF reports generated server-side (no browser required)
- Group-based permissions enforced on every route

---

## Quick start

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Fill in the Supabase connection details — see [Database](#database) below; the
application will not start without them. Then:

```bash
.venv/bin/python manage.py migrate
```

```bash
.venv/bin/python manage.py createsuperuser
```

```bash
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

Then open **http://localhost:8000**.

### Database

The application runs on **Supabase (PostgreSQL)** and reads its connection
details from the environment — there is no silent fallback to a local file. If
they are missing, startup stops with a message naming what to set.

Copy `.env.example` to `.env` and fill in **one** of these:

- `DATABASE_URL` — the full connection string from Supabase
  (*Project Settings → Database → Connection string*, transaction pooler,
  port `6543`), or
- `SUPABASE_PROJECT_REF`, `SUPABASE_DB_REGION` and `SUPABASE_DB_PASSWORD` —
  the URL is assembled from them and the password is percent-encoded for you.

`./scripts/set-db-password.sh` writes the password into `.env` without echoing
it to the screen or the shell history.

Run migrations through the session pooler (port `5432`), which is what
`DIRECT_DATABASE_URL` holds:

```bash
DATABASE_URL="$DIRECT_DATABASE_URL" .venv/bin/python manage.py migrate
```

For offline development, `USE_SQLITE=1` runs against a local SQLite file
instead. The test suite switches to SQLite on its own, so tests never touch the
Supabase project.

### Dates and times

Timestamps are **stored in UTC** and **displayed in Asia/Riyadh**.

`USE_TZ` is on, so every `DateTimeField` is timezone-aware and the database
connection runs in UTC — rows hold UTC wherever the server sits. `TIME_ZONE`
only decides what pages, PDF reports and form defaults convert to, and it is
set from `DISPLAY_TIME_ZONE` (default `Asia/Riyadh`; an unknown zone name stops
startup rather than silently falling back).

A per-user time zone can be added later without touching any stored data: keep
this value as the fallback and call `timezone.activate(<user zone>)` for the
duration of the request.

### Running the tests

```bash
.venv/bin/python manage.py collectstatic --noinput
```

```bash
.venv/bin/python manage.py test core
```

The suite runs on local SQLite by itself, so it never touches Supabase. The
`collectstatic` step is needed once on a fresh clone: Django forces
`DEBUG=False` during tests, and `ManifestStaticFilesStorage` then resolves
`{% static %}` through `staticfiles/staticfiles.json`, which is generated
rather than committed. Without it every view test fails with *Missing
staticfiles manifest entry*. Re-run it after changing `assets/app.css`.

### Language in the code

Everything developer-facing — comments, docstrings, log lines, CLI output and
error messages — is written in English. Everything user-facing stays Arabic:
templates, field labels (`verbose_name`), choice labels, flash messages, and
data rows such as the default group names.

---

## Running with Docker

```bash
cp .env.example .env
```

Edit `.env`, then:

```bash
docker compose up -d --build
```

Migrations are applied automatically on startup. Open **http://localhost:8000**.

### Useful commands

```bash
docker compose logs -f web
```

```bash
docker compose exec web python manage.py createsuperuser
```

```bash
docker compose restart web
```

```bash
docker compose down
```

### Notes

- The image runs as a non-root user (`factory`, uid 10001).
- Static files are served by WhiteNoise — no separate web server is needed for
  internal use.
- Uploaded media lives in a named volume and survives container rebuilds; the
  application data itself lives in Supabase.

Deployment is automatic: every push to `main` runs the tests, builds the image
on GitHub's runners and publishes it to GHCR, and a Watchtower container on the
VPS picks it up within a couple of minutes. See [DEPLOY.md](DEPLOY.md) for that
setup and for the two manual paths.

---

## Configuration

All configuration is read from environment variables (or a `.env` file).

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(empty)* | Supabase connection URL (transaction pooler, port `6543`). Required unless the `SUPABASE_*` variables below are set. |
| `DIRECT_DATABASE_URL` | *(derived)* | Session-pooler URL (port `5432`) for migrations and imports. |
| `SUPABASE_PROJECT_REF` | *(empty)* | Project reference ID; used to build the URL when `DATABASE_URL` is empty. |
| `SUPABASE_DB_PASSWORD` | *(empty)* | Database password; percent-encoded automatically. |
| `SUPABASE_DB_REGION` | `us-east-1` | Region in the pooler hostname, e.g. `ap-northeast-1`. |
| `SUPABASE_DB_HOST` / `_PORT` / `_USER` / `_NAME` | *(derived)* | Overrides for a self-hosted or non-standard Supabase setup. |
| `USE_SQLITE` | `0` | `1` runs on local SQLite instead of Supabase. Defaults to `1` while running tests. |
| `SQLITE_PATH` | `factory.sqlite3` | SQLite file path, used only when `USE_SQLITE=1`. |
| `DB_SSL_REQUIRE` | `1` | Require TLS for the database connection. |
| `DB_CONN_MAX_AGE` | `60` | Connection reuse time in seconds. |
| `DJANGO_SECRET_KEY` | *(dev key)* | **Must** be set to a long random value in production. |
| `DJANGO_DEBUG` | `1` | Set to `0` in production. |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated hostnames the app will serve. See [Allowed domains](#allowed-domains). |
| `DJANGO_BEHIND_PROXY` | `0` | Set to `1` when running behind a reverse proxy terminating TLS. |
| `DJANGO_SECURE_COOKIES` | `0` | Set to `1` when serving over HTTPS. |
| `DJANGO_CSRF_TRUSTED` | *(derived)* | Overrides the origins derived from `DJANGO_ALLOWED_HOSTS`. Must include the scheme. |
| `RUN_MIGRATIONS` | `1` | Apply migrations on container start. |
| `APP_TITLE` | *(default title)* | Application name shown in the interface. |
| `DISPLAY_TIME_ZONE` | `Asia/Riyadh` | Time zone shown to users. Storage is always UTC. |
| `TZ` | `UTC` | Container clock, used for log timestamps only. |

Generate a secret key with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Never commit a real `.env` file — only `.env.example` belongs in version control.
Empty values are treated as unset, so the defaults above apply.

### Allowed domains

`DJANGO_ALLOWED_HOSTS` controls which hostnames the application answers to.
A request arriving with any other `Host` header is rejected with **HTTP 400**.

```bash
DJANGO_ALLOWED_HOSTS=example.com,www.example.com,203.0.113.10
```

| Form | Matches |
|---|---|
| `example.com` | that exact hostname |
| `.example.com` | `example.com` and every subdomain of it |
| `203.0.113.10` | requests addressed to that IP |
| `*` | any host — fine locally, not recommended in production |

Rules:

- Hostname only — no scheme, no port, no trailing slash.
- Spaces after commas are ignored, so `example.com, www.example.com` works.
- Add every name users actually type. `example.com` and `www.example.com` are
  two different hosts; listing only one rejects the other.

**CSRF origins are derived from this list automatically**, so adding a domain in
one place is enough. Set `DJANGO_CSRF_TRUSTED` only when the public URL differs
from the hostname the container sees — values there must include the scheme
(`https://example.com`).

With `DJANGO_SECURE_COOKIES=1` the derived origins are `https://` only;
otherwise both `http://` and `https://` are trusted.

---

## Permissions

Enforced server-side on every route (`core/permissions.py`):

| Group | Capabilities |
|---|---|
| **admin** | Full access, including user management |
| **accountant** | Manage projects, employees, invoices and payments — no user management |
| **employee** | Timesheet entry only |

Unauthenticated requests are always redirected to the login page.

---

## Project structure

```
.
├── config/                   Project settings, URLs, WSGI entry point
├── core/
│   ├── models.py             Projects, employees, hours, invoices, payments, expenses
│   ├── services.py           Cost calculation and dashboard statistics
│   ├── views.py              Request handlers
│   ├── permissions.py        Access control
│   ├── forms.py              Input forms and validation
│   ├── reports.py            PDF report generation (WeasyPrint)
│   └── templates/            RTL templates using DaisyUI
├── assets/app.css            Tailwind + DaisyUI source stylesheet
├── Dockerfile
├── compose.yaml
└── requirements.txt
```

---

## Cost calculation

```
labour cost      = Σ (regular hours × hourly rate + overtime hours × overtime rate)
                   overtime rate defaults to 1.5 × hourly rate when not set

total cost       = labour cost + invoices + expenses
remaining budget = budget − total cost
utilisation      = total cost ÷ budget × 100

status: over 100% = over budget · over 80% = warning · otherwise = within budget
```

When an employee is created, rates are derived from the base salary and can be
overridden manually:

```
hourly rate   = (base salary + fixed allowance of 1000) ÷ 26 ÷ 8
overtime rate = base salary ÷ 30 ÷ 9 × 1.5
```

---

## Building the stylesheet

The compiled stylesheet at `core/static/css/app.css` is committed, so Node is
only needed when templates or the design change:

```bash
npm install
```

```bash
npm run build:css
```

During development, rebuild on change:

```bash
npm run watch:css
```

---

## PDF reports

Reports are rendered with WeasyPrint directly from HTML templates — no headless
browser involved. Arabic text requires Noto fonts and Pango on the host:

```bash
sudo apt install fonts-noto-core libpango-1.0-0 libpangoft2-1.0-0
```

These are already installed inside the Docker image.

---

## Running tests

```bash
.venv/bin/python manage.py test
```

---

## Production checklist

- [ ] `DJANGO_DEBUG=0`
- [ ] `DJANGO_SECRET_KEY` set to a fresh random value
- [ ] `DJANGO_ALLOWED_HOSTS` restricted to your real hostnames
- [ ] `DJANGO_SECURE_COOKIES=1` and `DJANGO_BEHIND_PROXY=1` behind HTTPS
- [ ] `DJANGO_CSRF_TRUSTED` set to your HTTPS origin
- [ ] Database backups scheduled
