"""
Procurement Plan Item Model - proc Schema.

Line items within a procurement plan.
"""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.procurement.enums import (
    PlanItemStatus,
    ProcurementMethod,
)

if TYPE_CHECKING:
    from app.models.procurement.procurement_plan import ProcurementPlan


class ProcurementPlanItem(Base):
    """
    Line item in a procurement plan.

    Specifies what is being procured, the estimated value,
    procurement method, and planned quarter.
    """

    __tablename__ = "procurement_plan_item"
    __table_args__ = (
        Index("idx_proc_plan_item_plan", "plan_id"),
        Index("idx_proc_plan_item_status", "organization_id", "status"),
        {"schema": "proc"},
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proc.procurement_plan.plan_id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    budget_line_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Economic code",
    )
    budget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Link to budget",
    )
    estimated_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    procurement_method: Mapped[ProcurementMethod] = mapped_column(
        default=ProcurementMethod.OPEN_COMPETITIVE,
    )
    planned_quarter: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="1-4",
    )
    approving_authority: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Auto-set from PPA 2007 thresholds",
    )
    category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Goods, Works, Services, Consulting",
    )
    status: Mapped[PlanItemStatus] = mapped_column(
        default=PlanItemStatus.PENDING,
    )

    # Relationships
    plan: Mapped["ProcurementPlan"] = relationship(
        "ProcurementPlan",
        back_populates="items",
    )
