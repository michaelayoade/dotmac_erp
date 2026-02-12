"""
Supplier Model - AP Schema.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base
from app.models.mixins import ERPNextSyncMixin


class SupplierType(str, enum.Enum):
    VENDOR = "VENDOR"
    CONTRACTOR = "CONTRACTOR"
    SERVICE_PROVIDER = "SERVICE_PROVIDER"
    UTILITY = "UTILITY"
    GOVERNMENT = "GOVERNMENT"
    RELATED_PARTY = "RELATED_PARTY"


class Supplier(Base, ERPNextSyncMixin):
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
    trading_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tax_identification_number: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    registration_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Payment terms
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=settings.default_functional_currency_code,
    )

    # Defaults
    default_expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    ap_control_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    supplier_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Related party
    is_related_party: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    related_party_relationship: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Withholding tax
    withholding_tax_applicable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    withholding_tax_code_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Contact & Address
    billing_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    remittance_address: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    primary_contact: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    bank_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Encrypted/masked",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Audit fields
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who created/imported this supplier",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who last updated this supplier",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
