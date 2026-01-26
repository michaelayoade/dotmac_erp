"""
Quote Service.

Business logic for sales quotes with conversion to invoices/sales orders.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, List
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.ar.quote import Quote, QuoteLine, QuoteStatus
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.payment_terms import PaymentTerms
from app.models.finance.ar.invoice import Invoice, InvoiceType, InvoiceStatus
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.sales_order import SalesOrder, SalesOrderLine, SOStatus
from app.models.finance.core_config import SequenceType
from app.models.finance.core_org.cost_center import CostCenter
from app.models.finance.core_org.project import Project
from app.models.finance.gl.account import Account
from app.models.finance.tax.tax_code import TaxCode
from app.services.common import coerce_uuid
from app.services.finance.common import SyncNumberingService, get_org_scoped_entity


class QuoteService:
    """Service for quote operations."""

    @staticmethod
    def _get_quote(
        db: Session,
        organization_id: UUID | str,
        quote_id: UUID | str,
    ) -> Quote:
        quote = get_org_scoped_entity(
            db=db,
            model_class=Quote,
            entity_id=quote_id,
            org_id=organization_id,
            entity_name="Quote",
        )
        if not quote:
            raise HTTPException(status_code=404, detail="Quote not found")
        return quote

    @staticmethod
    def generate_quote_number(db: Session, organization_id: UUID) -> str:
        """Generate unique quote number using configurable sequence."""
        numbering_service = SyncNumberingService(db)
        return numbering_service.generate_next_number(
            organization_id=organization_id,
            sequence_type=SequenceType.QUOTE,
        )

    @staticmethod
    def create(
        db: Session,
        organization_id: str,
        customer_id: str,
        quote_date: date,
        valid_until: date,
        created_by: str,
        currency_code: str = settings.default_functional_currency_code,
        exchange_rate: Decimal = Decimal("1"),
        reference: Optional[str] = None,
        contact_name: Optional[str] = None,
        contact_email: Optional[str] = None,
        payment_terms_id: Optional[str] = None,
        terms_and_conditions: Optional[str] = None,
        internal_notes: Optional[str] = None,
        customer_notes: Optional[str] = None,
        lines: Optional[List[dict]] = None,
    ) -> Quote:
        """Create a new quote."""
        org_id = coerce_uuid(organization_id)

        get_org_scoped_entity(
            db=db,
            model_class=Customer,
            entity_id=customer_id,
            org_id=org_id,
            entity_name="Customer",
        )
        if payment_terms_id:
            get_org_scoped_entity(
                db=db,
                model_class=PaymentTerms,
                entity_id=payment_terms_id,
                org_id=org_id,
                entity_name="Payment terms",
            )

        quote = Quote(
            organization_id=org_id,
            quote_number=QuoteService.generate_quote_number(db, org_id),
            customer_id=coerce_uuid(customer_id),
            quote_date=quote_date,
            valid_until=valid_until,
            currency_code=currency_code,
            exchange_rate=exchange_rate,
            reference=reference,
            contact_name=contact_name,
            contact_email=contact_email,
            payment_terms_id=coerce_uuid(payment_terms_id) if payment_terms_id else None,
            terms_and_conditions=terms_and_conditions,
            internal_notes=internal_notes,
            customer_notes=customer_notes,
            status=QuoteStatus.DRAFT,
            created_by=coerce_uuid(created_by),
        )

        db.add(quote)
        db.flush()

        # Add lines
        if lines:
            QuoteService._add_lines(db, quote, lines, org_id)

        # Recalculate totals
        QuoteService._recalculate_totals(quote)
        db.flush()

        return quote

    @staticmethod
    def _add_lines(db: Session, quote: Quote, lines: List[dict], org_id: UUID) -> None:
        """Add lines to quote."""
        for idx, line_data in enumerate(lines, start=1):
            tax_code_id = line_data.get("tax_code_id")
            revenue_account_id = line_data.get("revenue_account_id")
            project_id = line_data.get("project_id")
            cost_center_id = line_data.get("cost_center_id")

            if tax_code_id:
                get_org_scoped_entity(
                    db=db,
                    model_class=TaxCode,
                    entity_id=tax_code_id,
                    org_id=org_id,
                    entity_name="Tax code",
                )
            if revenue_account_id:
                get_org_scoped_entity(
                    db=db,
                    model_class=Account,
                    entity_id=revenue_account_id,
                    org_id=org_id,
                    entity_name="Revenue account",
                )
            if project_id:
                get_org_scoped_entity(
                    db=db,
                    model_class=Project,
                    entity_id=project_id,
                    org_id=org_id,
                    entity_name="Project",
                )
            if cost_center_id:
                get_org_scoped_entity(
                    db=db,
                    model_class=CostCenter,
                    entity_id=cost_center_id,
                    org_id=org_id,
                    entity_name="Cost center",
                )

            quantity = Decimal(str(line_data.get("quantity", 1)))
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

            line = QuoteLine(
                quote_id=quote.quote_id,
                line_number=idx,
                item_code=line_data.get("item_code"),
                description=line_data["description"],
                quantity=quantity,
                unit_of_measure=line_data.get("unit_of_measure"),
                unit_price=unit_price,
                discount_percent=discount_percent,
                discount_amount=discount_amount,
                tax_code_id=coerce_uuid(tax_code_id) if tax_code_id else None,
                tax_amount=tax_amount,
                line_total=line_total,
                revenue_account_id=coerce_uuid(revenue_account_id) if revenue_account_id else None,
                project_id=coerce_uuid(project_id) if project_id else None,
                cost_center_id=coerce_uuid(cost_center_id) if cost_center_id else None,
            )
            db.add(line)

    @staticmethod
    def _recalculate_totals(quote: Quote) -> None:
        """Recalculate quote totals from lines."""
        subtotal = Decimal("0")
        discount = Decimal("0")
        tax = Decimal("0")

        for line in quote.lines:
            gross = line.quantity * line.unit_price
            subtotal += gross
            discount += line.discount_amount
            tax += line.tax_amount

        quote.subtotal = subtotal
        quote.discount_amount = discount
        quote.tax_amount = tax
        quote.total_amount = subtotal - discount + tax

    @staticmethod
    def update(
        db: Session,
        organization_id: str,
        quote_id: str,
        updated_by: str,
        **kwargs,
    ) -> Quote:
        """Update a quote (only if DRAFT)."""
        quote = QuoteService._get_quote(db, organization_id, quote_id)

        if quote.status != QuoteStatus.DRAFT:
            raise ValueError(f"Cannot update quote in {quote.status.value} status")

        if "customer_id" in kwargs and kwargs["customer_id"]:
            get_org_scoped_entity(
                db=db,
                model_class=Customer,
                entity_id=kwargs["customer_id"],
                org_id=organization_id,
                entity_name="Customer",
            )
        if "payment_terms_id" in kwargs and kwargs["payment_terms_id"]:
            get_org_scoped_entity(
                db=db,
                model_class=PaymentTerms,
                entity_id=kwargs["payment_terms_id"],
                org_id=organization_id,
                entity_name="Payment terms",
            )

        # Update allowed fields
        allowed_fields = [
            "customer_id", "quote_date", "valid_until", "currency_code",
            "exchange_rate", "reference", "contact_name", "contact_email",
            "payment_terms_id", "terms_and_conditions", "internal_notes",
            "customer_notes",
        ]

        for field in allowed_fields:
            if field in kwargs and kwargs[field] is not None:
                value = kwargs[field]
                if field in ["customer_id", "payment_terms_id"] and value:
                    value = coerce_uuid(value)
                setattr(quote, field, value)

        quote.updated_by = coerce_uuid(updated_by)
        quote.updated_at = datetime.utcnow()

        db.flush()
        return quote

    @staticmethod
    def send(
        db: Session,
        organization_id: str,
        quote_id: str,
        sent_by: str,
    ) -> Quote:
        """Mark quote as sent."""
        quote = QuoteService._get_quote(db, organization_id, quote_id)

        if quote.status not in [QuoteStatus.DRAFT, QuoteStatus.SENT]:
            raise ValueError(f"Cannot send quote in {quote.status.value} status")

        quote.status = QuoteStatus.SENT
        quote.sent_by = coerce_uuid(sent_by)
        quote.sent_at = datetime.utcnow()

        db.flush()
        return quote

    @staticmethod
    def mark_viewed(
        db: Session,
        organization_id: str,
        quote_id: str,
    ) -> Quote:
        """Mark quote as viewed by customer."""
        quote = QuoteService._get_quote(db, organization_id, quote_id)

        if quote.status == QuoteStatus.SENT:
            quote.status = QuoteStatus.VIEWED
            quote.viewed_at = datetime.utcnow()
            db.flush()

        return quote

    @staticmethod
    def accept(
        db: Session,
        organization_id: str,
        quote_id: str,
    ) -> Quote:
        """Mark quote as accepted by customer."""
        quote = QuoteService._get_quote(db, organization_id, quote_id)

        if quote.status not in [QuoteStatus.SENT, QuoteStatus.VIEWED]:
            raise ValueError(f"Cannot accept quote in {quote.status.value} status")

        # Check if expired
        if quote.valid_until < date.today():
            quote.status = QuoteStatus.EXPIRED
            db.flush()
            raise ValueError("Quote has expired")

        quote.status = QuoteStatus.ACCEPTED
        quote.accepted_at = datetime.utcnow()

        db.flush()
        return quote

    @staticmethod
    def reject(
        db: Session,
        organization_id: str,
        quote_id: str,
        reason: Optional[str] = None,
    ) -> Quote:
        """Mark quote as rejected by customer."""
        quote = QuoteService._get_quote(db, organization_id, quote_id)

        if quote.status not in [QuoteStatus.SENT, QuoteStatus.VIEWED]:
            raise ValueError(f"Cannot reject quote in {quote.status.value} status")

        quote.status = QuoteStatus.REJECTED
        quote.rejected_at = datetime.utcnow()
        quote.rejection_reason = reason

        db.flush()
        return quote

    @staticmethod
    def convert_to_invoice(
        db: Session,
        organization_id: str,
        quote_id: str,
        created_by: str,
        invoice_date: Optional[date] = None,
    ) -> Invoice:
        """Convert accepted quote to invoice."""
        quote = QuoteService._get_quote(db, organization_id, quote_id)

        if quote.status != QuoteStatus.ACCEPTED:
            raise ValueError(f"Can only convert accepted quotes, current status: {quote.status.value}")

        user_id = coerce_uuid(created_by)

        # Generate invoice number using configurable sequence
        numbering_service = SyncNumberingService(db)
        invoice_number = numbering_service.generate_next_number(
            organization_id=quote.organization_id,
            sequence_type=SequenceType.INVOICE,
        )

        # Create invoice
        invoice = Invoice(
            organization_id=quote.organization_id,
            invoice_number=invoice_number,
            invoice_type=InvoiceType.STANDARD,
            customer_id=quote.customer_id,
            invoice_date=invoice_date or date.today(),
            due_date=invoice_date or date.today(),  # Will be calculated based on payment terms
            currency_code=quote.currency_code,
            exchange_rate=quote.exchange_rate,
            payment_terms_id=quote.payment_terms_id,
            subtotal=quote.subtotal,
            discount_amount=quote.discount_amount,
            tax_amount=quote.tax_amount,
            total_amount=quote.total_amount,
            reference=f"Quote: {quote.quote_number}",
            status=InvoiceStatus.DRAFT,
            created_by=user_id,
        )
        db.add(invoice)
        db.flush()

        # Copy lines
        for qline in quote.lines:
            iline = InvoiceLine(
                invoice_id=invoice.invoice_id,
                line_number=qline.line_number,
                description=qline.description,
                quantity=qline.quantity,
                unit_price=qline.unit_price,
                discount_percent=qline.discount_percent,
                discount_amount=qline.discount_amount,
                tax_code_id=qline.tax_code_id,
                tax_amount=qline.tax_amount,
                line_total=qline.line_total,
                revenue_account_id=qline.revenue_account_id,
                project_id=qline.project_id,
                cost_center_id=qline.cost_center_id,
            )
            db.add(iline)

        # Update quote
        quote.status = QuoteStatus.CONVERTED
        quote.converted_to_invoice_id = invoice.invoice_id
        quote.converted_at = datetime.utcnow()

        db.flush()
        return invoice

    @staticmethod
    def convert_to_sales_order(
        db: Session,
        organization_id: str,
        quote_id: str,
        created_by: str,
        order_date: Optional[date] = None,
        customer_po_number: Optional[str] = None,
    ) -> SalesOrder:
        """Convert accepted quote to sales order."""
        quote = QuoteService._get_quote(db, organization_id, quote_id)

        if quote.status != QuoteStatus.ACCEPTED:
            raise ValueError(f"Can only convert accepted quotes, current status: {quote.status.value}")

        user_id = coerce_uuid(created_by)

        # Generate SO number using configurable sequence
        numbering_service = SyncNumberingService(db)
        so_number = numbering_service.generate_next_number(
            organization_id=quote.organization_id,
            sequence_type=SequenceType.SALES_ORDER,
        )

        # Create sales order
        so = SalesOrder(
            organization_id=quote.organization_id,
            so_number=so_number,
            customer_id=quote.customer_id,
            quote_id=quote.quote_id,
            order_date=order_date or date.today(),
            customer_po_number=customer_po_number,
            currency_code=quote.currency_code,
            exchange_rate=quote.exchange_rate,
            payment_terms_id=quote.payment_terms_id,
            subtotal=quote.subtotal,
            discount_amount=quote.discount_amount,
            tax_amount=quote.tax_amount,
            total_amount=quote.total_amount,
            reference=f"Quote: {quote.quote_number}",
            customer_notes=quote.customer_notes,
            internal_notes=quote.internal_notes,
            status=SOStatus.DRAFT,
            created_by=user_id,
        )
        db.add(so)
        db.flush()

        # Copy lines
        for qline in quote.lines:
            so_line = SalesOrderLine(
                so_id=so.so_id,
                line_number=qline.line_number,
                item_code=qline.item_code,
                description=qline.description,
                quantity_ordered=qline.quantity,
                unit_of_measure=qline.unit_of_measure,
                unit_price=qline.unit_price,
                discount_percent=qline.discount_percent,
                discount_amount=qline.discount_amount,
                tax_code_id=qline.tax_code_id,
                tax_amount=qline.tax_amount,
                line_total=qline.line_total,
                revenue_account_id=qline.revenue_account_id,
                project_id=qline.project_id,
                cost_center_id=qline.cost_center_id,
            )
            db.add(so_line)

        # Update quote
        quote.status = QuoteStatus.CONVERTED
        quote.converted_to_so_id = so.so_id
        quote.converted_at = datetime.utcnow()

        db.flush()
        return so

    @staticmethod
    def void(
        db: Session,
        organization_id: str,
        quote_id: str,
        voided_by: str,
    ) -> Quote:
        """Void a quote."""
        quote = QuoteService._get_quote(db, organization_id, quote_id)

        if quote.status == QuoteStatus.CONVERTED:
            raise ValueError("Cannot void a converted quote")

        quote.status = QuoteStatus.VOID
        quote.updated_by = coerce_uuid(voided_by)
        quote.updated_at = datetime.utcnow()

        db.flush()
        return quote

    @staticmethod
    def expire_quotes(db: Session) -> int:
        """Mark expired quotes. Returns count of expired quotes."""
        today = date.today()
        count = (
            db.query(Quote)
            .filter(
                Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT, QuoteStatus.VIEWED]),
                Quote.valid_until < today,
            )
            .update({"status": QuoteStatus.EXPIRED}, synchronize_session=False)
        )
        db.flush()
        return count

    @staticmethod
    def list_quotes(
        db: Session,
        organization_id: str,
        customer_id: Optional[str] = None,
        status: Optional[QuoteStatus] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Quote]:
        """List quotes with filters."""
        org_id = coerce_uuid(organization_id)

        query = db.query(Quote).filter(Quote.organization_id == org_id)

        if customer_id:
            query = query.filter(Quote.customer_id == coerce_uuid(customer_id))

        if status:
            query = query.filter(Quote.status == status)

        if start_date:
            query = query.filter(Quote.quote_date >= start_date)

        if end_date:
            query = query.filter(Quote.quote_date <= end_date)

        return (
            query.order_by(Quote.quote_date.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )


quote_service = QuoteService()
