"""
FinancialInstrumentService - IFRS 9 financial instrument management.

Manages financial instrument lifecycle, classification, and ECL staging.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.fin_inst.financial_instrument import (
    FinancialInstrument,
    InstrumentType,
    InstrumentClassification,
    InstrumentStatus,
)
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class InstrumentInput:
    """Input for creating a financial instrument."""

    instrument_code: str
    instrument_name: str
    instrument_type: InstrumentType
    classification: InstrumentClassification
    counterparty_type: str
    counterparty_name: str
    currency_code: str
    face_value: Decimal
    trade_date: date
    settlement_date: date
    acquisition_cost: Decimal
    instrument_account_id: UUID
    description: Optional[str] = None
    is_asset: bool = True
    counterparty_id: Optional[UUID] = None
    isin: Optional[str] = None
    cusip: Optional[str] = None
    external_reference: Optional[str] = None
    maturity_date: Optional[date] = None
    is_interest_bearing: bool = False
    interest_rate_type: Optional[str] = None
    stated_interest_rate: Optional[Decimal] = None
    effective_interest_rate: Optional[Decimal] = None
    interest_payment_frequency: Optional[str] = None
    day_count_convention: Optional[str] = None
    next_interest_date: Optional[date] = None
    transaction_costs: Decimal = Decimal("0")
    interest_receivable_account_id: Optional[UUID] = None
    interest_income_account_id: Optional[UUID] = None
    fv_adjustment_account_id: Optional[UUID] = None
    oci_account_id: Optional[UUID] = None
    ecl_expense_account_id: Optional[UUID] = None


@dataclass
class ECLStagingResult:
    """Result of ECL staging assessment."""

    previous_stage: int
    new_stage: int
    stage_changed: bool
    loss_allowance: Decimal
    ecl_movement: Decimal
    is_credit_impaired: bool


class FinancialInstrumentService(ListResponseMixin):
    """
    Service for IFRS 9 financial instrument management.

    Handles instrument creation, classification, ECL staging, and lifecycle.
    """

    @staticmethod
    def calculate_premium_discount(
        face_value: Decimal,
        acquisition_cost: Decimal,
        transaction_costs: Decimal,
    ) -> Decimal:
        """
        Calculate premium or discount on acquisition.

        Premium (negative) = paid more than face value
        Discount (positive) = paid less than face value

        Args:
            face_value: Par/face value of instrument
            acquisition_cost: Price paid
            transaction_costs: Transaction costs (capitalized for amortized cost)

        Returns:
            Premium/discount amount
        """
        total_cost = acquisition_cost + transaction_costs
        return face_value - total_cost

    @staticmethod
    def determine_initial_carrying_amount(
        classification: InstrumentClassification,
        acquisition_cost: Decimal,
        transaction_costs: Decimal,
        fair_value: Optional[Decimal] = None,
    ) -> Decimal:
        """
        Determine initial carrying amount per IFRS 9.

        Args:
            classification: IFRS 9 classification
            acquisition_cost: Acquisition cost
            transaction_costs: Transaction costs
            fair_value: Fair value (for FVPL instruments)

        Returns:
            Initial carrying amount
        """
        if classification in [
            InstrumentClassification.FVPL,
            InstrumentClassification.FVPL_LIABILITY,
        ]:
            # FVPL: Transaction costs expensed, measure at fair value
            return fair_value or acquisition_cost
        else:
            # Amortized cost / FVOCI: Include transaction costs
            return acquisition_cost + transaction_costs

    @staticmethod
    def create_instrument(
        db: Session,
        organization_id: UUID,
        input: InstrumentInput,
        created_by_user_id: UUID,
    ) -> FinancialInstrument:
        """
        Create a new financial instrument.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Instrument input data
            created_by_user_id: User creating the instrument

        Returns:
            Created FinancialInstrument
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)

        # Check for duplicate code
        existing = (
            db.query(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == org_id,
                FinancialInstrument.instrument_code == input.instrument_code,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Instrument code '{input.instrument_code}' already exists",
            )

        # Calculate premium/discount
        premium_discount = FinancialInstrumentService.calculate_premium_discount(
            face_value=input.face_value,
            acquisition_cost=input.acquisition_cost,
            transaction_costs=input.transaction_costs,
        )

        # Determine initial carrying amount
        carrying_amount = FinancialInstrumentService.determine_initial_carrying_amount(
            classification=input.classification,
            acquisition_cost=input.acquisition_cost,
            transaction_costs=input.transaction_costs,
            fair_value=input.acquisition_cost,  # Assume FV = cost at inception
        )

        # Initial amortized cost = carrying amount for most instruments
        amortized_cost = carrying_amount

        instrument = FinancialInstrument(
            organization_id=org_id,
            instrument_code=input.instrument_code,
            instrument_name=input.instrument_name,
            description=input.description,
            instrument_type=input.instrument_type,
            classification=input.classification,
            is_asset=input.is_asset,
            counterparty_type=input.counterparty_type,
            counterparty_id=input.counterparty_id,
            counterparty_name=input.counterparty_name,
            isin=input.isin,
            cusip=input.cusip,
            external_reference=input.external_reference,
            currency_code=input.currency_code,
            face_value=input.face_value,
            current_principal=input.face_value,
            trade_date=input.trade_date,
            settlement_date=input.settlement_date,
            maturity_date=input.maturity_date,
            is_interest_bearing=input.is_interest_bearing,
            interest_rate_type=input.interest_rate_type,
            stated_interest_rate=input.stated_interest_rate,
            effective_interest_rate=input.effective_interest_rate or input.stated_interest_rate,
            interest_payment_frequency=input.interest_payment_frequency,
            day_count_convention=input.day_count_convention,
            next_interest_date=input.next_interest_date,
            acquisition_cost=input.acquisition_cost,
            transaction_costs=input.transaction_costs,
            premium_discount=premium_discount,
            amortized_cost=amortized_cost,
            fair_value=input.acquisition_cost,
            carrying_amount=carrying_amount,
            ecl_stage=1,
            loss_allowance=Decimal("0"),
            is_credit_impaired=False,
            accumulated_oci=Decimal("0"),
            status=InstrumentStatus.ACTIVE,
            instrument_account_id=input.instrument_account_id,
            interest_receivable_account_id=input.interest_receivable_account_id,
            interest_income_account_id=input.interest_income_account_id,
            fv_adjustment_account_id=input.fv_adjustment_account_id,
            oci_account_id=input.oci_account_id,
            ecl_expense_account_id=input.ecl_expense_account_id,
            created_by_user_id=user_id,
        )

        db.add(instrument)
        db.commit()
        db.refresh(instrument)

        return instrument

    @staticmethod
    def update_fair_value(
        db: Session,
        organization_id: UUID,
        instrument_id: UUID,
        new_fair_value: Decimal,
        valuation_date: date,
    ) -> tuple[FinancialInstrument, Decimal, Decimal]:
        """
        Update fair value of an instrument.

        Returns the FV change and where it should be recognized (P&L or OCI).

        Args:
            db: Database session
            organization_id: Organization scope
            instrument_id: Instrument to update
            new_fair_value: New fair value
            valuation_date: Date of valuation

        Returns:
            Tuple of (instrument, fv_change_pl, fv_change_oci)
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instrument_id)

        instrument = db.get(FinancialInstrument, inst_id)
        if not instrument or instrument.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Instrument not found")

        old_fair_value = instrument.fair_value or instrument.carrying_amount
        fv_change = new_fair_value - old_fair_value

        fv_change_pl = Decimal("0")
        fv_change_oci = Decimal("0")

        # Determine where to recognize FV change based on classification
        if instrument.classification == InstrumentClassification.FVPL:
            # FVPL: All changes to P&L
            fv_change_pl = fv_change
            instrument.carrying_amount = new_fair_value
        elif instrument.classification == InstrumentClassification.FVOCI_DEBT:
            # FVOCI debt: FV change to OCI, impairment to P&L
            fv_change_oci = fv_change
            instrument.accumulated_oci += fv_change
            instrument.carrying_amount = new_fair_value
        elif instrument.classification == InstrumentClassification.FVOCI_EQUITY:
            # FVOCI equity: All changes to OCI (no recycling)
            fv_change_oci = fv_change
            instrument.accumulated_oci += fv_change
            instrument.carrying_amount = new_fair_value
        elif instrument.classification == InstrumentClassification.FVPL_LIABILITY:
            # FVPL liability: All changes to P&L
            fv_change_pl = fv_change
            instrument.carrying_amount = new_fair_value
        # Amortized cost instruments don't have FV changes in P&L/OCI

        instrument.fair_value = new_fair_value

        db.commit()
        db.refresh(instrument)

        return (instrument, fv_change_pl, fv_change_oci)

    @staticmethod
    def assess_ecl_staging(
        db: Session,
        organization_id: UUID,
        instrument_id: UUID,
        pd_increase_significant: bool = False,
        is_30_days_past_due: bool = False,
        is_90_days_past_due: bool = False,
        is_credit_impaired: bool = False,
        loss_rate: Decimal = Decimal("0"),
    ) -> ECLStagingResult:
        """
        Assess and update ECL staging per IFRS 9.

        Stage 1: 12-month ECL (no significant increase in credit risk)
        Stage 2: Lifetime ECL (significant increase in credit risk)
        Stage 3: Lifetime ECL (credit-impaired)

        Args:
            db: Database session
            organization_id: Organization scope
            instrument_id: Instrument to assess
            pd_increase_significant: Whether PD has increased significantly
            is_30_days_past_due: 30 days past due (rebuttable presumption for Stage 2)
            is_90_days_past_due: 90 days past due (rebuttable presumption for Stage 3)
            is_credit_impaired: Whether instrument is credit-impaired
            loss_rate: Loss rate to apply for ECL calculation

        Returns:
            ECLStagingResult with staging outcome
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instrument_id)

        instrument = db.get(FinancialInstrument, inst_id)
        if not instrument or instrument.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Instrument not found")

        previous_stage = instrument.ecl_stage
        previous_loss_allowance = instrument.loss_allowance

        # Determine new stage
        new_stage = 1
        if is_credit_impaired or is_90_days_past_due:
            new_stage = 3
        elif pd_increase_significant or is_30_days_past_due:
            new_stage = 2

        # Calculate loss allowance
        exposure_at_default = instrument.carrying_amount + instrument.loss_allowance

        if new_stage == 1:
            # 12-month ECL
            loss_allowance = (exposure_at_default * loss_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            # Lifetime ECL (simplified - use higher rate for stages 2/3)
            lifetime_multiplier = Decimal("3") if new_stage == 2 else Decimal("5")
            loss_allowance = (exposure_at_default * loss_rate * lifetime_multiplier).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        ecl_movement = loss_allowance - previous_loss_allowance

        # Update instrument
        instrument.ecl_stage = new_stage
        instrument.loss_allowance = loss_allowance
        instrument.is_credit_impaired = is_credit_impaired or is_90_days_past_due

        # Update carrying amount for impairment
        if instrument.classification in [
            InstrumentClassification.AMORTIZED_COST,
            InstrumentClassification.FVOCI_DEBT,
        ]:
            instrument.carrying_amount = instrument.amortized_cost - loss_allowance

        db.commit()
        db.refresh(instrument)

        return ECLStagingResult(
            previous_stage=previous_stage,
            new_stage=new_stage,
            stage_changed=previous_stage != new_stage,
            loss_allowance=loss_allowance,
            ecl_movement=ecl_movement,
            is_credit_impaired=instrument.is_credit_impaired,
        )

    @staticmethod
    def record_principal_repayment(
        db: Session,
        organization_id: UUID,
        instrument_id: UUID,
        repayment_amount: Decimal,
        repayment_date: date,
    ) -> FinancialInstrument:
        """
        Record a principal repayment.

        Args:
            db: Database session
            organization_id: Organization scope
            instrument_id: Instrument receiving repayment
            repayment_amount: Amount repaid
            repayment_date: Date of repayment

        Returns:
            Updated FinancialInstrument
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instrument_id)

        instrument = db.get(FinancialInstrument, inst_id)
        if not instrument or instrument.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Instrument not found")

        if repayment_amount > instrument.current_principal:
            raise HTTPException(
                status_code=400,
                detail="Repayment amount exceeds current principal",
            )

        instrument.current_principal -= repayment_amount
        instrument.amortized_cost -= repayment_amount
        instrument.carrying_amount -= repayment_amount

        # Check if fully repaid
        if instrument.current_principal <= 0:
            instrument.status = InstrumentStatus.MATURED
            instrument.current_principal = Decimal("0")

        db.commit()
        db.refresh(instrument)

        return instrument

    @staticmethod
    def dispose_instrument(
        db: Session,
        organization_id: UUID,
        instrument_id: UUID,
        disposal_date: date,
        proceeds: Decimal,
        disposal_reason: str = "SOLD",
    ) -> tuple[FinancialInstrument, Decimal]:
        """
        Dispose of a financial instrument.

        Args:
            db: Database session
            organization_id: Organization scope
            instrument_id: Instrument to dispose
            disposal_date: Date of disposal
            proceeds: Sale proceeds
            disposal_reason: Reason for disposal

        Returns:
            Tuple of (instrument, gain_loss)
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instrument_id)

        instrument = db.get(FinancialInstrument, inst_id)
        if not instrument or instrument.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Instrument not found")

        if instrument.status != InstrumentStatus.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot dispose instrument with status '{instrument.status.value}'",
            )

        # Calculate gain/loss
        carrying_amount = instrument.carrying_amount
        gain_loss = proceeds - carrying_amount

        # For FVOCI debt, recycle accumulated OCI to P&L
        if instrument.classification == InstrumentClassification.FVOCI_DEBT:
            gain_loss += instrument.accumulated_oci

        # Update status
        if disposal_reason == "SOLD":
            instrument.status = InstrumentStatus.SOLD
        elif disposal_reason == "WRITTEN_OFF":
            instrument.status = InstrumentStatus.WRITTEN_OFF
        else:
            instrument.status = InstrumentStatus.SOLD

        instrument.carrying_amount = Decimal("0")
        instrument.current_principal = Decimal("0")

        db.commit()
        db.refresh(instrument)

        return (instrument, gain_loss)

    @staticmethod
    def write_off_instrument(
        db: Session,
        organization_id: UUID,
        instrument_id: UUID,
        write_off_date: date,
    ) -> FinancialInstrument:
        """
        Write off a credit-impaired instrument.

        Args:
            db: Database session
            organization_id: Organization scope
            instrument_id: Instrument to write off
            write_off_date: Date of write-off

        Returns:
            Updated FinancialInstrument
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instrument_id)

        instrument = db.get(FinancialInstrument, inst_id)
        if not instrument or instrument.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Instrument not found")

        if instrument.ecl_stage != 3:
            raise HTTPException(
                status_code=400,
                detail="Only Stage 3 (credit-impaired) instruments can be written off",
            )

        instrument.status = InstrumentStatus.WRITTEN_OFF
        instrument.carrying_amount = Decimal("0")
        instrument.current_principal = Decimal("0")
        instrument.amortized_cost = Decimal("0")

        db.commit()
        db.refresh(instrument)

        return instrument

    @staticmethod
    def get(
        db: Session,
        instrument_id: str,
    ) -> FinancialInstrument:
        """Get a financial instrument by ID."""
        instrument = db.get(FinancialInstrument, coerce_uuid(instrument_id))
        if not instrument:
            raise HTTPException(status_code=404, detail="Instrument not found")
        return instrument

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: str,
        instrument_code: str,
    ) -> Optional[FinancialInstrument]:
        """Get a financial instrument by code."""
        return (
            db.query(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == coerce_uuid(organization_id),
                FinancialInstrument.instrument_code == instrument_code,
            )
            .first()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        instrument_type: Optional[InstrumentType] = None,
        classification: Optional[InstrumentClassification] = None,
        status: Optional[InstrumentStatus] = None,
        ecl_stage: Optional[int] = None,
        is_asset: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FinancialInstrument]:
        """List financial instruments with optional filters."""
        query = db.query(FinancialInstrument)

        if organization_id:
            query = query.filter(
                FinancialInstrument.organization_id == coerce_uuid(organization_id)
            )

        if instrument_type:
            query = query.filter(FinancialInstrument.instrument_type == instrument_type)

        if classification:
            query = query.filter(FinancialInstrument.classification == classification)

        if status:
            query = query.filter(FinancialInstrument.status == status)

        if ecl_stage is not None:
            query = query.filter(FinancialInstrument.ecl_stage == ecl_stage)

        if is_asset is not None:
            query = query.filter(FinancialInstrument.is_asset == is_asset)

        query = query.order_by(FinancialInstrument.instrument_code)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def get_instruments_by_ecl_stage(
        db: Session,
        organization_id: str,
        ecl_stage: int,
    ) -> list[FinancialInstrument]:
        """Get all instruments in a specific ECL stage."""
        return (
            db.query(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == coerce_uuid(organization_id),
                FinancialInstrument.ecl_stage == ecl_stage,
                FinancialInstrument.status == InstrumentStatus.ACTIVE,
            )
            .all()
        )

    @staticmethod
    def get_maturing_instruments(
        db: Session,
        organization_id: str,
        as_of_date: date,
        days_ahead: int = 30,
    ) -> list[FinancialInstrument]:
        """Get instruments maturing within specified days."""
        from datetime import timedelta

        end_date = as_of_date + timedelta(days=days_ahead)

        return (
            db.query(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == coerce_uuid(organization_id),
                FinancialInstrument.status == InstrumentStatus.ACTIVE,
                FinancialInstrument.maturity_date.isnot(None),
                FinancialInstrument.maturity_date >= as_of_date,
                FinancialInstrument.maturity_date <= end_date,
            )
            .order_by(FinancialInstrument.maturity_date)
            .all()
        )


# Module-level singleton instance
financial_instrument_service = FinancialInstrumentService()
