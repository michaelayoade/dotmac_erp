"""
Bank Account Model.

Represents a bank account linked to a GL cash/bank account for reconciliation.
"""

import enum
from datetime import date, datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as SAUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base

if TYPE_CHECKING:
    from app.models.finance.gl.account import Account


class BankAccountType(str, enum.Enum):
    """Type of bank account."""

    checking = "checking"
    savings = "savings"
    money_market = "money_market"
    credit_line = "credit_line"
    loan = "loan"
    other = "other"


class BankAccountStatus(str, enum.Enum):
    """Status of the bank account."""

    active = "active"
    inactive = "inactive"
    closed = "closed"
    suspended = "suspended"


class BankAccount(Base):
    """
    Bank Account entity.

    Represents a physical bank account that is linked to a GL account
    for cash management and reconciliation purposes.
    """

    __tablename__ = "bank_accounts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_number",
            "bank_code",
            name="uq_bank_account_number",
        ),
        Index(
            "uq_banking_bank_accounts_mono_account_id",
            "mono_account_id",
            unique=True,
            postgresql_where=text("mono_account_id IS NOT NULL"),
        ),
        {"schema": "banking"},
    )

    # Primary key
    bank_account_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Organization
    organization_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Bank details
    bank_name: Mapped[str] = mapped_column(String(200), nullable=False)
    bank_code: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # SWIFT/BIC
    branch_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Account details
    account_number: Mapped[str] = mapped_column(String(50), nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_type: Mapped[BankAccountType] = mapped_column(
        Enum(BankAccountType, name="bank_account_type", schema="banking"),
        nullable=False,
        default=BankAccountType.checking,
    )
    iban: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Currency
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=settings.default_functional_currency_code,
    )

    # GL Account linkage
    gl_account_id: Mapped[UUID] = mapped_column(
        SAUUID(as_uuid=True),
        ForeignKey("gl.account.account_id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Status
    status: Mapped[BankAccountStatus] = mapped_column(
        Enum(BankAccountStatus, name="bank_account_status", schema="banking"),
        nullable=False,
        default=BankAccountStatus.active,
    )

    # Balance tracking (last known from statement)
    last_statement_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )
    last_statement_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    last_reconciled_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    last_reconciled_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )

    # Contact/Notes
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # External provider linking
    mono_account_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Mono sync tracking (see alembic/versions/20260415_add_mono_sync_tracking.py)
    mono_sync_from_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    mono_last_transaction_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    mono_last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Last time a sync produced *evidence that data actually flowed* — lines
    # ingested, or Mono's indexer confirming a healthy pull. Distinct from
    # ``mono_last_synced_at``, which only records that the API answered:
    # ``/transactions`` serves Mono's cache, so a de-authorised account still
    # returns 200 with zero rows. Anything deciding "is this link alive?"
    # must read this, not ``mono_last_synced_at``.
    mono_last_ingest_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # True when Mono reports the *bank link itself* is broken (reauth needed,
    # or the indexer's pull failed). Distinct from a transient API error: a
    # 5xx clears the moment Mono answers again, whereas a dead link keeps
    # serving 200s from cache and would otherwise be cleared by its own
    # staleness. Only positive evidence — lines ingested, or Mono confirming
    # a healthy pull — resets this.
    mono_link_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    mono_last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    mono_sync_buffer_days: Mapped[int] = mapped_column(
        nullable=False, default=7, server_default="7"
    )

    # Flags
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_overdraft: Mapped[bool] = mapped_column(Boolean, default=False)
    overdraft_limit: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    created_by: Mapped[UUID | None] = mapped_column(SAUUID(as_uuid=True), nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(SAUUID(as_uuid=True), nullable=True)

    # Relationships
    gl_account: Mapped["Account"] = relationship(
        "Account",
        foreign_keys=[gl_account_id],
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<BankAccount {self.bank_name} - {self.account_number}>"

    @property
    def display_name(self) -> str:
        """Return display name for the account."""
        return f"{self.bank_name} - {self.account_name} ({self.account_number[-4:]})"

    @property
    def masked_account_number(self) -> str:
        """Return masked account number for security."""
        if len(self.account_number) > 4:
            return "*" * (len(self.account_number) - 4) + self.account_number[-4:]
        return self.account_number
