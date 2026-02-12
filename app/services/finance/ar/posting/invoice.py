"""
AR Invoice Posting - Post customer invoices to GL.

Transforms customer invoices into journal entries with:
- Debit: AR Control account (increase receivable)
- Credit: Revenue accounts (from invoice lines)
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalType
from app.services.common import coerce_uuid
from app.services.finance.ar.ar_inventory_integration import ARInventoryIntegration
from app.services.finance.ar.posting.helpers import create_tax_transactions
from app.services.finance.ar.posting.result import ARPostingResult
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
) -> ARPostingResult:
    """
    Post an AR invoice to the general ledger.

    Creates a journal entry with:
    - Debit: AR Control account
    - Credit: Revenue accounts (from invoice lines)

    Args:
        db: Database session
        organization_id: Organization scope
        invoice_id: Invoice to post
        posting_date: Date for the GL posting
        posted_by_user_id: User posting
        idempotency_key: Optional idempotency key

    Returns:
        ARPostingResult with outcome
    """
    org_id = coerce_uuid(organization_id)
    inv_id = coerce_uuid(invoice_id)
    user_id = coerce_uuid(posted_by_user_id)

    # Load invoice
    invoice = db.get(Invoice, inv_id)
    if not invoice or invoice.organization_id != org_id:
        return ARPostingResult(success=False, message="Invoice not found")

    # Allow posting for APPROVED (normal workflow) and for invoices that are
    # already in a posted state but missing GL entries (sync/import backfill).
    postable_statuses = {
        InvoiceStatus.APPROVED,
        InvoiceStatus.POSTED,
        InvoiceStatus.PAID,
        InvoiceStatus.PARTIALLY_PAID,
        InvoiceStatus.OVERDUE,
    }
    if invoice.status not in postable_statuses:
        return ARPostingResult(
            success=False,
            message=f"Invoice must be APPROVED or already posted to create GL entries (current: {invoice.status.value})",
        )

    # Load customer
    customer = db.get(Customer, invoice.customer_id)
    if not customer:
        return ARPostingResult(success=False, message="Customer not found")

    # Load invoice lines
    lines = (
        db.query(InvoiceLine)
        .filter(InvoiceLine.invoice_id == inv_id)
        .order_by(InvoiceLine.line_number)
        .all()
    )

    if not lines:
        return ARPostingResult(success=False, message="Invoice has no lines")

    # Get fiscal period for inventory transactions
    fiscal_period = (
        db.query(FiscalPeriod)
        .filter(
            FiscalPeriod.organization_id == org_id,
            FiscalPeriod.start_date <= invoice.invoice_date,
            FiscalPeriod.end_date >= invoice.invoice_date,
        )
        .first()
    )

    # Check if there are inventory lines
    inventory_lines = [line for line in lines if line.item_id]
    is_credit_note = invoice.invoice_type == InvoiceType.CREDIT_NOTE

    # Validate inventory availability for standard invoices (not credit notes)
    if inventory_lines and not is_credit_note:
        is_valid, validation_errors = (
            ARInventoryIntegration.validate_inventory_availability(
                db=db,
                organization_id=org_id,
                lines=inventory_lines,
            )
        )
        if not is_valid:
            return ARPostingResult(
                success=False,
                message=f"Insufficient inventory: {'; '.join(validation_errors)}",
            )

    # Build journal entry lines
    journal_lines: list[JournalLineInput] = []
    exchange_rate = invoice.exchange_rate or Decimal("1.0")

    # Debit line (AR Control account)
    total_functional = invoice.functional_currency_amount

    if invoice.invoice_type == InvoiceType.CREDIT_NOTE:
        # Credit note: credit AR (reduce receivable)
        journal_lines.append(
            JournalLineInput(
                account_id=invoice.ar_control_account_id,
                debit_amount=Decimal("0"),
                credit_amount=abs(invoice.total_amount),
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=abs(total_functional),
                description=f"AR Credit Note: {customer.legal_name}",
            )
        )
    else:
        # Standard: debit AR (increase receivable)
        journal_lines.append(
            JournalLineInput(
                account_id=invoice.ar_control_account_id,
                debit_amount=invoice.total_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=total_functional,
                credit_amount_functional=Decimal("0"),
                description=f"AR Invoice: {customer.legal_name}",
            )
        )

    # Credit lines (revenue accounts)
    for inv_line in lines:
        account_id: UUID | None = inv_line.revenue_account_id
        if not account_id:
            account_id = customer.default_revenue_account_id

        if not account_id:
            return ARPostingResult(
                success=False,
                message=f"No revenue account for line {inv_line.line_number}",
            )

        line_total = inv_line.line_amount + inv_line.tax_amount
        functional_amount = line_total * exchange_rate

        if invoice.invoice_type == InvoiceType.CREDIT_NOTE:
            # Credit note: debit revenue (reduce revenue)
            journal_lines.append(
                JournalLineInput(
                    account_id=account_id,
                    debit_amount=abs(line_total),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(functional_amount),
                    credit_amount_functional=Decimal("0"),
                    description=f"AR Credit Note: {inv_line.description}",
                    cost_center_id=inv_line.cost_center_id,
                    project_id=inv_line.project_id,
                    segment_id=inv_line.segment_id,
                )
            )
        else:
            # Standard: credit revenue
            journal_lines.append(
                JournalLineInput(
                    account_id=account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=line_total,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=functional_amount,
                    description=f"AR Invoice: {inv_line.description}",
                    cost_center_id=inv_line.cost_center_id,
                    project_id=inv_line.project_id,
                    segment_id=inv_line.segment_id,
                )
            )

    # Process inventory lines and get COGS journal entries
    inventory_result = None
    if inventory_lines and fiscal_period:
        inventory_result = ARInventoryIntegration.process_invoice_inventory(
            db=db,
            organization_id=org_id,
            invoice=invoice,
            lines=inventory_lines,
            fiscal_period_id=fiscal_period.fiscal_period_id,
            user_id=user_id,
            is_credit_note=is_credit_note,
        )

        # Add COGS journal lines to the entry
        if inventory_result.cogs_journal_lines:
            journal_lines.extend(inventory_result.cogs_journal_lines)

    # Create journal entry
    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=invoice.invoice_date,
        posting_date=posting_date,
        description=f"AR Invoice {invoice.invoice_number} - {customer.legal_name}",
        reference=invoice.invoice_number,
        currency_code=invoice.currency_code,
        exchange_rate=exchange_rate,
        exchange_rate_type_id=invoice.exchange_rate_type_id,
        lines=journal_lines,
        source_module="AR",
        source_document_type="INVOICE",
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
        return ARPostingResult(success=False, message=error.message)

    # Post to ledger
    if not idempotency_key:
        idempotency_key = BasePostingAdapter.make_idempotency_key(org_id, "AR", inv_id)

    posting_result = BasePostingAdapter.post_to_ledger(
        db,
        organization_id=org_id,
        journal_entry_id=journal.journal_entry_id,
        posting_date=posting_date,
        idempotency_key=idempotency_key,
        source_module="AR",
        correlation_id=invoice.correlation_id,
        posted_by_user_id=user_id,
        success_message="Invoice posted successfully",
    )
    if not posting_result.success:
        return ARPostingResult(
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
        customer=customer,
        exchange_rate=exchange_rate,
        is_credit_note=invoice.invoice_type == InvoiceType.CREDIT_NOTE,
    )

    return ARPostingResult(
        success=True,
        journal_entry_id=journal.journal_entry_id,
        posting_batch_id=posting_result.posting_batch_id,
        message=posting_result.message,
    )
