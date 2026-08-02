"""
Fixed Asset Maintenance Work Order model.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class MaintenanceWorkOrderStatus(str, enum.Enum):
    """Lifecycle states for a maintenance work order."""

    DRAFT = "DRAFT"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_FOR_PARTS = "WAITING_FOR_PARTS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class MaintenanceWorkOrderPartStatus(str, enum.Enum):
    """Status of a part line in work order execution."""

    USED = "USED"
    PENDING_PROCUREMENT = "PENDING_PROCUREMENT"


class MaintenanceWorkOrder(Base):
    """
    Work order created from a maintenance request.
    """

    __tablename__ = "maintenance_work_order"
    __table_args__ = (
        Index(
            "uq_fa_maintenance_work_order_org_number",
            "organization_id",
            "work_order_number",
            unique=True,
        ),
        Index("idx_fa_maintenance_work_order_org", "organization_id"),
        Index("idx_fa_maintenance_work_order_request", "maintenance_request_id"),
        Index("idx_fa_maintenance_work_order_asset", "asset_id"),
        Index(
            "idx_fa_maintenance_work_order_status",
            "organization_id",
            "status",
        ),
        {"schema": "fa"},
    )

    work_order_id: Mapped[uuid.UUID] = mapped_column(
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
    maintenance_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.maintenance_request.maintenance_request_id"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset.asset_id"),
        nullable=False,
    )

    work_order_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[MaintenanceWorkOrderStatus] = mapped_column(
        SAEnum(MaintenanceWorkOrderStatus, name="maintenance_work_order_status"),
        nullable=False,
        default=MaintenanceWorkOrderStatus.DRAFT,
    )

    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    planned_start_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Money columns are exact decimals (kernel-adoption E4 float elimination);
    # SQLAlchemy already returned Decimal for Numeric — the float annotations
    # were a lie. No schema change.
    estimated_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
        server_default="0",
    )
    actual_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
        server_default="0",
    )
    labor_hours: Mapped[float | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )

    request = relationship("MaintenanceRequest", back_populates="work_orders")
    parts = relationship(
        "MaintenanceWorkOrderPart",
        back_populates="work_order",
        cascade="all, delete-orphan",
        order_by="MaintenanceWorkOrderPart.created_at",
    )

    def __repr__(self) -> str:
        return f"<MaintenanceWorkOrder {self.work_order_number}: {self.status.value}>"


class MaintenanceWorkOrderPart(Base):
    """Part consumed or requested during maintenance execution."""

    __tablename__ = "maintenance_work_order_part"
    __table_args__ = (
        Index("idx_fa_maintenance_work_order_part_org", "organization_id"),
        Index("idx_fa_maintenance_work_order_part_wo", "work_order_id"),
        Index("idx_fa_maintenance_work_order_part_item", "item_id"),
        {"schema": "fa"},
    )

    maintenance_work_order_part_id: Mapped[uuid.UUID] = mapped_column(
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
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.maintenance_work_order.work_order_id"),
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
    requested_quantity: Mapped[float] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
        server_default="0",
    )
    issued_quantity: Mapped[float] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
        server_default="0",
    )
    uom: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[MaintenanceWorkOrderPartStatus] = mapped_column(
        SAEnum(
            MaintenanceWorkOrderPartStatus,
            name="maintenance_work_order_part_status",
        ),
        nullable=False,
        default=MaintenanceWorkOrderPartStatus.USED,
    )
    issue_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.inventory_transaction.transaction_id"),
        nullable=True,
    )
    procurement_requisition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proc.purchase_requisition.requisition_id"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    work_order = relationship("MaintenanceWorkOrder", back_populates="parts")
