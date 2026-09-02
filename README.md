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
.venv/bin/python manage.py migrate
```

```bash
.venv/bin/python manage.py createsuperuser
```

```bash
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

Then open **http://localhost:8000**.

With no `DATABASE_URL` set, the application uses a local SQLite file — no
external services required to get running.

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
- Uploaded media and the SQLite fallback database live in named volumes and
  survive container rebuilds.

For deploying to a VPS, see [DEPLOY.md](DEPLOY.md).

---

## Configuration

All configuration is read from environment variables (or a `.env` file).

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(empty)* | PostgreSQL connection URL. Leave empty to use SQLite. |
| `SQLITE_PATH` | `factory.sqlite3` | SQLite file path, used only when `DATABASE_URL` is empty. |
| `DB_SSL_REQUIRE` | `1` | Require TLS for the database connection. |
| `DB_CONN_MAX_AGE` | `60` | Connection reuse time in seconds. |
| `DJANGO_SECRET_KEY` | *(dev key)* | **Must** be set to a long random value in production. |
| `DJANGO_DEBUG` | `1` | Set to `0` in production. |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated hostnames the app will serve. |
| `DJANGO_BEHIND_PROXY` | `0` | Set to `1` when running behind a reverse proxy terminating TLS. |
| `DJANGO_SECURE_COOKIES` | `0` | Set to `1` when serving over HTTPS. |
| `DJANGO_CSRF_TRUSTED` | *(empty)* | Comma-separated origins, e.g. `https://example.com`. |
| `RUN_MIGRATIONS` | `1` | Apply migrations on container start. |
| `APP_TITLE` | *(default title)* | Application name shown in the interface. |
| `TZ` | `Asia/Riyadh` | Container timezone. |

Generate a secret key with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Never commit a real `.env` file — only `.env.example` belongs in version control.

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
