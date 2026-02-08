"""
Fixtures for Discipline Module Tests.

These tests use mock objects to avoid PostgreSQL-specific dependencies
while still testing the service logic.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.models.people.discipline import (
    ActionType,
    CaseStatus,
    DocumentType,
    SeverityLevel,
    ViolationType,
)

# ============ Mock Model Classes ============


class MockEmployee:
    """Mock Employee model for discipline tests."""

    def __init__(
        self,
        employee_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        person_id: uuid.UUID = None,
        first_name: str = "John",
        last_name: str = "Doe",
        full_name: str = "John Doe",
        email: str = "john.doe@example.com",
    ):
        self.employee_id = employee_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.person_id = person_id or uuid.uuid4()
        self.first_name = first_name
        self.last_name = last_name
        self.full_name = full_name
        self.email = email


class MockDisciplinaryCase:
    """Mock DisciplinaryCase model."""

    def __init__(
        self,
        case_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        case_number: str = "DC-2026-0001",
        employee_id: uuid.UUID = None,
        violation_type: ViolationType = ViolationType.MISCONDUCT,
        severity: SeverityLevel = SeverityLevel.MODERATE,
        subject: str = "Test Violation",
        description: str | None = "Detailed description of the violation.",
        incident_date: date | None = None,
        reported_date: date = None,
        status: CaseStatus = CaseStatus.DRAFT,
        query_text: str | None = None,
        query_issued_date: date | None = None,
        response_due_date: date | None = None,
        hearing_date: datetime | None = None,
        hearing_location: str | None = None,
        hearing_notes: str | None = None,
        decision_summary: str | None = None,
        decision_date: date | None = None,
        appeal_deadline: date | None = None,
        appeal_reason: str | None = None,
        appeal_decision: str | None = None,
        closed_date: date | None = None,
        reported_by_id: uuid.UUID | None = None,
        investigating_officer_id: uuid.UUID | None = None,
        panel_chair_id: uuid.UUID | None = None,
        created_by_id: uuid.UUID | None = None,
        status_changed_at: datetime | None = None,
        status_changed_by_id: uuid.UUID | None = None,
        is_deleted: bool = False,
        created_at: datetime = None,
        updated_at: datetime | None = None,
        employee: MockEmployee | None = None,
    ):
        self.case_id = case_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.case_number = case_number
        self.employee_id = employee_id or uuid.uuid4()
        self.violation_type = violation_type
        self.severity = severity
        self.subject = subject
        self.description = description
        self.incident_date = incident_date or date.today() - timedelta(days=3)
        self.reported_date = reported_date or date.today()
        self.status = status
        self.query_text = query_text
        self.query_issued_date = query_issued_date
        self.response_due_date = response_due_date
        self.hearing_date = hearing_date
        self.hearing_location = hearing_location
        self.hearing_notes = hearing_notes
        self.decision_summary = decision_summary
        self.decision_date = decision_date
        self.appeal_deadline = appeal_deadline
        self.appeal_reason = appeal_reason
        self.appeal_decision = appeal_decision
        self.closed_date = closed_date
        self.reported_by_id = reported_by_id
        self.investigating_officer_id = investigating_officer_id
        self.panel_chair_id = panel_chair_id
        self.created_by_id = created_by_id
        self.status_changed_at = status_changed_at
        self.status_changed_by_id = status_changed_by_id
        self.is_deleted = is_deleted
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at
        self.employee = employee

        # Related collections
        self.witnesses: list[MockCaseWitness] = []
        self.actions: list[MockCaseAction] = []
        self.documents: list[MockCaseDocument] = []
        self.responses: list[MockCaseResponse] = []

    def __repr__(self) -> str:
        return f"<MockDisciplinaryCase {self.case_number} - {self.status.value}>"


class MockCaseAction:
    """Mock CaseAction model."""

    def __init__(
        self,
        action_id: uuid.UUID = None,
        case_id: uuid.UUID = None,
        action_type: ActionType = ActionType.WRITTEN_WARNING,
        description: str | None = "Test action description",
        effective_date: date = None,
        end_date: date | None = None,
        warning_expiry_date: date | None = None,
        is_active: bool = True,
        payroll_processed: bool = False,
        lifecycle_triggered: bool = False,
        issued_by_id: uuid.UUID | None = None,
        created_at: datetime = None,
    ):
        self.action_id = action_id or uuid.uuid4()
        self.case_id = case_id or uuid.uuid4()
        self.action_type = action_type
        self.description = description
        self.effective_date = effective_date or date.today()
        self.end_date = end_date
        self.warning_expiry_date = warning_expiry_date
        self.is_active = is_active
        self.payroll_processed = payroll_processed
        self.lifecycle_triggered = lifecycle_triggered
        self.issued_by_id = issued_by_id
        self.created_at = created_at or datetime.now(UTC)

    @property
    def is_suspension(self) -> bool:
        return self.action_type in (
            ActionType.SUSPENSION_PAID,
            ActionType.SUSPENSION_UNPAID,
        )

    @property
    def is_termination(self) -> bool:
        return self.action_type == ActionType.TERMINATION

    @property
    def requires_payroll_deduction(self) -> bool:
        return self.action_type == ActionType.SUSPENSION_UNPAID


class MockCaseWitness:
    """Mock CaseWitness model."""

    def __init__(
        self,
        witness_id: uuid.UUID = None,
        case_id: uuid.UUID = None,
        employee_id: uuid.UUID | None = None,
        external_name: str | None = None,
        external_contact: str | None = None,
        statement: str | None = None,
        statement_date: datetime | None = None,
        created_at: datetime = None,
    ):
        self.witness_id = witness_id or uuid.uuid4()
        self.case_id = case_id or uuid.uuid4()
        self.employee_id = employee_id
        self.external_name = external_name
        self.external_contact = external_contact
        self.statement = statement
        self.statement_date = statement_date
        self.created_at = created_at or datetime.now(UTC)


class MockCaseDocument:
    """Mock CaseDocument model."""

    def __init__(
        self,
        document_id: uuid.UUID = None,
        case_id: uuid.UUID = None,
        document_type: DocumentType = DocumentType.EVIDENCE,
        title: str = "Test Document",
        description: str | None = None,
        file_path: str = "/uploads/discipline/test.pdf",
        file_name: str = "test.pdf",
        file_size: int | None = 1024,
        mime_type: str | None = "application/pdf",
        uploaded_by_id: uuid.UUID | None = None,
        created_at: datetime = None,
    ):
        self.document_id = document_id or uuid.uuid4()
        self.case_id = case_id or uuid.uuid4()
        self.document_type = document_type
        self.title = title
        self.description = description
        self.file_path = file_path
        self.file_name = file_name
        self.file_size = file_size
        self.mime_type = mime_type
        self.uploaded_by_id = uploaded_by_id
        self.created_at = created_at or datetime.now(UTC)


class MockCaseResponse:
    """Mock CaseResponse model."""

    def __init__(
        self,
        response_id: uuid.UUID = None,
        case_id: uuid.UUID = None,
        response_text: str = "Employee response to the query.",
        is_initial_response: bool = True,
        is_appeal_response: bool = False,
        submitted_at: datetime = None,
        acknowledged_at: datetime | None = None,
    ):
        self.response_id = response_id or uuid.uuid4()
        self.case_id = case_id or uuid.uuid4()
        self.response_text = response_text
        self.is_initial_response = is_initial_response
        self.is_appeal_response = is_appeal_response
        self.submitted_at = submitted_at or datetime.now(UTC)
        self.acknowledged_at = acknowledged_at


# ============ Mock Database Session ============


class MockScalarsResult:
    """Mock scalars result that supports .all() and .unique().all() chains."""

    def __init__(self, items: list[Any]):
        self._items = items

    def all(self) -> list[Any]:
        return self._items

    def unique(self) -> "MockScalarsResult":
        return self


class MockQueryBuilder:
    """Chainable mock query builder."""

    def __init__(self, items: list[Any] = None):
        self._items = items or []

    def scalars(self, *args: Any, **kwargs: Any) -> MockScalarsResult:
        return MockScalarsResult(self._items)

    def scalar(self, *args: Any, **kwargs: Any) -> Any:
        return self._items[0] if self._items else None


def create_mock_db_session(
    *,
    get_returns: dict[uuid.UUID, Any] | None = None,
    scalar_returns: Any = None,
    scalars_returns: list[Any] | None = None,
) -> MagicMock:
    """
    Create a configurable mock database session.

    Args:
        get_returns: Dict mapping IDs to objects for db.get() calls
        scalar_returns: Return value for db.scalar() calls
        scalars_returns: List of items for db.scalars().all() calls
    """
    session = MagicMock()

    # Configure db.get() to look up by ID
    if get_returns:
        session.get = MagicMock(side_effect=lambda model, id: get_returns.get(id))
    else:
        session.get = MagicMock(return_value=None)

    # Configure db.scalar()
    session.scalar = MagicMock(return_value=scalar_returns)

    # Configure db.scalars() to return chainable result
    mock_result = MockScalarsResult(scalars_returns or [])
    session.scalars = MagicMock(return_value=mock_result)

    # Standard session methods
    session.add = MagicMock()
    session.flush = MagicMock()
    session.commit = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.execute = MagicMock(return_value=MockQueryBuilder(scalars_returns or []))

    return session


# ============ Fixtures ============


@pytest.fixture
def organization_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def employee_id() -> uuid.UUID:
    """Generate a test employee ID."""
    return uuid.uuid4()


@pytest.fixture
def case_id() -> uuid.UUID:
    """Generate a test case ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    """Generate a test user ID (for actor tracking)."""
    return uuid.uuid4()


