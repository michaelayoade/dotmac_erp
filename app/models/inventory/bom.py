"""
Bill of Materials (BOM) Models - Inventory Schema.

Manages product assembly structures and component relationships.
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


class BOMType(str, enum.Enum):
    ASSEMBLY = "ASSEMBLY"  # Final product from components
    KIT = "KIT"  # Bundle sold together, no physical assembly
    PHANTOM = "PHANTOM"  # Sub-assembly that passes through


class BillOfMaterials(Base):
    """
    Bill of Materials header.

    Defines an assembled product and its component structure.
    """

    __tablename__ = "bill_of_materials"
    __table_args__ = (
        UniqueConstraint("organization_id", "bom_code", name="uq_bom_code"),
        Index("idx_bom_item", "item_id"),
        {"schema": "inv"},
    )

    bom_id: Mapped[uuid.UUID] = mapped_column(
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

    bom_code: Mapped[str] = mapped_column(String(30), nullable=False)
    bom_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # The item this BOM produces
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=False,
    )

    bom_type: Mapped[BOMType] = mapped_column(
        Enum(BOMType, name="bom_type"),
        nullable=False,
        default=BOMType.ASSEMBLY,
    )

    # Output quantity (how many of item_id this BOM produces)
    output_quantity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=Decimal("1"),
    )
    output_uom: Mapped[str] = mapped_column(String(20), nullable=False)

    # Version control
    version: Mapped[int] = mapped_column(Numeric(5, 0), nullable=False, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
    item: Mapped["Item"] = relationship(
        "Item",
        foreign_keys=[item_id],
        lazy="noload",
    )
    components: Mapped[list["BOMComponent"]] = relationship(
        "BOMComponent",
        back_populates="bom",
        cascade="all, delete-orphan",
    )


class BOMComponent(Base):
    """
    BOM component line.

    Defines a component item and quantity required for assembly.
    """

    __tablename__ = "bom_component"
    __table_args__ = (
        Index("idx_bom_component_bom", "bom_id"),
        Index("idx_bom_component_item", "component_item_id"),
        {"schema": "inv"},
    )

    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    bom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.bill_of_materials.bom_id"),
        nullable=False,
    )

    # Component item
    component_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=False,
    )

    # Quantity required per output unit
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    uom: Mapped[str] = mapped_column(String(20), nullable=False)

    # Scrap/waste factor (percentage)
    scrap_percent: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Line number for ordering
    line_number: Mapped[int] = mapped_column(Numeric(5, 0), nullable=False, default=1)

    # Optional: specific warehouse/location for component
    warehouse_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    bom: Mapped["BillOfMaterials"] = relationship(
        "BillOfMaterials",
        back_populates="components",
    )
