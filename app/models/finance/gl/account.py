"""
Account Model - GL Schema.
Chart of accounts.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AccountType(str, enum.Enum):
    CONTROL = "CONTROL"
    POSTING = "POSTING"
    STATISTICAL = "STATISTICAL"


class NormalBalance(str, enum.Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class Account(Base):
    """
    GL Account (Chart of Accounts entry).
    """

    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("organization_id", "account_code", name="uq_account_code"),
        Index("idx_account_category", "category_id"),
        Index(
            "idx_account_active", "organization_id", "is_active", "is_posting_allowed"
        ),
        {"schema": "gl"},
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
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
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account_category.category_id"),
        nullable=False,
    )

    # Identity
    account_code: Mapped[str] = mapped_column(String(20), nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    search_terms: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Space-separated keywords",
    )

    # Classification
    account_type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type"),
        nullable=False,
        default=AccountType.POSTING,
    )
    normal_balance: Mapped[NormalBalance] = mapped_column(
        Enum(NormalBalance, name="normal_balance"),
        nullable=False,
    )

    # Currency
    is_multi_currency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    default_currency_code: Mapped[Optional[str]] = mapped_column(
        String(3), nullable=True
    )

    # Control flags
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_posting_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    is_budgetable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_reconciliation_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Subledger linking
    subledger_type: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="AR, AP, INVENTORY, ASSET, BANK",
    )

    # IFRS specific
    is_cash_equivalent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="For IAS 7 Cash Flow Statement",
    )
    is_financial_instrument: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Audit fields
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who created/imported this account",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who last updated this account",
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    category: Mapped["AccountCategory"] = relationship(
        "AccountCategory",
        back_populates="accounts",
    )


# Forward reference
from app.models.finance.gl.account_category import AccountCategory  # noqa: E402
