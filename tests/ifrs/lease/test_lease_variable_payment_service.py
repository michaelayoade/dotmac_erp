"""
Tests for LeaseVariablePaymentService.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.ifrs.lease.lease_contract import LeaseStatus
from app.models.ifrs.lease.lease_payment_schedule import PaymentStatus
from tests.ifrs.lease.conftest import (
    MockLeaseContract,
    MockLeaseLiability,
    MockLeaseAsset,
    MockLeasePaymentSchedule,
)


class TestLeaseVariablePaymentService:
    """Tests for LeaseVariablePaymentService."""

    def test_record_variable_payment_not_found(self, mock_db, org_id):
        """Test recording variable payment on non-existent schedule fails."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
            VariablePaymentInput,
        )
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        input_data = VariablePaymentInput(
            schedule_id=uuid.uuid4(),
            variable_amount=Decimal("500.00"),
        )

        with pytest.raises(HTTPException) as exc_info:
            LeaseVariablePaymentService.record_variable_payment(
                mock_db, org_id, input_data
            )

        assert exc_info.value.status_code == 404

    def test_record_variable_payment_already_paid(
        self, mock_db, org_id, mock_payment_schedule
    ):
        """Test recording variable payment on paid schedule fails."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
            VariablePaymentInput,
        )
        from fastapi import HTTPException

        mock_payment_schedule.status = PaymentStatus.PAID
        mock_db.query.return_value.filter.return_value.first.return_value = mock_payment_schedule

        input_data = VariablePaymentInput(
            schedule_id=mock_payment_schedule.schedule_id,
            variable_amount=Decimal("500.00"),
        )

        with pytest.raises(HTTPException) as exc_info:
            LeaseVariablePaymentService.record_variable_payment(
                mock_db, org_id, input_data
            )

        assert exc_info.value.status_code == 400
        assert "already paid" in exc_info.value.detail.lower()

    def test_record_variable_payment_success(
        self, mock_db, org_id, mock_payment_schedule
    ):
        """Test successful variable payment recording."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
            VariablePaymentInput,
        )

        mock_payment_schedule.status = PaymentStatus.SCHEDULED
        mock_payment_schedule.principal_portion = Decimal("4000.00")
        mock_payment_schedule.interest_portion = Decimal("1000.00")
        mock_db.query.return_value.filter.return_value.first.return_value = mock_payment_schedule

        input_data = VariablePaymentInput(
            schedule_id=mock_payment_schedule.schedule_id,
            variable_amount=Decimal("500.00"),
            description="Usage-based payment",
        )

        result = LeaseVariablePaymentService.record_variable_payment(
            mock_db, org_id, input_data
        )

        assert result.variable_payment == Decimal("500.00")
        assert result.total_payment == Decimal("5500.00")  # 4000 + 1000 + 500

    def test_apply_index_adjustment_contract_not_found(self, mock_db, org_id, user_id):
        """Test index adjustment fails when contract not found."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
            IndexAdjustmentInput,
        )

        mock_db.query.return_value.filter.return_value.first.return_value = None

        input_data = IndexAdjustmentInput(
            lease_id=uuid.uuid4(),
            adjustment_date=date.today(),
            fiscal_period_id=uuid.uuid4(),
            new_index_value=Decimal("110.00"),
            base_index_value=Decimal("100.00"),
        )

        result = LeaseVariablePaymentService.apply_index_adjustment(
            mock_db, org_id, input_data, user_id
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_apply_index_adjustment_wrong_status(
        self, mock_db, org_id, user_id, mock_contract
    ):
        """Test index adjustment fails for non-active contract."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
            IndexAdjustmentInput,
        )

        mock_contract.status = LeaseStatus.DRAFT
        mock_db.query.return_value.filter.return_value.first.return_value = mock_contract

        input_data = IndexAdjustmentInput(
            lease_id=mock_contract.lease_id,
            adjustment_date=date.today(),
            fiscal_period_id=uuid.uuid4(),
            new_index_value=Decimal("110.00"),
            base_index_value=Decimal("100.00"),
        )

        result = LeaseVariablePaymentService.apply_index_adjustment(
            mock_db, org_id, input_data, user_id
        )

        assert result.success is False
        assert "DRAFT" in result.message

    def test_apply_index_adjustment_no_liability(
        self, mock_db, org_id, user_id, mock_active_contract
    ):
        """Test index adjustment fails when liability not found."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
            IndexAdjustmentInput,
        )

        # Service queries contract, then liability, then asset
        mock_query = MagicMock()
        mock_query.filter.return_value.first.side_effect = [
            mock_active_contract,  # Contract
            None,  # Liability
            None,  # Asset
        ]
        mock_db.query.return_value = mock_query

        input_data = IndexAdjustmentInput(
            lease_id=mock_active_contract.lease_id,
            adjustment_date=date.today(),
            fiscal_period_id=uuid.uuid4(),
            new_index_value=Decimal("110.00"),
            base_index_value=Decimal("100.00"),
        )

        result = LeaseVariablePaymentService.apply_index_adjustment(
            mock_db, org_id, input_data, user_id
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_get_scheduled_payments(self, mock_db, mock_contract):
        """Test getting scheduled payments for a lease."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
        )

        mock_payments = [
            MockLeasePaymentSchedule(lease_id=mock_contract.lease_id, payment_number=i)
            for i in range(1, 6)
        ]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = mock_payments

        result = LeaseVariablePaymentService.get_scheduled_payments(
            mock_db, mock_contract.lease_id
        )

        assert len(result) == 5

    def test_get_scheduled_payments_include_paid(self, mock_db, mock_contract):
        """Test getting all payments including paid ones."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
        )

        mock_payments = [
            MockLeasePaymentSchedule(
                lease_id=mock_contract.lease_id,
                payment_number=i,
                status=PaymentStatus.PAID if i <= 3 else PaymentStatus.SCHEDULED,
            )
            for i in range(1, 6)
        ]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_payments

        result = LeaseVariablePaymentService.get_scheduled_payments(
            mock_db, mock_contract.lease_id, include_paid=True
        )

        assert len(result) == 5

    def test_mark_payment_paid_not_found(self, mock_db):
        """Test marking non-existent payment fails."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
        )
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseVariablePaymentService.mark_payment_paid(
                mock_db,
                uuid.uuid4(),
                actual_payment_date=date.today(),
                actual_payment_amount=Decimal("5000.00"),
            )

        assert exc_info.value.status_code == 404

    def test_mark_payment_paid_already_paid(self, mock_db, mock_payment_schedule):
        """Test marking already paid payment fails."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
        )
        from fastapi import HTTPException

        mock_payment_schedule.status = PaymentStatus.PAID
        mock_db.query.return_value.filter.return_value.first.return_value = mock_payment_schedule

        with pytest.raises(HTTPException) as exc_info:
            LeaseVariablePaymentService.mark_payment_paid(
                mock_db,
                mock_payment_schedule.schedule_id,
                actual_payment_date=date.today(),
                actual_payment_amount=Decimal("5000.00"),
            )

        assert exc_info.value.status_code == 400

    def test_mark_payment_paid_success(self, mock_db, mock_payment_schedule):
        """Test successful payment marking."""
        from app.services.ifrs.lease.lease_variable_payment import (
            LeaseVariablePaymentService,
        )

        mock_payment_schedule.status = PaymentStatus.SCHEDULED
        mock_db.query.return_value.filter.return_value.first.return_value = mock_payment_schedule

        payment_ref = uuid.uuid4()
        result = LeaseVariablePaymentService.mark_payment_paid(
            mock_db,
            mock_payment_schedule.schedule_id,
            actual_payment_date=date(2024, 1, 15),
            actual_payment_amount=Decimal("5000.00"),
            payment_reference=payment_ref,
        )

        assert result.status == PaymentStatus.PAID
        assert result.actual_payment_date == date(2024, 1, 15)
        assert result.actual_payment_amount == Decimal("5000.00")
        assert result.payment_reference == payment_ref
