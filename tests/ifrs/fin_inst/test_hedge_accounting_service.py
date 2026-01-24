"""
Tests for HedgeAccountingService.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.finance.fin_inst.hedge_relationship import HedgeType, HedgeStatus
from tests.ifrs.fin_inst.conftest import (
    MockFinancialInstrument,
    MockHedgeRelationship,
    MockHedgeEffectiveness,
)


class TestHedgeAccountingService:
    """Tests for HedgeAccountingService."""

    def test_calculate_effectiveness_ratio_zero_hedged_change(self):
        """Test effectiveness ratio with zero hedged item change."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        result = HedgeAccountingService.calculate_effectiveness_ratio(
            hedging_instrument_change=Decimal("1000.00"),
            hedged_item_change=Decimal("0"),
        )

        assert result == Decimal("0")

    def test_calculate_effectiveness_ratio_perfect_hedge(self):
        """Test effectiveness ratio for perfect hedge."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        result = HedgeAccountingService.calculate_effectiveness_ratio(
            hedging_instrument_change=Decimal("1000.00"),
            hedged_item_change=Decimal("-1000.00"),
        )

        assert result == Decimal("1.0000")

    def test_calculate_effectiveness_ratio_highly_effective(self):
        """Test effectiveness ratio within 80-125% range."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        result = HedgeAccountingService.calculate_effectiveness_ratio(
            hedging_instrument_change=Decimal("950.00"),
            hedged_item_change=Decimal("-1000.00"),
        )

        assert result == Decimal("0.9500")

    def test_is_highly_effective_true(self):
        """Test is_highly_effective returns true for ratio in range."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        assert HedgeAccountingService.is_highly_effective(Decimal("0.80")) is True
        assert HedgeAccountingService.is_highly_effective(Decimal("1.00")) is True
        assert HedgeAccountingService.is_highly_effective(Decimal("1.25")) is True

    def test_is_highly_effective_false(self):
        """Test is_highly_effective returns false for ratio outside range."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        assert HedgeAccountingService.is_highly_effective(Decimal("0.75")) is False
        assert HedgeAccountingService.is_highly_effective(Decimal("1.30")) is False

    def test_calculate_ineffectiveness_fair_value_hedge(self):
        """Test ineffectiveness calculation for fair value hedge."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        ineffectiveness, effective = HedgeAccountingService.calculate_ineffectiveness(
            hedge_type=HedgeType.FAIR_VALUE,
            hedging_instrument_change=Decimal("1000.00"),
            hedged_item_change=Decimal("-980.00"),
        )

        # Net = 1000 + (-980) = 20 ineffectiveness
        assert ineffectiveness == Decimal("20.00")
        # Effective portion = offset amount
        assert effective == Decimal("980.00")

    def test_calculate_ineffectiveness_cash_flow_hedge(self):
        """Test ineffectiveness calculation for cash flow hedge."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        ineffectiveness, effective = HedgeAccountingService.calculate_ineffectiveness(
            hedge_type=HedgeType.CASH_FLOW,
            hedging_instrument_change=Decimal("1000.00"),
            hedged_item_change=Decimal("-980.00"),
        )

        # Effective = min(abs values) = 980
        assert effective == Decimal("980")
        # Ineffectiveness = instrument change - effective = 1000 - 980 = 20
        assert ineffectiveness == Decimal("20")

    def test_designate_hedge_instrument_not_found(self, mock_db, org_id, user_id):
        """Test hedge designation fails when instrument not found."""
        from app.services.finance.fin_inst.hedge_accounting import (
            HedgeAccountingService,
            HedgeDesignationInput,
        )
        from fastapi import HTTPException

        mock_db.get.return_value = None

        input_data = HedgeDesignationInput(
            hedge_code="HEDGE-001",
            hedge_name="FX Hedge",
            hedge_type=HedgeType.CASH_FLOW,
            hedging_instrument_id=uuid.uuid4(),
            hedged_item_type="FORECAST",
            hedged_item_description="Forecasted revenue",
            hedged_risk="FX_RISK",
            designation_date=date.today(),
            effective_date=date.today(),
            prospective_test_method="CRITICAL_TERMS",
            retrospective_test_method="DOLLAR_OFFSET",
        )

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.designate_hedge(
                mock_db, org_id, input_data, user_id
            )

        assert exc_info.value.status_code == 404
        assert "instrument" in exc_info.value.detail.lower()

    def test_designate_hedge_duplicate_code(
        self, mock_db, org_id, user_id, mock_instrument
    ):
        """Test hedge designation fails with duplicate code."""
        from app.services.finance.fin_inst.hedge_accounting import (
            HedgeAccountingService,
            HedgeDesignationInput,
        )
        from fastapi import HTTPException

        existing_hedge = MockHedgeRelationship(organization_id=org_id)
        mock_db.get.return_value = mock_instrument
        mock_db.query.return_value.filter.return_value.first.return_value = existing_hedge

        input_data = HedgeDesignationInput(
            hedge_code="HEDGE-001",
            hedge_name="FX Hedge",
            hedge_type=HedgeType.CASH_FLOW,
            hedging_instrument_id=mock_instrument.instrument_id,
            hedged_item_type="FORECAST",
            hedged_item_description="Forecasted revenue",
            hedged_risk="FX_RISK",
            designation_date=date.today(),
            effective_date=date.today(),
            prospective_test_method="CRITICAL_TERMS",
            retrospective_test_method="DOLLAR_OFFSET",
        )

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.designate_hedge(
                mock_db, org_id, input_data, user_id
            )

        assert exc_info.value.status_code == 400
        assert "already exists" in exc_info.value.detail

    def test_designate_hedge_success(self, mock_db, org_id, user_id, mock_instrument):
        """Test successful hedge designation."""
        from app.services.finance.fin_inst.hedge_accounting import (
            HedgeAccountingService,
            HedgeDesignationInput,
        )

        mock_db.get.return_value = mock_instrument
        mock_db.query.return_value.filter.return_value.first.return_value = None

        input_data = HedgeDesignationInput(
            hedge_code="HEDGE-001",
            hedge_name="FX Hedge",
            hedge_type=HedgeType.CASH_FLOW,
            hedging_instrument_id=mock_instrument.instrument_id,
            hedged_item_type="FORECAST",
            hedged_item_description="Forecasted revenue",
            hedged_risk="FX_RISK",
            designation_date=date.today(),
            effective_date=date.today(),
            prospective_test_method="CRITICAL_TERMS",
            retrospective_test_method="DOLLAR_OFFSET",
        )

        result = HedgeAccountingService.designate_hedge(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_approve_hedge_not_found(self, mock_db, org_id, approver_id):
        """Test hedge approval fails when not found."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.approve_hedge(
                mock_db,
                org_id,
                uuid.uuid4(),
                approver_id,
            )

        assert exc_info.value.status_code == 404

    def test_approve_hedge_wrong_status(self, mock_db, org_id, mock_hedge, approver_id):
        """Test hedge approval fails for non-designated hedge."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService
        from fastapi import HTTPException

        mock_hedge.status = HedgeStatus.ACTIVE  # Already active
        mock_db.get.return_value = mock_hedge

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.approve_hedge(
                mock_db,
                org_id,
                mock_hedge.hedge_id,
                approver_id,
            )

        assert exc_info.value.status_code == 400

    def test_approve_hedge_sod_violation(self, mock_db, org_id, mock_hedge, user_id):
        """Test segregation of duties violation on approval."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService
        from fastapi import HTTPException

        mock_hedge.status = HedgeStatus.DESIGNATED
        mock_hedge.created_by_user_id = user_id
        mock_db.get.return_value = mock_hedge

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.approve_hedge(
                mock_db,
                org_id,
                mock_hedge.hedge_id,
                user_id,  # Same as creator
            )

        assert exc_info.value.status_code == 400
        assert "Segregation of duties" in exc_info.value.detail

    def test_approve_hedge_success(self, mock_db, org_id, mock_hedge, approver_id):
        """Test successful hedge approval."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        mock_hedge.status = HedgeStatus.DESIGNATED
        mock_db.get.return_value = mock_hedge

        result = HedgeAccountingService.approve_hedge(
            mock_db,
            org_id,
            mock_hedge.hedge_id,
            approver_id,
        )

        assert result.status == HedgeStatus.ACTIVE
        assert result.approved_by_user_id == approver_id

    def test_perform_effectiveness_test_not_found(self, mock_db, org_id, user_id):
        """Test effectiveness test fails when hedge not found."""
        from app.services.finance.fin_inst.hedge_accounting import (
            HedgeAccountingService,
            EffectivenessTestInput,
        )
        from fastapi import HTTPException

        mock_db.get.return_value = None

        input_data = EffectivenessTestInput(
            test_date=date.today(),
            hedging_instrument_fv_change=Decimal("1000.00"),
            hedged_item_fv_change=Decimal("-980.00"),
        )

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.perform_effectiveness_test(
                mock_db,
                org_id,
                uuid.uuid4(),
                uuid.uuid4(),
                input_data,
                user_id,
            )

        assert exc_info.value.status_code == 404

    def test_perform_effectiveness_test_wrong_status(
        self, mock_db, org_id, user_id, mock_hedge
    ):
        """Test effectiveness test fails for discontinued hedge."""
        from app.services.finance.fin_inst.hedge_accounting import (
            HedgeAccountingService,
            EffectivenessTestInput,
        )
        from fastapi import HTTPException

        mock_hedge.status = HedgeStatus.DISCONTINUED
        mock_db.get.return_value = mock_hedge

        input_data = EffectivenessTestInput(
            test_date=date.today(),
            hedging_instrument_fv_change=Decimal("1000.00"),
            hedged_item_fv_change=Decimal("-980.00"),
        )

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.perform_effectiveness_test(
                mock_db,
                org_id,
                mock_hedge.hedge_id,
                uuid.uuid4(),
                input_data,
                user_id,
            )

        assert exc_info.value.status_code == 400

    def test_discontinue_hedge_not_found(self, mock_db, org_id):
        """Test discontinuation fails when hedge not found."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.discontinue_hedge(
                mock_db,
                org_id,
                uuid.uuid4(),
                date.today(),
            )

        assert exc_info.value.status_code == 404

    def test_discontinue_hedge_wrong_status(self, mock_db, org_id, mock_hedge):
        """Test discontinuation fails for already discontinued hedge."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService
        from fastapi import HTTPException

        mock_hedge.status = HedgeStatus.DISCONTINUED
        mock_db.get.return_value = mock_hedge

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.discontinue_hedge(
                mock_db,
                org_id,
                mock_hedge.hedge_id,
                date.today(),
            )

        assert exc_info.value.status_code == 400

    def test_discontinue_hedge_success(self, mock_db, org_id, mock_hedge):
        """Test successful hedge discontinuation."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        mock_hedge.status = HedgeStatus.ACTIVE
        mock_db.get.return_value = mock_hedge

        result = HedgeAccountingService.discontinue_hedge(
            mock_db,
            org_id,
            mock_hedge.hedge_id,
            date(2024, 6, 30),
        )

        assert result.status == HedgeStatus.DISCONTINUED
        assert result.termination_date == date(2024, 6, 30)

    def test_reclassify_to_pl_not_found(self, mock_db, org_id, user_id):
        """Test OCI reclassification fails when hedge not found."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.reclassify_to_pl(
                mock_db,
                org_id,
                uuid.uuid4(),
                uuid.uuid4(),
                Decimal("1000.00"),
                user_id,
            )

        assert exc_info.value.status_code == 404

    def test_reclassify_to_pl_not_cash_flow(self, mock_db, org_id, user_id, mock_hedge):
        """Test OCI reclassification fails for non-cash flow hedge."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService
        from fastapi import HTTPException

        mock_hedge.hedge_type = HedgeType.FAIR_VALUE
        mock_db.get.return_value = mock_hedge

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.reclassify_to_pl(
                mock_db,
                org_id,
                mock_hedge.hedge_id,
                uuid.uuid4(),
                Decimal("1000.00"),
                user_id,
            )

        assert exc_info.value.status_code == 400
        assert "cash flow" in exc_info.value.detail.lower()

    def test_get_hedge_success(self, mock_db, mock_hedge):
        """Test getting a hedge by ID."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        mock_db.get.return_value = mock_hedge

        result = HedgeAccountingService.get(mock_db, str(mock_hedge.hedge_id))

        assert result is not None
        assert result.hedge_id == mock_hedge.hedge_id

    def test_get_hedge_not_found(self, mock_db):
        """Test getting non-existent hedge raises HTTPException."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            HedgeAccountingService.get(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_hedges(self, mock_db, org_id):
        """Test listing hedges."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        mock_hedges = [MockHedgeRelationship(organization_id=org_id) for _ in range(5)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_hedges

        result = HedgeAccountingService.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_hedges_with_type_filter(self, mock_db, org_id):
        """Test listing hedges with type filter."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        mock_hedges = [
            MockHedgeRelationship(
                organization_id=org_id,
                hedge_type=HedgeType.CASH_FLOW,
            )
        ]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_hedges

        result = HedgeAccountingService.list(
            mock_db,
            str(org_id),
            hedge_type=HedgeType.CASH_FLOW,
        )

        assert len(result) == 1

    def test_list_effectiveness_tests(self, mock_db, mock_hedge):
        """Test listing effectiveness tests for a hedge."""
        from app.services.finance.fin_inst.hedge_accounting import HedgeAccountingService

        mock_tests = [
            MockHedgeEffectiveness(hedge_id=mock_hedge.hedge_id) for _ in range(3)
        ]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_tests

        result = HedgeAccountingService.list_effectiveness_tests(
            mock_db, str(mock_hedge.hedge_id)
        )

        assert len(result) == 3
