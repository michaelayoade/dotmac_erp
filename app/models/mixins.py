"""
Model Mixins - Reusable model components.

Provides common functionality across models like audit trails,
soft delete, optimistic locking, sync tracking, and field-level
change tracking.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc
from typing import Any, ClassVar

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class VersionedMixin:
    """
    Mixin for optimistic locking via version field.

    Provides a version column that should be incremented on every update.
    Use with atomic_status_transition() for safe concurrent updates.

    Usage:
        class MyModel(Base, VersionedMixin):
            __tablename__ = "my_model"
            ...

    The version field:
    - Starts at 1 for new records
    - Should be incremented on every successful update
    - Used in WHERE clauses to detect concurrent modifications
    """

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="Optimistic locking version",
    )


class TimestampMixin:
    """
    Mixin for automatic timestamp tracking.

    Provides created_at and updated_at fields that are automatically
    set on insert and update respectively.

    Usage:
        class MyModel(Base, TimestampMixin):
            __tablename__ = "my_model"
            ...
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="When the record was created",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(UTC),
        comment="When the record was last updated",
    )


class AuditMixin:
    """
    Audit mixin for tracking who created/modified records.

    Links to public.people for user tracking. This requires the Person
    model to exist (it does in DotMac).

    Usage:
        class MyModel(Base, AuditMixin):
            __tablename__ = "my_model"
            ...

        # When creating:
        model.set_created_by(user_id)

        # When updating:
        model.set_updated_by(user_id)
    """

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
        comment="User who created this record",
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
        comment="User who last updated this record",
    )

    def set_created_by(self, user_id: uuid.UUID | str | None) -> None:
        """Set the user who created this record."""
        if user_id is not None:
            if isinstance(user_id, str):
                user_id = uuid.UUID(user_id)
            self.created_by_id = user_id

    def set_updated_by(self, user_id: uuid.UUID | str | None) -> None:
        """Set the user who last updated this record."""
        if user_id is not None:
            if isinstance(user_id, str):
                user_id = uuid.UUID(user_id)
            self.updated_by_id = user_id


class FullAuditMixin(TimestampMixin, AuditMixin):
    """
    Combined mixin for full audit trail (timestamps + user tracking).

    This is the recommended mixin for most auditable entities as it provides
    both temporal and user attribution tracking.

    Usage:
        class MyModel(Base, FullAuditMixin):
            __tablename__ = "my_model"
            ...
    """

    pass


class StatusTrackingMixin:
    """
    Tracks status changes for workflow entities.

    Useful for leave applications, expense claims, etc. that go through
    approval workflows.
    """

    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status_changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )


class ERPNextSyncMixin:
    """
    ERPNext synchronization mixin for migration compatibility.

    Preserves link to original ERPNext records during migration and
    supports ongoing synchronization if needed.
    """

    erpnext_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="ERPNext document name for migration/sync",
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last synchronization timestamp",
    )


class TrackedMixin:
    """Mixin for models that want field-level change tracking.

    Declares which fields to track and how to label them for the
    user-facing change history (FieldChangeLog).

    Usage::

        class Invoice(Base, TrackedMixin):
            __tracked_fields__: ClassVar[dict[str, dict[str, Any]]] = {
                "status": {"label": "Status"},
                "amount_total": {"label": "Total Amount"},
                "customer_id": {"label": "Customer"},
                "due_date": {"label": "Due Date"},
            }
            __tracking_entity_type__: ClassVar[str] = "Invoice"
            __tracking_pk_field__: ClassVar[str] = "invoice_id"
    """

    __tracked_fields__: ClassVar[dict[str, dict[str, Any]]] = {}
    __tracking_entity_type__: ClassVar[str] = ""
    __tracking_pk_field__: ClassVar[str] = "id"
