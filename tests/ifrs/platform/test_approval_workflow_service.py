"""
Tests for ApprovalWorkflowService.
"""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.ifrs.platform.conftest import MockColumn


@contextmanager
def patch_approval_workflow_service():
    """Helper context manager that sets up all required patches for ApprovalWorkflowService."""
    with patch(
        "app.services.finance.platform.approval_workflow.ApprovalWorkflow"
    ) as mock_workflow:
        mock_workflow.organization_id = MockColumn()
        mock_workflow.document_type = MockColumn()
        mock_workflow.is_active = MockColumn()
        mock_workflow.threshold_amount = MockColumn()
        with (
            patch(
                "app.services.finance.platform.approval_workflow.and_",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.finance.platform.approval_workflow.coerce_uuid",
                side_effect=lambda x: x,
            ),
        ):
            yield mock_workflow


class MockApprovalWorkflow:
    """Mock ApprovalWorkflow model."""

    def __init__(
        self,
        workflow_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        workflow_code: str = "WF-001",
        workflow_name: str = "Workflow",
        approval_levels: list | None = None,
        threshold_amount: Decimal | None = None,
        threshold_currency_code: str | None = None,
        is_active: bool = True,
    ):
        self.workflow_id = workflow_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.workflow_code = workflow_code
        self.workflow_name = workflow_name
        self.approval_levels = approval_levels or [
            {
                "level": 1,
                "approver_type": "USER",
                "approver_id": uuid.uuid4(),
                "required_count": 1,
            }
        ]
        self.threshold_amount = threshold_amount
        self.threshold_currency_code = threshold_currency_code
        self.is_active = is_active


class MockApprovalRequest:
    """Mock ApprovalRequest model."""

    def __init__(
        self,
        request_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        workflow_id: uuid.UUID = None,
        workflow: MockApprovalWorkflow | None = None,
        document_type: str = "JOURNAL",
        document_id: uuid.UUID = None,
        document_reference: str = "DOC-001",
        document_amount: Decimal | None = None,
        document_currency_code: str | None = None,
        requested_by_user_id: uuid.UUID = None,
        requested_at: datetime | None = None,
        current_level: int = 1,
        status: object = "PENDING",
        decisions: list | None = None,
    ):
        self.request_id = request_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.workflow_id = workflow_id or (
            workflow.workflow_id if workflow else uuid.uuid4()
        )
        self.workflow = workflow
        self.document_type = document_type
        self.document_id = document_id or uuid.uuid4()
        self.document_reference = document_reference
        self.document_amount = document_amount
        self.document_currency_code = document_currency_code
        self.requested_by_user_id = requested_by_user_id or uuid.uuid4()
        self.requested_at = requested_at or datetime.now(UTC)
        self.current_level = current_level
        self.status = status
        self.decisions = decisions or []
        self.completed_at = None
        self.final_approver_user_id = None
        self.notes = None


class MockApprovalDecision:
    """Mock ApprovalDecision model."""

    def __init__(
        self,
        decision_id: uuid.UUID = None,
        request_id: uuid.UUID = None,
        level: int = 1,
        approver_user_id: uuid.UUID = None,
        action: object = None,
        decided_at: datetime | None = None,
        comments: str | None = None,
        mfa_verified: bool = False,
    ):
        self.decision_id = decision_id or uuid.uuid4()
        self.request_id = request_id or uuid.uuid4()
        self.level = level
        self.approver_user_id = approver_user_id or uuid.uuid4()
        self.action = action or MagicMock(value="APPROVE")
        self.decided_at = decided_at or datetime.now(UTC)
        self.comments = comments
        self.mfa_verified = mfa_verified


