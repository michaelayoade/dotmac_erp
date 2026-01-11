"""
Quote Model - AR Schema.

Sales quotes/proposals that can be converted to invoices or sales orders.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    Date, DateTime, Enum, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, func, text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base


class QuoteStatus(str, enum.Enum):
    """Quote lifecycle status."""
    DRAFT = "DRAFT"
    SENT = "SENT"
    VIEWED = "VIEWED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONVERTED = "CONVERTED"
    VOID = "VOID"


class Quote(Base):
    """
    Sales quote/proposal for customers.

    Quotes can be:
    - Sent to customers for review
    - Accepted and converted to Invoice or Sales Order
    - Rejected or allowed to expire
    """

    __tablename__ = "quote"
    __table_args__ = (
        UniqueConstraint("organization_id", "quote_number", name="uq_quote_number"),
        Index("idx_quote_org_status", "organization_id", "status"),
        Index("idx_quote_customer", "customer_id"),
        Index("idx_quote_date", "organization_id", "quote_date"),
        {"schema": "ar"},
    )

    quote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Quote identification
    quote_number: Mapped[str] = mapped_column(String(30), nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Customer
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id"),
        nullable=False,
    )

    # Contact info (may differ from customer default)
    contact_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Dates
    quote_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)

    # Amounts
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Currency
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=settings.default_functional_currency_code,
    )
    exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(19, 10),
        nullable=False,
        default=Decimal("1"),
    )

    # Terms and conditions
    payment_terms_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.payment_terms.payment_terms_id"),
        nullable=True,
    )
    terms_and_conditions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Notes
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    customer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status
    status: Mapped[QuoteStatus] = mapped_column(
        Enum(QuoteStatus, name="quote_status"),
        nullable=False,
        default=QuoteStatus.DRAFT,
    )

    # Conversion tracking
    converted_to_invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.invoice.invoice_id"),
        nullable=True,
    )
    converted_to_so_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,  # FK added after SalesOrder model
    )
    converted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Workflow tracking
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    customer = relationship("Customer", foreign_keys=[customer_id], lazy="joined")
    payment_terms = relationship("PaymentTerms", foreign_keys=[payment_terms_id], lazy="joined")
    lines: Mapped[List["QuoteLine"]] = relationship(
        "QuoteLine",
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteLine.line_number",
    )
    converted_invoice = relationship("Invoice", foreign_keys=[converted_to_invoice_id], lazy="select")

    @property
    def is_expired(self) -> bool:
        """Check if quote has expired."""
        return self.valid_until < date.today() and self.status not in [
            QuoteStatus.ACCEPTED, QuoteStatus.CONVERTED, QuoteStatus.VOID
        ]

    @property
    def can_convert(self) -> bool:
        """Check if quote can be converted."""
        return self.status == QuoteStatus.ACCEPTED


class QuoteLine(Base):
    """
    Quote line item.
    """

    __tablename__ = "quote_line"
    __table_args__ = (
        Index("idx_quote_line_quote", "quote_id"),
        {"schema": "ar"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.quote.quote_id", ondelete="CASCADE"),
        nullable=False,
    )

    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Item details
    item_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)

    # Quantity and pricing
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("1"),
    )
    unit_of_measure: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )

    # Discounts
    discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0"),
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Tax
    tax_code_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_code.tax_code_id"),
        nullable=True,
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )

    # Line total
    line_total: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )

    # Revenue account
    revenue_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
    )

    # Dimensions
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=True,
    )
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
    )

    # Relationships
    quote = relationship("Quote", back_populates="lines")
    tax_code = relationship("TaxCode", lazy="joined")
    revenue_account = relationship("Account", foreign_keys=[revenue_account_id], lazy="joined")
    project = relationship("Project", lazy="joined")
    cost_center = relationship("CostCenter", lazy="joined")
