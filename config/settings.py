"""Django settings for the project cost management system."""

import os
import sys
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env when present.
# override=False keeps real environment variables (Docker, the shell) ahead of
# the file, which is what lets migrations run through a different URL:
#   DATABASE_URL="$DIRECT_DATABASE_URL" python manage.py migrate
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:  # python-dotenv not installed — rely on the real environment
    pass

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", ""
).strip() or "dev-only-key-change-in-production-8f2b1c4d9e7a"
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"


def _env(name, default=None):
    """Value of an environment variable, treating an empty value as unset."""
    value = os.environ.get(name, "").strip()
    return value if value else default


def _env_list(name, default=""):
    """Comma-separated environment variable as a list, ignoring blanks."""
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# Hostnames this app answers to. Example:
#   DJANGO_ALLOWED_HOSTS=example.com,www.example.com,192.168.1.50
# Spaces after commas are fine, and a leading dot covers every subdomain:
#   .example.com  ->  app.example.com, api.example.com
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "*")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "core" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app_meta",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ===================== Database =====================
# The application runs on Supabase (PostgreSQL) and reads its connection
# details from the environment only. Two ways to configure it:
#
#   1) A ready-made URL (takes priority):
#        DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/postgres
#
#   2) Separate values — the URL is assembled from them:
#        SUPABASE_PROJECT_REF=abcdefghijklmnop
#        SUPABASE_DB_PASSWORD=...
#        SUPABASE_DB_REGION=ap-northeast-1        (default: us-east-1)
#
# There is no silent fallback to SQLite: missing details stop startup with a
# clear message. SQLite stays available for local work through USE_SQLITE=1.

def _supabase_url(port):
    """Build a Supabase connection URL from the separate variables, or None."""
    password = _env("SUPABASE_DB_PASSWORD")
    project_ref = _env("SUPABASE_PROJECT_REF")
    host = _env("SUPABASE_DB_HOST")
    if not password or not (project_ref or host):
        return None

    region = _env("SUPABASE_DB_REGION", "us-east-1")
    host = host or f"aws-0-{region}.pooler.supabase.com"
    # The pooler user carries the project reference: postgres.<project_ref>
    user = _env("SUPABASE_DB_USER") or (
        f"postgres.{project_ref}" if project_ref else "postgres"
    )
    name = _env("SUPABASE_DB_NAME", "postgres")
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{name}"
    )


# Port 6543 = transaction pooler (runtime), 5432 = session pooler (migrations).
DATABASE_URL = _env("DATABASE_URL") or _supabase_url(_env("SUPABASE_DB_PORT", "6543"))
DIRECT_DATABASE_URL = _env("DIRECT_DATABASE_URL") or _supabase_url("5432") or DATABASE_URL

# SQLite is for development and tests only: USE_SQLITE=1 turns it on, and it
# turns itself on while tests run so no test database is created on Supabase
# (USE_SQLITE=0 opts back out).
_use_sqlite = _env("USE_SQLITE")
USE_SQLITE = _use_sqlite == "1" if _use_sqlite else "test" in sys.argv[1:2]

if USE_SQLITE:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": _env("SQLITE_PATH", BASE_DIR / "factory.sqlite3"),
            "OPTIONS": {
                "transaction_mode": "IMMEDIATE",
                "init_command": "PRAGMA foreign_keys=ON;",
            },
        }
    }
elif DATABASE_URL:
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(_env("DB_CONN_MAX_AGE", "60")),
            conn_health_checks=True,
            ssl_require=_env("DB_SSL_REQUIRE", "1") == "1",
        )
    }
    # The pooler in transaction mode (port 6543) supports neither server-side
    # cursors nor prepared statements.
    if ":6543" in DATABASE_URL:
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
        DATABASES["default"]["OPTIONS"] = {
            **DATABASES["default"].get("OPTIONS", {}),
            "prepare_threshold": None,
        }
else:
    raise ImproperlyConfigured(
        "Supabase database connection details are not configured.\n"
        "Set DATABASE_URL, or SUPABASE_PROJECT_REF together with "
        "SUPABASE_DB_PASSWORD,\n"
        "in .env or in the container environment. See .env.example.\n"
        "To develop against local SQLite instead: USE_SQLITE=1"
    )

AUTH_USER_MODEL = "core.User"

# bcrypt first so passwords carried over from the legacy system (bcryptjs)
# keep working; they are upgraded to the default hasher on the first login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.BCryptPasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 6}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# ===================== Language and time =====================
# Timestamps are always STORED in UTC. USE_TZ=True keeps every DateTimeField
# timezone-aware and opens the database connection in UTC, so rows hold UTC
# regardless of where the server or the container clock sits.
#
# TIME_ZONE is the DISPLAY zone only — what templates, form initial values and
# timezone.localdate() convert to. It defaults to Asia/Riyadh and can be moved
# per deployment through DISPLAY_TIME_ZONE.
#
# A future per-user zone setting builds on this without touching stored data:
# keep this value as the fallback and call timezone.activate(<user zone>) for
# the duration of the request.
LANGUAGE_CODE = "ar"
TIME_ZONE = _env("DISPLAY_TIME_ZONE", "Asia/Riyadh")
USE_I18N = True
USE_TZ = True

try:
    ZoneInfo(TIME_ZONE)
except (ZoneInfoNotFoundError, ValueError) as exc:
    raise ImproperlyConfigured(
        f"DISPLAY_TIME_ZONE={TIME_ZONE!r} is not a known IANA time zone "
        "(expected something like 'Asia/Riyadh' or 'UTC')."
    ) from exc

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_AGE = 60 * 60 * 24 * 7  # one week
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# Stays False for plain HTTP on a local network.
# Set DJANGO_SECURE_COOKIES=1 when running behind HTTPS.
SESSION_COOKIE_SECURE = os.environ.get("DJANGO_SECURE_COOKIES", "0") == "1"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

if os.environ.get("DJANGO_BEHIND_PROXY", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

def _as_origins(host):
    """Turn a hostname into valid CSRF origins — Django requires a scheme."""
    if "://" in host:
        return [host]
    if host == "*":
        return []  # every host cannot be trusted for CSRF
    schemes = ["https"] if SESSION_COOKIE_SECURE else ["https", "http"]
    names = [f"*.{host.lstrip('.')}", host.lstrip(".")] if host.startswith(".") else [host]
    return [f"{scheme}://{name}" for name in names for scheme in schemes]


# CSRF origins come from DJANGO_CSRF_TRUSTED when set, and are otherwise
# derived from DJANGO_ALLOWED_HOSTS so domains are configured in one place.
_csrf_sources = _env_list("DJANGO_CSRF_TRUSTED") or ALLOWED_HOSTS
CSRF_TRUSTED_ORIGINS = list(
    dict.fromkeys(origin for host in _csrf_sources for origin in _as_origins(host))
)

# System name as shown in the interface (user-facing, stays Arabic by default)
APP_TITLE = _env("APP_TITLE", "نظام إدارة تكاليف المشاريع")
