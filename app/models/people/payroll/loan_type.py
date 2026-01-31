"""
Loan Type Model - Payroll Schema.

Configuration for different types of employee loans/advances.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.finance.gl.account import Account


class LoanCategory(str, enum.Enum):
    """Category of employee loan."""

    SALARY_ADVANCE = "SALARY_ADVANCE"  # Short-term advance against salary
    PERSONAL_LOAN = "PERSONAL_LOAN"  # Standard personal loan
    EQUIPMENT_LOAN = "EQUIPMENT_LOAN"  # Loan for equipment purchase
    EMERGENCY_LOAN = "EMERGENCY_LOAN"  # Emergency fund loan
    HOUSING_LOAN = "HOUSING_LOAN"  # Housing/rent assistance
    EDUCATION_LOAN = "EDUCATION_LOAN"  # Education/training loan


class InterestMethod(str, enum.Enum):
    """Method for calculating loan interest."""

    NONE = "NONE"  # No interest (e.g., salary advances)
    FLAT = "FLAT"  # Simple interest on principal
    REDUCING_BALANCE = "REDUCING_BALANCE"  # Interest on outstanding balance


class LoanType(Base):
    """
    Loan Type - Configuration for employee loan categories.

    Defines loan parameters like max amount, tenure, interest rates,
    and GL account mappings for different loan types.
    """

    __tablename__ = "loan_type"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "type_code",
            name="uq_loan_type_org_code",
        ),
        Index("idx_loan_type_org", "organization_id"),
        {"schema": "payroll"},
    )

    loan_type_id: Mapped[uuid.UUID] = mapped_column(
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

    # Type identification
    type_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Short code, e.g., SALARY_ADV, PERSONAL",
    )
    type_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name, e.g., Salary Advance",
    )
    category: Mapped[LoanCategory] = mapped_column(
        Enum(LoanCategory, name="loan_category"),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Loan limits
    max_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2),
        nullable=True,
        comment="Maximum loan amount (NULL = no limit)",
    )
    min_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Minimum loan amount",
    )
    max_tenure_months: Mapped[int] = mapped_column(
        Integer,
        default=12,
        comment="Maximum repayment period in months",
    )
    min_tenure_months: Mapped[int] = mapped_column(
        Integer,
        default=1,
        comment="Minimum repayment period in months",
    )

    # Interest configuration
    interest_method: Mapped[InterestMethod] = mapped_column(
        Enum(InterestMethod, name="interest_method"),
        default=InterestMethod.NONE,
    )
    default_interest_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        comment="Annual interest rate (percentage)",
    )

    # Eligibility
    min_service_months: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Minimum months of service required",
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        comment="Whether loan requires approval workflow",
    )

    # GL Account mappings
    loan_receivable_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Asset account for loans receivable (debit on disbursement)",
    )
    loan_disbursement_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Account to credit on disbursement (typically bank)",
    )
    interest_income_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Revenue account for interest income",
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    # Timestamps
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
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )

    # Relationships
    loan_receivable_account: Mapped[Optional["Account"]] = relationship(
        "Account",
        foreign_keys=[loan_receivable_account_id],
    )
    loan_disbursement_account: Mapped[Optional["Account"]] = relationship(
        "Account",
        foreign_keys=[loan_disbursement_account_id],
    )
    interest_income_account: Mapped[Optional["Account"]] = relationship(
        "Account",
        foreign_keys=[interest_income_account_id],
    )

    def __repr__(self) -> str:
        return f"<LoanType {self.type_code}: {self.type_name}>"
