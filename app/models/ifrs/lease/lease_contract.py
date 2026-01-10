"""
Lease Contract Model - Lease Schema.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LeaseClassification(str, enum.Enum):
    FINANCE = "FINANCE"
    OPERATING = "OPERATING"
    SHORT_TERM = "SHORT_TERM"
    LOW_VALUE = "LOW_VALUE"


class LeaseStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    MODIFIED = "MODIFIED"
    TERMINATED = "TERMINATED"
    EXPIRED = "EXPIRED"


class LeaseContract(Base):
    """
    Lease contract master record (IFRS 16).
    """

    __tablename__ = "lease_contract"
    __table_args__ = (
        UniqueConstraint("organization_id", "lease_number", name="uq_lease_contract"),
        Index("idx_lease_lessor", "lessor_supplier_id"),
        {"schema": "lease"},
    )

    lease_id: Mapped[uuid.UUID] = mapped_column(
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

    lease_number: Mapped[str] = mapped_column(String(30), nullable=False)
    lease_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Lessor details
    lessor_supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    lessor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Classification
    classification: Mapped[LeaseClassification] = mapped_column(
        Enum(LeaseClassification, name="lease_classification"),
        nullable=False,
    )
    is_lessee: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Lease term
    commencement_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    lease_term_months: Mapped[int] = mapped_column(Numeric(10, 0), nullable=False)

    # Options
    has_renewal_option: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    renewal_option_term_months: Mapped[Optional[int]] = mapped_column(Numeric(10, 0), nullable=True)
    renewal_reasonably_certain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_purchase_option: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purchase_option_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    purchase_reasonably_certain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_termination_option: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    termination_penalty: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)

    # Payment terms
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    payment_timing: Mapped[str] = mapped_column(String(20), nullable=False, default="ADVANCE")
    base_payment_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Variable payments
    has_variable_payments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    variable_payment_basis: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Index/rate adjustments
    is_index_linked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    index_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    index_base_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    last_index_adjustment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Residual value
    residual_value_guarantee: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Discount rate
    incremental_borrowing_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 6),
        nullable=False,
    )
    implicit_rate_known: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    implicit_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    discount_rate_used: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)

    # Initial measurement
    initial_direct_costs: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    lease_incentives_received: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    restoration_obligation: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Underlying asset info
    asset_description: Mapped[str] = mapped_column(Text, nullable=False)
    asset_category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Status
    status: Mapped[LeaseStatus] = mapped_column(
        Enum(LeaseStatus, name="lease_status"),
        nullable=False,
        default=LeaseStatus.DRAFT,
    )

    # Accounting dimensions
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Audit
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
