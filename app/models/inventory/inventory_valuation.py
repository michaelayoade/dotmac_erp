"""
Inventory Valuation Model - Inventory Schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InventoryValuation(Base):
    """
    Inventory valuation snapshot for period-end (IAS 2 - lower of cost and NRV).
    """

    __tablename__ = "inventory_valuation"
    __table_args__ = (
        UniqueConstraint(
            "fiscal_period_id",
            "item_id",
            "warehouse_id",
            name="uq_inventory_valuation",
        ),
        Index("idx_inv_val_period", "fiscal_period_id"),
        {"schema": "inv"},
    )

    valuation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)

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
    lot_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Quantity
    quantity_on_hand: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    uom: Mapped[str] = mapped_column(String(20), nullable=False)

    # Cost values
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    costing_method: Mapped[str] = mapped_column(String(30), nullable=False)

    # Net realizable value (IAS 2)
    estimated_selling_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    estimated_costs_to_complete: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    estimated_selling_costs: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    net_realizable_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    # Lower of cost and NRV
    carrying_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    write_down_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    functional_currency_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Write-down journal
    write_down_journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
