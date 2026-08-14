"""
Supplier Invoice Model - AP Schema.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Computed,
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
    REJECTED = "REJECTED"
    VOID = "VOID"
    DISPUTED = "DISPUTED"

    @classmethod
    def gl_impacting(cls) -> frozenset["SupplierInvoiceStatus"]:
        """Statuses where the invoice has been posted to the General Ledger."""
        return frozenset({cls.POSTED, cls.PARTIALLY_PAID, cls.PAID})

    @classmethod
    def outstanding(cls) -> frozenset["SupplierInvoiceStatus"]:
        """Statuses where the invoice has an unpaid balance."""
        return frozenset({cls.APPROVED, cls.POSTED, cls.PARTIALLY_PAID, cls.ON_HOLD})

    @classmethod
    def terminal(cls) -> frozenset["SupplierInvoiceStatus"]:
        """Statuses where the invoice is fully settled or cancelled."""
        return frozenset({cls.PAID, cls.REJECTED, cls.VOID})


class PostingStatus(str, enum.Enum):
    NOT_POSTED = "NOT_POSTED"
    PENDING = "PENDING"
    POSTED = "POSTED"


class ThreeWayMatchStatus(str, enum.Enum):
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    UNMATCHED = "UNMATCHED"
    EXCEPTION = "EXCEPTION"


class InventoryReceiptMode(str, enum.Enum):
    NONE = "NONE"
    AUTO_RECEIVE = "AUTO_RECEIVE"
    STORE_APPROVAL = "STORE_APPROVAL"


class SupplierInvoice(Base, VersionedMixin):
    """
    Supplier invoice (AP invoice).

    Includes optimistic locking via version field (VersionedMixin).
    """

    __tablename__ = "supplier_invoice"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "invoice_number", name="uq_supplier_invoice"
        ),
        Index(
            "uq_supplier_invoice_source_correlation",
            "organization_id",
            "correlation_id",
            unique=True,
            postgresql_where=text("correlation_id LIKE 'sub-invoice:%'"),
        ),
        Index("idx_supplier_invoice_supplier", "supplier_id"),
        Index("idx_supplier_invoice_status", "organization_id", "status"),
        Index(
            "idx_supplier_invoice_due_date",
            "organization_id",
            "due_date",
            postgresql_where="status NOT IN ('PAID', 'VOID')",
        ),
        Index("idx_supplier_invoice_vehicle", "organization_id", "vehicle_id"),
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
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier.supplier_id"),
        nullable=False,
    )

    invoice_number: Mapped[str] = mapped_column(String(30), nullable=False)
    supplier_invoice_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    invoice_type: Mapped[SupplierInvoiceType] = mapped_column(
        Enum(SupplierInvoiceType, name="supplier_invoice_type"),
        nullable=False,
    )

    # Dates
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Optional fleet linkage (for vehicle cost reporting)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleet.vehicle.vehicle_id", ondelete="SET NULL"),
        nullable=True,
    )

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
    # Derived by the DATABASE, never written here — see ADR-0016. Was a Python
    # @property, which could not be queried, so services hand-wrote the
    # subtraction as SQL. One definition now serves the ORM and SQL alike.
    # A generated column is None until flush; SQLAlchemy refetches it after
    # every flush, including updates to the operands.
    balance_due: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        Computed("total_amount - amount_paid", persisted=True),
        nullable=False,
    )
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
        ForeignKey("gl.account.account_id"),
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
    posting_status: Mapped[PostingStatus] = mapped_column(
        Enum(PostingStatus, name="posting_status", schema="ap"),
        nullable=False,
        default=PostingStatus.NOT_POSTED,
    )

    # Three-way match
    three_way_match_status: Mapped[ThreeWayMatchStatus] = mapped_column(
        Enum(ThreeWayMatchStatus, name="three_way_match_status", schema="ap"),
        nullable=False,
        default=ThreeWayMatchStatus.PENDING,
    )

    # Withholding
    withholding_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    withholding_tax_code_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Stamp duty
    stamp_duty_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    stamp_duty_code_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Prepayment
    is_prepayment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prepayment_applied: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Inventory auto-receipt
    auto_create_inventory_receipt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    inventory_receipt_mode: Mapped[InventoryReceiptMode] = mapped_column(
        Enum(InventoryReceiptMode, name="supplier_invoice_inventory_receipt_mode"),
        nullable=False,
        default=InventoryReceiptMode.NONE,
        server_default=InventoryReceiptMode.NONE.value,
    )

    # Intercompany
    is_intercompany: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    intercompany_org_id: Mapped[uuid.UUID | None] = mapped_column(
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

    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    lines: Mapped[list["SupplierInvoiceLine"]] = relationship(
        "SupplierInvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )


# Forward reference
from app.models.finance.ap.supplier_invoice_line import (  # noqa: E402
    SupplierInvoiceLine,
)
