"""Durable, tenant-scoped records of the file-object orphan cleanup task.

One row per apply invocation (:class:`FileOrphanCleanupRun`) and one row per
candidate key processed during that invocation
(:class:`FileOrphanCleanupDeletion`), mirroring the ``ar`` schema's
outcome/issue composite-FK pattern (see
``DotmacSubInvoiceSyncOutcome``/``DotmacSubInvoiceSyncIssue``). These are
ERP-owned decision-and-outcome records, distinct from the ``dotmac_files``
module's own ``mod_files`` schema, which owns the managed-object metadata
these runs reconcile against.

``app.tasks.file_object_reconciliation.clean_tenant_file_objects`` writes
these rows on every authorized apply: a "started" row and one ``planned``
intent row per candidate key commit together, before any delete; each key's
row is then updated to its terminal outcome; the run row is updated to its
terminal status last. See ``app.services.file_object_cleanup`` for the
recording functions and
``docs/runbooks/managed-file-object-reconciliation.md`` for the full design
and how to query one run's rows to support a restore.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FileOrphanCleanupRun(Base):
    """One reviewed-and-authorized apply invocation of the cleanup task."""

    __tablename__ = "file_orphan_cleanup_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_file_orphan_cleanup_run_org",
        ),
        UniqueConstraint(
            "organization_id",
            "invocation_id",
            name="uq_file_orphan_cleanup_run_org_invocation",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_file_orphan_cleanup_run_id_org",
        ),
        CheckConstraint(
            "length(plan_digest) = 64 AND plan_digest = lower(plan_digest)",
            name="ck_file_orphan_cleanup_run_digest",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'partial_failure')",
            name="ck_file_orphan_cleanup_run_status",
        ),
        CheckConstraint(
            "candidate_count >= 0",
            name="ck_file_orphan_cleanup_run_candidate_count",
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # No standalone `index=True` here: the migration's composite
    # `ix_file_orphan_cleanup_run_org_started (organization_id, started_at)`
    # already covers an organization_id-only lookup as its leftmost prefix,
    # and declaring a second single-column index here would drift from what
    # the migration actually creates.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    older_than: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    plan_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provider_code: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    invocation_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_counts: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    deletions: Mapped[list[FileOrphanCleanupDeletion]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FileOrphanCleanupDeletion(Base):
    """One candidate storage key processed during a cleanup run."""

    __tablename__ = "file_orphan_cleanup_deletions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "organization_id"],
            [
                "public.file_orphan_cleanup_runs.id",
                "public.file_orphan_cleanup_runs.organization_id",
            ],
            ondelete="CASCADE",
            name="fk_file_orphan_cleanup_deletion_run_org",
        ),
        UniqueConstraint(
            "organization_id",
            "run_id",
            "storage_key",
            name="uq_file_orphan_cleanup_deletion_org_run_key",
        ),
        CheckConstraint(
            "outcome IN ("
            "'planned', 'deleted', 'rechecked_referenced', 'already_absent', "
            "'rechecked_too_new', 'failed'"
            ")",
            name="ck_file_orphan_cleanup_deletion_outcome",
        ),
        CheckConstraint(
            "length(key_digest) = 64 AND key_digest = lower(key_digest)",
            name="ck_file_orphan_cleanup_deletion_key_digest",
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # No standalone `index=True` here either: the migration's composite
    # `ix_file_orphan_cleanup_deletion_org_run (organization_id, run_id)`
    # already covers it — see the matching note on
    # `FileOrphanCleanupRun.organization_id` above.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # An opaque, module-generated identifier needed to restore the object
    # from a versioned bucket. Deliberately stored raw (not digest-only) so a
    # wrong deletion can be investigated and, if the provider supports
    # versioning, restored — protected by the same RLS policy as every other
    # column on this table.
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    error_class: Mapped[str | None] = mapped_column(Text)
    observed_last_modified: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[FileOrphanCleanupRun] = relationship(back_populates="deletions")


__all__ = ["FileOrphanCleanupDeletion", "FileOrphanCleanupRun"]
