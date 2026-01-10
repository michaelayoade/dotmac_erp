"""
FININSTPostingAdapter - Converts financial instrument transactions to GL entries.

Transforms interest accruals, valuations, ECL movements, and hedge accounting
entries into journal entries and posts them to the general ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.fin_inst.financial_instrument import (
    FinancialInstrument,
    InstrumentClassification,
)
from app.models.ifrs.fin_inst.interest_accrual import InterestAccrual
from app.models.ifrs.fin_inst.instrument_valuation import InstrumentValuation
from app.models.ifrs.fin_inst.hedge_relationship import HedgeRelationship, HedgeType
from app.models.ifrs.fin_inst.hedge_effectiveness import HedgeEffectiveness
from app.services.common import coerce_uuid
from app.services.ifrs.gl.journal import JournalService, JournalInput, JournalLineInput
from app.services.ifrs.gl.ledger_posting import LedgerPostingService, PostingRequest
from app.models.ifrs.gl.journal_entry import JournalType


@dataclass
class FININSTPostingResult:
    """Result of a financial instruments posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""


class FININSTPostingAdapter:
    """
    Adapter for posting financial instrument transactions to the general ledger.

    Converts interest accruals, fair value changes, ECL movements, and hedge
    accounting entries into journal entries.
    """

    @staticmethod
    def post_interest_accrual(
        db: Session,
        organization_id: UUID,
        accrual_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> FININSTPostingResult:
        """
        Post interest accrual to the general ledger.

        Creates journal entry:
        - For assets: Dr Interest Receivable, Cr Interest Income
        - For liabilities: Dr Interest Expense, Cr Interest Payable

        Args:
            db: Database session
            organization_id: Organization scope
            accrual_id: Interest accrual to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            FININSTPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        acc_id = coerce_uuid(accrual_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load accrual
        accrual = db.get(InterestAccrual, acc_id)
        if not accrual:
            return FININSTPostingResult(success=False, message="Interest accrual not found")

        # Load instrument
        instrument = db.get(FinancialInstrument, accrual.instrument_id)
        if not instrument or instrument.organization_id != org_id:
            return FININSTPostingResult(success=False, message="Instrument not found")

        if accrual.effective_interest_income == 0:
            return FININSTPostingResult(success=False, message="No interest to accrue")

        if not instrument.interest_receivable_account_id or not instrument.interest_income_account_id:
            return FININSTPostingResult(
                success=False,
                message="Instrument missing interest accounts",
            )

        journal_lines: list[JournalLineInput] = []

        if instrument.is_asset:
            # Asset: Dr Interest Receivable, Cr Interest Income
            journal_lines.append(
                JournalLineInput(
                    account_id=instrument.interest_receivable_account_id,
                    debit_amount=accrual.effective_interest_income,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=accrual.functional_currency_amount,
                    credit_amount_functional=Decimal("0"),
                    description=f"Interest receivable - {instrument.instrument_name}",
                )
            )
            journal_lines.append(
                JournalLineInput(
                    account_id=instrument.interest_income_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=accrual.effective_interest_income,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=accrual.functional_currency_amount,
                    description=f"Interest income - {instrument.instrument_name}",
                )
            )
        else:
            # Liability: Dr Interest Expense, Cr Interest Payable
            journal_lines.append(
                JournalLineInput(
                    account_id=instrument.interest_income_account_id,  # Used as expense for liabilities
                    debit_amount=accrual.effective_interest_income,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=accrual.functional_currency_amount,
                    credit_amount_functional=Decimal("0"),
                    description=f"Interest expense - {instrument.instrument_name}",
                )
            )
            journal_lines.append(
                JournalLineInput(
                    account_id=instrument.interest_receivable_account_id,  # Used as payable for liabilities
                    debit_amount=Decimal("0"),
                    credit_amount=accrual.effective_interest_income,
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=accrual.functional_currency_amount,
                    description=f"Interest payable - {instrument.instrument_name}",
                )
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=accrual.accrual_end_date,
            posting_date=posting_date,
            description=f"Interest Accrual: {instrument.instrument_name}",
            reference=f"INT-{instrument.instrument_code}",
            currency_code=instrument.currency_code,
            exchange_rate=accrual.exchange_rate or Decimal("1.0"),
            lines=journal_lines,
            source_module="FIN_INST",
            source_document_type="INTEREST_ACCRUAL",
            source_document_id=acc_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return FININSTPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:FININST:INT:{acc_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="FIN_INST",
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return FININSTPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            # Update accrual with journal reference
            accrual.journal_entry_id = journal.journal_entry_id
            db.commit()

            return FININSTPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Interest accrual posted successfully",
            )

        except Exception as e:
            return FININSTPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )

    @staticmethod
    def post_fair_value_change(
        db: Session,
        organization_id: UUID,
        valuation_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> FININSTPostingResult:
        """
        Post fair value change to the general ledger.

        FVPL: Dr/Cr Instrument, Cr/Dr FV Gain/Loss (P&L)
        FVOCI: Dr/Cr Instrument, Cr/Dr OCI

        Args:
            db: Database session
            organization_id: Organization scope
            valuation_id: Valuation record to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            FININSTPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        val_id = coerce_uuid(valuation_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load valuation
        valuation = db.get(InstrumentValuation, val_id)
        if not valuation:
            return FININSTPostingResult(success=False, message="Valuation not found")

        # Load instrument
        instrument = db.get(FinancialInstrument, valuation.instrument_id)
        if not instrument or instrument.organization_id != org_id:
            return FININSTPostingResult(success=False, message="Instrument not found")

        # Check if there's anything to post
        fv_change_pl = valuation.fv_change_pl
        fv_change_oci = valuation.fv_change_oci

        if fv_change_pl == 0 and fv_change_oci == 0:
            return FININSTPostingResult(
                success=True,
                message="No fair value changes to post",
            )

        if not instrument.fv_adjustment_account_id:
            return FININSTPostingResult(
                success=False,
                message="Instrument missing FV adjustment account",
            )

        journal_lines: list[JournalLineInput] = []

        # P&L fair value change (FVPL instruments)
        if fv_change_pl != 0:
            if fv_change_pl > 0:
                # Gain: Dr Instrument, Cr FV Gain
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.instrument_account_id,
                        debit_amount=abs(fv_change_pl),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(fv_change_pl),
                        credit_amount_functional=Decimal("0"),
                        description=f"FV increase - {instrument.instrument_name}",
                    )
                )
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.fv_adjustment_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(fv_change_pl),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(fv_change_pl),
                        description=f"FV gain P&L - {instrument.instrument_name}",
                    )
                )
            else:
                # Loss: Dr FV Loss, Cr Instrument
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.fv_adjustment_account_id,
                        debit_amount=abs(fv_change_pl),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(fv_change_pl),
                        credit_amount_functional=Decimal("0"),
                        description=f"FV loss P&L - {instrument.instrument_name}",
                    )
                )
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.instrument_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(fv_change_pl),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(fv_change_pl),
                        description=f"FV decrease - {instrument.instrument_name}",
                    )
                )

        # OCI fair value change (FVOCI instruments)
        if fv_change_oci != 0 and instrument.oci_account_id:
            if fv_change_oci > 0:
                # Gain: Dr Instrument, Cr OCI
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.instrument_account_id,
                        debit_amount=abs(fv_change_oci),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(fv_change_oci),
                        credit_amount_functional=Decimal("0"),
                        description=f"FV increase - {instrument.instrument_name}",
                    )
                )
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.oci_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(fv_change_oci),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(fv_change_oci),
                        description=f"FV gain OCI - {instrument.instrument_name}",
                    )
                )
            else:
                # Loss: Dr OCI, Cr Instrument
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.oci_account_id,
                        debit_amount=abs(fv_change_oci),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(fv_change_oci),
                        credit_amount_functional=Decimal("0"),
                        description=f"FV loss OCI - {instrument.instrument_name}",
                    )
                )
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.instrument_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(fv_change_oci),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(fv_change_oci),
                        description=f"FV decrease - {instrument.instrument_name}",
                    )
                )

        if not journal_lines:
            return FININSTPostingResult(
                success=True,
                message="No journal entries required",
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=valuation.valuation_date,
            posting_date=posting_date,
            description=f"FV Adjustment: {instrument.instrument_name}",
            reference=f"FV-{instrument.instrument_code}",
            currency_code=instrument.currency_code,
            exchange_rate=valuation.exchange_rate or Decimal("1.0"),
            lines=journal_lines,
            source_module="FIN_INST",
            source_document_type="FAIR_VALUE_CHANGE",
            source_document_id=val_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return FININSTPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:FININST:FV:{val_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="FIN_INST",
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return FININSTPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            # Update valuation with journal reference
            valuation.valuation_journal_entry_id = journal.journal_entry_id
            db.commit()

            return FININSTPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Fair value change posted successfully",
            )

        except Exception as e:
            return FININSTPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )

    @staticmethod
    def post_ecl_movement(
        db: Session,
        organization_id: UUID,
        instrument_id: UUID,
        ecl_movement: Decimal,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> FININSTPostingResult:
        """
        Post ECL (Expected Credit Loss) movement to the general ledger.

        Dr ECL Expense / Cr Loss Allowance (for increases)
        Dr Loss Allowance / Cr ECL Expense (for decreases/releases)

        Args:
            db: Database session
            organization_id: Organization scope
            instrument_id: Instrument with ECL movement
            ecl_movement: ECL movement amount (positive = increase)
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            FININSTPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instrument_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load instrument
        instrument = db.get(FinancialInstrument, inst_id)
        if not instrument or instrument.organization_id != org_id:
            return FININSTPostingResult(success=False, message="Instrument not found")

        if ecl_movement == 0:
            return FININSTPostingResult(
                success=True,
                message="No ECL movement to post",
            )

        if not instrument.ecl_expense_account_id:
            return FININSTPostingResult(
                success=False,
                message="Instrument missing ECL expense account",
            )

        journal_lines: list[JournalLineInput] = []

        if ecl_movement > 0:
            # ECL increase: Dr Expense, Cr Allowance (contra asset)
            journal_lines.append(
                JournalLineInput(
                    account_id=instrument.ecl_expense_account_id,
                    debit_amount=abs(ecl_movement),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(ecl_movement),
                    credit_amount_functional=Decimal("0"),
                    description=f"ECL expense - {instrument.instrument_name}",
                )
            )
            journal_lines.append(
                JournalLineInput(
                    account_id=instrument.instrument_account_id,  # Or separate allowance account
                    debit_amount=Decimal("0"),
                    credit_amount=abs(ecl_movement),
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=abs(ecl_movement),
                    description=f"Loss allowance - {instrument.instrument_name}",
                )
            )
        else:
            # ECL release: Dr Allowance, Cr Expense
            journal_lines.append(
                JournalLineInput(
                    account_id=instrument.instrument_account_id,
                    debit_amount=abs(ecl_movement),
                    credit_amount=Decimal("0"),
                    debit_amount_functional=abs(ecl_movement),
                    credit_amount_functional=Decimal("0"),
                    description=f"Loss allowance release - {instrument.instrument_name}",
                )
            )
            journal_lines.append(
                JournalLineInput(
                    account_id=instrument.ecl_expense_account_id,
                    debit_amount=Decimal("0"),
                    credit_amount=abs(ecl_movement),
                    debit_amount_functional=Decimal("0"),
                    credit_amount_functional=abs(ecl_movement),
                    description=f"ECL release - {instrument.instrument_name}",
                )
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=posting_date,
            posting_date=posting_date,
            description=f"ECL Movement: {instrument.instrument_name}",
            reference=f"ECL-{instrument.instrument_code}",
            currency_code=instrument.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="FIN_INST",
            source_document_type="ECL_MOVEMENT",
            source_document_id=inst_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return FININSTPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:FININST:ECL:{inst_id}:{posting_date.isoformat()}:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="FIN_INST",
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return FININSTPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            return FININSTPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="ECL movement posted successfully",
            )

        except Exception as e:
            return FININSTPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )

    @staticmethod
    def post_hedge_ineffectiveness(
        db: Session,
        organization_id: UUID,
        effectiveness_id: UUID,
        hedge_ineffectiveness_account_id: UUID,
        cash_flow_hedge_reserve_account_id: Optional[UUID],
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> FININSTPostingResult:
        """
        Post hedge accounting entries to the general ledger.

        For ineffectiveness: Dr/Cr Hedge Ineffectiveness P&L
        For cash flow hedges: Dr/Cr Cash Flow Hedge Reserve (OCI)

        Args:
            db: Database session
            organization_id: Organization scope
            effectiveness_id: Hedge effectiveness record to post
            hedge_ineffectiveness_account_id: P&L account for ineffectiveness
            cash_flow_hedge_reserve_account_id: OCI account for CFH reserve
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            FININSTPostingResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        eff_id = coerce_uuid(effectiveness_id)
        user_id = coerce_uuid(posted_by_user_id)

        # Load effectiveness
        effectiveness = db.get(HedgeEffectiveness, eff_id)
        if not effectiveness:
            return FININSTPostingResult(success=False, message="Effectiveness record not found")

        # Load hedge
        hedge = db.get(HedgeRelationship, effectiveness.hedge_id)
        if not hedge or hedge.organization_id != org_id:
            return FININSTPostingResult(success=False, message="Hedge relationship not found")

        # Load hedging instrument
        instrument = db.get(FinancialInstrument, hedge.hedging_instrument_id)
        if not instrument:
            return FININSTPostingResult(success=False, message="Hedging instrument not found")

        journal_lines: list[JournalLineInput] = []

        # Post ineffectiveness to P&L
        if effectiveness.ineffectiveness_recognized_pl != 0:
            ineff = effectiveness.ineffectiveness_recognized_pl
            if ineff > 0:
                # Loss
                journal_lines.append(
                    JournalLineInput(
                        account_id=hedge_ineffectiveness_account_id,
                        debit_amount=abs(ineff),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(ineff),
                        credit_amount_functional=Decimal("0"),
                        description=f"Hedge ineffectiveness - {hedge.hedge_name}",
                    )
                )
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.instrument_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(ineff),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(ineff),
                        description=f"Hedging instrument FV - {hedge.hedge_name}",
                    )
                )
            else:
                # Gain
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.instrument_account_id,
                        debit_amount=abs(ineff),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(ineff),
                        credit_amount_functional=Decimal("0"),
                        description=f"Hedging instrument FV - {hedge.hedge_name}",
                    )
                )
                journal_lines.append(
                    JournalLineInput(
                        account_id=hedge_ineffectiveness_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(ineff),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(ineff),
                        description=f"Hedge ineffectiveness gain - {hedge.hedge_name}",
                    )
                )

        # Post effective portion to OCI for cash flow hedges
        if (
            hedge.hedge_type == HedgeType.CASH_FLOW
            and effectiveness.effective_portion_oci != 0
            and cash_flow_hedge_reserve_account_id
        ):
            eff_oci = effectiveness.effective_portion_oci
            if eff_oci > 0:
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.instrument_account_id,
                        debit_amount=abs(eff_oci),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(eff_oci),
                        credit_amount_functional=Decimal("0"),
                        description=f"CFH effective portion - {hedge.hedge_name}",
                    )
                )
                journal_lines.append(
                    JournalLineInput(
                        account_id=cash_flow_hedge_reserve_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(eff_oci),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(eff_oci),
                        description=f"CFH reserve OCI - {hedge.hedge_name}",
                    )
                )
            else:
                journal_lines.append(
                    JournalLineInput(
                        account_id=cash_flow_hedge_reserve_account_id,
                        debit_amount=abs(eff_oci),
                        credit_amount=Decimal("0"),
                        debit_amount_functional=abs(eff_oci),
                        credit_amount_functional=Decimal("0"),
                        description=f"CFH reserve OCI - {hedge.hedge_name}",
                    )
                )
                journal_lines.append(
                    JournalLineInput(
                        account_id=instrument.instrument_account_id,
                        debit_amount=Decimal("0"),
                        credit_amount=abs(eff_oci),
                        debit_amount_functional=Decimal("0"),
                        credit_amount_functional=abs(eff_oci),
                        description=f"CFH effective portion - {hedge.hedge_name}",
                    )
                )

        if not journal_lines:
            return FININSTPostingResult(
                success=True,
                message="No hedge entries to post",
            )

        # Create journal entry
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=effectiveness.test_date,
            posting_date=posting_date,
            description=f"Hedge Accounting: {hedge.hedge_name}",
            reference=f"HEDGE-{hedge.hedge_code}",
            currency_code=instrument.currency_code,
            exchange_rate=Decimal("1.0"),
            lines=journal_lines,
            source_module="FIN_INST",
            source_document_type="HEDGE_ACCOUNTING",
            source_document_id=eff_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(db, org_id, journal.journal_entry_id, user_id)

        except HTTPException as e:
            return FININSTPostingResult(
                success=False, message=f"Journal creation failed: {e.detail}"
            )

        # Post to ledger
        if not idempotency_key:
            idempotency_key = f"{org_id}:FININST:HEDGE:{eff_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module="FIN_INST",
            posted_by_user_id=user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

            if not posting_result.success:
                return FININSTPostingResult(
                    success=False,
                    journal_entry_id=journal.journal_entry_id,
                    message=f"Ledger posting failed: {posting_result.message}",
                )

            # Update effectiveness with journal reference
            effectiveness.effectiveness_journal_entry_id = journal.journal_entry_id
            db.commit()

            return FININSTPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message="Hedge accounting posted successfully",
            )

        except Exception as e:
            return FININSTPostingResult(
                success=False,
                journal_entry_id=journal.journal_entry_id,
                message=f"Posting error: {str(e)}",
            )


# Module-level singleton instance
fin_inst_posting_adapter = FININSTPostingAdapter()
