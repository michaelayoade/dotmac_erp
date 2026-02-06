"""
AP Reversal Posting - Reverse posted supplier invoices.

Uses the GL ReversalService to create reversing journal entries.
"""

from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.services.common import coerce_uuid
from app.services.finance.ap.posting.result import APPostingResult


def reverse_invoice_posting(
    db: Session,
    organization_id: UUID,
    invoice_id: UUID,
    reversal_date: date,
    reversed_by_user_id: UUID,
    reason: str,
) -> APPostingResult:
    """
    Reverse a posted invoice's GL entries.

    Args:
        db: Database session
        organization_id: Organization scope
        invoice_id: Invoice to reverse
        reversal_date: Date for reversal
        reversed_by_user_id: User reversing
        reason: Reason for reversal

    Returns:
        APPostingResult with reversal outcome
    """
    from app.services.finance.gl.reversal import ReversalService

    org_id = coerce_uuid(organization_id)
    inv_id = coerce_uuid(invoice_id)
    user_id = coerce_uuid(reversed_by_user_id)

    invoice = db.get(SupplierInvoice, inv_id)
    if not invoice or invoice.organization_id != org_id:
        return APPostingResult(success=False, message="Invoice not found")

    if not invoice.journal_entry_id:
        return APPostingResult(success=False, message="Invoice has not been posted")

    try:
        result = ReversalService.create_reversal(
            db=db,
            organization_id=org_id,
            original_journal_id=invoice.journal_entry_id,
            reversal_date=reversal_date,
            created_by_user_id=user_id,
            reason=f"AP Invoice reversal: {reason}",
            auto_post=True,
        )

        if not result.success:
            return APPostingResult(success=False, message=result.message)

        return APPostingResult(
            success=True,
            journal_entry_id=result.reversal_journal_id,
            message="Invoice posting reversed successfully",
        )

    except HTTPException as e:
        return APPostingResult(success=False, message=f"Reversal failed: {e.detail}")
