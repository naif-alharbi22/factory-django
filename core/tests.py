"""Tests for the group-based permission system.

Group and label strings stay Arabic: they are real data rows, not code text.
"""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    ActivityAction, ActivityLog, ActivityTarget, DashboardPeriod,
    DashboardSettings, Expense, Invoice, Manufacturing, ManufacturingPhase,
    ManufacturingStage, Project, ProjectPayment, ProjectStatus, ReportJob,
    ReportJobStatus, StageStatus, User, Worker,
)
from .permissions import ALL_CODENAMES, DEFAULT_GROUPS
from .reports import _run_report_job
from .services import calc_project_cost, dashboard_projects, dashboard_stats


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
    """The default groups are created by the migration with the right permissions."""

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
    """A user inherits their group's permissions automatically."""

    def test_user_inherits_group_permissions(self):
        group = make_group("قراءة المشاريع", ["view_projects"])
        user = make_user("viewer", group)
        self.assertTrue(user.has_perm("core.view_projects"))
        self.assertFalse(user.has_perm("core.add_project"))

    def test_changing_group_permissions_applies_to_members(self):
        group = make_group("متغيرة", ["view_projects"])
        user = make_user("member", group)
        group.permissions.add(perm("edit_project"))
        user = User.objects.get(pk=user.pk)  # bypass the permission cache
        self.assertTrue(user.has_perm("core.edit_project"))

    def test_employee_only_when_group_has_no_permissions(self):
        employee = make_user("emp", Group.objects.get(name="موظف"))
        self.assertTrue(employee.is_employee_only)
        manager = make_user("mgr", Group.objects.get(name="مدير"))
        self.assertFalse(manager.is_employee_only)


