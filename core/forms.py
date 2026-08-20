"""نماذج الإدخال."""

from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import Group

from .models import (
    Expense, Invoice, Project, ProjectPayment, User, Worker, WorkHour,
)
from .permissions import ALL_CODENAMES, ALL_PERMISSIONS

BASE_INPUT = "input input-bordered w-full"
BASE_SELECT = "select select-bordered w-full"
BASE_TEXTAREA = "textarea textarea-bordered w-full"


def _style(fields, widget_map=None):
    """توحيد أنماط DaisyUI على حقول النموذج."""
    for name, field in fields.items():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault("class", "checkbox checkbox-primary")
        elif isinstance(widget, forms.Select):
            widget.attrs.setdefault("class", BASE_SELECT)
        elif isinstance(widget, forms.Textarea):
            widget.attrs.setdefault("class", BASE_TEXTAREA)
            widget.attrs.setdefault("rows", 3)
        else:
            widget.attrs.setdefault("class", BASE_INPUT)
        if isinstance(widget, forms.DateInput):
            widget.input_type = "date"


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="اسم المستخدم",
        widget=forms.TextInput(attrs={
            "class": BASE_INPUT, "placeholder": "أدخل اسم المستخدم",
            "autofocus": True, "autocomplete": "username",
        }),
    )
    password = forms.CharField(
        label="كلمة المرور",
        widget=forms.PasswordInput(attrs={
            "class": BASE_INPUT, "placeholder": "أدخل كلمة المرور",
            "autocomplete": "current-password",
        }),
    )
    error_messages = {
        "invalid_login": "اسم المستخدم أو كلمة المرور غير صحيحة",
        "inactive": "هذا الحساب غير نشط",
    }


