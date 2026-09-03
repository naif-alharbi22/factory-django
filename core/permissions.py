"""Application permissions — group-based, built on Django Groups/Permissions.

    Every module of the system has its own permissions (view/manage), and those
    permissions are collected into groups (auth.Group). A user inherits every
    permission of their group automatically.

    Default groups (names are stored in the database, so they stay Arabic):
      مدير   : every permission, users and groups included
      محاسب  : every permission except the users and groups modules
      موظف   : no permissions — only the timesheet page, open to any signed-in user

    Labels below are user-facing: they appear on the groups and permissions
    screens, so they stay Arabic like the rest of the interface.
"""

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

# Permission registry — the single source of truth, grouped by module.
# Each codename is stored in auth.Permission under core.apppermission.
# One permission per action (view/add/edit/delete) in every module.
PERMISSION_MODULES = [
    ("لوحة المعلومات", [
        ("view_dashboard", "عرض لوحة المعلومات"),
    ]),
    ("المشاريع", [
        ("view_projects", "عرض المشاريع وتفاصيلها"),
        ("add_project", "إضافة مشروع"),
        ("edit_project", "تعديل مشروع"),
        ("add_project_payment", "إضافة دفعة لمشروع"),
        ("add_project_expense", "إضافة مصروف لمشروع"),
    ]),
    ("الموظفون", [
        ("view_workers", "عرض الموظفين وتفاصيلهم"),
        ("add_worker", "إضافة موظف"),
        ("edit_worker", "تعديل بيانات موظف"),
        ("toggle_worker", "تفعيل وإيقاف موظف"),
    ]),
    ("ساعات العمل", [
        ("add_work_hours", "تسجيل ساعات عمل"),
        ("delete_work_hours", "حذف سجل ساعات"),
    ]),
    ("الفواتير", [
        ("view_invoices", "عرض الفواتير"),
        ("add_invoice", "إضافة فاتورة"),
        ("edit_invoice", "تعديل فاتورة"),
        ("delete_invoice", "حذف فاتورة"),
    ]),
    ("التصنيع", [
        ("view_manufacturing", "عرض متابعة التصنيع"),
        ("add_manufacturing", "إنشاء متابعة تصنيع لمشروع"),
        ("update_manufacturing_stage", "تحديث حالة خطوة تصنيع"),
        ("add_manufacturing_note", "إضافة ملاحظات على خطوات التصنيع"),
    ]),
    ("إعدادات التصنيع", [
        ("view_manufacturing_config", "عرض إعدادات مراحل التصنيع"),
        ("add_manufacturing_phase", "إضافة مرحلة تصنيع"),
        ("edit_manufacturing_phase", "تعديل مرحلة تصنيع"),
        ("delete_manufacturing_phase", "حذف أو إيقاف مرحلة تصنيع"),
        ("add_manufacturing_stage", "إضافة خطوة تصنيع"),
        ("edit_manufacturing_stage", "تعديل خطوة تصنيع"),
        ("delete_manufacturing_stage", "حذف أو إيقاف خطوة تصنيع"),
        ("reorder_manufacturing", "إعادة ترتيب مراحل وخطوات التصنيع"),
    ]),
    ("المقارنة", [
        ("view_compare", "مقارنة المشاريع"),
    ]),
    ("التقارير", [
        ("view_reports", "تحميل تقارير المشاريع PDF"),
    ]),
    ("المستخدمون", [
        ("view_users", "عرض المستخدمين"),
        ("add_user", "إضافة مستخدم"),
        ("edit_user", "تعديل مستخدم"),
        ("delete_user", "حذف مستخدم"),
    ]),
    ("المجموعات", [
        ("view_groups", "عرض المجموعات"),
        ("add_group", "إضافة مجموعة"),
        ("edit_group", "تعديل مجموعة وصلاحياتها"),
        ("delete_group", "حذف مجموعة"),
    ]),
]

ALL_PERMISSIONS = [perm for _, perms in PERMISSION_MODULES for perm in perms]
ALL_CODENAMES = [codename for codename, _ in ALL_PERMISSIONS]

# Modules not granted to non-managers by default (system administration
# and workflow configuration)
_ADMIN_MODULES = ("المستخدمون", "المجموعات", "إعدادات التصنيع")
# The default groups and the permissions each one carries
DEFAULT_GROUPS = {
    "مدير": ALL_CODENAMES,
    "محاسب": [
        codename
        for label, perms in PERMISSION_MODULES if label not in _ADMIN_MODULES
        for codename, _ in perms
    ],
    "موظف": [],
}


def home_route(user):
    """The landing page that suits the user's permissions."""
    if user.has_perm("core.view_dashboard"):
        return "dashboard"
    for codename, route in (
        ("view_projects", "project_list"),
        ("view_workers", "worker_list"),
        ("view_invoices", "invoice_list"),
        ("view_manufacturing", "manufacturing_list"),
        ("view_compare", "compare"),
        ("view_manufacturing_config", "workflow_settings"),
        ("view_users", "user_list"),
        ("view_groups", "group_list"),
    ):
        if user.has_perm(f"core.{codename}"):
            return route
    return "my_hours"


def require_perm(codename):
    """Require one module permission; a user with none goes to their timesheet."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect("login")
            if user.has_perm(f"core.{codename}"):
                return view(request, *args, **kwargs)
            if user.is_employee_only:
                messages.warning(request, "ليس لديك صلاحية للوصول لهذه الصفحة")
                return redirect("my_hours")
            raise PermissionDenied("ليس لديك صلاحية لهذا الإجراء")

        return wrapper

    return decorator
