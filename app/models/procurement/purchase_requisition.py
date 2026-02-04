"""
Purchase Requisition Model - proc Schema.

Internal purchase request (converts from Material Request or standalone).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.procurement.base import ProcurementBaseMixin
from app.models.procurement.enums import RequisitionStatus, UrgencyLevel

if TYPE_CHECKING:
    from app.models.procurement.purchase_requisition_line import PurchaseRequisitionLine


class PurchaseRequisition(Base, ProcurementBaseMixin):
    """
    Purchase requisition (internal purchase request).

    Can originate from a Material Request or be created standalone.
    Goes through budget verification and approval before conversion to RFQ.
    """

    __tablename__ = "purchase_requisition"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "requisition_number",
            name="uq_proc_requisition_org_number",
        ),
        Index("idx_proc_requisition_status", "organization_id", "status"),
        Index("idx_proc_requisition_requester", "requester_id"),
        Index("idx_proc_requisition_date", "organization_id", "requisition_date"),
        {"schema": "proc"},
    )

    requisition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    requisition_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    requisition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    requester_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="hr.employee who raised the requisition",
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    status: Mapped[RequisitionStatus] = mapped_column(
        default=RequisitionStatus.DRAFT,
    )
    urgency: Mapped[UrgencyLevel] = mapped_column(
        default=UrgencyLevel.NORMAL,
    )
    justification: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    total_estimated_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=Decimal("0"),
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="NGN",
    )
    budget_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    budget_verified_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    budget_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    material_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Link to inv.material_request",
    )
    plan_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Link to proc.procurement_plan_item",
    )
    approval_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Link to approval workflow",
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Relationships
    lines: Mapped[List["PurchaseRequisitionLine"]] = relationship(
        "PurchaseRequisitionLine",
        back_populates="requisition",
        cascade="all, delete-orphan",
        order_by="PurchaseRequisitionLine.line_number",
        lazy="selectin",
    )