class ProjectForm(StyledModelForm):
    class Meta:
        model = Project
        fields = [
            "name", "code", "client_name", "client_phone", "client_email",
            "address", "type", "status", "budget", "start_date",
            "estimated_end_date", "description",
        ]
        widgets = {
            "start_date": forms.DateInput(),
            "estimated_end_date": forms.DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["type"].empty_label = "بدون نوع"
        self.fields["type"].required = False


class WorkerForm(StyledModelForm):
    class Meta:
        model = Worker
        fields = [
            "name", "employee_number", "id_number", "nationality", "phone",
            "position", "base_salary", "hourly_rate", "overtime_rate",
            "insurance_amount", "hire_date", "is_active",
        ]
        widgets = {"hire_date": forms.DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # تُحسب تلقائياً في الواجهة من الراتب، وتبقى قابلة للتعديل يدوياً
        self.fields["hourly_rate"].widget.attrs.update({"step": "0.0001", "data-rate": "hourly"})
        self.fields["overtime_rate"].widget.attrs.update({"step": "0.0001", "data-rate": "overtime"})
        self.fields["base_salary"].widget.attrs.update({"step": "0.01", "data-rate": "salary"})
        self.fields["overtime_rate"].required = False


class WorkHourForm(StyledModelForm):
    class Meta:
        model = WorkHour
        fields = ["worker", "project", "date", "regular_hours", "overtime_hours", "notes"]
        widgets = {"date": forms.DateInput()}

    def __init__(self, *args, **kwargs):
        lock_worker = kwargs.pop("lock_worker", None)
        super().__init__(*args, **kwargs)
        self.fields["worker"].queryset = Worker.objects.filter(is_active=True).order_by("name")
        self.fields["project"].queryset = Project.objects.exclude(
            status="CLOSED"
        ).order_by("-id")
        self.fields["project"].empty_label = "بدون مشروع"
        self.fields["project"].required = False
        self.fields["regular_hours"].widget.attrs.update({"step": "0.5", "min": "0"})
        self.fields["overtime_hours"].widget.attrs.update({"step": "0.5", "min": "0"})
        if lock_worker is not None:
            self.fields["worker"].queryset = Worker.objects.filter(pk=lock_worker.pk)
            self.fields["worker"].initial = lock_worker

    def clean(self):
        cleaned = super().clean()
        regular = cleaned.get("regular_hours") or Decimal("0")
        overtime = cleaned.get("overtime_hours") or Decimal("0")
        if regular <= 0 and overtime <= 0:
            raise forms.ValidationError("أدخل ساعات عادية أو إضافية")
        if regular > 24 or overtime > 24:
            raise forms.ValidationError("عدد الساعات في اليوم لا يتجاوز 24")
        return cleaned


class InvoiceForm(StyledModelForm):
    class Meta:
        model = Invoice
        fields = [
            "project", "title", "description", "issue_date", "due_date",
            "total_amount", "paid_amount", "tax_percentage", "status", "notes",
        ]
        widgets = {
            "issue_date": forms.DateInput(),
            "due_date": forms.DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.order_by("-id")
        self.fields["project"].empty_label = "بدون مشروع"

    def clean(self):
        cleaned = super().clean()
        total = cleaned.get("total_amount") or Decimal("0")
        paid = cleaned.get("paid_amount") or Decimal("0")
        if paid > total:
            raise forms.ValidationError("المبلغ المدفوع أكبر من إجمالي الفاتورة")
        return cleaned


class ProjectPaymentForm(StyledModelForm):
    class Meta:
        model = ProjectPayment
        fields = [
            "amount", "payment_date", "payment_method", "reference_number",
            "receipt_number", "description",
        ]
        widgets = {"payment_date": forms.DateInput()}

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("المبلغ يجب أن يكون أكبر من صفر")
        return amount


class ExpenseForm(StyledModelForm):
    class Meta:
        model = Expense
        fields = ["title", "amount", "expense_date", "category", "description"]
        widgets = {"expense_date": forms.DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].empty_label = "بدون تصنيف"
        self.fields["category"].required = False

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("المبلغ يجب أن يكون أكبر من صفر")
        return amount


class UserCreateForm(StyledModelForm):
    group = forms.ModelChoiceField(
        label="المجموعة", queryset=Group.objects.order_by("name"),
        empty_label=None,
        help_text="يرث المستخدم كل صلاحيات المجموعة تلقائياً",
    )
    password = forms.CharField(
        label="كلمة المرور", min_length=6,
        widget=forms.PasswordInput(attrs={"class": BASE_INPUT, "autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ["username", "full_name"]

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("اسم المستخدم موجود مسبقاً")
        if len(username) < 3:
            raise forms.ValidationError("اسم المستخدم قصير جداً")
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
            user.groups.set([self.cleaned_data["group"]])
        return user


class UserEditForm(StyledModelForm):
    group = forms.ModelChoiceField(
        label="المجموعة", queryset=Group.objects.order_by("name"),
        empty_label=None,
        help_text="يرث المستخدم كل صلاحيات المجموعة تلقائياً",
    )
    password = forms.CharField(
        label="كلمة مرور جديدة (اختياري)", required=False, min_length=6,
        widget=forms.PasswordInput(attrs={"class": BASE_INPUT, "autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ["full_name", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["group"].initial = self.instance.groups.first()

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
            user.groups.set([self.cleaned_data["group"]])
        return user


class GroupForm(StyledModelForm):
    """مجموعة صلاحيات — الاسم مع اختيار حر لصلاحيات كل قسم."""

    permissions = forms.MultipleChoiceField(
        label="الصلاحيات", required=False,
        choices=ALL_PERMISSIONS,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Group
        fields = ["name"]
        labels = {"name": "اسم المجموعة"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["permissions"].initial = list(
                self.instance.permissions.values_list("codename", flat=True)
            )

    def save(self, commit=True):
        from django.contrib.auth.models import Permission

        group = super().save(commit)
        if commit:
            selected = set(self.cleaned_data["permissions"])
            group.permissions.set(
                Permission.objects.filter(
                    content_type__app_label="core",
                    content_type__model="apppermission",
                    codename__in=[c for c in ALL_CODENAMES if c in selected],
                )
            )
        return group
