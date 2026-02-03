"""
Discipline Service Unit Tests.

Tests for the DisciplineService business logic using mock objects.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.errors import NotFoundError, ValidationError
from app.models.people.discipline import (
    CaseStatus,
    ViolationType,
    SeverityLevel,
    ActionType,
    DocumentType,
)
from app.services.people.discipline import DisciplineService
from app.schemas.people.discipline import (
    DisciplinaryCaseCreate,
    DisciplinaryCaseUpdate,
    IssueQueryRequest,
    ScheduleHearingRequest,
    RecordDecisionRequest,
    FileAppealRequest,
    DecideAppealRequest,
    CaseActionCreate,
    CaseWitnessCreate,
    CaseResponseCreate,
    CaseListFilter,
)

from .conftest import (
    MockDisciplinaryCase,
    MockCaseAction,
    MockCaseWitness,
    MockCaseResponse,
    MockEmployee,
    create_mock_db_session,
)


# =============================================================================
# Case CRUD Tests
# =============================================================================


class TestCaseCRUD:
    """Tests for case create, read, update operations."""

    def test_get_case_returns_case_when_exists(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test getting a case that exists."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id, organization_id=organization_id
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        result = service.get_case(case_id)

        assert result == mock_case
        db.get.assert_called_once()

    def test_get_case_returns_none_when_not_exists(self, case_id: uuid.UUID):
        """Test getting a case that doesn't exist."""
        db = create_mock_db_session(get_returns={})

        service = DisciplineService(db)
        result = service.get_case(case_id)

        assert result is None

    def test_get_case_or_404_raises_when_not_exists(self, case_id: uuid.UUID):
        """Test get_case_or_404 raises NotFoundError when case doesn't exist."""
        db = create_mock_db_session(get_returns={})

        service = DisciplineService(db)

        with pytest.raises(NotFoundError, match="not found"):
            service.get_case_or_404(case_id)

    def test_create_case_success(
        self,
        organization_id: uuid.UUID,
        employee_id: uuid.UUID,
        sample_case_create_data: dict,
    ):
        """Test successful case creation."""
        mock_employee = MockEmployee(
            employee_id=employee_id, organization_id=organization_id
        )
        db = create_mock_db_session(
            get_returns={employee_id: mock_employee},
            scalar_returns=None,  # No existing case numbers
        )

        service = DisciplineService(db)
        data = DisciplinaryCaseCreate(
            employee_id=employee_id,
            violation_type=ViolationType.MISCONDUCT,
            severity=SeverityLevel.MODERATE,
            subject="Test violation",
            reported_date=date.today(),
        )

        result = service.create_case(organization_id, data)

        # Verify case was added and flushed
        db.add.assert_called_once()
        db.flush.assert_called_once()

        # The result is the added object (mocked)
        assert db.add.call_args is not None

    def test_create_case_employee_not_found_raises_error(
        self, organization_id: uuid.UUID
    ):
        """Test creating case with non-existent employee raises error."""
        db = create_mock_db_session(get_returns={})  # Employee not found

        service = DisciplineService(db)
        data = DisciplinaryCaseCreate(
            employee_id=uuid.uuid4(),
            violation_type=ViolationType.MISCONDUCT,
            severity=SeverityLevel.MODERATE,
            subject="Test violation",
            reported_date=date.today(),
        )

        with pytest.raises(ValidationError, match="not found"):
            service.create_case(organization_id, data)

    def test_create_case_employee_wrong_organization_raises_error(
        self, organization_id: uuid.UUID, employee_id: uuid.UUID
    ):
        """Test creating case for employee from different organization."""
        different_org = uuid.uuid4()
        mock_employee = MockEmployee(
            employee_id=employee_id, organization_id=different_org
        )
        db = create_mock_db_session(get_returns={employee_id: mock_employee})

        service = DisciplineService(db)
        data = DisciplinaryCaseCreate(
            employee_id=employee_id,
            violation_type=ViolationType.MISCONDUCT,
            severity=SeverityLevel.MODERATE,
            subject="Test violation",
            reported_date=date.today(),
        )

        with pytest.raises(ValidationError, match="does not belong"):
            service.create_case(organization_id, data)

    def test_update_case_in_draft_status_succeeds(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test updating a case in DRAFT status."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.DRAFT,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = DisciplinaryCaseUpdate(
            subject="Updated subject",
            severity=SeverityLevel.MAJOR,
        )

        result = service.update_case(case_id, data)

        assert result.subject == "Updated subject"
        assert result.severity == SeverityLevel.MAJOR

    def test_update_case_not_in_draft_raises_error(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test updating a case not in DRAFT status fails."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.QUERY_ISSUED,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = DisciplinaryCaseUpdate(subject="Updated subject")

        with pytest.raises(ValidationError, match="only update case details in DRAFT"):
            service.update_case(case_id, data)


# =============================================================================
# Workflow Transition Tests
# =============================================================================


class TestWorkflowTransitions:
    """Tests for case workflow state transitions."""

    def test_issue_query_from_draft_succeeds(
        self,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        mock_employee: MockEmployee,
    ):
        """Test issuing query from DRAFT status."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.DRAFT,
            employee=mock_employee,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        with patch.object(
            service, "_generate_case_number", return_value="DC-2026-0001"
        ):
            data = IssueQueryRequest(
                query_text="Please explain your actions.",
                response_due_date=date.today() + timedelta(days=7),
            )

            # Patch notification to avoid actual notification
            with patch(
                "app.services.people.discipline.discipline_service.notification_service"
            ):
                result = service.issue_query(case_id, data)

        assert result.status == CaseStatus.QUERY_ISSUED
        assert result.query_text == "Please explain your actions."
        assert result.response_due_date == date.today() + timedelta(days=7)
        db.flush.assert_called()

    def test_issue_query_not_from_draft_raises_error(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test issuing query from non-DRAFT status fails."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.QUERY_ISSUED,  # Already issued
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = IssueQueryRequest(
            query_text="Please explain.",
            response_due_date=date.today() + timedelta(days=7),
        )

        with pytest.raises(ValidationError, match="only issue query from DRAFT"):
            service.issue_query(case_id, data)

    def test_record_response_updates_status(
        self,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        mock_employee: MockEmployee,
    ):
        """Test recording response updates status to RESPONSE_RECEIVED."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.QUERY_ISSUED,
            employee=mock_employee,
            created_by_id=uuid.uuid4(),
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = CaseResponseCreate(response_text="I apologize for my actions.")

        with patch(
            "app.services.people.discipline.discipline_service.notification_service"
        ):
            result = service.record_response(case_id, data)

        assert mock_case.status == CaseStatus.RESPONSE_RECEIVED
        assert result.response_text == "I apologize for my actions."
        assert result.is_initial_response is True
        db.add.assert_called_once()

    def test_schedule_hearing_from_response_received(
        self,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        mock_employee: MockEmployee,
    ):
        """Test scheduling hearing from RESPONSE_RECEIVED status."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.RESPONSE_RECEIVED,
            employee=mock_employee,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        hearing_datetime = datetime.now(timezone.utc) + timedelta(days=5)
        service = DisciplineService(db)
        data = ScheduleHearingRequest(
            hearing_date=hearing_datetime,
            hearing_location="Conference Room A",
        )

        with patch(
            "app.services.people.discipline.discipline_service.notification_service"
        ):
            result = service.schedule_hearing(case_id, data)

        assert result.status == CaseStatus.HEARING_SCHEDULED
        assert result.hearing_date == hearing_datetime
        assert result.hearing_location == "Conference Room A"

    def test_record_hearing_notes_transitions_to_completed(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test recording hearing notes transitions to HEARING_COMPLETED."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.HEARING_SCHEDULED,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        result = service.record_hearing_notes(
            case_id,
            hearing_notes="Employee presented mitigating circumstances.",
        )

        assert result.status == CaseStatus.HEARING_COMPLETED
        assert result.hearing_notes == "Employee presented mitigating circumstances."

    def test_record_decision_creates_actions(
        self,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        mock_employee: MockEmployee,
    ):
        """Test recording decision creates action records."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.HEARING_COMPLETED,
            employee=mock_employee,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = RecordDecisionRequest(
            decision_summary="A written warning is issued.",
            actions=[
                CaseActionCreate(
                    action_type=ActionType.WRITTEN_WARNING,
                    description="First warning for misconduct.",
                    effective_date=date.today(),
                )
            ],
        )

        with patch(
            "app.services.people.discipline.discipline_service.notification_service"
        ):
            result = service.record_decision(case_id, data)

        assert result.status == CaseStatus.DECISION_MADE
        assert result.decision_summary == "A written warning is issued."
        assert result.appeal_deadline is not None
        # Action was added to db
        assert db.add.call_count >= 1

    def test_file_appeal_within_deadline(
        self,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        mock_employee: MockEmployee,
    ):
        """Test filing appeal within deadline succeeds."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.DECISION_MADE,
            appeal_deadline=date.today() + timedelta(days=7),
            employee=mock_employee,
            created_by_id=uuid.uuid4(),
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = FileAppealRequest(appeal_reason="The decision was too harsh.")

        with patch(
            "app.services.people.discipline.discipline_service.notification_service"
        ):
            result = service.file_appeal(case_id, data)

        assert result.status == CaseStatus.APPEAL_FILED
        assert result.appeal_reason == "The decision was too harsh."

    def test_file_appeal_after_deadline_raises_error(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test filing appeal after deadline fails."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.DECISION_MADE,
            appeal_deadline=date.today() - timedelta(days=1),  # Past deadline
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = FileAppealRequest(appeal_reason="Late appeal.")

        with pytest.raises(ValidationError, match="deadline has passed"):
            service.file_appeal(case_id, data)

    def test_decide_appeal_revises_actions(
        self,
        organization_id: uuid.UUID,
        case_id: uuid.UUID,
        mock_employee: MockEmployee,
    ):
        """Test appeal decision can revise original actions."""
        existing_action = MockCaseAction(
            case_id=case_id,
            action_type=ActionType.WRITTEN_WARNING,
            is_active=True,
        )
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.APPEAL_FILED,
            employee=mock_employee,
        )
        mock_case.actions = [existing_action]
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = DecideAppealRequest(
            appeal_decision="Appeal upheld. Original warning rescinded.",
            revised_actions=[
                CaseActionCreate(
                    action_type=ActionType.VERBAL_WARNING,
                    description="Reduced to verbal warning.",
                    effective_date=date.today(),
                )
            ],
        )

        with patch(
            "app.services.people.discipline.discipline_service.notification_service"
        ):
            result = service.decide_appeal(case_id, data)

        assert result.status == CaseStatus.APPEAL_DECIDED
        assert result.appeal_decision == "Appeal upheld. Original warning rescinded."
        # Original action should be deactivated
        assert existing_action.is_active is False

    def test_close_case_from_decision_made(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test closing case from DECISION_MADE status."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.DECISION_MADE,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        result = service.close_case(case_id)

        assert result.status == CaseStatus.CLOSED
        assert result.closed_date == date.today()

    def test_withdraw_case_from_open_status(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test withdrawing case from an open status."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.UNDER_INVESTIGATION,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        result = service.withdraw_case(case_id)

        assert result.status == CaseStatus.WITHDRAWN
        assert result.closed_date == date.today()

    def test_cannot_withdraw_closed_case(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test cannot withdraw already closed case."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.CLOSED,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)

        with pytest.raises(ValidationError, match="already closed or withdrawn"):
            service.withdraw_case(case_id)


# =============================================================================
# Witness Management Tests
# =============================================================================


class TestWitnessManagement:
    """Tests for witness-related operations."""

    def test_add_internal_witness(self, organization_id: uuid.UUID, case_id: uuid.UUID):
        """Test adding an internal (employee) witness."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
        )
        witness_employee_id = uuid.uuid4()
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = CaseWitnessCreate(
            employee_id=witness_employee_id,
            statement="I witnessed the incident.",
        )

        result = service.add_witness(case_id, data)

        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_add_external_witness(self, organization_id: uuid.UUID, case_id: uuid.UUID):
        """Test adding an external witness."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
        )
        db = create_mock_db_session(get_returns={case_id: mock_case})

        service = DisciplineService(db)
        data = CaseWitnessCreate(
            external_name="Jane External",
            external_contact="jane@external.com",
        )

        result = service.add_witness(case_id, data)

        db.add.assert_called_once()

    def test_record_witness_statement(self, case_id: uuid.UUID):
        """Test recording a witness statement."""
        witness_id = uuid.uuid4()
        mock_witness = MockCaseWitness(witness_id=witness_id, case_id=case_id)
        db = create_mock_db_session(get_returns={witness_id: mock_witness})

        service = DisciplineService(db)
        result = service.record_witness_statement(
            witness_id, "Detailed witness statement."
        )

        assert result.statement == "Detailed witness statement."
        assert result.statement_date is not None


# =============================================================================
# Integration Query Tests
# =============================================================================


class TestIntegrationQueries:
    """Tests for queries used by other modules (payroll, leave, etc.)."""

    def test_get_active_actions_for_employee(
        self, organization_id: uuid.UUID, employee_id: uuid.UUID
    ):
        """Test getting active disciplinary actions for an employee."""
        db = create_mock_db_session(
            scalars_returns=[
                MockCaseAction(action_type=ActionType.WRITTEN_WARNING),
            ]
        )

        service = DisciplineService(db)
        result = service.get_active_actions_for_employee(organization_id, employee_id)

        assert len(result) == 1
        assert result[0].action_type == ActionType.WRITTEN_WARNING

    def test_get_unpaid_suspensions_date_range(
        self, organization_id: uuid.UUID, employee_id: uuid.UUID
    ):
        """Test getting unpaid suspensions within date range."""
        suspension = MockCaseAction(
            action_type=ActionType.SUSPENSION_UNPAID,
            effective_date=date.today() - timedelta(days=5),
            end_date=date.today() + timedelta(days=5),
        )
        db = create_mock_db_session(scalars_returns=[suspension])

        service = DisciplineService(db)
        result = service.get_unpaid_suspensions(
            organization_id,
            employee_id,
            from_date=date.today() - timedelta(days=10),
            to_date=date.today() + timedelta(days=10),
        )

        assert len(result) == 1
        assert result[0].action_type == ActionType.SUSPENSION_UNPAID

    def test_has_active_investigation_returns_true(
        self, organization_id: uuid.UUID, employee_id: uuid.UUID
    ):
        """Test has_active_investigation returns True when investigation exists."""
        db = create_mock_db_session(scalar_returns=1)

        service = DisciplineService(db)
        result = service.has_active_investigation(organization_id, employee_id)

        assert result is True

    def test_has_active_investigation_closed_case_returns_false(
        self, organization_id: uuid.UUID, employee_id: uuid.UUID
    ):
        """Test has_active_investigation returns False for closed cases only."""
        db = create_mock_db_session(scalar_returns=0)

        service = DisciplineService(db)
        result = service.has_active_investigation(organization_id, employee_id)

        assert result is False


# =============================================================================
# Case Number Generation Tests
# =============================================================================


class TestCaseNumberGeneration:
    """Tests for case number generation."""

    def test_generates_sequential_numbers(self, organization_id: uuid.UUID):
        """Test case numbers are generated sequentially."""
        year = date.today().year
        db = create_mock_db_session(scalar_returns=f"DC-{year}-0005")

        service = DisciplineService(db)
        result = service._generate_case_number(organization_id)

        assert result == f"DC-{year}-0006"

    def test_starts_at_0001_when_no_existing(self, organization_id: uuid.UUID):
        """Test first case of year starts at 0001."""
        year = date.today().year
        db = create_mock_db_session(scalar_returns=None)

        service = DisciplineService(db)
        result = service._generate_case_number(organization_id)

        assert result == f"DC-{year}-0001"

    def test_handles_malformed_case_number(self, organization_id: uuid.UUID):
        """Test handles malformed existing case numbers gracefully."""
        db = create_mock_db_session(scalar_returns="DC-INVALID")

        service = DisciplineService(db)
        result = service._generate_case_number(organization_id)

        year = date.today().year
        assert result == f"DC-{year}-0001"


# =============================================================================
# Status Validation Tests
# =============================================================================


class TestStatusValidation:
    """Tests for status transition validation."""

    def test_valid_transitions_from_draft(
        self, organization_id: uuid.UUID, case_id: uuid.UUID
    ):
        """Test valid transitions from DRAFT status."""
        mock_case = MockDisciplinaryCase(
            case_id=case_id,
            organization_id=organization_id,
            status=CaseStatus.DRAFT,
        )

        service = DisciplineService(MagicMock())

        # Should not raise for valid transitions
        service._validate_transition(CaseStatus.DRAFT, CaseStatus.QUERY_ISSUED)
        service._validate_transition(CaseStatus.DRAFT, CaseStatus.WITHDRAWN)

    def test_invalid_transition_raises_error(self):
        """Test invalid status transition raises ValidationError."""
        service = DisciplineService(MagicMock())

        with pytest.raises(ValidationError, match="Cannot transition"):
            service._validate_transition(CaseStatus.DRAFT, CaseStatus.CLOSED)

    def test_cannot_transition_from_closed(self):
        """Test no transitions allowed from CLOSED status."""
        service = DisciplineService(MagicMock())

        with pytest.raises(ValidationError, match="Cannot transition"):
            service._validate_transition(CaseStatus.CLOSED, CaseStatus.APPEAL_FILED)


# =============================================================================
# List Cases Tests
# =============================================================================


class TestListCases:
    """Tests for listing cases with filters."""

    def test_list_cases_with_status_filter(self, organization_id: uuid.UUID):
        """Test listing cases filtered by status."""
        mock_cases = [
            MockDisciplinaryCase(
                organization_id=organization_id,
                status=CaseStatus.QUERY_ISSUED,
            )
        ]
        mock_result = MagicMock()
        mock_result.all.return_value = mock_cases

        db = MagicMock()
        db.scalars.return_value = mock_result
        db.scalar.return_value = 1

        service = DisciplineService(db)
        filters = CaseListFilter(status=CaseStatus.QUERY_ISSUED)

        cases, total = service.list_cases(organization_id, filters=filters)

        assert len(cases) == 1
        assert total == 1

    def test_list_employee_cases_excludes_closed_by_default(
        self, organization_id: uuid.UUID, employee_id: uuid.UUID
    ):
        """Test list_employee_cases excludes closed cases by default."""
        open_case = MockDisciplinaryCase(
            employee_id=employee_id,
            status=CaseStatus.QUERY_ISSUED,
        )
        mock_result = MagicMock()
        mock_result.all.return_value = [open_case]

        db = MagicMock()
        db.scalars.return_value = mock_result
        db.scalar.return_value = 1

        service = DisciplineService(db)
        cases, total = service.list_employee_cases(
            organization_id, employee_id, include_closed=False
        )

        assert len(cases) == 1
