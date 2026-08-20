"""مسارات النظام."""

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.contrib.auth.models import Group

from .forms import (
    ExpenseForm, GroupForm, InvoiceForm, LoginForm, ProjectForm,
    ProjectPaymentForm, UserCreateForm, UserEditForm, WorkerForm, WorkHourForm,
)
from .models import (
    Expense, ExpenseCategory, Invoice, InvoiceStatus, Project, ProjectPayment,
    ProjectStatus, ProjectType, User, Worker, WorkHour,
)
from .permissions import PERMISSION_MODULES, home_route, require_perm
from .services import (
    ZERO, attach_costs, calc_project_cost, calc_project_costs_batch,
    dashboard_stats, next_invoice_number, project_hours_with_costs,
    worker_hours_with_costs,
)

PAGE_SIZE = 20


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _redirect_or_partial(request, fallback, partial=None, context=None):
    """يعيد جزءاً من الصفحة لطلبات HTMX أو يحوّل لصفحة كاملة."""
    if _is_htmx(request) and partial:
        return render(request, partial, context or {})
    return redirect(fallback)


# ===================== الدخول والخروج =====================
def login_view(request):
    if request.user.is_authenticated:
        return redirect(home_route(request.user))

    form = LoginForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        auth_login(request, form.get_user())
        user = form.get_user()
        messages.success(request, f"أهلاً بك، {user.full_name}")
        return redirect(home_route(user))

    return render(request, "auth/login.html", {"form": form})


@require_POST
@login_required
def logout_view(request):
    auth_logout(request)
    return redirect("login")


# ===================== لوحة المعلومات =====================
@login_required
@require_perm("view_dashboard")
def dashboard(request):
    stats = dashboard_stats()
    active = list(
        Project.objects.filter(status=ProjectStatus.IN_PROGRESS)
        .select_related("type").order_by("-id")[:8]
    )
    attach_costs(active)

    max_status = max((row["cnt"] for row in stats["status_counts"]), default=1) or 1
    max_type = max((row["cnt"] for row in stats["type_counts"]), default=1) or 1

    return render(request, "dashboard.html", {
        "stats": stats,
        "active_projects": active,
        "max_status": max_status,
        "max_type": max_type,
        "today": timezone.localdate(),
    })


# ===================== المشاريع =====================
def _project_queryset(request):
    qs = Project.objects.select_related("type")
    search = request.GET.get("search", "").strip()
    status = request.GET.get("status", "").strip()
    type_id = request.GET.get("type", "").strip()

    if search:
        qs = qs.filter(
            Q(name__icontains=search)
            | Q(client_name__icontains=search)
            | Q(code__icontains=search)
        )
    if status and status != "ALL":
        qs = qs.filter(status=status)
    if type_id and type_id != "ALL":
        qs = qs.filter(type_id=type_id)
    return qs.order_by("-id"), search, status, type_id


