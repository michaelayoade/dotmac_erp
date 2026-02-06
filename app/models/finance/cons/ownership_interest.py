"""
Ownership Interest Model - Consolidation Schema.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class OwnershipInterest(Base):
    """
    Ownership interest between entities.
    """

    __tablename__ = "ownership_interest"
    __table_args__ = (
        UniqueConstraint(
            "investor_entity_id",
            "investee_entity_id",
            "effective_from",
            name="uq_ownership_interest",
        ),
        {"schema": "cons"},
    )

    interest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    investor_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cons.legal_entity.entity_id"),
        nullable=False,
    )
    investee_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cons.legal_entity.entity_id"),
        nullable=False,
    )

    # Ownership percentage
    ownership_percentage: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False
    )
    voting_rights_percentage: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False
    )

    # Effective ownership (through chain)
    effective_ownership_percentage: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False
    )

    # Effective dates
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Investment details
    shares_held: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    total_shares_outstanding: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    investment_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    # NCI tracking
    nci_percentage: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), nullable=False, default=0
    )
    nci_at_acquisition: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    nci_measurement_basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Control indicators
    has_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_significant_influence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    has_joint_control: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
