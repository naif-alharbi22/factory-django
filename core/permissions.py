"""صلاحيات النظام — تُفرَض على الخادم في كل مسار.

    مدير   (admin)      : كل شيء، بما فيه إدارة المستخدمين
    محاسب  (accountant) : تعديل المشاريع والموظفين والفواتير والدفعات
    موظف   (employee)   : تسجيل ساعات العمل فقط
"""

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def _employee_home(request):
    messages.warning(request, "ليس لديك صلاحية للوصول لهذه الصفحة")
    return redirect("my_hours")


def staff_area(view):
    """المدير والمحاسب فقط — الموظف يُحوَّل إلى صفحة ساعاته."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect("login")
        if user.is_employee_only:
            return _employee_home(request)
        return view(request, *args, **kwargs)

    return wrapper


def can_edit(view):
    """عمليات التعديل: المدير والمحاسب."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect("login")
        if not user.can_edit_data:
            raise PermissionDenied("ليس لديك صلاحية لتعديل البيانات")
        return view(request, *args, **kwargs)

    return wrapper


def admin_only(view):
    """إدارة المستخدمين: المدير فقط."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect("login")
        if not user.is_admin:
            raise PermissionDenied("هذه الصفحة للمدير فقط")
        return view(request, *args, **kwargs)

    return wrapper
