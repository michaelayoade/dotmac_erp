"""
AR Quote Web Service - Quote web view methods.

Provides view-focused data and operations for AR quote web routes.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, cast

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.payment_terms import PaymentTerms
from app.models.finance.ar.quote import Quote, QuoteStatus
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.tax.tax_code import TaxCode
from app.services.common import coerce_uuid
from app.services.finance.ar.quote import quote_service
from app.services.finance.common import format_date, format_currency, parse_date
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.templates import templates
from app.web.deps import base_context, WebAuthContext

logger = logging.getLogger(__name__)


def _customer_display_name(customer: Customer | None) -> str:
    if not customer:
        return "-"
    return cast(str, customer.trading_name or customer.legal_name)


def _customer_email(customer: Customer | None) -> str | None:
    if not customer:
        return None
    return (customer.primary_contact or {}).get("email")


class QuoteWebService:
    """Web service methods for AR quotes."""

    # =========================================================================
    # Context Methods
    # =========================================================================

    @staticmethod
    def form_context(db: Session, organization_id) -> dict:
        """Get common form context for quote create/edit forms."""
        logger.debug("form_context: org=%s", organization_id)
        org_id = coerce_uuid(organization_id)

        # Customers
        customers = (
            db.query(Customer)
            .filter(Customer.organization_id == org_id, Customer.is_active.is_(True))
            .order_by(Customer.legal_name)
            .all()
        )
        customer_options = [
            {
                "customer_id": str(c.customer_id),
                "name": c.trading_name or c.legal_name,
                "email": _customer_email(c),
            }
            for c in customers
        ]

        # Revenue accounts
        revenue_accounts = (
            db.query(Account)
            .join(AccountCategory)
            .filter(
                Account.organization_id == org_id,
                Account.is_active.is_(True),
                AccountCategory.ifrs_category == IFRSCategory.REVENUE,
            )
            .order_by(Account.account_code)
            .all()
        )
        revenue_options = [
            {
                "account_id": str(a.account_id),
                "code": a.account_code,
                "name": a.account_name,
            }
            for a in revenue_accounts
        ]

        # Tax codes
        tax_codes = (
            db.query(TaxCode)
            .filter(TaxCode.organization_id == org_id, TaxCode.is_active.is_(True))
            .order_by(TaxCode.tax_code)
            .all()
        )
        tax_options = [
            {
                "tax_code_id": str(t.tax_code_id),
                "code": t.tax_code,
                "rate": float(t.tax_rate),
            }
            for t in tax_codes
        ]

        # Payment terms
        payment_terms = (
            db.query(PaymentTerms)
            .filter(PaymentTerms.organization_id == org_id, PaymentTerms.is_active.is_(True))
            .order_by(PaymentTerms.terms_name)
            .all()
        )
        terms_options = [
            {"terms_id": str(t.payment_terms_id), "name": t.terms_name}
            for t in payment_terms
        ]

        context = {
            "customers": customer_options,
            "revenue_accounts": revenue_options,
            "tax_codes": tax_options,
            "payment_terms": terms_options,
            "today": format_date(date.today()),
            "default_valid_until": format_date(date.today() + timedelta(days=30)),
        }
        context.update(get_currency_context(db, str(org_id)))
        return context

    @staticmethod
    def list_context(
        db: Session,
        organization_id: str,
        status: Optional[str] = None,
        customer_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Get context for quote listing page."""
        logger.debug(
            "list_context: org=%s status=%s customer=%s",
            organization_id, status, customer_id
        )
        org_id = coerce_uuid(organization_id)

        # Parse status filter
        status_filter = None
        if status:
            try:
                status_filter = QuoteStatus(status)
            except ValueError:
                pass

        parsed_start_date = (
            datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
        )
        parsed_end_date = (
            datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
        )

        # Get quotes from service
        quotes = quote_service.list_quotes(
            db,
            organization_id,
            customer_id=customer_id,
            status=status_filter,
            start_date=parsed_start_date,
            end_date=parsed_end_date,
        )

        # Format for template
        items = []
        for q in quotes:
            items.append({
                "quote_id": str(q.quote_id),
                "quote_number": q.quote_number,
                "quote_date": format_date(q.quote_date),
                "valid_until": format_date(q.valid_until),
                "customer_name": _customer_display_name(q.customer),
                "total_amount": format_currency(q.total_amount, q.currency_code),
                "status": q.status.value,
                "is_expired": q.is_expired,
            })

        # Status counts
        status_counts = {}
        for s in QuoteStatus:
            count = db.query(Quote).filter(
                Quote.organization_id == org_id,
                Quote.status == s,
            ).count()
            status_counts[s.value] = count

        # Customers for filter dropdown
        customers = (
            db.query(Customer)
            .filter(Customer.organization_id == org_id)
            .order_by(Customer.legal_name)
            .all()
        )
        customer_options = [
            {"id": str(c.customer_id), "name": c.trading_name or c.legal_name}
            for c in customers
        ]

        logger.debug("list_context: found %d quotes", len(items))

        return {
            "quotes": items,
            "filter_status": status,
            "filter_customer_id": customer_id,
            "filter_start_date": start_date,
            "filter_end_date": end_date,
            "status_counts": status_counts,
            "statuses": [s.value for s in QuoteStatus],
            "customers": customer_options,
        }

    @staticmethod
    def detail_context(
        db: Session,
        organization_id: str,
        quote_id: str,
    ) -> dict:
        """Get context for quote detail page."""
        logger.debug("detail_context: org=%s quote=%s", organization_id, quote_id)
        org_id = coerce_uuid(organization_id)
        quote = db.get(Quote, coerce_uuid(quote_id))

        if not quote or quote.organization_id != org_id:
            return {"quote": None, "lines": []}

        # Format quote data
        quote_data = {
            "quote_id": str(quote.quote_id),
            "quote_number": quote.quote_number,
            "quote_date": format_date(quote.quote_date),
            "valid_until": format_date(quote.valid_until),
            "customer_name": _customer_display_name(quote.customer),
            "customer_email": _customer_email(quote.customer) or "-",
            "contact_name": quote.contact_name or "-",
            "contact_email": quote.contact_email or "-",
            "subtotal": format_currency(quote.subtotal, quote.currency_code),
            "discount_amount": format_currency(quote.discount_amount, quote.currency_code),
            "tax_amount": format_currency(quote.tax_amount, quote.currency_code),
            "total_amount": format_currency(quote.total_amount, quote.currency_code),
            "currency_code": quote.currency_code,
            "status": quote.status.value,
            "is_expired": quote.is_expired,
            "customer_notes": quote.customer_notes or "",
            "internal_notes": quote.internal_notes or "",
            "terms_and_conditions": quote.terms_and_conditions or "",
            "reference": quote.reference or "-",
            "payment_terms": quote.payment_terms.terms_name if quote.payment_terms else "-",
            "sent_at": quote.sent_at.strftime("%Y-%m-%d %H:%M") if quote.sent_at else None,
            "viewed_at": quote.viewed_at.strftime("%Y-%m-%d %H:%M") if quote.viewed_at else None,
            "accepted_at": quote.accepted_at.strftime("%Y-%m-%d %H:%M") if quote.accepted_at else None,
            "rejected_at": quote.rejected_at.strftime("%Y-%m-%d %H:%M") if quote.rejected_at else None,
            "rejection_reason": quote.rejection_reason,
            "converted_at": quote.converted_at.strftime("%Y-%m-%d %H:%M") if quote.converted_at else None,
            "converted_to_invoice_id": str(quote.converted_to_invoice_id) if quote.converted_to_invoice_id else None,
            "converted_to_so_id": str(quote.converted_to_so_id) if quote.converted_to_so_id else None,
            "created_at": quote.created_at.strftime("%Y-%m-%d %H:%M") if quote.created_at else "",
            # Actions
            "can_edit": quote.status == QuoteStatus.DRAFT,
            "can_send": quote.status == QuoteStatus.DRAFT,
            "can_accept": quote.status in [QuoteStatus.SENT, QuoteStatus.VIEWED] and not quote.is_expired,
            "can_reject": quote.status in [QuoteStatus.SENT, QuoteStatus.VIEWED],
            "can_convert": quote.status == QuoteStatus.ACCEPTED,
            "can_void": quote.status not in [QuoteStatus.CONVERTED, QuoteStatus.VOID],
        }

        # Format lines
        lines = []
        for line in quote.lines:
            lines.append({
                "line_number": line.line_number,
                "item_code": line.item_code or "-",
                "description": line.description,
                "quantity": str(line.quantity),
                "unit_price": format_currency(line.unit_price, quote.currency_code),
                "discount": format_currency(line.discount_amount, quote.currency_code),
                "tax": format_currency(line.tax_amount, quote.currency_code),
                "line_total": format_currency(line.line_total, quote.currency_code),
            })

        return {"quote": quote_data, "lines": lines}

    # =========================================================================
    # Business Logic Methods
    # =========================================================================

    @staticmethod
    def create_quote(
        db: Session,
        organization_id: str,
        user_id: str,
        customer_id: str,
        quote_date: str,
        valid_until: str,
        lines_json: str,
        currency_code: Optional[str] = None,
        contact_name: Optional[str] = None,
        contact_email: Optional[str] = None,
        payment_terms_id: Optional[str] = None,
        customer_notes: Optional[str] = None,
        internal_notes: Optional[str] = None,
        terms_and_conditions: Optional[str] = None,
    ) -> tuple[Optional[Quote], Optional[str]]:
        """Create a new quote. Returns (quote, error)."""
        logger.debug(
            "create_quote: org=%s customer=%s",
            organization_id, customer_id
        )
        try:
            lines = json.loads(lines_json) if lines_json else []

            # Get default currency if not provided
            if not currency_code:
                currency_code = org_context_service.get_functional_currency(
                    db,
                    coerce_uuid(organization_id),
                )

            # Parse dates
            quote_dt = datetime.strptime(quote_date, "%Y-%m-%d").date()
            valid_dt = datetime.strptime(valid_until, "%Y-%m-%d").date()

            quote = quote_service.create(
                db,
                organization_id=organization_id,
                customer_id=customer_id,
                quote_date=quote_dt,
                valid_until=valid_dt,
                created_by=user_id,
                currency_code=currency_code,
                contact_name=contact_name,
                contact_email=contact_email,
                payment_terms_id=payment_terms_id if payment_terms_id else None,
                customer_notes=customer_notes,
                internal_notes=internal_notes,
                terms_and_conditions=terms_and_conditions,
                lines=lines,
            )
            db.commit()
            logger.info("create_quote: created %s for org %s", quote.quote_number, organization_id)
            return quote, None

        except Exception as e:
            db.rollback()
            logger.exception("create_quote: failed for org %s", organization_id)
            return None, str(e)

    # =========================================================================
    # Response Methods
    # =========================================================================

    def list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str],
        customer_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> HTMLResponse:
        """Render quote list page."""
        context = base_context(request, auth, "Quotes", "quotes")
        context.update(
            self.list_context(
                db,
                str(auth.organization_id),
                status=status,
                customer_id=customer_id,
                start_date=start_date,
                end_date=end_date,
            )
        )
        return templates.TemplateResponse(request, "finance/ar/quotes.html", context)

    def new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: Optional[str] = None,
    ) -> HTMLResponse:
        """Render new quote form page."""
        context = base_context(request, auth, "New Quote", "quotes")
        context.update(self.form_context(db, auth.organization_id))
        context["quote"] = None
        context["selected_customer_id"] = customer_id
        return templates.TemplateResponse(request, "finance/ar/quote_form.html", context)

    def create_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        customer_id: str,
        quote_date: str,
        valid_until: str,
        lines_json: str,
        currency_code: Optional[str] = None,
        contact_name: Optional[str] = None,
        contact_email: Optional[str] = None,
        payment_terms_id: Optional[str] = None,
        customer_notes: Optional[str] = None,
        internal_notes: Optional[str] = None,
        terms_and_conditions: Optional[str] = None,
    ) -> HTMLResponse | RedirectResponse:
        """Handle quote creation form submission."""
        quote, error = self.create_quote(
            db,
            str(auth.organization_id),
            str(auth.user_id),
            customer_id=customer_id,
            quote_date=quote_date,
            valid_until=valid_until,
            lines_json=lines_json,
            currency_code=currency_code,
            contact_name=contact_name,
            contact_email=contact_email,
            payment_terms_id=payment_terms_id,
            customer_notes=customer_notes,
            internal_notes=internal_notes,
            terms_and_conditions=terms_and_conditions,
        )

        if error or quote is None:
            context = base_context(request, auth, "New Quote", "quotes")
            context.update(self.form_context(db, auth.organization_id))
            context["quote"] = None
            context["error"] = error or "Quote creation failed"
            return templates.TemplateResponse(request, "finance/ar/quote_form.html", context)

        return RedirectResponse(url=f"/quotes/{quote.quote_id}", status_code=303)

    def detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        quote_id: str,
    ) -> HTMLResponse:
        """Render quote detail page."""
        detail_ctx = self.detail_context(db, str(auth.organization_id), quote_id)

        if detail_ctx["quote"] is None:
            context = base_context(request, auth, "Quote Not Found", "quotes")
            context["quote"] = None
            return templates.TemplateResponse(request, "finance/ar/quote_detail.html", context)

        quote_number = detail_ctx["quote"]["quote_number"]
        context = base_context(request, auth, f"Quote {quote_number}", "quotes")
        context.update(detail_ctx)
        return templates.TemplateResponse(request, "finance/ar/quote_detail.html", context)

    def send_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        quote_id: str,
    ) -> RedirectResponse:
        """Handle send quote action."""
        try:
            quote_service.send(db, str(auth.organization_id), quote_id, str(auth.user_id))
            db.commit()
        except Exception:
            db.rollback()
        return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)

    def accept_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        quote_id: str,
    ) -> RedirectResponse:
        """Handle accept quote action."""
        try:
            quote_service.accept(db, str(auth.organization_id), quote_id)
            db.commit()
        except Exception:
            db.rollback()
        return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)

    def reject_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        quote_id: str,
        reason: Optional[str] = None,
    ) -> RedirectResponse:
        """Handle reject quote action."""
        try:
            quote_service.reject(db, str(auth.organization_id), quote_id, reason)
            db.commit()
        except Exception:
            db.rollback()
        return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)

    def convert_to_invoice_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        quote_id: str,
    ) -> RedirectResponse:
        """Handle convert to invoice action."""
        try:
            invoice = quote_service.convert_to_invoice(
                db, str(auth.organization_id), quote_id, str(auth.user_id)
            )
            db.commit()
            return RedirectResponse(url=f"/ar/invoices/{invoice.invoice_id}", status_code=303)
        except Exception:
            db.rollback()
            return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)

    def convert_to_sales_order_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        quote_id: str,
        customer_po_number: Optional[str] = None,
    ) -> RedirectResponse:
        """Handle convert to sales order action."""
        try:
            so = quote_service.convert_to_sales_order(
                db, str(auth.organization_id), quote_id, str(auth.user_id),
                customer_po_number=customer_po_number,
            )
            db.commit()
            return RedirectResponse(url=f"/sales-orders/{so.so_id}", status_code=303)
        except Exception:
            db.rollback()
            return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)

    def void_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        quote_id: str,
    ) -> RedirectResponse:
        """Handle void quote action."""
        try:
            quote_service.void(db, str(auth.organization_id), quote_id, str(auth.user_id))
            db.commit()
        except Exception:
            db.rollback()
        return RedirectResponse(url=f"/quotes/{quote_id}", status_code=303)


# Module-level singleton
quote_web_service = QuoteWebService()
