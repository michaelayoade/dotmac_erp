"""
Tax Reconciliation Model - Tax Schema.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TaxReconciliation(Base):
    """
    Tax rate reconciliation (IAS 12 disclosure).
    """

    __tablename__ = "tax_reconciliation"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "fiscal_period_id",
            "jurisdiction_id",
            name="uq_tax_reconciliation",
        ),
        {"schema": "tax"},
    )

    reconciliation_id: Mapped[uuid.UUID] = mapped_column(
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
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_jurisdiction.jurisdiction_id"),
        nullable=False,
    )

    # Starting point
    profit_before_tax: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    statutory_tax_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    tax_at_statutory_rate: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Reconciling items (tax effect amounts)
    permanent_differences: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    non_deductible_expenses: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    non_taxable_income: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    rate_differential_on_foreign_income: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    tax_credits_utilized: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    change_in_unrecognized_dta: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    effect_of_tax_rate_change: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    prior_year_adjustments: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    other_reconciling_items: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    other_items_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Totals
    total_tax_expense: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    current_tax_expense: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    deferred_tax_expense: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Effective tax rate
    effective_tax_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    rate_variance: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)

    # Notes
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Audit
    prepared_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
