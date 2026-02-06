"""
Tax Return Model - Tax Schema.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TaxReturnStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PREPARED = "PREPARED"
    REVIEWED = "REVIEWED"
    FILED = "FILED"
    AMENDED = "AMENDED"


class TaxReturnType(str, enum.Enum):
    VAT = "VAT"
    GST = "GST"
    SALES_TAX = "SALES_TAX"
    WITHHOLDING = "WITHHOLDING"
    INCOME = "INCOME"
    PAYROLL = "PAYROLL"


class TaxReturn(Base):
    """
    Tax return submission.
    """

    __tablename__ = "tax_return"
    __table_args__ = (
        Index("idx_tax_return_period", "tax_period_id"),
        {"schema": "tax"},
    )

    return_id: Mapped[uuid.UUID] = mapped_column(
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
    tax_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_period.period_id"),
        nullable=False,
    )
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_jurisdiction.jurisdiction_id"),
        nullable=False,
    )

    return_type: Mapped[TaxReturnType] = mapped_column(
        Enum(TaxReturnType, name="tax_return_type"),
        nullable=False,
    )
    return_reference: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Amounts
    total_output_tax: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    total_input_tax: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    net_tax_payable: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    adjustments: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    final_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Tax return box values (for various formats)
    box_values: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    status: Mapped[TaxReturnStatus] = mapped_column(
        Enum(TaxReturnStatus, name="tax_return_status"),
        nullable=False,
        default=TaxReturnStatus.DRAFT,
    )

    # Filing
    filed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    filed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    filing_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Payment
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    payment_journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Amendment
    is_amendment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    original_return_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    amendment_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Review
    prepared_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    prepared_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
