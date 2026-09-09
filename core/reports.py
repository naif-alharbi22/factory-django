"""Arabic PDF reports through WeasyPrint (the legacy system used Puppeteer).

Building a report can take several seconds for a project with a lot of data
(invoices, hours, expenses), so it does not run inside the request: pressing
"generate report" creates a ReportJob row and starts the build on a separate
background thread. The request returns immediately and the gunicorn worker
stays free for other users, while the page polls the job status over HTMX
until the download link appears.
"""

import logging
import threading
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from urllib.parse import quote

from .activity import log_create
from .models import (
    ActivityAction, ActivityLog, ActivityTarget, Project, ReportJob,
    ReportJobStatus, User,
)
from .permissions import require_any_perm, require_perm
from .services import ZERO, calc_project_cost, project_hours_with_costs

logger = logging.getLogger(__name__)

# A job that stays "running" for longer than this is treated as stuck (a server
# restart mid-build, for example) so its owner can retry instead of waiting.
STALE_AFTER = timedelta(minutes=10)


def _safe_filename(project):
    safe_name = "".join(
        ch for ch in (project.name or "report")
        if ch.isalnum() or "؀" <= ch <= "ۿ" or ch in " -_"
    ).strip().replace(" ", "_") or "report"
    return f"{safe_name}_تقرير.pdf"


def _render_report_pdf(project, generated_by):
    """Build the complete report PDF. Called from the background thread."""
    from weasyprint import HTML  # deferred import to keep server start-up fast

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
        "generated_by": generated_by,
    })

    return HTML(string=html).write_pdf()


def _run_report_job(job_id):
    """Runs on its own thread, so no server worker is held during the build."""
    try:
        try:
            job = ReportJob.objects.select_related("project", "requested_by").get(pk=job_id)
        except ReportJob.DoesNotExist:
            return

        job.status = ReportJobStatus.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])

        try:
            pdf_bytes = _render_report_pdf(job.project, job.requested_by)
        except Exception as exc:  # record build failures on the job, not as a 500
            logger.exception("Report generation failed for project #%s", job.project_id)
            job.status = ReportJobStatus.FAILED
            job.error_message = str(exc) or exc.__class__.__name__
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
            return

        job.file.save(_safe_filename(job.project), ContentFile(pdf_bytes), save=False)
        job.status = ReportJobStatus.DONE
        job.finished_at = timezone.now()
        job.save(update_fields=["file", "status", "finished_at"])
    finally:
        # This thread is done, so close its database connection explicitly —
        # it matters behind a connection pooler (Supabase/pgbouncer).
        connection.close()


@login_required
@require_perm("view_reports")
def project_report_generate(request, pk):
    """Queue a new report and build it in the background; the browser waits for
    nothing."""
    project = get_object_or_404(Project, pk=pk)

    active = ReportJob.objects.filter(
        project=project, status__in=[ReportJobStatus.QUEUED, ReportJobStatus.RUNNING],
    ).first()
    if active is not None:
        messages.info(request, "هناك طلب تقرير قيد التنفيذ بالفعل لهذا المشروع")
    else:
        job = ReportJob.objects.create(project=project, requested_by=request.user)
        # Start the thread only once the job row is committed to the database
        transaction.on_commit(
            lambda: threading.Thread(
                target=_run_report_job, args=(job.id,), daemon=True,
            ).start()
        )
        log_create(request, job, f"طلب تقرير المشروع «{project.name}»")
        messages.success(request, "جارٍ إنشاء التقرير — سيظهر رابط التحميل هنا خلال لحظات")

    return redirect("project_detail", pk=project.pk)


@login_required
@require_perm("view_reports")
def project_report_status(request, pk):
    """HTMX fragment showing this project's latest report job; polled while it
    runs."""
    project = get_object_or_404(Project, pk=pk)
    job = ReportJob.objects.filter(project=project).order_by("-id").first()

    if (
        job is not None
        and job.status == ReportJobStatus.RUNNING
        and job.started_at
        and timezone.now() - job.started_at > STALE_AFTER
    ):
        job.status = ReportJobStatus.FAILED
        job.error_message = "انتهت المهلة — يبدو أن الخادم أُعيد تشغيله أثناء التوليد"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])

    return render(request, "reports/_status.html", {"project": project, "job": job})


@login_required
@require_perm("view_reports")
def project_report_download(request, pk, job_id):
    """Download a finished report — builds nothing, just serves the saved file."""
    project = get_object_or_404(Project, pk=pk)
    job = get_object_or_404(ReportJob, pk=job_id, project=project)
    if job.status != ReportJobStatus.DONE or not job.file:
        raise Http404("التقرير غير جاهز بعد")

    filename = quote(_safe_filename(project))
    response = FileResponse(job.file.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
    return response


# ===================== The reports section =====================
ACTIVITY_PAGE_SIZE = 25


@login_required
@require_any_perm("view_activity", "view_reports")
def reports_index(request):
    """The reports landing page; each card appears only for those who may open
    it."""
    return render(request, "reports/index.html", {
        "activity_count": (
            ActivityLog.objects.count()
            if request.user.has_perm("core.view_activity") else 0
        ),
    })


def _activity_queryset(request):
    """The activity list with the page's filters applied; every filter is
    optional and they combine."""
    qs = ActivityLog.objects.select_related("actor", "project")

    search = request.GET.get("search", "").strip()
    actor_id = request.GET.get("actor", "").strip()
    target_type = request.GET.get("target", "").strip()
    action = request.GET.get("action", "").strip()
    project_id = request.GET.get("project", "").strip()
    date_from = parse_date(request.GET.get("date_from", "").strip())
    date_to = parse_date(request.GET.get("date_to", "").strip())

    if search:
        qs = qs.filter(
            Q(description__icontains=search)
            | Q(actor_name__icontains=search)
            | Q(target_label__icontains=search)
            | Q(project_name__icontains=search)
        )
    if actor_id.isdigit():
        qs = qs.filter(actor_id=int(actor_id))
    if target_type and target_type != "ALL":
        qs = qs.filter(target_type=target_type)
    if action and action != "ALL":
        qs = qs.filter(action=action)
    if project_id.isdigit():
        qs = qs.filter(project_id=int(project_id))
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    return qs, {
        "search": search,
        "actor_id": actor_id,
        "target_type": target_type or "ALL",
        "action": action or "ALL",
        "project_id": project_id,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
    }


@login_required
@require_perm("view_activity")
def activity_log(request):
    """Every action taken in the system: when, by whom, what, and on which
    project."""
    qs, filters = _activity_queryset(request)
    paginator = Paginator(qs, ACTIVITY_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    context = {
        **filters,
        "page_obj": page,
        "activities": page.object_list,
        "total": paginator.count,
        "actors": User.objects.order_by("full_name"),
        "projects": Project.objects.order_by("-id"),
        "targets": ActivityTarget.choices,
        "actions": ActivityAction.choices,
    }
    if request.headers.get("HX-Request") == "true":
        return render(request, "reports/_activity_table.html", context)
    return render(request, "reports/activity.html", context)
