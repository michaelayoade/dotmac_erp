"""
INV Transaction Router - Routes transactions to appropriate posting handler.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.inv.inventory_transaction import InventoryTransaction, TransactionType
from app.services.common import coerce_uuid

from app.services.finance.inv.posting.result import INVPostingResult
from app.services.finance.inv.posting.receipt import post_receipt
from app.services.finance.inv.posting.issue import post_issue
from app.services.finance.inv.posting.adjustment import post_adjustment


def post_transaction(
    db: Session,
    organization_id: UUID,
    transaction_id: UUID,
    posting_date: date,
    posted_by_user_id: UUID,
    idempotency_key: Optional[str] = None,
) -> INVPostingResult:
    """
    Post any inventory transaction to the general ledger.

    Routes to the appropriate posting method based on transaction type.

    Args:
        db: Database session
        organization_id: Organization scope
        transaction_id: Transaction to post
        posting_date: Date for the GL posting
        posted_by_user_id: User posting
        idempotency_key: Optional idempotency key

    Returns:
        INVPostingResult with outcome
    """
    org_id = coerce_uuid(organization_id)
    txn_id = coerce_uuid(transaction_id)

    transaction = db.get(InventoryTransaction, txn_id)
    if not transaction or transaction.organization_id != org_id:
        return INVPostingResult(success=False, message="Transaction not found")

    if transaction.transaction_type in [
        TransactionType.RECEIPT,
        TransactionType.RETURN,
        TransactionType.ASSEMBLY,
    ]:
        return post_receipt(
            db, organization_id, transaction_id, posting_date,
            posted_by_user_id, idempotency_key=idempotency_key
        )
    elif transaction.transaction_type in [
        TransactionType.ISSUE,
        TransactionType.SALE,
        TransactionType.DISASSEMBLY,
    ]:
        return post_issue(
            db, organization_id, transaction_id, posting_date,
            posted_by_user_id, idempotency_key=idempotency_key
        )
    elif transaction.transaction_type in [
        TransactionType.ADJUSTMENT,
        TransactionType.COUNT_ADJUSTMENT,
        TransactionType.SCRAP,
    ]:
        return post_adjustment(
            db, organization_id, transaction_id, posting_date,
            posted_by_user_id, idempotency_key=idempotency_key
        )
    else:
        return INVPostingResult(
            success=False,
            message=f"Posting not supported for transaction type: {transaction.transaction_type}",
        )
