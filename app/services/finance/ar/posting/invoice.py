"""
AR Invoice Posting - Post customer invoices to GL.

Transforms customer invoices into journal entries with:
- Debit: AR Control account (increase receivable)
- Credit: Revenue accounts (from invoice lines)
"""

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalType
from app.models.finance.tax.tax_code import TaxCode
from app.services.common import coerce_uuid
from app.services.finance.ar.ar_inventory_integration import ARInventoryIntegration
from app.services.finance.ar.posting.helpers import create_tax_transactions
from app.services.finance.ar.posting.result import ARPostingResult
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.posting.base import BasePostingAdapter

logger = logging.getLogger(__name__)


def _allocate_delta_across_lines(
    base_amounts: list[Decimal],
    delta: Decimal,
) -> list[Decimal]:
    """Allocate a header/line delta across lines, keeping total exact."""
    if not base_amounts:
        return []

    # Use absolute base amounts for proportional allocation to avoid sign issues.
    weights = [abs(v) for v in base_amounts]
    total_weight = sum(weights)

    if total_weight == Decimal("0"):
        # Degenerate case: push the full delta to first line.
        adjusted = [Decimal("0")] * len(base_amounts)
        adjusted[0] = delta
        return adjusted

    allocated: list[Decimal] = []
    running = Decimal("0")
    last_idx = len(base_amounts) - 1

    for idx, weight in enumerate(weights):
        if idx == last_idx:
            part = delta - running
        else:
            part = (delta * weight) / total_weight
            running += part
        allocated.append(part)

    return allocated


