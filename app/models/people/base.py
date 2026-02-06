"""
People Module Base Models and Mixins.

This module provides base classes and mixins for all People/HR models.
These follow the same patterns as the Finance models but with HR-specific
extensions.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class PeopleBaseMixin:
    """
    Base mixin for all People models.

    Provides:
    - UUID primary key
    - Organization ID for multi-tenancy
    - Created/updated timestamps
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )


class AuditMixin:
    """
    Audit mixin for tracking who created/modified records.

    Links to public.people for user tracking. This requires the Person
    model to exist (it does in DotMac ERP).
    """

    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )
    updated_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )


class SoftDeleteMixin:
    """
    Soft delete mixin for HR entities.

    Instead of hard deleting, marks records as deleted and tracks when/who.
    Useful for audit trails and compliance requirements.
    """

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )


class StatusTrackingMixin:
    """
    Tracks status changes for workflow entities.

    Useful for leave applications, expense claims, etc. that go through
    approval workflows.
    """

    status_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status_changed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
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

    erpnext_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="ERPNext document name for migration/sync",
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last synchronization timestamp",
    )


class VersionMixin:
    """
    Optimistic locking mixin for concurrent access control.

    Used to prevent lost updates in multi-user scenarios.
    """

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="Optimistic locking version",
    )
