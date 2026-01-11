"""
Payee Master Model.

Stores recognized payees/payers for auto-categorization of bank transactions.
"""

import enum
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PayeeType(str, enum.Enum):
    """Type of payee."""
    VENDOR = "VENDOR"       # Supplier/vendor payments
    CUSTOMER = "CUSTOMER"   # Customer receipts
    EMPLOYEE = "EMPLOYEE"   # Payroll, reimbursements
    BANK = "BANK"           # Bank fees, interest
    TAX = "TAX"             # Tax authorities
    UTILITY = "UTILITY"     # Utility companies
    OTHER = "OTHER"


class Payee(Base):
    """
    Payee Master entity.

    Stores recognized payees for auto-categorization of bank transactions.
    Learns from user categorization choices to suggest future matches.
    """

    __tablename__ = "payee"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "payee_name",
            name="uq_payee_name",
        ),
        {"schema": "banking"},
    )

    payee_id: Mapped[uuid4] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    organization_id: Mapped[uuid4] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    # Payee identification
    payee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    payee_type: Mapped[PayeeType] = mapped_column(
        Enum(PayeeType, name="payee_type", schema="banking"),
        nullable=False,
        default=PayeeType.OTHER,
    )

    # Matching patterns (for fuzzy matching)
    name_patterns: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Pipe-separated patterns for matching, e.g., 'AMAZON|AMZN|AWS'",
    )

    # Default categorization
    default_account_id: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Default GL account for transactions with this payee",
    )
    default_tax_code_id: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Default tax code for transactions with this payee",
    )

    # Linked entities (optional)
    supplier_id: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier.supplier_id"),
        nullable=True,
    )
    customer_id: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id"),
        nullable=True,
    )

    # Usage statistics
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_matched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by: Mapped[Optional[uuid4]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def matches_name(self, transaction_description: str) -> bool:
        """Check if a transaction description matches this payee."""
        if not transaction_description:
            return False

        desc_upper = transaction_description.upper()

        # Check main name
        if self.payee_name.upper() in desc_upper:
            return True

        # Check patterns
        if self.name_patterns:
            patterns = [p.strip().upper() for p in self.name_patterns.split("|")]
            for pattern in patterns:
                if pattern and pattern in desc_upper:
                    return True

        return False
