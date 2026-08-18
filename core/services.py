"""منطق الأعمال: حساب التكاليف وإحصاءات لوحة المعلومات.

نُقل حرفياً من النظام السابق (server/sqlite.ts) مع الحفاظ على نفس المعادلات:
    تكلفة الموظفين = ساعات عادية × أجر الساعة + ساعات إضافية × (أجر إضافي أو 1.5×)
    التكلفة الكلية = الموظفون + الفواتير + المصروفات
    نسبة الاستخدام = التكلفة الكلية ÷ الميزانية × 100
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from .models import (
    Expense, Invoice, Project, ProjectPayment, ProjectStatus, Worker, WorkHour,
)

ZERO = Decimal("0")
CENTS = Decimal("0.01")
TENTH = Decimal("0.1")
OVERTIME_MULTIPLIER = Decimal("1.5")

# حدود حالة الميزانية (كما في النظام السابق)
WARNING_THRESHOLD = Decimal("80")
OVER_BUDGET_THRESHOLD = Decimal("100")

STATUS_OVER = "تجاوز الميزانية"
STATUS_WARNING = "تحذير"
STATUS_OK = "ضمن الميزانية"

_money = DecimalField(max_digits=18, decimal_places=4)


def q2(value):
    """تقريب إلى منزلتين عشريتين."""
    return (value or ZERO).quantize(CENTS, rounding=ROUND_HALF_UP)


def q1(value):
    """تقريب إلى منزلة عشرية واحدة (النِسب)."""
    return (value or ZERO).quantize(TENTH, rounding=ROUND_HALF_UP)


def budget_status(usage_percent):
    if usage_percent > OVER_BUDGET_THRESHOLD:
        return STATUS_OVER
    if usage_percent > WARNING_THRESHOLD:
        return STATUS_WARNING
    return STATUS_OK


def budget_status_class(usage_percent):
    """لون التنبيه في الواجهة."""
    if usage_percent > OVER_BUDGET_THRESHOLD:
        return "error"
    if usage_percent > WARNING_THRESHOLD:
        return "warning"
    return "success"


@dataclass
class ProjectCost:
    project_id: int
    budget: Decimal = ZERO
    workers_cost: Decimal = ZERO
    invoices_cost: Decimal = ZERO
    expenses_cost: Decimal = ZERO
    total_cost: Decimal = ZERO
    remaining: Decimal = ZERO
    usage_percent: Decimal = ZERO
    payments_received: Decimal = ZERO
    budget_status: str = STATUS_OK

    @property
    def status_class(self):
        return budget_status_class(self.usage_percent)

    @property
    def usage_capped(self):
        """نسبة الاستخدام محدودة بـ100 لعرض شريط التقدم."""
        return min(self.usage_percent, Decimal("100"))


def _worker_cost_expression():
    """تعبير SQL لتكلفة سجل ساعات واحد."""
    return (
        F("regular_hours") * F("worker__hourly_rate")
        + F("overtime_hours") * Coalesce(
            F("worker__overtime_rate"),
            F("worker__hourly_rate") * Value(OVERTIME_MULTIPLIER),
            output_field=_money,
        )
    )


def _assemble(project_id, budget, workers, invoices, expenses, payments):
    workers_cost = q2(workers)
    invoices_cost = q2(invoices)
    expenses_cost = q2(expenses)
    total_cost = q2(workers_cost + invoices_cost + expenses_cost)
    budget = q2(budget)
    remaining = q2(budget - total_cost)
    usage = q1(total_cost / budget * 100) if budget > ZERO else ZERO
    return ProjectCost(
        project_id=project_id,
        budget=budget,
        workers_cost=workers_cost,
        invoices_cost=invoices_cost,
        expenses_cost=expenses_cost,
        total_cost=total_cost,
        remaining=remaining,
        usage_percent=usage,
        payments_received=q2(payments),
        budget_status=budget_status(usage),
    )


def calc_project_cost(project_id, budget=None):
    """حساب تكلفة مشروع واحد."""
    if budget is None:
        budget = (
            Project.objects.filter(pk=project_id)
            .values_list("budget", flat=True).first() or ZERO
        )

    workers = WorkHour.objects.filter(project_id=project_id).aggregate(
        total=Coalesce(Sum(_worker_cost_expression(), output_field=_money), Value(ZERO), output_field=_money)
    )["total"]

    invoices = Invoice.objects.filter(project_id=project_id).aggregate(
        total=Coalesce(Sum("total_amount"), Value(ZERO), output_field=_money)
    )["total"]

    expenses = Expense.objects.filter(project_id=project_id).aggregate(
        total=Coalesce(Sum("amount"), Value(ZERO), output_field=_money)
    )["total"]

    payments = ProjectPayment.objects.filter(
        project_id=project_id, status="confirmed"
    ).aggregate(
        total=Coalesce(Sum("amount"), Value(ZERO), output_field=_money)
    )["total"]

    return _assemble(project_id, budget, workers, invoices, expenses, payments)


def calc_project_costs_batch(project_ids):
    """حساب تكاليف عدة مشاريع باستعلامات مجمّعة (لتفادي N+1)."""
    ids = list(project_ids)
    if not ids:
        return {}

    budgets = dict(
        Project.objects.filter(pk__in=ids).values_list("id", "budget")
    )

    workers = dict(
        WorkHour.objects.filter(project_id__in=ids)
        .values_list("project_id")
        .annotate(total=Sum(_worker_cost_expression(), output_field=_money))
        .values_list("project_id", "total")
    )
    invoices = dict(
        Invoice.objects.filter(project_id__in=ids)
        .values_list("project_id")
        .annotate(total=Sum("total_amount"))
        .values_list("project_id", "total")
    )
    expenses = dict(
        Expense.objects.filter(project_id__in=ids)
        .values_list("project_id")
        .annotate(total=Sum("amount"))
        .values_list("project_id", "total")
    )
    payments = dict(
        ProjectPayment.objects.filter(project_id__in=ids, status="confirmed")
        .values_list("project_id")
        .annotate(total=Sum("amount"))
        .values_list("project_id", "total")
    )

    return {
        pid: _assemble(
            pid,
            budgets.get(pid, ZERO),
            workers.get(pid, ZERO),
            invoices.get(pid, ZERO),
            expenses.get(pid, ZERO),
            payments.get(pid, ZERO),
        )
        for pid in ids
    }


def attach_costs(projects):
    """إلحاق كائن التكلفة بكل مشروع في قائمة."""
    projects = list(projects)
    costs = calc_project_costs_batch([p.id for p in projects])
    for project in projects:
        project.cost = costs.get(project.id, ProjectCost(project_id=project.id))
    return projects


def dashboard_stats():
    """إحصاءات الصفحة الرئيسية."""
    status_counts = list(
        Project.objects.values("status").annotate(cnt=Count("id")).order_by("-cnt")
    )
    for row in status_counts:
        row["label"] = (
            ProjectStatus(row["status"]).label
            if row["status"] in ProjectStatus.values else row["status"]
        )

    type_counts = list(
        Project.objects.values("type__name").annotate(cnt=Count("id")).order_by("-cnt")
    )
    for row in type_counts:
        row["name"] = row["type__name"] or "غير محدد"

    totals = Project.objects.aggregate(
        total_budget=Coalesce(Sum("budget"), Value(ZERO), output_field=_money),
        total_projects=Count("id"),
    )
    invoice_totals = Invoice.objects.aggregate(
        total_invoices=Count("id"),
        paid=Coalesce(Sum("paid_amount"), Value(ZERO), output_field=_money),
        billed=Coalesce(Sum("total_amount"), Value(ZERO), output_field=_money),
    )

    # الترتيب حسب التكلفة الكلية المعروضة نفسها: نأخذ أعلى 15 حسب الفواتير
    # (وهي الجزء الأكبر من التكلفة) ثم نحسب تكلفتها الكاملة ونرتّبها.
    shortlist = list(
        Project.objects.annotate(
            invoices_cost=Coalesce(Sum("invoices__total_amount"), Value(ZERO), output_field=_money)
        ).order_by("-invoices_cost")[:15]
    )
    shortlist_costs = calc_project_costs_batch([p.id for p in shortlist])
    for project in shortlist:
        project.cost = shortlist_costs[project.id]
    top_projects = sorted(shortlist, key=lambda p: p.cost.total_cost, reverse=True)[:5]

    return {
        "status_counts": status_counts,
        "type_counts": type_counts,
        "total_budget": q2(totals["total_budget"]),
        "total_projects": totals["total_projects"],
        "active_projects": Project.objects.filter(status=ProjectStatus.IN_PROGRESS).count(),
        "total_workers": Worker.objects.filter(is_active=True).count(),
        "total_invoices": invoice_totals["total_invoices"],
        "paid_invoices": q2(invoice_totals["paid"]),
        "billed_invoices": q2(invoice_totals["billed"]),
        "top_projects": top_projects,
    }


def next_invoice_number():
    """توليد رقم الفاتورة التالي (INV-####) كما في النظام السابق."""
    import re

    last = (
        Invoice.objects.exclude(invoice_number__isnull=True)
        .exclude(invoice_number="")
        .order_by("-id")
        .values_list("invoice_number", flat=True)
        .first()
    )
    nxt = 1001
    if last:
        match = re.search(r"\d+", last)
        if match:
            nxt = int(match.group()) + 1
    return f"INV-{nxt}"


def worker_hours_with_costs(worker):
    """سجل ساعات موظف مع التكاليف وأسماء المشاريع."""
    rows = (
        WorkHour.objects.filter(worker=worker)
        .select_related("project", "worker")
        .order_by("-date", "-id")
    )
    total_cost = ZERO
    total_hours = ZERO
    for row in rows:
        total_cost += row.total_cost
        total_hours += (row.regular_hours or ZERO) + (row.overtime_hours or ZERO)
    return rows, q2(total_cost), q2(total_hours)


def project_hours_with_costs(project):
    """سجل ساعات مشروع مع التكاليف وأسماء الموظفين."""
    rows = (
        WorkHour.objects.filter(project=project)
        .select_related("worker")
        .order_by("-date", "worker__name")
    )
    return rows