class ViewEnforcementTests(TestCase):
    """Every route enforces its module permission server-side."""

    # (route name, required permission, route arguments)
    PROTECTED = [
        ("dashboard", "view_dashboard", []),
        ("project_list", "view_projects", []),
        ("project_create", "add_project", []),
        ("worker_list", "view_workers", []),
        ("worker_create", "add_worker", []),
        ("invoice_list", "view_invoices", []),
        ("invoice_create", "add_invoice", []),
        ("compare", "view_compare", []),
        ("reports_index", "view_activity", []),
        ("activity_log", "view_activity", []),
        ("user_list", "view_users", []),
        ("group_list", "view_groups", []),
        ("dashboard_settings_edit", "view_dashboard", []),
        ("group_create", "add_group", []),
        ("manufacturing_list", "view_manufacturing", []),
        ("workflow_settings", "view_manufacturing_config", []),
        ("phase_create", "add_manufacturing_phase", []),
        ("stage_create", "add_manufacturing_stage", []),
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
        # A user with a single permission — every other route is denied (403)
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
    """Creating groups with arbitrary permissions, editing and deleting them."""

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
        # A member of the new group inherits its permissions immediately
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
    """Creating a user and assigning them to a group."""

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


# ===================== Manufacturing =====================
def make_workflow(*phase_specs):
    """Build a workflow with arbitrary names, proving the logic never depends
    on them.

    phase_specs: (phase name, [stage names in order]) pairs
    """
    # Disable any earlier configuration (seed data included) for isolation
    ManufacturingPhase.objects.update(is_active=False)
    stages = {}
    for order, (phase_name, stage_names) in enumerate(phase_specs, start=1):
        phase = ManufacturingPhase.objects.create(name=phase_name, order=order)
        for stage_order, stage_name in enumerate(stage_names, start=1):
            stages[stage_name] = ManufacturingStage.objects.create(
                phase=phase, name=stage_name, order=stage_order
            )
    return stages


def workflow_names(manufacturing):
    return [r.stage.name for r in manufacturing.ordered_records()]


class SeedDataTests(TestCase):
    """The default seed data was created by the migration and not duplicated."""

    def test_default_workflow_seeded(self):
        phases = list(ManufacturingPhase.objects.order_by("order"))
        self.assertEqual([p.name for p in phases], ["التصنيع", "التنفيذ النهائي"])
        self.assertEqual(
            [s.name for s in phases[0].stages.order_by("order")], ["قص", "تجميع"]
        )
        self.assertEqual(
            [s.name for s in phases[1].stages.order_by("order")], ["تركيب", "تسليم"]
        )

    def test_seed_is_idempotent(self):
        from importlib import import_module
        from django.apps import apps as global_apps

        migration = import_module(
            "core.migrations.0005_manufacturingphase_alter_apppermission_options_and_more"
        )
        before = (
            ManufacturingPhase.objects.count(),
            ManufacturingStage.objects.count(),
        )
        migration.seed_manufacturing(global_apps, None)
        after = (
            ManufacturingPhase.objects.count(),
            ManufacturingStage.objects.count(),
        )
        self.assertEqual(before, after)


class WorkflowConfigTests(TestCase):
    """The workflow comes from configuration, not from names or constants."""

    def test_new_workflow_uses_current_active_config(self):
        make_workflow(("Phase X", ["Stage A", "Stage B"]), ("Phase Y", ["Stage C"]))
        m = Manufacturing.create_for_project(Project.objects.create(name="p1"))
        self.assertEqual(workflow_names(m), ["Stage A", "Stage B", "Stage C"])

    def test_reorder_changes_workflow_dynamically(self):
        stages = make_workflow(("Phase X", ["Stage A", "Stage B"]))
        stages["Stage A"].order, stages["Stage B"].order = 2, 1
        stages["Stage A"].save()
        stages["Stage B"].save()
        m = Manufacturing.create_for_project(Project.objects.create(name="p2"))
        self.assertEqual(workflow_names(m), ["Stage B", "Stage A"])

    def test_added_stage_appears_in_new_workflows_only(self):
        stages = make_workflow(("Phase X", ["Stage A", "Stage C"]))
        old = Manufacturing.create_for_project(Project.objects.create(name="old"))
        # Insert a stage between A and C by ordering alone, with no code change
        ManufacturingStage.objects.create(
            phase=stages["Stage A"].phase, name="Stage B", order=1
        )
        stages["Stage A"].order = 0
        stages["Stage A"].save()
        new = Manufacturing.create_for_project(Project.objects.create(name="new"))
        self.assertEqual(workflow_names(new), ["Stage A", "Stage B", "Stage C"])
        self.assertEqual(workflow_names(old), ["Stage A", "Stage C"])

    def test_inactive_stage_excluded_from_new_workflows(self):
        stages = make_workflow(("Phase X", ["Stage A", "Stage B", "Stage C"]))
        stages["Stage B"].is_active = False
        stages["Stage B"].save()
        m = Manufacturing.create_for_project(Project.objects.create(name="p3"))
        self.assertEqual(workflow_names(m), ["Stage A", "Stage C"])

    def test_no_active_stages_rejected(self):
        ManufacturingPhase.objects.update(is_active=False)
        with self.assertRaises(ValueError):
            Manufacturing.create_for_project(Project.objects.create(name="p4"))


class WorkflowProgressionTests(TestCase):
    """Progression rules are generic: any names, any number of stages."""

    def setUp(self):
        make_workflow(("Phase X", ["Stage A", "Stage B"]), ("Phase Y", ["Stage C"]))
        self.m = Manufacturing.create_for_project(Project.objects.create(name="p"))
        self.a, self.b, self.c = list(self.m.ordered_records())

    def test_first_active_stage_is_starting_stage(self):
        self.assertEqual(self.m.current_record.pk, self.a.pk)
        self.assertIsNone(self.a.blocked_by)

    def test_cannot_complete_before_previous_done(self):
        self.assertEqual(self.b.blocked_by.pk, self.a.pk)
        self.assertEqual(self.c.blocked_by.pk, self.a.pk)
        self.assertFalse(self.b.can_advance)

    def test_progression_in_order(self):
        for record in (self.a, self.b, self.c):
            record.refresh_from_db()
            self.assertTrue(record.can_advance)
            record.status = StageStatus.DONE
            record.save()
        self.m.refresh_from_db()
        self.assertTrue(self.m.is_complete)
        self.assertIsNone(self.m.current_record)

    def test_status_update_view_enforces_order(self):
        boss = make_user("wf-boss", Group.objects.get(name="مدير"))
        self.client.force_login(boss)
        # Completing the second stage before the first is refused
        self.client.post(
            reverse("manufacturing_record_status", args=[self.b.pk]),
            {"action": "complete"},
        )
        self.b.refresh_from_db()
        self.assertEqual(self.b.status, StageStatus.NOT_STARTED)
        # Completing the first and then the second goes through
        self.client.post(
            reverse("manufacturing_record_status", args=[self.a.pk]),
            {"action": "complete"},
        )
        self.client.post(
            reverse("manufacturing_record_status", args=[self.b.pk]),
            {"action": "complete"},
        )
        self.b.refresh_from_db()
        self.assertEqual(self.b.status, StageStatus.DONE)
        self.assertIsNotNone(self.b.completed_at)


class ManufacturingPagesTests(TestCase):
    """Manufacturing pages render whatever is configured, dynamically."""

    def test_detail_page_renders_configured_workflow(self):
        make_workflow(("Phase X", ["Stage A", "Stage B"]))
        m = Manufacturing.create_for_project(Project.objects.create(name="p"))
        boss = make_user("pages-boss", Group.objects.get(name="مدير"))
        self.client.force_login(boss)
        response = self.client.get(reverse("manufacturing_detail", args=[m.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        for name in ("Phase X", "Stage A", "Stage B"):
            self.assertIn(name, content)
        response = self.client.get(reverse("manufacturing_list"))
        self.assertContains(response, "Stage A")  # the current stage shown in the table


class HistoricalIntegrityTests(TestCase):
    """Configuration changes never corrupt historical records."""

    def setUp(self):
        self.stages = make_workflow(("Phase X", ["Stage A", "Stage B", "Stage C"]))
        self.m = Manufacturing.create_for_project(Project.objects.create(name="p"))

    def test_rename_preserves_records(self):
        record = self.m.ordered_records()[0]
        stage = self.stages["Stage A"]
        stage.name = "Stage A — renamed"
        stage.save()
        record.refresh_from_db()
        self.assertEqual(record.stage_id, stage.pk)
        self.assertEqual(record.stage.name, "Stage A — renamed")

    def test_deactivation_keeps_history_visible(self):
        self.stages["Stage B"].is_active = False
        self.stages["Stage B"].save()
        names = workflow_names(self.m)
        self.assertIn("Stage B", names)  # still present in the historical record
        active = [r.stage.name for r in self.m.active_records()]
        self.assertNotIn("Stage B", active)  # but not counted in the active workflow

    def test_deactivated_stage_does_not_block_progression(self):
        a, b, c = list(self.m.ordered_records())
        self.stages["Stage B"].is_active = False
        self.stages["Stage B"].save()
        a.status = StageStatus.DONE
        a.save()
        c.refresh_from_db()
        self.assertTrue(c.can_advance)

    def test_delete_stage_with_records_is_blocked(self):
        boss = make_user("hist-boss", Group.objects.get(name="مدير"))
        self.client.force_login(boss)
        stage = self.stages["Stage A"]
        self.client.post(reverse("stage_delete", args=[stage.pk]))
        self.assertTrue(ManufacturingStage.objects.filter(pk=stage.pk).exists())
        phase = stage.phase
        self.client.post(reverse("phase_delete", args=[phase.pk]))
        self.assertTrue(ManufacturingPhase.objects.filter(pk=phase.pk).exists())

    def test_delete_unused_stage_allowed(self):
        boss = make_user("hist-boss2", Group.objects.get(name="مدير"))
        self.client.force_login(boss)
        unused = ManufacturingStage.objects.create(
            phase=self.stages["Stage A"].phase, name="Stage Z", order=9
        )
        self.client.post(reverse("stage_delete", args=[unused.pk]))
        self.assertFalse(ManufacturingStage.objects.filter(pk=unused.pk).exists())


class ProgressCalculationTests(TestCase):
    """Progress is calculated dynamically from the active stages."""

    def setUp(self):
        self.stages = make_workflow(("Phase X", ["Stage A", "Stage B", "Stage C"]))
        self.m = Manufacturing.create_for_project(Project.objects.create(name="p"))

    def test_completion_changes_percentage(self):
        self.assertEqual(self.m.progress_percent, 0)
        a = self.m.ordered_records()[0]
        a.status = StageStatus.DONE
        a.save()
        self.assertEqual(self.m.progress_percent, 33)

    def test_deactivating_stage_recalculates_percentage(self):
        a = self.m.ordered_records()[0]
        a.status = StageStatus.DONE
        a.save()
        self.assertEqual(self.m.progress_percent, 33)  # 1 of 3
        self.stages["Stage B"].is_active = False
        self.stages["Stage B"].save()
        self.assertEqual(self.m.progress_percent, 50)  # 1 of the 2 that stay active

    def test_new_stage_affects_new_workflows_percentage(self):
        # On the existing tracker: completing all three stages is 100%
        for record in self.m.ordered_records():
            record.status = StageStatus.DONE
            record.save()
        self.assertEqual(self.m.progress_percent, 100)
        # Adding a new active stage does not disturb the existing tracker
        ManufacturingStage.objects.create(
            phase=self.stages["Stage A"].phase, name="Stage D", order=9
        )
        self.assertEqual(self.m.progress_percent, 100)
        # but it does count for trackers created afterwards
        new = Manufacturing.create_for_project(Project.objects.create(name="p2"))
        done = list(new.ordered_records())
        for record in done[:2]:
            record.status = StageStatus.DONE
            record.save()
        self.assertEqual(new.progress_percent, 50)  # 2 of 4


# ===================== Project reports (background job) =====================
class ReportJobGenerationTests(TestCase):
    """Requesting a report creates the row at once, without building the file
    inside the request."""

    def setUp(self):
        self.group = make_group("تقارير", ["view_reports", "view_projects"])
        self.user = make_user("rep-user", self.group)
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="مشروع الاختبار", budget=1000)

    def test_generate_creates_queued_job(self):
        response = self.client.post(
            reverse("project_report_generate", args=[self.project.pk])
        )
        self.assertRedirects(response, reverse("project_detail", args=[self.project.pk]))
        jobs = ReportJob.objects.filter(project=self.project)
        self.assertEqual(jobs.count(), 1)
        self.assertEqual(jobs.first().status, ReportJobStatus.QUEUED)
        self.assertEqual(jobs.first().requested_by, self.user)

    def test_generate_does_not_duplicate_active_job(self):
        self.client.post(reverse("project_report_generate", args=[self.project.pk]))
        self.client.post(reverse("project_report_generate", args=[self.project.pk]))
        self.assertEqual(ReportJob.objects.filter(project=self.project).count(), 1)

    def test_generate_allows_new_job_after_previous_finished(self):
        old = ReportJob.objects.create(
            project=self.project, status=ReportJobStatus.DONE,
        )
        self.client.post(reverse("project_report_generate", args=[self.project.pk]))
        self.assertEqual(ReportJob.objects.filter(project=self.project).count(), 2)
        self.assertTrue(ReportJob.objects.filter(pk=old.pk, status=ReportJobStatus.DONE).exists())

    def test_generate_requires_permission(self):
        # Give one unrelated permission so the user is denied (403) rather than
        # redirected as a permission-less employee (302)
        outsider = make_user("no-perm", make_group("بلا تقارير", ["view_projects"]))
        self.client.force_login(outsider)
        response = self.client.post(
            reverse("project_report_generate", args=[self.project.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReportJob.objects.filter(project=self.project).exists())


class ReportJobWorkerTests(TestCase):
    """The background-thread function really builds a PDF and updates the job."""

    def setUp(self):
        self.user = make_user("worker-user", make_group("تنفيذ", ["view_reports"]))
        self.project = Project.objects.create(name="مشروع PDF", budget=5000)

    def test_run_report_job_produces_pdf_and_marks_done(self):
        job = ReportJob.objects.create(project=self.project, requested_by=self.user)
        _run_report_job(job.pk)

        job.refresh_from_db()
        self.assertEqual(job.status, ReportJobStatus.DONE)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertTrue(job.file.name)
        with job.file.open("rb") as fh:
            self.assertTrue(fh.read(5).startswith(b"%PDF"))
        job.file.delete(save=False)

    def test_run_report_job_missing_job_is_a_noop(self):
        _run_report_job(999999)  # no such id — must not raise


class ReportJobStatusAndDownloadTests(TestCase):
    """The HTMX polling fragment and download link follow the real job state."""

    def setUp(self):
        self.user = make_user("status-user", make_group("حالة", ["view_reports"]))
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="مشروع الحالة", budget=100)

    def test_status_with_no_job_offers_generate_button(self):
        response = self.client.get(
            reverse("project_report_status", args=[self.project.pk])
        )
        self.assertContains(response, "إنشاء تقرير PDF")
        self.assertNotContains(response, "hx-trigger")

    def test_status_while_running_keeps_polling(self):
        ReportJob.objects.create(project=self.project, status=ReportJobStatus.RUNNING)
        response = self.client.get(
            reverse("project_report_status", args=[self.project.pk])
        )
        self.assertContains(response, "جارٍ إنشاء التقرير")
        self.assertContains(response, "hx-trigger=\"every 2s\"")

    def test_download_blocked_until_done(self):
        job = ReportJob.objects.create(project=self.project, status=ReportJobStatus.QUEUED)
        response = self.client.get(
            reverse("project_report_download", args=[self.project.pk, job.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_download_serves_finished_file(self):
        job = ReportJob.objects.create(project=self.project)
        _run_report_job(job.pk)
        job.refresh_from_db()

        response = self.client.get(
            reverse("project_report_download", args=[self.project.pk, job.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        job.file.delete(save=False)


# ===================== Project filtering by creation date =====================
class ProjectDateFilterTests(TestCase):
    def setUp(self):
        self.user = make_user("date-user", make_group("عرض المشاريع", ["view_projects"]))
        self.client.force_login(self.user)
        self.old = Project.objects.create(name="مشروع قديم")
        Project.objects.filter(pk=self.old.pk).update(created_at="2024-01-10T00:00:00Z")
        self.new = Project.objects.create(name="مشروع جديد")
        Project.objects.filter(pk=self.new.pk).update(created_at="2026-06-01T00:00:00Z")

    def test_filters_by_creation_date_range(self):
        response = self.client.get(
            reverse("project_list"), {"date_from": "2025-01-01", "date_to": "2025-12-31"}
        )
        names = {p.name for p in response.context["projects"]}
        self.assertEqual(names, set())

        response = self.client.get(reverse("project_list"), {"date_from": "2026-01-01"})
        names = {p.name for p in response.context["projects"]}
        self.assertEqual(names, {"مشروع جديد"})

        response = self.client.get(reverse("project_list"), {"date_to": "2024-12-31"})
        names = {p.name for p in response.context["projects"]}
        self.assertEqual(names, {"مشروع قديم"})

    def test_invalid_date_is_ignored_not_an_error(self):
        response = self.client.get(reverse("project_list"), {"date_from": "not-a-date"})
        self.assertEqual(response.status_code, 200)
        names = {p.name for p in response.context["projects"]}
        self.assertEqual(names, {"مشروع قديم", "مشروع جديد"})

    def test_project_detail_shows_creation_date(self):
        response = self.client.get(reverse("project_detail", args=[self.new.pk]))
        self.assertContains(response, "تاريخ الإنشاء")


# ===================== Activity log =====================
class ActivityLogRecordingTests(TestCase):
    """Every action a user takes leaves one readable row behind."""

    def setUp(self):
        self.admin = make_user("logger", Group.objects.get(name="مدير"))
        self.client.force_login(self.admin)

    def test_project_create_is_recorded_with_actor_and_project(self):
        self.client.post(reverse("project_create"), {
            "name": "برج الواجهات", "status": "IN_PROGRESS", "budget": "10000",
        })
        project = Project.objects.get(name="برج الواجهات")
        entry = ActivityLog.objects.get(target_type=ActivityTarget.PROJECT)
        self.assertEqual(entry.action, ActivityAction.CREATE)
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.actor_name, self.admin.full_name)
        self.assertEqual(entry.project, project)
        self.assertIn("برج الواجهات", entry.description)

    def test_payment_and_expense_are_tied_to_their_project(self):
        project = Project.objects.create(name="مشروع الدفعات")
        self.client.post(reverse("project_add_payment", args=[project.pk]), {
            "amount": "500", "payment_date": "2026-01-05", "status": "confirmed",
        })
        self.client.post(reverse("project_add_expense", args=[project.pk]), {
            "title": "مواد", "amount": "300", "expense_date": "2026-01-06",
        })
        recorded = ActivityLog.objects.filter(project=project).values_list(
            "target_type", flat=True
        )
        self.assertEqual(
            set(recorded), {ActivityTarget.PAYMENT, ActivityTarget.EXPENSE}
        )

    def test_invoice_delete_is_recorded_before_the_row_disappears(self):
        project = Project.objects.create(name="مشروع الفواتير")
        invoice = Invoice.objects.create(
            invoice_number="INV-1", project=project, issue_date="2026-01-01",
        )
        self.client.post(reverse("invoice_delete", args=[invoice.pk]))
        self.assertFalse(Invoice.objects.filter(pk=invoice.pk).exists())
        entry = ActivityLog.objects.get(target_type=ActivityTarget.INVOICE)
        self.assertEqual(entry.action, ActivityAction.DELETE)
        self.assertIn("INV-1", entry.description)
        self.assertEqual(entry.project, project)

    def test_worker_toggle_is_recorded_as_a_status_change(self):
        worker = Worker.objects.create(name="سعيد")
        self.client.post(reverse("worker_toggle_active", args=[worker.pk]))
        entry = ActivityLog.objects.get(target_type=ActivityTarget.WORKER)
        self.assertEqual(entry.action, ActivityAction.STATUS)
        self.assertIsNone(entry.project)

    def test_sign_in_and_out_are_recorded(self):
        self.client.post(reverse("logout"))
        User.objects.filter(pk=self.admin.pk)  # the user survives the sign-out
        self.client.post(reverse("login"), {
            "username": self.admin.username, "password": "pass123456",
        })
        actions = set(
            ActivityLog.objects.filter(target_type=ActivityTarget.SESSION)
            .values_list("action", flat=True)
        )
        self.assertEqual(actions, {ActivityAction.LOGIN, ActivityAction.LOGOUT})

    def test_a_failing_log_never_costs_the_user_their_work(self):
        """The audit row is best-effort: its failure must not surface."""
        project = Project.objects.create(name="مشروع محمي")
        with patch.object(
            ActivityLog.objects, "create", side_effect=RuntimeError("db down")
        ), self.assertLogs("core.activity", level="ERROR"):
            response = self.client.post(reverse("project_edit", args=[project.pk]), {
                "name": "اسم جديد", "status": "IN_PROGRESS", "budget": "0",
            })
        self.assertRedirects(response, reverse("project_detail", args=[project.pk]))
        self.assertEqual(Project.objects.get(pk=project.pk).name, "اسم جديد")


class ActivityLogSurvivalTests(TestCase):
    """History outlives what it describes."""

    def test_entry_survives_deleting_its_actor(self):
        admin = make_user("deleter", Group.objects.get(name="مدير"))
        victim = make_user("victim", Group.objects.get(name="موظف"))
        self.client.force_login(admin)
        self.client.post(reverse("user_delete", args=[victim.pk]))

        entry = ActivityLog.objects.get(target_type=ActivityTarget.USER)
        self.assertEqual(entry.target_label, victim.full_name)

        # Now delete the actor themselves: the row keeps the name it snapshotted
        ActivityLog.objects.filter(pk=entry.pk).update(actor=admin)
        admin.delete()
        entry = ActivityLog.objects.get(pk=entry.pk)
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.actor_name, "deleter")

    def test_entry_survives_deleting_its_project_and_drops_the_link(self):
        admin = make_user("proj-deleter", Group.objects.get(name="مدير"))
        project = Project.objects.create(name="مشروع زائل")
        entry = ActivityLog.objects.create(
            actor=admin, actor_name=admin.full_name, action=ActivityAction.CREATE,
            target_type=ActivityTarget.PROJECT, target_id=project.pk,
            target_label=project.name, description="أنشأ المشروع «مشروع زائل»",
            project=project, project_name=project.name,
        )
        project.delete()

        entry = ActivityLog.objects.get(pk=entry.pk)
        self.assertIsNone(entry.project)
        self.assertEqual(entry.project_name, "مشروع زائل")
        self.assertIsNone(entry.project_url)


class ActivityLogPagesTests(TestCase):
    """The dashboard card and the reports section."""

    def setUp(self):
        self.admin = make_user("viewer-admin", Group.objects.get(name="مدير"))
        self.client.force_login(self.admin)
        self.project = Project.objects.create(name="مشروع الأنشطة")
        for index in range(12):
            ActivityLog.objects.create(
                actor=self.admin, actor_name=self.admin.full_name,
                action=ActivityAction.CREATE, target_type=ActivityTarget.PROJECT,
                target_id=self.project.pk, target_label=self.project.name,
                description=f"حدث رقم {index}", project=self.project,
                project_name=self.project.name,
            )

    def test_dashboard_shows_the_latest_ten_only(self):
        response = self.client.get(reverse("dashboard"))
        recent = list(response.context["recent_activity"])
        self.assertEqual(len(recent), 10)
        # Newest first: the last one created leads
        self.assertEqual(recent[0].description, "حدث رقم 11")

    def test_dashboard_card_hidden_without_the_permission(self):
        user = make_user("no-activity", make_group("بلا أنشطة", ["view_dashboard"]))
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(list(response.context["recent_activity"]), [])
        self.assertNotContains(response, "آخر الأحداث")

    def test_reports_index_lists_the_activity_card(self):
        response = self.client.get(reverse("reports_index"))
        self.assertContains(response, "حركة الأنشطة")
        self.assertEqual(response.context["activity_count"], 12)

    def test_activity_page_paginates(self):
        response = self.client.get(reverse("activity_log"))
        self.assertEqual(response.context["total"], 12)
        self.assertEqual(len(response.context["activities"]), 12)

    def test_reports_index_opens_with_only_the_pdf_permission(self):
        user = make_user("pdf-only", make_group("تقارير PDF", ["view_reports"]))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("reports_index")).status_code, 200)
        # …but the activity page itself stays closed
        self.assertEqual(self.client.get(reverse("activity_log")).status_code, 403)


class ActivityLogFilterTests(TestCase):
    """Each filter narrows the list, and they combine."""

    def setUp(self):
        self.admin = make_user("filter-admin", Group.objects.get(name="مدير"))
        self.other = make_user("filter-other", Group.objects.get(name="محاسب"))
        self.client.force_login(self.admin)
        self.project = Project.objects.create(name="مشروع الفلترة")

        self.by_admin = ActivityLog.objects.create(
            actor=self.admin, actor_name=self.admin.full_name,
            action=ActivityAction.CREATE, target_type=ActivityTarget.PROJECT,
            description="أنشأ المشروع", project=self.project,
            project_name=self.project.name,
        )
        self.by_other = ActivityLog.objects.create(
            actor=self.other, actor_name=self.other.full_name,
            action=ActivityAction.DELETE, target_type=ActivityTarget.INVOICE,
            description="حذف الفاتورة INV-9",
        )
        ActivityLog.objects.filter(pk=self.by_admin.pk).update(
            created_at="2026-01-10T09:00:00Z"
        )
        ActivityLog.objects.filter(pk=self.by_other.pk).update(
            created_at="2026-05-20T09:00:00Z"
        )

    def descriptions(self, **params):
        response = self.client.get(reverse("activity_log"), params)
        return {item.description for item in response.context["activities"]}

    def test_filter_by_actor(self):
        self.assertEqual(
            self.descriptions(actor=self.other.pk), {"حذف الفاتورة INV-9"}
        )

    def test_filter_by_action_and_target(self):
        self.assertEqual(self.descriptions(action="delete"), {"حذف الفاتورة INV-9"})
        self.assertEqual(self.descriptions(target="project"), {"أنشأ المشروع"})

    def test_filter_by_project(self):
        self.assertEqual(self.descriptions(project=self.project.pk), {"أنشأ المشروع"})

    def test_filter_by_date_range(self):
        self.assertEqual(
            self.descriptions(date_from="2026-05-01"), {"حذف الفاتورة INV-9"}
        )
        self.assertEqual(self.descriptions(date_to="2026-01-31"), {"أنشأ المشروع"})

    def test_search_covers_description_actor_and_project(self):
        self.assertEqual(self.descriptions(search="INV-9"), {"حذف الفاتورة INV-9"})
        self.assertEqual(self.descriptions(search="filter-other"), {"حذف الفاتورة INV-9"})
        self.assertEqual(self.descriptions(search="مشروع الفلترة"), {"أنشأ المشروع"})

    def test_filters_combine(self):
        self.assertEqual(
            self.descriptions(actor=self.admin.pk, action="delete"), set()
        )

    def test_invalid_date_is_ignored_not_an_error(self):
        response = self.client.get(reverse("activity_log"), {"date_from": "not-a-date"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 2)


class PaymentAndExpenseManagementTests(TestCase):
    """Correcting or erasing a payment or an expense already on a project."""

    def setUp(self):
        self.admin = make_user("finance", Group.objects.get(name="مدير"))
        self.client.force_login(self.admin)
        self.project = Project.objects.create(name="مشروع التسويات", budget="10000")
        self.payment = ProjectPayment.objects.create(
            project=self.project, amount="500", payment_date="2026-01-05",
        )
        self.expense = Expense.objects.create(
            project=self.project, title="مواد", amount="300",
            expense_date="2026-01-06",
        )

    def test_edit_payment_saves_and_is_recorded(self):
        response = self.client.post(
            reverse("project_payment_edit", args=[self.payment.pk]),
            {"amount": "750", "payment_date": "2026-01-07"},
        )
        self.assertRedirects(
            response, reverse("project_detail", args=[self.project.pk])
        )
        self.payment.refresh_from_db()
        self.assertEqual(str(self.payment.amount), "750.00")
        entry = ActivityLog.objects.get(
            target_type=ActivityTarget.PAYMENT, action=ActivityAction.UPDATE,
        )
        self.assertEqual(entry.project, self.project)

    def test_delete_payment_removes_it_and_leaves_history(self):
        self.client.post(reverse("project_payment_delete", args=[self.payment.pk]))
        self.assertFalse(ProjectPayment.objects.filter(pk=self.payment.pk).exists())
        entry = ActivityLog.objects.get(
            target_type=ActivityTarget.PAYMENT, action=ActivityAction.DELETE,
        )
        self.assertEqual(entry.project, self.project)
        self.assertIn("500", entry.description)

    def test_edit_expense_saves_and_is_recorded(self):
        response = self.client.post(
            reverse("project_expense_edit", args=[self.expense.pk]),
            {"title": "مواد ألمنيوم", "amount": "420", "expense_date": "2026-01-06"},
        )
        self.assertRedirects(
            response, reverse("project_detail", args=[self.project.pk])
        )
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.title, "مواد ألمنيوم")
        self.assertTrue(
            ActivityLog.objects.filter(
                target_type=ActivityTarget.EXPENSE, action=ActivityAction.UPDATE,
            ).exists()
        )

    def test_delete_expense_removes_it(self):
        self.client.post(reverse("project_expense_delete", args=[self.expense.pk]))
        self.assertFalse(Expense.objects.filter(pk=self.expense.pk).exists())
        self.assertTrue(
            ActivityLog.objects.filter(
                target_type=ActivityTarget.EXPENSE, action=ActivityAction.DELETE,
            ).exists()
        )

    def test_deleting_a_payment_lowers_what_the_project_received(self):
        """The figures on the project follow the deletion, not just the table."""
        self.assertEqual(
            str(calc_project_cost(self.project.pk).payments_received), "500.00"
        )
        self.client.post(reverse("project_payment_delete", args=[self.payment.pk]))
        self.assertEqual(
            str(calc_project_cost(self.project.pk).payments_received), "0.00"
        )

    def test_a_project_less_expense_falls_back_to_the_project_list(self):
        """Expense.project is nullable, so neither screen may assume one."""
        orphan = Expense.objects.create(
            title="مصروف عام", amount="100", expense_date="2026-01-08",
        )
        self.assertEqual(
            self.client.get(reverse("project_expense_edit", args=[orphan.pk])).status_code,
            200,
        )
        response = self.client.post(
            reverse("project_expense_delete", args=[orphan.pk])
        )
        self.assertRedirects(response, reverse("project_list"))


class PaymentAndExpensePermissionTests(TestCase):
    """The four new permissions are enforced, and start with the manager."""

    # (route name, the permission it requires)
    ROUTES = [
        ("project_payment_edit", "edit_project_payment"),
        ("project_payment_delete", "delete_project_payment"),
        ("project_expense_edit", "edit_project_expense"),
        ("project_expense_delete", "delete_project_expense"),
    ]

    def setUp(self):
        self.project = Project.objects.create(name="مشروع الصلاحيات")
        self.payment = ProjectPayment.objects.create(
            project=self.project, amount="200", payment_date="2026-02-01",
        )
        self.expense = Expense.objects.create(
            project=self.project, title="نقل", amount="150",
            expense_date="2026-02-01",
        )

    def _pk_for(self, route):
        return self.payment.pk if "payment" in route else self.expense.pk

    def test_manager_holds_all_four_by_default(self):
        held = set(
            Group.objects.get(name="مدير")
            .permissions.values_list("codename", flat=True)
        )
        self.assertTrue({codename for _, codename in self.ROUTES} <= held)

    def test_accountant_holds_none_of_them_by_default(self):
        """They start with the manager alone — the manager hands them on."""
        held = set(
            Group.objects.get(name="محاسب")
            .permissions.values_list("codename", flat=True)
        )
        self.assertFalse({codename for _, codename in self.ROUTES} & held)
        # The accountant can still record new ones
        self.assertIn("add_project_payment", held)
        self.assertIn("add_project_expense", held)

    def test_missing_permission_is_denied(self):
        user = make_user("noedit", make_group("عرض فقط", ["view_projects"]))
        self.client.force_login(user)
        for route, _ in self.ROUTES:
            with self.subTest(route=route):
                response = self.client.post(
                    reverse(route, args=[self._pk_for(route)])
                )
                self.assertEqual(response.status_code, 403, route)
        self.assertTrue(ProjectPayment.objects.filter(pk=self.payment.pk).exists())
        self.assertTrue(Expense.objects.filter(pk=self.expense.pk).exists())

    def test_permission_granted_to_any_group_opens_the_route(self):
        """Nothing ties these permissions to the manager but the default."""
        group = make_group("محاسب أول", ["view_projects", "delete_project_payment"])
        self.client.force_login(make_user("senior", group))
        response = self.client.post(
            reverse("project_payment_delete", args=[self.payment.pk])
        )
        self.assertRedirects(
            response, reverse("project_detail", args=[self.project.pk])
        )
        self.assertFalse(ProjectPayment.objects.filter(pk=self.payment.pk).exists())

    def test_the_project_page_only_offers_what_the_user_may_do(self):
        viewer = make_user("plain", make_group("مشاهد", ["view_projects"]))
        self.client.force_login(viewer)
        page = self.client.get(
            reverse("project_detail", args=[self.project.pk])
        ).content.decode()
        self.assertNotIn(
            reverse("project_payment_edit", args=[self.payment.pk]), page
        )
        self.assertNotIn(
            reverse("project_expense_delete", args=[self.expense.pk]), page
        )

        self.client.force_login(make_user("full", Group.objects.get(name="مدير")))
        page = self.client.get(
            reverse("project_detail", args=[self.project.pk])
        ).content.decode()
        self.assertIn(reverse("project_payment_edit", args=[self.payment.pk]), page)
        self.assertIn(reverse("project_expense_delete", args=[self.expense.pk]), page)


class DashboardWindowTests(TestCase):
    """The period each choice resolves to."""

    def _settings(self, period):
        return DashboardSettings(period=period)

    def test_all_has_no_cut_off(self):
        self.assertIsNone(self._settings(DashboardPeriod.ALL).window_start())

    def test_current_year_starts_in_january(self):
        start = self._settings(DashboardPeriod.CURRENT_YEAR).window_start()
        self.assertEqual((start.month, start.day), (1, 1))
        self.assertEqual(start.year, timezone.localdate().year)

    def test_month_windows_step_back_that_many_months(self):
        today = timezone.localdate()
        for period, months in (
            (DashboardPeriod.LAST_3_MONTHS, 3),
            (DashboardPeriod.LAST_6_MONTHS, 6),
            (DashboardPeriod.LAST_12_MONTHS, 12),
        ):
            with self.subTest(period=period):
                start = self._settings(period).window_start()
                elapsed = (today.year - start.year) * 12 + today.month - start.month
                self.assertEqual(elapsed, months)
                self.assertLess(start, today)

    def test_a_short_target_month_clamps_instead_of_overflowing(self):
        """31 May minus three months is 28/29 February, never 2 or 3 March."""
        settings = self._settings(DashboardPeriod.LAST_3_MONTHS)
        with patch("core.models.timezone.localdate", return_value=date(2026, 5, 31)):
            self.assertEqual(settings.window_start(), date(2026, 2, 28))

    def test_a_window_can_cross_into_the_previous_year(self):
        settings = self._settings(DashboardPeriod.LAST_6_MONTHS)
        with patch("core.models.timezone.localdate", return_value=date(2026, 2, 15)):
            self.assertEqual(settings.window_start(), date(2025, 8, 15))


class DashboardScopeTests(TestCase):
    """The dashboard reads the user's slice of the system, not all of it."""

    def setUp(self):
        self.admin = make_user("viewer1", Group.objects.get(name="مدير"))
        self.client.force_login(self.admin)
        today = timezone.now()
        self.recent = Project.objects.create(
            name="مشروع حديث", budget="1000", created_at=today,
        )
        self.old = Project.objects.create(
            name="مشروع قديم", budget="2000",
            created_at=today - timedelta(days=400),
        )
        self.closed = Project.objects.create(
            name="مشروع مغلق", budget="4000",
            status=ProjectStatus.CLOSED, created_at=today,
        )

    def _names(self, settings):
        return set(
            dashboard_projects(settings).values_list("name", flat=True)
        )

    def test_default_window_hides_projects_older_than_six_months(self):
        settings = DashboardSettings(user=self.admin)
        self.assertEqual(settings.period, DashboardPeriod.LAST_6_MONTHS)
        self.assertNotIn("مشروع قديم", self._names(settings))
        self.assertIn("مشروع حديث", self._names(settings))

    def test_closed_projects_are_out_until_asked_for(self):
        settings = DashboardSettings(user=self.admin)
        self.assertNotIn("مشروع مغلق", self._names(settings))
        settings.include_closed_projects = True
        self.assertIn("مشروع مغلق", self._names(settings))

    def test_the_whole_history_is_still_available(self):
        settings = DashboardSettings(
            user=self.admin, period=DashboardPeriod.ALL,
            include_closed_projects=True,
        )
        self.assertEqual(
            self._names(settings),
            {"مشروع حديث", "مشروع قديم", "مشروع مغلق"},
        )

    def test_totals_count_only_what_is_in_scope(self):
        """The budget total follows the window — it is not the system's sum."""
        scoped = dashboard_stats(DashboardSettings(user=self.admin))
        self.assertEqual(scoped["total_projects"], 1)
        self.assertEqual(str(scoped["total_budget"]), "1000.00")

        everything = dashboard_stats(DashboardSettings(
            user=self.admin, period=DashboardPeriod.ALL,
            include_closed_projects=True,
        ))
        self.assertEqual(everything["total_projects"], 3)
        self.assertEqual(str(everything["total_budget"]), "7000.00")

    def test_invoices_follow_the_window_on_their_issue_date(self):
        Invoice.objects.create(
            invoice_number="INV-OLD", project=self.recent,
            issue_date=timezone.localdate() - timedelta(days=400),
            total_amount="900",
        )
        Invoice.objects.create(
            invoice_number="INV-NEW", project=self.recent,
            issue_date=timezone.localdate(), total_amount="100",
        )
        scoped = dashboard_stats(DashboardSettings(user=self.admin))
        self.assertEqual(scoped["total_invoices"], 1)
        self.assertEqual(str(scoped["billed_invoices"]), "100.00")

    def test_row_counts_bound_each_card(self):
        for index in range(6):
            Project.objects.create(name=f"مشروع {index}", budget="500")
        settings = DashboardSettings(
            user=self.admin, top_projects_count=2, active_projects_count=3,
        )
        self.assertEqual(len(dashboard_stats(settings)["top_projects"]), 2)

        settings.save()
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(len(page.context["active_projects"]), 3)

    def test_the_page_says_which_window_it_is_showing(self):
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, DashboardPeriod.LAST_6_MONTHS.label)


class DashboardSettingsPageTests(TestCase):
    """Each user's own settings — reachable, saved, and private to them."""

    def setUp(self):
        self.admin = make_user("owner", Group.objects.get(name="مدير"))
        self.client.force_login(self.admin)

    def test_settings_index_gathers_the_sections(self):
        page = self.client.get(reverse("settings_index"))
        self.assertEqual(page.status_code, 200)
        for route in ("user_list", "group_list", "dashboard_settings_edit"):
            self.assertContains(page, reverse(route))

    def test_saving_creates_the_row_and_changes_the_dashboard(self):
        self.assertFalse(DashboardSettings.objects.filter(user=self.admin).exists())
        response = self.client.post(reverse("dashboard_settings_edit"), {
            "period": DashboardPeriod.CURRENT_YEAR,
            "include_closed_projects": "on",
            "top_projects_count": "3",
            "active_projects_count": "4",
            "activity_count": "5",
        })
        self.assertRedirects(response, reverse("settings_index"))
        saved = DashboardSettings.objects.get(user=self.admin)
        self.assertEqual(saved.period, DashboardPeriod.CURRENT_YEAR)
        self.assertTrue(saved.include_closed_projects)
        self.assertEqual(saved.top_projects_count, 3)
        self.assertContains(
            self.client.get(reverse("dashboard")),
            DashboardPeriod.CURRENT_YEAR.label,
        )

    def test_row_counts_outside_their_bounds_are_refused(self):
        response = self.client.post(reverse("dashboard_settings_edit"), {
            "period": DashboardPeriod.LAST_6_MONTHS,
            "top_projects_count": "500",
            "active_projects_count": "4",
            "activity_count": "5",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DashboardSettings.objects.filter(user=self.admin).exists())

    def test_settings_are_private_to_each_user(self):
        self.client.post(reverse("dashboard_settings_edit"), {
            "period": DashboardPeriod.ALL,
            "top_projects_count": "3",
            "active_projects_count": "4",
            "activity_count": "5",
        })
        other = make_user("neighbour", Group.objects.get(name="مدير"))
        self.client.force_login(other)
        self.assertContains(
            self.client.get(reverse("dashboard")),
            DashboardPeriod.LAST_6_MONTHS.label,
        )
        self.assertEqual(DashboardSettings.objects.count(), 1)

    def test_a_user_who_cannot_read_the_log_never_posts_its_settings(self):
        """Dropping the fields keeps a save from silently clearing them."""
        no_log = make_user(
            "nolog", make_group("بلا سجل", ["view_dashboard"]),
        )
        DashboardSettings.objects.create(
            user=no_log, activity_count=25, only_own_activity=True,
        )
        self.client.force_login(no_log)
        page = self.client.get(reverse("dashboard_settings_edit"))
        self.assertNotIn("activity_count", page.context["form"].fields)

        self.client.post(reverse("dashboard_settings_edit"), {
            "period": DashboardPeriod.ALL,
            "top_projects_count": "3",
            "active_projects_count": "4",
        })
        stored = DashboardSettings.objects.get(user=no_log)
        self.assertEqual(stored.period, DashboardPeriod.ALL)
        self.assertEqual(stored.activity_count, 25)
        self.assertTrue(stored.only_own_activity)

    def test_the_activity_card_can_be_narrowed_to_the_user(self):
        mine = Project.objects.create(name="مشروعي")
        ActivityLog.objects.create(
            actor=self.admin, action=ActivityAction.CREATE,
            target_type=ActivityTarget.PROJECT, target_id=mine.pk,
            description="حدث خاص بي",
        )
        ActivityLog.objects.create(
            actor=make_user("someone", Group.objects.get(name="مدير")),
            action=ActivityAction.CREATE, target_type=ActivityTarget.PROJECT,
            target_id=mine.pk, description="حدث لغيري",
        )
        DashboardSettings.objects.create(user=self.admin, only_own_activity=True)
        feed = self.client.get(reverse("dashboard")).context["recent_activity"]
        self.assertEqual([entry.description for entry in feed], ["حدث خاص بي"])
