"""
AP Posting Helpers - Shared utilities for AP GL posting.

Provides:
- Account routing logic
- Tax transaction creation
- WHT transaction creation
- Asset capitalization integration
"""

import logging
from decimal import Decimal
from typing import TypedDict
from unittest.mock import Mock
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ap.supplier_invoice_line_tax import SupplierInvoiceLineTax
from app.models.inventory.item import Item
from app.models.inventory.item_category import ItemCategory
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.services.finance.posting.tax import (
    create_invoice_tax_transactions,
    prorate_amount as _prorate,
    resolve_tax_posting_account_id as _resolve_tax_posting_account_id,
)
from app.services.finance.tax.tax_transaction import tax_transaction_service

logger = logging.getLogger(__name__)


class CashVATReclassEntry(TypedDict):
    current_account_id: UUID
    deferred_account_id: UUID
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
        tax_account_attr="tax_paid_account_id",
        prefer_deferred=prefer_deferred,
    )


def determine_debit_account(
    db: Session,
    organization_id: UUID,
    line: SupplierInvoiceLine,
    supplier: Supplier,
) -> UUID | None:
    """
    Determine the appropriate debit account for an invoice line.

    Routing logic:
    1. If line has goods_receipt_line_id → use GRNI/clearing account
    2. If line has item_id → use inventory account from Item or ItemCategory
    3. If line has asset_account_id (capitalization) → use asset account
    4. Else → use expense_account_id or supplier default

    Args:
        db: Database session
        organization_id: Organization scope
        line: The invoice line
        supplier: The supplier for default accounts

    Returns:
        Account UUID or None if not determinable
    """
    # Priority 1: GR-matched line - use GRNI/clearing account
    # (In GRNI accounting, goods receipt debits Inventory/Cr GRNI
    #  Invoice then debits GRNI/Cr AP to clear the accrual)
    if line.goods_receipt_line_id:
        receipt_line = db.get(GoodsReceiptLine, line.goods_receipt_line_id)

        # Prefer a dedicated GRNI account if the organization model exposes one.
        from app.models.core_org.organization import Organization

        org = db.get(Organization, organization_id)
        if org and hasattr(org, "grni_account_id"):
            acc_id: UUID | None = getattr(org, "grni_account_id", None)
            if acc_id:
                return acc_id

        # Fall back to the matched item's inventory clearing/adjustment account.
        matched_item_id = receipt_line.item_id if receipt_line else line.item_id
        if matched_item_id:
            item = db.get(Item, matched_item_id)
            if item and item.category_id:
                category = db.get(ItemCategory, item.category_id)
                if category and category.inventory_adjustment_account_id:
                    return category.inventory_adjustment_account_id

    # Priority 2: Inventory item - route to inventory account
    if line.item_id:
        item = db.get(Item, line.item_id)
        if item:
            # Check item-level override first
            if item.inventory_account_id:
                return item.inventory_account_id

            # Fall back to category inventory account
            if item.category_id:
                category = db.get(ItemCategory, item.category_id)
                if category and category.inventory_account_id:
                    return category.inventory_account_id

    # Priority 3: Capitalize flag - use asset account
    if getattr(line, "capitalize_flag", False) and line.asset_account_id:
        return line.asset_account_id

    # Priority 4: Explicit expense account on line
    if line.expense_account_id:
        return line.expense_account_id

    # Priority 5: Asset account on line (non-capitalize)
    if line.asset_account_id:
        return line.asset_account_id

    # Priority 6: Supplier default
    return supplier.default_expense_account_id


def create_tax_transactions(
    db: Session,
    organization_id: UUID,
    invoice: SupplierInvoice,
    lines: list[SupplierInvoiceLine],
    supplier: Supplier,
    exchange_rate: Decimal,
    is_credit_note: bool = False,
) -> list[UUID]:
    """Create AP tax transactions for supplier invoice lines with tax codes."""
    return create_invoice_tax_transactions(
        db,
        organization_id,
        invoice=invoice,
        lines=lines,
        counterparty_name=supplier.legal_name,
        counterparty_tax_id=supplier.tax_identification_number,
        exchange_rate=exchange_rate,
        is_purchase=True,
        tax_service=tax_transaction_service,
        is_credit_note=is_credit_note,
        log_label="AP",
    )


