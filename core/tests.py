"""Tests for the group-based permission system.

Group and label strings stay Arabic: they are real data rows, not code text.
"""

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from .models import (
    Manufacturing, ManufacturingPhase, ManufacturingStage, Project,
    ReportJob, ReportJobStatus, StageStatus, User,
)
from .permissions import ALL_CODENAMES, DEFAULT_GROUPS
from .reports import _run_report_job


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
        ("user_list", "view_users", []),
        ("group_list", "view_groups", []),
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
