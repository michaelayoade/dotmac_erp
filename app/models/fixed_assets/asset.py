"""
Asset Model - FA Schema.
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
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import ERPNextSyncMixin


class AssetStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    FULLY_DEPRECIATED = "FULLY_DEPRECIATED"
    DISPOSED = "DISPOSED"
    IMPAIRED = "IMPAIRED"
    UNDER_CONSTRUCTION = "UNDER_CONSTRUCTION"


class Asset(Base, ERPNextSyncMixin):
    """
    Fixed asset master record.
    """

    __tablename__ = "asset"
    __table_args__ = (
        UniqueConstraint("organization_id", "asset_number", name="uq_asset"),
        Index("idx_asset_category", "category_id"),
        Index("idx_asset_location", "location_id"),
        {"schema": "fa"},
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(
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

    asset_number: Mapped[str] = mapped_column(String(30), nullable=False)
    asset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset_category.category_id"),
        nullable=False,
    )

    # Location and responsibility
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.location.location_id"),
        nullable=True,
    )
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    custodian_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Employee responsible for this asset",
    )

    # Project assignment (for project-specific assets)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=True,
        index=True,
        comment="Project this asset is assigned to",
    )

    # Acquisition details
    acquisition_date: Mapped[date] = mapped_column(Date, nullable=False)
    in_service_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    acquisition_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    functional_currency_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Source document
    source_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    invoice_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Depreciation parameters (can override category defaults)
    depreciation_method: Mapped[str] = mapped_column(String(30), nullable=False)
    useful_life_months: Mapped[int] = mapped_column(Numeric(10, 0), nullable=False)
    remaining_life_months: Mapped[int] = mapped_column(Numeric(10, 0), nullable=False)
    residual_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    depreciation_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Current values
    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    net_book_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    revalued_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    impairment_loss: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Status
    status: Mapped[AssetStatus] = mapped_column(
        Enum(AssetStatus, name="asset_status"),
        nullable=False,
        default=AssetStatus.DRAFT,
    )

    # CGU assignment for impairment testing
    cash_generating_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.cash_generating_unit.cgu_id"),
        nullable=True,
    )

    # Physical attributes
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    warranty_expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Insurance
    insured_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    insurance_policy_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    # Disposal
    disposal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    disposal_proceeds: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    disposal_gain_loss: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    # Component accounting (IAS 16)
    is_component_parent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset.asset_id"),
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
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
