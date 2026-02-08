"""
Legal Entity Model - Consolidation Schema.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EntityType(str, enum.Enum):
    PARENT = "PARENT"
    SUBSIDIARY = "SUBSIDIARY"
    ASSOCIATE = "ASSOCIATE"
    JOINT_VENTURE = "JOINT_VENTURE"
    BRANCH = "BRANCH"


class ConsolidationMethod(str, enum.Enum):
    FULL = "FULL"
    PROPORTIONATE = "PROPORTIONATE"
    EQUITY = "EQUITY"
    NOT_CONSOLIDATED = "NOT_CONSOLIDATED"


class LegalEntity(Base):
    """
    Legal entity in group structure.
    """

    __tablename__ = "legal_entity"
    __table_args__ = (
        UniqueConstraint("group_id", "entity_code", name="uq_legal_entity"),
        {"schema": "cons"},
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Group structure
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=True,
    )

    entity_code: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType, name="entity_type"),
        nullable=False,
    )

    # Immediate parent
    parent_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cons.legal_entity.entity_id"),
        nullable=True,
    )

    # Consolidation
    consolidation_method: Mapped[ConsolidationMethod] = mapped_column(
        Enum(ConsolidationMethod, name="consolidation_method"),
        nullable=False,
    )
    is_consolidating_entity: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Geographic/legal info
    country_code: Mapped[str] = mapped_column(String(3), nullable=False)
    incorporation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Functional currency
    functional_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    reporting_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Fiscal year
    fiscal_year_end_month: Mapped[int] = mapped_column(
        Numeric(2, 0), nullable=False, default=12
    )
    fiscal_year_end_day: Mapped[int] = mapped_column(
        Numeric(2, 0), nullable=False, default=31
    )

    # Acquisition/disposal
    acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    disposal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    acquisition_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    # Goodwill
    goodwill_at_acquisition: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    accumulated_goodwill_impairment: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Contact info
    address: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
