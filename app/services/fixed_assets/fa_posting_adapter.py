"""
FAPostingAdapter - Converts FA documents to GL entries.

Transforms depreciation runs, disposals, and revaluations into journal entries
and posts them to the general ledger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.gl.journal_entry import JournalType
from app.models.fixed_assets.asset import Asset
from app.models.fixed_assets.asset_category import AssetCategory
from app.models.fixed_assets.asset_disposal import AssetDisposal
from app.models.fixed_assets.asset_revaluation import AssetRevaluation
from app.models.fixed_assets.depreciation_run import (
    DepreciationRun,
    DepreciationRunStatus,
)
from app.models.fixed_assets.depreciation_schedule import DepreciationSchedule
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
)
from app.services.finance.platform.org_context import org_context_service
from app.services.finance.posting.base import BasePostingAdapter, PostingResult

logger = logging.getLogger(__name__)


@dataclass
class FAPostingResult(PostingResult):
    """Result of an FA posting operation."""


class FAPostingAdapter:
    """
    Adapter for posting FA documents to the general ledger.

    Converts depreciation runs, disposals, and revaluations into
    journal entries and coordinates posting.
    """

    @staticmethod
    def post_asset_acquisition(
        db: Session,
        organization_id: UUID,
        asset_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        credit_account_id: UUID,
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> FAPostingResult:
        """
        Post an asset acquisition to the general ledger.

        Creates a journal entry:
        - Debit: Fixed asset account (from asset category)
        - Credit: Provided clearing/payable/cash account
        """
        org_id = coerce_uuid(organization_id)
        ast_id = coerce_uuid(asset_id)
        user_id = coerce_uuid(posted_by_user_id)
        credit_id = coerce_uuid(credit_account_id)

        asset = db.get(Asset, ast_id)
        if not asset or asset.organization_id != org_id:
            return FAPostingResult(success=False, message="Asset not found")

        category = db.get(AssetCategory, asset.category_id)
        if not category or category.organization_id != org_id:
            return FAPostingResult(success=False, message="Asset category not found")

        amount = asset.acquisition_cost
        functional_amount = asset.functional_currency_cost or amount

        exchange_rate = Decimal("1.0")
        if amount and functional_amount:
            exchange_rate = (functional_amount / amount).quantize(Decimal("0.000001"))

        journal_lines = [
            JournalLineInput(
                account_id=category.asset_account_id,
                debit_amount=amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=functional_amount,
                credit_amount_functional=Decimal("0"),
                currency_code=asset.currency_code,
                exchange_rate=exchange_rate,
                description="Asset acquisition",
                cost_center_id=asset.cost_center_id,
                project_id=asset.project_id,
            ),
            JournalLineInput(
                account_id=credit_id,
                debit_amount=Decimal("0"),
                credit_amount=amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=functional_amount,
                currency_code=asset.currency_code,
                exchange_rate=exchange_rate,
                description="Asset acquisition offset",
                cost_center_id=asset.cost_center_id,
                project_id=asset.project_id,
            ),
        ]

        functional_currency = org_context_service.get_functional_currency(db, org_id)
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=description or f"Asset acquisition {asset.asset_number}",
            reference=f"FA-ACQ-{asset.asset_number}",
            currency_code=functional_currency,
            exchange_rate=exchange_rate,
            lines=journal_lines,
            source_module="FA",
            source_document_type="ASSET_ACQUISITION",
            source_document_id=ast_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return FAPostingResult(success=False, message=error.message)

        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id, "FA:ACQ", ast_id, action="post"
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="FA",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Asset acquisition posted successfully",
        )
        if not posting_result.success:
            return FAPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        return FAPostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_depreciation_run(
        db: Session,
        organization_id: UUID,
        run_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
    ) -> FAPostingResult:
        """
        Post a depreciation run to the general ledger.

        Creates a journal entry with:
        - Debit: Depreciation Expense accounts
        - Credit: Accumulated Depreciation accounts

        Args:
            db: Database session
            organization_id: Organization scope
            run_id: Depreciation run to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            FAPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        r_id = coerce_uuid(run_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load run
        run = db.get(DepreciationRun, r_id)
        if not run or run.organization_id != org_id:
            return FAPostingResult(success=False, message="Depreciation run not found")

        if run.status != DepreciationRunStatus.POSTING:
            return FAPostingResult(
                success=False,
                message=f"Run must be in POSTING status (current: {run.status.value})",
            )

        # Load schedules
        schedules = (
            select(DepreciationSchedule)
            .where(DepreciationSchedule.run_id == r_id)
            .all()
        )

        if not schedules:
            return FAPostingResult(
                success=False, message="No depreciation schedules found"
            )

        # Build journal lines - aggregate by account
        expense_by_account: dict[UUID, Decimal] = {}
        accum_by_account: dict[UUID, Decimal] = {}

        for schedule in schedules:
            if schedule.depreciation_amount > 0:
                # Debit expense
                expense_by_account[schedule.expense_account_id] = (
                    expense_by_account.get(schedule.expense_account_id, Decimal("0"))
                    + schedule.depreciation_amount
                )
                # Credit accumulated depreciation
                accum_by_account[schedule.accumulated_depreciation_account_id] = (
                    accum_by_account.get(
                        schedule.accumulated_depreciation_account_id, Decimal("0")
                    )
                    + schedule.depreciation_amount
                )

        journal_lines: list[JournalLineInput] = []

        # Expense lines (Debit)
        for account_id, amount in expense_by_account.items():
            journal_lines.append(
                JournalLineInput(
                    account_id=account_id,
                    debit_amount=amount,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=amount,
                    credit_amount_functional=Decimal("0"),
                    description=f"Depreciation expense - Run #{run.run_number}",
                )
            )

        # Accumulated depreciation lines (Credit)
        for account_id, amount in accum_by_account.items():
            journal_lines.append(
                JournalLineInput(
                    account_id=account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=amount,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=amount,
                    description=f"Accumulated depreciation - Run #{run.run_number}",
                )
            )

        # Get organization's functional currency
        functional_currency = org_context_service.get_functional_currency(db, org_id)

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"FA Depreciation Run #{run.run_number}",
            reference=f"DEP-{run.run_number}",
            currency_code=functional_currency,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="FA",
            source_document_type="DEPRECIATION_RUN",
            source_document_id=r_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return FAPostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id, "FA:DEP", r_id, action="post"
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="FA",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Depreciation posted successfully",
        )
        if not posting_result.success:
            return FAPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        return FAPostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_asset_disposal(
        db: Session,
        organization_id: UUID,
        disposal_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
    ) -> FAPostingResult:
        """
        Post an asset disposal to the general ledger.

        Creates a journal entry with:
        - Debit: Accumulated Depreciation (clear accum dep)
        - Debit: Bank/Cash (if proceeds)
        - Debit/Credit: Gain/Loss account (balancing)
        - Credit: Asset account (remove asset cost)

        Args:
            db: Database session
            organization_id: Organization scope
            disposal_id: Disposal to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            FAPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        disp_id = coerce_uuid(disposal_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load disposal
        disposal = db.get(AssetDisposal, disp_id)
        if not disposal:
            return FAPostingResult(success=False, message="Disposal not found")

        # Load asset and category
        asset = db.get(Asset, disposal.asset_id)
        if not asset or asset.organization_id != org_id:
            return FAPostingResult(success=False, message="Asset not found")

        category = db.get(AssetCategory, asset.category_id)
        if not category:
            return FAPostingResult(success=False, message="Category not found")

        journal_lines: list[JournalLineInput] = []

        # 1. Debit: Accumulated Depreciation (clear it)
        if disposal.accumulated_depreciation_at_disposal > 0:
            journal_lines.append(
                JournalLineInput(
                    account_id=category.accumulated_depreciation_account_id,
                    debit_amount=disposal.accumulated_depreciation_at_disposal,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=disposal.accumulated_depreciation_at_disposal,
                    credit_amount_functional=Decimal("0"),
                    description=f"Clear accum dep: {asset.asset_name}",
                )
            )

        # 2. Debit: Cash/Bank (if there are proceeds)
        # Note: This would need a receivables account for actual implementation
        # For now, we'll book directly to gain/loss
        if disposal.net_proceeds > 0:
            journal_lines.append(
                JournalLineInput(
                    account_id=category.gain_loss_disposal_account_id,
                    debit_amount=disposal.net_proceeds,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=disposal.net_proceeds,
                    credit_amount_functional=Decimal("0"),
                    description=f"Disposal proceeds: {asset.asset_name}",
                )
            )

        # 3. Credit: Asset account (remove cost)
        journal_lines.append(
            JournalLineInput(
                account_id=category.asset_account_id,
                debit_amount=Decimal("0"),
                credit_amount=disposal.cost_at_disposal,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=disposal.cost_at_disposal,
                description=f"Remove asset: {asset.asset_name}",
            )
        )

        # 4. Gain or Loss on disposal (balancing entry)
        if disposal.gain_loss_on_disposal > 0:
            # Gain - credit
            journal_lines.append(
                JournalLineInput(
                    account_id=category.gain_loss_disposal_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=disposal.gain_loss_on_disposal,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=disposal.gain_loss_on_disposal,
                    description=f"Gain on disposal: {asset.asset_name}",
                )
            )
        elif disposal.gain_loss_on_disposal < 0:
            # Loss - debit
            journal_lines.append(
                JournalLineInput(
                    account_id=category.gain_loss_disposal_account_id,
                    debit_amount=abs(disposal.gain_loss_on_disposal),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(disposal.gain_loss_on_disposal),
                    credit_amount_functional=Decimal("0"),
                    description=f"Loss on disposal: {asset.asset_name}",
                )
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=disposal.disposal_date,
            posting_date=posting_date,
            description=f"Asset Disposal: {asset.asset_name}",
            reference=f"DISP-{asset.asset_number}",
            currency_code=asset.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="FA",
            source_document_type="ASSET_DISPOSAL",
            source_document_id=disp_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return FAPostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id, "FA:DISP", disp_id, action="post"
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="FA",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Disposal posted successfully",
        )
        if not posting_result.success:
            return FAPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        return FAPostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )

    @staticmethod
    def post_revaluation(
        db: Session,
        organization_id: UUID,
        revaluation_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
    ) -> FAPostingResult:
        """
        Post an asset revaluation to the general ledger.

        IAS 16 revaluation model:
        - Surplus: Credit to Revaluation Surplus (OCI/Equity)
        - Deficit: Debit to P&L (unless reversing prior surplus)

        Args:
            db: Database session
            organization_id: Organization scope
            revaluation_id: Revaluation to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            FAPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        reval_id = coerce_uuid(revaluation_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load revaluation
        revaluation = db.get(AssetRevaluation, reval_id)
        if not revaluation:
            return FAPostingResult(success=False, message="Revaluation not found")

        # Load asset and category
        asset = db.get(Asset, revaluation.asset_id)
        if not asset or asset.organization_id != org_id:
            return FAPostingResult(success=False, message="Asset not found")

        category = db.get(AssetCategory, asset.category_id)
        if not category:
            return FAPostingResult(success=False, message="Category not found")

        if not category.revaluation_surplus_account_id:
            return FAPostingResult(
                success=False,
                message="Category does not have a revaluation surplus account configured",
            )

        journal_lines: list[JournalLineInput] = []

        # Determine entries based on surplus or deficit
        if revaluation.revaluation_surplus_or_deficit > 0:
            # Surplus - increase asset value
            # Debit: Asset account
            journal_lines.append(
                JournalLineInput(
                    account_id=category.asset_account_id,
                    debit_amount=abs(revaluation.revaluation_surplus_or_deficit),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(
                        revaluation.revaluation_surplus_or_deficit
                    ),
                    credit_amount_functional=Decimal("0"),
                    description=f"Revaluation increase: {asset.asset_name}",
                )
            )

            # Credit: Revaluation surplus (OCI)
            if revaluation.surplus_to_equity > 0:
                journal_lines.append(
                    JournalLineInput(
                        account_id=category.revaluation_surplus_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=revaluation.surplus_to_equity,
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=revaluation.surplus_to_equity,
                        description=f"Revaluation surplus: {asset.asset_name}",
                    )
                )

            # Credit: P&L if reversing prior deficit
            if revaluation.prior_deficit_reversed > 0:
                journal_lines.append(
                    JournalLineInput(
                        account_id=category.impairment_loss_account_id
                        or category.gain_loss_disposal_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=revaluation.prior_deficit_reversed,
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=revaluation.prior_deficit_reversed,
                        description=f"Reversal of prior revaluation loss: {asset.asset_name}",
                    )
                )

        elif revaluation.revaluation_surplus_or_deficit < 0:
            # Deficit - decrease asset value
            # Credit: Asset account
            journal_lines.append(
                JournalLineInput(
                    account_id=category.asset_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=abs(revaluation.revaluation_surplus_or_deficit),
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=abs(
                        revaluation.revaluation_surplus_or_deficit
                    ),
                    description=f"Revaluation decrease: {asset.asset_name}",
                )
            )

            # Debit: Revaluation surplus (if reversing prior surplus)
            if revaluation.prior_surplus_reversed > 0:
                journal_lines.append(
                    JournalLineInput(
                        account_id=category.revaluation_surplus_account_id,
                        debit_amount=revaluation.prior_surplus_reversed,
                        credit_amount=Decimal("0"),
                        debit_amount_functional=revaluation.prior_surplus_reversed,
                        credit_amount_functional=Decimal("0"),
                        description=f"Reversal of prior revaluation surplus: {asset.asset_name}",
                    )
                )

            # Debit: P&L for remaining deficit
            if revaluation.deficit_to_pl > 0:
                journal_lines.append(
                    JournalLineInput(
                        account_id=category.impairment_loss_account_id
                        or category.gain_loss_disposal_account_id,
                        debit_amount=revaluation.deficit_to_pl,
                        credit_amount=Decimal("0"),
                        debit_amount_functional=revaluation.deficit_to_pl,
                        credit_amount_functional=Decimal("0"),
                        description=f"Revaluation loss: {asset.asset_name}",
                    )
                )

        if not journal_lines:
            return FAPostingResult(
                success=False,
                message="No journal entries to create - revaluation amount is zero",
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=revaluation.revaluation_date,
            posting_date=posting_date,
            description=f"Asset Revaluation: {asset.asset_name}",
            reference=f"REVAL-{asset.asset_number}",
            currency_code=asset.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="FA",
            source_document_type="ASSET_REVALUATION",
            source_document_id=reval_id,
        )

        journal, error = BasePostingAdapter.create_and_approve_journal(
            db,
            org_id,
            journal_input,
            user_id,
            error_prefix="Journal creation failed",
        )
        if error:
            return FAPostingResult(success=False, message=error.message)

        # Post to ledger
        if not idempotency_key:
            idempotency_key = BasePostingAdapter.make_idempotency_key(
                org_id, "FA:REVAL", reval_id, action="post"
            )

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="FA",
            correlation_id=None,
            posted_by_user_id=user_id,
            success_message="Revaluation posted successfully",
        )
        if not posting_result.success:
            return FAPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=posting_result.message,
            )

        return FAPostingResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            posting_batch_id=posting_result.posting_batch_id,
            message=posting_result.message,
        )


# Module-level singleton instance
fa_posting_adapter = FAPostingAdapter()
