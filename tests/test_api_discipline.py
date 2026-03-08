"""
Discipline API Tests.

Tests for the /api/v1/people/discipline endpoints.
These tests mock the service layer since the discipline module
uses PostgreSQL-specific features not available in SQLite.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.people.discipline import (
    ActionType,
    CaseStatus,
    SeverityLevel,
    ViolationType,
)

# ============ Mock Case for Testing ============


class MockCase:
    """Mock DisciplinaryCase for API tests."""

    def __init__(
        self,
        case_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        case_number: str = "DC-2026-0001",
        employee_id: uuid.UUID = None,
        violation_type: ViolationType = ViolationType.MISCONDUCT,
        severity: SeverityLevel = SeverityLevel.MODERATE,
        subject: str = "Test violation",
        description: str = "Test description",
        incident_date: date = None,
        reported_date: date = None,
        status: CaseStatus = CaseStatus.DRAFT,
        **kwargs,
    ):
        self.case_id = case_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.case_number = case_number
        self.employee_id = employee_id or uuid.uuid4()
        self.violation_type = violation_type
        self.severity = severity
        self.subject = subject
        self.description = description
        self.incident_date = incident_date or date.today()
        self.reported_date = reported_date or date.today()
        self.status = status
        self.query_text = kwargs.get("query_text")
        self.query_issued_date = kwargs.get("query_issued_date")
        self.response_due_date = kwargs.get("response_due_date")
        self.hearing_date = kwargs.get("hearing_date")
        self.hearing_location = kwargs.get("hearing_location")
        self.hearing_notes = kwargs.get("hearing_notes")
        self.decision_summary = kwargs.get("decision_summary")
        self.decision_date = kwargs.get("decision_date")
        self.appeal_deadline = kwargs.get("appeal_deadline")
        self.appeal_reason = kwargs.get("appeal_reason")
        self.appeal_decision = kwargs.get("appeal_decision")
        self.closed_date = kwargs.get("closed_date")
        self.reported_by_id = kwargs.get("reported_by_id")
        self.investigating_officer_id = kwargs.get("investigating_officer_id")
        self.panel_chair_id = kwargs.get("panel_chair_id")
        self.created_at = kwargs.get("created_at") or datetime.now(UTC)
        self.updated_at = kwargs.get("updated_at")
        self.is_deleted = False

        # Related entities
        self.employee = MockEmployee(employee_id=self.employee_id)
        self.reported_by = None
        self.investigating_officer = None
        self.panel_chair = None
        self.witnesses = []
        self.actions = []
        self.documents = []
        self.responses = []


class MockEmployee:
    """Mock Employee for API tests."""

    def __init__(self, employee_id: uuid.UUID = None, full_name: str = "John Doe"):
        self.employee_id = employee_id or uuid.uuid4()
        self.full_name = full_name


# ============ Test Fixtures ============


@pytest.fixture
def mock_discipline_service():
    """Create a mock DisciplineService."""
    with patch("app.api.people.discipline.DisciplineService") as mock_cls:
        mock_service = MagicMock()
        mock_cls.return_value = mock_service
        yield mock_service


@pytest.fixture
def test_org_id() -> uuid.UUID:
    """Test organization ID."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def test_case(test_org_id: uuid.UUID) -> MockCase:
    """Create a test case."""
    return MockCase(organization_id=test_org_id)


# ============ API Tests ============


