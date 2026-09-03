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
from django.db import connection, transaction
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from urllib.parse import quote

from .models import Project, ReportJob, ReportJobStatus
from .permissions import require_perm
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
