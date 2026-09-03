"""Models for the project cost management system.

Field labels (verbose_name) and choice labels are user-facing, so they stay
Arabic like the rest of the interface.
"""

from decimal import Decimal

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from . import permissions as permissions_registry

MONEY = {"max_digits": 14, "decimal_places": 2}
HOURS = {"max_digits": 7, "decimal_places": 2}
# Hourly rates come out of a division (e.g. 2700 / 208 hours), so they are
# stored at high precision to match the legacy system's costs to the halala.
RATE = {"max_digits": 18, "decimal_places": 10}


# ===================== Users and permissions =====================
class AppPermission(models.Model):
    """Table-less model that carries the module permissions in auth.Permission.

    The real registry lives in core/permissions.py (PERMISSION_MODULES); the
    auth.Permission rows are created automatically after migrations
    (post_migrate).
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = permissions_registry.ALL_PERMISSIONS
        verbose_name = "صلاحية النظام"
        verbose_name_plural = "صلاحيات النظام"


class UserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra):
        if not username:
            raise ValueError("اسم المستخدم مطلوب")
        user = self.model(username=username, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("full_name", username)
        return self.create_user(username, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    username = models.CharField("اسم المستخدم", max_length=50, unique=True)
    full_name = models.CharField("الاسم الكامل", max_length=100)
    is_active = models.BooleanField("نشط", default=True)
    is_staff = models.BooleanField("موظف إداري", default=False)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "مستخدم"
        verbose_name_plural = "المستخدمون"
        ordering = ["id"]

    def __str__(self):
        return f"{self.full_name} ({self.username})"

    # Application permissions are derived from the user's group (auth.Group)
    @property
    def group(self):
        """The user's group — every user is assigned to exactly one."""
        return self.groups.first()

    @property
    def group_name(self):
        group = self.group
        if group:
            return group.name
        return "مدير النظام" if self.is_superuser else "بدون مجموعة"

    @property
    def is_employee_only(self):
        """No module permissions at all — the timesheet is their only page."""
        if self.is_superuser:
            return False
        return not self.get_all_permissions()


# ===================== Reference data =====================
class ProjectType(models.Model):
    name = models.CharField("النوع", max_length=100)
    description = models.TextField("الوصف", blank=True, null=True)

    class Meta:
        verbose_name = "نوع مشروع"
        verbose_name_plural = "أنواع المشاريع"
        ordering = ["id"]

    def __str__(self):
        return self.name


class ExpenseCategory(models.Model):
    name = models.CharField("التصنيف", max_length=100)
    description = models.TextField("الوصف", blank=True, null=True)

    class Meta:
        verbose_name = "تصنيف مصروف"
        verbose_name_plural = "تصنيفات المصروفات"
        ordering = ["name"]

    def __str__(self):
        return self.name


# ===================== Projects =====================
class ProjectStatus(models.TextChoices):
    PLANNING = "PLANNING", "تخطيط"
    APPROVAL = "APPROVAL", "قيد الموافقة"
    IN_PROGRESS = "IN_PROGRESS", "قيد التنفيذ"
    INSPECTION = "INSPECTION", "فحص"
    ON_HOLD = "ON_HOLD", "متوقف"
    CLOSED = "CLOSED", "مغلق"


