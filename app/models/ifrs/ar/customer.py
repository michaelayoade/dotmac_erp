"""
Customer Model - AR Schema.
"""
import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CustomerType(str, enum.Enum):
    INDIVIDUAL = "INDIVIDUAL"
    COMPANY = "COMPANY"
    GOVERNMENT = "GOVERNMENT"
    RELATED_PARTY = "RELATED_PARTY"


class RiskCategory(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    WATCH = "WATCH"


class Customer(Base):
    """
    Customer master data.
    """

    __tablename__ = "customer"
    __table_args__ = (
        UniqueConstraint("organization_id", "customer_code", name="uq_customer_code"),
        Index("idx_customer_org", "organization_id", "is_active"),
        Index("idx_customer_risk", "organization_id", "risk_category"),
        {"schema": "ar"},
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
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

    customer_code: Mapped[str] = mapped_column(String(30), nullable=False)
    customer_type: Mapped[CustomerType] = mapped_column(
        Enum(CustomerType, name="customer_type"),
        nullable=False,
    )

    # Identity
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trading_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tax_identification_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    registration_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Credit
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    credit_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    payment_terms_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Defaults
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    price_list_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    ar_control_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    default_revenue_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Relationships
    sales_rep_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    customer_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Risk (for IFRS 9 ECL)
    risk_category: Mapped[RiskCategory] = mapped_column(
        Enum(RiskCategory, name="risk_category"),
        nullable=False,
        default=RiskCategory.MEDIUM,
    )

    # Related party
    is_related_party: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    related_party_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    related_party_relationship: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Contact & Address (JSONB for flexibility)
    billing_address: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    shipping_address: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    primary_contact: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    bank_details: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Encrypted/masked",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