class TestApprovalWorkflowService:
    """Tests for ApprovalWorkflowService."""

    @pytest.fixture
    def service(self):
        """Import the service with mocked dependencies."""
        with patch.dict(
            "sys.modules",
            {
                "app.models.ifrs.audit.approval_workflow": MagicMock(),
                "app.models.ifrs.audit.approval_request": MagicMock(),
                "app.models.ifrs.audit.approval_decision": MagicMock(),
                "app.models.rbac": MagicMock(),
            },
        ):
            from app.services.finance.platform.approval_workflow import (
                ApprovalWorkflowService,
            )

            return ApprovalWorkflowService

    @pytest.mark.skip(
        reason="Complex FXService mocking requires integration testing - tested via integration tests"
    )
    def test_check_workflow_required_selects_threshold_workflow(
        self, service, mock_db_session, organization_id
    ):
        """check_workflow_required should choose threshold workflow with FX conversion."""
        pass

    @pytest.mark.skip(
        reason="Complex FXService mocking requires integration testing - tested via integration tests"
    )
    def test_check_workflow_required_falls_back_to_default(
        self, service, mock_db_session, organization_id
    ):
        """check_workflow_required should return default when below threshold."""
        pass

    def test_submit_for_approval_creates_request(
        self, service, mock_db_session, organization_id, user_id
    ):
        """submit_for_approval should create an approval request."""
        workflow = MockApprovalWorkflow(organization_id=organization_id)
        mock_db_session.get.return_value = workflow

        # Create a mock request with a request_id
        mock_request = MagicMock()
        mock_request.request_id = uuid.uuid4()

        with (
            patch(
                "app.services.finance.platform.approval_workflow.ApprovalRequest",
                return_value=mock_request,
            ),
            patch(
                "app.services.finance.platform.approval_workflow.ApprovalRequestStatus"
            ) as MockStatus,
        ):
            MockStatus.PENDING = "PENDING"
            with patch(
                "app.services.finance.platform.approval_workflow.coerce_uuid",
                side_effect=lambda x: x,
            ):
                result = service.submit_for_approval(
                    mock_db_session,
                    organization_id=organization_id,
                    workflow_id=workflow.workflow_id,
                    document_type="JOURNAL",
                    document_id=uuid.uuid4(),
                    document_reference="DOC-001",
                    document_amount=Decimal("10.00"),
                    document_currency_code="USD",
                    requested_by_user_id=user_id,
                )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()
        assert result == mock_request.request_id

    def test_submit_for_approval_raises_when_no_workflow(
        self, service, mock_db_session, organization_id, user_id
    ):
        """submit_for_approval should raise when workflow is missing."""
        mock_db_session.get.return_value = None

        with (
            patch(
                "app.services.finance.platform.approval_workflow.coerce_uuid",
                side_effect=lambda x: x,
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            service.submit_for_approval(
                mock_db_session,
                organization_id=organization_id,
                workflow_id=uuid.uuid4(),
                document_type="JOURNAL",
                document_id=uuid.uuid4(),
                document_reference="DOC-001",
                document_amount=Decimal("10.00"),
                document_currency_code="USD",
                requested_by_user_id=user_id,
            )

        assert exc_info.value.status_code == 404

    def test_approve_advances_level(
        self, service, mock_db_session, organization_id, user_id
    ):
        """approve should create decision and advance to next level."""
        workflow = MockApprovalWorkflow(
            organization_id=organization_id,
            approval_levels=[{"level": 1}, {"level": 2}],
        )
        request = MockApprovalRequest(
            organization_id=organization_id,
            workflow=workflow,
            current_level=1,
            status="PENDING",
            decisions=[],
        )
        mock_db_session.get.return_value = request

        with patch.object(
            service, "_is_user_allowed_for_level", return_value=(True, None)
        ):
            with patch.object(service, "get_approval_status", return_value=MagicMock()):
                with patch(
                    "app.services.finance.platform.approval_workflow.ApprovalDecision"
                ):
                    with patch(
                        "app.services.finance.platform.approval_workflow.ApprovalDecisionAction"
                    ) as MockAction:
                        MockAction.APPROVE = "APPROVE"
                        with patch(
                            "app.services.finance.platform.approval_workflow.ApprovalRequestStatus"
                        ) as MockStatus:
                            MockStatus.PENDING = "PENDING"
                            MockStatus.APPROVED = "APPROVED"
                            with patch(
                                "app.services.finance.platform.approval_workflow.coerce_uuid",
                                side_effect=lambda x: x,
                            ):
                                service.approve(
                                    mock_db_session,
                                    request_id=request.request_id,
                                    approver_user_id=user_id,
                                    comments="Approved",
                                )

        assert request.current_level == 2
        mock_db_session.add.assert_called()
        mock_db_session.commit.assert_called()

    def test_approve_raises_for_ineligible_user(
        self, service, mock_db_session, organization_id, user_id
    ):
        """approve should raise when user is not eligible for level."""
        allowed_user = uuid.uuid4()
        workflow = MockApprovalWorkflow(
            organization_id=organization_id,
            approval_levels=[
                {"level": 1, "approver_type": "USER", "approver_id": allowed_user}
            ],
        )
        request = MockApprovalRequest(
            organization_id=organization_id,
            workflow=workflow,
            current_level=1,
            status="PENDING",
            decisions=[],
        )
        mock_db_session.get.return_value = request

        with patch(
            "app.services.finance.platform.approval_workflow.ApprovalRequestStatus"
        ) as MockStatus:
            MockStatus.PENDING = "PENDING"
            with (
                patch(
                    "app.services.finance.platform.approval_workflow.coerce_uuid",
                    side_effect=lambda x: x,
                ),
                pytest.raises(HTTPException) as exc_info,
            ):
                service.approve(
                    mock_db_session,
                    request_id=request.request_id,
                    approver_user_id=user_id,
                )

        assert exc_info.value.status_code == 403

    def test_reject_updates_status(
        self, service, mock_db_session, organization_id, user_id
    ):
        """reject should create decision and reject request."""
        workflow = MockApprovalWorkflow(organization_id=organization_id)
        request = MockApprovalRequest(
            organization_id=organization_id,
            workflow=workflow,
            current_level=1,
            status="PENDING",
        )
        mock_db_session.get.return_value = request

        with patch.object(service, "get_approval_status", return_value=MagicMock()):
            with patch(
                "app.services.finance.platform.approval_workflow.ApprovalDecision"
            ):
                with patch(
                    "app.services.finance.platform.approval_workflow.ApprovalDecisionAction"
                ) as MockAction:
                    MockAction.REJECT = "REJECT"
                    with patch(
                        "app.services.finance.platform.approval_workflow.ApprovalRequestStatus"
                    ) as MockStatus:
                        MockStatus.PENDING = "PENDING"
                        MockStatus.REJECTED = "REJECTED"
                        with patch(
                            "app.services.finance.platform.approval_workflow.coerce_uuid",
                            side_effect=lambda x: x,
                        ):
                            service.reject(
                                mock_db_session,
                                request_id=request.request_id,
                                rejector_user_id=user_id,
                                comments="Incomplete documentation",
                            )

        assert request.status == "REJECTED"
        mock_db_session.add.assert_called()
        mock_db_session.commit.assert_called()

    def test_cancel_request_raises_for_wrong_user(
        self, service, mock_db_session, user_id
    ):
        """cancel_request should raise when user is not requester."""
        other_user = uuid.uuid4()
        request = MockApprovalRequest(
            requested_by_user_id=other_user,
            status="PENDING",
        )
        mock_db_session.get.return_value = request

        with patch(
            "app.services.finance.platform.approval_workflow.ApprovalRequestStatus"
        ) as MockStatus:
            MockStatus.PENDING = "PENDING"
            with (
                patch(
                    "app.services.finance.platform.approval_workflow.coerce_uuid",
                    side_effect=lambda x: x,
                ),
                pytest.raises(HTTPException) as exc_info,
            ):
                service.cancel_request(
                    mock_db_session,
                    request_id=request.request_id,
                    cancelled_by_user_id=user_id,
                    reason="No longer needed",
                )

        assert exc_info.value.status_code == 403

    def test_get_pending_approvals_filters_by_eligibility(
        self, service, mock_db_session, organization_id, user_id
    ):
        """get_pending_approvals should filter by eligibility."""
        workflow = MockApprovalWorkflow(organization_id=organization_id)
        requests = [
            MockApprovalRequest(organization_id=organization_id, workflow=workflow),
            MockApprovalRequest(organization_id=organization_id, workflow=workflow),
        ]
        mock_db_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = requests

        with patch(
            "app.services.finance.platform.approval_workflow.ApprovalRequestStatus"
        ) as MockStatus:
            MockStatus.PENDING = "PENDING"
            with (
                patch.object(
                    service,
                    "_is_user_allowed_for_request",
                    side_effect=[(True, None), (False, "no")],
                ),
                patch(
                    "app.services.finance.platform.approval_workflow.coerce_uuid",
                    side_effect=lambda x: x,
                ),
            ):
                result = service.get_pending_approvals(
                    mock_db_session,
                    organization_id=organization_id,
                    approver_user_id=user_id,
                )

        assert len(result) == 1

    def test_get_approval_status_returns_decisions(
        self, service, mock_db_session, organization_id
    ):
        """get_approval_status should return decision history."""
        workflow = MockApprovalWorkflow(
            organization_id=organization_id,
            approval_levels=[{"level": 1}, {"level": 2}],
        )
        decision = MockApprovalDecision(
            level=1,
            action=MagicMock(value="APPROVE"),
            mfa_verified=True,
        )
        request = MockApprovalRequest(
            organization_id=organization_id,
            workflow=workflow,
            current_level=2,
            status=MagicMock(value="PENDING"),
            decisions=[decision],
        )
        mock_db_session.get.return_value = request

        with patch(
            "app.services.finance.platform.approval_workflow.coerce_uuid",
            side_effect=lambda x: x,
        ):
            result = service.get_approval_status(
                mock_db_session,
                request_id=request.request_id,
            )

        assert result.status == "PENDING"
        assert result.total_levels == 2
        assert len(result.decisions) == 1