class Project(models.Model):
    name = models.CharField("اسم المشروع", max_length=200)
    code = models.CharField("الكود", max_length=30, blank=True, null=True)
    client_name = models.CharField("العميل", max_length=150, blank=True, null=True)
    client_phone = models.CharField("هاتف العميل", max_length=30, blank=True, null=True)
    client_email = models.CharField("بريد العميل", max_length=120, blank=True, null=True)
    address = models.TextField("العنوان", blank=True, null=True)
    description = models.TextField("الوصف", blank=True, null=True)
    type = models.ForeignKey(
        ProjectType, verbose_name="النوع", on_delete=models.SET_NULL,
        blank=True, null=True, related_name="projects",
    )
    status = models.CharField(
        "الحالة", max_length=20, choices=ProjectStatus.choices,
        default=ProjectStatus.IN_PROGRESS,
    )
    budget = models.DecimalField("الميزانية", default=Decimal("0"), **MONEY)
    start_date = models.DateField("تاريخ البداية", blank=True, null=True)
    estimated_end_date = models.DateField("النهاية المتوقعة", blank=True, null=True)
    actual_end_date = models.DateField("النهاية الفعلية", blank=True, null=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "مشروع"
        verbose_name_plural = "المشاريع"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return self.name

    @property
    def status_label(self):
        return ProjectStatus(self.status).label if self.status in ProjectStatus.values else self.status


# ===================== Workers =====================
class Worker(models.Model):
    name = models.CharField("الاسم", max_length=150)
    employee_number = models.CharField("الرقم الوظيفي", max_length=30, blank=True, null=True)
    id_number = models.CharField("رقم الهوية", max_length=30, blank=True, null=True)
    nationality = models.CharField("الجنسية", max_length=60, blank=True, null=True)
    phone = models.CharField("الهاتف", max_length=30, blank=True, null=True)
    address = models.TextField("العنوان", blank=True, null=True)
    position = models.CharField("الوظيفة", max_length=100, blank=True, null=True)
    base_salary = models.DecimalField("الراتب الأساسي", default=Decimal("0"), **MONEY)
    hourly_rate = models.DecimalField("أجر الساعة", default=Decimal("0"), **RATE)
    overtime_rate = models.DecimalField(
        "أجر الساعة الإضافية", blank=True, null=True, **RATE,
    )
    insurance_amount = models.DecimalField("التأمين", default=Decimal("1600"), **MONEY)
    hire_date = models.DateField("تاريخ التعيين", blank=True, null=True)
    end_date = models.DateField("تاريخ نهاية الخدمة", blank=True, null=True)
    is_active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "موظف"
        verbose_name_plural = "الموظفون"
        ordering = ["name"]
        indexes = [models.Index(fields=["is_active"])]

    def __str__(self):
        return self.name

    @property
    def effective_overtime_rate(self):
        """Overtime rate; when unset it is 1.5x the hourly rate."""
        if self.overtime_rate is not None:
            return self.overtime_rate
        return (self.hourly_rate or Decimal("0")) * Decimal("1.5")

    @property
    def salary_with_allowances(self):
        """Salary including allowances (a flat 1000, as in the legacy system)."""
        return (self.base_salary or Decimal("0")) + Decimal("1000")


class WorkHour(models.Model):
    worker = models.ForeignKey(
        Worker, verbose_name="الموظف", on_delete=models.CASCADE, related_name="work_hours",
    )
    project = models.ForeignKey(
        Project, verbose_name="المشروع", on_delete=models.SET_NULL,
        blank=True, null=True, related_name="work_hours",
    )
    date = models.DateField("التاريخ")
    regular_hours = models.DecimalField("ساعات عادية", default=Decimal("0"), **HOURS)
    overtime_hours = models.DecimalField("ساعات إضافية", default=Decimal("0"), **HOURS)
    notes = models.TextField("ملاحظات", blank=True, null=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "ساعات عمل"
        verbose_name_plural = "ساعات العمل"
        ordering = ["-date", "worker__name"]
        indexes = [
            models.Index(fields=["project"]),
            models.Index(fields=["worker"]),
            models.Index(fields=["date"]),
        ]

    def __str__(self):
        return f"{self.worker.name} - {self.date}"

    @property
    def regular_cost(self):
        return (self.regular_hours or Decimal("0")) * (self.worker.hourly_rate or Decimal("0"))

    @property
    def overtime_cost(self):
        return (self.overtime_hours or Decimal("0")) * self.worker.effective_overtime_rate

    @property
    def total_cost(self):
        return self.regular_cost + self.overtime_cost


# ===================== Invoices =====================
class InvoiceStatus(models.TextChoices):
    DRAFT = "DRAFT", "مسودة"
    APPROVED = "APPROVED", "معتمدة"
    PAID = "PAID", "مدفوعة"
    CANCELLED = "CANCELLED", "ملغاة"


class Invoice(models.Model):
    invoice_number = models.CharField("رقم الفاتورة", max_length=30, blank=True, null=True)
    project = models.ForeignKey(
        Project, verbose_name="المشروع", on_delete=models.CASCADE,
        blank=True, null=True, related_name="invoices",
    )
    title = models.CharField("البيان", max_length=200, blank=True, null=True)
    description = models.TextField("الوصف", blank=True, null=True)
    issue_date = models.DateField("تاريخ الإصدار")
    due_date = models.DateField("تاريخ الاستحقاق", blank=True, null=True)
    total_amount = models.DecimalField("الإجمالي", default=Decimal("0"), **MONEY)
    paid_amount = models.DecimalField("المدفوع", default=Decimal("0"), **MONEY)
    tax_percentage = models.DecimalField(
        "الضريبة %", default=Decimal("0"), max_digits=5, decimal_places=2,
    )
    status = models.CharField(
        "الحالة", max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.APPROVED,
    )
    notes = models.TextField("ملاحظات", blank=True, null=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "فاتورة"
        verbose_name_plural = "الفواتير"
        ordering = ["-issue_date", "-id"]
        indexes = [
            models.Index(fields=["project"]),
            models.Index(fields=["status"]),
            models.Index(fields=["invoice_number"]),
        ]

    def __str__(self):
        return self.invoice_number or f"فاتورة #{self.pk}"

    @property
    def remaining(self):
        return (self.total_amount or Decimal("0")) - (self.paid_amount or Decimal("0"))


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(
        Invoice, verbose_name="الفاتورة", on_delete=models.CASCADE, related_name="items",
    )
    description = models.CharField("البند", max_length=255)
    quantity = models.DecimalField("الكمية", default=Decimal("1"), **HOURS)
    unit = models.CharField("الوحدة", max_length=20, default="قطعة", blank=True, null=True)
    unit_price = models.DecimalField("سعر الوحدة", default=Decimal("0"), **MONEY)
    total_price = models.DecimalField("الإجمالي", default=Decimal("0"), **MONEY)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "بند فاتورة"
        verbose_name_plural = "بنود الفواتير"
        ordering = ["id"]

    def __str__(self):
        return self.description


class InvoicePayment(models.Model):
    invoice = models.ForeignKey(
        Invoice, verbose_name="الفاتورة", on_delete=models.CASCADE, related_name="payments",
    )
    amount = models.DecimalField("المبلغ", default=Decimal("0"), **MONEY)
    payment_date = models.DateField("تاريخ الدفع")
    payment_method = models.CharField("طريقة الدفع", max_length=50, blank=True, null=True)
    reference_number = models.CharField("رقم المرجع", max_length=100, blank=True, null=True)
    notes = models.TextField("ملاحظات", blank=True, null=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)

    class Meta:
        verbose_name = "دفعة فاتورة"
        verbose_name_plural = "دفعات الفواتير"
        ordering = ["-payment_date", "-id"]
        indexes = [models.Index(fields=["invoice"])]

    def __str__(self):
        return f"{self.amount}"


# ===================== Payments and expenses =====================
class PaymentStatus(models.TextChoices):
    CONFIRMED = "confirmed", "مؤكدة"
    PENDING = "pending", "معلّقة"
    CANCELLED = "cancelled", "ملغاة"


class ProjectPayment(models.Model):
    project = models.ForeignKey(
        Project, verbose_name="المشروع", on_delete=models.CASCADE, related_name="payments",
    )
    amount = models.DecimalField("المبلغ", default=Decimal("0"), **MONEY)
    payment_date = models.DateField("تاريخ الدفعة")
    payment_method = models.CharField("طريقة الدفع", max_length=50, default="cash", blank=True, null=True)
    reference_number = models.CharField("رقم المرجع", max_length=100, blank=True, null=True)
    receipt_number = models.CharField("رقم السند", max_length=100, blank=True, null=True)
    description = models.TextField("البيان", blank=True, null=True)
    notes = models.TextField("ملاحظات", blank=True, null=True)
    status = models.CharField(
        "الحالة", max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.CONFIRMED,
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "دفعة مشروع"
        verbose_name_plural = "دفعات المشاريع"
        ordering = ["-payment_date", "-id"]
        indexes = [models.Index(fields=["project", "status"])]

    def __str__(self):
        return f"{self.amount}"


class Expense(models.Model):
    project = models.ForeignKey(
        Project, verbose_name="المشروع", on_delete=models.CASCADE,
        blank=True, null=True, related_name="expenses",
    )
    title = models.CharField("البيان", max_length=150)
    description = models.TextField("الوصف", blank=True, null=True)
    amount = models.DecimalField("المبلغ", default=Decimal("0"), **MONEY)
    expense_date = models.DateField("تاريخ المصروف")
    category = models.ForeignKey(
        ExpenseCategory, verbose_name="التصنيف", on_delete=models.SET_NULL,
        blank=True, null=True, related_name="expenses",
    )
    is_approved = models.BooleanField("معتمد", default=False)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "مصروف"
        verbose_name_plural = "المصروفات"
        ordering = ["-expense_date", "-id"]
        indexes = [models.Index(fields=["project"])]

    def __str__(self):
        return self.title

# ===================== Manufacturing =====================
# The manufacturing workflow is a fully configurable engine: phases and stages
# are database rows managed from the interface, and no logic depends on their
# names or on how many there are.
class ManufacturingPhase(models.Model):
    name = models.CharField("الاسم", max_length=100)
    description = models.TextField("الوصف", blank=True, null=True)
    order = models.PositiveIntegerField("الترتيب", default=0)
    is_active = models.BooleanField("نشطة", default=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "مرحلة تصنيع"
        verbose_name_plural = "مراحل التصنيع"
        ordering = ["order", "id"]

    def __str__(self):
        return self.name


class ManufacturingStage(models.Model):
    phase = models.ForeignKey(
        ManufacturingPhase, verbose_name="المرحلة", on_delete=models.CASCADE,
        related_name="stages",
    )
    name = models.CharField("الاسم", max_length=100)
    description = models.TextField("الوصف", blank=True, null=True)
    order = models.PositiveIntegerField("الترتيب", default=0)
    is_active = models.BooleanField("نشطة", default=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "خطوة تصنيع"
        verbose_name_plural = "خطوات التصنيع"
        ordering = ["phase__order", "order", "id"]

    def __str__(self):
        return self.name


class StageStatus(models.TextChoices):
    NOT_STARTED = "not_started", "لم يبدأ"
    IN_PROGRESS = "in_progress", "قيد التنفيذ"
    DONE = "done", "مكتمل"


class Manufacturing(models.Model):
    """A project's manufacturing tracker — a snapshot of the stages configured
    when it was created."""

    project = models.OneToOneField(
        Project, verbose_name="المشروع", on_delete=models.CASCADE,
        related_name="manufacturing",
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "متابعة تصنيع"
        verbose_name_plural = "متابعات التصنيع"
        ordering = ["-id"]

    def __str__(self):
        return f"تصنيع {self.project.name}"

    @classmethod
    def create_for_project(cls, project):
        """Create a tracker with one record per active stage in the current setup."""
        stages = list(
            ManufacturingStage.objects.filter(is_active=True, phase__is_active=True)
            .order_by("phase__order", "order", "id")
        )
        if not stages:
            raise ValueError("لا توجد خطوات تصنيع نشطة — هيّئ مراحل التصنيع أولاً")
        manufacturing = cls.objects.create(project=project)
        ManufacturingStageRecord.objects.bulk_create([
            ManufacturingStageRecord(manufacturing=manufacturing, stage=stage)
            for stage in stages
        ])
        return manufacturing

    def ordered_records(self):
        """Every record in workflow order, derived from phase and stage order."""
        return (
            self.records.select_related("stage", "stage__phase")
            .order_by("stage__phase__order", "stage__order", "stage__id")
        )

    def active_records(self):
        """The records that count towards the workflow; disabled stages drop out."""
        return [
            record for record in self.ordered_records()
            if record.is_active_step
        ]

    @property
    def progress_percent(self):
        """Progress across active stages — it follows configuration changes."""
        records = self.active_records()
        if not records:
            return 0
        done = sum(1 for r in records if r.status == StageStatus.DONE)
        return round(done * 100 / len(records))

    @property
    def current_record(self):
        """The first active stage that is not finished."""
        for record in self.active_records():
            if record.status != StageStatus.DONE:
                return record
        return None

    @property
    def is_complete(self):
        records = self.active_records()
        return bool(records) and all(r.status == StageStatus.DONE for r in records)

    def phases_with_records(self):
        """Records grouped by phase for display, in workflow order."""
        groups = []
        for record in self.ordered_records():
            phase = record.stage.phase
            if not groups or groups[-1]["phase"].pk != phase.pk:
                groups.append({"phase": phase, "records": []})
            groups[-1]["records"].append(record)
        return groups


class ManufacturingStageRecord(models.Model):
    """The state of one stage inside a project's tracker.

    The link to the stage is a protected FK (PROTECT): renaming a stage shows
    up here automatically, disabling it leaves records untouched, and deleting
    one is refused while any historical record still references it.
    """

    manufacturing = models.ForeignKey(
        Manufacturing, verbose_name="المتابعة", on_delete=models.CASCADE,
        related_name="records",
    )
    stage = models.ForeignKey(
        ManufacturingStage, verbose_name="الخطوة", on_delete=models.PROTECT,
        related_name="records",
    )
    status = models.CharField(
        "الحالة", max_length=20, choices=StageStatus.choices,
        default=StageStatus.NOT_STARTED,
    )
    started_at = models.DateTimeField("تاريخ البدء", blank=True, null=True)
    completed_at = models.DateTimeField("تاريخ الإكمال", blank=True, null=True)
    notes = models.TextField("ملاحظات", blank=True, null=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "سجل خطوة تصنيع"
        verbose_name_plural = "سجلات خطوات التصنيع"
        constraints = [
            models.UniqueConstraint(
                fields=["manufacturing", "stage"], name="uniq_manufacturing_stage",
            ),
        ]

    def __str__(self):
        return f"{self.stage.name} — {self.get_status_display()}"

    @property
    def is_active_step(self):
        return self.stage.is_active and self.stage.phase.is_active

    @property
    def blocked_by(self):
        """The first unfinished active stage before this one; disabled stages
        never block progress."""
        for record in self.manufacturing.ordered_records():
            if record.pk == self.pk:
                return None
            if record.is_active_step and record.status != StageStatus.DONE:
                return record
        return None

    @property
    def can_advance(self):
        return (
            self.is_active_step
            and self.status != StageStatus.DONE
            and self.blocked_by is None
        )


# ===================== Reports =====================
class ReportJobStatus(models.TextChoices):
    QUEUED = "queued", "قيد الانتظار"
    RUNNING = "running", "جارٍ الإنشاء"
    DONE = "done", "جاهز"
    FAILED = "failed", "فشل"


class ReportJob(models.Model):
    """A request to build a project's PDF report.

    The work itself runs on a background thread (see core/reports.py) so the
    user's request does not wait and a server worker is not tied up for the
    whole build — the page polls the job status (HTMX) and shows the download
    link as soon as it is ready.
    """

    project = models.ForeignKey(
        Project, verbose_name="المشروع", on_delete=models.CASCADE,
        related_name="report_jobs",
    )
    status = models.CharField(
        "الحالة", max_length=10, choices=ReportJobStatus.choices,
        default=ReportJobStatus.QUEUED,
    )
    file = models.FileField("الملف", upload_to="reports/%Y/%m/", blank=True, null=True)
    error_message = models.TextField("رسالة الخطأ", blank=True, null=True)
    requested_by = models.ForeignKey(
        User, verbose_name="طلبه", on_delete=models.SET_NULL,
        blank=True, null=True, related_name="report_jobs",
    )
    created_at = models.DateTimeField("تاريخ الطلب", default=timezone.now)
    started_at = models.DateTimeField("بدأ التنفيذ", blank=True, null=True)
    finished_at = models.DateTimeField("انتهى", blank=True, null=True)

    class Meta:
        verbose_name = "طلب تقرير"
        verbose_name_plural = "طلبات التقارير"
        ordering = ["-id"]
        indexes = [models.Index(fields=["project", "status"])]

    def __str__(self):
        return f"تقرير {self.project.name} — {self.get_status_display()}"

    @property
    def is_active(self):
        return self.status in (ReportJobStatus.QUEUED, ReportJobStatus.RUNNING)
