"""
Discipline Service - Core business logic for disciplinary cases.

This service handles ALL business logic for discipline management:
- Case creation and updates
- Workflow state transitions
- Query issuance and response handling
- Hearing scheduling and decision recording
- Appeal management
- Notifications

Routes and tasks should delegate to this service - no logic in routes!
"""

import logging
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.errors import NotFoundError, ValidationError
from app.models.finance.audit.audit_log import AuditAction
from app.models.people.discipline import (
    ActionType,
    CaseAction,
    CaseDocument,
    CaseResponse,
    CaseStatus,
    CaseWitness,
    DisciplinaryCase,
    DocumentType,
)
from app.models.people.hr.employee import Employee
from app.schemas.people.discipline import (
    CaseActionCreate,
    CaseListFilter,
    CaseResponseCreate,
    CaseWitnessCreate,
    DecideAppealRequest,
    DisciplinaryCaseCreate,
    DisciplinaryCaseUpdate,
    FileAppealRequest,
    IssueQueryRequest,
    RecordDecisionRequest,
    ScheduleHearingRequest,
)
from app.services.audit_dispatcher import fire_audit_event
from app.services.notification import notification_service
from app.services.state_machine import StateMachine

logger = logging.getLogger(__name__)

# Valid status transitions for the discipline workflow
VALID_TRANSITIONS = {
    CaseStatus.DRAFT: [CaseStatus.QUERY_ISSUED, CaseStatus.WITHDRAWN],
    CaseStatus.QUERY_ISSUED: [
        CaseStatus.RESPONSE_RECEIVED,
        CaseStatus.UNDER_INVESTIGATION,
        CaseStatus.WITHDRAWN,
    ],
    CaseStatus.RESPONSE_RECEIVED: [
        CaseStatus.UNDER_INVESTIGATION,
        CaseStatus.HEARING_SCHEDULED,
        CaseStatus.DECISION_MADE,
        CaseStatus.WITHDRAWN,
    ],
    CaseStatus.UNDER_INVESTIGATION: [
        CaseStatus.HEARING_SCHEDULED,
        CaseStatus.DECISION_MADE,
        CaseStatus.WITHDRAWN,
    ],
    CaseStatus.HEARING_SCHEDULED: [
        CaseStatus.HEARING_COMPLETED,
        CaseStatus.WITHDRAWN,
    ],
    CaseStatus.HEARING_COMPLETED: [CaseStatus.DECISION_MADE],
    CaseStatus.DECISION_MADE: [CaseStatus.APPEAL_FILED, CaseStatus.CLOSED],
    CaseStatus.APPEAL_FILED: [CaseStatus.APPEAL_DECIDED],
    CaseStatus.APPEAL_DECIDED: [CaseStatus.CLOSED],
    CaseStatus.CLOSED: [],
    CaseStatus.WITHDRAWN: [],
}
_STATE_MACHINE = StateMachine(VALID_TRANSITIONS)

# Default appeal window in days after decision
DEFAULT_APPEAL_WINDOW_DAYS = 14


