"""
Customer Model - AR Schema.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
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
        Index("idx_customer_parent", "organization_id", "parent_customer_id"),
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
    trading_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tax_identification_number: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    vat_category: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="VAT class/category for the customer",
    )
    registration_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Credit
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    credit_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    credit_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payment_terms_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Defaults
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=settings.default_functional_currency_code,
    )
    price_list_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    ar_control_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    default_revenue_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    default_tax_code_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_code.tax_code_id"),
        nullable=True,
        comment="Default sales tax code for this customer",
    )

    # Relationships
    sales_rep_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    customer_group_id: Mapped[uuid.UUID | None] = mapped_column(
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
    is_related_party: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    related_party_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_party_relationship: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Withholding Tax (WHT) Configuration
    # When true, this customer deducts WHT when paying invoices
    is_wht_applicable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Customer deducts WHT on payments to us",
    )
    default_wht_code_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Default WHT rate for this customer",
    )
    wht_exemption_certificate: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="WHT exemption certificate number",
    )
    wht_exemption_expiry: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="When WHT exemption expires",
    )

    # VAT Exemption
    is_vat_exempt: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="Customer is exempt from VAT — invoice lines default to No Tax",
    )

    # Contact & Address (JSONB for flexibility)
    billing_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    shipping_address: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    primary_contact: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    bank_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Encrypted/masked",
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # External system references (for dedup and sync)
    erpnext_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="ERPNext customer name/ID (for sync lookup)",
    )
    splynx_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Splynx customer ID (for sync lookup)",
    )
    crm_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
        comment="DotMac CRM customer/company ID (for sync lookup)",
    )

    # Parent-child hierarchy (ISP reseller → sub-accounts)
    parent_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id", ondelete="SET NULL"),
        nullable=True,
    )

    # Splynx partner ID (for resolving parent during sync)
    splynx_partner_id: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        index=True,
        comment="Splynx partner ID — reseller customers have this set",
    )

    # Audit fields
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who created/imported this customer",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who last updated this customer",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    parent_customer: Mapped["Customer | None"] = relationship(
        "Customer",
        remote_side="Customer.customer_id",
        foreign_keys=[parent_customer_id],
        back_populates="child_customers",
    )
    child_customers: Mapped[list["Customer"]] = relationship(
        "Customer",
        back_populates="parent_customer",
        foreign_keys=[parent_customer_id],
    )
