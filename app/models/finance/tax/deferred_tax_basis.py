"""
Deferred Tax Basis Model - Tax Schema.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
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


class DifferenceType(str, enum.Enum):
    TEMPORARY_TAXABLE = "TEMPORARY_TAXABLE"
    TEMPORARY_DEDUCTIBLE = "TEMPORARY_DEDUCTIBLE"
    PERMANENT = "PERMANENT"


class DeferredTaxBasis(Base):
    """
    Deferred tax basis tracking (IAS 12).
    """

    __tablename__ = "deferred_tax_basis"
    __table_args__ = (
        UniqueConstraint("organization_id", "basis_code", name="uq_deferred_tax_basis"),
        {"schema": "tax"},
    )

    basis_id: Mapped[uuid.UUID] = mapped_column(
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
    jurisdiction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_jurisdiction.jurisdiction_id"),
        nullable=False,
    )

    basis_code: Mapped[str] = mapped_column(String(50), nullable=False)
    basis_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    difference_type: Mapped[DifferenceType] = mapped_column(
        Enum(DifferenceType, name="difference_type"),
        nullable=False,
    )

    # Source of difference
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    gl_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Current amounts
    accounting_base: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    tax_base: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    temporary_difference: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Deferred tax
    applicable_tax_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    deferred_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    is_asset: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Recognition
    is_recognized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    recognition_probability: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 4),
        nullable=True,
    )
    unrecognized_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Reversal expectation
    expected_reversal_year: Mapped[Optional[int]] = mapped_column(
        Numeric(4, 0), nullable=True
    )
    is_current_year_reversal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
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
