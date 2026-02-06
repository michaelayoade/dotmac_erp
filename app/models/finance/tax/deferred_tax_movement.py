"""
Deferred Tax Movement Model - Tax Schema.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DeferredTaxMovement(Base):
    """
    Movement in deferred tax for a period (IAS 12).
    """

    __tablename__ = "deferred_tax_movement"
    __table_args__ = {"schema": "tax"}

    movement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    basis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.deferred_tax_basis.basis_id"),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    # Opening balances
    accounting_base_opening: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    tax_base_opening: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    temporary_difference_opening: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    deferred_tax_opening: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Movements in period
    accounting_base_movement: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    tax_base_movement: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    temporary_difference_movement: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Tax rate impact
    tax_rate_opening: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    tax_rate_closing: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    tax_rate_change_impact: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Deferred tax movement
    deferred_tax_movement_pl: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    deferred_tax_movement_oci: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    deferred_tax_movement_equity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Closing balances
    accounting_base_closing: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    tax_base_closing: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    temporary_difference_closing: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    deferred_tax_closing: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Recognition change
    recognition_change: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    unrecognized_closing: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    movement_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    movement_category: Mapped[str] = mapped_column(String(50), nullable=False)

    # Journal entry
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
