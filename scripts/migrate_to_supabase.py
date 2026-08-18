#!/usr/bin/env python
"""تنفيذ ترحيل قاعدة البيانات إلى Supabase والتحقق منه.

لا ينفّذ هذا السكربت أي عملية تدميرية: لا حذف، لا TRUNCATE، لا DROP.
إن وجد بيانات موجودة مسبقاً في الوجهة فإنه يتوقف ويطلب قراراً.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
os.chdir(BASE)
PY = str(BASE / ".venv" / "bin" / "python")

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def mask(text):
    """إخفاء كلمة المرور في أي نص قبل طباعته."""
    return re.sub(r"(://[^:/@]+:)[^@]*(@)", r"\1****\2", str(text))


def head(title):
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def run(args, env=None, check=True, quiet=False):
    merged = {**os.environ, **(env or {})}
    proc = subprocess.run(args, env=merged, capture_output=True, text=True)
    out = (proc.stdout + proc.stderr).strip()
    if not quiet and out:
        print(mask(out))
    if check and proc.returncode != 0:
        print(f"{RED}فشل الأمر: {' '.join(args[:3])}...{RESET}")
        sys.exit(1)
    return proc


def load_env():
    env = {}
    for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def main():
    env_file = load_env()
    direct = env_file.get("DIRECT_DATABASE_URL", "")
    runtime = env_file.get("DATABASE_URL", "")

    if not direct or "://" not in direct or re.match(r"postgresql://[^:]+:@", direct):
        print(f"{RED}DIRECT_DATABASE_URL غير مضبوط. نفّذ: ./scripts/set-db-password.sh{RESET}")
        sys.exit(1)

    # الترحيلات والاستيراد عبر مجمّع الجلسات (5432)
    migrate_env = {"DATABASE_URL": direct}

    # ---------- 1) التحقق من الاتصال ----------
    head("1) التحقق من الاتصال بـ Supabase")
    probe = f'''
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.db import connection
with connection.cursor() as c:
    c.execute("SELECT version(), current_database(), inet_server_addr()::text")
    version, dbname, addr = c.fetchone()
print("  engine :", connection.vendor)
print("  host   :", connection.settings_dict["HOST"])
print("  port   :", connection.settings_dict["PORT"])
print("  db     :", dbname)
print("  server :", version.split(" on ")[0])
'''
    run([PY, "-c", probe], env=migrate_env)

    # ---------- 2) فحص Django ----------
    head("2) python manage.py check")
    run([PY, "manage.py", "check"], env=migrate_env)

    # ---------- 3) الترحيلات ----------
    head("3) python manage.py migrate  (عبر مجمّع الجلسات 5432)")
    run([PY, "manage.py", "showmigrations", "--plan"], env=migrate_env, quiet=True)
    run([PY, "manage.py", "migrate", "--noinput"], env=migrate_env)

    # ---------- 4) التأكد أن الوجهة فارغة قبل الاستيراد ----------
    head("4) فحص الوجهة قبل استيراد البيانات")
    guard = '''
import django, os, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from core.models import (Project, Worker, WorkHour, Invoice, InvoiceItem,
                         InvoicePayment, ProjectPayment, Expense,
                         ProjectType, ExpenseCategory, User)
from django.contrib.sessions.models import Session
existing = {m.__name__: m.objects.count() for m in
            (Project, Worker, WorkHour, Invoice, InvoiceItem, InvoicePayment,
             ProjectPayment, Expense, ProjectType, ExpenseCategory, Session)}
non_empty = {k: v for k, v in existing.items() if v}
# المستخدمون يُنشأون فقط عبر الاستيراد؛ الجداول التطبيقية يجب أن تكون فارغة
print("  جداول غير فارغة:", non_empty or "لا يوجد — الوجهة نظيفة")
sys.exit(2 if non_empty else 0)
'''
    proc = run([PY, "-c", guard], env=migrate_env, check=False)
    if proc.returncode == 2:
        print(f"{YELLOW}الوجهة تحتوي بيانات مسبقاً — توقّف السكربت لتفادي التعارض.{RESET}")
        print("لن يُحذف أي شيء. راجع الحالة ثم قرّر.")
        sys.exit(2)

    # ---------- 5) استيراد البيانات ----------
    head("5) استيراد البيانات (loaddata)")
    stamp = (BASE / "backups" / ".latest-stamp").read_text().strip()
    fixture = f"backups/data-{stamp}.json"
    print(f"  الملف: {fixture}")
    run([PY, "manage.py", "loaddata", fixture], env=migrate_env)

    # ---------- 6) ضبط المتسلسلات ----------
    head("6) ضبط متسلسلات PostgreSQL")
    seq = '''
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.apps import apps
from django.core.management.color import no_style
from django.db import connection
models = list(apps.get_app_config("core").get_models())
sql = connection.ops.sequence_reset_sql(no_style(), models)
with connection.cursor() as c:
    for statement in sql:
        c.execute(statement)
print(f"  أُعيد ضبط {len(sql)} متسلسلة")

# التحقق: أعلى معرّف مقابل القيمة التالية للمتسلسلة
from core.models import Project, Worker, Invoice, WorkHour
for model in (Project, Worker, Invoice, WorkHour):
    table = model._meta.db_table
    with connection.cursor() as c:
        c.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
        top = c.fetchone()[0]
        c.execute(f"SELECT last_value FROM pg_get_serial_sequence('{table}', 'id')")
        last = c.fetchone()[0]
    status = "OK" if last >= top else "!! أقل من أعلى معرّف"
    print(f"  {table:<24} max_id={top:<6} sequence={last:<6} {status}")
'''
    run([PY, "-c", seq], env=migrate_env)

    # ---------- 7) مقارنة أعداد الصفوف ----------
    head("7) مقارنة أعداد الصفوف: SQLite مقابل Supabase")
    compare = f'''
import django, os, sqlite3, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from core.models import (User, ProjectType, ExpenseCategory, Project, Worker,
                         WorkHour, Invoice, InvoiceItem, InvoicePayment,
                         ProjectPayment, Expense)
from django.contrib.sessions.models import Session
from django.contrib.auth.models import Group

MODELS = [("Users", User, "core_user"), ("Groups", Group, "auth_group"),
          ("Sessions", Session, "django_session"),
          ("ProjectTypes", ProjectType, "core_projecttype"),
          ("ExpenseCategories", ExpenseCategory, "core_expensecategory"),
          ("Projects", Project, "core_project"), ("Workers", Worker, "core_worker"),
          ("WorkHours", WorkHour, "core_workhour"), ("Invoices", Invoice, "core_invoice"),
          ("InvoiceItems", InvoiceItem, "core_invoiceitem"),
          ("InvoicePayments", InvoicePayment, "core_invoicepayment"),
          ("ProjectPayments", ProjectPayment, "core_projectpayment"),
          ("Expenses", Expense, "core_expense")]

lite = sqlite3.connect("file:factory.sqlite3?mode=ro", uri=True)
print(f"  {{'Table':<20}}{{'SQLite':>9}}{{'Supabase':>11}}   Status")
print("  " + "-" * 52)
bad = 0
for label, model, table in MODELS:
    try:
        local = lite.execute(f"SELECT COUNT(*) FROM {{table}}").fetchone()[0]
    except sqlite3.OperationalError:
        local = 0
    remote = model.objects.count()
    ok = local == remote
    bad += 0 if ok else 1
    print(f"  {{label:<20}}{{local:>9}}{{remote:>11}}   {{'OK' if ok else 'MISMATCH'}}")
lite.close()
sys.exit(1 if bad else 0)
'''
    proc = run([PY, "-c", compare], env=migrate_env, check=False)
    counts_ok = proc.returncode == 0

    # ---------- 8) فحص العلاقات والحسابات ----------
    head("8) فحص العلاقات والحسابات على Supabase")
    rel = '''
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from core.models import Project, WorkHour, Invoice, User
from core.services import calc_project_cost

p = Project.objects.filter(invoices__isnull=False).distinct().order_by("-id").first()
cost = calc_project_cost(p.pk, p.budget)
print(f"  مشروع #{p.pk} {p.name[:30]}")
print(f"    الفواتير المرتبطة : {p.invoices.count()}")
print(f"    ساعات العمل       : {p.work_hours.count()}")
print(f"    الدفعات           : {p.payments.count()}")
print(f"    التكلفة المحسوبة  : {cost.total_cost}  (استخدام {cost.usage_percent}%)")

orphan_hours = WorkHour.objects.filter(worker__isnull=True).count()
orphan_inv = Invoice.objects.filter(project__isnull=True).count()
print(f"  سجلات ساعات بلا موظف : {orphan_hours}")
print(f"  فواتير بلا مشروع     : {orphan_inv}")
print(f"  المستخدمون: " + ", ".join(f"{u.username}({u.role})" for u in User.objects.all()))
'''
    run([PY, "-c", rel], env=migrate_env)

    # ---------- 9) اختبار مجمّع المعاملات (وضع التشغيل) ----------
    head("9) اختبار مجمّع المعاملات (6543) — وضع تشغيل التطبيق")
    if runtime and ":6543" in runtime:
        pooler = '''
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.conf import settings
from django.db import connection
from core.models import Project
print("  DISABLE_SERVER_SIDE_CURSORS:", settings.DATABASES["default"].get("DISABLE_SERVER_SIDE_CURSORS"))
print("  port:", connection.settings_dict["PORT"])
print("  استعلام تجريبي — عدد المشاريع:", Project.objects.count())
print("  أول 3 مشاريع:", list(Project.objects.order_by("-id").values_list("name", flat=True)[:3]))
'''
        run([PY, "-c", pooler], env={"DATABASE_URL": runtime})
    else:
        print(f"  {YELLOW}DATABASE_URL لا يستخدم المنفذ 6543 — تخطّي{RESET}")

    head("النتيجة")
    if counts_ok:
        print(f"{GREEN}  اكتمل الترحيل والتحقق بنجاح.{RESET}")
    else:
        print(f"{RED}  اكتمل الترحيل مع اختلاف في أعداد الصفوف — راجع الجدول أعلاه.{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
