"""
Appropriation & Allotment Models - IPSAS Schema.
Budget authority granted by legislative/governing body.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.finance.ipsas.enums import (
    AllotmentStatus,
    AppropriationStatus,
    AppropriationType,
)


class Appropriation(Base):
    """
    Appropriation - legislatively authorized spending authority.
    Links to a fund and optionally to a GL budget.
    """

    __tablename__ = "appropriation"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "appropriation_code", name="uq_appropriation_code"
        ),
        Index("idx_approp_org_fy", "organization_id", "fiscal_year_id"),
        Index("idx_approp_fund", "fund_id"),
        {"schema": "ipsas"},
    )

    appropriation_id: Mapped[uuid.UUID] = mapped_column(
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
    fund_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipsas.fund.fund_id"),
        nullable=False,
    )
    budget_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.budget.budget_id"),
        nullable=True,
    )

    # Identity
    appropriation_code: Mapped[str] = mapped_column(String(30), nullable=False)
    appropriation_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Classification
    appropriation_type: Mapped[AppropriationType] = mapped_column(
        Enum(AppropriationType, name="appropriation_type", schema="ipsas"),
        nullable=False,
    )
    status: Mapped[AppropriationStatus] = mapped_column(
        Enum(AppropriationStatus, name="appropriation_status", schema="ipsas"),
        nullable=False,
        default=AppropriationStatus.DRAFT,
    )

    # Amounts
    approved_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    revised_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Scope (optional narrowing)
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
    )
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    business_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Legislative reference
    appropriation_act_reference: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    # Effective dates
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Audit / SoD
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
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
    allotments: Mapped[list["Allotment"]] = relationship(
        "Allotment",
        back_populates="appropriation",
        cascade="all, delete-orphan",
    )


class Allotment(Base):
    """
    Allotment - sub-allocation of an appropriation to a cost center or period.
    """

    __tablename__ = "allotment"
    __table_args__ = (
        UniqueConstraint(
            "appropriation_id", "allotment_code", name="uq_allotment_code"
        ),
        Index("idx_allotment_approp", "appropriation_id"),
        {"schema": "ipsas"},
    )

    allotment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    appropriation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipsas.appropriation.appropriation_id"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    # Identity
    allotment_code: Mapped[str] = mapped_column(String(30), nullable=False)
    allotment_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Scope
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    business_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Amount
    allotted_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Period scope
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)

    # Status
    status: Mapped[AllotmentStatus] = mapped_column(
        Enum(AllotmentStatus, name="allotment_status", schema="ipsas"),
        nullable=False,
        default=AllotmentStatus.ACTIVE,
    )

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
    appropriation: Mapped["Appropriation"] = relationship(
        "Appropriation",
        back_populates="allotments",
    )
