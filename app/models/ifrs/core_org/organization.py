"""
Organization Model - Core Org.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ConsolidationMethod(str, enum.Enum):
    FULL = "FULL"
    PROPORTIONAL = "PROPORTIONAL"
    EQUITY = "EQUITY"
    NONE = "NONE"


class Organization(Base):
    """
    Organization entity - the top-level entity for multi-tenancy.
    """

    __tablename__ = "organization"
    __table_args__ = {"schema": "core_org"}

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        unique=True,
    )

    # Legal identity
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trading_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    registration_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tax_identification_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    incorporation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    jurisdiction_country_code: Mapped[Optional[str]] = mapped_column(
        String(2),
        nullable=True,
    )

    # Currency settings
    functional_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    presentation_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Fiscal year settings
    fiscal_year_end_month: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_year_end_day: Mapped[int] = mapped_column(Integer, nullable=False)

    # Group structure
    parent_organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=True,
    )
    consolidation_method: Mapped[Optional[ConsolidationMethod]] = mapped_column(
        Enum(ConsolidationMethod, name="consolidation_method"),
        nullable=True,
    )
    ownership_percentage: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Regional settings
    timezone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    date_format: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    number_format: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Contact information
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Address
    address_line1: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Branding
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

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
    parent_organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        remote_side=[organization_id],
        foreign_keys=[parent_organization_id],
    )
    subsidiaries: Mapped[list["Organization"]] = relationship(
        "Organization",
        back_populates="parent_organization",
    )
    business_units: Mapped[list["BusinessUnit"]] = relationship(
        "BusinessUnit",
        back_populates="organization",
    )


# Forward reference
from app.models.ifrs.core_org.business_unit import BusinessUnit  # noqa: E402
