"""
Field-Level Change Tracking — SQLAlchemy event listener.

Captures changes to declared fields on models that use ``TrackedMixin``
and writes ``FieldChangeLog`` records to the ``audit`` schema.  This
provides a user-facing change history with human-readable labels,
complementing the forensic ``audit.audit_log``.

The listener hooks into ``before_flush`` so that attribute history is
still available (before the ORM resets it).  Audit records are added
to the same session and flushed with the main transaction.

Context variables (request ID, actor ID) are pulled from
``app.observability`` so that every change is attributed correctly.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.audit_field_tracking import FieldChangeLog
from app.models.mixins import TrackedMixin
from app.observability import actor_id_var, request_id_var

logger = logging.getLogger(__name__)

# ── Context var for change source (web_form / api / celery_task / system) ──
change_source_var: ContextVar[str] = ContextVar("change_source", default="")


def set_change_source(source: str) -> None:
    """Set the change source context for the current request/task.

    Call from middleware or Celery task startup::

        set_change_source("web_form")   # from ObservabilityMiddleware
        set_change_source("celery_task") # from task preamble
    """
    change_source_var.set(source)


# ── Value serialisation ─────────────────────────────────────────────────────


def _serialise(val: Any) -> str | None:
    """Convert a Python value to a display-friendly string."""
    if val is None:
        return None
    if isinstance(val, Enum):
        return str(val.value)
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


# ── Change extraction ───────────────────────────────────────────────────────


def _extract_changes(instance: Any) -> list[dict[str, Any]]:
    """Extract field-level changes from a dirty SQLAlchemy instance.

    Only inspects fields declared in ``__tracked_fields__``.
    Returns a list of change dicts ready for ``FieldChangeLog`` creation.
    """
    tracked: dict[str, dict[str, Any]] = instance.__tracked_fields__
    if not tracked:
        return []

    mapper_attrs = inspect(instance).attrs
    changes: list[dict[str, Any]] = []

    for field_name, field_config in tracked.items():
        attr = mapper_attrs.get(field_name)
        if attr is None:
            continue

        history = attr.history
        # history.has_changes() is True when old != new
        if not history.has_changes():
            continue

        # history.deleted = old values, history.added = new values
        old_val = history.deleted[0] if history.deleted else None
        new_val = history.added[0] if history.added else None

        # Skip false positives (same value after serialisation)
        old_str = _serialise(old_val)
        new_str = _serialise(new_val)
        if old_str == new_str:
            continue

        changes.append(
            {
                "field_name": field_name,
                "field_label": field_config.get("label", field_name),
                "old_value": old_str,
                "new_value": new_str,
                # Display values are populated later if display_field is configured
                "old_display": None,
                "new_display": None,
            }
        )

    return changes


# ── Event listener ──────────────────────────────────────────────────────────


def _on_before_flush(session: Session, flush_context: Any, instances: Any) -> None:
    """Capture field changes on TrackedMixin instances before flush.

    Creates ``FieldChangeLog`` records and adds them to the session
    so they are persisted in the same transaction.
    """
    for instance in list(session.dirty):
        if not isinstance(instance, TrackedMixin):
            continue
        if not instance.__tracked_fields__:
            continue

        changes = _extract_changes(instance)
        if not changes:
            continue

        # Resolve entity metadata
        entity_type: str = instance.__tracking_entity_type__ or type(instance).__name__
        pk_field: str = instance.__tracking_pk_field__
        entity_id_val = getattr(instance, pk_field, None)
        entity_id: str = str(entity_id_val) if entity_id_val is not None else ""

        # Resolve organization_id (multi-tenant)
        org_id_val = getattr(instance, "organization_id", None)
        if org_id_val is None:
            # Skip non-tenant models — shouldn't happen for tracked models
            logger.debug(
                "Skipping field tracking for %s (no organization_id)", entity_type
            )
            continue

        # Resolve context
        actor_str = actor_id_var.get()
        changed_by: UUID | None = None
        if actor_str:
            try:
                changed_by = UUID(actor_str)
            except ValueError:
                pass

        req_id = request_id_var.get() or None
        source = change_source_var.get() or None

        for change in changes:
            log = FieldChangeLog(
                organization_id=org_id_val,
                entity_type=entity_type,
                entity_id=entity_id,
                field_name=change["field_name"],
                field_label=change["field_label"],
                old_value=change["old_value"],
                new_value=change["new_value"],
                old_display=change["old_display"],
                new_display=change["new_display"],
                changed_by_user_id=changed_by,
                change_source=source,
                request_id=req_id,
            )
            session.add(log)

        logger.debug(
            "Tracked %d field changes on %s %s",
            len(changes),
            entity_type,
            entity_id,
        )


# ── Registration ────────────────────────────────────────────────────────────


def register_field_tracking() -> None:
    """Register field-level change tracking listeners on all Sessions.

    Call once at application startup (e.g. in ``app/main.py``), after
    the audit listeners are registered.
    """
    event.listen(Session, "before_flush", _on_before_flush)
    logger.info("Field-level change tracking listeners registered")
