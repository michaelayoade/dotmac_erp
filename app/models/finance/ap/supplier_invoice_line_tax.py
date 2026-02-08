"""
Supplier Invoice Line Tax Model - AP Schema.

Allows multiple tax codes per supplier invoice line for flexible tax handling.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SupplierInvoiceLineTax(Base):
    """
    Tax applied to a supplier invoice line.

    Allows multiple tax codes per line (e.g., VAT + Import Duty).
    Stores rate snapshot at invoice time for audit trail.
    """

    __tablename__ = "supplier_invoice_line_tax"
    __table_args__ = {"schema": "ap"}

    line_tax_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier_invoice_line.line_id"),
        nullable=False,
        index=True,
    )
    tax_code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_code.tax_code_id"),
        nullable=False,
    )

    # Base amount on which tax is calculated
    base_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Rate snapshot at invoice time (for audit trail)
    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 6),
        nullable=False,
        comment="Rate snapshot at invoice time",
    )

    # Calculated tax amount
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Was this tax included in the line price?
    is_inclusive: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # For compound taxes - order in which taxes are applied
    sequence: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
        comment="Order for compound tax calculation",
    )

    # Recoverability tracking
    is_recoverable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Can this input tax be recovered?",
    )
    recoverable_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
        comment="Amount that can be recovered/claimed",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    invoice_line: Mapped["SupplierInvoiceLine"] = relationship(
        "SupplierInvoiceLine",
        back_populates="line_taxes",
    )


# Forward reference
from app.models.finance.ap.supplier_invoice_line import (  # noqa: E402
    SupplierInvoiceLine,
)
