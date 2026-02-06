"""
INV Adjustment Posting - Post inventory adjustments to GL.

Transforms inventory adjustments into journal entries with:
- Positive adjustment: Debit Inventory, Credit Adjustment account
- Negative adjustment: Debit Adjustment account, Credit Inventory
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.gl.journal_entry import JournalType
from app.models.inventory.inventory_transaction import (
    InventoryTransaction,
    TransactionType,
)
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.posting.base import BasePostingAdapter
from app.services.inventory.posting.helpers import (
    get_inventory_account,
    get_item_accounts,
)
from app.services.inventory.posting.result import INVPostingResult


def post_adjustment(
    db: Session,
    organization_id: UUID,
    transaction_id: UUID,
    posting_date: date,
    posted_by_user_id: UUID,
    idempotency_key: Optional[str] = None,
) -> INVPostingResult:
    """
    Post an inventory adjustment to the general ledger.

    Creates journal entry:
    - Positive adjustment: Debit Inventory, Credit Adjustment account
    - Negative adjustment: Debit Adjustment account, Credit Inventory

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
    user_id = coerce_uuid(posted_by_user_id)

    transaction = db.get(InventoryTransaction, txn_id)
    if not transaction or transaction.organization_id != org_id:
        return INVPostingResult(success=False, message="Transaction not found")

    if transaction.transaction_type not in [
        TransactionType.ADJUSTMENT,
        TransactionType.COUNT_ADJUSTMENT,
        TransactionType.SCRAP,
    ]:
        return INVPostingResult(
            success=False,
            message="Transaction is not an adjustment",
        )

    if transaction.journal_entry_id:
        return INVPostingResult(
            success=False,
            message="Transaction already posted",
        )

    # Get item and category for accounts
    item, category = get_item_accounts(db, transaction)
    if not item:
        return INVPostingResult(success=False, message="Item not found")
    if not category:
        return INVPostingResult(success=False, message="Item category not found")

    inventory_account = get_inventory_account(item, category)
    adjustment_account = category.inventory_adjustment_account_id
    if not inventory_account:
        return INVPostingResult(
            success=False, message="Inventory account not configured"
        )
    if not adjustment_account:
        return INVPostingResult(
            success=False, message="Adjustment account not configured"
        )

    journal_lines = []

    if transaction.quantity > 0:
        # Positive adjustment: increase inventory
        journal_lines = [
            JournalLineInput(
                account_id=inventory_account,
                debit_amount=transaction.total_cost,
                credit_amount=Decimal("0"),
                debit_amount_functional=transaction.total_cost,
                credit_amount_functional=Decimal("0"),
                description=f"Inventory adjustment increase: {item.item_code}",
            ),
            JournalLineInput(
                account_id=adjustment_account,
                debit_amount=Decimal("0"),
                credit_amount=transaction.total_cost,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=transaction.total_cost,
                description=f"Inventory adjustment: {item.item_code}",
            ),
        ]
    else:
        # Negative adjustment: decrease inventory
        journal_lines = [
            JournalLineInput(
                account_id=adjustment_account,
                debit_amount=transaction.total_cost,
                credit_amount=Decimal("0"),
                debit_amount_functional=transaction.total_cost,
                credit_amount_functional=Decimal("0"),
                description=f"Inventory adjustment: {item.item_code}",
            ),
            JournalLineInput(
                account_id=inventory_account,
                debit_amount=Decimal("0"),
                credit_amount=transaction.total_cost,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=transaction.total_cost,
                description=f"Inventory adjustment decrease: {item.item_code}",
            ),
        ]

    # Create journal entry
    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=transaction.transaction_date.date(),
        posting_date=posting_date,
        description=f"Inventory Adjustment: {item.item_code}",
        reference=transaction.reference or f"INV-ADJ-{txn_id}",
        currency_code=transaction.currency_code,
        exchange_rate=Decimal("1.0"),
        lines=journal_lines,
        source_module="INV",
        source_document_type="ADJUSTMENT",
        source_document_id=txn_id,
    )

    journal, error = BasePostingAdapter.create_and_approve_journal(
        db,
        org_id,
        journal_input,
        user_id,
        error_prefix="Journal creation failed",
    )
    if error:
        return INVPostingResult(success=False, message=error.message)

    # Post to ledger
    if not idempotency_key:
        idempotency_key = BasePostingAdapter.make_idempotency_key(
            org_id, "INV:ADJ", txn_id, action="post"
        )

    posting_result = BasePostingAdapter.post_to_ledger(
        db,
        organization_id=org_id,
        journal_entry_id=journal.journal_entry_id,
        posting_date=posting_date,
        idempotency_key=idempotency_key,
        source_module="INV",
        correlation_id=None,
        posted_by_user_id=user_id,
        success_message="Adjustment posted successfully",
    )
    if not posting_result.success:
        return INVPostingResult(
            success=False,
            journal_entry_id=journal.journal_entry_id,
            message=posting_result.message,
        )

    # Update transaction with journal reference
    transaction.journal_entry_id = journal.journal_entry_id

    return INVPostingResult(
        success=True,
        journal_entry_id=journal.journal_entry_id,
        posting_batch_id=posting_result.posting_batch_id,
        message=posting_result.message,
    )
