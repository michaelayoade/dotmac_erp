"""
Procurement Plan Model - proc Schema.

Annual procurement plan aligned with budget.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.procurement.base import ProcurementBaseMixin
from app.models.procurement.enums import ProcurementPlanStatus

if TYPE_CHECKING:
    from app.models.procurement.procurement_plan_item import ProcurementPlanItem


class ProcurementPlan(Base, ProcurementBaseMixin):
    """
    Annual procurement plan.

    Organizations create plans aligned with their budget to schedule
    procurement activities across fiscal year quarters.
    """

    __tablename__ = "procurement_plan"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "plan_number",
            name="uq_proc_plan_org_number",
        ),
        Index("idx_proc_plan_status", "organization_id", "status"),
        Index("idx_proc_plan_fiscal_year", "organization_id", "fiscal_year"),
        {"schema": "proc"},
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    plan_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    fiscal_year: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment='e.g. "2025/2026"',
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    status: Mapped[ProcurementPlanStatus] = mapped_column(
        default=ProcurementPlanStatus.DRAFT,
    )
    total_estimated_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=Decimal("0"),
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="NGN",
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Relationships
    items: Mapped[list["ProcurementPlanItem"]] = relationship(
        "ProcurementPlanItem",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="ProcurementPlanItem.line_number",
        lazy="selectin",
    )