@pytest.fixture
def mock_employee(organization_id: uuid.UUID, employee_id: uuid.UUID) -> MockEmployee:
    """Create a mock employee."""
    return MockEmployee(
        employee_id=employee_id,
        organization_id=organization_id,
    )


@pytest.fixture
def mock_case(
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    case_id: uuid.UUID,
    mock_employee: MockEmployee,
) -> MockDisciplinaryCase:
    """Create a mock disciplinary case in DRAFT status."""
    return MockDisciplinaryCase(
        case_id=case_id,
        organization_id=organization_id,
        employee_id=employee_id,
        employee=mock_employee,
    )


@pytest.fixture
def mock_case_query_issued(mock_case: MockDisciplinaryCase) -> MockDisciplinaryCase:
    """Create a mock case in QUERY_ISSUED status."""
    mock_case.status = CaseStatus.QUERY_ISSUED
    mock_case.query_text = "Please explain your actions on the incident date."
    mock_case.query_issued_date = date.today()
    mock_case.response_due_date = date.today() + timedelta(days=7)
    return mock_case


@pytest.fixture
def mock_case_decision_made(mock_case: MockDisciplinaryCase) -> MockDisciplinaryCase:
    """Create a mock case in DECISION_MADE status."""
    mock_case.status = CaseStatus.DECISION_MADE
    mock_case.query_text = "Please explain your actions."
    mock_case.query_issued_date = date.today() - timedelta(days=14)
    mock_case.response_due_date = date.today() - timedelta(days=7)
    mock_case.decision_date = date.today()
    mock_case.decision_summary = "A written warning is issued."
    mock_case.appeal_deadline = date.today() + timedelta(days=14)
    mock_case.actions = [
        MockCaseAction(
            case_id=mock_case.case_id,
            action_type=ActionType.WRITTEN_WARNING,
        )
    ]
    return mock_case


