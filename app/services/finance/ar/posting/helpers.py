"""
AR Posting Helpers - Shared utilities for AR GL posting.

Provides:
- Tax transaction creation for AR invoices
"""

import logging
from decimal import Decimal
from typing import TypedDict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.services.finance.posting.tax import (
    create_invoice_tax_transactions,
    prorate_amount as _prorate,
    resolve_tax_posting_account_id as _resolve_tax_posting_account_id,
)
from app.services.finance.tax.tax_transaction import tax_transaction_service

logger = logging.getLogger(__name__)


class CashVATReclassEntry(TypedDict):
    deferred_account_id: UUID
    current_account_id: UUID
    tax_amount: Decimal


class CashVATRecognitionPayload(TypedDict):
    tax_code_id: UUID
    source_document_line_id: UUID | None
    source_document_reference: str | None
    base_amount: Decimal
    tax_amount: Decimal


def resolve_tax_posting_account_id(
    db: Session,
    organization_id: UUID,
    tax_code_id: UUID,
    *,
    prefer_deferred: bool,
) -> UUID | None:
    return _resolve_tax_posting_account_id(
        db,
        organization_id,
        tax_code_id,
        tax_account_attr="tax_collected_account_id",
        prefer_deferred=prefer_deferred,
    )


def create_tax_transactions(
    db: Session,
    organization_id: UUID,
    invoice: Invoice,
    lines: list[InvoiceLine],
    customer: Customer,
    exchange_rate: Decimal,
    is_credit_note: bool = False,
) -> list[UUID]:
    """Create AR tax transactions for invoice lines with tax codes."""
    return create_invoice_tax_transactions(
        db,
        organization_id,
        invoice=invoice,
        lines=lines,
        counterparty_name=customer.legal_name,
        counterparty_tax_id=customer.tax_identification_number,
        exchange_rate=exchange_rate,
        is_purchase=False,
        tax_service=tax_transaction_service,
        is_credit_note=is_credit_note,
        log_label="AR",
    )


def build_cash_vat_reclass_entries(
    db: Session,
    organization_id: UUID,
    allocations: list[PaymentAllocation],
) -> tuple[list[CashVATReclassEntry], list[CashVATRecognitionPayload]]:
    """Build AR payment-time VAT reclass entries and tax-recognition payloads."""
    journal_entries: list[CashVATReclassEntry] = []
    tax_payloads: list[CashVATRecognitionPayload] = []

    for allocation in allocations:
        invoice = db.get(Invoice, allocation.invoice_id)
        if not invoice or invoice.organization_id != organization_id:
            continue
        if invoice.total_amount == Decimal("0"):
            continue

        line_taxes = db.scalars(
            select(InvoiceLineTax)
            .join(InvoiceLine, InvoiceLine.line_id == InvoiceLineTax.line_id)
            .join(TaxCode, TaxCode.tax_code_id == InvoiceLineTax.tax_code_id)
            .where(
                InvoiceLine.invoice_id == invoice.invoice_id,
                TaxCode.tax_type.in_({TaxType.VAT, TaxType.GST}),
            )
        ).all()

        for line_tax in line_taxes:
            current_account_id = resolve_tax_posting_account_id(
                db,
                organization_id,
                line_tax.tax_code_id,
                prefer_deferred=False,
            )
            deferred_account_id = resolve_tax_posting_account_id(
                db,
                organization_id,
                line_tax.tax_code_id,
                prefer_deferred=True,
            )
            if (
                not current_account_id
                or not deferred_account_id
                or current_account_id == deferred_account_id
            ):
                continue

            tax_amount = _prorate(
                allocation.allocated_amount,
                line_tax.tax_amount,
                invoice.total_amount,
            )
            base_amount = _prorate(
                allocation.allocated_amount,
                line_tax.base_amount,
                invoice.total_amount,
            )
            if tax_amount == Decimal("0"):
                continue

            journal_entries.append(
                {
                    "deferred_account_id": deferred_account_id,
                    "current_account_id": current_account_id,
                    "tax_amount": tax_amount,
                }
            )
            tax_payloads.append(
                {
                    "tax_code_id": line_tax.tax_code_id,
                    "source_document_line_id": allocation.allocation_id,
                    "source_document_reference": invoice.invoice_number,
                    "base_amount": base_amount,
                    "tax_amount": tax_amount,
                }
            )

    return journal_entries, tax_payloads
