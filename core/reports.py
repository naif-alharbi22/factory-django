"""تقارير PDF بالعربية عبر WeasyPrint (بديل Puppeteer في النظام السابق).

توليد التقرير قد يستغرق عدة ثوانٍ لمشروع فيه بيانات كثيرة (فواتير/ساعات/
مصروفات) — لذا لا يُنفَّذ داخل نفس الطلب: الضغط على «إنشاء تقرير» ينشئ
سجل ReportJob ويُشغّل التوليد في خيط خلفي منفصل، فيرجع الطلب فوراً ويبقى
عامل الخادم (gunicorn worker) حراً لخدمة بقية المستخدمين. الصفحة تستطلع
حالة الطلب دورياً عبر HTMX حتى يظهر رابط التحميل.
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

# أي طلب "جارٍ الإنشاء" لفترة أطول من هذا يُعتبر عالقاً (مثلاً بسبب إعادة
# تشغيل الخادم أثناء التوليد) — يُتاح لصاحبه إعادة المحاولة بدل الانتظار للأبد.
STALE_AFTER = timedelta(minutes=10)


def _safe_filename(project):
    safe_name = "".join(
        ch for ch in (project.name or "report")
        if ch.isalnum() or "؀" <= ch <= "ۿ" or ch in " -_"
    ).strip().replace(" ", "_") or "report"
    return f"{safe_name}_تقرير.pdf"


def _render_report_pdf(project, generated_by):
    """يبني ملف الـ PDF الكامل لتقرير المشروع. يُستدعى من الخيط الخلفي."""
    from weasyprint import HTML  # استيراد مؤجل ليبقى بدء الخادم سريعاً

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
    """ينفَّذ في خيط منفصل عن خيط الطلب — لا يحجز عامل الخادم أثناء التوليد."""
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
        except Exception as exc:  # أي خطأ في التوليد يُسجَّل على الطلب لا كخطأ خادم للمستخدم
            logger.exception("فشل إنشاء تقرير المشروع #%s", job.project_id)
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
        # هذا الخيط لن يُستخدم مجدداً — إغلاق اتصاله بقاعدة البيانات صراحة
        # مهم خصوصاً خلف مجمّع اتصالات (Supabase/pgbouncer).
        connection.close()


@login_required
@require_perm("view_reports")
def project_report_generate(request, pk):
    """يطلب إنشاء تقرير جديد وينفّذه في الخلفية — لا ينتظر المتصفح توليد الملف."""
    project = get_object_or_404(Project, pk=pk)

    active = ReportJob.objects.filter(
        project=project, status__in=[ReportJobStatus.QUEUED, ReportJobStatus.RUNNING],
    ).first()
    if active is not None:
        messages.info(request, "هناك طلب تقرير قيد التنفيذ بالفعل لهذا المشروع")
    else:
        job = ReportJob.objects.create(project=project, requested_by=request.user)
        # يبدأ الخيط فقط بعد تأكيد حفظ سجل الطلب في قاعدة البيانات
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
    """جزء HTMX يعرض حالة آخر طلب تقرير لهذا المشروع — يُستطلَع دورياً أثناء التنفيذ."""
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
    """تنزيل ملف تقرير جاهز — لا يُنشئ شيئاً، فقط يخدم الملف المحفوظ مسبقاً."""
    project = get_object_or_404(Project, pk=pk)
    job = get_object_or_404(ReportJob, pk=job_id, project=project)
    if job.status != ReportJobStatus.DONE or not job.file:
        raise Http404("التقرير غير جاهز بعد")

    filename = quote(_safe_filename(project))
    response = FileResponse(job.file.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
    return response
