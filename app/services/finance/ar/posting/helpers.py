"""
AR Posting Helpers - Shared utilities for AR GL posting.

Provides:
- Tax transaction creation for AR invoices
"""

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice
from app.models.finance.ar.invoice_line import InvoiceLine
from app.services.finance.tax.tax_transaction import tax_transaction_service

logger = logging.getLogger(__name__)


def create_tax_transactions(
    db: Session,
    organization_id: UUID,
    invoice: Invoice,
    lines: list[InvoiceLine],
    customer: Customer,
    exchange_rate: Decimal,
    is_credit_note: bool = False,
) -> list[UUID]:
    """
    Create tax transactions for invoice lines with tax codes.

    Args:
        db: Database session
        organization_id: Organization scope
        invoice: The invoice being posted
        lines: Invoice lines
        customer: Customer for counterparty info
        exchange_rate: Exchange rate to functional currency
        is_credit_note: Whether this is a credit note (negative amounts)

    Returns:
        List of created tax transaction IDs
    """
    from app.models.finance.gl.fiscal_period import FiscalPeriod

    tax_transaction_ids: list[UUID] = []

    # Get fiscal period from invoice date
    fiscal_period = db.scalar(
        select(FiscalPeriod).where(
            FiscalPeriod.organization_id == organization_id,
            FiscalPeriod.start_date <= invoice.invoice_date,
            FiscalPeriod.end_date >= invoice.invoice_date,
        )
    )

    if not fiscal_period:
        # No fiscal period found - skip tax transactions
        return tax_transaction_ids

    for line in lines:
        if not line.tax_code_id or line.tax_amount == Decimal("0"):
            continue

        # For credit notes, we record negative tax (reduces output tax)
        base_amount = line.line_amount if not is_credit_note else -line.line_amount

        try:
            tax_txn = tax_transaction_service.create_from_invoice_line(
                db=db,
                organization_id=organization_id,
                fiscal_period_id=fiscal_period.fiscal_period_id,
                tax_code_id=line.tax_code_id,
                invoice_id=invoice.invoice_id,
                invoice_line_id=line.line_id,
                invoice_number=invoice.invoice_number,
                transaction_date=invoice.invoice_date,
                is_purchase=False,  # AR = OUTPUT tax (sales)
                base_amount=base_amount,
                currency_code=invoice.currency_code,
                counterparty_name=customer.legal_name,
                counterparty_tax_id=customer.tax_identification_number,
                exchange_rate=exchange_rate,
            )
            tax_transaction_ids.append(tax_txn.transaction_id)
        except Exception:
            # Log error but don't fail the posting
            logger.exception(
                "create_tax_transaction failed for AR invoice %s",
                invoice.invoice_number,
            )

    return tax_transaction_ids
