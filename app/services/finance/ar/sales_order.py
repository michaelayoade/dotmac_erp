"""
Sales Order Service.

Business logic for sales orders with fulfillment and invoicing.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.sales_order import (
    FulfillmentStatus,
    SalesOrder,
    SalesOrderLine,
    Shipment,
    ShipmentLine,
    SOStatus,
)
from app.models.finance.core_config import SequenceType
from app.services.common import coerce_uuid
from app.services.finance.common import SyncNumberingService

logger = logging.getLogger(__name__)


class SalesOrderService:
    """Service for sales order operations."""

    @staticmethod
    def generate_so_number(db: Session, organization_id: UUID) -> str:
        """Generate unique SO number using configurable sequence."""
        numbering_service = SyncNumberingService(db)
        return numbering_service.generate_next_number(
            organization_id=organization_id,
            sequence_type=SequenceType.SALES_ORDER,
        )

    @staticmethod
    def create(
        db: Session,
        organization_id: str,
        customer_id: str,
        order_date: date,
        created_by: str,
        currency_code: str = settings.default_functional_currency_code,
        exchange_rate: Decimal = Decimal("1"),
        customer_po_number: str | None = None,
        reference: str | None = None,
        requested_date: date | None = None,
        promised_date: date | None = None,
        payment_terms_id: str | None = None,
        ship_to_name: str | None = None,
        ship_to_address: str | None = None,
        ship_to_city: str | None = None,
        ship_to_state: str | None = None,
        ship_to_postal_code: str | None = None,
        ship_to_country: str | None = None,
        shipping_method: str | None = None,
        allow_partial_shipment: bool = True,
        internal_notes: str | None = None,
        customer_notes: str | None = None,
        lines: list[dict] | None = None,
    ) -> SalesOrder:
        """Create a new sales order."""
        org_id = coerce_uuid(organization_id)

        so = SalesOrder(
            organization_id=org_id,
            so_number=SalesOrderService.generate_so_number(db, org_id),
            customer_id=coerce_uuid(customer_id),
            order_date=order_date,
            customer_po_number=customer_po_number,
            reference=reference,
            requested_date=requested_date,
            promised_date=promised_date,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            payment_terms_id=coerce_uuid(payment_terms_id)
            if payment_terms_id
            else None,
            ship_to_name=ship_to_name,
            ship_to_address=ship_to_address,
            ship_to_city=ship_to_city,
            ship_to_state=ship_to_state,
            ship_to_postal_code=ship_to_postal_code,
            ship_to_country=ship_to_country,
            shipping_method=shipping_method,
            allow_partial_shipment=allow_partial_shipment,
            internal_notes=internal_notes,
            customer_notes=customer_notes,
            status=SOStatus.DRAFT,
            created_by=coerce_uuid(created_by),
        )

        db.add(so)
        db.flush()

        # Add lines
        if lines:
            SalesOrderService._add_lines(db, so, lines)

        # Recalculate totals
        SalesOrderService._recalculate_totals(so)
        db.flush()
        db.commit()
        db.refresh(so)

        return so

    @staticmethod
    def _add_lines(db: Session, so: SalesOrder, lines: list[dict]) -> None:
        """Add lines to sales order."""
        for idx, line_data in enumerate(lines, start=1):
            quantity = Decimal(
                str(line_data.get("quantity_ordered", line_data.get("quantity", 1)))
            )
            unit_price = Decimal(str(line_data.get("unit_price", 0)))
            discount_percent = Decimal(str(line_data.get("discount_percent", 0)))
            discount_amount = Decimal(str(line_data.get("discount_amount", 0)))
            tax_amount = Decimal(str(line_data.get("tax_amount", 0)))

            # Calculate line total
            gross = quantity * unit_price
            if discount_percent > 0:
                discount_amount = gross * (discount_percent / 100)
            net = gross - discount_amount
            line_total = net + tax_amount

            line = SalesOrderLine(
                so_id=so.so_id,
                line_number=idx,
                item_id=coerce_uuid(line_data["item_id"])
                if line_data.get("item_id")
                else None,
                item_code=line_data.get("item_code"),
                description=line_data["description"],
                quantity_ordered=quantity,
                unit_of_measure=line_data.get("unit_of_measure"),
                unit_price=unit_price,
                discount_percent=discount_percent,
                discount_amount=discount_amount,
                tax_code_id=coerce_uuid(line_data["tax_code_id"])
                if line_data.get("tax_code_id")
                else None,
                tax_amount=tax_amount,
                line_total=line_total,
                revenue_account_id=coerce_uuid(line_data["revenue_account_id"])
                if line_data.get("revenue_account_id")
                else None,
                project_id=coerce_uuid(line_data["project_id"])
                if line_data.get("project_id")
                else None,
                cost_center_id=coerce_uuid(line_data["cost_center_id"])
                if line_data.get("cost_center_id")
                else None,
                requested_date=line_data.get("requested_date"),
                promised_date=line_data.get("promised_date"),
                fulfillment_status=FulfillmentStatus.PENDING,
            )
            db.add(line)

    @staticmethod
    def _recalculate_totals(so: SalesOrder) -> None:
        """Recalculate SO totals from lines."""
        subtotal = Decimal("0")
        discount = Decimal("0")
        tax = Decimal("0")

        for line in so.lines:
            gross = line.quantity_ordered * line.unit_price
            subtotal += gross
            discount += line.discount_amount
            tax += line.tax_amount

        so.subtotal = subtotal
        so.discount_amount = discount
        so.tax_amount = tax
        so.total_amount = subtotal - discount + tax + so.shipping_amount

    @staticmethod
    def submit(
        db: Session,
        so_id: str,
        submitted_by: str,
    ) -> SalesOrder:
        """Submit SO for approval."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so:
            raise ValueError("Sales order not found")

        if so.status != SOStatus.DRAFT:
            raise ValueError(f"Cannot submit SO in {so.status.value} status")

        so.status = SOStatus.SUBMITTED
        so.submitted_by = coerce_uuid(submitted_by)
        so.submitted_at = datetime.utcnow()

        db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=so.organization_id,
                entity_type="SALES_ORDER",
                entity_id=so.so_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "DRAFT"},
                new_values={"status": "SUBMITTED"},
                user_id=coerce_uuid(submitted_by),
            )
        except Exception:
            pass

        db.commit()
        db.refresh(so)
        return so

    @staticmethod
    def approve(
        db: Session,
        so_id: str,
        approved_by: str,
    ) -> SalesOrder:
        """Approve SO."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so:
            raise ValueError("Sales order not found")

        if so.status != SOStatus.SUBMITTED:
            raise ValueError(f"Cannot approve SO in {so.status.value} status")

        so.status = SOStatus.APPROVED
        so.approved_by = coerce_uuid(approved_by)
        so.approved_at = datetime.utcnow()

        db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=so.organization_id,
                entity_type="SALES_ORDER",
                entity_id=so.so_id,
                event="ON_APPROVAL",
                old_values={"status": "SUBMITTED"},
                new_values={"status": "APPROVED"},
                user_id=coerce_uuid(approved_by),
            )
        except Exception:
            pass

        db.commit()
        db.refresh(so)
        return so

    @staticmethod
    def confirm(
        db: Session,
        so_id: str,
    ) -> SalesOrder:
        """Confirm SO with customer."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so:
            raise ValueError("Sales order not found")

        if so.status != SOStatus.APPROVED:
            raise ValueError(f"Cannot confirm SO in {so.status.value} status")

        so.status = SOStatus.CONFIRMED
        so.confirmed_at = datetime.utcnow()

        db.flush()
        db.commit()
        db.refresh(so)
        return so

    @staticmethod
    def create_shipment(
        db: Session,
        so_id: str,
        shipment_date: date,
        created_by: str,
        line_quantities: list[dict],  # [{"line_id": "...", "quantity": 10}, ...]
        carrier: str | None = None,
        tracking_number: str | None = None,
        shipping_method: str | None = None,
        notes: str | None = None,
    ) -> Shipment:
        """Create a shipment for sales order lines."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so:
            raise ValueError("Sales order not found")

        if so.status not in [SOStatus.CONFIRMED, SOStatus.IN_PROGRESS]:
            raise ValueError(f"Cannot ship SO in {so.status.value} status")

        org_id = so.organization_id
        user_id = coerce_uuid(created_by)

        # Generate shipment number using configurable sequence
        numbering_service = SyncNumberingService(db)
        shipment_number = numbering_service.generate_next_number(
            organization_id=org_id,
            sequence_type=SequenceType.SHIPMENT,
        )

        # Create shipment
        shipment = Shipment(
            organization_id=org_id,
            shipment_number=shipment_number,
            so_id=so.so_id,
            shipment_date=shipment_date,
            carrier=carrier,
            tracking_number=tracking_number,
            shipping_method=shipping_method or so.shipping_method,
            ship_to_name=so.ship_to_name,
            ship_to_address=so.ship_to_address,
            notes=notes,
            created_by=user_id,
        )
        db.add(shipment)
        db.flush()

        # Process line quantities
        for lq in line_quantities:
            line_id = coerce_uuid(lq["line_id"])
            qty_to_ship = Decimal(str(lq["quantity"]))

            so_line = db.get(SalesOrderLine, line_id)
            if not so_line or so_line.so_id != so.so_id:
                raise ValueError(f"Invalid line: {lq['line_id']}")

            remaining = so_line.quantity_ordered - so_line.quantity_shipped
            if qty_to_ship > remaining:
                raise ValueError(
                    f"Cannot ship {qty_to_ship} for line {so_line.line_number}, "
                    f"only {remaining} remaining"
                )

            # Create shipment line
            ship_line = ShipmentLine(
                shipment_id=shipment.shipment_id,
                so_line_id=line_id,
                quantity_shipped=qty_to_ship,
                lot_number=lq.get("lot_number"),
                serial_number=lq.get("serial_number"),
            )
            db.add(ship_line)

            # Update SO line
            so_line.quantity_shipped += qty_to_ship

            # Update fulfillment status
            if so_line.quantity_shipped >= so_line.quantity_ordered:
                so_line.fulfillment_status = FulfillmentStatus.FULFILLED
            elif so_line.quantity_shipped > 0:
                so_line.fulfillment_status = FulfillmentStatus.PARTIAL

        # Update SO status
        if so.is_fully_shipped:
            so.status = SOStatus.SHIPPED
        else:
            so.status = SOStatus.IN_PROGRESS

        db.flush()
        db.commit()
        db.refresh(shipment)
        db.refresh(so)
        return shipment

    @staticmethod
    def mark_delivered(
        db: Session,
        shipment_id: str,
    ) -> Shipment:
        """Mark shipment as delivered."""
        shipment = db.get(Shipment, coerce_uuid(shipment_id))
        if not shipment:
            raise ValueError("Shipment not found")

        shipment.is_delivered = True
        shipment.delivered_at = datetime.utcnow()

        db.flush()
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def create_invoice_from_so(
        db: Session,
        so_id: str,
        created_by: str,
        invoice_date: date | None = None,
        line_quantities: list[dict]
        | None = None,  # If None, invoice all shipped but not invoiced
    ) -> Invoice:
        """Create invoice from shipped SO lines."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so:
            raise ValueError("Sales order not found")

        if so.status not in [
            SOStatus.IN_PROGRESS,
            SOStatus.SHIPPED,
            SOStatus.COMPLETED,
        ]:
            raise ValueError(f"Cannot invoice SO in {so.status.value} status")

        user_id = coerce_uuid(created_by)
        inv_date = invoice_date or date.today()

        # Generate invoice number using configurable sequence
        numbering_service = SyncNumberingService(db)
        invoice_number = numbering_service.generate_next_number(
            organization_id=so.organization_id,
            sequence_type=SequenceType.INVOICE,
        )

        # Determine quantities to invoice
        lines_to_invoice = []
        if line_quantities:
            # Specific lines/quantities
            for lq in line_quantities:
                line_id = coerce_uuid(lq["line_id"])
                qty = Decimal(str(lq["quantity"]))
                so_line = db.get(SalesOrderLine, line_id)
                if so_line and so_line.so_id == so.so_id:
                    available = so_line.quantity_shipped - so_line.quantity_invoiced
                    if qty > available:
                        raise ValueError(
                            f"Cannot invoice {qty} for line {so_line.line_number}, "
                            f"only {available} shipped and not yet invoiced"
                        )
                    lines_to_invoice.append((so_line, qty))
        else:
            # Invoice all shipped but not invoiced
            for so_line in so.lines:
                available = so_line.quantity_shipped - so_line.quantity_invoiced
                if available > 0:
                    lines_to_invoice.append((so_line, available))

        if not lines_to_invoice:
            raise ValueError("No lines to invoice")

        # Calculate invoice totals
        subtotal = Decimal("0")
        discount = Decimal("0")
        tax = Decimal("0")

        for so_line, qty in lines_to_invoice:
            ratio = (
                qty / so_line.quantity_ordered
                if so_line.quantity_ordered
                else Decimal("1")
            )
            line_gross = qty * so_line.unit_price
            line_discount = so_line.discount_amount * ratio
            line_tax = so_line.tax_amount * ratio
            subtotal += line_gross
            discount += line_discount
            tax += line_tax

        # Create invoice
        invoice = Invoice(
            organization_id=so.organization_id,
            invoice_number=invoice_number,
            invoice_type=InvoiceType.STANDARD,
            customer_id=so.customer_id,
            invoice_date=inv_date,
            due_date=inv_date,  # Should be calculated from payment terms
            currency_code=so.currency_code,
            exchange_rate=so.exchange_rate,
            payment_terms_id=so.payment_terms_id,
            subtotal=subtotal,
            discount_amount=discount,
            tax_amount=tax,
            total_amount=subtotal - discount + tax,
            reference=f"SO: {so.so_number}",
            customer_po_number=so.customer_po_number,
            status=InvoiceStatus.DRAFT,
            created_by=user_id,
        )
        db.add(invoice)
        db.flush()

        # Create invoice lines
        for line_num, (so_line, qty) in enumerate(lines_to_invoice, start=1):
            ratio = (
                qty / so_line.quantity_ordered
                if so_line.quantity_ordered
                else Decimal("1")
            )

            iline = InvoiceLine(
                invoice_id=invoice.invoice_id,
                line_number=line_num,
                description=so_line.description,
                quantity=qty,
                unit_price=so_line.unit_price,
                discount_percent=so_line.discount_percent,
                discount_amount=so_line.discount_amount * ratio,
                tax_code_id=so_line.tax_code_id,
                tax_amount=so_line.tax_amount * ratio,
                line_total=(qty * so_line.unit_price)
                - (so_line.discount_amount * ratio)
                + (so_line.tax_amount * ratio),
                revenue_account_id=so_line.revenue_account_id,
                project_id=so_line.project_id,
                cost_center_id=so_line.cost_center_id,
            )
            db.add(iline)

            # Update SO line
            so_line.quantity_invoiced += qty

        # Update SO invoiced amount
        so.invoiced_amount += invoice.total_amount

        # Check if SO is fully invoiced
        if so.is_fully_invoiced and so.is_fully_shipped:
            so.status = SOStatus.COMPLETED
            so.completed_at = datetime.utcnow()

        db.flush()
        db.commit()
        db.refresh(invoice)
        db.refresh(so)
        return invoice

    @staticmethod
    def cancel(
        db: Session,
        so_id: str,
        cancelled_by: str,
        reason: str | None = None,
    ) -> SalesOrder:
        """Cancel a sales order."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so:
            raise ValueError("Sales order not found")

        if so.status in [SOStatus.SHIPPED, SOStatus.COMPLETED]:
            raise ValueError(f"Cannot cancel SO in {so.status.value} status")

        # Check if any shipments exist
        if so.shipments:
            raise ValueError("Cannot cancel SO with existing shipments")

        so.status = SOStatus.CANCELLED
        so.cancelled_at = datetime.utcnow()
        so.cancellation_reason = reason
        so.updated_by = coerce_uuid(cancelled_by)

        # Cancel all lines
        for line in so.lines:
            line.fulfillment_status = FulfillmentStatus.CANCELLED

        db.flush()
        db.commit()
        db.refresh(so)
        return so

    @staticmethod
    def hold(
        db: Session,
        so_id: str,
        held_by: str,
    ) -> SalesOrder:
        """Put SO on hold."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so:
            raise ValueError("Sales order not found")

        if so.status in [SOStatus.COMPLETED, SOStatus.CANCELLED]:
            raise ValueError(f"Cannot hold SO in {so.status.value} status")

        so.status = SOStatus.ON_HOLD
        so.updated_by = coerce_uuid(held_by)
        so.updated_at = datetime.utcnow()

        db.flush()
        db.commit()
        db.refresh(so)
        return so

    @staticmethod
    def release_hold(
        db: Session,
        so_id: str,
        released_by: str,
    ) -> SalesOrder:
        """Release SO from hold."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so:
            raise ValueError("Sales order not found")

        if so.status != SOStatus.ON_HOLD:
            raise ValueError("SO is not on hold")

        # Determine appropriate status
        if so.is_fully_shipped:
            so.status = SOStatus.SHIPPED
        elif any(line.quantity_shipped > 0 for line in so.lines):
            so.status = SOStatus.IN_PROGRESS
        elif so.confirmed_at:
            so.status = SOStatus.CONFIRMED
        elif so.approved_at:
            so.status = SOStatus.APPROVED
        else:
            so.status = SOStatus.SUBMITTED

        so.updated_by = coerce_uuid(released_by)
        so.updated_at = datetime.utcnow()

        db.flush()
        db.commit()
        db.refresh(so)
        return so

    @staticmethod
    def list_orders(
        db: Session,
        organization_id: str,
        customer_id: str | None = None,
        status: SOStatus | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SalesOrder]:
        """List sales orders with filters."""
        org_id = coerce_uuid(organization_id)

        query = db.query(SalesOrder).filter(SalesOrder.organization_id == org_id)

        if customer_id:
            query = query.filter(SalesOrder.customer_id == coerce_uuid(customer_id))

        if status:
            query = query.filter(SalesOrder.status == status)

        if start_date:
            query = query.filter(SalesOrder.order_date >= start_date)

        if end_date:
            query = query.filter(SalesOrder.order_date <= end_date)

        return (
            query.order_by(SalesOrder.order_date.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )


sales_order_service = SalesOrderService()
