"""
Price List Models - Inventory Schema.

Manages pricing tiers, customer-specific pricing, and quantity breaks.
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
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PriceListType(str, enum.Enum):
    SALES = "SALES"
    PURCHASE = "PURCHASE"


class PriceList(Base):
    """
    Price list header.

    Represents a named pricing structure (e.g., Retail, Wholesale, VIP).
    """

    __tablename__ = "price_list"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "price_list_code", name="uq_price_list_code"
        ),
        Index("idx_price_list_type", "price_list_type"),
        {"schema": "inv"},
    )

    price_list_id: Mapped[uuid.UUID] = mapped_column(
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

    price_list_code: Mapped[str] = mapped_column(String(30), nullable=False)
    price_list_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    price_list_type: Mapped[PriceListType] = mapped_column(
        Enum(PriceListType, name="price_list_type"),
        nullable=False,
        default=PriceListType.SALES,
    )

    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Effective dates
    effective_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Priority for overlapping price lists (higher = checked first)
    priority: Mapped[int] = mapped_column(Numeric(5, 0), nullable=False, default=0)

    # Base price list for inheritance/markup
    base_price_list_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.price_list.price_list_id"),
        nullable=True,
    )
    # Markup/markdown percentage from base price list
    markup_percent: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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

    # Relationships
    items: Mapped[list["PriceListItem"]] = relationship(
        "PriceListItem",
        back_populates="price_list",
        cascade="all, delete-orphan",
    )


class PriceListItem(Base):
    """
    Price list line item.

    Defines price for a specific item in a price list, with optional quantity breaks.
    """

    __tablename__ = "price_list_item"
    __table_args__ = (
        Index("idx_price_list_item_list", "price_list_id"),
        Index("idx_price_list_item_item", "item_id"),
        {"schema": "inv"},
    )

    price_list_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    price_list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.price_list.price_list_id"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=False,
    )

    # Pricing
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Quantity breaks (min quantity for this price)
    min_quantity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("1")
    )

    # Discount options
    discount_percent: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    discount_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    # Override effective dates at item level
    effective_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

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

    # Relationships
    price_list: Mapped["PriceList"] = relationship(
        "PriceList",
        back_populates="items",
    )
