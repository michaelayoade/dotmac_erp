"""
AP Invoice Posting - Post supplier invoices to GL.

Transforms supplier invoices into journal entries with:
- Debit: Expense/Asset/Inventory accounts (from invoice lines)
- Debit: Stamp duty expense account (if stamp_duty_amount > 0)
- Credit: WHT payable account (if withholding_tax_amount > 0)
- Credit: AP Control account (net of WHT, plus stamp duty)
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
    SupplierInvoiceType,
)
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.gl.journal_entry import JournalType
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.services.common import coerce_uuid
from app.services.finance.ap.posting.helpers import (
    create_assets_for_capitalizable_lines,
    create_tax_transactions,
    determine_debit_account,
)
from app.services.finance.ap.posting.result import APPostingResult
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.posting.base import BasePostingAdapter


def post_invoice(
    db: Session,
    organization_id: UUID,
    invoice_id: UUID,
    posting_date: date,
    posted_by_user_id: UUID,
    idempotency_key: str | None = None,
    use_saga: bool = False,
    correlation_id: str | None = None,
) -> APPostingResult:
    """
    Post a supplier invoice to the general ledger.

    Creates a journal entry with:
    - Debit: Expense/Asset accounts (from invoice lines)
    - Credit: AP Control account

    Args:
        db: Database session
        organization_id: Organization scope
        invoice_id: Invoice to post
        posting_date: Date for the GL posting
        posted_by_user_id: User posting
        idempotency_key: Optional idempotency key
        use_saga: If True, use saga pattern for transactional guarantees
        correlation_id: Optional correlation ID for tracing

    Returns:
        APPostingResult with outcome

    Raises:
        HTTPException: If posting fails
    """
    # Delegate to saga if requested
    if use_saga:
        from app.services.finance.ap.ap_posting_saga import post_invoice_with_saga

        result = post_invoice_with_saga(
            db=db,
            organization_id=organization_id,
            invoice_id=invoice_id,
            posting_date=posting_date,
            posted_by_user_id=posted_by_user_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        return APPostingResult(
            success=result.success,
            journal_entry_id=result.journal_entry_id,
            posting_batch_id=result.posting_batch_id,
            message=result.message,
        )

    org_id = coerce_uuid(organization_id)
    inv_id = coerce_uuid(invoice_id)
    user_id = coerce_uuid(posted_by_user_id)

    # Load invoice with lines
    invoice = db.get(SupplierInvoice, inv_id)
    if not invoice or invoice.organization_id != org_id:
        return APPostingResult(success=False, message="Invoice not found")

    # Allow posting for APPROVED (normal workflow) and for invoices that are
    # already in a posted state but missing GL entries (sync/import backfill).
    postable_statuses = {
        SupplierInvoiceStatus.APPROVED,
        SupplierInvoiceStatus.POSTED,
        SupplierInvoiceStatus.PAID,
        SupplierInvoiceStatus.PARTIALLY_PAID,
    }
    if invoice.status not in postable_statuses:
        return APPostingResult(
            success=False,
            message=f"Invoice must be APPROVED or already posted to create GL entries (current: {invoice.status.value})",
        )

    # Skip zero-amount invoices — nothing meaningful to post to GL
    if invoice.total_amount == Decimal("0"):
        return APPostingResult(
            success=True,
            message="Zero amount invoice — no GL posting needed",
        )

    # Load supplier for control account
    supplier = db.get(Supplier, invoice.supplier_id)
    if not supplier:
        return APPostingResult(success=False, message="Supplier not found")

    # Load invoice lines
    lines = list(
        db.scalars(
            select(SupplierInvoiceLine)
            .where(SupplierInvoiceLine.invoice_id == inv_id)
            .order_by(SupplierInvoiceLine.line_number)
        ).all()
    )

    if not lines:
        return APPostingResult(success=False, message="Invoice has no lines")

    # Build journal entry lines
    journal_lines: list[JournalLineInput] = []
    exchange_rate = invoice.exchange_rate or Decimal("1.0")

    # Debit lines (expense/asset/inventory accounts)
    for inv_line in lines:
        # Determine account using smart routing
        account_id = determine_debit_account(db, org_id, inv_line, supplier)

        if not account_id:
            return APPostingResult(
                success=False,
                message=f"No expense account for line {inv_line.line_number}",
            )

        line_total = inv_line.line_amount + inv_line.tax_amount
        functional_amount = line_total * exchange_rate

        # For standard invoice: debit expense
        # For credit note: credit expense (negative amounts)
        if invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
            # Credit note: credit the expense (reduce expense)
            journal_lines.append(
                JournalLineInput(
                    account_id=account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=abs(line_total),
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=abs(functional_amount),
                    description=f"AP Credit Note: {inv_line.description}",
                    cost_center_id=inv_line.cost_center_id,
                    project_id=inv_line.project_id,
                    segment_id=inv_line.segment_id,
                )
            )
        else:
            # Standard/Debit note: debit expense
            journal_lines.append(
                JournalLineInput(
                    account_id=account_id,
                    debit_amount=line_total,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=functional_amount,
                    credit_amount_functional=Decimal("0"),
                    description=f"AP Invoice: {inv_line.description}",
                    cost_center_id=inv_line.cost_center_id,
                    project_id=inv_line.project_id,
                    segment_id=inv_line.segment_id,
                )
            )

    # ── Stamp duty debit line ──────────────────────────────────────
    stamp_duty_amount = getattr(invoice, "stamp_duty_amount", None) or Decimal("0")
    stamp_duty_code_id = getattr(invoice, "stamp_duty_code_id", None)
    if stamp_duty_amount > Decimal("0") and stamp_duty_code_id:
        stamp_code = db.get(TaxCode, stamp_duty_code_id)
        if not stamp_code or stamp_code.organization_id != org_id:
            return APPostingResult(
                success=False, message="Stamp duty tax code not found"
            )
        if stamp_code.tax_type != TaxType.STAMP_DUTY:
            return APPostingResult(
                success=False,
                message="Selected stamp duty code is not a STAMP_DUTY tax code",
            )
        # Use tax_expense_account_id (stamp duty is an expense to the buyer)
        stamp_account_id = stamp_code.tax_expense_account_id
        if not stamp_account_id:
            return APPostingResult(
                success=False,
                message="Stamp duty expense account is not configured on the tax code",
            )
        stamp_functional = stamp_duty_amount * exchange_rate
        if invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
            journal_lines.append(
                JournalLineInput(
                    account_id=stamp_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=abs(stamp_duty_amount),
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=abs(stamp_functional),
                    description=f"AP Credit Note stamp duty: {invoice.invoice_number}",
                )
            )
        else:
            journal_lines.append(
                JournalLineInput(
                    account_id=stamp_account_id,
                    debit_amount=stamp_duty_amount,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=stamp_functional,
                    credit_amount_functional=Decimal("0"),
                    description=f"AP Invoice stamp duty: {invoice.invoice_number}",
                )
            )

    # ── WHT credit line ────────────────────────────────────────────
    wht_amount = getattr(invoice, "withholding_tax_amount", None) or Decimal("0")
    withholding_tax_code_id = getattr(invoice, "withholding_tax_code_id", None)
    if wht_amount > Decimal("0") and withholding_tax_code_id:
        wht_code = db.get(TaxCode, withholding_tax_code_id)
        if not wht_code or wht_code.organization_id != org_id:
            return APPostingResult(success=False, message="WHT tax code not found")
        if wht_code.tax_type != TaxType.WITHHOLDING:
            return APPostingResult(
                success=False,
                message="Selected WHT code is not a WITHHOLDING tax code",
            )
        # WHT payable = tax_collected_account_id (amount owed to tax authority)
        wht_account_id = wht_code.tax_collected_account_id
        if not wht_account_id:
            return APPostingResult(
                success=False,
                message="WHT payable account is not configured on the WHT tax code",
            )
        wht_functional = wht_amount * exchange_rate
        if invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
            # Credit note reverses WHT: debit WHT payable
            journal_lines.append(
                JournalLineInput(
                    account_id=wht_account_id,
                    debit_amount=abs(wht_amount),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(wht_functional),
                    credit_amount_functional=Decimal("0"),
                    description=f"AP Credit Note WHT reversal: {invoice.invoice_number}",
                )
            )
        else:
            # Standard invoice: credit WHT payable (we owe tax authority)
            journal_lines.append(
                JournalLineInput(
                    account_id=wht_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=wht_amount,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=wht_functional,
                    description=f"AP Invoice WHT withheld: {invoice.invoice_number}",
                )
            )

    # ── AP Control credit line ─────────────────────────────────────
    # AP control = total_amount + stamp_duty - WHT
    # (stamp duty increases the obligation; WHT reduces what we owe the supplier)
    ap_amount = invoice.total_amount + stamp_duty_amount - wht_amount
    total_functional = invoice.functional_currency_amount
    ap_functional = (
        total_functional
        + (stamp_duty_amount * exchange_rate)
        - (wht_amount * exchange_rate)
    )

    if invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE:
        # Credit note: debit AP (reduce liability)
        journal_lines.append(
            JournalLineInput(
                account_id=invoice.ap_control_account_id,
                debit_amount=abs(ap_amount),
                credit_amount=Decimal("0"),
                debit_amount_functional=abs(ap_functional),
                credit_amount_functional=Decimal("0"),
                description=f"AP Credit Note: {supplier.legal_name}",
            )
        )
    else:
        # Standard/Debit note: credit AP (increase liability)
        journal_lines.append(
            JournalLineInput(
                account_id=invoice.ap_control_account_id,
                debit_amount=Decimal("0"),
                credit_amount=ap_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=ap_functional,
                description=f"AP Invoice: {supplier.legal_name}",
            )
        )

    # Create journal entry
    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=invoice.invoice_date,
        posting_date=posting_date,
        description=f"AP Invoice {invoice.invoice_number} - {supplier.legal_name}",
        reference=invoice.supplier_invoice_number or invoice.invoice_number,
        currency_code=invoice.currency_code,
        exchange_rate=exchange_rate,
        exchange_rate_type_id=invoice.exchange_rate_type_id,
        lines=journal_lines,
        source_module="AP",
        source_document_type="SUPPLIER_INVOICE",
        source_document_id=inv_id,
        correlation_id=invoice.correlation_id,
    )

    journal, error = BasePostingAdapter.create_and_approve_journal(
        db,
        org_id,
        journal_input,
        user_id,
        error_prefix="Journal creation failed",
    )
    if error:
        return APPostingResult(success=False, message=error.message)

    # Post to ledger
    if not idempotency_key:
        idempotency_key = BasePostingAdapter.make_idempotency_key(org_id, "AP", inv_id)

    posting_result = BasePostingAdapter.post_to_ledger(
        db,
        organization_id=org_id,
        journal_entry_id=journal.journal_entry_id,
        posting_date=posting_date,
        idempotency_key=idempotency_key,
        source_module="AP",
        correlation_id=invoice.correlation_id,
        posted_by_user_id=user_id,
        success_message="Invoice posted successfully",
    )
    if not posting_result.success:
        return APPostingResult(
            success=False,
            journal_entry_id=journal.journal_entry_id,
            message=posting_result.message,
        )

    # Create tax transactions for taxable invoice lines
    create_tax_transactions(
        db=db,
        organization_id=org_id,
        invoice=invoice,
        lines=lines,
        supplier=supplier,
        exchange_rate=exchange_rate,
        is_credit_note=invoice.invoice_type == SupplierInvoiceType.CREDIT_NOTE,
    )

    # Create fixed assets for capitalizable lines (AP → FA integration)
    # Only for standard invoices, not credit notes
    if invoice.invoice_type != SupplierInvoiceType.CREDIT_NOTE:
        create_assets_for_capitalizable_lines(
            db=db,
            organization_id=org_id,
            invoice=invoice,
            lines=lines,
            supplier=supplier,
            user_id=user_id,
        )

    return APPostingResult(
        success=True,
        journal_entry_id=journal.journal_entry_id,
        posting_batch_id=posting_result.posting_batch_id,
        message=posting_result.message,
    )
