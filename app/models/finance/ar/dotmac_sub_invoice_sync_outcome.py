"""Durable, tenant-scoped outcomes from the Self-Care invoice projection."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class DotmacSubInvoiceSyncOutcome(Base):
    """One observed Self-Care invoice revision and its durable disposition."""

    __tablename__ = "dotmac_sub_invoice_sync_outcome"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_sub_invoice_outcome_org",
        ),
        UniqueConstraint(
            "organization_id",
            "source_invoice_id",
            "source_updated_at",
            name="uq_sub_invoice_outcome_org_revision",
        ),
        UniqueConstraint(
            "outcome_id",
            "organization_id",
            name="uq_sub_invoice_outcome_id_org",
        ),
        CheckConstraint(
            "disposition IN ('ready', 'blocked', 'not_applicable')",
            name="ck_sub_invoice_outcome_disposition",
        ),
        CheckConstraint(
            "contract_version = 'invoice-accounting-sync.v2'",
            name="ck_sub_invoice_outcome_contract",
        ),
        CheckConstraint(
            "source_kind IN ('native', 'splynx_legacy')",
            name="ck_sub_invoice_outcome_source_kind",
        ),
        CheckConstraint(
            "length(projection_fingerprint) = 64",
            name="ck_sub_invoice_outcome_fingerprint",
        ),
        CheckConstraint(
            "(disposition = 'blocked' AND issue_count > 0) OR "
            "(disposition <> 'blocked' AND issue_count = 0)",
            name="ck_sub_invoice_outcome_issue_count",
        ),
        CheckConstraint(
            "occurrence_count > 0",
            name="ck_sub_invoice_outcome_occurrences",
        ),
        {"schema": "ar"},
    )

    outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    source_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    projection_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    issues: Mapped[list[DotmacSubInvoiceSyncIssue]] = relationship(
        back_populates="outcome",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DotmacSubInvoiceSyncIssue(Base):
    """Normalized, non-PII evidence for one blocked outcome."""

    __tablename__ = "dotmac_sub_invoice_sync_issue"
    __table_args__ = (
        ForeignKeyConstraint(
            ["outcome_id", "organization_id"],
            [
                "ar.dotmac_sub_invoice_sync_outcome.outcome_id",
                "ar.dotmac_sub_invoice_sync_outcome.organization_id",
            ],
            ondelete="CASCADE",
            name="fk_sub_invoice_issue_outcome_org",
        ),
        UniqueConstraint(
            "outcome_id",
            "issue_fingerprint",
            name="uq_sub_invoice_issue_outcome_fingerprint",
        ),
        CheckConstraint(
            "expected_amount IS NOT NULL OR actual_amount IS NOT NULL "
            "OR source_line_id IS NOT NULL OR issue_code <> ''",
            name="ck_sub_invoice_issue_has_evidence",
        ),
        {"schema": "ar"},
    )

    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    outcome_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    issue_code: Mapped[str] = mapped_column(String(80), nullable=False)
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    issue_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    outcome: Mapped[DotmacSubInvoiceSyncOutcome] = relationship(back_populates="issues")
