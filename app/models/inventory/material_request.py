"""
Material Request Models - Inventory Schema.

Material Requests are inventory requisitions synced from ERPNext.
They can be linked to Projects, Support Tickets, and Tasks for
cross-module inventory tracking.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
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

if TYPE_CHECKING:
    pass


class MaterialRequestType(str, enum.Enum):
    """Type of material request."""

    PURCHASE = "PURCHASE"  # Purchase from supplier
    TRANSFER = "TRANSFER"  # Transfer between warehouses
    ISSUE = "ISSUE"  # Issue/consume from stock
    MANUFACTURE = "MANUFACTURE"  # Manufacture/produce


class MaterialRequestStatus(str, enum.Enum):
    """Status of material request workflow."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_ORDERED = "PARTIALLY_ORDERED"
    ORDERED = "ORDERED"
    ISSUED = "ISSUED"
    TRANSFERRED = "TRANSFERRED"
    CANCELLED = "CANCELLED"


class MaterialRequest(Base):
    """
    Material Request header - inventory requisition.

    Synced from ERPNext Material Request DocType.
    Contains common request data: type, status, dates, requester.
    """

    __tablename__ = "material_request"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "request_number", name="uq_material_request_org_number"
        ),
        Index("idx_material_request_org", "organization_id"),
        Index("idx_material_request_status", "status"),
        Index("idx_material_request_type", "request_type"),
        Index("idx_material_request_schedule_date", "schedule_date"),
        Index("idx_material_request_requested_by", "requested_by_id"),
        Index("idx_material_request_project", "project_id"),
        Index("idx_material_request_ticket", "ticket_id"),
        Index("idx_material_request_erpnext", "erpnext_id"),
        {"schema": "inv"},
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
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

    request_number: Mapped[str] = mapped_column(String(50), nullable=False)

    request_type: Mapped[MaterialRequestType] = mapped_column(
        Enum(MaterialRequestType, name="material_request_type", schema="inv"),
        nullable=False,
        default=MaterialRequestType.PURCHASE,
    )

    status: Mapped[MaterialRequestStatus] = mapped_column(
        Enum(MaterialRequestStatus, name="material_request_status", schema="inv"),
        nullable=False,
        default=MaterialRequestStatus.DRAFT,
    )

    schedule_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )

    default_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse.warehouse_id"),
        nullable=True,
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=True,
    )

    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support.ticket.ticket_id"),
        nullable=True,
    )

    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ERPNext sync tracking
    erpnext_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Audit fields
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
    # Note: No FK constraint to people table for sync compatibility
    # The actual DB table has the FK, but SQLAlchemy model omits it
    # to avoid import order issues during sync operations
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Relationships
    items: Mapped[list["MaterialRequestItem"]] = relationship(
        "MaterialRequestItem",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="MaterialRequestItem.sequence",
    )


class MaterialRequestItem(Base):
    """
    Material Request line item - individual item requested.

    Supports cross-module links:
    - project_id: Link to core_org.project for project-based requests
    - ticket_id: Link to support.ticket for support-related inventory
    - task_id: Link to pm.task for task-specific inventory needs
    """

    __tablename__ = "material_request_item"
    __table_args__ = (
        Index("idx_mri_org", "organization_id"),
        Index("idx_mri_request", "request_id"),
        Index("idx_mri_item", "inventory_item_id"),
        Index("idx_mri_warehouse", "warehouse_id"),
        Index("idx_mri_project", "project_id"),
        Index("idx_mri_ticket", "ticket_id"),
        Index("idx_mri_task", "task_id"),
        Index("idx_mri_erpnext", "erpnext_id"),
        {"schema": "inv"},
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
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
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.material_request.request_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Item reference
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=False,
    )

    # Warehouse for this specific line item
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.warehouse.warehouse_id"),
        nullable=True,
    )

    # Quantities
    requested_qty: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    ordered_qty: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    uom: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Line-item specific schedule date (can override header)
    schedule_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Cross-module links for inventory-to-project/support integration
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=True,
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support.ticket.ticket_id"),
        nullable=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.task.task_id"),
        nullable=True,
    )

    # Ordering
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ERPNext sync tracking
    erpnext_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Audit
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

    # Relationships
    request: Mapped["MaterialRequest"] = relationship(
        "MaterialRequest",
        back_populates="items",
    )
