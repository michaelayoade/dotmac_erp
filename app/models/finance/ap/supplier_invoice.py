"""
Supplier Invoice Model - AP Schema.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.mixins import VersionedMixin


class SupplierInvoiceType(str, enum.Enum):
    STANDARD = "STANDARD"
    CREDIT_NOTE = "CREDIT_NOTE"
    DEBIT_NOTE = "DEBIT_NOTE"


class SupplierInvoiceStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    ON_HOLD = "ON_HOLD"
    VOID = "VOID"
    DISPUTED = "DISPUTED"


class SupplierInvoice(Base, VersionedMixin):
    """
    Supplier invoice (AP invoice).

    Includes optimistic locking via version field (VersionedMixin).
    """

    __tablename__ = "supplier_invoice"
    __table_args__ = (
        UniqueConstraint("organization_id", "invoice_number", name="uq_supplier_invoice"),
        Index("idx_supplier_invoice_supplier", "supplier_id"),
        Index("idx_supplier_invoice_status", "organization_id", "status"),
        Index(
            "idx_supplier_invoice_due_date",
            "organization_id",
            "due_date",
            postgresql_where="status NOT IN ('PAID', 'VOID')",
        ),
        {"schema": "ap"},
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
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier.supplier_id"),
        nullable=False,
    )

    invoice_number: Mapped[str] = mapped_column(String(30), nullable=False)
    supplier_invoice_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    invoice_type: Mapped[SupplierInvoiceType] = mapped_column(
        Enum(SupplierInvoiceType, name="supplier_invoice_type"),
        nullable=False,
    )

    # Dates
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 10), nullable=True)
    exchange_rate_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Amounts
    subtotal: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    # balance_due is computed
    functional_currency_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Status
    status: Mapped[SupplierInvoiceStatus] = mapped_column(
        Enum(SupplierInvoiceStatus, name="supplier_invoice_status"),
        nullable=False,
        default=SupplierInvoiceStatus.DRAFT,
    )

    # Accounting
    ap_control_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posting_batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Posting status
    posting_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="NOT_POSTED",
    )

    # Three-way match
    three_way_match_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        comment="PENDING, MATCHED, UNMATCHED, EXCEPTION",
    )

    # Withholding
    withholding_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Prepayment
    is_prepayment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prepayment_applied: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Intercompany
    is_intercompany: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    intercompany_org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # SoD tracking
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    submitted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    posted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    approval_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    correlation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

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

    # Relationships
    lines: Mapped[list["SupplierInvoiceLine"]] = relationship(
        "SupplierInvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )

    @property
    def balance_due(self) -> Decimal:
        return self.total_amount - self.amount_paid


# Forward reference
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine  # noqa: E402
