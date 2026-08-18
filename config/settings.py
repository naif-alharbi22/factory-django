"""إعدادات نظام إدارة تكاليف المشاريع - Django."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# تحميل متغيرات البيئة من ملف .env إن وُجد.
# override=False حتى تبقى متغيرات البيئة الحقيقية (مثل Docker) هي الأعلى أولوية،
# وهو ما يسمح بتشغيل الترحيلات عبر رابط مختلف:
#   DATABASE_URL="$DIRECT_DATABASE_URL" python manage.py migrate
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:  # python-dotenv غير مثبّت — نعتمد على بيئة النظام فقط
    pass

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-only-key-change-in-production-8f2b1c4d9e7a"
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

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

# ===================== قاعدة البيانات =====================
# عند ضبط DATABASE_URL يُستخدم PostgreSQL (Supabase)،
# وبدونه يعود النظام إلى SQLite المحلي للتطوير.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "60")),
            conn_health_checks=True,
            ssl_require=os.environ.get("DB_SSL_REQUIRE", "1") == "1",
        )
    }
    # مجمّع الاتصالات في وضع المعاملات (منفذ 6543) لا يدعم
    # المؤشرات على الخادم ولا الجُمل المُجهّزة مسبقاً.
    if ":6543" in DATABASE_URL:
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
        DATABASES["default"]["OPTIONS"] = {
            **DATABASES["default"].get("OPTIONS", {}),
            "prepare_threshold": None,
        }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("SQLITE_PATH", BASE_DIR / "factory.sqlite3"),
            "OPTIONS": {
                "transaction_mode": "IMMEDIATE",
                "init_command": "PRAGMA foreign_keys=ON;",
            },
        }
    }

AUTH_USER_MODEL = "core.User"

# bcrypt أولاً حتى تعمل كلمات المرور المنقولة من النظام القديم (bcryptjs)،
# ثم تُرقّى تلقائياً إلى الخوارزمية الافتراضية عند أول تسجيل دخول ناجح.
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

LANGUAGE_CODE = "ar"
TIME_ZONE = "Asia/Riyadh"
USE_I18N = True
USE_TZ = True

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

SESSION_COOKIE_AGE = 60 * 60 * 24 * 7  # أسبوع
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# ملاحظة: يبقى False للتشغيل عبر HTTP على الشبكة المحلية.
# فعّل DJANGO_SECURE_COOKIES=1 عند التشغيل خلف HTTPS.
SESSION_COOKIE_SECURE = os.environ.get("DJANGO_SECURE_COOKIES", "0") == "1"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

if os.environ.get("DJANGO_BEHIND_PROXY", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("DJANGO_CSRF_TRUSTED", "").split(",") if o
]

# اسم النظام كما يظهر في الواجهة
APP_TITLE = os.environ.get("APP_TITLE", "نظام إدارة تكاليف المشاريع")
