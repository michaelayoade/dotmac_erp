"""
AP invoice inventory receipt approval model.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InvoiceInventoryReceiptApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    REJECTED = "REJECTED"
    POSTED_TO_INVENTORY = "POSTED_TO_INVENTORY"


class InvoiceInventoryReceiptApproval(Base):
    """
    Store manager approval request for AP invoice stock receipts.

    Pending records do not affect stock. Inventory is updated only after approval
    creates a linked inventory transaction.
    """

    __tablename__ = "invoice_inventory_receipt_approval"
    __table_args__ = (
        Index("idx_ap_inv_receipt_approval_org_status", "organization_id", "status"),
        Index("idx_ap_inv_receipt_approval_invoice", "supplier_invoice_id"),
        Index("idx_ap_inv_receipt_approval_warehouse", "organization_id", "warehouse_id"),
        {"schema": "ap"},
    )

    approval_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    supplier_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    supplier_invoice_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=False,
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse.warehouse_id"),
        nullable=True,
    )
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    approved_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    receipt_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    receipt_serial_numbers: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    status: Mapped[InvoiceInventoryReceiptApprovalStatus] = mapped_column(
        Enum(
            InvoiceInventoryReceiptApprovalStatus,
            name="invoice_inventory_receipt_approval_status",
        ),
        nullable=False,
        default=InvoiceInventoryReceiptApprovalStatus.PENDING,
        server_default=InvoiceInventoryReceiptApprovalStatus.PENDING.value,
    )
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    rejected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    inventory_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.inventory_transaction.transaction_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
