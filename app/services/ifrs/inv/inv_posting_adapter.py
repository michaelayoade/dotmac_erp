"""
INVPostingAdapter - Converts inventory transactions to GL entries.

Posts inventory movements (receipts, issues, adjustments) to the general ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.inv.item import Item
from app.models.ifrs.inv.item_category import ItemCategory
from app.models.ifrs.inv.inventory_transaction import InventoryTransaction, TransactionType
from app.services.common import coerce_uuid
from app.services.ifrs.gl.journal import JournalService, JournalInput, JournalLineInput
from app.services.ifrs.gl.ledger_posting import LedgerPostingService, PostingRequest
from app.models.ifrs.gl.journal_entry import JournalType


@dataclass
class INVPostingResult:
    """Result of an inventory posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""


class INVPostingAdapter:
    """
    Adapter for posting inventory transactions to the general ledger.

    Converts receipts, issues, and adjustments into journal entries.
    """

    @staticmethod
    def post_receipt(
        db: Session,
        organization_id: UUID,
        transaction_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        ap_control_account_id: Optional[UUID] = None,
        idempotency_key: Optional[str] = None,
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
        item = db.get(Item, transaction.item_id)
        if not item:
            return INVPostingResult(success=False, message="Item not found")

        category = db.get(ItemCategory, item.category_id)
        if not category:
            return INVPostingResult(success=False, message="Item category not found")

        inventory_account = item.inventory_account_id or category.inventory_account_id

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
        if transaction.cost_variance != Decimal("0") and category.purchase_variance_account_id:
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

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return INVPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:INV:RCV:{txn_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="INV",
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return INVPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            # Update transaction with journal reference
            transaction.journal_entry_id = journal.journal_entry_id

            return INVPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Receipt posted successfully",
            )

        except Exception as e:
            return INVPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )

    @staticmethod
    def post_issue(
        db: Session,
        organization_id: UUID,
        transaction_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        expense_account_id: Optional[UUID] = None,
        idempotency_key: Optional[str] = None,
    ) -> INVPostingResult:
        """
        Post an inventory issue to the general ledger.

        Creates journal entry:
        - Debit: COGS or Expense account
        - Credit: Inventory account

        Args:
            db: Database session
            organization_id: Organization scope
            transaction_id: Transaction to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            expense_account_id: Override expense account (defaults to COGS)
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
            TransactionType.ISSUE,
            TransactionType.SALE,
            TransactionType.DISASSEMBLY,
        ]:
            return INVPostingResult(
                success=False,
                message="Transaction is not an issue or sale",
            )

        if transaction.journal_entry_id:
            return INVPostingResult(
                success=False,
                message="Transaction already posted",
            )

        # Get item and category for accounts
        item = db.get(Item, transaction.item_id)
        if not item:
            return INVPostingResult(success=False, message="Item not found")

        category = db.get(ItemCategory, item.category_id)
        if not category:
            return INVPostingResult(success=False, message="Item category not found")

        inventory_account = item.inventory_account_id or category.inventory_account_id
        cogs_account = expense_account_id or item.cogs_account_id or category.cogs_account_id

        journal_lines = [
            # Debit: COGS/Expense
            JournalLineInput(
                account_id=cogs_account,
                debit_amount=transaction.total_cost,
                credit_amount=Decimal("0"),
                debit_amount_functional=transaction.total_cost,
                credit_amount_functional=Decimal("0"),
                description=f"COGS: {item.item_code}",
            ),
            # Credit: Inventory
            JournalLineInput(
                account_id=inventory_account,
                debit_amount=Decimal("0"),
                credit_amount=transaction.total_cost,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=transaction.total_cost,
                description=f"Inventory issue: {item.item_code}",
            ),
        ]

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=transaction.transaction_date.date(),
            posting_date=posting_date,
            description=f"Inventory Issue: {item.item_code}",
            reference=transaction.reference or f"INV-ISS-{txn_id}",
            currency_code=transaction.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="INV",
            source_document_type="ISSUE",
            source_document_id=txn_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return INVPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:INV:ISS:{txn_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="INV",
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return INVPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            # Update transaction with journal reference
            transaction.journal_entry_id = journal.journal_entry_id

            return INVPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Issue posted successfully",
            )

        except Exception as e:
            return INVPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )

    @staticmethod
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
        item = db.get(Item, transaction.item_id)
        if not item:
            return INVPostingResult(success=False, message="Item not found")

        category = db.get(ItemCategory, item.category_id)
        if not category:
            return INVPostingResult(success=False, message="Item category not found")

        inventory_account = item.inventory_account_id or category.inventory_account_id
        adjustment_account = category.inventory_adjustment_account_id

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

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return INVPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:INV:ADJ:{txn_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="INV",
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return INVPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            # Update transaction with journal reference
            transaction.journal_entry_id = journal.journal_entry_id

            return INVPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Adjustment posted successfully",
            )

        except Exception as e:
            return INVPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )


    @staticmethod
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
            return INVPostingAdapter.post_receipt(
                db, organization_id, transaction_id, posting_date,
                posted_by_user_id, idempotency_key=idempotency_key
            )
        elif transaction.transaction_type in [
            TransactionType.ISSUE,
            TransactionType.SALE,
            TransactionType.DISASSEMBLY,
        ]:
            return INVPostingAdapter.post_issue(
                db, organization_id, transaction_id, posting_date,
                posted_by_user_id, idempotency_key=idempotency_key
            )
        elif transaction.transaction_type in [
            TransactionType.ADJUSTMENT,
            TransactionType.COUNT_ADJUSTMENT,
            TransactionType.SCRAP,
        ]:
            return INVPostingAdapter.post_adjustment(
                db, organization_id, transaction_id, posting_date,
                posted_by_user_id, idempotency_key=idempotency_key
            )
        else:
            return INVPostingResult(
                success=False,
                message=f"Posting not supported for transaction type: {transaction.transaction_type}",
            )


# Module-level singleton instance
inv_posting_adapter = INVPostingAdapter()
