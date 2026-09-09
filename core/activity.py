"""Recording the activity log.

Every action that changes data goes through log_activity() right after it is
saved. This is a deliberate choice over model signals: a signal cannot see who
is signing the action (there is no request there), and it fires just as loudly
for the legacy importer, the fixtures and the test suite as it does for a real
user. An explicit call also lets each view phrase its own sentence.

Writing the log must never cost the user their work, so every failure here is
swallowed and logged. That is safe because the views run in autocommit — no
ATOMIC_REQUESTS — so a failed insert cannot poison a transaction that still has
the user's own save in it.

Descriptions are user-facing, so they stay Arabic like the rest of the system.
"""

import logging

from django.contrib.auth.models import Group

from .models import (
    ActivityAction, ActivityLog, ActivityTarget, Expense, Invoice,
    Manufacturing, ManufacturingPhase, ManufacturingStage,
    ManufacturingStageRecord, Project, ProjectPayment, ReportJob, User, Worker,
    WorkHour,
)

logger = logging.getLogger(__name__)

# What each model is called in the log, how to label one row of it, and which
# project it belongs to. A model missing from here can still be logged by
# passing target_type and target_label explicitly.
_REGISTRY = {
    Project: (
        ActivityTarget.PROJECT,
        lambda obj: obj.name,
        lambda obj: obj,
    ),
    Worker: (
        ActivityTarget.WORKER,
        lambda obj: obj.name,
        lambda obj: None,
    ),
    Invoice: (
        ActivityTarget.INVOICE,
        lambda obj: f"فاتورة {obj.invoice_number or obj.pk}",
        lambda obj: obj.project,
    ),
    ProjectPayment: (
        ActivityTarget.PAYMENT,
        lambda obj: f"دفعة بمبلغ {obj.amount}",
        lambda obj: obj.project,
    ),
    Expense: (
        ActivityTarget.EXPENSE,
        lambda obj: obj.title,
        lambda obj: obj.project,
    ),
    WorkHour: (
        ActivityTarget.WORK_HOURS,
        lambda obj: f"ساعات {obj.worker.name} — {obj.date}",
        lambda obj: obj.project,
    ),
    Manufacturing: (
        ActivityTarget.MANUFACTURING,
        lambda obj: f"متابعة تصنيع {obj.project.name}",
        lambda obj: obj.project,
    ),
    ManufacturingStageRecord: (
        ActivityTarget.STAGE_RECORD,
        lambda obj: obj.stage.name,
        lambda obj: obj.manufacturing.project,
    ),
    ManufacturingPhase: (
        ActivityTarget.PHASE,
        lambda obj: obj.name,
        lambda obj: None,
    ),
    ManufacturingStage: (
        ActivityTarget.STAGE,
        lambda obj: obj.name,
        lambda obj: None,
    ),
    User: (
        ActivityTarget.USER,
        lambda obj: obj.full_name,
        lambda obj: None,
    ),
    Group: (
        ActivityTarget.GROUP,
        lambda obj: obj.name,
        lambda obj: None,
    ),
    ReportJob: (
        ActivityTarget.REPORT,
        lambda obj: f"تقرير {obj.project.name}",
        lambda obj: obj.project,
    ),
}


def _describe(obj):
    """(target_type, label, project) for a model instance."""
    for model, (target_type, label_of, project_of) in _REGISTRY.items():
        if isinstance(obj, model):
            return target_type, label_of(obj), project_of(obj)
    raise LookupError(f"no activity registry entry for {type(obj).__name__}")


def log_activity(request, action, obj, *, project=None, description=None,
                 target_type=None, target_label=None, target_id=None):
    """Record one action.

    obj is the model instance that was acted on; its type, label and project
    are read from the registry above unless given explicitly — which is what a
    deletion needs, since the instance no longer has a primary key by then.
    """
    try:
        if obj is not None:
            found_type, found_label, found_project = _describe(obj)
            target_type = target_type or found_type
            target_label = target_label or found_label
            if target_id is None:
                target_id = obj.pk
            if project is None:
                project = found_project

        actor = getattr(request, "user", None)
        if actor is not None and not actor.is_authenticated:
            actor = None

        ActivityLog.objects.create(
            actor=actor,
            actor_name=actor.full_name if actor else "",
            action=action,
            target_type=target_type or "",
            target_id=target_id,
            target_label=(target_label or "")[:200],
            description=(description or target_label or "")[:300],
            project=project,
            project_name=project.name if project else "",
        )
    except Exception:  # an audit row is never worth failing the user's action
        logger.exception("failed to write activity log entry")


def log_create(request, obj, description, **kwargs):
    return log_activity(request, ActivityAction.CREATE, obj, description=description, **kwargs)


def log_update(request, obj, description, **kwargs):
    return log_activity(request, ActivityAction.UPDATE, obj, description=description, **kwargs)


def log_delete(request, obj, description, **kwargs):
    """Log a deletion.

    Call it *before* the delete so the instance still knows its id and its
    related rows; the row it writes keeps the snapshot either way.
    """
    return log_activity(request, ActivityAction.DELETE, obj, description=description, **kwargs)


def log_status(request, obj, description, **kwargs):
    return log_activity(request, ActivityAction.STATUS, obj, description=description, **kwargs)
