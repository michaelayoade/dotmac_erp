"""
Tests for LeaseModificationService.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.finance.lease.lease_contract import LeaseClassification, LeaseStatus
from app.models.finance.lease.lease_modification import ModificationType
from tests.ifrs.lease.conftest import (
    MockLeaseContract,
    MockLeaseLiability,
    MockLeaseAsset,
    MockLeaseModification,
)


class TestLeaseModificationService:
    """Tests for LeaseModificationService."""

    def test_process_modification_contract_not_found(self, mock_db, org_id, user_id):
        """Test modification fails when contract not found."""
        from app.services.finance.lease.lease_modification import (
            LeaseModificationService,
            ModificationInput,
        )

        mock_db.query.return_value.filter.return_value.first.return_value = None

        input_data = ModificationInput(
            lease_id=uuid.uuid4(),
            fiscal_period_id=uuid.uuid4(),
            modification_date=date.today(),
            effective_date=date.today(),
            modification_type=ModificationType.PAYMENT_CHANGE,
        )

        result = LeaseModificationService.process_modification(
            mock_db, org_id, input_data, user_id
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_process_modification_wrong_status(self, mock_db, org_id, user_id, mock_contract):
        """Test modification fails when contract not active."""
        from app.services.finance.lease.lease_modification import (
            LeaseModificationService,
            ModificationInput,
        )

        mock_contract.status = LeaseStatus.DRAFT
        mock_db.query.return_value.filter.return_value.first.return_value = mock_contract

        input_data = ModificationInput(
            lease_id=mock_contract.lease_id,
            fiscal_period_id=uuid.uuid4(),
            modification_date=date.today(),
            effective_date=date.today(),
            modification_type=ModificationType.PAYMENT_CHANGE,
        )

        result = LeaseModificationService.process_modification(
            mock_db, org_id, input_data, user_id
        )

        assert result.success is False
        assert "DRAFT" in result.message

    def test_process_modification_no_liability(
        self, mock_db, org_id, user_id, mock_active_contract
    ):
        """Test modification fails when liability not found."""
        from app.services.finance.lease.lease_modification import (
            LeaseModificationService,
            ModificationInput,
        )

        # Service queries contract, then liability, then asset
        mock_query = MagicMock()
        mock_query.filter.return_value.first.side_effect = [
            mock_active_contract,  # Contract query
            None,  # Liability query
            None,  # Asset query
        ]
        mock_db.query.return_value = mock_query

        input_data = ModificationInput(
            lease_id=mock_active_contract.lease_id,
            fiscal_period_id=uuid.uuid4(),
            modification_date=date.today(),
            effective_date=date.today(),
            modification_type=ModificationType.PAYMENT_CHANGE,
        )

        result = LeaseModificationService.process_modification(
            mock_db, org_id, input_data, user_id
        )

        assert result.success is False
        assert "must exist" in result.message.lower()

    def test_approve_modification_not_found(self, mock_db, org_id, approver_id):
        """Test approving non-existent modification fails."""
        from app.services.finance.lease.lease_modification import LeaseModificationService
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseModificationService.approve_modification(
                mock_db,
                org_id,
                uuid.uuid4(),
                approver_id,
            )

        assert exc_info.value.status_code == 404

    def test_approve_modification_sod_violation(
        self, mock_db, org_id, user_id, mock_modification
    ):
        """Test segregation of duties violation on approval."""
        from app.services.finance.lease.lease_modification import LeaseModificationService
        from fastapi import HTTPException

        mock_modification.created_by_user_id = user_id
        mock_db.query.return_value.filter.return_value.first.return_value = mock_modification

        with pytest.raises(HTTPException) as exc_info:
            LeaseModificationService.approve_modification(
                mock_db,
                org_id,
                mock_modification.modification_id,
                user_id,  # Same as creator
            )

        assert exc_info.value.status_code == 400
        assert "cannot be the same" in exc_info.value.detail.lower()

    def test_approve_modification_success(
        self, mock_db, org_id, mock_modification, approver_id
    ):
        """Test successful modification approval."""
        from app.services.finance.lease.lease_modification import LeaseModificationService

        mock_db.query.return_value.filter.return_value.first.return_value = mock_modification

        result = LeaseModificationService.approve_modification(
            mock_db,
            org_id,
            mock_modification.modification_id,
            approver_id,
        )

        assert result.approved_by_user_id == approver_id
        assert result.approved_at is not None

    def test_get_modification_success(self, mock_db, mock_modification):
        """Test getting a modification by ID."""
        from app.services.finance.lease.lease_modification import LeaseModificationService

        mock_db.query.return_value.filter.return_value.first.return_value = mock_modification

        result = LeaseModificationService.get(
            mock_db, str(mock_modification.modification_id)
        )

        assert result is not None
        assert result.modification_id == mock_modification.modification_id

    def test_get_modification_not_found(self, mock_db):
        """Test getting non-existent modification returns None."""
        from app.services.finance.lease.lease_modification import LeaseModificationService

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = LeaseModificationService.get(mock_db, str(uuid.uuid4()))

        assert result is None

    def test_list_by_lease(self, mock_db, mock_contract):
        """Test listing modifications for a lease."""
        from app.services.finance.lease.lease_modification import LeaseModificationService

        mock_modifications = [
            MockLeaseModification(lease_id=mock_contract.lease_id)
            for _ in range(3)
        ]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_modifications

        result = LeaseModificationService.list_by_lease(
            mock_db, mock_contract.lease_id
        )

        assert len(result) == 3

    def test_list_modifications(self, mock_db, org_id):
        """Test listing modifications with filters."""
        from app.services.finance.lease.lease_modification import LeaseModificationService

        mock_modifications = [MockLeaseModification() for _ in range(5)]
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_modifications

        result = LeaseModificationService.list(mock_db)

        assert len(result) == 5

    def test_list_modifications_with_type_filter(self, mock_db):
        """Test listing modifications with type filter."""
        from app.services.finance.lease.lease_modification import LeaseModificationService

        mock_modifications = [
            MockLeaseModification(modification_type=ModificationType.TERM_EXTENSION)
        ]
        mock_db.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_modifications

        result = LeaseModificationService.list(
            mock_db,
            modification_type=ModificationType.TERM_EXTENSION,
        )

        assert len(result) == 1

    def test_list_modifications_with_date_range(self, mock_db):
        """Test listing modifications with date range filter."""
        from app.services.finance.lease.lease_modification import LeaseModificationService

        mock_modifications = [MockLeaseModification()]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_modifications

        result = LeaseModificationService.list(
            mock_db,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
        )

        assert len(result) == 1

    def test_calculate_remaining_months(self):
        """Test remaining months calculation."""
        from app.services.finance.lease.lease_modification import LeaseModificationService

        result = LeaseModificationService._calculate_remaining_months(
            commencement_date=date(2024, 1, 1),
            total_term_months=60,
            as_of_date=date(2025, 1, 1),  # 12 months elapsed
        )

        assert result == 48  # 60 - 12

    def test_calculate_remaining_months_expired(self):
        """Test remaining months returns zero when expired."""
        from app.services.finance.lease.lease_modification import LeaseModificationService

        result = LeaseModificationService._calculate_remaining_months(
            commencement_date=date(2020, 1, 1),
            total_term_months=24,
            as_of_date=date(2024, 1, 1),  # 48 months elapsed
        )

        assert result == 0  # Cannot be negative
