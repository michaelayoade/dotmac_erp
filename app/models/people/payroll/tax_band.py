"""
Tax Band Model - NTA 2025 PAYE Tax Bands.

Stores progressive tax bands for Nigerian PAYE calculation under the
Nigeria Tax Act 2025, effective January 2026.

NTA 2025 Tax Bands:
- Band 1: ₦0 - ₦800,000 @ 0%
- Band 2: ₦800,001 - ₦3,000,000 @ 15%
- Band 3: ₦3,000,001 - ₦12,000,000 @ 18%
- Band 4: ₦12,000,001 - ₦25,000,000 @ 21%
- Band 5: ₦25,000,001 - ₦50,000,000 @ 23%
- Band 6: Above ₦50,000,000 @ 25%
"""

import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    pass


class TaxBand(Base, AuditMixin):
    """
    Tax Band - Progressive tax bracket for PAYE calculation.

    Tax bands are applied in sequence from lowest to highest.
    For a given taxable income, tax is calculated by applying each
    band's rate to the portion of income that falls within that band.

    Example:
        Income: ₦5,000,000
        Band 1 (0-800k): ₦800,000 × 0% = ₦0
        Band 2 (800k-3M): ₦2,200,000 × 15% = ₦330,000
        Band 3 (3M-5M): ₦2,000,000 × 18% = ₦360,000
        Total: ₦690,000
    """

    __tablename__ = "tax_band"
    __table_args__ = (
        Index("idx_tax_band_org", "organization_id"),
        Index("idx_tax_band_active", "organization_id", "is_active", "effective_from"),
        Index("idx_tax_band_sequence", "organization_id", "sequence"),
        {"schema": "payroll"},
    )

    tax_band_id: Mapped[uuid.UUID] = mapped_column(
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

    # Band identification
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name, e.g., 'NTA 2025 - 15%'",
    )

    # Band range
    min_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        comment="Lower bound (inclusive)",
    )
    max_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(19, 4),
        nullable=True,
        comment="Upper bound (exclusive), NULL = unlimited",
    )

    # Tax rate
    rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        comment="Tax rate as decimal, e.g., 0.15 for 15%",
    )

    # Effective dates
    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    effective_to: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Ordering
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Order for calculation",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    @property
    def rate_percent(self) -> Decimal:
        """Return rate as percentage (e.g., 15 instead of 0.15)."""
        return self.rate * 100

    @property
    def range_display(self) -> str:
        """Human-readable range display."""
        min_fmt = f"₦{self.min_amount:,.0f}"
        if self.max_amount is None:
            return f"{min_fmt}+"
        max_fmt = f"₦{self.max_amount:,.0f}"
        return f"{min_fmt} - {max_fmt}"

    def calculate_tax(self, taxable_income: Decimal) -> Decimal:
        """
        Calculate tax for this band given total taxable income.

        Args:
            taxable_income: Total annual taxable income

        Returns:
            Tax amount for this band (quantized to 2 decimal places)
        """
        if taxable_income <= self.min_amount:
            return Decimal("0")

        # Determine the portion of income in this band
        if self.max_amount is None:
            # Unlimited band
            taxable_in_band = taxable_income - self.min_amount
        else:
            # Capped band
            upper = min(taxable_income, self.max_amount)
            taxable_in_band = upper - self.min_amount

        if taxable_in_band <= 0:
            return Decimal("0")

        # Quantize to 2 decimal places for currency precision
        return (taxable_in_band * self.rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def __repr__(self) -> str:
        return f"<TaxBand {self.name}: {self.range_display} @ {self.rate_percent}%>"
