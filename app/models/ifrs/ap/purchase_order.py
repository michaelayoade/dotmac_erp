"""
Purchase Order Model - AP Schema.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class POStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class PurchaseOrder(Base):
    """
    Purchase order header.
    """

    __tablename__ = "purchase_order"
    __table_args__ = (
        UniqueConstraint("organization_id", "po_number", name="uq_po_number"),
        Index("idx_po_supplier", "supplier_id"),
        Index("idx_po_status", "organization_id", "status"),
        {"schema": "ap"},
    )

    po_id: Mapped[uuid.UUID] = mapped_column(
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
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier.supplier_id"),
        nullable=False,
    )

    po_number: Mapped[str] = mapped_column(String(30), nullable=False)
    po_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_delivery_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 10), nullable=True)

    # Amounts
    subtotal: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    amount_invoiced: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    amount_received: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Status
    status: Mapped[POStatus] = mapped_column(
        Enum(POStatus, name="po_status"),
        nullable=False,
        default=POStatus.DRAFT,
    )

    shipping_address: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    terms_and_conditions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Budget / Encumbrance
    budget_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    commitment_journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # SoD tracking
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approval_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    correlation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

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

    # Relationships
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        "PurchaseOrderLine",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
    )


# Forward reference
from app.models.ifrs.ap.purchase_order_line import PurchaseOrderLine  # noqa: E402
