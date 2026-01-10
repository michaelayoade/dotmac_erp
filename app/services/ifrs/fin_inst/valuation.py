"""
InstrumentValuationService - IFRS 9 period-end valuation.

Handles period-end valuations, fair value measurements, and P&L/OCI recognition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.fin_inst.financial_instrument import (
    FinancialInstrument,
    InstrumentClassification,
    InstrumentStatus,
)
from app.models.ifrs.fin_inst.instrument_valuation import InstrumentValuation
from app.models.ifrs.fin_inst.interest_accrual import InterestAccrual
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class ValuationInput:
    """Input for period-end valuation."""

    valuation_date: date
    fair_value_closing: Optional[Decimal] = None
    fair_value_level: Optional[int] = None
    valuation_technique: Optional[str] = None
    key_inputs: Optional[str] = None
    ecl_stage_closing: Optional[int] = None
    loss_allowance_closing: Optional[Decimal] = None
    exchange_rate: Decimal = Decimal("1.0")


@dataclass
class ValuationResult:
    """Result of period-end valuation."""

    valuation_id: UUID
    carrying_amount_closing: Decimal
    interest_income_pl: Decimal
    fv_change_pl: Decimal
    fv_change_oci: Decimal
    ecl_expense_pl: Decimal
    translation_difference: Decimal


class InstrumentValuationService(ListResponseMixin):
    """
    Service for IFRS 9 period-end valuations.

    Handles:
    - Period-end fair value measurements
    - P&L and OCI impact calculations
    - ECL movements
    - Foreign currency translation
    """

    @staticmethod
    def calculate_fv_impact(
        classification: InstrumentClassification,
        fv_change: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        Determine P&L vs OCI impact of fair value change.

        Args:
            classification: IFRS 9 classification
            fv_change: Fair value change amount

        Returns:
            Tuple of (fv_change_pl, fv_change_oci)
        """
        if classification in [
            InstrumentClassification.FVPL,
            InstrumentClassification.FVPL_LIABILITY,
        ]:
            # FVPL: All to P&L
            return (fv_change, Decimal("0"))
        elif classification in [
            InstrumentClassification.FVOCI_DEBT,
            InstrumentClassification.FVOCI_EQUITY,
        ]:
            # FVOCI: All to OCI
            return (Decimal("0"), fv_change)
        else:
            # Amortized cost: No FV changes
            return (Decimal("0"), Decimal("0"))

    @staticmethod
    def create_period_valuation(
        db: Session,
        organization_id: UUID,
        instrument_id: UUID,
        fiscal_period_id: UUID,
        input: ValuationInput,
    ) -> ValuationResult:
        """
        Create a period-end valuation for a financial instrument.

        Args:
            db: Database session
            organization_id: Organization scope
            instrument_id: Instrument to value
            fiscal_period_id: Fiscal period for valuation
            input: Valuation input data

        Returns:
            ValuationResult with P&L/OCI impacts
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instrument_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Load instrument
        instrument = db.get(FinancialInstrument, inst_id)
        if not instrument or instrument.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Instrument not found")

        # Check for existing valuation
        existing = (
            db.query(InstrumentValuation)
            .filter(
                InstrumentValuation.instrument_id == inst_id,
                InstrumentValuation.fiscal_period_id == period_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Valuation already exists for this period",
            )

        # Opening values
        amortized_cost_opening = instrument.amortized_cost
        fair_value_opening = instrument.fair_value
        loss_allowance_opening = instrument.loss_allowance
        ecl_stage_opening = instrument.ecl_stage

        # Get interest accrued for the period
        accrual = (
            db.query(InterestAccrual)
            .filter(
                InterestAccrual.instrument_id == inst_id,
                InterestAccrual.fiscal_period_id == period_id,
            )
            .first()
        )

        interest_accrued = accrual.effective_interest_income if accrual else Decimal("0")
        premium_discount_amortized = accrual.premium_discount_amortization if accrual else Decimal("0")

        # Calculate amortized cost closing
        amortized_cost_closing = amortized_cost_opening + premium_discount_amortized

        # Fair value change
        fair_value_closing = input.fair_value_closing or instrument.fair_value
        fv_change = Decimal("0")
        if fair_value_opening and fair_value_closing:
            fv_change = fair_value_closing - fair_value_opening

        # Determine P&L vs OCI for FV change
        fv_change_pl, fv_change_oci = InstrumentValuationService.calculate_fv_impact(
            instrument.classification, fv_change
        )

        # ECL movement
        loss_allowance_closing = input.loss_allowance_closing or instrument.loss_allowance
        ecl_movement = loss_allowance_closing - loss_allowance_opening
        ecl_expense_pl = ecl_movement  # ECL changes go to P&L

        ecl_stage_closing = input.ecl_stage_closing or instrument.ecl_stage

        # Calculate carrying amount closing
        if instrument.classification in [
            InstrumentClassification.FVPL,
            InstrumentClassification.FVOCI_DEBT,
            InstrumentClassification.FVOCI_EQUITY,
            InstrumentClassification.FVPL_LIABILITY,
        ]:
            carrying_amount_closing = fair_value_closing or amortized_cost_closing
        else:
            carrying_amount_closing = amortized_cost_closing - loss_allowance_closing

        # Functional currency translation
        functional_amount = (carrying_amount_closing * input.exchange_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        translation_difference = Decimal("0")  # Would need prior period rate to calculate

        # Interest income for P&L
        interest_income_pl = interest_accrued

        # Create valuation record
        valuation = InstrumentValuation(
            instrument_id=inst_id,
            fiscal_period_id=period_id,
            valuation_date=input.valuation_date,
            amortized_cost_opening=amortized_cost_opening,
            fair_value_opening=fair_value_opening,
            loss_allowance_opening=loss_allowance_opening,
            ecl_stage_opening=ecl_stage_opening,
            interest_accrued=interest_accrued,
            premium_discount_amortized=premium_discount_amortized,
            principal_repayments=Decimal("0"),
            ecl_movement=ecl_movement,
            fair_value_closing=fair_value_closing,
            fair_value_change=fv_change,
            fair_value_level=input.fair_value_level,
            valuation_technique=input.valuation_technique,
            key_inputs=input.key_inputs,
            amortized_cost_closing=amortized_cost_closing,
            loss_allowance_closing=loss_allowance_closing,
            carrying_amount_closing=carrying_amount_closing,
            ecl_stage_closing=ecl_stage_closing,
            interest_income_pl=interest_income_pl,
            fv_change_pl=fv_change_pl,
            fv_change_oci=fv_change_oci,
            ecl_expense_pl=ecl_expense_pl,
            exchange_rate=input.exchange_rate,
            functional_currency_amount=functional_amount,
            translation_difference=translation_difference,
        )

        db.add(valuation)

        # Update instrument with closing values
        instrument.amortized_cost = amortized_cost_closing
        instrument.fair_value = fair_value_closing
        instrument.carrying_amount = carrying_amount_closing
        instrument.loss_allowance = loss_allowance_closing
        instrument.ecl_stage = ecl_stage_closing

        if fv_change_oci != 0:
            instrument.accumulated_oci += fv_change_oci

        db.commit()
        db.refresh(valuation)

        return ValuationResult(
            valuation_id=valuation.valuation_id,
            carrying_amount_closing=carrying_amount_closing,
            interest_income_pl=interest_income_pl,
            fv_change_pl=fv_change_pl,
            fv_change_oci=fv_change_oci,
            ecl_expense_pl=ecl_expense_pl,
            translation_difference=translation_difference,
        )

    @staticmethod
    def run_period_valuation(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        valuation_date: date,
        fair_values: Optional[dict[UUID, Decimal]] = None,
        exchange_rate: Decimal = Decimal("1.0"),
    ) -> list[ValuationResult]:
        """
        Run period-end valuation for all active instruments.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period for valuation
            valuation_date: Date of valuation
            fair_values: Dict of instrument_id -> fair_value (optional)
            exchange_rate: Exchange rate to functional currency

        Returns:
            List of ValuationResult for each instrument
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Get all active instruments
        instruments = (
            db.query(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == org_id,
                FinancialInstrument.status == InstrumentStatus.ACTIVE,
            )
            .all()
        )

        fair_values = fair_values or {}
        results = []

        for instrument in instruments:
            # Check if already valued
            existing = (
                db.query(InstrumentValuation)
                .filter(
                    InstrumentValuation.instrument_id == instrument.instrument_id,
                    InstrumentValuation.fiscal_period_id == period_id,
                )
                .first()
            )
            if existing:
                continue

            input = ValuationInput(
                valuation_date=valuation_date,
                fair_value_closing=fair_values.get(instrument.instrument_id),
                exchange_rate=exchange_rate,
            )

            result = InstrumentValuationService.create_period_valuation(
                db=db,
                organization_id=org_id,
                instrument_id=instrument.instrument_id,
                fiscal_period_id=period_id,
                input=input,
            )
            results.append(result)

        return results

    @staticmethod
    def get_valuation(
        db: Session,
        valuation_id: str,
    ) -> InstrumentValuation:
        """Get a valuation by ID."""
        valuation = db.get(InstrumentValuation, coerce_uuid(valuation_id))
        if not valuation:
            raise HTTPException(status_code=404, detail="Valuation not found")
        return valuation

    @staticmethod
    def list_valuations_for_instrument(
        db: Session,
        instrument_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InstrumentValuation]:
        """List all valuations for an instrument."""
        return (
            db.query(InstrumentValuation)
            .filter(InstrumentValuation.instrument_id == coerce_uuid(instrument_id))
            .order_by(InstrumentValuation.valuation_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    @staticmethod
    def list_valuations_for_period(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
    ) -> list[InstrumentValuation]:
        """List all valuations for a fiscal period."""
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        return (
            db.query(InstrumentValuation)
            .join(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == org_id,
                InstrumentValuation.fiscal_period_id == period_id,
            )
            .all()
        )

    @staticmethod
    def get_period_summary(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
    ) -> dict:
        """
        Get summary of P&L and OCI impacts for a period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period

        Returns:
            Dict with aggregated P&L and OCI impacts
        """
        from sqlalchemy import func

        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        result = (
            db.query(
                func.sum(InstrumentValuation.interest_income_pl).label("interest_income"),
                func.sum(InstrumentValuation.fv_change_pl).label("fv_change_pl"),
                func.sum(InstrumentValuation.fv_change_oci).label("fv_change_oci"),
                func.sum(InstrumentValuation.ecl_expense_pl).label("ecl_expense"),
                func.sum(InstrumentValuation.translation_difference).label("translation_diff"),
            )
            .join(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == org_id,
                InstrumentValuation.fiscal_period_id == period_id,
            )
            .first()
        )

        return {
            "interest_income": result.interest_income or Decimal("0"),
            "fv_change_pl": result.fv_change_pl or Decimal("0"),
            "fv_change_oci": result.fv_change_oci or Decimal("0"),
            "ecl_expense": result.ecl_expense or Decimal("0"),
            "translation_difference": result.translation_diff or Decimal("0"),
            "total_pl_impact": (
                (result.interest_income or Decimal("0"))
                + (result.fv_change_pl or Decimal("0"))
                - (result.ecl_expense or Decimal("0"))
            ),
            "total_oci_impact": result.fv_change_oci or Decimal("0"),
        }

    @staticmethod
    def get_fair_value_hierarchy(
        db: Session,
        organization_id: str,
        valuation_date: date,
    ) -> dict:
        """
        Get fair value hierarchy breakdown (Level 1, 2, 3).

        Args:
            db: Database session
            organization_id: Organization scope
            valuation_date: Valuation date

        Returns:
            Dict with FV by level
        """
        from sqlalchemy import func

        org_id = coerce_uuid(organization_id)

        # Get latest valuations for each instrument
        subquery = (
            db.query(
                InstrumentValuation.instrument_id,
                func.max(InstrumentValuation.valuation_date).label("max_date"),
            )
            .join(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == org_id,
                InstrumentValuation.valuation_date <= valuation_date,
            )
            .group_by(InstrumentValuation.instrument_id)
            .subquery()
        )

        results = (
            db.query(
                InstrumentValuation.fair_value_level,
                func.sum(InstrumentValuation.fair_value_closing).label("total_fv"),
                func.count(InstrumentValuation.instrument_id).label("count"),
            )
            .join(
                subquery,
                (InstrumentValuation.instrument_id == subquery.c.instrument_id)
                & (InstrumentValuation.valuation_date == subquery.c.max_date),
            )
            .filter(InstrumentValuation.fair_value_level.isnot(None))
            .group_by(InstrumentValuation.fair_value_level)
            .all()
        )

        hierarchy = {
            "level_1": {"total_fair_value": Decimal("0"), "count": 0},
            "level_2": {"total_fair_value": Decimal("0"), "count": 0},
            "level_3": {"total_fair_value": Decimal("0"), "count": 0},
        }

        for row in results:
            level_key = f"level_{row.fair_value_level}"
            if level_key in hierarchy:
                hierarchy[level_key] = {
                    "total_fair_value": row.total_fv or Decimal("0"),
                    "count": row.count,
                }

        return hierarchy


# Module-level singleton instance
instrument_valuation_service = InstrumentValuationService()
