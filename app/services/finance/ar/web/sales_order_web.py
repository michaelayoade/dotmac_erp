"""
AR Sales Order Web Service - Sales order web view methods.

Provides view-focused data and operations for AR sales order web routes.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.payment_terms import PaymentTerms
from app.models.finance.ar.sales_order import SalesOrder, SOStatus
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.tax.tax_code import TaxCode
from app.models.inventory.item import Item
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.feature_flags import FEATURE_STOCK_RESERVATION, is_feature_enabled
from app.services.finance.ar.sales_order import sales_order_service
from app.services.finance.ar.web.base import normalize_date_range_filters
from app.services.finance.common import format_currency, format_date, parse_date
from app.services.finance.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def _customer_display_name(customer: Customer | None) -> str:
    if not customer:
        return "-"
    return customer.trading_name or customer.legal_name or "-"


def _customer_email(customer: Customer | None) -> str | None:
    if not customer:
        return None
    return (customer.primary_contact or {}).get("email")


class SalesOrderWebService:
    """Web service methods for AR sales orders."""

    # =========================================================================
    # Context Methods
    # =========================================================================

    @staticmethod
    def form_context(db: Session, organization_id) -> dict:
        """Get common form context for sales order create/edit forms."""
        logger.debug("form_context: org=%s", organization_id)
        org_id = coerce_uuid(organization_id)

        # Customers
        customers = db.scalars(
            select(Customer)
            .where(Customer.organization_id == org_id, Customer.is_active.is_(True))
            .order_by(Customer.legal_name)
        )
        customers = customers.all()
        customer_options = [
            {
                "customer_id": str(c.customer_id),
                "name": _customer_display_name(c),
                "email": _customer_email(c),
                "default_tax_code_id": str(c.default_tax_code_id)
                if c.default_tax_code_id
                else None,
            }
            for c in customers
        ]

        # Revenue accounts
        revenue_accounts = db.scalars(
            select(Account)
            .join(AccountCategory)
            .where(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
                AccountCategory.ifrs_category == IFRSCategory.REVENUE,
            )
            .order_by(Account.account_code)
        )
        revenue_accounts = revenue_accounts.all()
        revenue_options = [
            {
                "account_id": str(a.account_id),
                "code": a.account_code,
                "name": a.account_name,
            }
            for a in revenue_accounts
        ]

        # Tax codes
        tax_codes = db.scalars(
            select(TaxCode)
            .where(TaxCode.organization_id == org_id, TaxCode.is_active.is_(True))
            .order_by(TaxCode.tax_code)
        )
        tax_codes = tax_codes.all()
        tax_options = [
            {
                "tax_code_id": str(t.tax_code_id),
                "code": t.tax_code,
                "rate": float(t.tax_rate),
            }
            for t in tax_codes
        ]

        # Payment terms
        payment_terms = db.scalars(
            select(PaymentTerms)
            .where(
                PaymentTerms.organization_id == org_id, PaymentTerms.is_active.is_(True)
            )
            .order_by(PaymentTerms.terms_name)
        )
        payment_terms = payment_terms.all()
        terms_options = [
            {"terms_id": str(t.payment_terms_id), "name": t.terms_name}
            for t in payment_terms
        ]

        # Items (products/services)
        items = db.scalars(
            select(Item)
            .where(
                Item.organization_id == org_id,
                Item.is_active.is_(True),
                Item.is_saleable.is_(True),
            )
            .order_by(Item.item_code)
        )
        items = items.all()
        item_options = [
            {
                "item_id": str(i.item_id),
                "item_code": i.item_code,
                "item_name": i.item_name,
            }
            for i in items
        ]

        context = {
            "customers": customer_options,
            "revenue_accounts": revenue_options,
            "tax_codes": tax_options,
            "payment_terms": terms_options,
            "items": item_options,
            "today": format_date(date.today()),
        }
        context.update(get_currency_context(db, str(org_id)))
        return context

    @staticmethod
    def list_context(
        db: Session,
        organization_id: str,
        status: str | None = None,
        customer_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> dict:
        """Get context for sales order listing page."""
        logger.debug(
            "list_context: org=%s status=%s customer=%s",
            organization_id,
            status,
            customer_id,
        )
        org_id = coerce_uuid(organization_id)

        # Parse status filter
        status_filter = None
        if status:
            try:
                status_filter = SOStatus(status)
            except ValueError:
                pass

        parsed_start_date = parse_date(start_date)
        parsed_end_date = parse_date(end_date)

        # Get orders from service
        orders = sales_order_service.list_orders(
            db,
            organization_id,
            customer_id=customer_id,
            status=status_filter,
            start_date=parsed_start_date,
            end_date=parsed_end_date,
            sort=sort,
            sort_dir=sort_dir,
        )

        # Format for template
        items = []
        for so in orders:
            items.append(
                {
                    "so_id": str(so.so_id),
                    "so_number": so.so_number,
                    "order_date": format_date(so.order_date),
                    "customer_name": _customer_display_name(so.customer),
                    "customer_po": so.customer_po_number or "-",
                    "total_amount": format_currency(so.total_amount, so.currency_code),
                    "status": so.status.value,
                    "is_fully_shipped": so.is_fully_shipped,
                    "is_fully_invoiced": so.is_fully_invoiced,
                }
            )

        # Status counts
        status_counts = {}
        for s in SOStatus:
            count = db.scalar(
                select(func.count())
                .select_from(SalesOrder)
                .where(
                    SalesOrder.organization_id == org_id,
                    SalesOrder.status == s,
                )
            )
            status_counts[s.value] = count or 0

        # Customers for filter dropdown
        customers = db.scalars(
            select(Customer)
            .where(Customer.organization_id == org_id)
            .order_by(Customer.legal_name)
        )
        customers = customers.all()
        customer_options = [
            {"id": str(c.customer_id), "name": _customer_display_name(c)}
            for c in customers
        ]

        logger.debug("list_context: found %d orders", len(items))

        active_filters = build_active_filters(
            params={
                "status": status,
                "customer_id": customer_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
            options={
                "customer_id": {
                    str(c.customer_id): _customer_display_name(c) for c in customers
                }
            },
        )
        return {
            "orders": items,
            "filter_status": status,
            "filter_customer_id": customer_id,
            "filter_start_date": start_date,
            "filter_end_date": end_date,
            "status_counts": status_counts,
            "statuses": [s.value for s in SOStatus],
            "customers": customer_options,
            "active_filters": active_filters,
            "sort": sort or "",
            "sort_dir": sort_dir or "desc",
        }

    @staticmethod
    def detail_context(
        db: Session,
        organization_id: str,
        so_id: str,
    ) -> dict:
        """Get context for sales order detail page."""
        logger.debug("detail_context: org=%s so=%s", organization_id, so_id)
        org_id = coerce_uuid(organization_id)
        so = db.get(SalesOrder, coerce_uuid(so_id))

        if not so or so.organization_id != org_id:
            return {"order": None, "lines": [], "shipments": []}

        reservation_by_line: dict[str, dict] = {}
        if is_feature_enabled(db, FEATURE_STOCK_RESERVATION):
            try:
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
                    reservation_by_line[str(reservation.source_line_id)] = {
                        "status": reservation.status.value,
                        "quantity_reserved": str(reservation.quantity_reserved),
                        "quantity_remaining": str(reservation.quantity_remaining),
                    }
            except Exception:
                logger.exception(
                    "detail_context: reservation load failed for so=%s",
                    so.so_id,
                )

        # Format order data
        order_data = {
            "so_id": str(so.so_id),
            "so_number": so.so_number,
            "order_date": format_date(so.order_date),
            "customer_name": _customer_display_name(so.customer),
            "customer_po": so.customer_po_number or "-",
            "reference": so.reference or "-",
            "requested_date": format_date(so.requested_date)
            if so.requested_date
            else "-",
            "promised_date": format_date(so.promised_date) if so.promised_date else "-",
            "subtotal": format_currency(so.subtotal, so.currency_code),
            "discount_amount": format_currency(so.discount_amount, so.currency_code),
            "tax_amount": format_currency(so.tax_amount, so.currency_code),
            "shipping_amount": format_currency(so.shipping_amount, so.currency_code),
            "total_amount": format_currency(so.total_amount, so.currency_code),
            "invoiced_amount": format_currency(so.invoiced_amount, so.currency_code),
            "currency_code": so.currency_code,
            "status": so.status.value,
            "is_fully_shipped": so.is_fully_shipped,
            "is_fully_invoiced": so.is_fully_invoiced,
            "customer_notes": so.customer_notes or "",
            "internal_notes": so.internal_notes or "",
            "payment_terms": so.payment_terms.terms_name if so.payment_terms else "-",
            # Shipping
            "ship_to_name": so.ship_to_name or "-",
            "ship_to_address": so.ship_to_address or "",
            "ship_to_city": so.ship_to_city or "",
            "ship_to_state": so.ship_to_state or "",
            "ship_to_postal_code": so.ship_to_postal_code or "",
            "ship_to_country": so.ship_to_country or "",
            "shipping_method": so.shipping_method or "-",
            "allow_partial": so.allow_partial_shipment,
            # Timestamps
            "submitted_at": so.submitted_at.strftime("%Y-%m-%d %H:%M")
            if so.submitted_at
            else None,
            "approved_at": so.approved_at.strftime("%Y-%m-%d %H:%M")
            if so.approved_at
            else None,
            "confirmed_at": so.confirmed_at.strftime("%Y-%m-%d %H:%M")
            if so.confirmed_at
            else None,
            "completed_at": so.completed_at.strftime("%Y-%m-%d %H:%M")
            if so.completed_at
            else None,
            "cancelled_at": so.cancelled_at.strftime("%Y-%m-%d %H:%M")
            if so.cancelled_at
            else None,
            "cancellation_reason": so.cancellation_reason,
            "created_at": so.created_at.strftime("%Y-%m-%d %H:%M")
            if so.created_at
            else "",
            # Actions
            "can_submit": so.status == SOStatus.DRAFT,
            "can_approve": so.status == SOStatus.SUBMITTED,
            "can_confirm": so.status == SOStatus.APPROVED,
            "can_ship": so.status in [SOStatus.CONFIRMED, SOStatus.IN_PROGRESS]
            and not so.is_fully_shipped,
            "can_invoice": so.status in [SOStatus.IN_PROGRESS, SOStatus.SHIPPED]
            and not so.is_fully_invoiced,
            "can_cancel": so.status
            not in [SOStatus.SHIPPED, SOStatus.COMPLETED, SOStatus.CANCELLED],
            "can_hold": so.status
            not in [SOStatus.COMPLETED, SOStatus.CANCELLED, SOStatus.ON_HOLD],
            "can_release": so.status == SOStatus.ON_HOLD,
        }

        # Format lines
        lines = []
        for line in so.lines:
            lines.append(
                {
                    "line_id": str(line.line_id),
                    "line_number": line.line_number,
                    "item_code": line.item_code or "-",
                    "description": line.description,
                    "quantity_ordered": str(line.quantity_ordered),
                    "quantity_shipped": str(line.quantity_shipped),
                    "quantity_invoiced": str(line.quantity_invoiced),
                    "unit_price": format_currency(line.unit_price, so.currency_code),
                    "discount": format_currency(line.discount_amount, so.currency_code),
                    "tax": format_currency(line.tax_amount, so.currency_code),
                    "line_total": format_currency(line.line_total, so.currency_code),
                    "fulfillment_status": line.fulfillment_status.value,
                    "reservation_status": reservation_by_line.get(
                        str(line.line_id), {}
                    ).get("status"),
                    "reserved_quantity": reservation_by_line.get(
                        str(line.line_id), {}
                    ).get("quantity_reserved"),
                    "reserved_remaining": reservation_by_line.get(
                        str(line.line_id), {}
                    ).get("quantity_remaining"),
                    "can_ship": line.quantity_shipped < line.quantity_ordered
                    and so.status in [SOStatus.CONFIRMED, SOStatus.IN_PROGRESS],
                }
            )

        # Format shipments
        shipments = []
        for ship in so.shipments:
            shipments.append(
                {
                    "shipment_id": str(ship.shipment_id),
                    "shipment_number": ship.shipment_number,
                    "shipment_date": format_date(ship.shipment_date),
                    "carrier": ship.carrier or "-",
                    "tracking_number": ship.tracking_number or "-",
                    "is_delivered": ship.is_delivered,
                    "delivered_at": ship.delivered_at.strftime("%Y-%m-%d %H:%M")
                    if ship.delivered_at
                    else None,
                }
            )

        return {"order": order_data, "lines": lines, "shipments": shipments}

    @staticmethod
    def shipment_form_context(
        db: Session,
        organization_id: str,
        so_id: str,
    ) -> dict:
        """Get context for shipment form."""
        logger.debug("shipment_form_context: org=%s so=%s", organization_id, so_id)
        org_id = coerce_uuid(organization_id)
        so = db.get(SalesOrder, coerce_uuid(so_id))

        if not so or so.organization_id != org_id:
            return {"order": None, "lines": []}

        # Get lines available for shipping
        lines = []
        for line in so.lines:
            remaining = line.quantity_ordered - line.quantity_shipped
            if remaining > 0:
                lines.append(
                    {
                        "line_id": str(line.line_id),
                        "line_number": line.line_number,
                        "item_code": line.item_code or "-",
                        "description": line.description,
                        "quantity_ordered": str(line.quantity_ordered),
                        "quantity_shipped": str(line.quantity_shipped),
                        "remaining": str(remaining),
                    }
                )

        order_data = {
            "so_id": str(so.so_id),
            "so_number": so.so_number,
            "customer_name": _customer_display_name(so.customer),
            "ship_to_name": so.ship_to_name or "-",
            "ship_to_address": so.ship_to_address or "",
            "shipping_method": so.shipping_method or "",
        }

        return {
            "order": order_data,
            "lines": lines,
            "today": format_date(date.today()),
        }

    # =========================================================================
    # Business Logic Methods
    # =========================================================================

    @staticmethod
    def create_sales_order(
        db: Session,
        organization_id: str,
        user_id: str,
        customer_id: str,
        order_date: str,
        lines_json: str,
        currency_code: str | None = None,
        exchange_rate: str | None = None,
        customer_po_number: str | None = None,
        requested_date: str | None = None,
        promised_date: str | None = None,
        payment_terms_id: str | None = None,
        ship_to_name: str | None = None,
        ship_to_address: str | None = None,
        ship_to_city: str | None = None,
        ship_to_state: str | None = None,
        ship_to_postal_code: str | None = None,
        ship_to_country: str | None = None,
        shipping_method: str | None = None,
        allow_partial_shipment: bool = False,
        customer_notes: str | None = None,
        internal_notes: str | None = None,
    ) -> tuple[SalesOrder | None, str | None]:
        """Create a new sales order. Returns (order, error)."""
        logger.debug(
            "create_sales_order: org=%s customer=%s", organization_id, customer_id
        )
        try:
            payload = {
                "customer_id": customer_id,
                "order_date": order_date,
                "lines_json": lines_json,
                "currency_code": currency_code,
                "exchange_rate": exchange_rate,
                "customer_po_number": customer_po_number,
                "requested_date": requested_date,
                "promised_date": promised_date,
                "payment_terms_id": payment_terms_id,
                "ship_to_name": ship_to_name,
                "ship_to_address": ship_to_address,
                "ship_to_city": ship_to_city,
                "ship_to_state": ship_to_state,
                "ship_to_postal_code": ship_to_postal_code,
                "ship_to_country": ship_to_country,
                "shipping_method": shipping_method,
                "allow_partial_shipment": allow_partial_shipment,
                "customer_notes": customer_notes,
                "internal_notes": internal_notes,
            }
            so = sales_order_service.create_from_payload(
                db,
                organization_id=organization_id,
                user_id=user_id,
                payload=payload,
            )
            logger.info(
                "create_sales_order: created %s for org %s",
                so.so_number,
                organization_id,
            )
            return so, None

        except Exception as e:
            logger.exception("create_sales_order: failed for org %s", organization_id)
            return None, str(e)

    @staticmethod
    def create_shipment(
        db: Session,
        organization_id: str,
        user_id: str,
        so_id: str,
        shipment_date: str,
        line_quantities_json: str,
        carrier: str | None = None,
        tracking_number: str | None = None,
        shipping_method: str | None = None,
        notes: str | None = None,
    ) -> tuple[object | None, str | None]:
        """Create a shipment for a sales order. Returns (shipment, error)."""
        logger.debug("create_shipment: org=%s so=%s", organization_id, so_id)
        try:
            payload = {
                "line_quantities_json": line_quantities_json,
                "shipment_date": shipment_date,
                "carrier": carrier,
                "tracking_number": tracking_number,
                "shipping_method": shipping_method,
                "notes": notes,
            }
            shipment = sales_order_service.create_shipment_from_payload(
                db,
                so_id=so_id,
                user_id=user_id,
                payload=payload,
            )
            logger.info("create_shipment: created shipment for SO %s", so_id)
            return shipment, None

        except Exception as e:
            logger.exception("create_shipment: failed for SO %s", so_id)
            return None, str(e)

    # =========================================================================
    # Response Methods
    # =========================================================================

    def list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None,
        customer_id: str | None,
        start_date: str | None,
        end_date: str | None,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> HTMLResponse:
        """Render sales order list page."""
        start_date, end_date = normalize_date_range_filters(
            start_date,
            end_date,
            request.query_params,
        )
        context = base_context(request, auth, "Sales Orders", "sales-orders")
        context.update(
            self.list_context(
                db,
                str(auth.organization_id),
                status=status,
                customer_id=customer_id,
                start_date=start_date,
                end_date=end_date,
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(
            request, "finance/ar/sales_orders.html", context
        )

    def new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str | None = None,
        quote_id: str | None = None,
    ) -> HTMLResponse:
        """Render new sales order form page."""
        context = base_context(request, auth, "New Sales Order", "sales-orders")
        context.update(self.form_context(db, auth.organization_id))
        context["order"] = None
        context["selected_customer_id"] = customer_id
        context["quote_id"] = quote_id
        return templates.TemplateResponse(
            request, "finance/ar/sales_order_form.html", context
        )

    def create_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str,
        order_date: str,
        lines_json: str,
        currency_code: str | None = None,
        exchange_rate: str | None = None,
        customer_po_number: str | None = None,
        requested_date: str | None = None,
        promised_date: str | None = None,
        payment_terms_id: str | None = None,
        ship_to_name: str | None = None,
        ship_to_address: str | None = None,
        ship_to_city: str | None = None,
        ship_to_state: str | None = None,
        ship_to_postal_code: str | None = None,
        ship_to_country: str | None = None,
        shipping_method: str | None = None,
        allow_partial_shipment: bool = False,
        customer_notes: str | None = None,
        internal_notes: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Handle sales order creation form submission."""
        so, error = self.create_sales_order(
            db,
            str(auth.organization_id),
            str(auth.user_id),
            customer_id=customer_id,
            order_date=order_date,
            lines_json=lines_json,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            customer_po_number=customer_po_number,
            requested_date=requested_date,
            promised_date=promised_date,
            payment_terms_id=payment_terms_id,
            ship_to_name=ship_to_name,
            ship_to_address=ship_to_address,
            ship_to_city=ship_to_city,
            ship_to_state=ship_to_state,
            ship_to_postal_code=ship_to_postal_code,
            ship_to_country=ship_to_country,
            shipping_method=shipping_method,
            allow_partial_shipment=allow_partial_shipment,
            customer_notes=customer_notes,
            internal_notes=internal_notes,
        )

        if error or so is None:
            context = base_context(request, auth, "New Sales Order", "sales-orders")
            context.update(self.form_context(db, auth.organization_id))
            context["order"] = None
            context["error"] = error or "Sales order creation failed"
            return templates.TemplateResponse(
                request, "finance/ar/sales_order_form.html", context
            )

        return RedirectResponse(
            url=f"/sales-orders/{so.so_id}?saved=1", status_code=303
        )

    def detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        so_id: str,
    ) -> HTMLResponse:
        """Render sales order detail page."""
        detail_ctx = self.detail_context(db, str(auth.organization_id), so_id)

        if detail_ctx["order"] is None:
            context = base_context(
                request, auth, "Sales Order Not Found", "sales-orders"
            )
            context["order"] = None
            return templates.TemplateResponse(
                request, "finance/ar/sales_order_detail.html", context
            )

        so_number = detail_ctx["order"]["so_number"]
        context = base_context(request, auth, f"SO {so_number}", "sales-orders")
        context.update(detail_ctx)
        return templates.TemplateResponse(
            request, "finance/ar/sales_order_detail.html", context
        )

    def submit_response(
        self, request: Request, auth: WebAuthContext, db: Session, so_id: str
    ) -> RedirectResponse:
        """Handle submit sales order action."""
        try:
            sales_order_service.submit(
                db, so_id, str(auth.user_id), organization_id=str(auth.organization_id)
            )
        except Exception:
            logger.exception("submit_response: failed")
        return RedirectResponse(url=f"/sales-orders/{so_id}?saved=1", status_code=303)

    def approve_response(
        self, request: Request, auth: WebAuthContext, db: Session, so_id: str
    ) -> RedirectResponse:
        """Handle approve sales order action."""
        try:
            sales_order_service.approve(
                db, so_id, str(auth.user_id), organization_id=str(auth.organization_id)
            )
        except Exception:
            logger.exception("approve_response: failed")
        return RedirectResponse(url=f"/sales-orders/{so_id}?saved=1", status_code=303)

    def confirm_response(
        self, request: Request, auth: WebAuthContext, db: Session, so_id: str
    ) -> RedirectResponse:
        """Handle confirm sales order action."""
        try:
            sales_order_service.confirm(
                db, so_id, organization_id=str(auth.organization_id)
            )
        except Exception:
            logger.exception("confirm_response: failed")
        return RedirectResponse(url=f"/sales-orders/{so_id}?saved=1", status_code=303)

    def cancel_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        so_id: str,
        reason: str | None = None,
    ) -> RedirectResponse:
        """Handle cancel sales order action."""
        try:
            sales_order_service.cancel(
                db,
                so_id,
                str(auth.user_id),
                reason,
                organization_id=str(auth.organization_id),
            )
        except Exception:
            logger.exception("cancel_response: failed")
        return RedirectResponse(url=f"/sales-orders/{so_id}?saved=1", status_code=303)

    def hold_response(
        self, request: Request, auth: WebAuthContext, db: Session, so_id: str
    ) -> RedirectResponse:
        """Handle hold sales order action."""
        try:
            sales_order_service.hold(
                db, so_id, str(auth.user_id), organization_id=str(auth.organization_id)
            )
        except Exception:
            logger.exception("hold_response: failed")
        return RedirectResponse(url=f"/sales-orders/{so_id}?saved=1", status_code=303)

    def release_response(
        self, request: Request, auth: WebAuthContext, db: Session, so_id: str
    ) -> RedirectResponse:
        """Handle release sales order from hold action."""
        try:
            sales_order_service.release_hold(
                db, so_id, str(auth.user_id), organization_id=str(auth.organization_id)
            )
        except Exception:
            logger.exception("release_response: failed")
        return RedirectResponse(url=f"/sales-orders/{so_id}?saved=1", status_code=303)

    def create_invoice_response(
        self, request: Request, auth: WebAuthContext, db: Session, so_id: str
    ) -> RedirectResponse:
        """Handle create invoice from sales order action."""
        try:
            invoice = sales_order_service.create_invoice_from_so(
                db, so_id, str(auth.user_id), organization_id=str(auth.organization_id)
            )
            return RedirectResponse(
                url=f"/ar/invoices/{invoice.invoice_id}?saved=1", status_code=303
            )
        except Exception:
            logger.exception("create_invoice_response: failed")
            return RedirectResponse(url=f"/sales-orders/{so_id}", status_code=303)

    def shipment_form_response(
        self, request: Request, auth: WebAuthContext, db: Session, so_id: str
    ) -> HTMLResponse | RedirectResponse:
        """Render shipment form page."""
        shipment_ctx = self.shipment_form_context(db, str(auth.organization_id), so_id)

        if shipment_ctx["order"] is None:
            return RedirectResponse(
                url="/sales-orders?success=Record+saved+successfully", status_code=303
            )

        so_number = shipment_ctx["order"]["so_number"]
        context = base_context(request, auth, f"Ship SO {so_number}", "sales-orders")
        context.update(shipment_ctx)
        return templates.TemplateResponse(
            request, "finance/ar/shipment_form.html", context
        )

    def create_shipment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        so_id: str,
        shipment_date: str,
        line_quantities_json: str,
        carrier: str | None = None,
        tracking_number: str | None = None,
        shipping_method: str | None = None,
        notes: str | None = None,
    ) -> RedirectResponse:
        """Handle shipment creation form submission."""
        _, error = self.create_shipment(
            db,
            str(auth.organization_id),
            str(auth.user_id),
            so_id=so_id,
            shipment_date=shipment_date,
            line_quantities_json=line_quantities_json,
            carrier=carrier,
            tracking_number=tracking_number,
            shipping_method=shipping_method,
            notes=notes,
        )

        if error:
            return RedirectResponse(
                url=f"/sales-orders/{so_id}/ship?error={error}", status_code=303
            )

        return RedirectResponse(url=f"/sales-orders/{so_id}?saved=1", status_code=303)

    def mark_delivered_response(
        self, request: Request, auth: WebAuthContext, db: Session, shipment_id: str
    ) -> RedirectResponse:
        """Handle mark shipment as delivered action."""
        try:
            shipment = sales_order_service.mark_delivered(
                db, shipment_id, organization_id=str(auth.organization_id)
            )
            return RedirectResponse(
                url=f"/sales-orders/{shipment.so_id}?saved=1", status_code=303
            )
        except Exception:
            logger.exception("mark_delivered_response: failed")
            return RedirectResponse(url="/sales-orders", status_code=303)


# Module-level singleton
sales_order_web_service = SalesOrderWebService()