@login_required
@require_perm("view_projects")
def project_list(request):
    qs, search, status, type_id = _project_queryset(request)
    paginator = Paginator(qs, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    attach_costs(page.object_list)

    context = {
        "page_obj": page,
        "projects": page.object_list,
        "total": paginator.count,
        "search": search,
        "status": status or "ALL",
        "type_id": type_id or "ALL",
        "statuses": ProjectStatus.choices,
        "types": ProjectType.objects.all(),
    }
    if _is_htmx(request):
        return render(request, "projects/_table.html", context)
    return render(request, "projects/list.html", context)


@login_required
@require_perm("view_projects")
def project_detail(request, pk):
    project = get_object_or_404(Project.objects.select_related("type"), pk=pk)
    cost = calc_project_cost(project.pk, project.budget)

    invoices = project.invoices.order_by("-issue_date", "-id")
    payments = project.payments.order_by("-payment_date", "-id")
    expenses = project.expenses.select_related("category").order_by("-expense_date")
    hours = project_hours_with_costs(project)

    invoice_totals = invoices.aggregate(
        total=Coalesce(Sum("total_amount"), Value(ZERO)),
        paid=Coalesce(Sum("paid_amount"), Value(ZERO)),
    )

    return render(request, "projects/detail.html", {
        "project": project,
        "cost": cost,
        "invoices": invoices,
        "payments": payments,
        "expenses": expenses,
        "hours": hours,
        "invoice_totals": invoice_totals,
        "payment_form": ProjectPaymentForm(initial={"payment_date": timezone.localdate()}),
        "expense_form": ExpenseForm(initial={"expense_date": timezone.localdate()}),
        "invoice_form": InvoiceForm(initial={
            "project": project, "issue_date": timezone.localdate(),
            "status": InvoiceStatus.APPROVED,
        }),
        "next_invoice_number": next_invoice_number(),
    })


@login_required
@require_perm("add_project")
def project_create(request):
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        project = form.save()
        messages.success(request, f"تم إنشاء المشروع: {project.name}")
        return redirect("project_detail", pk=project.pk)
    return render(request, "projects/form.html", {"form": form, "mode": "create"})


@login_required
@require_perm("edit_project")
def project_edit(request, pk):
    project = get_object_or_404(Project, pk=pk)
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ التعديلات")
        return redirect("project_detail", pk=project.pk)
    return render(request, "projects/form.html", {
        "form": form, "mode": "edit", "project": project,
    })


@login_required
@require_perm("add_project_payment")
@require_POST
def project_add_payment(request, pk):
    project = get_object_or_404(Project, pk=pk)
    form = ProjectPaymentForm(request.POST)
    if form.is_valid():
        payment = form.save(commit=False)
        payment.project = project
        payment.save()
        messages.success(request, "تمت إضافة الدفعة")
    else:
        messages.error(request, "تعذّر حفظ الدفعة — تحقق من البيانات")
    return redirect("project_detail", pk=project.pk)


@login_required
@require_perm("add_project_expense")
@require_POST
def project_add_expense(request, pk):
    project = get_object_or_404(Project, pk=pk)
    form = ExpenseForm(request.POST)
    if form.is_valid():
        expense = form.save(commit=False)
        expense.project = project
        expense.save()
        messages.success(request, "تمت إضافة المصروف")
    else:
        messages.error(request, "تعذّر حفظ المصروف — تحقق من البيانات")
    return redirect("project_detail", pk=project.pk)


# ===================== الموظفون =====================
@login_required
@require_perm("view_workers")
def worker_list(request):
    search = request.GET.get("search", "").strip()
    active_only = request.GET.get("active") == "1"

    qs = Worker.objects.all()
    if search:
        qs = qs.filter(
            Q(name__icontains=search)
            | Q(id_number__icontains=search)
            | Q(position__icontains=search)
            | Q(employee_number__icontains=search)
        )
    if active_only:
        qs = qs.filter(is_active=True)
    qs = qs.order_by("name")

    context = {
        "workers": qs,
        "search": search,
        "active_only": active_only,
        "total": qs.count(),
        "active_count": Worker.objects.filter(is_active=True).count(),
    }
    if _is_htmx(request):
        return render(request, "workers/_table.html", context)
    return render(request, "workers/list.html", {
        **context,
        "form": WorkerForm(),
        "hours_form": WorkHourForm(initial={"date": timezone.localdate()}),
    })


@login_required
@require_perm("view_workers")
def worker_detail(request, pk):
    worker = get_object_or_404(Worker, pk=pk)
    rows, total_cost, total_hours = worker_hours_with_costs(worker)
    return render(request, "workers/detail.html", {
        "worker": worker,
        "hours": rows,
        "total_cost": total_cost,
        "total_hours": total_hours,
        "hours_form": WorkHourForm(
            initial={"date": timezone.localdate(), "worker": worker},
            lock_worker=worker,
        ),
    })


@login_required
@require_perm("add_worker")
def worker_create(request):
    form = WorkerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        worker = form.save()
        messages.success(request, f"تمت إضافة الموظف: {worker.name}")
        return redirect("worker_detail", pk=worker.pk)
    return render(request, "workers/form.html", {"form": form, "mode": "create"})


@login_required
@require_perm("edit_worker")
def worker_edit(request, pk):
    worker = get_object_or_404(Worker, pk=pk)
    form = WorkerForm(request.POST or None, instance=worker)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ بيانات الموظف")
        return redirect("worker_detail", pk=worker.pk)
    return render(request, "workers/form.html", {
        "form": form, "mode": "edit", "worker": worker,
    })


@login_required
@require_perm("toggle_worker")
@require_POST
def worker_toggle_active(request, pk):
    worker = get_object_or_404(Worker, pk=pk)
    worker.is_active = not worker.is_active
    worker.save(update_fields=["is_active", "updated_at"])
    messages.success(
        request, f"{worker.name}: {'تم التفعيل' if worker.is_active else 'تم الإيقاف'}"
    )
    return redirect(request.META.get("HTTP_REFERER") or reverse("worker_list"))


# ===================== ساعات العمل =====================
@login_required
@require_perm("add_work_hours")
@require_POST
def hours_add(request):
    form = WorkHourForm(request.POST)
    if form.is_valid():
        entry = form.save()
        messages.success(
            request, f"تم تسجيل {entry.regular_hours} ساعة لـ{entry.worker.name}"
        )
    else:
        messages.error(request, "; ".join(
            f"{error}" for errors in form.errors.values() for error in errors
        ) or "تعذّر تسجيل الساعات")
    return redirect(request.META.get("HTTP_REFERER") or reverse("worker_list"))


@login_required
@require_perm("delete_work_hours")
@require_POST
def hours_delete(request, pk):
    entry = get_object_or_404(WorkHour, pk=pk)
    worker_id = entry.worker_id
    entry.delete()
    messages.success(request, "تم حذف سجل الساعات")
    return redirect(request.META.get("HTTP_REFERER") or reverse("worker_detail", args=[worker_id]))


# ===================== صفحة الموظف =====================
@login_required
def my_hours(request):
    """صفحة تسجيل الساعات — متاحة لكل الأدوار، وهي الصفحة الوحيدة للموظف."""
    today = timezone.localdate()
    form = WorkHourForm(request.POST or None, initial={"date": today})

    if request.method == "POST":
        if form.is_valid():
            entry = form.save()
            messages.success(
                request,
                f"تم تسجيل {entry.regular_hours} ساعة عادية و{entry.overtime_hours} إضافية لـ{entry.worker.name}",
            )
            return redirect("my_hours")
        messages.error(request, "تعذّر التسجيل — راجع الحقول")

    recent = (
        WorkHour.objects.select_related("worker", "project")
        .order_by("-created_at", "-id")[:15]
    )
    today_count = WorkHour.objects.filter(date=today).count()

    return render(request, "hours/my_hours.html", {
        "form": form,
        "recent": recent,
        "today": today,
        "today_count": today_count,
        "active_projects": Project.objects.filter(
            status=ProjectStatus.IN_PROGRESS
        ).count(),
    })


# ===================== الفواتير =====================
@login_required
@require_perm("view_invoices")
def invoice_list(request):
    search = request.GET.get("search", "").strip()
    status = request.GET.get("status", "").strip()

    qs = Invoice.objects.select_related("project")
    if search:
        qs = qs.filter(Q(invoice_number__icontains=search) | Q(title__icontains=search))
    if status and status != "ALL":
        qs = qs.filter(status=status)
    qs = qs.order_by("-issue_date", "-id")

    totals = qs.aggregate(
        total=Coalesce(Sum("total_amount"), Value(ZERO)),
        paid=Coalesce(Sum("paid_amount"), Value(ZERO)),
    )
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page,
        "invoices": page.object_list,
        "total": paginator.count,
        "search": search,
        "status": status or "ALL",
        "statuses": InvoiceStatus.choices,
        "totals": totals,
        "remaining": (totals["total"] or ZERO) - (totals["paid"] or ZERO),
    }
    if _is_htmx(request):
        return render(request, "invoices/_table.html", context)
    return render(request, "invoices/list.html", context)


