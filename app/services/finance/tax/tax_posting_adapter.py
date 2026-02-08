"""
TAXPostingAdapter - Converts tax transactions to GL entries.

Transforms tax transactions, deferred tax movements, and current tax
provisions into journal entries and posts them to the general ledger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.gl.journal_entry import JournalType
from app.models.finance.tax.deferred_tax_basis import DeferredTaxBasis
from app.models.finance.tax.deferred_tax_movement import DeferredTaxMovement
from app.models.finance.tax.tax_code import TaxCode
from app.models.finance.tax.tax_jurisdiction import TaxJurisdiction
from app.models.finance.tax.tax_transaction import TaxTransaction, TaxTransactionType
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.posting.base import BasePostingAdapter, PostingResult

logger = logging.getLogger(__name__)


@dataclass
class TAXPostingResult(PostingResult):
    """Result of a tax posting operation."""


class TAXPostingAdapter:
    """
    Adapter for posting tax transactions to the general ledger.

    Converts VAT/GST transactions, current tax provisions, and deferred
    tax movements into journal entries.
    """

    @staticmethod
    def post_tax_transaction(
        db: Session,
        organization_id: UUID,
        transaction_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
    ) -> TAXPostingResult:
        """
        Post a tax transaction to the general ledger.

        VAT/GST posting:
        - Input tax: Dr Tax Paid (asset), Cr Source
        - Output tax: Dr Source, Cr Tax Collected (liability)
        - Withholding: Dr Expense, Cr Withholding Payable

        Args:
            db: Database session
            organization_id: Organization scope
            transaction_id: Tax transaction to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            TAXPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        txn_id = coerce_uuid(transaction_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load transaction
        transaction = db.get(TaxTransaction, txn_id)
        if not transaction or transaction.organization_id != org_id:
            return TAXPostingResult(success=False, message="Tax transaction not found")

        # Load tax code for accounts
        tax_code = db.get(TaxCode, transaction.tax_code_id)
        if not tax_code:
            return TAXPostingResult(success=False, message="Tax code not found")

        journal_lines: list[JournalLineInput] = []

        if transaction.transaction_type == TaxTransactionType.INPUT:
            # Input tax (purchases)
            if tax_code.tax_paid_account_id:
                # Recoverable portion to asset
                if transaction.recoverable_amount > 0:
                    journal_lines.append(
                        JournalLineInput(
                            account_id=tax_code.tax_paid_account_id,
                            debit_amount=transaction.recoverable_amount,
                            credit_amount=Decimal("0"),
                            debit_amount_functional=transaction.recoverable_amount,
                            credit_amount_functional=Decimal("0"),
                            description=f"Input tax recoverable - {tax_code.tax_name}",
                        )
                    )

                # Non-recoverable portion to expense
                if (
                    transaction.non_recoverable_amount > 0
                    and tax_code.tax_expense_account_id
                ):
                    journal_lines.append(
                        JournalLineInput(
                            account_id=tax_code.tax_expense_account_id,
                            debit_amount=transaction.non_recoverable_amount,
                            credit_amount=Decimal("0"),
                            debit_amount_functional=transaction.non_recoverable_amount,
                            credit_amount_functional=Decimal("0"),
                            description=f"Input tax non-recoverable - {tax_code.tax_name}",
                        )
                    )

        elif transaction.transaction_type == TaxTransactionType.OUTPUT:
            # Output tax (sales)
            if tax_code.tax_collected_account_id:
                journal_lines.append(
                    JournalLineInput(
                        account_id=tax_code.tax_collected_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=transaction.functional_tax_amount,
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=transaction.functional_tax_amount,
                        description=f"Output tax - {tax_code.tax_name}",
                    )
                )

        elif transaction.transaction_type == TaxTransactionType.WITHHOLDING:
            # Withholding tax
            if tax_code.tax_collected_account_id:
                journal_lines.append(
                    JournalLineInput(
                        account_id=tax_code.tax_collected_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=transaction.functional_tax_amount,
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=transaction.functional_tax_amount,
                        description=f"Withholding tax payable - {tax_code.tax_name}",
                    )
                )

        if not journal_lines:
            return TAXPostingResult(
                success=False,
                message="No journal entries to create - missing tax accounts",
            )

        # Note: The offsetting entry would come from the source document (AP/AR invoice)
        # This adapter posts just the tax portion

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=transaction.transaction_date,
            posting_date=posting_date,
            description=f"Tax: {tax_code.tax_name} - {transaction.transaction_type.value}",
            reference=transaction.source_document_reference or f"TAX-{txn_id}",
            currency_code=transaction.currency_code,
            exchange_rate=transaction.exchange_rate or Decimal("1.0"),
            lines=journal_lines,
            source_module="TAX",
            source_document_type="TAX_TRANSACTION",
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
            return TAXPostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id, "TAX:TXN", txn_id, action="post"
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="TAX",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Tax transaction posted successfully",
        )
        if not posting_result.success:
            return TAXPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        # Update transaction with journal reference
        transaction.journal_entry_id = journal.journal_entry_id
        db.commit()

        return TAXPostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_current_tax_provision(
        db: Session,
        organization_id: UUID,
        jurisdiction_id: UUID,
        fiscal_period_id: UUID,
        current_tax_expense: Decimal,
        posting_date: date,
        posted_by_user_id: UUID,
        reference: str | None = None,
        idempotency_key: str | None = None,
    ) -> TAXPostingResult:
        """
        Post current income tax provision to the general ledger.

        Dr Current Tax Expense
        Cr Current Tax Payable

        Args:
            db: Database session
            organization_id: Organization scope
            jurisdiction_id: Tax jurisdiction
            fiscal_period_id: Fiscal period
            current_tax_expense: Tax expense amount
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            reference: Optional reference
            idempotency_key: Optional idempotency key

        Returns:
            TAXPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        jur_id = coerce_uuid(jurisdiction_id)
        period_id = coerce_uuid(fiscal_period_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load jurisdiction for accounts
        jurisdiction = db.get(TaxJurisdiction, jur_id)
        if not jurisdiction or jurisdiction.organization_id != org_id:
            return TAXPostingResult(success=False, message="Jurisdiction not found")

        if current_tax_expense == 0:
            return TAXPostingResult(
                success=True,
                message="No current tax to post",
            )

        journal_lines: list[JournalLineInput] = []

        if current_tax_expense > 0:
            # Tax expense
            journal_lines.append(
                JournalLineInput(
                    account_id=jurisdiction.current_tax_expense_account_id,
                    debit_amount=current_tax_expense,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=current_tax_expense,
                    credit_amount_functional=Decimal("0"),
                    description=f"Current income tax expense - {jurisdiction.jurisdiction_name}",
                )
            )
            journal_lines.append(
                JournalLineInput(
                    account_id=jurisdiction.current_tax_payable_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=current_tax_expense,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=current_tax_expense,
                    description=f"Current income tax payable - {jurisdiction.jurisdiction_name}",
                )
            )
        else:
            # Tax benefit (refund receivable)
            journal_lines.append(
                JournalLineInput(
                    account_id=jurisdiction.current_tax_payable_account_id,
                    debit_amount=abs(current_tax_expense),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(current_tax_expense),
                    credit_amount_functional=Decimal("0"),
                    description=f"Current income tax receivable - {jurisdiction.jurisdiction_name}",
                )
            )
            journal_lines.append(
                JournalLineInput(
                    account_id=jurisdiction.current_tax_expense_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=abs(current_tax_expense),
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=abs(current_tax_expense),
                    description=f"Current income tax benefit - {jurisdiction.jurisdiction_name}",
                )
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"Current Tax Provision: {jurisdiction.jurisdiction_name}",
            reference=reference or f"CTAX-{jurisdiction.jurisdiction_code}",
            currency_code=jurisdiction.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="TAX",
            source_document_type="CURRENT_TAX_PROVISION",
            source_document_id=period_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return TAXPostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id, f"TAX:CTAX:{jur_id}", period_id, action="post"
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="TAX",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Current tax provision posted successfully",
        )
        if not posting_result.success:
            return TAXPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        return TAXPostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_deferred_tax_movement(
        db: Session,
        organization_id: UUID,
        movement_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
    ) -> TAXPostingResult:
        """
        Post deferred tax movement to the general ledger.

        DTA increase: Dr Deferred Tax Asset, Cr Deferred Tax Expense (or OCI/Equity)
        DTL increase: Dr Deferred Tax Expense (or OCI/Equity), Cr Deferred Tax Liability

        Args:
            db: Database session
            organization_id: Organization scope
            movement_id: Deferred tax movement to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            TAXPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        mov_id = coerce_uuid(movement_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load movement
        movement = db.get(DeferredTaxMovement, mov_id)
        if not movement:
            return TAXPostingResult(
                success=False, message="Deferred tax movement not found"
            )

        # Load basis and jurisdiction
        basis = db.get(DeferredTaxBasis, movement.basis_id)
        if not basis or basis.organization_id != org_id:
            return TAXPostingResult(
                success=False, message="Deferred tax basis not found"
            )

        jurisdiction = db.get(TaxJurisdiction, basis.jurisdiction_id)
        if not jurisdiction:
            return TAXPostingResult(success=False, message="Jurisdiction not found")

        # Check if there's anything to post
        total_movement = (
            movement.deferred_tax_movement_pl
            + movement.deferred_tax_movement_oci
            + movement.deferred_tax_movement_equity
        )
        if total_movement == 0:
            return TAXPostingResult(
                success=True,
                message="No deferred tax movement to post",
            )

        journal_lines: list[JournalLineInput] = []

        # Determine accounts based on whether this is DTA or DTL
        if basis.is_asset:
            balance_account_id = jurisdiction.deferred_tax_asset_account_id
        else:
            balance_account_id = jurisdiction.deferred_tax_liability_account_id

        expense_account_id = jurisdiction.deferred_tax_expense_account_id

        # P&L movement
        if movement.deferred_tax_movement_pl != 0:
            pl_mov = movement.deferred_tax_movement_pl
            if basis.is_asset:
                # DTA: positive movement = increase DTA, credit expense (benefit)
                if pl_mov > 0:
                    journal_lines.append(
                        JournalLineInput(
                            account_id=balance_account_id,
                            debit_amount=abs(pl_mov),
                            credit_amount=Decimal("0"),
                            debit_amount_functional=abs(pl_mov),
                            credit_amount_functional=Decimal("0"),
                            description=f"Deferred tax asset increase - {basis.basis_name}",
                        )
                    )
                    journal_lines.append(
                        JournalLineInput(
                            account_id=expense_account_id,
                            debit_amount=Decimal("0"),
                            credit_amount=abs(pl_mov),
                            debit_amount_functional=Decimal("0"),
                            credit_amount_functional=abs(pl_mov),
                            description=f"Deferred tax benefit - {basis.basis_name}",
                        )
                    )
                else:
                    journal_lines.append(
                        JournalLineInput(
                            account_id=expense_account_id,
                            debit_amount=abs(pl_mov),
                            credit_amount=Decimal("0"),
                            debit_amount_functional=abs(pl_mov),
                            credit_amount_functional=Decimal("0"),
                            description=f"Deferred tax expense - {basis.basis_name}",
                        )
                    )
                    journal_lines.append(
                        JournalLineInput(
                            account_id=balance_account_id,
                            debit_amount=Decimal("0"),
                            credit_amount=abs(pl_mov),
                            debit_amount_functional=Decimal("0"),
                            credit_amount_functional=abs(pl_mov),
                            description=f"Deferred tax asset decrease - {basis.basis_name}",
                        )
                    )
            else:
                # DTL: positive movement = increase DTL, debit expense
                if pl_mov > 0:
                    journal_lines.append(
                        JournalLineInput(
                            account_id=expense_account_id,
                            debit_amount=abs(pl_mov),
                            credit_amount=Decimal("0"),
                            debit_amount_functional=abs(pl_mov),
                            credit_amount_functional=Decimal("0"),
                            description=f"Deferred tax expense - {basis.basis_name}",
                        )
                    )
                    journal_lines.append(
                        JournalLineInput(
                            account_id=balance_account_id,
                            debit_amount=Decimal("0"),
                            credit_amount=abs(pl_mov),
                            debit_amount_functional=Decimal("0"),
                            credit_amount_functional=abs(pl_mov),
                            description=f"Deferred tax liability increase - {basis.basis_name}",
                        )
                    )
                else:
                    journal_lines.append(
                        JournalLineInput(
                            account_id=balance_account_id,
                            debit_amount=abs(pl_mov),
                            credit_amount=Decimal("0"),
                            debit_amount_functional=abs(pl_mov),
                            credit_amount_functional=Decimal("0"),
                            description=f"Deferred tax liability decrease - {basis.basis_name}",
                        )
                    )
                    journal_lines.append(
                        JournalLineInput(
                            account_id=expense_account_id,
                            debit_amount=Decimal("0"),
                            credit_amount=abs(pl_mov),
                            debit_amount_functional=Decimal("0"),
                            credit_amount_functional=abs(pl_mov),
                            description=f"Deferred tax benefit - {basis.basis_name}",
                        )
                    )

        # OCI movement would use OCI account instead of expense
        # Simplified - would need OCI-specific accounts

        if not journal_lines:
            return TAXPostingResult(
                success=True,
                message="No journal entries required",
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"Deferred Tax Movement: {basis.basis_name}",
            reference=f"DT-{basis.basis_code}",
            currency_code=jurisdiction.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="TAX",
            source_document_type="DEFERRED_TAX_MOVEMENT",
            source_document_id=mov_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return TAXPostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id, "TAX:DT", mov_id, action="post"
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="TAX",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Deferred tax movement posted successfully",
        )
        if not posting_result.success:
            return TAXPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        # Update movement with journal reference
        movement.journal_entry_id = journal.journal_entry_id
        db.commit()

        return TAXPostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )


# Module-level singleton instance
tax_posting_adapter = TAXPostingAdapter()
