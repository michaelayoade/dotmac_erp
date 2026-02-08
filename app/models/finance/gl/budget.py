"""
Budget Model - GL Schema.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
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


class BudgetStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class Budget(Base):
    """
    Budget header for annual/project budgets.
    """

    __tablename__ = "budget"
    __table_args__ = (
        UniqueConstraint("organization_id", "budget_code", name="uq_budget_code"),
        {"schema": "gl"},
    )

    budget_id: Mapped[uuid.UUID] = mapped_column(
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
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_year.fiscal_year_id"),
        nullable=False,
    )

    budget_code: Mapped[str] = mapped_column(String(30), nullable=False)
    budget_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Type
    budget_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="OPERATING",
        comment="OPERATING, CAPITAL, PROJECT",
    )

    # Scope
    business_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Status
    status: Mapped[BudgetStatus] = mapped_column(
        Enum(BudgetStatus, name="budget_status"),
        nullable=False,
        default=BudgetStatus.DRAFT,
    )

    # SoD tracking
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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

    # Relationships
    lines: Mapped[list["BudgetLine"]] = relationship(
        "BudgetLine",
        back_populates="budget",
        cascade="all, delete-orphan",
    )


# Forward reference
from app.models.finance.gl.budget_line import BudgetLine  # noqa: E402
