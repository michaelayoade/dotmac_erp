"""
Unit tests for RequisitionService approval/rejection guards.

Covers:
- F37b: segregation of duties — creator cannot approve their own requisition
- F44: reject only applies to in-flight (SUBMITTED / BUDGET_VERIFIED) states
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.procurement.enums import RequisitionStatus
from app.services.common import ValidationError
from app.services.procurement.requisition import RequisitionService


def _make_requisition(*, status, created_by_user_id):
    req = MagicMock()
    req.status = status
    req.created_by_user_id = created_by_user_id
    req.requisition_number = "REQ-000001"
    return req


class TestApproveSegregationOfDuties:
    def test_creator_cannot_approve_own_requisition(self):
        """F37b: the creator approving their own requisition is rejected."""
        db = MagicMock()
        service = RequisitionService(db)
        org_id = uuid4()
        requisition_id = uuid4()
        user_id = uuid4()

        req = _make_requisition(
            status=RequisitionStatus.SUBMITTED,
            created_by_user_id=user_id,
        )
        service.get_by_id = MagicMock(return_value=req)

        with pytest.raises(ValidationError) as exc_info:
            service.approve(org_id, requisition_id, user_id)

        assert "segregation of duties" in str(exc_info.value).lower()
        assert req.status == RequisitionStatus.SUBMITTED  # unchanged

    def test_different_approver_succeeds(self):
        """A non-creator approver moves the requisition to APPROVED."""
        db = MagicMock()
        service = RequisitionService(db)
        org_id = uuid4()
        requisition_id = uuid4()

        req = _make_requisition(
            status=RequisitionStatus.SUBMITTED,
            created_by_user_id=uuid4(),
        )
        service.get_by_id = MagicMock(return_value=req)

        result = service.approve(org_id, requisition_id, uuid4())

        assert result.status == RequisitionStatus.APPROVED
        db.flush.assert_called()

    def test_approve_non_approvable_state(self):
        """A DRAFT requisition is not approvable."""
        db = MagicMock()
        service = RequisitionService(db)

        req = _make_requisition(
            status=RequisitionStatus.DRAFT,
            created_by_user_id=uuid4(),
        )
        service.get_by_id = MagicMock(return_value=req)

        with pytest.raises(ValidationError) as exc_info:
            service.approve(uuid4(), uuid4(), uuid4())

        assert "approvable" in str(exc_info.value).lower()


class TestRejectStateGuard:
    @pytest.mark.parametrize(
        "status",
        [RequisitionStatus.SUBMITTED, RequisitionStatus.BUDGET_VERIFIED],
    )
    def test_reject_in_flight_state_succeeds(self, status):
        """F44: in-flight requisitions can be rejected."""
        db = MagicMock()
        service = RequisitionService(db)

        req = _make_requisition(status=status, created_by_user_id=uuid4())
        service.get_by_id = MagicMock(return_value=req)

        result = service.reject(uuid4(), uuid4())

        assert result.status == RequisitionStatus.REJECTED
        db.flush.assert_called()

    @pytest.mark.parametrize(
        "status",
        [
            RequisitionStatus.DRAFT,
            RequisitionStatus.APPROVED,
            RequisitionStatus.CONVERTED,
            RequisitionStatus.REJECTED,
            RequisitionStatus.CANCELLED,
        ],
    )
    def test_reject_invalid_state_rejected(self, status):
        """F44: requisitions outside the in-flight states cannot be rejected."""
        db = MagicMock()
        service = RequisitionService(db)

        req = _make_requisition(status=status, created_by_user_id=uuid4())
        service.get_by_id = MagicMock(return_value=req)

        with pytest.raises(ValidationError) as exc_info:
            service.reject(uuid4(), uuid4())

        assert "rejectable" in str(exc_info.value).lower()
        assert req.status == status  # unchanged
