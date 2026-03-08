"""
Sales Order Service.

Business logic for sales orders with fulfillment and invoicing.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.domain_settings import SettingDomain
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
from app.services.feature_flags import FEATURE_STOCK_RESERVATION, is_feature_enabled
from app.services.finance.ar.input_utils import (
    parse_date_str,
    parse_json_list,
    require_uuid,
    resolve_currency_code,
)
from app.services.finance.common import SyncNumberingService
from app.services.settings_cache import settings_cache

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
        organization_id: UUID | str,
        customer_id: UUID | str,
        order_date: date,
        created_by: UUID | str,
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

        return so

    @staticmethod
    def create_from_payload(
        db: Session,
        organization_id: UUID | str,
        user_id: UUID | str,
        payload: dict,
    ) -> SalesOrder:
        """Create a new sales order from raw payload (strings or JSON)."""
        org_id = coerce_uuid(organization_id)

        customer_id = require_uuid(payload.get("customer_id"), "Customer")
        order_date = parse_date_str(payload.get("order_date"), "Order date", True)
        if order_date is None:
            raise ValueError("Order date is required")
        currency_code = resolve_currency_code(db, org_id, payload.get("currency_code"))
        lines = parse_json_list(
            payload.get("lines") or payload.get("lines_json"), "Lines"
        )
        allow_partial = payload.get("allow_partial_shipment", True)
        allow_partial_shipment = bool(allow_partial)

        exchange_rate = Decimal("1")
        raw_rate = payload.get("exchange_rate")
        if raw_rate not in (None, ""):
            exchange_rate = Decimal(str(raw_rate))

        return SalesOrderService.create(
            db=db,
            organization_id=org_id,
            customer_id=customer_id,
            order_date=order_date,
            created_by=user_id,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            customer_po_number=payload.get("customer_po_number"),
            reference=payload.get("reference"),
            requested_date=parse_date_str(
                payload.get("requested_date"), "Requested date"
            ),
            promised_date=parse_date_str(payload.get("promised_date"), "Promised date"),
            payment_terms_id=payload.get("payment_terms_id") or None,
            ship_to_name=payload.get("ship_to_name"),
            ship_to_address=payload.get("ship_to_address"),
            ship_to_city=payload.get("ship_to_city"),
            ship_to_state=payload.get("ship_to_state"),
            ship_to_postal_code=payload.get("ship_to_postal_code"),
            ship_to_country=payload.get("ship_to_country"),
            shipping_method=payload.get("shipping_method"),
            allow_partial_shipment=allow_partial_shipment,
            internal_notes=payload.get("internal_notes"),
            customer_notes=payload.get("customer_notes"),
            lines=lines,
        )

    @staticmethod
    def create_shipment_from_payload(
        db: Session,
        so_id: UUID | str,
        user_id: UUID | str,
        payload: dict,
    ) -> Shipment:
        """Create a shipment from raw payload (strings or JSON)."""
        shipment_date = parse_date_str(
            payload.get("shipment_date"), "Shipment date", True
        )
        if shipment_date is None:
            raise ValueError("Shipment date is required")
        line_quantities = parse_json_list(
            payload.get("line_quantities") or payload.get("line_quantities_json"),
            "Line quantities",
        )

        return SalesOrderService.create_shipment(
            db=db,
            so_id=str(so_id),
            shipment_date=shipment_date,
            created_by=str(user_id),
            line_quantities=line_quantities,
            carrier=payload.get("carrier"),
            tracking_number=payload.get("tracking_number"),
            shipping_method=payload.get("shipping_method"),
            notes=payload.get("notes"),
        )

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
        organization_id: str | None = None,
    ) -> SalesOrder:
        """Submit SO for approval."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so or (
            organization_id and so.organization_id != coerce_uuid(organization_id)
        ):
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
            logger.exception("Ignored exception")

        db.flush()
        return so

    @staticmethod
    def approve(
        db: Session,
        so_id: str,
        approved_by: str,
        organization_id: str | None = None,
    ) -> SalesOrder:
        """Approve SO."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so or (
            organization_id and so.organization_id != coerce_uuid(organization_id)
        ):
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
            logger.exception("Ignored exception")

        db.flush()
        return so

    @staticmethod
    def confirm(
        db: Session,
        so_id: str,
        organization_id: str | None = None,
    ) -> SalesOrder:
        """Confirm SO with customer."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so or (
            organization_id and so.organization_id != coerce_uuid(organization_id)
        ):
            raise ValueError("Sales order not found")

        if so.status != SOStatus.APPROVED:
            raise ValueError(f"Cannot confirm SO in {so.status.value} status")

        so.status = SOStatus.CONFIRMED
        so.confirmed_at = datetime.utcnow()

        SalesOrderService._reserve_stock_on_confirm(db, so)
        try:
            from app.services.hooks import emit_hook_event
            from app.services.hooks.events import SALES_ORDER_CONFIRMED

            emit_hook_event(
                db,
                event_name=SALES_ORDER_CONFIRMED,
                organization_id=so.organization_id,
                entity_type="SalesOrder",
                entity_id=so.so_id,
                actor_user_id=None,
                payload={
                    "so_id": str(so.so_id),
                    "so_number": so.so_number,
                    "status": so.status.value,
                    "total_amount": str(so.total_amount),
                },
            )
        except Exception:
            logger.exception(
                "Failed to emit sales.order.confirmed hook for %s", so.so_id
            )

        db.flush()
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
        organization_id: str | None = None,
    ) -> Shipment:
        """Create a shipment for sales order lines."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so or (
            organization_id and so.organization_id != coerce_uuid(organization_id)
        ):
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
        from app.services.inventory.stock_reservation import (
            ReservationSourceType,
            StockReservationService,
        )

        reservation_service = StockReservationService(db)
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

            if is_feature_enabled(db, so.organization_id, FEATURE_STOCK_RESERVATION):
                reservation = reservation_service.get_reservation_for_line(
                    ReservationSourceType.SALES_ORDER,
                    so_line.line_id,
                )
                if reservation:
                    try:
                        reservation_service.fulfill(
                            reservation.reservation_id,
                            qty_to_ship,
                        )
                    except ValueError:
                        logger.warning(
                            "Could not fulfill reservation for SO line %s",
                            so_line.line_id,
                            exc_info=True,
                        )

        # Update SO status
        if so.is_fully_shipped:
            so.status = SOStatus.SHIPPED
        else:
            so.status = SOStatus.IN_PROGRESS

        try:
            from app.services.hooks import emit_hook_event
            from app.services.hooks.events import SHIPMENT_CREATED

            emit_hook_event(
                db,
                event_name=SHIPMENT_CREATED,
                organization_id=so.organization_id,
                entity_type="Shipment",
                entity_id=shipment.shipment_id,
                actor_user_id=user_id,
                payload={
                    "shipment_id": str(shipment.shipment_id),
                    "shipment_number": shipment.shipment_number,
                    "so_id": str(so.so_id),
                    "so_number": so.so_number,
                    "line_count": str(len(line_quantities)),
                    "status": so.status.value,
                },
            )
        except Exception:
            logger.exception(
                "Failed to emit shipment.created hook for shipment %s",
                shipment.shipment_id,
            )

        db.flush()
        return shipment

    @staticmethod
    def mark_delivered(
        db: Session,
        shipment_id: str,
        organization_id: str | None = None,
    ) -> Shipment:
        """Mark shipment as delivered."""
        shipment = db.get(Shipment, coerce_uuid(shipment_id))
        if not shipment or (
            organization_id and shipment.organization_id != coerce_uuid(organization_id)
        ):
            raise ValueError("Shipment not found")

        shipment.is_delivered = True
        shipment.delivered_at = datetime.utcnow()

        db.flush()
        return shipment

    @staticmethod
    def create_invoice_from_so(
        db: Session,
        so_id: str,
        created_by: str,
        invoice_date: date | None = None,
        line_quantities: list[dict]
        | None = None,  # If None, invoice all shipped but not invoiced
        organization_id: str | None = None,
    ) -> Invoice:
        """Create invoice from shipped SO lines."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so or (
            organization_id and so.organization_id != coerce_uuid(organization_id)
        ):
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
        return invoice

    @staticmethod
    def cancel(
        db: Session,
        so_id: str,
        cancelled_by: str,
        reason: str | None = None,
        organization_id: str | None = None,
    ) -> SalesOrder:
        """Cancel a sales order."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so or (
            organization_id and so.organization_id != coerce_uuid(organization_id)
        ):
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

        if is_feature_enabled(db, so.organization_id, FEATURE_STOCK_RESERVATION):
            from app.services.inventory.stock_reservation import (
                ReservationSourceType,
                StockReservationService,
            )

            reservation_service = StockReservationService(db)
            reservations = reservation_service.get_reservations_for_source(
                ReservationSourceType.SALES_ORDER,
                so.so_id,
            )
            for reservation in reservations:
                try:
                    reservation_service.cancel(
                        reservation.reservation_id,
                        reason=reason,
                    )
                except ValueError:
                    logger.warning(
                        "Could not cancel reservation %s",
                        reservation.reservation_id,
                        exc_info=True,
                    )
        try:
            from app.services.hooks import emit_hook_event
            from app.services.hooks.events import SALES_ORDER_CANCELLED

            emit_hook_event(
                db,
                event_name=SALES_ORDER_CANCELLED,
                organization_id=so.organization_id,
                entity_type="SalesOrder",
                entity_id=so.so_id,
                actor_user_id=coerce_uuid(cancelled_by),
                payload={
                    "so_id": str(so.so_id),
                    "so_number": so.so_number,
                    "status": so.status.value,
                    "reason": reason,
                },
            )
        except Exception:
            logger.exception(
                "Failed to emit sales.order.cancelled hook for %s", so.so_id
            )

        db.flush()
        return so

    @staticmethod
    def hold(
        db: Session,
        so_id: str,
        held_by: str,
        organization_id: str | None = None,
    ) -> SalesOrder:
        """Put SO on hold."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so or (
            organization_id and so.organization_id != coerce_uuid(organization_id)
        ):
            raise ValueError("Sales order not found")

        if so.status in [SOStatus.COMPLETED, SOStatus.CANCELLED]:
            raise ValueError(f"Cannot hold SO in {so.status.value} status")

        so.status = SOStatus.ON_HOLD
        so.updated_by = coerce_uuid(held_by)
        so.updated_at = datetime.utcnow()

        db.flush()
        return so

    @staticmethod
    def release_hold(
        db: Session,
        so_id: str,
        released_by: str,
        organization_id: str | None = None,
    ) -> SalesOrder:
        """Release SO from hold."""
        so = db.get(SalesOrder, coerce_uuid(so_id))
        if not so or (
            organization_id and so.organization_id != coerce_uuid(organization_id)
        ):
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
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> list[SalesOrder]:
        """List sales orders with filters."""
        from app.services.finance.common.sorting import apply_sort

        org_id = coerce_uuid(organization_id)

        stmt = select(SalesOrder).where(SalesOrder.organization_id == org_id)

        if customer_id:
            stmt = stmt.where(SalesOrder.customer_id == coerce_uuid(customer_id))

        if status:
            stmt = stmt.where(SalesOrder.status == status)

        if start_date:
            stmt = stmt.where(SalesOrder.order_date >= start_date)

        if end_date:
            stmt = stmt.where(SalesOrder.order_date <= end_date)

        column_map = {
            "order_date": SalesOrder.order_date,
            "so_number": SalesOrder.so_number,
            "total_amount": SalesOrder.total_amount,
            "status": SalesOrder.status,
        }
        stmt = apply_sort(
            stmt, sort, sort_dir, column_map, default=SalesOrder.order_date.desc()
        )

        return list(db.scalars(stmt.offset(offset).limit(limit)).all())

    @staticmethod
    def _reserve_stock_on_confirm(db: Session, so: SalesOrder) -> None:
        """Auto-reserve inventory on SO confirmation when feature is enabled."""
        if not is_feature_enabled(db, so.organization_id, FEATURE_STOCK_RESERVATION):
            return

        from app.services.inventory.stock_reservation import (
            ReservationSourceType,
            StockReservationService,
        )

        reservation_service = StockReservationService(db)
        config = reservation_service.load_config(db, so.organization_id)
        if not config.enabled or not config.auto_on_confirm:
            return

        raw_warehouse_id = settings_cache.get_setting_value(
            db,
            domain=SettingDomain.inventory,
            key="inventory_default_warehouse_id",
            default=None,
        )
        warehouse_id = None
        if raw_warehouse_id:
            try:
                warehouse_id = coerce_uuid(raw_warehouse_id)
            except Exception:
                logger.warning(
                    "Skipping stock reservation for SO %s: invalid default warehouse id",
                    so.so_id,
                )
                return
        if warehouse_id is None:
            logger.info(
                "Skipping stock reservation for SO %s: no default warehouse configured",
                so.so_id,
            )
            return

        reserved_by = so.updated_by or so.approved_by or so.created_by
        for line in so.lines:
            if line.item_id is None or line.quantity_ordered <= 0:
                continue
            try:
                result = reservation_service.reserve(
                    organization_id=so.organization_id,
                    item_id=line.item_id,
                    warehouse_id=warehouse_id,
                    quantity=line.quantity_ordered,
                    source_type=ReservationSourceType.SALES_ORDER,
                    source_id=so.so_id,
                    source_line_id=line.line_id,
                    reserved_by_user_id=reserved_by,
                    config=config,
                )
                if not result.success:
                    logger.warning(
                        "Reservation failed for SO %s line %s: %s",
                        so.so_id,
                        line.line_id,
                        result.message,
                    )
            except Exception:
                logger.exception(
                    "Reservation error for SO %s line %s",
                    so.so_id,
                    line.line_id,
                )


sales_order_service = SalesOrderService()
