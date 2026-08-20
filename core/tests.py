"""اختبارات نظام الصلاحيات المبني على المجموعات."""

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from .models import User
from .permissions import ALL_CODENAMES, DEFAULT_GROUPS


def perm(codename):
    return Permission.objects.get(
        content_type__app_label="core",
        content_type__model="apppermission",
        codename=codename,
    )


def make_group(name, codenames):
    group = Group.objects.create(name=name)
    group.permissions.set([perm(c) for c in codenames])
    return group


def make_user(username, group=None):
    user = User.objects.create_user(username, "pass123456", full_name=username)
    if group:
        user.groups.set([group])
    return user


class DefaultGroupsTests(TestCase):
    """المجموعات الافتراضية تُنشأ بالترحيل بصلاحياتها الصحيحة."""

    def test_default_groups_exist_with_expected_permissions(self):
        for name, codenames in DEFAULT_GROUPS.items():
            group = Group.objects.get(name=name)
            self.assertEqual(
                set(group.permissions.values_list("codename", flat=True)),
                set(codenames),
                name,
            )

    def test_all_registry_permissions_created(self):
        existing = set(
            Permission.objects.filter(
                content_type__app_label="core",
                content_type__model="apppermission",
            ).values_list("codename", flat=True)
        )
        self.assertEqual(existing, set(ALL_CODENAMES))


class PermissionInheritanceTests(TestCase):
    """المستخدم يرث صلاحيات مجموعته تلقائياً."""

    def test_user_inherits_group_permissions(self):
        group = make_group("قراءة المشاريع", ["view_projects"])
        user = make_user("viewer", group)
        self.assertTrue(user.has_perm("core.view_projects"))
        self.assertFalse(user.has_perm("core.add_project"))

    def test_changing_group_permissions_applies_to_members(self):
        group = make_group("متغيرة", ["view_projects"])
        user = make_user("member", group)
        group.permissions.add(perm("edit_project"))
        user = User.objects.get(pk=user.pk)  # تجاوز كاش الصلاحيات
        self.assertTrue(user.has_perm("core.edit_project"))

    def test_employee_only_when_group_has_no_permissions(self):
        employee = make_user("emp", Group.objects.get(name="موظف"))
        self.assertTrue(employee.is_employee_only)
        manager = make_user("mgr", Group.objects.get(name="مدير"))
        self.assertFalse(manager.is_employee_only)


class ViewEnforcementTests(TestCase):
    """كل مسار يفرض صلاحية قسمه على الخادم."""

    # (اسم المسار، الصلاحية المطلوبة، وسائط المسار)
    PROTECTED = [
        ("dashboard", "view_dashboard", []),
        ("project_list", "view_projects", []),
        ("project_create", "add_project", []),
        ("worker_list", "view_workers", []),
        ("worker_create", "add_worker", []),
        ("invoice_list", "view_invoices", []),
        ("invoice_create", "add_invoice", []),
        ("compare", "view_compare", []),
        ("user_list", "view_users", []),
        ("group_list", "view_groups", []),
        ("group_create", "add_group", []),
    ]

    def test_permission_grants_access(self):
        for route, codename, args in self.PROTECTED:
            with self.subTest(route=route):
                group = make_group(f"g-{route}", [codename])
                user = make_user(f"u-{route}", group)
                self.client.force_login(user)
                response = self.client.get(reverse(route, args=args))
                self.assertEqual(response.status_code, 200, route)

    def test_missing_permission_is_denied(self):
        # مستخدم له صلاحية واحدة فقط — كل المسارات الأخرى تُرفض (403)
        group = make_group("قراءة فقط", ["view_projects"])
        user = make_user("limited", group)
        self.client.force_login(user)
        for route, codename, args in self.PROTECTED:
            if codename == "view_projects":
                continue
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=args))
                self.assertEqual(response.status_code, 403, route)

    def test_employee_redirected_to_my_hours(self):
        employee = make_user("emp2", Group.objects.get(name="موظف"))
        self.client.force_login(employee)
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("my_hours"))

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class GroupManagementTests(TestCase):
    """إنشاء مجموعات جديدة بصلاحيات حرة وتعديلها وحذفها."""

    def setUp(self):
        self.admin = make_user("boss", Group.objects.get(name="مدير"))
        self.client.force_login(self.admin)

    def test_create_group_with_chosen_permissions(self):
        response = self.client.post(reverse("group_create"), {
            "name": "مشرف فواتير",
            "permissions": ["view_invoices", "add_invoice", "edit_invoice"],
        })
        self.assertRedirects(response, reverse("group_list"))
        group = Group.objects.get(name="مشرف فواتير")
        self.assertEqual(
            set(group.permissions.values_list("codename", flat=True)),
            {"view_invoices", "add_invoice", "edit_invoice"},
        )
        # عضو المجموعة الجديدة يرث صلاحياتها فوراً
        member = make_user("inv-user", group)
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("invoice_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("project_list")).status_code, 403)

    def test_edit_group_permissions(self):
        group = make_group("مؤقتة", ["view_projects"])
        response = self.client.post(reverse("group_edit", args=[group.pk]), {
            "name": "مؤقتة",
            "permissions": ["view_workers"],
        })
        self.assertRedirects(response, reverse("group_list"))
        self.assertEqual(
            set(group.permissions.values_list("codename", flat=True)),
            {"view_workers"},
        )

    def test_delete_group_with_members_is_blocked(self):
        group = make_group("مأهولة", [])
        make_user("occupant", group)
        self.client.post(reverse("group_delete", args=[group.pk]))
        self.assertTrue(Group.objects.filter(pk=group.pk).exists())

    def test_delete_empty_group(self):
        group = make_group("فارغة", [])
        self.client.post(reverse("group_delete", args=[group.pk]))
        self.assertFalse(Group.objects.filter(pk=group.pk).exists())


class UserManagementTests(TestCase):
    """إنشاء مستخدم مع إسناده لمجموعة."""

    def setUp(self):
        self.admin = make_user("boss2", Group.objects.get(name="مدير"))
        self.client.force_login(self.admin)

    def test_create_user_with_group(self):
        group = Group.objects.get(name="محاسب")
        response = self.client.post(reverse("user_create"), {
            "username": "newacc",
            "full_name": "محاسب جديد",
            "group": group.pk,
            "password": "secret123",
        })
        self.assertRedirects(response, reverse("user_list"))
        user = User.objects.get(username="newacc")
        self.assertEqual(user.group, group)
        self.assertTrue(user.has_perm("core.add_invoice"))
        self.assertFalse(user.has_perm("core.add_user"))

    def test_edit_user_changes_group(self):
        user = make_user("mover", Group.objects.get(name="موظف"))
        response = self.client.post(reverse("user_edit", args=[user.pk]), {
            "full_name": user.full_name,
            "group": Group.objects.get(name="محاسب").pk,
            "is_active": "on",
        })
        self.assertRedirects(response, reverse("user_list"))
        user = User.objects.get(pk=user.pk)
        self.assertEqual(user.group_name, "محاسب")
        self.assertTrue(user.has_perm("core.view_projects"))
