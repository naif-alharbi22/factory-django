from django.http import HttpResponse
from django.urls import path
from django.views.decorators.cache import cache_control

from . import reports, views

_FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<text y=".9em" font-size="90">🏭</text></svg>'
)


@cache_control(max_age=60 * 60 * 24)
def favicon(_request):
    return HttpResponse(_FAVICON, content_type="image/svg+xml")

urlpatterns = [
    path("favicon.ico", favicon),

    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    path("", views.dashboard, name="dashboard"),

    path("projects/", views.project_list, name="project_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<int:pk>/", views.project_detail, name="project_detail"),
    path("projects/<int:pk>/edit/", views.project_edit, name="project_edit"),
    path("projects/<int:pk>/payments/add/", views.project_add_payment, name="project_add_payment"),
    path("projects/<int:pk>/expenses/add/", views.project_add_expense, name="project_add_expense"),
    path("projects/<int:pk>/report.pdf", reports.project_report_pdf, name="project_report"),

    path("workers/", views.worker_list, name="worker_list"),
    path("workers/new/", views.worker_create, name="worker_create"),
    path("workers/<int:pk>/", views.worker_detail, name="worker_detail"),
    path("workers/<int:pk>/edit/", views.worker_edit, name="worker_edit"),
    path("workers/<int:pk>/toggle/", views.worker_toggle_active, name="worker_toggle_active"),

    path("hours/add/", views.hours_add, name="hours_add"),
    path("hours/<int:pk>/delete/", views.hours_delete, name="hours_delete"),
    path("my-hours/", views.my_hours, name="my_hours"),

    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/new/", views.invoice_create, name="invoice_create"),
    path("invoices/<int:pk>/edit/", views.invoice_edit, name="invoice_edit"),
    path("invoices/<int:pk>/delete/", views.invoice_delete, name="invoice_delete"),

    path("compare/", views.compare, name="compare"),

    path("users/", views.user_list, name="user_list"),
    path("users/new/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("users/<int:pk>/delete/", views.user_delete, name="user_delete"),

    path("groups/", views.group_list, name="group_list"),
    path("groups/new/", views.group_create, name="group_create"),
    path("groups/<int:pk>/edit/", views.group_edit, name="group_edit"),
    path("groups/<int:pk>/delete/", views.group_delete, name="group_delete"),
]
