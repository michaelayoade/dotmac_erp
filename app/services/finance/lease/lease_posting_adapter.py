"""
LeasePostingAdapter - Converts IFRS 16 lease transactions to GL entries.

Transforms lease initial recognition, interest accrual, payments, and
ROU depreciation into journal entries and posts them to the general ledger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.gl.journal_entry import JournalType
from app.models.finance.lease.lease_asset import LeaseAsset
from app.models.finance.lease.lease_contract import LeaseContract, LeaseStatus
from app.models.finance.lease.lease_liability import LeaseLiability
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.posting.base import BasePostingAdapter, PostingResult

logger = logging.getLogger(__name__)


@dataclass
class LeasePostingResult(PostingResult):
    """Result of a lease posting operation."""


class LeasePostingAdapter:
    """
    Adapter for posting IFRS 16 lease transactions to the general ledger.

    Converts initial recognition, interest accrual, payments, and depreciation
    into journal entries and coordinates posting.
    """

    @staticmethod
    def post_initial_recognition(
        db: Session,
        organization_id: UUID,
        lease_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> LeasePostingResult:
        """
        Post the initial recognition of a lease to the general ledger.

        Creates a journal entry for IFRS 16 initial recognition:
        - Debit: ROU Asset (at initial measurement)
        - Credit: Lease Liability (PV of lease payments)

        Args:
            db: Database session
            organization_id: Organization scope
            lease_id: Lease contract to recognize
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            LeasePostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        ls_id = coerce_uuid(lease_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load contract
        contract = db.get(LeaseContract, ls_id)
        if not contract or contract.organization_id != org_id:
            return LeasePostingResult(success=False, message="Lease contract not found")

        if contract.status != LeaseStatus.ACTIVE:
            return LeasePostingResult(
                success=False,
                message=f"Lease must be ACTIVE for initial recognition (current: {contract.status.value})",
            )

        # Load liability and asset
        liability = (
            db.query(LeaseLiability).filter(LeaseLiability.lease_id == ls_id).first()
        )
        asset = db.query(LeaseAsset).filter(LeaseAsset.lease_id == ls_id).first()

        if not liability or not asset:
            return LeasePostingResult(
                success=False,
                message="Lease liability and asset must exist for initial recognition",
            )

        journal_lines: list[JournalLineInput] = []

        # Debit: ROU Asset
        journal_lines.append(
            JournalLineInput(
                account_id=asset.rou_asset_account_id,
                debit_amount=asset.initial_rou_asset_value,
                credit_amount=Decimal("0"),
                debit_amount_functional=asset.initial_rou_asset_value,
                credit_amount_functional=Decimal("0"),
                description=f"ROU Asset - {contract.lease_name}",
            )
        )

        # Credit: Lease Liability
        journal_lines.append(
            JournalLineInput(
                account_id=liability.lease_liability_account_id,
                debit_amount=Decimal("0"),
                credit_amount=liability.initial_liability_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=liability.initial_liability_amount,
                description=f"Lease Liability - {contract.lease_name}",
            )
        )

        # Handle initial direct costs (included in ROU asset)
        # Initial direct costs are already included in initial_rou_asset_value
        # If they need separate tracking, create an offset entry from cash/prepaid
        if contract.initial_direct_costs and contract.initial_direct_costs > 0:
            # Initial direct costs offset - typically paid in cash
            # This would be recorded separately when the costs were incurred
            # The ROU asset already includes these costs per IFRS 16.24(a)
            pass  # Already included in ROU asset value above

        # Handle restoration obligation (ARO - Asset Retirement Obligation)
        # Per IFRS 16.24(d), restoration costs should increase ROU asset
        # with corresponding provision liability
        if contract.restoration_obligation and contract.restoration_obligation > 0:
            # Additional debit to ROU Asset for restoration costs
            journal_lines.append(
                JournalLineInput(
                    account_id=asset.rou_asset_account_id,
                    debit_amount=contract.restoration_obligation,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=contract.restoration_obligation,
                    credit_amount_functional=Decimal("0"),
                    description=f"Restoration obligation - {contract.lease_name}",
                )
            )
            # Credit to Provision for Restoration
            # Use a restoration provision account if available
            # Otherwise use the lease liability account as a default
            provision_account = getattr(asset, "restoration_provision_account_id", None)
            if not provision_account:
                provision_account = liability.lease_liability_account_id
            journal_lines.append(
                JournalLineInput(
                    account_id=provision_account,
                    debit_amount=Decimal("0"),
                    credit_amount=contract.restoration_obligation,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=contract.restoration_obligation,
                    description=f"Provision for restoration - {contract.lease_name}",
                )
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=contract.commencement_date,
            posting_date=posting_date,
            description=f"IFRS 16 Initial Recognition: {contract.lease_name}",
            reference=f"LEASE-{contract.lease_number}",
            currency_code=contract.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="LEASE",
            source_document_type="INITIAL_RECOGNITION",
            source_document_id=ls_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return LeasePostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id, "LEASE:INIT", ls_id, action="post"
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="LEASE",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Initial recognition posted successfully",
        )
        if not posting_result.success:
            return LeasePostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        return LeasePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_interest_accrual(
        db: Session,
        organization_id: UUID,
        lease_id: UUID,
        accrual_date: date,
        interest_amount: Decimal,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> LeasePostingResult:
        """
        Post interest accrual on lease liability to the general ledger.

        Creates a journal entry for interest expense:
        - Debit: Interest Expense
        - Credit: Lease Liability (increases liability)

        Args:
            db: Database session
            organization_id: Organization scope
            lease_id: Lease contract for accrual
            accrual_date: Date of interest accrual
            interest_amount: Interest amount to accrue
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            LeasePostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        ls_id = coerce_uuid(lease_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load contract
        contract = db.get(LeaseContract, ls_id)
        if not contract or contract.organization_id != org_id:
            return LeasePostingResult(success=False, message="Lease contract not found")

        # Load liability
        liability = (
            db.query(LeaseLiability).filter(LeaseLiability.lease_id == ls_id).first()
        )

        if not liability:
            return LeasePostingResult(
                success=False,
                message="Lease liability not found",
            )

        if interest_amount <= 0:
            return LeasePostingResult(
                success=False,
                message="Interest amount must be positive",
            )

        journal_lines: list[JournalLineInput] = []

        # Debit: Interest Expense
        journal_lines.append(
            JournalLineInput(
                account_id=liability.interest_expense_account_id,
                debit_amount=interest_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=interest_amount,
                credit_amount_functional=Decimal("0"),
                description=f"Interest expense - {contract.lease_name}",
            )
        )

        # Credit: Lease Liability
        journal_lines.append(
            JournalLineInput(
                account_id=liability.lease_liability_account_id,
                debit_amount=Decimal("0"),
                credit_amount=interest_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=interest_amount,
                description=f"Interest accrual - {contract.lease_name}",
            )
        )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=accrual_date,
            posting_date=accrual_date,
            description=f"Lease Interest Accrual: {contract.lease_name}",
            reference=f"LEASE-INT-{contract.lease_number}",
            currency_code=contract.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="LEASE",
            source_document_type="INTEREST_ACCRUAL",
            source_document_id=ls_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return LeasePostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id,
                f"LEASE:INT:{ls_id}",
                ls_id,
                action=accrual_date.isoformat(),
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=accrual_date,
            idempotency_key=idempotency_key,
            source_module="LEASE",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Interest accrual posted successfully",
        )
        if not posting_result.success:
            return LeasePostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        # Update liability balance
        liability.current_liability_balance += interest_amount
        db.commit()
        db.refresh(liability)

        return LeasePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_lease_payment(
        db: Session,
        organization_id: UUID,
        lease_id: UUID,
        payment_date: date,
        payment_amount: Decimal,
        cash_account_id: UUID,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> LeasePostingResult:
        """
        Post a lease payment to the general ledger.

        Creates a journal entry for lease payment:
        - Debit: Lease Liability (reduces liability)
        - Credit: Cash/Bank

        Args:
            db: Database session
            organization_id: Organization scope
            lease_id: Lease contract for payment
            payment_date: Date of payment
            payment_amount: Payment amount
            cash_account_id: Cash/Bank account to credit
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            LeasePostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        ls_id = coerce_uuid(lease_id)
        user_id = coerce_uuid(posted_by_user_id)
        cash_id = coerce_uuid(cash_account_id)

        # Load contract
        contract = db.get(LeaseContract, ls_id)
        if not contract or contract.organization_id != org_id:
            return LeasePostingResult(success=False, message="Lease contract not found")

        # Load liability
        liability = (
            db.query(LeaseLiability).filter(LeaseLiability.lease_id == ls_id).first()
        )

        if not liability:
            return LeasePostingResult(
                success=False,
                message="Lease liability not found",
            )

        if payment_amount <= 0:
            return LeasePostingResult(
                success=False,
                message="Payment amount must be positive",
            )

        journal_lines: list[JournalLineInput] = []

        # Debit: Lease Liability (reduce liability)
        journal_lines.append(
            JournalLineInput(
                account_id=liability.lease_liability_account_id,
                debit_amount=payment_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=payment_amount,
                credit_amount_functional=Decimal("0"),
                description=f"Lease payment - {contract.lease_name}",
            )
        )

        # Credit: Cash/Bank
        journal_lines.append(
            JournalLineInput(
                account_id=cash_id,
                debit_amount=Decimal("0"),
                credit_amount=payment_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=payment_amount,
                description=f"Lease payment - {contract.lease_name}",
            )
        )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=payment_date,
            posting_date=payment_date,
            description=f"Lease Payment: {contract.lease_name}",
            reference=f"LEASE-PAY-{contract.lease_number}",
            currency_code=contract.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="LEASE",
            source_document_type="LEASE_PAYMENT",
            source_document_id=ls_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return LeasePostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id,
                f"LEASE:PAY:{ls_id}",
                ls_id,
                action=payment_date.isoformat(),
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=payment_date,
            idempotency_key=idempotency_key,
            source_module="LEASE",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Lease payment posted successfully",
        )
        if not posting_result.success:
            return LeasePostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        # Update liability balance
        liability.current_liability_balance -= payment_amount
        if liability.current_liability_balance < 0:
            liability.current_liability_balance = Decimal("0")
        db.commit()
        db.refresh(liability)

        return LeasePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_rou_depreciation(
        db: Session,
        organization_id: UUID,
        lease_id: UUID,
        depreciation_date: date,
        depreciation_amount: Decimal,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> LeasePostingResult:
        """
        Post ROU asset depreciation to the general ledger.

        Creates a journal entry for depreciation:
        - Debit: Depreciation Expense
        - Credit: Accumulated Depreciation (ROU Asset)

        Args:
            db: Database session
            organization_id: Organization scope
            lease_id: Lease contract for depreciation
            depreciation_date: Date of depreciation
            depreciation_amount: Depreciation amount
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            LeasePostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        ls_id = coerce_uuid(lease_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load contract
        contract = db.get(LeaseContract, ls_id)
        if not contract or contract.organization_id != org_id:
            return LeasePostingResult(success=False, message="Lease contract not found")

        # Load asset
        asset = db.query(LeaseAsset).filter(LeaseAsset.lease_id == ls_id).first()

        if not asset:
            return LeasePostingResult(
                success=False,
                message="Lease asset not found",
            )

        if depreciation_amount <= 0:
            return LeasePostingResult(
                success=False,
                message="Depreciation amount must be positive",
            )

        journal_lines: list[JournalLineInput] = []

        # Debit: Depreciation Expense
        journal_lines.append(
            JournalLineInput(
                account_id=asset.depreciation_expense_account_id,
                debit_amount=depreciation_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=depreciation_amount,
                credit_amount_functional=Decimal("0"),
                description=f"ROU Depreciation - {contract.lease_name}",
            )
        )

        # Credit: Accumulated Depreciation (ROU Asset)
        journal_lines.append(
            JournalLineInput(
                account_id=asset.accumulated_depreciation_account_id,
                debit_amount=Decimal("0"),
                credit_amount=depreciation_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=depreciation_amount,
                description=f"Accum Dep ROU - {contract.lease_name}",
            )
        )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=depreciation_date,
            posting_date=depreciation_date,
            description=f"ROU Asset Depreciation: {contract.lease_name}",
            reference=f"LEASE-DEP-{contract.lease_number}",
            currency_code=contract.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="LEASE",
            source_document_type="ROU_DEPRECIATION",
            source_document_id=ls_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return LeasePostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id,
                f"LEASE:DEP:{ls_id}",
                ls_id,
                action=depreciation_date.isoformat(),
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=depreciation_date,
            idempotency_key=idempotency_key,
            source_module="LEASE",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="ROU depreciation posted successfully",
        )
        if not posting_result.success:
            return LeasePostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        # Update asset carrying amount
        asset.accumulated_depreciation += depreciation_amount
        asset.carrying_amount = (
            asset.initial_rou_asset_value - asset.accumulated_depreciation
        )
        if asset.carrying_amount < 0:
            asset.carrying_amount = Decimal("0")
        db.commit()
        db.refresh(asset)

        return LeasePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_lease_termination(
        db: Session,
        organization_id: UUID,
        lease_id: UUID,
        termination_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> LeasePostingResult:
        """
        Post early lease termination to the general ledger.

        Removes remaining ROU asset and liability, recognizing any gain/loss:
        - Debit: Lease Liability (remaining balance)
        - Debit: Accumulated Depreciation (ROU)
        - Credit: ROU Asset
        - Debit/Credit: Gain/Loss on termination

        Args:
            db: Database session
            organization_id: Organization scope
            lease_id: Lease contract to terminate
            termination_date: Date of termination
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            LeasePostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        ls_id = coerce_uuid(lease_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load contract
        contract = db.get(LeaseContract, ls_id)
        if not contract or contract.organization_id != org_id:
            return LeasePostingResult(success=False, message="Lease contract not found")

        if contract.status != LeaseStatus.TERMINATED:
            return LeasePostingResult(
                success=False,
                message="Lease must be TERMINATED status for termination posting",
            )

        # Load liability and asset
        liability = (
            db.query(LeaseLiability).filter(LeaseLiability.lease_id == ls_id).first()
        )
        asset = db.query(LeaseAsset).filter(LeaseAsset.lease_id == ls_id).first()

        if not liability or not asset:
            return LeasePostingResult(
                success=False,
                message="Lease liability and asset must exist for termination",
            )

        remaining_liability = liability.current_liability_balance
        rou_carrying_value = asset.carrying_amount
        accumulated_depreciation = asset.accumulated_depreciation

        # Calculate gain/loss
        # If liability > ROU carrying value, there's a gain
        # If liability < ROU carrying value, there's a loss
        gain_loss = remaining_liability - rou_carrying_value

        journal_lines: list[JournalLineInput] = []

        # Debit: Lease Liability (clear it)
        if remaining_liability > 0:
            journal_lines.append(
                JournalLineInput(
                    account_id=liability.lease_liability_account_id,
                    debit_amount=remaining_liability,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=remaining_liability,
                    credit_amount_functional=Decimal("0"),
                    description=f"Clear lease liability - {contract.lease_name}",
                )
            )

        # Debit: Accumulated Depreciation (clear it)
        if accumulated_depreciation > 0:
            journal_lines.append(
                JournalLineInput(
                    account_id=asset.accumulated_depreciation_account_id,
                    debit_amount=accumulated_depreciation,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=accumulated_depreciation,
                    credit_amount_functional=Decimal("0"),
                    description=f"Clear accum dep - {contract.lease_name}",
                )
            )

        # Credit: ROU Asset (remove it at original cost)
        journal_lines.append(
            JournalLineInput(
                account_id=asset.rou_asset_account_id,
                debit_amount=Decimal("0"),
                credit_amount=asset.initial_rou_asset_value,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=asset.initial_rou_asset_value,
                description=f"Remove ROU asset - {contract.lease_name}",
            )
        )

        # Gain or Loss on termination
        if gain_loss > 0:
            # Gain - credit to interest expense (or separate gain account)
            journal_lines.append(
                JournalLineInput(
                    account_id=liability.interest_expense_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=gain_loss,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=gain_loss,
                    description=f"Gain on lease termination - {contract.lease_name}",
                )
            )
        elif gain_loss < 0:
            # Loss - debit to interest expense (or separate loss account)
            journal_lines.append(
                JournalLineInput(
                    account_id=liability.interest_expense_account_id,
                    debit_amount=abs(gain_loss),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(gain_loss),
                    credit_amount_functional=Decimal("0"),
                    description=f"Loss on lease termination - {contract.lease_name}",
                )
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=termination_date,
            posting_date=termination_date,
            description=f"Lease Termination: {contract.lease_name}",
            reference=f"LEASE-TERM-{contract.lease_number}",
            currency_code=contract.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="LEASE",
            source_document_type="LEASE_TERMINATION",
            source_document_id=ls_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return LeasePostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id, "LEASE:TERM", ls_id, action="post"
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=termination_date,
            idempotency_key=idempotency_key,
            source_module="LEASE",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Lease termination posted successfully",
        )
        if not posting_result.success:
            return LeasePostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        # Zero out liability and asset balances
        liability.current_liability_balance = Decimal("0")
        asset.carrying_amount = Decimal("0")
        db.commit()

        return LeasePostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )


# Module-level singleton instance
lease_posting_adapter = LeasePostingAdapter()
