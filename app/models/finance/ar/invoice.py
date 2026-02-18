"""
Invoice Model - AR Schema.
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import VersionedMixin


class InvoiceType(str, enum.Enum):
    STANDARD = "STANDARD"
    CREDIT_NOTE = "CREDIT_NOTE"
    DEBIT_NOTE = "DEBIT_NOTE"
    PROFORMA = "PROFORMA"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    VOID = "VOID"
    DISPUTED = "DISPUTED"

    @classmethod
    def gl_impacting(cls) -> frozenset["InvoiceStatus"]:
        """Statuses where the invoice has been posted to the General Ledger."""
        return frozenset({cls.POSTED, cls.PARTIALLY_PAID, cls.PAID, cls.OVERDUE})

    @classmethod
    def outstanding(cls) -> frozenset["InvoiceStatus"]:
        """Statuses where the invoice has an unpaid balance."""
        return frozenset({cls.POSTED, cls.PARTIALLY_PAID, cls.OVERDUE})

    @classmethod
    def terminal(cls) -> frozenset["InvoiceStatus"]:
        """Statuses where the invoice is fully settled or cancelled."""
        return frozenset({cls.PAID, cls.VOID})


class Invoice(Base, VersionedMixin):
    """
    AR Invoice.

    Includes optimistic locking via version field (VersionedMixin).
    """

    __tablename__ = "invoice"
    __table_args__ = (
        UniqueConstraint("organization_id", "invoice_number", name="uq_invoice_number"),
        Index("idx_invoice_customer", "customer_id"),
        Index("idx_invoice_status", "organization_id", "status"),
        Index(
            "idx_invoice_due_date",
            "organization_id",
            "due_date",
            postgresql_where="status NOT IN ('PAID', 'VOID')",
        ),
        Index(
            "idx_invoice_correlation",
            "correlation_id",
            postgresql_where="correlation_id IS NOT NULL",
        ),
        {"schema": "ar"},
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id"),
        nullable=False,
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.contract.contract_id"),
        nullable=True,
    )

    invoice_number: Mapped[str] = mapped_column(String(30), nullable=False)
    invoice_type: Mapped[InvoiceType] = mapped_column(
        Enum(InvoiceType, name="invoice_type"),
        nullable=False,
    )

    # Dates
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    exchange_rate_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Amounts
    subtotal: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    # balance_due is computed: total_amount - amount_paid
    functional_currency_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Status
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"),
        nullable=False,
        default=InvoiceStatus.DRAFT,
    )

    # References
    payment_terms_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    billing_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    shipping_address: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Accounting
    ar_control_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posting_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Posting status
    posting_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="NOT_POSTED",
        comment="NOT_POSTED, POSTING, POSTED, FAILED",
    )

    # IFRS 9 ECL
    ecl_provision_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Intercompany
    is_intercompany: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    intercompany_org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Source
    source_document_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # SoD tracking
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    posted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    voided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # External system IDs for sync/dedup
    erpnext_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    splynx_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    splynx_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    # Relationships
    customer = relationship("Customer", foreign_keys=[customer_id], lazy="joined")
    lines: Mapped[list["InvoiceLine"]] = relationship(
        "InvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )

    @property
    def balance_due(self) -> Decimal:
        return self.total_amount - self.amount_paid


# Forward reference
from app.models.finance.ar.invoice_line import InvoiceLine  # noqa: E402