@pytest.fixture
def mock_db_session() -> MagicMock:
    """Create a basic mock database session."""
    return create_mock_db_session()


@pytest.fixture
def sample_case_create_data() -> dict[str, Any]:
    """Sample data for creating a disciplinary case."""
    return {
        "employee_id": uuid.uuid4(),
        "violation_type": ViolationType.MISCONDUCT,
        "severity": SeverityLevel.MODERATE,
        "subject": "Unauthorized absence",
        "description": "Employee was absent without permission on specified date.",
        "incident_date": date.today() - timedelta(days=2),
        "reported_date": date.today(),
        "reported_by_id": uuid.uuid4(),
    }


@pytest.fixture
def sample_issue_query_data() -> dict[str, Any]:
    """Sample data for issuing a query."""
    return {
        "query_text": (
            "You are hereby required to explain in writing why you were "
            "absent from work without authorization on the specified date. "
            "Please provide your response within 7 days."
        ),
        "response_due_date": date.today() + timedelta(days=7),
    }


@pytest.fixture
def sample_schedule_hearing_data() -> dict[str, Any]:
    """Sample data for scheduling a hearing."""
    return {
        "hearing_date": datetime.now(UTC) + timedelta(days=5),
        "hearing_location": "Conference Room A",
        "panel_chair_id": uuid.uuid4(),
    }


@pytest.fixture
def sample_decision_data() -> dict[str, Any]:
    """Sample data for recording a decision."""
    return {
        "decision_summary": (
            "After reviewing the evidence and the employee's response, "
            "a written warning is issued for the first offense."
        ),
        "actions": [
            {
                "action_type": ActionType.WRITTEN_WARNING,
                "description": "First written warning for unauthorized absence.",
                "effective_date": date.today(),
                "warning_expiry_date": date.today() + timedelta(days=365),
            }
        ],
    }


@pytest.fixture
def sample_appeal_data() -> dict[str, Any]:
    """Sample data for filing an appeal."""
    return {
        "appeal_reason": (
            "I believe the decision was unfair because I had a medical "
            "emergency that prevented me from contacting the office."
        ),
    }
