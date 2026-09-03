"""Import legacy system data (Node/SQLite) into the Django database.

Usage:
    python manage.py import_legacy --source /path/to/factory_management.db
"""

import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone

from core.models import (
    Expense, ExpenseCategory, Invoice, InvoiceItem, InvoicePayment,
    Project, ProjectPayment, ProjectStatus, ProjectType, User,
    Worker, WorkHour,
)

VALID_PROJECT_STATUS = set(ProjectStatus.values)


def dec(value, default="0"):
    """Safely turn any numeric value coming from SQLite into a Decimal."""
    if value is None or value == "":
        value = default
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def dec_rate(value, allow_none=False):
    """Hourly rates carry over at full precision, without rounding."""
    if value is None or value == "":
        return None if allow_none else Decimal("0")
    try:
        return Decimal(str(value)).quantize(Decimal("0.0000000001"))
    except (InvalidOperation, ValueError):
        return None if allow_none else Decimal("0")


def dec_or_none(value):
    if value is None or value == "":
        return None
    return dec(value)


def as_date(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = parse_date(text[:10])
    return parsed


def as_datetime(value):
    if not value:
        return timezone.now()
    text = str(value).strip().replace("T", " ")
    parsed = parse_datetime(text)
    if parsed is None:
        d = as_date(text)
        if d is None:
            return timezone.now()
        parsed = timezone.datetime(d.year, d.month, d.day)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_default_timezone())
    return parsed


def clean(value, limit=None):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] if limit else text


# Columns that must exist. Some were added by the legacy system's own
# migrations and may live in the -wal file, not yet merged into the main one.
REQUIRED_COLUMNS = {
    "workers": ["employee_number", "insurance_amount"],
    "projects": ["client_email", "address"],
    "invoices": ["description", "notes"],
}


def verify_schema(con, source):
    """Check the schema is complete before starting the import."""
    missing = []
    for table, columns in REQUIRED_COLUMNS.items():
        try:
            present = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            missing.append(f"{table} (the table itself is missing)")
            continue
        for column in columns:
            if column not in present:
                missing.append(f"{table}.{column}")

    if missing:
        wal = Path(f"{source}-wal")
        hint = ""
        if not wal.exists():
            hint = (
                "\n\nMost likely cause: the write-ahead log file "
                f"'{wal.name}' is not next to the database.\n"
                "Copy or mount the whole directory, not just the .db file — "
                "in Docker:\n"
                "    -v /path/to/server:/legacy:ro"
            )
        raise CommandError(
            "The legacy database schema is incomplete. Missing columns: "
            + ", ".join(missing) + hint
        )


@contextmanager
def open_legacy(source):
    """Open the legacy database through a temporary copy.

    The legacy database runs in WAL mode, and opening it read-only can fail
    because SQLite needs write access to the -wal and -shm files. Copying
    guarantees every committed row is read (including what has not been merged
    into the main file yet) without touching the original at all.
    """
    with tempfile.TemporaryDirectory(prefix="factory-legacy-") as tmp:
        target = Path(tmp) / "legacy.db"
        shutil.copy2(source, target)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{source}{suffix}")
            if sidecar.exists():
                shutil.copy2(sidecar, f"{target}{suffix}")

        con = sqlite3.connect(target)
        con.row_factory = sqlite3.Row
        try:
            verify_schema(con, source)
            yield con
        finally:
            con.close()


