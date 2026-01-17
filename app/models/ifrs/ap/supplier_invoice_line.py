"""
Supplier Invoice Line Model - AP Schema.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SupplierInvoiceLine(Base):
    """
    Supplier invoice line item.
    """

    __tablename__ = "supplier_invoice_line"
    __table_args__ = (
        UniqueConstraint("invoice_id", "line_number", name="uq_supplier_invoice_line"),
        {"schema": "ap"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier_invoice.invoice_id"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Matching
    po_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.purchase_order_line.line_id"),
        nullable=True,
    )
    goods_receipt_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.goods_receipt_line.line_id"),
        nullable=True,
    )

    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Quantity & Price
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    line_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Tax
    tax_code_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Accounting
    expense_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    asset_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Dimensions
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Capitalization
    capitalize_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    asset_category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset_category.category_id"),
        nullable=True,
    )
    created_asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset.asset_id"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    invoice: Mapped["SupplierInvoice"] = relationship("SupplierInvoice", back_populates="lines")
    line_taxes: Mapped[list["SupplierInvoiceLineTax"]] = relationship(
        "SupplierInvoiceLineTax",
        back_populates="invoice_line",
        cascade="all, delete-orphan",
    )


# Forward references
from app.models.ifrs.ap.supplier_invoice import SupplierInvoice  # noqa: E402
from app.models.ifrs.ap.supplier_invoice_line_tax import SupplierInvoiceLineTax  # noqa: E402
