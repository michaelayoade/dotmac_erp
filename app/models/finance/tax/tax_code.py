"""
Tax Code Model - Tax Schema.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TaxType(str, enum.Enum):
    VAT = "VAT"
    GST = "GST"
    SALES_TAX = "SALES_TAX"
    WITHHOLDING = "WITHHOLDING"
    INCOME_TAX = "INCOME_TAX"
    EXCISE = "EXCISE"
    CUSTOMS = "CUSTOMS"
    OTHER = "OTHER"


class TaxCode(Base):
    """
    Tax code/rate master.
    """

    __tablename__ = "tax_code"
    __table_args__ = (
        UniqueConstraint("organization_id", "tax_code", name="uq_tax_code"),
        {"schema": "tax"},
    )

    tax_code_id: Mapped[uuid.UUID] = mapped_column(
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

    tax_code: Mapped[str] = mapped_column(String(30), nullable=False)
    tax_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tax_type: Mapped[TaxType] = mapped_column(
        Enum(TaxType, name="tax_type"),
        nullable=False,
    )
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_jurisdiction.jurisdiction_id"),
        nullable=False,
    )

    # Rate
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Tax calculation
    is_compound: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_inclusive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_recoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    recovery_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=1
    )

    # Purchase/sales applicability
    applies_to_purchases: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    applies_to_sales: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # Reporting
    tax_return_box: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    reporting_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # Accounts
    tax_collected_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    tax_paid_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    tax_expense_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
