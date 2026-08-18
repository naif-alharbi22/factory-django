# ===================== المرحلة 1: بناء ملف التنسيق =====================
FROM node:22-slim AS css

WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY assets/ ./assets/
COPY core/templates/ ./core/templates/
RUN npx tailwindcss -i ./assets/app.css -o ./app.css --minify


# ===================== المرحلة 2: التشغيل =====================
FROM python:3.12-slim AS runtime

# متطلبات WeasyPrint لتوليد تقارير PDF بالعربية + الخطوط العربية
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi8 \
        fonts-noto-core \
        fonts-noto-cjk \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# كود التطبيق
COPY manage.py entrypoint.sh ./
COPY config/ ./config/
COPY core/ ./core/

# ملف التنسيق المبني في المرحلة الأولى
COPY --from=css /build/app.css ./core/static/css/app.css

RUN chmod +x entrypoint.sh \
    && mkdir -p /app/data /app/media /app/staticfiles

# مستخدم غير جذري
RUN useradd --create-home --uid 10001 factory \
    && chown -R factory:factory /app
USER factory

# جمع الملفات الثابتة أثناء البناء (لا يحتاج قاعدة بيانات)
RUN python manage.py collectstatic --noinput --clear

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/login/ -o /dev/null || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
