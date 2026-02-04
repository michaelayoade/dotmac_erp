"""
Fund Model - IPSAS Schema.
Core fund accounting entity for government/public sector organizations.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.finance.ipsas.enums import FundStatus, FundType


class Fund(Base):
    """
    Fund entity for IPSAS fund accounting.
    Represents a fiscal/accounting fund (general, capital, donor, trust, etc.).
    """

    __tablename__ = "fund"
    __table_args__ = (
        UniqueConstraint("organization_id", "fund_code", name="uq_fund_code"),
        Index("idx_fund_org_status", "organization_id", "status"),
        Index("idx_fund_type", "organization_id", "fund_type"),
        {"schema": "ipsas"},
    )

    fund_id: Mapped[uuid.UUID] = mapped_column(
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

    # Identity
    fund_code: Mapped[str] = mapped_column(String(20), nullable=False)
    fund_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Classification
    fund_type: Mapped[FundType] = mapped_column(
        Enum(FundType, name="fund_type", schema="ipsas"),
        nullable=False,
    )
    status: Mapped[FundStatus] = mapped_column(
        Enum(FundStatus, name="fund_status", schema="ipsas"),
        nullable=False,
        default=FundStatus.ACTIVE,
    )

    # IPSAS net assets classification
    is_restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    restriction_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Donor information (for donor/trust funds)
    donor_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    donor_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Effective dates
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Hierarchy
    parent_fund_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ipsas.fund.fund_id"),
        nullable=True,
    )

    # Audit
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
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
    parent_fund: Mapped[Optional["Fund"]] = relationship(
        "Fund",
        remote_side=[fund_id],
        foreign_keys=[parent_fund_id],
    )
    child_funds: Mapped[list["Fund"]] = relationship(
        "Fund",
        back_populates="parent_fund",
    )