class Command(BaseCommand):
    help = "Import legacy system data from a SQLite file"

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Path to the legacy database file")
        parser.add_argument(
            "--flush", action="store_true",
            help="Delete existing data before importing",
        )

    def handle(self, *args, **options):
        source = Path(options["source"]).expanduser().resolve()
        if not source.exists():
            raise CommandError(f"File not found: {source}")

        with open_legacy(source) as con, transaction.atomic():
            if options["flush"]:
                self.stdout.write("Deleting existing data...")
                for model in (WorkHour, InvoicePayment, InvoiceItem, Invoice,
                              ProjectPayment, Expense, Project, Worker,
                              ProjectType, ExpenseCategory):
                    model.objects.all().delete()
                User.objects.filter(is_superuser=False).delete()

            counts = {}
            counts["Project types"] = self.import_project_types(con)
            counts["Expense categories"] = self.import_expense_categories(con)
            counts["Users"] = self.import_users(con)
            counts["Projects"] = self.import_projects(con)
            counts["Workers"] = self.import_workers(con)
            counts["Work hours"] = self.import_work_hours(con)
            counts["Invoices"] = self.import_invoices(con)
            counts["Invoice items"] = self.import_invoice_items(con)
            counts["Invoice payments"] = self.import_invoice_payments(con)
            counts["Project payments"] = self.import_project_payments(con)
            counts["Expenses"] = self.import_expenses(con)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Import finished:"))
        for label, n in counts.items():
            self.stdout.write(f"  {label:<22} {n}")

    # ---------- Reference data ----------
    def import_project_types(self, con):
        rows = con.execute("SELECT * FROM project_types").fetchall()
        ProjectType.objects.bulk_create(
            [ProjectType(id=r["id"], name=clean(r["name"], 100) or "-",
                         description=clean(r["description"]))
             for r in rows],
            ignore_conflicts=True,
        )
        return len(rows)

    def import_expense_categories(self, con):
        rows = con.execute("SELECT * FROM expense_categories").fetchall()
        ExpenseCategory.objects.bulk_create(
            [ExpenseCategory(id=r["id"], name=clean(r["name"], 100) or "-",
                             description=clean(r["description"]))
             for r in rows],
            ignore_conflicts=True,
        )
        return len(rows)

    # ---------- Users ----------
    def import_users(self, con):
        """Move users across, keeping their passwords (bcrypt from bcryptjs)."""
        try:
            rows = con.execute("SELECT * FROM app_users").fetchall()
        except sqlite3.OperationalError:
            return 0

        # Legacy roles map onto the default groups (group names are data)
        role_groups = {
            role: Group.objects.get_or_create(name=name)[0]
            for role, name in (("admin", "مدير"), ("accountant", "محاسب"), ("employee", "موظف"))
        }

        created = 0
        for r in rows:
            role = r["role"] if r["role"] in role_groups else "employee"
            legacy_hash = clean(r["password_hash"]) or ""
            # Django's format for bcrypt passwords: "bcrypt$<hash>"
            if legacy_hash.startswith("$2"):
                password = f"bcrypt${legacy_hash}"
            else:
                password = "!"  # unusable password

            user, made = User.objects.update_or_create(
                username=clean(r["username"], 50),
                defaults={
                    "full_name": clean(r["full_name"], 100) or clean(r["username"], 100),
                    "is_active": bool(r["is_active"]),
                    "is_staff": role == "admin",
                    "is_superuser": role == "admin",
                    "password": password,
                    "created_at": as_datetime(r["created_at"]),
                },
            )
            user.groups.set([role_groups[role]])
            if r["last_login"]:
                User.objects.filter(pk=user.pk).update(last_login=as_datetime(r["last_login"]))
            created += 1
        return created

    # ---------- Projects ----------
    def import_projects(self, con):
        rows = con.execute("SELECT * FROM projects").fetchall()
        type_ids = set(ProjectType.objects.values_list("id", flat=True))
        objs = []
        for r in rows:
            status = r["status"] if r["status"] in VALID_PROJECT_STATUS else ProjectStatus.IN_PROGRESS
            objs.append(Project(
                id=r["id"],
                name=clean(r["name"], 200) or "-",
                code=clean(r["code"], 30),
                client_name=clean(r["client_name"], 150),
                client_phone=clean(r["client_phone"], 30),
                client_email=clean(r["client_email"], 120),
                address=clean(r["address"]),
                description=clean(r["description"]),
                type_id=r["type_id"] if r["type_id"] in type_ids else None,
                status=status,
                budget=dec(r["budget"]),
                start_date=as_date(r["start_date"]),
                estimated_end_date=as_date(r["estimated_end_date"]),
                actual_end_date=as_date(r["actual_end_date"]),
                created_at=as_datetime(r["created_at"]),
            ))
        Project.objects.bulk_create(objs, batch_size=500)
        return len(objs)

    # ---------- Workers ----------
    def import_workers(self, con):
        rows = con.execute("SELECT * FROM workers").fetchall()
        objs = []
        for r in rows:
            objs.append(Worker(
                id=r["id"],
                name=clean(r["name"], 150) or "-",
                employee_number=clean(r["employee_number"], 30),
                id_number=clean(r["id_number"], 30),
                nationality=clean(r["nationality"], 60),
                phone=clean(r["phone"], 30),
                address=clean(r["address"]),
                position=clean(r["position"], 100),
                base_salary=dec(r["base_salary"]),
                hourly_rate=dec_rate(r["hourly_rate"]),
                overtime_rate=dec_rate(r["overtime_rate"], allow_none=True),
                insurance_amount=dec(r["insurance_amount"], "1600"),
                hire_date=as_date(r["hire_date"]),
                end_date=as_date(r["end_date"]),
                is_active=bool(r["is_active"]) if r["is_active"] is not None else True,
                created_at=as_datetime(r["created_at"]),
            ))
        Worker.objects.bulk_create(objs, batch_size=500)
        return len(objs)

    def import_work_hours(self, con):
        rows = con.execute("SELECT * FROM work_hours").fetchall()
        worker_ids = set(Worker.objects.values_list("id", flat=True))
        project_ids = set(Project.objects.values_list("id", flat=True))
        objs = []
        skipped = 0
        for r in rows:
            if r["worker_id"] not in worker_ids:
                skipped += 1
                continue
            date = as_date(r["date"])
            if date is None:
                skipped += 1
                continue
            objs.append(WorkHour(
                id=r["id"],
                worker_id=r["worker_id"],
                project_id=r["project_id"] if r["project_id"] in project_ids else None,
                date=date,
                regular_hours=dec(r["regular_hours"]),
                overtime_hours=dec(r["overtime_hours"]),
                notes=clean(r["notes"]),
                created_at=as_datetime(r["created_at"]),
            ))
        WorkHour.objects.bulk_create(objs, batch_size=1000)
        if skipped:
            self.stdout.write(self.style.WARNING(
                f"  Skipped {skipped} work-hour rows (unknown worker or invalid date)"
            ))
        return len(objs)

    # ---------- Invoices ----------
    def import_invoices(self, con):
        rows = con.execute("SELECT * FROM invoices").fetchall()
        project_ids = set(Project.objects.values_list("id", flat=True))
        objs = []
        skipped = 0
        for r in rows:
            issue = as_date(r["issue_date"])
            if issue is None:
                skipped += 1
                continue
            objs.append(Invoice(
                id=r["id"],
                invoice_number=clean(r["invoice_number"], 30),
                project_id=r["project_id"] if r["project_id"] in project_ids else None,
                title=clean(r["title"], 200),
                description=clean(r["description"]),
                issue_date=issue,
                due_date=as_date(r["due_date"]),
                total_amount=dec(r["total_amount"]),
                paid_amount=dec(r["paid_amount"]),
                tax_percentage=dec(r["tax_percentage"]),
                status=clean(r["status"], 20) or "APPROVED",
                notes=clean(r["notes"]),
                created_at=as_datetime(r["created_at"]),
            ))
        Invoice.objects.bulk_create(objs, batch_size=500)
        if skipped:
            self.stdout.write(self.style.WARNING(f"  Skipped {skipped} invoices with no issue date"))
        return len(objs)

    def import_invoice_items(self, con):
        rows = con.execute("SELECT * FROM invoice_items").fetchall()
        invoice_ids = set(Invoice.objects.values_list("id", flat=True))
        objs = [
            InvoiceItem(
                id=r["id"], invoice_id=r["invoice_id"],
                description=clean(r["description"], 255) or "-",
                quantity=dec(r["quantity"], "1"),
                unit=clean(r["unit"], 20) or "قطعة",
                unit_price=dec(r["unit_price"]),
                total_price=dec(r["total_price"]),
                created_at=as_datetime(r["created_at"]),
            )
            for r in rows if r["invoice_id"] in invoice_ids
        ]
        InvoiceItem.objects.bulk_create(objs, batch_size=500)
        return len(objs)

    def import_invoice_payments(self, con):
        rows = con.execute("SELECT * FROM invoice_payments").fetchall()
        invoice_ids = set(Invoice.objects.values_list("id", flat=True))
        objs = []
        for r in rows:
            if r["invoice_id"] not in invoice_ids:
                continue
            date = as_date(r["payment_date"])
            if date is None:
                continue
            objs.append(InvoicePayment(
                id=r["id"], invoice_id=r["invoice_id"],
                amount=dec(r["amount"]), payment_date=date,
                payment_method=clean(r["payment_method"], 50),
                reference_number=clean(r["reference_number"], 100),
                notes=clean(r["notes"]),
                created_at=as_datetime(r["created_at"]),
            ))
        InvoicePayment.objects.bulk_create(objs, batch_size=1000)
        return len(objs)

    def import_project_payments(self, con):
        rows = con.execute("SELECT * FROM project_payments").fetchall()
        project_ids = set(Project.objects.values_list("id", flat=True))
        objs = []
        for r in rows:
            if r["project_id"] not in project_ids:
                continue
            date = as_date(r["payment_date"])
            if date is None:
                continue
            objs.append(ProjectPayment(
                id=r["id"], project_id=r["project_id"],
                amount=dec(r["amount"]), payment_date=date,
                payment_method=clean(r["payment_method"], 50) or "cash",
                reference_number=clean(r["reference_number"], 100),
                receipt_number=clean(r["receipt_number"], 100),
                description=clean(r["description"]),
                notes=clean(r["notes"]),
                status=clean(r["status"], 20) or "confirmed",
                created_at=as_datetime(r["created_at"]),
            ))
        ProjectPayment.objects.bulk_create(objs, batch_size=500)
        return len(objs)

    def import_expenses(self, con):
        rows = con.execute("SELECT * FROM expenses").fetchall()
        project_ids = set(Project.objects.values_list("id", flat=True))
        category_ids = set(ExpenseCategory.objects.values_list("id", flat=True))
        objs = []
        for r in rows:
            date = as_date(r["expense_date"])
            if date is None:
                continue
            objs.append(Expense(
                id=r["id"],
                project_id=r["project_id"] if r["project_id"] in project_ids else None,
                title=clean(r["title"], 150) or "-",
                description=clean(r["description"]),
                amount=dec(r["amount"]),
                expense_date=date,
                category_id=r["category_id"] if r["category_id"] in category_ids else None,
                is_approved=bool(r["is_approved"]),
                created_at=as_datetime(r["created_at"]),
            ))
        Expense.objects.bulk_create(objs, batch_size=500)
        return len(objs)
