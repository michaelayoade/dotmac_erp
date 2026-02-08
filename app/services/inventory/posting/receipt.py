"""
INV Receipt Posting - Post inventory receipts to GL.

Transforms inventory receipts into journal entries with:
- Debit: Inventory account
- Credit: AP Control or GRNI account
"""

from datetime import date
from decimal import Decimal
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


def post_receipt(
    db: Session,
    organization_id: UUID,
    transaction_id: UUID,
    posting_date: date,
    posted_by_user_id: UUID,
    ap_control_account_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> INVPostingResult:
    """
    Post an inventory receipt to the general ledger.

    Creates journal entry:
    - Debit: Inventory account
    - Credit: AP Control or Goods Received Not Invoiced

    Args:
        db: Database session
        organization_id: Organization scope
        transaction_id: Transaction to post
        posting_date: Date for the GL posting
        posted_by_user_id: User posting
        ap_control_account_id: AP control account for the credit side
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
        TransactionType.RECEIPT,
        TransactionType.RETURN,
        TransactionType.ASSEMBLY,
    ]:
        return INVPostingResult(
            success=False,
            message="Transaction is not a receipt",
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
    if not inventory_account:
        return INVPostingResult(
            success=False, message="Inventory account not configured"
        )

    journal_lines = [
        # Debit: Inventory
        JournalLineInput(
            account_id=inventory_account,
            debit_amount=transaction.total_cost,
            credit_amount=Decimal("0"),
            debit_amount_functional=transaction.total_cost,
            credit_amount_functional=Decimal("0"),
            description=f"Inventory receipt: {item.item_code}",
        ),
    ]

    # Credit: AP Control or GRNI
    if ap_control_account_id:
        journal_lines.append(
            JournalLineInput(
                account_id=ap_control_account_id,
                debit_amount=Decimal("0"),
                credit_amount=transaction.total_cost,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=transaction.total_cost,
                description=f"AP for inventory: {item.item_code}",
            )
        )
    else:
        # Use inventory adjustment account as GRNI placeholder
        if not category.inventory_adjustment_account_id:
            return INVPostingResult(
                success=False, message="Adjustment account not configured"
            )
        journal_lines.append(
            JournalLineInput(
                account_id=category.inventory_adjustment_account_id,
                debit_amount=Decimal("0"),
                credit_amount=transaction.total_cost,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=transaction.total_cost,
                description=f"GRNI: {item.item_code}",
            )
        )

    # Add variance entry for standard costing
    if (
        transaction.cost_variance != Decimal("0")
        and category.purchase_variance_account_id
    ):
        if transaction.cost_variance > 0:
            # Unfavorable variance - debit
            journal_lines.append(
                JournalLineInput(
                    account_id=category.purchase_variance_account_id,
                    debit_amount=transaction.cost_variance,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=transaction.cost_variance,
                    credit_amount_functional=Decimal("0"),
                    description=f"Purchase price variance: {item.item_code}",
                )
            )
        else:
            # Favorable variance - credit
            journal_lines.append(
                JournalLineInput(
                    account_id=category.purchase_variance_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=abs(transaction.cost_variance),
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=abs(transaction.cost_variance),
                    description=f"Purchase price variance: {item.item_code}",
                )
            )

    # Create journal entry
    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=transaction.transaction_date.date(),
        posting_date=posting_date,
        description=f"Inventory Receipt: {item.item_code}",
        reference=transaction.reference or f"INV-RCV-{txn_id}",
        currency_code=transaction.currency_code,
        exchange_rate=Decimal("1.0"),
        lines=journal_lines,
        source_module="INV",
        source_document_type="RECEIPT",
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
            org_id, "INV:RCV", txn_id, action="post"
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
        success_message="Receipt posted successfully",
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
