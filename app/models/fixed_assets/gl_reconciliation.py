"""Fixed asset GL reconciliation run models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FixedAssetGLReconciliationRun(Base):
    """A persisted fixed-asset-to-GL reconciliation package."""

    __tablename__ = "gl_reconciliation_run"
    __table_args__ = (
        Index("idx_fa_gl_recon_run_org_date", "organization_id", "as_of_date"),
        Index("idx_fa_gl_recon_run_status", "organization_id", "status"),
        {"schema": "fa"},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)

    category_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_variance_abs: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    nbv_variance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    cost_variance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    accumulated_depreciation_variance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    proposed_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    summary_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )

    exceptions: Mapped[list[FixedAssetGLReconciliationException]] = relationship(
        "FixedAssetGLReconciliationException",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class FixedAssetGLReconciliationException(Base):
    """One out-of-balance mapping captured for approval review."""

    __tablename__ = "gl_reconciliation_exception"
    __table_args__ = (
        Index("idx_fa_gl_recon_exception_run", "run_id"),
        Index("idx_fa_gl_recon_exception_status", "organization_id", "status"),
        {"schema": "fa"},
    )

    exception_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.gl_reconciliation_run.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN")
    exception_type: Mapped[str] = mapped_column(String(50), nullable=False)

    asset_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    accumulated_depreciation_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )
    category_codes: Mapped[str | None] = mapped_column(Text, nullable=True)
    variance_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    evidence_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[FixedAssetGLReconciliationRun] = relationship(
        "FixedAssetGLReconciliationRun",
        back_populates="exceptions",
    )