@login_required
@require_perm("add_invoice")
def invoice_create(request):
    initial = {"issue_date": timezone.localdate(), "status": InvoiceStatus.APPROVED}
    project_id = request.GET.get("project")
    if project_id:
        initial["project"] = project_id

    form = InvoiceForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        invoice = form.save(commit=False)
        if not invoice.invoice_number:
            invoice.invoice_number = next_invoice_number()
        invoice.save()
        messages.success(request, f"تم إنشاء الفاتورة {invoice.invoice_number}")
        if invoice.project_id:
            return redirect("project_detail", pk=invoice.project_id)
        return redirect("invoice_list")

    return render(request, "invoices/form.html", {
        "form": form, "mode": "create",
        "next_number": next_invoice_number(),
    })


@login_required
@require_perm("edit_invoice")
def invoice_edit(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    form = InvoiceForm(request.POST or None, instance=invoice)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ الفاتورة")
        return redirect("invoice_list")
    return render(request, "invoices/form.html", {
        "form": form, "mode": "edit", "invoice": invoice,
    })


@login_required
@require_perm("delete_invoice")
@require_POST
def invoice_delete(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    project_id = invoice.project_id
    number = invoice.invoice_number
    invoice.delete()
    messages.success(request, f"تم حذف الفاتورة {number}")
    if project_id and "project" in (request.META.get("HTTP_REFERER") or ""):
        return redirect("project_detail", pk=project_id)
    return redirect("invoice_list")


# ===================== المقارنة =====================
@login_required
@require_perm("view_compare")
def compare(request):
    ids = [int(v) for v in request.GET.getlist("ids") if v.isdigit()][:10]
    projects = []
    costs = {}
    totals = {}

    if len(ids) >= 2:
        projects = list(Project.objects.filter(pk__in=ids).select_related("type"))
        projects.sort(key=lambda p: ids.index(p.id))
        costs = calc_project_costs_batch([p.id for p in projects])
        for project in projects:
            project.cost = costs[project.id]
        totals = {
            "budget": sum(c.budget for c in costs.values()),
            "total_cost": sum(c.total_cost for c in costs.values()),
            "workers": sum(c.workers_cost for c in costs.values()),
            "invoices": sum(c.invoices_cost for c in costs.values()),
            "payments": sum(c.payments_received for c in costs.values()),
        }

    search = request.GET.get("q", "").strip()
    candidates = Project.objects.order_by("-id")
    if search:
        candidates = candidates.filter(
            Q(name__icontains=search) | Q(code__icontains=search)
            | Q(client_name__icontains=search)
        )
    candidates = candidates[:30]

    max_cost = max((c.total_cost for c in costs.values()), default=Decimal("1")) or Decimal("1")
    max_budget = max((c.budget for c in costs.values()), default=Decimal("1")) or Decimal("1")
    scale = max(max_cost, max_budget) or Decimal("1")

    context = {
        "selected": projects,
        "ids": ids,
        "candidates": candidates,
        "search": search,
        "totals": totals,
        "scale": scale,
    }
    if _is_htmx(request):
        return render(request, "compare/_picker.html", context)
    return render(request, "compare/index.html", context)


# ===================== المستخدمون =====================
@login_required
@require_perm("view_users")
def user_list(request):
    users = User.objects.prefetch_related("groups").order_by("id")
    groups = (
        Group.objects.annotate(
            members=Count("user", distinct=True),
            perm_count=Count("permissions", distinct=True),
        )
        .order_by("name")
    )
    return render(request, "users/list.html", {
        "users": users,
        "groups": groups,
        "create_form": UserCreateForm(),
    })


@login_required
@require_perm("add_user")
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"تم إنشاء المستخدم: {user.full_name}")
        return redirect("user_list")
    return render(request, "users/form.html", {"form": form, "mode": "create"})


@login_required
@require_perm("edit_user")
def user_edit(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    form = UserEditForm(request.POST or None, instance=user_obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم حفظ بيانات المستخدم")
        return redirect("user_list")
    return render(request, "users/form.html", {
        "form": form, "mode": "edit", "user_obj": user_obj,
    })


@login_required
@require_perm("delete_user")
@require_POST
def user_delete(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    if user_obj.pk == request.user.pk:
        messages.error(request, "لا يمكنك حذف حسابك الحالي")
    else:
        name = user_obj.full_name
        user_obj.delete()
        messages.success(request, f"تم حذف المستخدم: {name}")
    return redirect("user_list")


# ===================== المجموعات والصلاحيات =====================
def _group_permission_modules(request, group=None):
    """أقسام النظام وصلاحياتها مع تحديد المفعّل منها في المجموعة."""
    if request.method == "POST":
        selected = set(request.POST.getlist("permissions"))
    elif group and group.pk:
        selected = set(group.permissions.values_list("codename", flat=True))
    else:
        selected = set()
    return [
        {
            "label": label,
            "perms": [
                {"codename": codename, "name": name, "checked": codename in selected}
                for codename, name in perms
            ],
        }
        for label, perms in PERMISSION_MODULES
    ]


@login_required
@require_perm("view_groups")
def group_list(request):
    groups = (
        Group.objects.annotate(members=Count("user", distinct=True))
        .prefetch_related("permissions").order_by("name")
    )
    return render(request, "groups/list.html", {"groups": groups})


@login_required
@require_perm("add_group")
def group_create(request):
    form = GroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        group = form.save()
        messages.success(request, f"تم إنشاء المجموعة: {group.name}")
        return redirect("group_list")
    return render(request, "groups/form.html", {
        "form": form, "mode": "create",
        "modules": _group_permission_modules(request),
    })


@login_required
@require_perm("edit_group")
def group_edit(request, pk):
    group = get_object_or_404(Group, pk=pk)
    form = GroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"تم حفظ المجموعة: {group.name}")
        return redirect("group_list")
    return render(request, "groups/form.html", {
        "form": form, "mode": "edit", "group": group,
        "modules": _group_permission_modules(request, group),
    })


@login_required
@require_perm("delete_group")
@require_POST
def group_delete(request, pk):
    group = get_object_or_404(Group, pk=pk)
    if group.user_set.exists():
        messages.error(
            request,
            f"لا يمكن حذف المجموعة «{group.name}» — بها مستخدمون، انقلهم لمجموعة أخرى أولاً",
        )
    else:
        name = group.name
        group.delete()
        messages.success(request, f"تم حذف المجموعة: {name}")
    return redirect("group_list")
