"""
Commitment & CommitmentLine Models - IPSAS Schema.
Encumbrance/commitment lifecycle tracking for budget control.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.finance.ipsas.enums import CommitmentStatus, CommitmentType


class Commitment(Base):
    """
    Commitment (encumbrance) - tracks budget reservation lifecycle.
    PENDING -> COMMITTED -> OBLIGATED -> PARTIALLY_PAID -> EXPENDED
    """

    __tablename__ = "commitment"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "commitment_number", name="uq_commitment_number"
        ),
        Index("idx_commitment_org_status", "organization_id", "status"),
        Index("idx_commitment_fund", "fund_id"),
        Index("idx_commitment_approp", "appropriation_id"),
        Index("idx_commitment_source", "source_type", "source_id"),
        {"schema": "ipsas"},
    )

    commitment_id: Mapped[uuid.UUID] = mapped_column(
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

    # Identity
    commitment_number: Mapped[str] = mapped_column(String(30), nullable=False)
    commitment_type: Mapped[CommitmentType] = mapped_column(
        Enum(CommitmentType, name="commitment_type", schema="ipsas"),
        nullable=False,
    )
    status: Mapped[CommitmentStatus] = mapped_column(
        Enum(CommitmentStatus, name="commitment_status", schema="ipsas"),
        nullable=False,
        default=CommitmentStatus.PENDING,
    )

    # Budget linkage
    appropriation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipsas.appropriation.appropriation_id"),
        nullable=True,
    )
    allotment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipsas.allotment.allotment_id"),
        nullable=True,
    )
    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipsas.fund.fund_id"),
        nullable=False,
    )

    # Source document (polymorphic link)
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="purchase_order, contract, payroll, etc.",
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # GL dimensions
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=False,
    )
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Period
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_year.fiscal_year_id"),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Lifecycle amounts
    committed_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    obligated_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    expended_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    cancelled_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Journal references
    commitment_journal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Encumbrance journal entry",
    )
    obligation_journal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Obligation journal entry",
    )

    # Dates
    commitment_date: Mapped[date] = mapped_column(Date, nullable=False)
    obligation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expenditure_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Audit
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    lines: Mapped[list["CommitmentLine"]] = relationship(
        "CommitmentLine",
        back_populates="commitment",
        cascade="all, delete-orphan",
    )


class CommitmentLine(Base):
    """
    Commitment line item - individual line-level encumbrance tracking.
    """

    __tablename__ = "commitment_line"
    __table_args__ = (
        Index("idx_commitment_line_commitment", "commitment_id"),
        {"schema": "ipsas"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    commitment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipsas.commitment.commitment_id"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Account
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=False,
    )

    # Description
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Amounts
    committed_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    obligated_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    expended_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Source line (polymorphic)
    source_line_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Relationships
    commitment: Mapped["Commitment"] = relationship(
        "Commitment",
        back_populates="lines",
    )