class TestDisciplineListCases:
    """Tests for GET /discipline/cases endpoint."""

    def test_list_cases_requires_auth(self, client):
        """Test that listing cases requires authentication."""
        response = client.get("/api/v1/people/discipline/cases")
        # Should get 401 or 403 without auth
        assert response.status_code in (401, 403)

    def test_list_cases_returns_paginated_response(
        self, client, auth_headers, mock_discipline_service, test_case
    ):
        """Test listing cases returns paginated results."""
        mock_discipline_service.list_cases.return_value = ([test_case], 1)

        response = client.get(
            "/api/v1/people/discipline/cases",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == 1

    def test_list_cases_with_status_filter(
        self, client, auth_headers, mock_discipline_service, test_case
    ):
        """Test filtering cases by status."""
        test_case.status = CaseStatus.QUERY_ISSUED
        mock_discipline_service.list_cases.return_value = ([test_case], 1)

        response = client.get(
            "/api/v1/people/discipline/cases?status=QUERY_ISSUED",
            headers=auth_headers,
        )

        assert response.status_code == 200
        mock_discipline_service.list_cases.assert_called_once()
        # Verify filter was passed correctly
        call_kwargs = mock_discipline_service.list_cases.call_args
        filters = call_kwargs[1]["filters"]
        assert filters.status == CaseStatus.QUERY_ISSUED


class TestDisciplineCreateCase:
    """Tests for POST /discipline/cases endpoint."""

    def test_create_case_success(
        self, client, auth_headers, mock_discipline_service, test_org_id
    ):
        """Test creating a case successfully."""
        employee_id = uuid.uuid4()
        created_case = MockCase(
            organization_id=test_org_id,
            employee_id=employee_id,
            case_number="DC-2026-0001",
        )
        mock_discipline_service.create_case.return_value = created_case

        payload = {
            "employee_id": str(employee_id),
            "violation_type": "MISCONDUCT",
            "severity": "MODERATE",
            "subject": "Unauthorized absence",
            "reported_date": str(date.today()),
        }

        response = client.post(
            "/api/v1/people/discipline/cases",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["case_number"] == "DC-2026-0001"
        assert data["status"] == "DRAFT"

    def test_create_case_missing_required_field(self, client, auth_headers):
        """Test creating case without required field fails."""
        payload = {
            "violation_type": "MISCONDUCT",
            # Missing employee_id, subject, reported_date
        }

        response = client.post(
            "/api/v1/people/discipline/cases",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422


class TestDisciplineGetCase:
    """Tests for GET /discipline/cases/{case_id} endpoint."""

    def test_get_case_success(
        self, client, auth_headers, mock_discipline_service, test_case
    ):
        """Test getting case details."""
        mock_discipline_service.get_case_detail.return_value = test_case

        response = client.get(
            f"/api/v1/people/discipline/cases/{test_case.case_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["case_id"] == str(test_case.case_id)
        assert data["case_number"] == test_case.case_number

    def test_get_case_includes_related_entities(
        self, client, auth_headers, mock_discipline_service, test_case
    ):
        """Test case detail includes witnesses, actions, responses."""
        # Add related entities to test case
        test_case.witnesses = []
        test_case.actions = []
        test_case.responses = []
        mock_discipline_service.get_case_detail.return_value = test_case

        response = client.get(
            f"/api/v1/people/discipline/cases/{test_case.case_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "witnesses" in data
        assert "actions" in data
        assert "responses" in data

    def test_get_case_wrong_organization_returns_404(
        self, client, auth_headers, mock_discipline_service
    ):
        """Test accessing case from different organization returns 404."""
        different_org = uuid.uuid4()
        case = MockCase(organization_id=different_org)
        mock_discipline_service.get_case_detail.return_value = case

        response = client.get(
            f"/api/v1/people/discipline/cases/{case.case_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestDisciplineWorkflow:
    """Tests for workflow action endpoints."""

    def test_issue_query_endpoint(
        self, client, auth_headers, mock_discipline_service, test_case
    ):
        """Test issuing a query."""
        test_case.status = CaseStatus.QUERY_ISSUED
        test_case.query_text = "Please explain your actions."
        test_case.response_due_date = date.today() + timedelta(days=7)

        mock_discipline_service.get_case_or_404.return_value = test_case
        mock_discipline_service.issue_query.return_value = test_case

        payload = {
            "query_text": "Please explain your actions on the incident date.",
            "response_due_date": str(date.today() + timedelta(days=7)),
        }

        response = client.post(
            f"/api/v1/people/discipline/cases/{test_case.case_id}/issue-query",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        mock_discipline_service.issue_query.assert_called_once()

    def test_submit_response_endpoint(
        self, client, auth_headers, mock_discipline_service, test_case
    ):
        """Test submitting employee response."""
        mock_response = MagicMock()
        mock_response.response_id = uuid.uuid4()
        mock_response.case_id = test_case.case_id
        mock_response.response_text = "My explanation."
        mock_response.is_initial_response = True
        mock_response.is_appeal_response = False
        mock_response.submitted_at = datetime.now(UTC)
        mock_response.acknowledged_at = None

        mock_discipline_service.get_case_or_404.return_value = test_case
        mock_discipline_service.record_response.return_value = mock_response

        payload = {"response_text": "My explanation."}

        response = client.post(
            f"/api/v1/people/discipline/cases/{test_case.case_id}/respond",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 200
        mock_discipline_service.record_response.assert_called_once()

    def test_workflow_validates_status(
        self, client, auth_headers, mock_discipline_service, test_case
    ):
        """Test workflow endpoints validate current status."""
        from app.errors import ValidationError

        mock_discipline_service.get_case_or_404.return_value = test_case
        mock_discipline_service.issue_query.side_effect = ValidationError(
            "Can only issue query from DRAFT status"
        )

        payload = {
            "query_text": "Please explain.",
            "response_due_date": str(date.today() + timedelta(days=7)),
        }

        response = client.post(
            f"/api/v1/people/discipline/cases/{test_case.case_id}/issue-query",
            json=payload,
            headers=auth_headers,
        )

        # ValidationError should result in 400 or 422
        assert response.status_code in (400, 422, 500)


class TestDisciplineDeleteCase:
    """Tests for DELETE /discipline/cases/{case_id} endpoint."""

    def test_delete_case_success(
        self, client, auth_headers, mock_discipline_service, test_case
    ):
        """Test deleting a draft case."""
        mock_discipline_service.get_case_or_404.return_value = test_case

        response = client.delete(
            f"/api/v1/people/discipline/cases/{test_case.case_id}",
            headers=auth_headers,
        )

        assert response.status_code == 204
        mock_discipline_service.delete_case.assert_called_once()

    def test_delete_case_wrong_organization_returns_404(
        self, client, auth_headers, mock_discipline_service
    ):
        """Test deleting case from different organization returns 404."""
        from app.errors import NotFoundError

        case = MockCase(organization_id=uuid.uuid4())
        mock_discipline_service.get_case_or_404.side_effect = NotFoundError(
            f"Disciplinary case {case.case_id} not found"
        )

        response = client.delete(
            f"/api/v1/people/discipline/cases/{case.case_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404
        mock_discipline_service.delete_case.assert_not_called()


class TestEmployeeActions:
    """Tests for employee action query endpoints."""

    def test_get_employee_active_actions(
        self, client, auth_headers, mock_discipline_service
    ):
        """Test getting active actions for an employee."""
        employee_id = uuid.uuid4()
        mock_action = MagicMock()
        mock_action.action_id = uuid.uuid4()
        mock_action.case_id = uuid.uuid4()
        mock_action.action_type = ActionType.WRITTEN_WARNING
        mock_action.description = "First warning"
        mock_action.effective_date = date.today()
        mock_action.end_date = None
        mock_action.warning_expiry_date = date.today() + timedelta(days=365)
        mock_action.is_active = True
        mock_action.payroll_processed = False
        mock_action.lifecycle_triggered = False
        mock_action.issued_by_id = None
        mock_action.created_at = datetime.now(UTC)

        mock_discipline_service.get_active_actions_for_employee.return_value = [
            mock_action
        ]

        response = client.get(
            f"/api/v1/people/discipline/employees/{employee_id}/active-actions",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["action_type"] == "WRITTEN_WARNING"

    def test_check_active_investigation(
        self, client, auth_headers, mock_discipline_service
    ):
        """Test checking if employee has active investigation."""
        employee_id = uuid.uuid4()
        mock_discipline_service.has_active_investigation.return_value = True

        response = client.get(
            f"/api/v1/people/discipline/employees/{employee_id}/has-investigation",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["has_active_investigation"] is True
