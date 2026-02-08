"""
Inventory Transaction Model - Inventory Schema.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TransactionType(str, enum.Enum):
    RECEIPT = "RECEIPT"
    ISSUE = "ISSUE"
    TRANSFER = "TRANSFER"
    ADJUSTMENT = "ADJUSTMENT"
    SALE = "SALE"
    RETURN = "RETURN"
    ASSEMBLY = "ASSEMBLY"
    DISASSEMBLY = "DISASSEMBLY"
    SCRAP = "SCRAP"
    COUNT_ADJUSTMENT = "COUNT_ADJUSTMENT"


class InventoryTransaction(Base):
    """
    Inventory movement transaction.
    """

    __tablename__ = "inventory_transaction"
    __table_args__ = (
        Index("idx_inv_txn_item", "item_id"),
        Index("idx_inv_txn_warehouse", "warehouse_id"),
        Index("idx_inv_txn_date", "transaction_date"),
        {"schema": "inv"},
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, name="inv_transaction_type"),
        nullable=False,
    )
    transaction_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Item and location
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=False,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse.warehouse_id"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse_location.location_id"),
        nullable=True,
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.inventory_lot.lot_id"),
        nullable=True,
    )

    # Transfer destination
    to_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    to_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Quantity and cost
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    uom: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Cost variance (for standard costing)
    cost_variance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Running balances
    quantity_before: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    quantity_after: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Source documents
    source_document_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    source_document_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Reference
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Journal entry
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Audit
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
