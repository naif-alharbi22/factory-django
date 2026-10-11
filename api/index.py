"""WSGI entrypoint for Vercel preview deployments.

Vercel had nothing to run before this file existed. With no entrypoint and no
vercel.json, every push was built as a zero-config static project: the build
reported success because there was nothing to build, and the preview URL served
a repository with no index.html. This makes those previews run the real Django
app instead.

It is for previews only. Production is unchanged — the app ships as a container
image (see DEPLOY.md), and nothing here is imported by it.

The preview is deliberately disposable. Vercel gives each function instance a
read-only filesystem with a writable /tmp that disappears with the instance, so
the database lives there: SQLite, migrated and seeded on cold start, reset every
time a new instance spins up. That is the point — a preview is for clicking
through a branch, not for keeping anything.

Every setting below goes through the environment variables config/settings.py
already documents, and every one uses setdefault, so anything configured on the
Vercel project wins. Point DATABASE_URL at a real database and the ephemeral
SQLite — and the bootstrap that prepares it — step aside entirely.
"""

import os
import pathlib
import sys
import traceback

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# The one writable directory on a Vercel function
RUNTIME_DIR = pathlib.Path(os.environ.get("PREVIEW_RUNTIME_DIR", "/tmp/factory-preview"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# A real database configured on the project takes precedence; without one the
# preview runs on throwaway SQLite rather than failing to boot.
_EPHEMERAL_DB = not (
    os.environ.get("DATABASE_URL", "").strip()
    or os.environ.get("SUPABASE_DB_HOST", "").strip()
)
if _EPHEMERAL_DB:
    os.environ.setdefault("USE_SQLITE", "1")
    os.environ.setdefault("SQLITE_PATH", str(RUNTIME_DIR / "factory.sqlite3"))

# There is nowhere to collect static files into, so WhiteNoise serves them
# straight from the staticfiles finders: no collectstatic, no manifest to miss,
# and no compression pass holding up every cold start.
os.environ.setdefault("DJANGO_STATIC_FROM_FINDERS", "1")

# A leading dot covers every preview subdomain, and settings.py derives
# CSRF_TRUSTED_ORIGINS from it — which "*" cannot do, since no host can be
# trusted for CSRF. Without this the login form is rejected.
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", ".vercel.app")
# Vercel terminates TLS ahead of the function, so the scheme arrives in a header
os.environ.setdefault("DJANGO_BEHIND_PROXY", "1")
os.environ.setdefault("DJANGO_SECURE_COOKIES", "1")
# On by default so a broken page explains itself: this instance holds no real
# data, and a preview nobody can diagnose is not much of a preview. Set
# DJANGO_DEBUG=0 on the Vercel project to turn it off.
os.environ.setdefault("DJANGO_DEBUG", "1")

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

from django.core.wsgi import get_wsgi_application  # noqa: E402  (after the env)

application = get_wsgi_application()


def _seed_preview_user():
    """Create one sign-in for the preview, with the manager's permissions.

    Only ever runs against the ephemeral database, and only when it holds no
    users at all, so it cannot touch or overwrite a real account.
    """
    from django.contrib.auth.models import Group

    from core.models import User

    if User.objects.exists():
        return
    user = User.objects.create_user(
        os.environ.get("PREVIEW_ADMIN_USERNAME", "admin"),
        os.environ.get("PREVIEW_ADMIN_PASSWORD", "preview-admin"),
        full_name="Preview Admin",
    )
    # The group the migrations create; it carries every permission
    manager = Group.objects.filter(name="مدير").first()
    if manager:
        user.groups.add(manager)


def _bootstrap():
    """Prepare the throwaway database, once per instance.

    The marker file makes it a no-op for every request after the first. It is
    skipped entirely when a real database is configured: migrating somebody's
    production data from a preview function is not this file's business.
    """
    if not _EPHEMERAL_DB:
        return
    marker = RUNTIME_DIR / ".bootstrapped"
    if marker.exists():
        return

    from django.core.management import call_command

    call_command("migrate", verbosity=0, interactive=False)
    _seed_preview_user()
    marker.touch()


try:
    _bootstrap()
except Exception:  # noqa: BLE001 — the reason has to reach whoever opened the URL
    _FAILURE = traceback.format_exc()
    sys.stderr.write(f"preview bootstrap failed\n{_FAILURE}")

    def application(environ, start_response):  # noqa: F811
        """Serve the bootstrap failure instead of an opaque 500.

        A preview that dies on cold start is otherwise a blank page, and the
        logs behind it are not always reachable by whoever is looking.
        """
        body = (
            "Preview bootstrap failed — this deployment could not prepare its "
            "throwaway database.\n\n" + _FAILURE
        ).encode()
        start_response(
            "500 Internal Server Error",
            [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
        )
        return [body]


# Vercel's Python runtime looks for `app`
app = application