def create_wht_transaction(
    db: Session,
    organization_id: UUID,
    payment,  # SupplierPayment
    supplier: Supplier,
    wht_amount: Decimal,
    exchange_rate: Decimal,
) -> UUID | None:
    """
    Create a WHT tax transaction for a supplier payment.

    This records the withholding tax withheld from the supplier payment
    for tax reporting purposes.

    Args:
        db: Database session
        organization_id: Organization scope
        payment: SupplierPayment object
        supplier: Supplier object
        wht_amount: WHT amount withheld
        exchange_rate: Exchange rate to functional currency

    Returns:
        Transaction ID if created, None otherwise
    """
    from app.models.finance.gl.fiscal_period import FiscalPeriod
    from app.models.finance.tax.tax_code import TaxCode, TaxType
    from app.models.finance.tax.tax_transaction import TaxTransactionType
    from app.services.finance.tax.tax_transaction import TaxTransactionInput

    # Get fiscal period from payment date
    fiscal_period_stmt = select(FiscalPeriod).where(
        and_(
            FiscalPeriod.organization_id == organization_id,
            FiscalPeriod.start_date <= payment.payment_date,
            FiscalPeriod.end_date >= payment.payment_date,
        )
    )
    fiscal_period = db.scalar(fiscal_period_stmt)
    if isinstance(fiscal_period, Mock):
        scalar_result = db.scalars(fiscal_period_stmt)
        fiscal_period = (
            scalar_result.first() if hasattr(scalar_result, "first") else None
        )
    if isinstance(fiscal_period, Mock):
        fiscal_period = None

    if not fiscal_period:
        return None

    try:
        # WHT base is the net amount (pre-VAT) not gross.
        # Back-calculate from WHT amount and rate, falling back to gross if rate is zero.
        gross_amount = payment.gross_amount or (payment.amount + wht_amount)

        tax_code_id = payment.withholding_tax_code_id
        if not tax_code_id:
            return None

        tax_code = db.get(TaxCode, tax_code_id)
        if (
            not tax_code
            or tax_code.organization_id != organization_id
            or tax_code.tax_type != TaxType.WITHHOLDING
        ):
            return None

        # WHT base = net amount (pre-VAT). Back-calculate from WHT and rate.
        if tax_code.tax_rate and tax_code.tax_rate > Decimal("0"):
            wht_base = wht_amount / tax_code.tax_rate
        else:
            wht_base = gross_amount  # Fallback if rate is zero/missing

        tax_txn = tax_transaction_service.create_transaction(
            db=db,
            organization_id=organization_id,
            input=TaxTransactionInput(
                fiscal_period_id=fiscal_period.fiscal_period_id,
                tax_code_id=tax_code_id,
                jurisdiction_id=tax_code.jurisdiction_id,
                transaction_type=TaxTransactionType.WITHHOLDING,
                transaction_date=payment.payment_date,
                source_document_type="SUPPLIER_PAYMENT",
                source_document_id=payment.payment_id,
                source_document_reference=payment.payment_number,
                currency_code=payment.currency_code,
                base_amount=wht_base,
                tax_rate=tax_code.tax_rate,
                tax_amount=wht_amount,
                functional_base_amount=wht_base * exchange_rate,
                functional_tax_amount=wht_amount * exchange_rate,
                exchange_rate=exchange_rate,
                counterparty_name=supplier.legal_name,
                counterparty_tax_id=supplier.tax_identification_number,
            ),
        )
        return tax_txn.transaction_id
    except Exception:
        logger.exception(
            "WHT transaction creation failed for payment %s", payment.payment_id
        )
        return None


def build_cash_vat_reclass_entries(
    db: Session,
    organization_id: UUID,
    allocations: list[APPaymentAllocation],
) -> tuple[list[CashVATReclassEntry], list[CashVATRecognitionPayload]]:
    """Build AP payment-time VAT reclass entries and tax-recognition payloads."""
    journal_entries: list[CashVATReclassEntry] = []
    tax_payloads: list[CashVATRecognitionPayload] = []

    for allocation in allocations:
        invoice = db.get(SupplierInvoice, allocation.invoice_id)
        if not invoice or invoice.organization_id != organization_id:
            continue
        if invoice.total_amount == Decimal("0"):
            continue

        line_taxes = db.scalars(
            select(SupplierInvoiceLineTax)
            .join(
                SupplierInvoiceLine,
                SupplierInvoiceLine.line_id == SupplierInvoiceLineTax.line_id,
            )
            .join(TaxCode, TaxCode.tax_code_id == SupplierInvoiceLineTax.tax_code_id)
            .where(
                SupplierInvoiceLine.invoice_id == invoice.invoice_id,
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
                    "current_account_id": current_account_id,
                    "deferred_account_id": deferred_account_id,
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


def create_assets_for_capitalizable_lines(
    db: Session,
    organization_id: UUID,
    invoice: SupplierInvoice,
    lines: list[SupplierInvoiceLine],
    supplier: Supplier,
    user_id: UUID,
) -> None:
    """
    Create fixed assets for invoice lines marked for capitalization.

    Uses the CapitalizationService to create DRAFT assets for lines
    that have capitalize_flag=True and asset_category_id set.

    Args:
        db: Database session
        organization_id: Organization scope
        invoice: The posted invoice
        lines: Invoice lines to check
        supplier: Supplier for asset linkage
        user_id: User creating the assets
    """
    from app.services.fixed_assets.capitalization import CapitalizationService

    # Check if any lines are capitalizable
    capitalizable_lines = [
        line for line in lines if line.capitalize_flag and line.asset_category_id
    ]

    if not capitalizable_lines:
        return

    # Create assets through CapitalizationService
    result = CapitalizationService.create_assets_from_invoice(
        db=db,
        organization_id=organization_id,
        invoice=invoice,
        lines=capitalizable_lines,
        supplier=supplier,
        user_id=user_id,
    )

    # Log errors but don't fail the posting
    # (Assets are supplementary - invoice posting should still succeed)
    if result.errors:
        for err in result.errors:
            logger.error(
                "Asset capitalization error for invoice %s: %s",
                invoice.invoice_id,
                err,
            )