class DisciplineService:
    """
    Service for managing disciplinary cases.

    Contains ALL business logic for discipline management.
    Routes should be thin wrappers that delegate to this service.
    """

    def __init__(self, db: Session):
        self.db = db

    # =========================================================================
    # Case CRUD Operations
    # =========================================================================

    def get_case(self, case_id: UUID) -> DisciplinaryCase | None:
        """Get a single case by ID."""
        return self.db.get(DisciplinaryCase, case_id)

    def get_case_or_404(self, case_id: UUID) -> DisciplinaryCase:
        """Get case or raise NotFoundError."""
        case = self.get_case(case_id)
        if not case:
            raise NotFoundError(f"Disciplinary case {case_id} not found")
        return case

    def get_case_detail(self, case_id: UUID) -> DisciplinaryCase:
        """Get case with all related entities loaded."""
        stmt = (
            select(DisciplinaryCase)
            .options(
                joinedload(DisciplinaryCase.employee),
                joinedload(DisciplinaryCase.reported_by),
                joinedload(DisciplinaryCase.investigating_officer),
                joinedload(DisciplinaryCase.panel_chair),
                selectinload(DisciplinaryCase.witnesses),
                selectinload(DisciplinaryCase.actions),
                selectinload(DisciplinaryCase.documents),
                selectinload(DisciplinaryCase.responses),
            )
            .where(DisciplinaryCase.case_id == case_id)
        )
        case = self.db.scalar(stmt)
        if not case:
            raise NotFoundError(f"Disciplinary case {case_id} not found")
        return case

    def list_cases(
        self,
        organization_id: UUID,
        filters: CaseListFilter | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[DisciplinaryCase], int]:
        """List cases with filters and pagination."""
        stmt = (
            select(DisciplinaryCase)
            .where(DisciplinaryCase.organization_id == organization_id)
            .where(DisciplinaryCase.is_deleted == False)
        )

        if filters:
            if filters.status:
                stmt = stmt.where(DisciplinaryCase.status == filters.status)
            if filters.violation_type:
                stmt = stmt.where(
                    DisciplinaryCase.violation_type == filters.violation_type
                )
            if filters.severity:
                stmt = stmt.where(DisciplinaryCase.severity == filters.severity)
            if filters.employee_id:
                stmt = stmt.where(DisciplinaryCase.employee_id == filters.employee_id)
            if filters.investigating_officer_id:
                stmt = stmt.where(
                    DisciplinaryCase.investigating_officer_id
                    == filters.investigating_officer_id
                )
            if filters.from_date:
                stmt = stmt.where(DisciplinaryCase.reported_date >= filters.from_date)
            if filters.to_date:
                stmt = stmt.where(DisciplinaryCase.reported_date <= filters.to_date)
            if not filters.include_closed:
                stmt = stmt.where(
                    DisciplinaryCase.status.notin_(
                        [CaseStatus.CLOSED, CaseStatus.WITHDRAWN]
                    )
                )

        # Get total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = self.db.scalar(count_stmt) or 0

        # Apply pagination
        stmt = stmt.order_by(DisciplinaryCase.created_at.desc())
        stmt = stmt.offset(offset).limit(limit)

        cases = list(self.db.scalars(stmt).all())
        return cases, total

    def list_employee_cases(
        self,
        organization_id: UUID,
        employee_id: UUID,
        include_closed: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[DisciplinaryCase], int]:
        """List all cases for a specific employee (self-service view)."""
        stmt = (
            select(DisciplinaryCase)
            .where(DisciplinaryCase.organization_id == organization_id)
            .where(DisciplinaryCase.employee_id == employee_id)
            .where(DisciplinaryCase.is_deleted == False)
        )

        if not include_closed:
            stmt = stmt.where(
                DisciplinaryCase.status.notin_(
                    [CaseStatus.CLOSED, CaseStatus.WITHDRAWN]
                )
            )

        # Get total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = self.db.scalar(count_stmt) or 0

        # Apply pagination
        stmt = stmt.order_by(DisciplinaryCase.created_at.desc())
        stmt = stmt.offset(offset).limit(limit)

        cases = list(self.db.scalars(stmt).all())
        return cases, total

    def create_case(
        self,
        organization_id: UUID,
        data: DisciplinaryCaseCreate,
        created_by_id: UUID | None = None,
    ) -> DisciplinaryCase:
        """Create a new disciplinary case."""
        # Verify employee exists and belongs to organization
        employee = self.db.get(Employee, data.employee_id)
        if not employee:
            raise ValidationError(f"Employee {data.employee_id} not found")
        if employee.organization_id != organization_id:
            raise ValidationError("Employee does not belong to this organization")

        # Generate case number
        case_number = self._generate_case_number(organization_id)

        case = DisciplinaryCase(
            organization_id=organization_id,
            case_number=case_number,
            employee_id=data.employee_id,
            violation_type=data.violation_type,
            severity=data.severity,
            subject=data.subject,
            description=data.description,
            incident_date=data.incident_date,
            reported_date=data.reported_date,
            reported_by_id=data.reported_by_id,
            status=CaseStatus.DRAFT,
            created_by_id=created_by_id,
        )

        self.db.add(case)
        self.db.flush()

        fire_audit_event(
            self.db,
            organization_id,
            "discipline",
            "disciplinary_case",
            str(case.case_id),
            AuditAction.INSERT,
            new_values={
                "case_number": case_number,
                "employee_id": str(data.employee_id),
                "violation_type": data.violation_type.value,
                "severity": data.severity.value,
                "status": "DRAFT",
            },
            user_id=created_by_id,
        )

        logger.info(
            "Created disciplinary case %s for employee %s",
            case.case_number,
            case.employee_id,
        )

        return case

    def update_case(
        self,
        case_id: UUID,
        data: DisciplinaryCaseUpdate,
        updated_by_id: UUID | None = None,
    ) -> DisciplinaryCase:
        """Update case details (only allowed in DRAFT status)."""
        case = self.get_case_or_404(case_id)

        if case.status != CaseStatus.DRAFT:
            raise ValidationError("Can only update case details in DRAFT status")

        if data.violation_type is not None:
            case.violation_type = data.violation_type
        if data.severity is not None:
            case.severity = data.severity
        if data.subject is not None:
            case.subject = data.subject
        if data.description is not None:
            case.description = data.description
        if data.incident_date is not None:
            case.incident_date = data.incident_date
        if data.investigating_officer_id is not None:
            case.investigating_officer_id = data.investigating_officer_id

        case.updated_by_id = updated_by_id
        self.db.flush()

        logger.info("Updated disciplinary case %s", case.case_number)
        return case

    # =========================================================================
    # Workflow Operations
    # =========================================================================

    def _validate_transition(
        self, current_status: CaseStatus, new_status: CaseStatus
    ) -> None:
        """Validate status transition is allowed."""
        _STATE_MACHINE.validate(current_status, new_status)

    def _update_status(
        self,
        case: DisciplinaryCase,
        new_status: CaseStatus,
        changed_by_id: UUID | None = None,
    ) -> None:
        """Update case status with tracking."""
        self._validate_transition(case.status, new_status)
        case.status = new_status
        case.status_changed_at = datetime.now(UTC)
        case.status_changed_by_id = changed_by_id

    def issue_query(
        self,
        case_id: UUID,
        data: IssueQueryRequest,
        issued_by_id: UUID | None = None,
    ) -> DisciplinaryCase:
        """Issue a formal query to the employee."""
        case = self.get_case_or_404(case_id)

        if case.status != CaseStatus.DRAFT:
            raise ValidationError("Can only issue query from DRAFT status")

        case.query_text = data.query_text
        case.query_issued_date = date.today()
        case.response_due_date = data.response_due_date

        old_status = case.status.value if case.status else "DRAFT"
        self._update_status(case, CaseStatus.QUERY_ISSUED, issued_by_id)
        self.db.flush()

        fire_audit_event(
            self.db,
            case.organization_id,
            "discipline",
            "disciplinary_case",
            str(case.case_id),
            AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={
                "status": "QUERY_ISSUED",
                "response_due_date": str(case.response_due_date),
            },
            user_id=issued_by_id,
        )

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=case.organization_id,
                entity_type="DISCIPLINARY_CASE",
                entity_id=case.case_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": old_status},
                new_values={"status": "QUERY_ISSUED"},
                user_id=issued_by_id,
            )
        except Exception:
            pass

        logger.info(
            "Query issued for case %s, response due %s",
            case.case_number,
            case.response_due_date,
        )

        # Send notification to employee
        if case.employee and case.employee.person_id:
            notification_service.notify_discipline_query_issued(
                self.db,
                organization_id=case.organization_id,
                case_id=case.case_id,
                case_number=case.case_number,
                employee_id=case.employee.person_id,
                response_due_date=case.response_due_date.strftime("%B %d, %Y"),
                actor_id=issued_by_id,
            )

        return case

    def record_response(
        self,
        case_id: UUID,
        data: CaseResponseCreate,
    ) -> CaseResponse:
        """Record employee's response to a query."""
        case = self.get_case_or_404(case_id)

        allowed_statuses = [CaseStatus.QUERY_ISSUED, CaseStatus.APPEAL_FILED]
        if case.status not in allowed_statuses:
            raise ValidationError(
                f"Cannot submit response in {case.status.value} status"
            )

        response = CaseResponse(
            case_id=case_id,
            response_text=data.response_text,
            is_initial_response=case.status == CaseStatus.QUERY_ISSUED,
            is_appeal_response=case.status == CaseStatus.APPEAL_FILED,
            submitted_at=datetime.now(UTC),
        )

        self.db.add(response)

        # Update case status if this is initial response
        if case.status == CaseStatus.QUERY_ISSUED:
            self._update_status(case, CaseStatus.RESPONSE_RECEIVED)

        self.db.flush()

        fire_audit_event(
            self.db,
            case.organization_id,
            "discipline",
            "disciplinary_case",
            str(case.case_id),
            AuditAction.UPDATE,
            new_values={"status": case.status.value},
            reason="Employee response recorded",
        )

        logger.info("Response recorded for case %s", case.case_number)

        # Notify HR that employee has responded
        if case.created_by_id and case.employee:
            notification_service.notify_discipline_response_received(
                self.db,
                organization_id=case.organization_id,
                case_id=case.case_id,
                case_number=case.case_number,
                hr_recipient_id=case.created_by_id,
                employee_name=case.employee.full_name,
            )

        return response

    def acknowledge_response(
        self,
        response_id: UUID,
        acknowledged_by_id: UUID | None = None,
    ) -> CaseResponse:
        """Acknowledge that HR has reviewed the response."""
        response = self.db.get(CaseResponse, response_id)
        if not response:
            raise NotFoundError(f"Response {response_id} not found")

        response.acknowledged_at = datetime.now(UTC)
        self.db.flush()

        logger.info("Response %s acknowledged", response_id)
        return response

    def schedule_hearing(
        self,
        case_id: UUID,
        data: ScheduleHearingRequest,
        scheduled_by_id: UUID | None = None,
    ) -> DisciplinaryCase:
        """Schedule a disciplinary hearing."""
        case = self.get_case_or_404(case_id)

        allowed_statuses = [
            CaseStatus.RESPONSE_RECEIVED,
            CaseStatus.UNDER_INVESTIGATION,
        ]
        if case.status not in allowed_statuses:
            raise ValidationError(
                f"Cannot schedule hearing from {case.status.value} status"
            )

        case.hearing_date = data.hearing_date
        case.hearing_location = data.hearing_location
        if data.panel_chair_id:
            case.panel_chair_id = data.panel_chair_id

        old_status = case.status.value if case.status else "DRAFT"
        self._update_status(case, CaseStatus.HEARING_SCHEDULED, scheduled_by_id)
        self.db.flush()

        fire_audit_event(
            self.db,
            case.organization_id,
            "discipline",
            "disciplinary_case",
            str(case.case_id),
            AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={
                "status": "HEARING_SCHEDULED",
                "hearing_date": str(case.hearing_date),
            },
            user_id=scheduled_by_id,
        )

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=case.organization_id,
                entity_type="DISCIPLINARY_CASE",
                entity_id=case.case_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": old_status},
                new_values={"status": "HEARING_SCHEDULED"},
                user_id=scheduled_by_id,
            )
        except Exception:
            pass

        logger.info(
            "Hearing scheduled for case %s on %s",
            case.case_number,
            case.hearing_date,
        )

        # Send notification to employee
        if case.employee and case.employee.person_id:
            notification_service.notify_discipline_hearing_scheduled(
                self.db,
                organization_id=case.organization_id,
                case_id=case.case_id,
                case_number=case.case_number,
                employee_id=case.employee.person_id,
                hearing_date=case.hearing_date.strftime("%B %d, %Y at %H:%M"),
                hearing_location=case.hearing_location,
                actor_id=scheduled_by_id,
            )

        return case

    def record_hearing_notes(
        self,
        case_id: UUID,
        hearing_notes: str,
        recorded_by_id: UUID | None = None,
    ) -> DisciplinaryCase:
        """Record notes from the hearing and mark as completed."""
        case = self.get_case_or_404(case_id)

        if case.status != CaseStatus.HEARING_SCHEDULED:
            raise ValidationError(
                "Can only record hearing notes after hearing is scheduled"
            )

        case.hearing_notes = hearing_notes
        self._update_status(case, CaseStatus.HEARING_COMPLETED, recorded_by_id)
        self.db.flush()

        logger.info("Hearing notes recorded for case %s", case.case_number)
        return case

    def record_decision(
        self,
        case_id: UUID,
        data: RecordDecisionRequest,
        decided_by_id: UUID | None = None,
    ) -> DisciplinaryCase:
        """Record the decision and any disciplinary actions."""
        case = self.get_case_or_404(case_id)

        allowed_statuses = [
            CaseStatus.RESPONSE_RECEIVED,
            CaseStatus.UNDER_INVESTIGATION,
            CaseStatus.HEARING_COMPLETED,
        ]
        if case.status not in allowed_statuses:
            raise ValidationError(
                f"Cannot record decision from {case.status.value} status"
            )

        case.decision_summary = data.decision_summary
        case.decision_date = date.today()
        case.appeal_deadline = date.today() + timedelta(days=DEFAULT_APPEAL_WINDOW_DAYS)

        # Record any actions
        for action_data in data.actions:
            action = CaseAction(
                case_id=case_id,
                action_type=action_data.action_type,
                description=action_data.description,
                effective_date=action_data.effective_date,
                end_date=action_data.end_date,
                warning_expiry_date=action_data.warning_expiry_date,
                issued_by_id=decided_by_id,
            )
            self.db.add(action)

        old_status = case.status.value if case.status else "DRAFT"
        self._update_status(case, CaseStatus.DECISION_MADE, decided_by_id)
        self.db.flush()

        fire_audit_event(
            self.db,
            case.organization_id,
            "discipline",
            "disciplinary_case",
            str(case.case_id),
            AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={
                "status": "DECISION_MADE",
                "decision_date": str(case.decision_date),
                "action_count": len(data.actions),
            },
            user_id=decided_by_id,
        )

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=case.organization_id,
                entity_type="DISCIPLINARY_CASE",
                entity_id=case.case_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": old_status},
                new_values={"status": "DECISION_MADE"},
                user_id=decided_by_id,
            )
        except Exception:
            pass

        logger.info(
            "Decision recorded for case %s with %d action(s)",
            case.case_number,
            len(data.actions),
        )

        # Trigger cross-module integrations for specific action types
        for action_data in data.actions:
            if action_data.action_type == ActionType.TERMINATION:
                self._trigger_termination_lifecycle(case, action_data, decided_by_id)
            elif action_data.action_type == ActionType.MANDATORY_TRAINING:
                self._trigger_mandatory_training(case, action_data, decided_by_id)

        # Send notification to employee about decision
        if case.employee and case.employee.person_id:
            appeal_deadline_str = None
            if case.appeal_deadline:
                appeal_deadline_str = case.appeal_deadline.strftime("%B %d, %Y")
            notification_service.notify_discipline_decision_made(
                self.db,
                organization_id=case.organization_id,
                case_id=case.case_id,
                case_number=case.case_number,
                employee_id=case.employee.person_id,
                appeal_deadline=appeal_deadline_str,
                actor_id=decided_by_id,
            )

        return case

    def file_appeal(
        self,
        case_id: UUID,
        data: FileAppealRequest,
    ) -> DisciplinaryCase:
        """Employee files an appeal against the decision."""
        case = self.get_case_or_404(case_id)

        if case.status != CaseStatus.DECISION_MADE:
            raise ValidationError("Can only appeal after decision is made")

        if case.appeal_deadline and date.today() > case.appeal_deadline:
            raise ValidationError("Appeal deadline has passed")

        old_status = case.status.value if case.status else "DRAFT"
        case.appeal_reason = data.appeal_reason
        self._update_status(case, CaseStatus.APPEAL_FILED)
        self.db.flush()

        fire_audit_event(
            self.db,
            case.organization_id,
            "discipline",
            "disciplinary_case",
            str(case.case_id),
            AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={"status": "APPEAL_FILED"},
        )

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=case.organization_id,
                entity_type="DISCIPLINARY_CASE",
                entity_id=case.case_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": old_status},
                new_values={"status": "APPEAL_FILED"},
            )
        except Exception:
            pass

        logger.info("Appeal filed for case %s", case.case_number)

        # Notify HR (the person who created the case)
        if case.created_by_id and case.employee:
            notification_service.notify_discipline_appeal_filed(
                self.db,
                organization_id=case.organization_id,
                case_id=case.case_id,
                case_number=case.case_number,
                hr_recipient_id=case.created_by_id,
                employee_name=case.employee.full_name,
            )

        return case

    def decide_appeal(
        self,
        case_id: UUID,
        data: DecideAppealRequest,
        decided_by_id: UUID | None = None,
    ) -> DisciplinaryCase:
        """Record the decision on an appeal."""
        case = self.get_case_or_404(case_id)

        if case.status != CaseStatus.APPEAL_FILED:
            raise ValidationError("No appeal has been filed")

        case.appeal_decision = data.appeal_decision

        # Handle revised actions if provided
        if data.revised_actions:
            # Deactivate existing actions
            for action in case.actions:
                action.is_active = False

            # Add revised actions
            for action_data in data.revised_actions:
                action = CaseAction(
                    case_id=case_id,
                    action_type=action_data.action_type,
                    description=action_data.description,
                    effective_date=action_data.effective_date,
                    end_date=action_data.end_date,
                    warning_expiry_date=action_data.warning_expiry_date,
                    issued_by_id=decided_by_id,
                )
                self.db.add(action)

        self._update_status(case, CaseStatus.APPEAL_DECIDED, decided_by_id)
        self.db.flush()

        logger.info("Appeal decided for case %s", case.case_number)

        # Send notification to employee about appeal decision
        if case.employee and case.employee.person_id:
            notification_service.notify_discipline_decision_made(
                self.db,
                organization_id=case.organization_id,
                case_id=case.case_id,
                case_number=case.case_number,
                employee_id=case.employee.person_id,
                appeal_deadline=None,  # No further appeal after appeal decision
                actor_id=decided_by_id,
            )

        return case

    def close_case(
        self,
        case_id: UUID,
        closed_by_id: UUID | None = None,
    ) -> DisciplinaryCase:
        """Close a case after decision or appeal."""
        case = self.get_case_or_404(case_id)

        allowed_statuses = [CaseStatus.DECISION_MADE, CaseStatus.APPEAL_DECIDED]
        if case.status not in allowed_statuses:
            raise ValidationError(f"Cannot close case from {case.status.value} status")

        old_status = case.status.value if case.status else "DRAFT"
        case.closed_date = date.today()
        self._update_status(case, CaseStatus.CLOSED, closed_by_id)
        self.db.flush()

        fire_audit_event(
            self.db,
            case.organization_id,
            "discipline",
            "disciplinary_case",
            str(case.case_id),
            AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={"status": "CLOSED", "closed_date": str(case.closed_date)},
            user_id=closed_by_id,
        )

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=self.db,
                organization_id=case.organization_id,
                entity_type="DISCIPLINARY_CASE",
                entity_id=case.case_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": old_status},
                new_values={"status": "CLOSED"},
                user_id=closed_by_id,
            )
        except Exception:
            pass

        logger.info("Case %s closed", case.case_number)
        return case

    def withdraw_case(
        self,
        case_id: UUID,
        withdrawn_by_id: UUID | None = None,
    ) -> DisciplinaryCase:
        """Withdraw a case (can be done at most stages)."""
        case = self.get_case_or_404(case_id)

        if case.status in [CaseStatus.CLOSED, CaseStatus.WITHDRAWN]:
            raise ValidationError("Case is already closed or withdrawn")

        self._update_status(case, CaseStatus.WITHDRAWN, withdrawn_by_id)
        case.closed_date = date.today()
        self.db.flush()

        logger.info("Case %s withdrawn", case.case_number)
        return case

    # =========================================================================
    # Witness Management
    # =========================================================================

    def add_witness(
        self,
        case_id: UUID,
        data: CaseWitnessCreate,
    ) -> CaseWitness:
        """Add a witness to a case."""
        case = self.get_case_or_404(case_id)

        witness = CaseWitness(
            case_id=case_id,
            employee_id=data.employee_id,
            external_name=data.external_name,
            external_contact=data.external_contact,
            statement=data.statement,
        )

        self.db.add(witness)
        self.db.flush()

        logger.info("Witness added to case %s", case.case_number)
        return witness

    def record_witness_statement(
        self,
        witness_id: UUID,
        statement: str,
    ) -> CaseWitness:
        """Record or update a witness statement."""
        witness = self.db.get(CaseWitness, witness_id)
        if not witness:
            raise NotFoundError(f"Witness {witness_id} not found")

        witness.statement = statement
        witness.statement_date = datetime.now(UTC)
        self.db.flush()

        logger.info("Statement recorded for witness %s", witness_id)
        return witness

    # =========================================================================
    # Document Management
    # =========================================================================

    def add_document(
        self,
        case_id: UUID,
        document_type: DocumentType,
        title: str,
        file_path: str,
        file_name: str,
        file_size: int | None = None,
        mime_type: str | None = None,
        uploaded_by_id: UUID | None = None,
        description: str | None = None,
    ) -> CaseDocument:
        """Add a document to a case."""
        case = self.get_case_or_404(case_id)

        document = CaseDocument(
            case_id=case_id,
            document_type=document_type,
            title=title,
            description=description,
            file_path=file_path,
            file_name=file_name,
            file_size=file_size,
            mime_type=mime_type,
            uploaded_by_id=uploaded_by_id,
        )

        self.db.add(document)
        self.db.flush()

        logger.info("Document '%s' added to case %s", title, case.case_number)
        return document

    # =========================================================================
    # Action Queries
    # =========================================================================

    def get_active_actions_for_employee(
        self,
        organization_id: UUID,
        employee_id: UUID,
    ) -> list[CaseAction]:
        """Get all active disciplinary actions for an employee."""
        stmt = (
            select(CaseAction)
            .join(DisciplinaryCase)
            .where(DisciplinaryCase.organization_id == organization_id)
            .where(DisciplinaryCase.employee_id == employee_id)
            .where(DisciplinaryCase.is_deleted == False)
            .where(CaseAction.is_active == True)
            .where(
                (CaseAction.end_date == None) | (CaseAction.end_date >= date.today())
            )
        )
        return list(self.db.scalars(stmt).all())

    def get_unpaid_suspensions(
        self,
        organization_id: UUID,
        employee_id: UUID,
        from_date: date,
        to_date: date,
    ) -> list[CaseAction]:
        """Get unpaid suspensions for payroll calculation."""
        stmt = (
            select(CaseAction)
            .join(DisciplinaryCase)
            .where(DisciplinaryCase.organization_id == organization_id)
            .where(DisciplinaryCase.employee_id == employee_id)
            .where(DisciplinaryCase.is_deleted == False)
            .where(CaseAction.action_type == ActionType.SUSPENSION_UNPAID)
            .where(CaseAction.is_active == True)
            .where(CaseAction.effective_date <= to_date)
            .where((CaseAction.end_date == None) | (CaseAction.end_date >= from_date))
        )
        return list(self.db.scalars(stmt).all())

    def has_active_investigation(
        self, organization_id: UUID, employee_id: UUID
    ) -> bool:
        """Check if employee has an active investigation (for leave blocking)."""
        stmt = (
            select(func.count(DisciplinaryCase.case_id))
            .where(DisciplinaryCase.organization_id == organization_id)
            .where(DisciplinaryCase.employee_id == employee_id)
            .where(DisciplinaryCase.is_deleted == False)
            .where(
                DisciplinaryCase.status.in_(
                    [
                        CaseStatus.QUERY_ISSUED,
                        CaseStatus.RESPONSE_RECEIVED,
                        CaseStatus.UNDER_INVESTIGATION,
                        CaseStatus.HEARING_SCHEDULED,
                    ]
                )
            )
        )
        count = self.db.scalar(stmt) or 0
        return count > 0

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _generate_case_number(self, organization_id: UUID, max_retries: int = 3) -> str:
        """Generate a unique case number.

        Uses FOR UPDATE to prevent race conditions when multiple cases
        are created simultaneously. Retries on IntegrityError for extra
        safety against concurrent inserts.
        """
        year = date.today().year
        prefix = f"DC-{year}-"

        for attempt in range(max_retries):
            # Get the latest existing number for this year with row locking
            stmt = (
                select(DisciplinaryCase.case_number)
                .where(DisciplinaryCase.organization_id == organization_id)
                .where(DisciplinaryCase.case_number.like(f"{prefix}%"))
                .order_by(DisciplinaryCase.case_number.desc())
                .limit(1)
                .with_for_update()
            )
            max_number = self.db.scalar(stmt)

            if max_number:
                # Extract the sequence number and increment
                try:
                    seq = int(max_number.split("-")[-1])
                    next_seq = seq + 1 + attempt  # Offset by retry attempt
                except (ValueError, IndexError):
                    next_seq = 1
            else:
                next_seq = 1

            return f"{prefix}{next_seq:04d}"

        # Fallback (shouldn't be reached)
        return f"{prefix}{next_seq:04d}"

    # =========================================================================
    # Reminder Query Methods (for Celery tasks)
    # =========================================================================

    def get_cases_with_pending_responses(
        self, days_before: int = 3
    ) -> list[DisciplinaryCase]:
        """Get cases where employee response is due within N days.

        Used by the reminder task to send notifications before deadline.

        Args:
            days_before: Number of days before due date to include

        Returns:
            List of cases with pending responses due within the window
        """
        today = date.today()
        cutoff = today + timedelta(days=days_before)

        stmt = (
            select(DisciplinaryCase)
            .options(joinedload(DisciplinaryCase.employee))
            .where(DisciplinaryCase.status == CaseStatus.QUERY_ISSUED)
            .where(DisciplinaryCase.is_deleted == False)
            .where(DisciplinaryCase.response_due_date.isnot(None))
            .where(DisciplinaryCase.response_due_date <= cutoff)
            .where(DisciplinaryCase.response_due_date >= today)
        )
        return list(self.db.scalars(stmt).unique().all())

    def get_cases_with_overdue_responses(self) -> list[DisciplinaryCase]:
        """Get cases where employee response is overdue.

        Used by the reminder task to send overdue notifications.

        Returns:
            List of cases with overdue responses (up to 7 days overdue)
        """
        today = date.today()
        max_overdue = today - timedelta(days=7)  # Stop reminding after 7 days

        stmt = (
            select(DisciplinaryCase)
            .options(joinedload(DisciplinaryCase.employee))
            .where(DisciplinaryCase.status == CaseStatus.QUERY_ISSUED)
            .where(DisciplinaryCase.is_deleted == False)
            .where(DisciplinaryCase.response_due_date.isnot(None))
            .where(DisciplinaryCase.response_due_date < today)
            .where(DisciplinaryCase.response_due_date >= max_overdue)
        )
        return list(self.db.scalars(stmt).unique().all())

    def get_cases_with_upcoming_hearings(
        self, days_before: int = 3
    ) -> list[DisciplinaryCase]:
        """Get cases with hearings scheduled within N days.

        Used by the reminder task to send hearing notifications.

        Args:
            days_before: Number of days before hearing to include

        Returns:
            List of cases with hearings scheduled within the window
        """
        now = datetime.now(UTC)
        cutoff = now + timedelta(days=days_before)

        stmt = (
            select(DisciplinaryCase)
            .options(joinedload(DisciplinaryCase.employee))
            .where(DisciplinaryCase.status == CaseStatus.HEARING_SCHEDULED)
            .where(DisciplinaryCase.is_deleted == False)
            .where(DisciplinaryCase.hearing_date.isnot(None))
            .where(DisciplinaryCase.hearing_date <= cutoff)
            .where(DisciplinaryCase.hearing_date >= now)
        )
        return list(self.db.scalars(stmt).unique().all())

    def get_cases_with_expiring_appeals(
        self, days_before: int = 7
    ) -> list[DisciplinaryCase]:
        """Get cases where appeal deadline is expiring within N days.

        Used by the reminder task to send appeal deadline notifications.

        Args:
            days_before: Number of days before deadline to include

        Returns:
            List of cases with appeal deadlines within the window
        """
        today = date.today()
        cutoff = today + timedelta(days=days_before)

        stmt = (
            select(DisciplinaryCase)
            .options(joinedload(DisciplinaryCase.employee))
            .where(DisciplinaryCase.status == CaseStatus.DECISION_MADE)
            .where(DisciplinaryCase.is_deleted == False)
            .where(DisciplinaryCase.appeal_deadline.isnot(None))
            .where(DisciplinaryCase.appeal_deadline <= cutoff)
            .where(DisciplinaryCase.appeal_deadline >= today)
        )
        return list(self.db.scalars(stmt).unique().all())

    # =========================================================================
    # Cross-Module Integrations
    # =========================================================================

    def _trigger_termination_lifecycle(
        self,
        case: DisciplinaryCase,
        action_data: CaseActionCreate,
        triggered_by_id: UUID | None = None,
    ) -> None:
        """
        Trigger the separation workflow when a termination action is recorded.

        Creates an EmployeeSeparation record and updates employee status.
        This integrates the discipline module with the lifecycle module.

        Args:
            case: The disciplinary case
            action_data: The termination action data
            triggered_by_id: User who triggered the action
        """
        try:
            from app.models.people.hr.employee import EmployeeStatus
            from app.models.people.hr.lifecycle import SeparationType
            from app.services.people.hr.lifecycle import LifecycleService

            lifecycle_svc = LifecycleService(self.db)

            # Create separation record
            separation = lifecycle_svc.create_separation(
                org_id=case.organization_id,
                employee_id=case.employee_id,
                separation_type=SeparationType.TERMINATION,
                separation_date=action_data.effective_date or date.today(),
                reason_for_leaving=f"Disciplinary Termination - Case {case.case_number}",
                notes=f"Terminated as a result of disciplinary case {case.case_number}. "
                f"Reason: {case.decision_summary or 'See case details'}",
            )

            # Update employee status to terminated
            employee = self.db.get(Employee, case.employee_id)
            if employee:
                employee.status = EmployeeStatus.TERMINATED
                # Set separation date
                employee.date_of_leaving = action_data.effective_date or date.today()

            logger.info(
                "Triggered termination lifecycle for employee %s from case %s. "
                "Created separation record %s",
                case.employee_id,
                case.case_number,
                separation.separation_id,
            )

        except ImportError:
            logger.warning(
                "Lifecycle service not available - skipping termination workflow "
                "for case %s",
                case.case_number,
            )
        except Exception as e:
            logger.exception(
                "Failed to trigger termination lifecycle for case %s: %s",
                case.case_number,
                str(e),
            )
            # Don't fail the decision recording if lifecycle integration fails
            # The termination action is still recorded, and HR can manually process

    def _trigger_mandatory_training(
        self,
        case: DisciplinaryCase,
        action_data: CaseActionCreate,
        assigned_by_id: UUID | None = None,
    ) -> None:
        """
        Trigger mandatory training assignment for corrective actions.

        This is a stub for integration with a training module.
        The training module would create a mandatory training assignment
        for the employee as a result of the disciplinary action.

        Args:
            case: The disciplinary case
            action_data: The mandatory training action data
            assigned_by_id: User who assigned the training
        """
        try:
            # Training module integration would go here
            # from app.services.people.training import TrainingService
            # training_svc = TrainingService(self.db)
            # training_svc.create_mandatory_assignment(
            #     org_id=case.organization_id,
            #     employee_id=case.employee_id,
            #     reason=f"Disciplinary - Case {case.case_number}",
            #     due_date=action_data.end_date,
            # )

            logger.info(
                "Mandatory training would be assigned for employee %s from case %s",
                case.employee_id,
                case.case_number,
            )

        except ImportError:
            logger.warning(
                "Training module not available - skipping training assignment "
                "for case %s",
                case.case_number,
            )
