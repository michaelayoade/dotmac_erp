"""
Supplier Model - AP Schema.
"""
import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base


class SupplierType(str, enum.Enum):
    VENDOR = "VENDOR"
    CONTRACTOR = "CONTRACTOR"
    SERVICE_PROVIDER = "SERVICE_PROVIDER"
    UTILITY = "UTILITY"
    GOVERNMENT = "GOVERNMENT"
    RELATED_PARTY = "RELATED_PARTY"


class Supplier(Base):
    """
    Supplier master data.
    """

    __tablename__ = "supplier"
    __table_args__ = (
        UniqueConstraint("organization_id", "supplier_code", name="uq_supplier_code"),
        Index("idx_supplier_org", "organization_id", "is_active"),
        {"schema": "ap"},
    )

    supplier_id: Mapped[uuid.UUID] = mapped_column(
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

    supplier_code: Mapped[str] = mapped_column(String(30), nullable=False)
    supplier_type: Mapped[SupplierType] = mapped_column(
        Enum(SupplierType, name="supplier_type"),
        nullable=False,
    )

    # Identity
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trading_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tax_identification_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    registration_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Payment terms
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=settings.default_functional_currency_code,
    )

    # Defaults
    default_expense_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    ap_control_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    supplier_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Related party
    is_related_party: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    related_party_relationship: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Withholding tax
    withholding_tax_applicable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    withholding_tax_code_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Contact & Address
    billing_address: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    remittance_address: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
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
