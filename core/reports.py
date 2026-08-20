"""تقارير PDF بالعربية عبر WeasyPrint (بديل Puppeteer في النظام السابق)."""

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify
from urllib.parse import quote

from .models import Project
from .permissions import require_perm
from .services import ZERO, calc_project_cost, project_hours_with_costs


@login_required
@require_perm("view_reports")
def project_report_pdf(request, pk):
    """تقرير المشروع الكامل بصيغة PDF."""
    from weasyprint import HTML  # استيراد مؤجل ليبقى بدء الخادم سريعاً

    project = get_object_or_404(Project.objects.select_related("type"), pk=pk)
    cost = calc_project_cost(project.pk, project.budget)

    invoices = list(project.invoices.order_by("-issue_date", "-id"))
    payments = list(project.payments.order_by("-payment_date", "-id"))
    expenses = list(project.expenses.select_related("category").order_by("-expense_date"))
    hours = list(project_hours_with_costs(project))

    invoice_totals = project.invoices.aggregate(
        total=Coalesce(Sum("total_amount"), Value(ZERO)),
        paid=Coalesce(Sum("paid_amount"), Value(ZERO)),
    )

    html = render_to_string("reports/project.html", {
        "project": project,
        "cost": cost,
        "invoices": invoices,
        "payments": payments,
        "expenses": expenses,
        "hours": hours,
        "invoice_totals": invoice_totals,
        "report_date": timezone.localdate(),
        "generated_by": request.user,
    }, request=request)

    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()

    safe_name = "".join(
        ch for ch in (project.name or "report")
        if ch.isalnum() or "؀" <= ch <= "ۿ" or ch in " -_"
    ).strip().replace(" ", "_") or "report"
    filename = quote(f"{safe_name}_تقرير.pdf")

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
    return response
