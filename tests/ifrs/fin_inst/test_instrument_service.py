"""
Tests for FinancialInstrumentService.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.ifrs.fin_inst.financial_instrument import (
    InstrumentType,
    InstrumentClassification,
    InstrumentStatus,
)
from tests.ifrs.fin_inst.conftest import MockFinancialInstrument


class TestFinancialInstrumentService:
    """Tests for FinancialInstrumentService."""

    def test_calculate_premium_discount_premium(self):
        """Test premium calculation when paying above face value."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        result = FinancialInstrumentService.calculate_premium_discount(
            face_value=Decimal("100000.00"),
            acquisition_cost=Decimal("102000.00"),  # Paid more
            transaction_costs=Decimal("500.00"),
        )

        # Premium = face - (cost + trans) = 100000 - 102500 = -2500
        assert result == Decimal("-2500.00")

    def test_calculate_premium_discount_discount(self):
        """Test discount calculation when paying below face value."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        result = FinancialInstrumentService.calculate_premium_discount(
            face_value=Decimal("100000.00"),
            acquisition_cost=Decimal("98000.00"),  # Paid less
            transaction_costs=Decimal("500.00"),
        )

        # Discount = face - (cost + trans) = 100000 - 98500 = 1500
        assert result == Decimal("1500.00")

    def test_calculate_premium_discount_at_par(self):
        """Test when buying at par value."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        result = FinancialInstrumentService.calculate_premium_discount(
            face_value=Decimal("100000.00"),
            acquisition_cost=Decimal("100000.00"),
            transaction_costs=Decimal("0"),
        )

        assert result == Decimal("0")

    def test_determine_initial_carrying_amount_fvpl(self):
        """Test initial carrying for FVPL uses fair value."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        result = FinancialInstrumentService.determine_initial_carrying_amount(
            classification=InstrumentClassification.FVPL,
            acquisition_cost=Decimal("100000.00"),
            transaction_costs=Decimal("500.00"),
            fair_value=Decimal("100000.00"),
        )

        # FVPL: transaction costs expensed, use fair value
        assert result == Decimal("100000.00")

    def test_determine_initial_carrying_amount_amortized_cost(self):
        """Test initial carrying for amortized cost includes transaction costs."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        result = FinancialInstrumentService.determine_initial_carrying_amount(
            classification=InstrumentClassification.AMORTIZED_COST,
            acquisition_cost=Decimal("98000.00"),
            transaction_costs=Decimal("500.00"),
        )

        # Amortized cost: includes transaction costs
        assert result == Decimal("98500.00")

    def test_determine_initial_carrying_amount_fvoci_debt(self):
        """Test initial carrying for FVOCI debt includes transaction costs."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        result = FinancialInstrumentService.determine_initial_carrying_amount(
            classification=InstrumentClassification.FVOCI_DEBT,
            acquisition_cost=Decimal("98000.00"),
            transaction_costs=Decimal("500.00"),
        )

        # FVOCI debt: includes transaction costs
        assert result == Decimal("98500.00")

    def test_create_instrument_duplicate(self, mock_db, org_id, user_id):
        """Test creating duplicate instrument code fails."""
        from app.services.ifrs.fin_inst.instrument import (
            FinancialInstrumentService,
            InstrumentInput,
        )
        from fastapi import HTTPException

        existing = MockFinancialInstrument(organization_id=org_id)
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        input_data = InstrumentInput(
            instrument_code="BOND-001",
            instrument_name="Corporate Bond",
            instrument_type=InstrumentType.DEBT_SECURITY,
            classification=InstrumentClassification.AMORTIZED_COST,
            counterparty_type="CORPORATE",
            counterparty_name="ABC Corp",
            currency_code="USD",
            face_value=Decimal("100000.00"),
            trade_date=date.today(),
            settlement_date=date.today(),
            acquisition_cost=Decimal("98000.00"),
            instrument_account_id=uuid.uuid4(),
        )

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.create_instrument(
                mock_db, org_id, input_data, user_id
            )

        assert exc_info.value.status_code == 400
        assert "already exists" in exc_info.value.detail

    def test_create_instrument_success(self, mock_db, org_id, user_id):
        """Test successful instrument creation."""
        from app.services.ifrs.fin_inst.instrument import (
            FinancialInstrumentService,
            InstrumentInput,
        )

        mock_db.query.return_value.filter.return_value.first.return_value = None

        input_data = InstrumentInput(
            instrument_code="BOND-001",
            instrument_name="Corporate Bond",
            instrument_type=InstrumentType.DEBT_SECURITY,
            classification=InstrumentClassification.AMORTIZED_COST,
            counterparty_type="CORPORATE",
            counterparty_name="ABC Corp",
            currency_code="USD",
            face_value=Decimal("100000.00"),
            trade_date=date.today(),
            settlement_date=date.today(),
            acquisition_cost=Decimal("98000.00"),
            instrument_account_id=uuid.uuid4(),
        )

        result = FinancialInstrumentService.create_instrument(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_update_fair_value_not_found(self, mock_db, org_id):
        """Test fair value update fails when instrument not found."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.update_fair_value(
                mock_db,
                org_id,
                uuid.uuid4(),
                Decimal("99000.00"),
                date.today(),
            )

        assert exc_info.value.status_code == 404

    def test_update_fair_value_fvpl(self, mock_db, org_id, mock_fvpl_instrument):
        """Test fair value update for FVPL instrument goes to P&L."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_fvpl_instrument.fair_value = Decimal("98000.00")
        mock_fvpl_instrument.carrying_amount = Decimal("98000.00")
        mock_db.get.return_value = mock_fvpl_instrument

        instrument, fv_change_pl, fv_change_oci = FinancialInstrumentService.update_fair_value(
            mock_db,
            org_id,
            mock_fvpl_instrument.instrument_id,
            Decimal("99000.00"),
            date.today(),
        )

        assert fv_change_pl == Decimal("1000.00")
        assert fv_change_oci == Decimal("0")

    def test_update_fair_value_fvoci_debt(self, mock_db, org_id, mock_fvoci_instrument):
        """Test fair value update for FVOCI debt goes to OCI."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_fvoci_instrument.fair_value = Decimal("98000.00")
        mock_fvoci_instrument.carrying_amount = Decimal("98000.00")
        mock_fvoci_instrument.accumulated_oci = Decimal("0")
        mock_db.get.return_value = mock_fvoci_instrument

        instrument, fv_change_pl, fv_change_oci = FinancialInstrumentService.update_fair_value(
            mock_db,
            org_id,
            mock_fvoci_instrument.instrument_id,
            Decimal("99000.00"),
            date.today(),
        )

        assert fv_change_pl == Decimal("0")
        assert fv_change_oci == Decimal("1000.00")

    def test_assess_ecl_staging_not_found(self, mock_db, org_id):
        """Test ECL staging fails when instrument not found."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.assess_ecl_staging(
                mock_db,
                org_id,
                uuid.uuid4(),
            )

        assert exc_info.value.status_code == 404

    def test_assess_ecl_staging_stage_1(self, mock_db, org_id, mock_instrument):
        """Test ECL staging stays at Stage 1 with no impairment indicators."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instrument.ecl_stage = 1
        mock_instrument.loss_allowance = Decimal("0")
        mock_instrument.carrying_amount = Decimal("98500.00")
        mock_db.get.return_value = mock_instrument

        result = FinancialInstrumentService.assess_ecl_staging(
            mock_db,
            org_id,
            mock_instrument.instrument_id,
            loss_rate=Decimal("0.01"),
        )

        assert result.new_stage == 1
        assert not result.stage_changed

    def test_assess_ecl_staging_move_to_stage_2(self, mock_db, org_id, mock_instrument):
        """Test ECL staging moves to Stage 2 with significant increase in credit risk."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instrument.ecl_stage = 1
        mock_instrument.loss_allowance = Decimal("0")
        mock_instrument.carrying_amount = Decimal("98500.00")
        mock_db.get.return_value = mock_instrument

        result = FinancialInstrumentService.assess_ecl_staging(
            mock_db,
            org_id,
            mock_instrument.instrument_id,
            pd_increase_significant=True,
            loss_rate=Decimal("0.01"),
        )

        assert result.new_stage == 2
        assert result.stage_changed

    def test_assess_ecl_staging_30_days_past_due(self, mock_db, org_id, mock_instrument):
        """Test ECL staging moves to Stage 2 when 30 days past due."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instrument.ecl_stage = 1
        mock_instrument.loss_allowance = Decimal("0")
        mock_instrument.carrying_amount = Decimal("98500.00")
        mock_db.get.return_value = mock_instrument

        result = FinancialInstrumentService.assess_ecl_staging(
            mock_db,
            org_id,
            mock_instrument.instrument_id,
            is_30_days_past_due=True,
            loss_rate=Decimal("0.01"),
        )

        assert result.new_stage == 2

    def test_assess_ecl_staging_move_to_stage_3(self, mock_db, org_id, mock_instrument):
        """Test ECL staging moves to Stage 3 when credit impaired."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instrument.ecl_stage = 1
        mock_instrument.loss_allowance = Decimal("0")
        mock_instrument.carrying_amount = Decimal("98500.00")
        mock_db.get.return_value = mock_instrument

        result = FinancialInstrumentService.assess_ecl_staging(
            mock_db,
            org_id,
            mock_instrument.instrument_id,
            is_credit_impaired=True,
            loss_rate=Decimal("0.01"),
        )

        assert result.new_stage == 3
        assert result.is_credit_impaired

    def test_assess_ecl_staging_90_days_past_due(self, mock_db, org_id, mock_instrument):
        """Test ECL staging moves to Stage 3 when 90 days past due."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instrument.ecl_stage = 1
        mock_instrument.loss_allowance = Decimal("0")
        mock_instrument.carrying_amount = Decimal("98500.00")
        mock_db.get.return_value = mock_instrument

        result = FinancialInstrumentService.assess_ecl_staging(
            mock_db,
            org_id,
            mock_instrument.instrument_id,
            is_90_days_past_due=True,
            loss_rate=Decimal("0.02"),
        )

        assert result.new_stage == 3

    def test_record_principal_repayment_not_found(self, mock_db, org_id):
        """Test principal repayment fails when instrument not found."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.record_principal_repayment(
                mock_db,
                org_id,
                uuid.uuid4(),
                Decimal("10000.00"),
                date.today(),
            )

        assert exc_info.value.status_code == 404

    def test_record_principal_repayment_exceeds_principal(
        self, mock_db, org_id, mock_instrument
    ):
        """Test repayment exceeding principal fails."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService
        from fastapi import HTTPException

        mock_instrument.current_principal = Decimal("10000.00")
        mock_db.get.return_value = mock_instrument

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.record_principal_repayment(
                mock_db,
                org_id,
                mock_instrument.instrument_id,
                Decimal("20000.00"),  # More than principal
                date.today(),
            )

        assert exc_info.value.status_code == 400
        assert "exceeds" in exc_info.value.detail

    def test_record_principal_repayment_success(self, mock_db, org_id, mock_instrument):
        """Test successful principal repayment."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instrument.current_principal = Decimal("100000.00")
        mock_instrument.amortized_cost = Decimal("98500.00")
        mock_instrument.carrying_amount = Decimal("98500.00")
        mock_db.get.return_value = mock_instrument

        result = FinancialInstrumentService.record_principal_repayment(
            mock_db,
            org_id,
            mock_instrument.instrument_id,
            Decimal("10000.00"),
            date.today(),
        )

        assert result.current_principal == Decimal("90000.00")

    def test_record_principal_repayment_full_payoff(self, mock_db, org_id, mock_instrument):
        """Test full principal repayment sets status to MATURED."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instrument.current_principal = Decimal("10000.00")
        mock_instrument.amortized_cost = Decimal("10000.00")
        mock_instrument.carrying_amount = Decimal("10000.00")
        mock_db.get.return_value = mock_instrument

        result = FinancialInstrumentService.record_principal_repayment(
            mock_db,
            org_id,
            mock_instrument.instrument_id,
            Decimal("10000.00"),  # Full payoff
            date.today(),
        )

        assert result.status == InstrumentStatus.MATURED
        assert result.current_principal == Decimal("0")

    def test_dispose_instrument_not_found(self, mock_db, org_id):
        """Test disposal fails when instrument not found."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.dispose_instrument(
                mock_db,
                org_id,
                uuid.uuid4(),
                date.today(),
                Decimal("100000.00"),
            )

        assert exc_info.value.status_code == 404

    def test_dispose_instrument_wrong_status(self, mock_db, org_id, mock_instrument):
        """Test disposal fails for non-active instrument."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService
        from fastapi import HTTPException

        mock_instrument.status = InstrumentStatus.MATURED
        mock_db.get.return_value = mock_instrument

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.dispose_instrument(
                mock_db,
                org_id,
                mock_instrument.instrument_id,
                date.today(),
                Decimal("100000.00"),
            )

        assert exc_info.value.status_code == 400

    def test_dispose_instrument_success(self, mock_db, org_id, mock_instrument):
        """Test successful instrument disposal with gain."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instrument.carrying_amount = Decimal("98500.00")
        mock_instrument.accumulated_oci = Decimal("0")
        mock_db.get.return_value = mock_instrument

        instrument, gain_loss = FinancialInstrumentService.dispose_instrument(
            mock_db,
            org_id,
            mock_instrument.instrument_id,
            date.today(),
            Decimal("100000.00"),  # Proceeds
        )

        assert gain_loss == Decimal("1500.00")  # Gain
        assert instrument.status == InstrumentStatus.SOLD

    def test_dispose_fvoci_debt_recycles_oci(self, mock_db, org_id, mock_fvoci_instrument):
        """Test FVOCI debt disposal recycles OCI to P&L."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_fvoci_instrument.carrying_amount = Decimal("99000.00")
        mock_fvoci_instrument.accumulated_oci = Decimal("500.00")  # Gain in OCI
        mock_db.get.return_value = mock_fvoci_instrument

        instrument, gain_loss = FinancialInstrumentService.dispose_instrument(
            mock_db,
            org_id,
            mock_fvoci_instrument.instrument_id,
            date.today(),
            Decimal("99000.00"),  # Proceeds = carrying
        )

        # Gain includes recycled OCI
        assert gain_loss == Decimal("500.00")

    def test_write_off_instrument_not_found(self, mock_db, org_id):
        """Test write-off fails when instrument not found."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.write_off_instrument(
                mock_db,
                org_id,
                uuid.uuid4(),
                date.today(),
            )

        assert exc_info.value.status_code == 404

    def test_write_off_instrument_not_stage_3(self, mock_db, org_id, mock_instrument):
        """Test write-off fails for non-Stage 3 instrument."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService
        from fastapi import HTTPException

        mock_instrument.ecl_stage = 1  # Not Stage 3
        mock_db.get.return_value = mock_instrument

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.write_off_instrument(
                mock_db,
                org_id,
                mock_instrument.instrument_id,
                date.today(),
            )

        assert exc_info.value.status_code == 400
        assert "Stage 3" in exc_info.value.detail

    def test_write_off_instrument_success(self, mock_db, org_id, mock_instrument):
        """Test successful instrument write-off."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instrument.ecl_stage = 3  # Credit impaired
        mock_db.get.return_value = mock_instrument

        result = FinancialInstrumentService.write_off_instrument(
            mock_db,
            org_id,
            mock_instrument.instrument_id,
            date.today(),
        )

        assert result.status == InstrumentStatus.WRITTEN_OFF
        assert result.carrying_amount == Decimal("0")

    def test_get_instrument_success(self, mock_db, mock_instrument):
        """Test getting an instrument by ID."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_db.get.return_value = mock_instrument

        result = FinancialInstrumentService.get(mock_db, str(mock_instrument.instrument_id))

        assert result is not None
        assert result.instrument_id == mock_instrument.instrument_id

    def test_get_instrument_not_found(self, mock_db):
        """Test getting non-existent instrument raises HTTPException."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            FinancialInstrumentService.get(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_instruments(self, mock_db, org_id):
        """Test listing instruments."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instruments = [MockFinancialInstrument(organization_id=org_id) for _ in range(5)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_instruments

        result = FinancialInstrumentService.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_instruments_with_type_filter(self, mock_db, org_id):
        """Test listing instruments with type filter."""
        from app.services.ifrs.fin_inst.instrument import FinancialInstrumentService

        mock_instruments = [
            MockFinancialInstrument(
                organization_id=org_id,
                instrument_type=InstrumentType.DEBT_SECURITY,
            )
        ]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_instruments

        result = FinancialInstrumentService.list(
            mock_db,
            str(org_id),
            instrument_type=InstrumentType.DEBT_SECURITY,
        )

        assert len(result) == 1
