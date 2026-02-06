"""
Contract Model - AR Schema.
IFRS 15 Revenue Recognition.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ContractType(str, enum.Enum):
    STANDARD = "STANDARD"
    FRAMEWORK = "FRAMEWORK"
    SUBSCRIPTION = "SUBSCRIPTION"
    PROJECT = "PROJECT"


class ContractStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"
    SUSPENDED = "SUSPENDED"


class Contract(Base):
    """
    IFRS 15 Contract for revenue recognition.
    """

    __tablename__ = "contract"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "contract_number", name="uq_contract_number"
        ),
        {"schema": "ar"},
    )

    contract_id: Mapped[uuid.UUID] = mapped_column(
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
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id"),
        nullable=False,
    )

    contract_number: Mapped[str] = mapped_column(String(50), nullable=False)
    contract_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contract_type: Mapped[ContractType] = mapped_column(
        Enum(ContractType, name="contract_type"),
        nullable=False,
    )

    # Timeline
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Value
    total_contract_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Status
    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, name="contract_status"),
        nullable=False,
        default=ContractStatus.DRAFT,
    )
    approval_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
    )

    # IFRS 15 criteria
    is_enforceable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    has_commercial_substance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    collectability_assessment: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PROBABLE",
    )

    # Contract modifications (JSONB for history)
    modification_history: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Variable consideration
    variable_consideration: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Financing component
    significant_financing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    financing_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 5), nullable=True
    )

    # Non-cash consideration
    noncash_consideration: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    consideration_payable: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    terms_and_conditions: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
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
