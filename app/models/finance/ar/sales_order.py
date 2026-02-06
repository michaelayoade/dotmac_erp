"""
Sales Order Model - AR Schema.

Sales orders with fulfillment tracking and invoice generation.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    Date,
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

from app.config import settings
from app.db import Base


class SOStatus(str, enum.Enum):
    """Sales Order lifecycle status."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    CONFIRMED = "CONFIRMED"  # Confirmed with customer
    IN_PROGRESS = "IN_PROGRESS"  # Partially shipped
    SHIPPED = "SHIPPED"  # Fully shipped
    COMPLETED = "COMPLETED"  # Shipped and invoiced
    CANCELLED = "CANCELLED"
    ON_HOLD = "ON_HOLD"


class FulfillmentStatus(str, enum.Enum):
    """Line fulfillment status."""

    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    FULFILLED = "FULFILLED"
    BACKORDERED = "BACKORDERED"
    CANCELLED = "CANCELLED"


class SalesOrder(Base):
    """
    Sales Order for customer orders.

    Supports:
    - Order to delivery workflow
    - Partial shipments
    - Partial invoicing
    - Back orders
    - Delivery scheduling
    """

    __tablename__ = "sales_order"
    __table_args__ = (
        UniqueConstraint("organization_id", "so_number", name="uq_sales_order_number"),
        Index("idx_so_org_status", "organization_id", "status"),
        Index("idx_so_customer", "customer_id"),
        Index("idx_so_date", "organization_id", "order_date"),
        {"schema": "ar"},
    )

    so_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # SO identification
    so_number: Mapped[str] = mapped_column(String(30), nullable=False)
    customer_po_number: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Customer
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id"),
        nullable=False,
    )

    # Source (if converted from quote)
    quote_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.quote.quote_id"),
        nullable=True,
    )

    # Dates
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    promised_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Shipping info
    ship_to_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    ship_to_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ship_to_city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ship_to_state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ship_to_postal_code: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )
    ship_to_country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    shipping_method: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

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
    shipping_amount: Mapped[Decimal] = mapped_column(
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

    # Invoiced tracking
    invoiced_amount: Mapped[Decimal] = mapped_column(
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

    # Terms
    payment_terms_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.payment_terms.payment_terms_id"),
        nullable=True,
    )

    # Status
    status: Mapped[SOStatus] = mapped_column(
        Enum(SOStatus, name="so_status"),
        nullable=False,
        default=SOStatus.DRAFT,
    )

    # Flags
    is_backorder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_partial_shipment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # Notes
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    customer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Workflow tracking
    submitted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    customer = relationship("Customer", foreign_keys=[customer_id], lazy="joined")
    quote = relationship("Quote", foreign_keys=[quote_id], lazy="select")
    payment_terms = relationship(
        "PaymentTerms", foreign_keys=[payment_terms_id], lazy="joined"
    )
    lines: Mapped[List["SalesOrderLine"]] = relationship(
        "SalesOrderLine",
        back_populates="sales_order",
        cascade="all, delete-orphan",
        order_by="SalesOrderLine.line_number",
    )
    shipments: Mapped[List["Shipment"]] = relationship(
        "Shipment",
        back_populates="sales_order",
        cascade="all, delete-orphan",
        order_by="Shipment.shipment_date.desc()",
    )

    @property
    def fulfillment_percent(self) -> Decimal:
        """Calculate overall fulfillment percentage."""
        if not self.lines:
            return Decimal("0")
        total_ordered = sum(line.quantity_ordered for line in self.lines)
        total_shipped = sum(line.quantity_shipped for line in self.lines)
        if total_ordered == 0:
            return Decimal("0")
        return (total_shipped / total_ordered) * 100

    @property
    def is_fully_shipped(self) -> bool:
        """Check if all lines are fully shipped."""
        return all(
            line.quantity_shipped >= line.quantity_ordered for line in self.lines
        )

    @property
    def is_fully_invoiced(self) -> bool:
        """Check if all lines are fully invoiced."""
        return all(
            line.quantity_invoiced >= line.quantity_ordered for line in self.lines
        )


class SalesOrderLine(Base):
    """
    Sales Order line item with fulfillment tracking.
    """

    __tablename__ = "sales_order_line"
    __table_args__ = (
        Index("idx_so_line_so", "so_id"),
        {"schema": "ar"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    so_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.sales_order.so_id", ondelete="CASCADE"),
        nullable=False,
    )

    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Item details
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inv.item.item_id"),
        nullable=True,
    )
    item_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)

    # Quantity tracking
    quantity_ordered: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )
    quantity_shipped: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    quantity_invoiced: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    quantity_backordered: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    unit_of_measure: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Pricing
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

    # Fulfillment status
    fulfillment_status: Mapped[FulfillmentStatus] = mapped_column(
        Enum(FulfillmentStatus, name="so_fulfillment_status"),
        nullable=False,
        default=FulfillmentStatus.PENDING,
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

    # Dates
    requested_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    promised_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Relationships
    sales_order = relationship("SalesOrder", back_populates="lines")
    item = relationship("Item", lazy="joined")
    tax_code = relationship("TaxCode", lazy="joined")
    revenue_account = relationship(
        "Account", foreign_keys=[revenue_account_id], lazy="joined"
    )
    project = relationship("Project", lazy="joined")
    cost_center = relationship("CostCenter", lazy="joined")

    @property
    def quantity_remaining(self) -> Decimal:
        """Quantity remaining to ship."""
        return self.quantity_ordered - self.quantity_shipped

    @property
    def quantity_to_invoice(self) -> Decimal:
        """Quantity shipped but not yet invoiced."""
        return self.quantity_shipped - self.quantity_invoiced


class Shipment(Base):
    """
    Shipment/delivery record for sales orders.
    """

    __tablename__ = "shipment"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "shipment_number", name="uq_shipment_number"
        ),
        Index("idx_shipment_so", "so_id"),
        Index("idx_shipment_date", "organization_id", "shipment_date"),
        {"schema": "ar"},
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    shipment_number: Mapped[str] = mapped_column(String(30), nullable=False)

    so_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.sales_order.so_id"),
        nullable=False,
    )

    # Shipment details
    shipment_date: Mapped[date] = mapped_column(Date, nullable=False)
    carrier: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tracking_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    shipping_method: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Shipping address (snapshot from SO)
    ship_to_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    ship_to_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status
    is_delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    sales_order = relationship("SalesOrder", back_populates="shipments")
    lines: Mapped[List["ShipmentLine"]] = relationship(
        "ShipmentLine",
        back_populates="shipment",
        cascade="all, delete-orphan",
    )


class ShipmentLine(Base):
    """
    Shipment line linking to SO line with quantity shipped.
    """

    __tablename__ = "shipment_line"
    __table_args__ = (
        Index("idx_shipment_line_shipment", "shipment_id"),
        Index("idx_shipment_line_so_line", "so_line_id"),
        {"schema": "ar"},
    )

    shipment_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.shipment.shipment_id", ondelete="CASCADE"),
        nullable=False,
    )

    so_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.sales_order_line.line_id"),
        nullable=False,
    )

    quantity_shipped: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )

    # Lot/serial tracking (optional)
    lot_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    serial_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relationships
    shipment = relationship("Shipment", back_populates="lines")
    so_line = relationship("SalesOrderLine", lazy="joined")
