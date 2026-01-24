"""
Tests for LeaseContractService.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.finance.lease.lease_contract import LeaseClassification, LeaseStatus
from tests.ifrs.lease.conftest import (
    MockLeaseContract,
    MockLeaseLiability,
    MockLeaseAsset,
)


class TestLeaseContractService:
    """Tests for LeaseContractService."""

    def test_calculate_lease_term_months(self):
        """Test lease term calculation in months."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        result = LeaseContractService.calculate_lease_term_months(
            commencement_date=date(2024, 1, 1),
            end_date=date(2028, 12, 31),
            renewal_months=0,
            renewal_certain=False,
        )

        assert result == 59  # 5 years minus 1 month

    def test_calculate_lease_term_with_renewal(self):
        """Test lease term calculation including renewal option."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        result = LeaseContractService.calculate_lease_term_months(
            commencement_date=date(2024, 1, 1),
            end_date=date(2028, 12, 31),
            renewal_months=24,
            renewal_certain=True,
        )

        assert result == 83  # 59 + 24 renewal months

    def test_calculate_lease_term_renewal_not_certain(self):
        """Test that renewal months are not added when not reasonably certain."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        result = LeaseContractService.calculate_lease_term_months(
            commencement_date=date(2024, 1, 1),
            end_date=date(2028, 12, 31),
            renewal_months=24,
            renewal_certain=False,  # Not certain
        )

        assert result == 59  # Only base term, no renewal

    def test_determine_discount_rate_implicit_known(self):
        """Test discount rate uses implicit rate when known."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        result = LeaseContractService.determine_discount_rate(
            ibr=Decimal("0.06"),
            implicit_rate=Decimal("0.045"),
            implicit_known=True,
        )

        assert result == Decimal("0.045")

    def test_determine_discount_rate_ibr_fallback(self):
        """Test discount rate falls back to IBR when implicit not known."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        result = LeaseContractService.determine_discount_rate(
            ibr=Decimal("0.06"),
            implicit_rate=Decimal("0.045"),
            implicit_known=False,
        )

        assert result == Decimal("0.06")

    def test_get_contract_success(self, mock_db, mock_contract):
        """Test getting a lease contract by ID."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_db.get.return_value = mock_contract

        result = LeaseContractService.get(mock_db, str(mock_contract.lease_id))

        assert result is not None
        assert result.lease_id == mock_contract.lease_id

    def test_get_contract_not_found(self, mock_db):
        """Test getting non-existent contract raises HTTPException."""
        from app.services.finance.lease.lease_contract import LeaseContractService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseContractService.get(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_approve_contract_success(self, mock_db, org_id, mock_contract, approver_id):
        """Test successful contract approval."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_contract.status = LeaseStatus.DRAFT
        mock_db.get.return_value = mock_contract

        result = LeaseContractService.approve_contract(
            mock_db,
            org_id,
            mock_contract.lease_id,
            approver_id,
        )

        assert result.approved_by_user_id == approver_id
        assert result.approved_at is not None

    def test_approve_contract_not_found(self, mock_db, org_id, approver_id):
        """Test approving non-existent contract fails."""
        from app.services.finance.lease.lease_contract import LeaseContractService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseContractService.approve_contract(
                mock_db,
                org_id,
                uuid.uuid4(),
                approver_id,
            )

        assert exc_info.value.status_code == 404

    def test_approve_contract_wrong_status(self, mock_db, org_id, mock_contract, approver_id):
        """Test approving contract with wrong status fails."""
        from app.services.finance.lease.lease_contract import LeaseContractService
        from fastapi import HTTPException

        mock_contract.status = LeaseStatus.ACTIVE  # Already active
        mock_db.get.return_value = mock_contract

        with pytest.raises(HTTPException) as exc_info:
            LeaseContractService.approve_contract(
                mock_db,
                org_id,
                mock_contract.lease_id,
                approver_id,
            )

        assert exc_info.value.status_code == 400
        assert "Cannot approve" in exc_info.value.detail

    def test_approve_contract_sod_violation(self, mock_db, org_id, mock_contract, user_id):
        """Test segregation of duties violation (creator cannot approve)."""
        from app.services.finance.lease.lease_contract import LeaseContractService
        from fastapi import HTTPException

        mock_contract.status = LeaseStatus.DRAFT
        mock_contract.created_by_user_id = user_id
        mock_db.get.return_value = mock_contract

        with pytest.raises(HTTPException) as exc_info:
            LeaseContractService.approve_contract(
                mock_db,
                org_id,
                mock_contract.lease_id,
                user_id,  # Same as creator
            )

        assert exc_info.value.status_code == 400
        assert "Segregation of duties" in exc_info.value.detail

    def test_terminate_contract_success(self, mock_db, org_id, mock_active_contract):
        """Test successful contract termination."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_db.get.return_value = mock_active_contract

        result = LeaseContractService.terminate_contract(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            termination_date=date(2025, 6, 30),
            termination_reason="Early exit",
        )

        assert result.status == LeaseStatus.TERMINATED
        assert result.end_date == date(2025, 6, 30)

    def test_terminate_contract_not_found(self, mock_db, org_id):
        """Test terminating non-existent contract fails."""
        from app.services.finance.lease.lease_contract import LeaseContractService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseContractService.terminate_contract(
                mock_db,
                org_id,
                uuid.uuid4(),
                termination_date=date.today(),
            )

        assert exc_info.value.status_code == 404

    def test_terminate_contract_wrong_status(self, mock_db, org_id, mock_contract):
        """Test terminating contract with wrong status fails."""
        from app.services.finance.lease.lease_contract import LeaseContractService
        from fastapi import HTTPException

        mock_contract.status = LeaseStatus.DRAFT  # Not active
        mock_db.get.return_value = mock_contract

        with pytest.raises(HTTPException) as exc_info:
            LeaseContractService.terminate_contract(
                mock_db,
                org_id,
                mock_contract.lease_id,
                termination_date=date.today(),
            )

        assert exc_info.value.status_code == 400

    def test_list_contracts(self, mock_db, org_id):
        """Test listing lease contracts."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_contracts = [MockLeaseContract(organization_id=org_id) for _ in range(5)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_contracts

        result = LeaseContractService.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_contracts_with_classification_filter(self, mock_db, org_id):
        """Test listing contracts with classification filter."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_contracts = [
            MockLeaseContract(organization_id=org_id, classification=LeaseClassification.FINANCE)
        ]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_contracts

        result = LeaseContractService.list(
            mock_db,
            str(org_id),
            classification=LeaseClassification.FINANCE,
        )

        assert len(result) == 1

    def test_list_contracts_with_status_filter(self, mock_db, org_id):
        """Test listing contracts with status filter."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_contracts = [
            MockLeaseContract(organization_id=org_id, status=LeaseStatus.ACTIVE)
        ]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_contracts

        result = LeaseContractService.list(
            mock_db,
            str(org_id),
            status=LeaseStatus.ACTIVE,
        )

        assert len(result) == 1

    def test_get_liability_success(self, mock_db, mock_liability):
        """Test getting lease liability."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_db.query.return_value.filter.return_value.first.return_value = mock_liability

        result = LeaseContractService.get_liability(mock_db, str(mock_liability.lease_id))

        assert result is not None
        assert result.liability_id == mock_liability.liability_id

    def test_get_liability_not_found(self, mock_db):
        """Test getting non-existent liability returns None."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = LeaseContractService.get_liability(mock_db, str(uuid.uuid4()))

        assert result is None

    def test_get_asset_success(self, mock_db, mock_asset):
        """Test getting ROU asset."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_db.query.return_value.filter.return_value.first.return_value = mock_asset

        result = LeaseContractService.get_asset(mock_db, str(mock_asset.lease_id))

        assert result is not None
        assert result.asset_id == mock_asset.asset_id

    def test_get_asset_not_found(self, mock_db):
        """Test getting non-existent asset returns None."""
        from app.services.finance.lease.lease_contract import LeaseContractService

        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = LeaseContractService.get_asset(mock_db, str(uuid.uuid4()))

        assert result is None