def _resolve_tax_accounts(
    db: Session,
    organization_id: UUID,
    lines: list[InvoiceLine],
) -> dict[UUID, UUID]:
    """Resolve tax code -> tax_collected_account_id for the invoice lines."""
    tax_code_ids = {line.tax_code_id for line in lines if line.tax_code_id}
    if not tax_code_ids:
        return {}

    tax_codes = db.scalars(
        select(TaxCode).where(
            and_(
                TaxCode.organization_id == organization_id,
                TaxCode.tax_code_id.in_(tax_code_ids),
                TaxCode.tax_collected_account_id.isnot(None),
            )
        )
    ).all()

    accounts_by_tax_code: dict[UUID, UUID] = {}
    for tax_code in tax_codes:
        if tax_code.tax_collected_account_id:
            accounts_by_tax_code[tax_code.tax_code_id] = (
                tax_code.tax_collected_account_id
            )
    return accounts_by_tax_code


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

    # Idempotency: if this invoice already has a GL journal, skip.
    if invoice.journal_entry_id is not None:
        return ARPostingResult(
            success=True,
            journal_entry_id=invoice.journal_entry_id,
            message="Invoice already posted to GL (idempotent)",
        )

    # Secondary idempotency guard: check if a journal already exists for
    # this source document (protects against journal_entry_id not being
    # written back due to RLS or session issues).
    from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus

    existing_journal = db.scalar(
        select(JournalEntry).where(
            JournalEntry.source_module == "AR",
            JournalEntry.source_document_type == "INVOICE",
            JournalEntry.source_document_id == inv_id,
            JournalEntry.status.notin_([JournalStatus.VOID, JournalStatus.REVERSED]),
            JournalEntry.journal_type != JournalType.REVERSAL,
        )
    )
    if existing_journal:
        # Backfill the missing reference
        invoice.journal_entry_id = existing_journal.journal_entry_id
        db.flush()
        logger.info(
            "Invoice %s already has journal %s — backfilled reference",
            inv_id,
            existing_journal.journal_number,
        )
        return ARPostingResult(
            success=True,
            journal_entry_id=existing_journal.journal_entry_id,
            message="Invoice already posted to GL (backfilled reference)",
        )

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

    # Skip zero-amount invoices — nothing meaningful to post to GL
    if invoice.total_amount == Decimal("0"):
        return ARPostingResult(
            success=True,
            message="Zero amount invoice — no GL posting needed",
        )

    # Load customer
    customer = db.get(Customer, invoice.customer_id)
    if not customer:
        return ARPostingResult(success=False, message="Customer not found")

    # Load invoice lines
    lines = list(
        db.scalars(
            select(InvoiceLine)
            .where(InvoiceLine.invoice_id == inv_id)
            .order_by(InvoiceLine.line_number)
        ).all()
    )

    if not lines:
        return ARPostingResult(success=False, message="Invoice has no lines")

    # Get fiscal period for inventory transactions
    fiscal_period = db.scalars(
        select(FiscalPeriod).where(
            and_(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= invoice.invoice_date,
                FiscalPeriod.end_date >= invoice.invoice_date,
            )
        )
    ).first()

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
    tax_accounts_by_code = _resolve_tax_accounts(db, org_id, lines)

    # For strict tax-account posting, tax should go to tax liability accounts.
    # When unavailable (legacy/sync data), keep backward-compatible behavior by
    # rolling tax into revenue for that line.
    revenue_base_totals: list[Decimal] = []
    line_tax_posting: list[tuple[UUID, Decimal] | None] = []
    for line in lines:
        line_revenue = line.line_amount
        line_tax = line.tax_amount or Decimal("0")
        tax_account_id = (
            tax_accounts_by_code.get(line.tax_code_id) if line.tax_code_id else None
        )
        if line_tax != Decimal("0") and tax_account_id:
            line_tax_posting.append((tax_account_id, line_tax))
        else:
            # Fallback keeps invoice postable even when tax-account mapping is missing.
            line_revenue += line_tax
            line_tax_posting.append(None)
        revenue_base_totals.append(line_revenue)

    # Backfill imports can have header/line deltas. Keep balancing behavior.
    lines_total = sum(revenue_base_totals, Decimal("0")) + sum(
        (t[1] for t in line_tax_posting if t is not None),
        Decimal("0"),
    )
    header_total = invoice.total_amount
    line_adjustments = [Decimal("0")] * len(lines)

    if header_total != lines_total:
        delta = header_total - lines_total
        line_adjustments = _allocate_delta_across_lines(revenue_base_totals, delta)
        logger.warning(
            "Applying header/line delta allocation for invoice %s (status=%s): header=%s, lines=%s, delta=%s",
            invoice.invoice_id,
            invoice.status.value,
            header_total,
            lines_total,
            delta,
        )

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
    for idx, inv_line in enumerate(lines):
        account_id: UUID | None = inv_line.revenue_account_id
        if not account_id:
            account_id = customer.default_revenue_account_id

        if not account_id:
            return ARPostingResult(
                success=False,
                message=f"No revenue account for line {inv_line.line_number}",
            )

        revenue_total = revenue_base_totals[idx] + line_adjustments[idx]
        if revenue_total == Decimal("0"):
            continue  # Skip zero-amount lines (e.g. bundled equipment at no charge)
        functional_revenue = revenue_total * exchange_rate

        if invoice.invoice_type == InvoiceType.CREDIT_NOTE:
            # Credit note: debit revenue (reduce revenue)
            journal_lines.append(
                JournalLineInput(
                    account_id=account_id,
                    debit_amount=abs(revenue_total),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(functional_revenue),
                    credit_amount_functional=Decimal("0"),
                    description=f"AR Credit Note: {inv_line.description}",
                    cost_center_id=inv_line.cost_center_id,
                    project_id=inv_line.project_id,
                    segment_id=inv_line.segment_id,
                )
            )
        else:
            # Standard: credit revenue.  Negative lines (discounts) flip to debit.
            if revenue_total < Decimal("0"):
                journal_lines.append(
                    JournalLineInput(
                        account_id=account_id,
                        debit_amount=abs(revenue_total),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(functional_revenue),
                        credit_amount_functional=Decimal("0"),
                        description=f"AR Discount: {inv_line.description}",
                        cost_center_id=inv_line.cost_center_id,
                        project_id=inv_line.project_id,
                        segment_id=inv_line.segment_id,
                    )
                )
            else:
                journal_lines.append(
                    JournalLineInput(
                        account_id=account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=revenue_total,
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=functional_revenue,
                        description=f"AR Invoice: {inv_line.description}",
                        cost_center_id=inv_line.cost_center_id,
                        project_id=inv_line.project_id,
                        segment_id=inv_line.segment_id,
                    )
                )

        # Dedicated tax-account posting rule (when tax account is configured).
        tax_post = line_tax_posting[idx]
        if tax_post is not None:
            tax_account_id, tax_amount = tax_post
            functional_tax = tax_amount * exchange_rate
            if invoice.invoice_type == InvoiceType.CREDIT_NOTE:
                journal_lines.append(
                    JournalLineInput(
                        account_id=tax_account_id,
                        debit_amount=abs(tax_amount),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(functional_tax),
                        credit_amount_functional=Decimal("0"),
                        description=f"AR Credit Note Tax: {inv_line.description}",
                    )
                )
            else:
                journal_lines.append(
                    JournalLineInput(
                        account_id=tax_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=tax_amount,
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=functional_tax,
                        description=f"AR Invoice Tax: {inv_line.description}",
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
