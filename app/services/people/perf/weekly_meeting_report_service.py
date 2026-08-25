"""Business service for weekly meeting reports."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import cast
from uuid import UUID

try:
    from datetime import UTC  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - Python 3.10 compatibility
    UTC = timezone.utc

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.finance.core_org.organization import Organization
from app.models.people.hr import Department, Employee, EmployeeStatus
from app.models.people.perf.weekly_meeting_report import (
    MeetingActionStatus,
    MeetingAttendanceStatus,
    MeetingParticipantSource,
    ReportEmailStatus,
    WeeklyMeetingActionItem,
    WeeklyMeetingParticipant,
    WeeklyMeetingReport,
    WeeklyMeetingReportStatus,
)
from app.models.person import Person
from app.services.people.hr.org_resolver import OrgResolver

logger = logging.getLogger(__name__)

DEFAULT_HR_REPORT_EMAIL = "hr@dotmac.ng"


class WeeklyMeetingReportError(ValueError):
    """Base error for weekly meeting report operations."""


class WeeklyMeetingReportNotFoundError(WeeklyMeetingReportError):
    """Raised when a report is not visible in the current organization."""


class WeeklyMeetingReportLockedError(WeeklyMeetingReportError):
    """Raised when a submitted report is edited without reopening it."""


@dataclass(frozen=True)
class ParticipantInput:
    """Validated participant data received from the report form."""

    employee_id: UUID | None
    name: str
    role: str
    attendance_status: MeetingAttendanceStatus
    source: MeetingParticipantSource
    role_overridden: bool = False


@dataclass(frozen=True)
class ActionItemInput:
    """Validated action-item data received from the report form."""

    action_text: str
    owner_employee_id: UUID | None
    owner_name: str
    due_date: date | None
    status: MeetingActionStatus


@dataclass(frozen=True)
class WeeklyMeetingReportInput:
    """Editable weekly report fields."""

    department_id: UUID
    division_head_employee_id: UUID | None
    week_ending: date
    meeting_date: date
    meeting_time: time
    purpose_context: str
    matters_discussed: str
    key_decisions: str
    issues_risks_support: str
    carry_forward: str
    participants: list[ParticipantInput]
    action_items: list[ActionItemInput]


class WeeklyMeetingReportService:
    """Create, refresh, submit, and retrieve weekly meeting reports."""

    def __init__(self, db: Session):
        self.db = db
        self.org_resolver = OrgResolver(db)

    def list_reports(
        self,
        organization_id: UUID,
        *,
        search: str = "",
        status: WeeklyMeetingReportStatus | None = None,
        department_id: UUID | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[WeeklyMeetingReport], int]:
        """Return tenant-scoped reports and a total count."""
        conditions = [WeeklyMeetingReport.organization_id == organization_id]
        if status is not None:
            conditions.append(WeeklyMeetingReport.status == status)
        if department_id is not None:
            conditions.append(WeeklyMeetingReport.department_id == department_id)
        if search.strip():
            pattern = f"%{search.strip()}%"
            conditions.append(
                or_(
                    WeeklyMeetingReport.report_number.ilike(pattern),
                    WeeklyMeetingReport.division_name_snapshot.ilike(pattern),
                    WeeklyMeetingReport.prepared_by_name_snapshot.ilike(pattern),
                )
            )

        total = (
            self.db.scalar(
                select(func.count(WeeklyMeetingReport.report_id)).where(*conditions)
            )
            or 0
        )
        stmt = (
            select(WeeklyMeetingReport)
            .where(*conditions)
            .order_by(
                WeeklyMeetingReport.week_ending.desc(),
                WeeklyMeetingReport.created_at.desc(),
            )
            .offset(max(page - 1, 0) * per_page)
            .limit(per_page)
        )
        return list(self.db.scalars(stmt).all()), total

    def get_report(self, organization_id: UUID, report_id: UUID) -> WeeklyMeetingReport:
        """Load one report with children, scoped to its organization."""
        report = cast(
            WeeklyMeetingReport | None,
            self.db.scalar(
                select(WeeklyMeetingReport)
                .where(
                    WeeklyMeetingReport.organization_id == organization_id,
                    WeeklyMeetingReport.report_id == report_id,
                )
                .options(
                    selectinload(WeeklyMeetingReport.participants),
                    selectinload(WeeklyMeetingReport.action_items),
                )
            ),
        )
        if report is None:
            raise WeeklyMeetingReportNotFoundError("Weekly meeting report not found")
        return report

    def list_departments(self, organization_id: UUID) -> list[Department]:
        """Return current active departments with their configured heads."""
        return list(
            self.db.scalars(
                select(Department)
                .where(
                    Department.organization_id == organization_id,
                    Department.is_active.is_(True),
                )
                .options(joinedload(Department.head).joinedload(Employee.person))
                .order_by(Department.department_name)
            )
            .unique()
            .all()
        )

    def list_employee_options(self, organization_id: UUID) -> list[dict[str, str]]:
        """Return live active employee options for manual pickers."""
        employees = list(
            self.db.scalars(
                select(Employee)
                .where(
                    Employee.organization_id == organization_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                )
                .options(joinedload(Employee.person), joinedload(Employee.designation))
                .order_by(Employee.employee_code)
            )
            .unique()
            .all()
        )
        return [
            self._employee_option(employee, organization_id) for employee in employees
        ]

    def department_roster(
        self, organization_id: UUID, department_id: UUID
    ) -> dict[str, object]:
        """Resolve a department head and live employee roster."""
        department = self._get_department(organization_id, department_id)
        employees = list(
            self.db.scalars(
                select(Employee)
                .where(
                    Employee.organization_id == organization_id,
                    Employee.department_id == department_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                )
                .options(joinedload(Employee.person), joinedload(Employee.designation))
                .order_by(Employee.employee_code)
            )
            .unique()
            .all()
        )
        head = (
            department.head
            if department.head and department.head.status == EmployeeStatus.ACTIVE
            else None
        )
        return {
            "department_id": str(department.department_id),
            "department_name": department.department_name,
            "head": self._employee_option(head, organization_id) if head else None,
            "participants": [
                {
                    **self._employee_option(employee, organization_id),
                    "attendance_status": MeetingAttendanceStatus.INVITED.value,
                    "source": MeetingParticipantSource.SUGGESTED.value,
                    "role_overridden": False,
                }
                for employee in employees
            ],
        }

    def save_draft(
        self,
        organization_id: UUID,
        prepared_by_person_id: UUID,
        prepared_by_employee_id: UUID | None,
        data: WeeklyMeetingReportInput,
        *,
        report_id: UUID | None = None,
    ) -> WeeklyMeetingReport:
        """Create or update a draft; the caller owns the transaction."""
        self._validate_input(organization_id, data)
        department = self._get_department(organization_id, data.department_id)
        preparer = self._get_person(organization_id, prepared_by_person_id)
        if prepared_by_employee_id is not None:
            preparer_employee = self._get_employee(
                organization_id, prepared_by_employee_id
            )
            if preparer_employee.person_id != prepared_by_person_id:
                raise WeeklyMeetingReportError(
                    "Prepared-by employee does not match the authenticated person"
                )

        if report_id is None:
            report = WeeklyMeetingReport(
                organization_id=organization_id,
                report_number=self._report_number(department, data.week_ending),
                department_id=department.department_id,
                division_name_snapshot=department.department_name,
                week_ending=data.week_ending,
                meeting_date=data.meeting_date,
                meeting_time=data.meeting_time,
                prepared_by_person_id=prepared_by_person_id,
                prepared_by_employee_id=prepared_by_employee_id,
                prepared_by_name_snapshot=preparer.name,
                created_by_id=prepared_by_person_id,
                updated_by_id=prepared_by_person_id,
            )
            self.db.add(report)
            self.db.flush()
        else:
            report = self.get_report(organization_id, report_id)
            self._require_draft(report)
            report.updated_by_id = prepared_by_person_id
            report.version += 1

        report.department_id = department.department_id
        report.division_name_snapshot = department.department_name
        report.report_number = self._report_number(department, data.week_ending)
        report.week_ending = data.week_ending
        report.meeting_date = data.meeting_date
        report.meeting_time = data.meeting_time
        report.purpose_context = data.purpose_context or None
        report.matters_discussed = data.matters_discussed or None
        report.key_decisions = data.key_decisions or None
        report.issues_risks_support = data.issues_risks_support or None
        report.carry_forward = data.carry_forward or None
        self._apply_head(organization_id, report, data.division_head_employee_id)
        self._replace_participants(
            organization_id, report, data.participants, prepared_by_person_id
        )
        self._replace_action_items(
            organization_id, report, data.action_items, prepared_by_person_id
        )
        self.db.flush()
        logger.info("Saved weekly meeting report draft %s", report.report_id)
        return report

    def refresh_from_hr(
        self,
        organization_id: UUID,
        report_id: UUID,
        actor_person_id: UUID,
    ) -> WeeklyMeetingReport:
        """Merge current HR data into a draft without losing manual work."""
        report = self.get_report(organization_id, report_id)
        self._require_draft(report)
        roster = self.department_roster(organization_id, report.department_id)
        head = roster.get("head")
        if isinstance(head, dict) and head.get("employee_id"):
            self._apply_head(organization_id, report, UUID(str(head["employee_id"])))
        else:
            self._apply_head(organization_id, report, None)

        current_by_employee = {
            str(participant.employee_id): participant
            for participant in report.participants
            if participant.employee_id is not None
        }
        participant_rows = cast(list[dict[str, object]], roster["participants"])
        for row in participant_rows:
            employee_id = str(row["employee_id"])
            existing = current_by_employee.get(employee_id)
            if existing is None:
                report.participants.append(
                    WeeklyMeetingParticipant(
                        organization_id=organization_id,
                        employee_id=UUID(employee_id),
                        name_snapshot=str(row["name"]),
                        role_snapshot=str(row["role"] or ""),
                        attendance_status=MeetingAttendanceStatus.INVITED,
                        source=MeetingParticipantSource.SUGGESTED,
                        role_overridden=False,
                        sequence=len(report.participants),
                        created_by_id=actor_person_id,
                    )
                )
                continue
            existing.name_snapshot = str(row["name"])
            if not existing.role_overridden:
                existing.role_snapshot = str(row["role"] or "") or None
            existing.updated_by_id = actor_person_id

        report.hr_refreshed_at = datetime.now(UTC)
        report.updated_by_id = actor_person_id
        report.version += 1
        self.db.flush()
        return report

    def submit(
        self,
        organization_id: UUID,
        report_id: UUID,
        submitted_by_id: UUID,
    ) -> WeeklyMeetingReport:
        """Finalize a report and stage its single HR email notification."""
        report = self.get_report(organization_id, report_id)
        if report.status == WeeklyMeetingReportStatus.SUBMITTED:
            return report
        self._require_draft(report)
        self._get_person(organization_id, submitted_by_id)
        if not report.participants:
            raise WeeklyMeetingReportError(
                "Add at least one participant before submission"
            )

        organization = self.db.scalar(
            select(Organization).where(Organization.organization_id == organization_id)
        )
        if organization is None:
            raise WeeklyMeetingReportError("Organization not found")

        recipient = (
            organization.hr_weekly_report_email or DEFAULT_HR_REPORT_EMAIL
        ).strip()
        if not self._is_valid_notification_email(recipient):
            raise WeeklyMeetingReportError(
                "Configure a valid weekly report recipient email before submission"
            )

        report.status = WeeklyMeetingReportStatus.SUBMITTED
        report.submitted_at = datetime.now(UTC)
        report.submitted_by_id = submitted_by_id
        report.notification_recipient = recipient
        report.notification_status = ReportEmailStatus.PENDING
        report.updated_by_id = submitted_by_id
        report.version += 1
        self.db.flush()
        logger.info("Submitted weekly meeting report %s", report.report_id)
        return report

    def reopen(
        self, organization_id: UUID, report_id: UUID, actor_person_id: UUID
    ) -> WeeklyMeetingReport:
        """Return a submitted report to draft without erasing delivery history."""
        report = self.get_report(organization_id, report_id)
        if report.status != WeeklyMeetingReportStatus.SUBMITTED:
            raise WeeklyMeetingReportError("Only submitted reports can be reopened")
        self._get_person(organization_id, actor_person_id)
        report.status = WeeklyMeetingReportStatus.DRAFT
        report.submitted_at = None
        report.submitted_by_id = None
        report.updated_by_id = actor_person_id
        report.version += 1
        self.db.flush()
        logger.info("Reopened weekly meeting report %s", report.report_id)
        return report

    def mark_notification_pending(
        self, organization_id: UUID, report_id: UUID
    ) -> WeeklyMeetingReport:
        """Stage a retry for a failed or stranded report email."""
        report = self.get_report(organization_id, report_id)
        if report.status != WeeklyMeetingReportStatus.SUBMITTED:
            raise WeeklyMeetingReportError("Only submitted reports can be emailed")
        if report.notification_status == ReportEmailStatus.SENT:
            return report
        report.notification_status = ReportEmailStatus.PENDING
        report.notification_last_error = None
        self.db.flush()
        return report

    def _get_department(self, organization_id: UUID, department_id: UUID) -> Department:
        department = self.db.scalar(
            select(Department)
            .where(
                Department.organization_id == organization_id,
                Department.department_id == department_id,
                Department.is_active.is_(True),
            )
            .options(joinedload(Department.head).joinedload(Employee.person))
        )
        if department is None:
            raise WeeklyMeetingReportError("Division or department not found")
        return department

    def _get_person(self, organization_id: UUID, person_id: UUID) -> Person:
        person = self.db.scalar(
            select(Person).where(
                Person.organization_id == organization_id,
                Person.id == person_id,
                Person.is_active.is_(True),
            )
        )
        if person is None:
            raise WeeklyMeetingReportError("Prepared-by person not found")
        return person

    def _get_employee(self, organization_id: UUID, employee_id: UUID) -> Employee:
        employee = self.db.scalar(
            select(Employee)
            .where(
                Employee.organization_id == organization_id,
                Employee.employee_id == employee_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .options(joinedload(Employee.person), joinedload(Employee.designation))
        )
        if employee is None:
            raise WeeklyMeetingReportError("Selected employee is not active")
        return employee

    def _employee_option(
        self, employee: Employee, organization_id: UUID
    ) -> dict[str, str]:
        assignment = self.org_resolver.get_active_assignment(
            employee.employee_id, organization_id
        )
        role = ""
        if assignment and assignment.position:
            role = assignment.position.position_name
        elif employee.designation:
            role = employee.designation.designation_name
        return {
            "employee_id": str(employee.employee_id),
            "name": employee.full_name,
            "role": role,
            "department_id": str(employee.department_id or ""),
        }

    def _apply_head(
        self,
        organization_id: UUID,
        report: WeeklyMeetingReport,
        employee_id: UUID | None,
    ) -> None:
        report.division_head_employee_id = employee_id
        if employee_id is None:
            report.division_head_name_snapshot = None
            return
        employee = self._get_employee(organization_id, employee_id)
        report.division_head_name_snapshot = employee.full_name

    def _replace_participants(
        self,
        organization_id: UUID,
        report: WeeklyMeetingReport,
        inputs: list[ParticipantInput],
        actor_person_id: UUID,
    ) -> None:
        if len(inputs) > 1000:
            raise WeeklyMeetingReportError("A report cannot exceed 1,000 participants")
        for participant in list(report.participants):
            self.db.delete(participant)
        report.participants.clear()
        # Delete existing rows before inserting their replacements so the
        # per-report employee uniqueness constraint cannot see both versions.
        self.db.flush()
        seen: set[UUID] = set()
        for sequence, item in enumerate(inputs):
            employee: Employee | None = None
            if item.employee_id is not None:
                if item.employee_id in seen:
                    raise WeeklyMeetingReportError(
                        "An employee cannot appear twice in the participant list"
                    )
                seen.add(item.employee_id)
                employee = self._get_employee(organization_id, item.employee_id)
            name = employee.full_name if employee else item.name.strip()
            if not name:
                raise WeeklyMeetingReportError("Every participant requires a name")
            if len(name) > 160:
                raise WeeklyMeetingReportError(
                    "Participant names cannot exceed 160 characters"
                )
            role = item.role.strip()
            if employee and not item.role_overridden:
                role = self._employee_option(employee, organization_id)["role"]
            if len(role) > 160:
                raise WeeklyMeetingReportError(
                    "Participant roles cannot exceed 160 characters"
                )
            report.participants.append(
                WeeklyMeetingParticipant(
                    organization_id=organization_id,
                    employee_id=item.employee_id,
                    name_snapshot=name,
                    role_snapshot=role or None,
                    attendance_status=item.attendance_status,
                    source=item.source,
                    role_overridden=item.role_overridden,
                    sequence=sequence,
                    created_by_id=actor_person_id,
                )
            )

    def _replace_action_items(
        self,
        organization_id: UUID,
        report: WeeklyMeetingReport,
        inputs: list[ActionItemInput],
        actor_person_id: UUID,
    ) -> None:
        if len(inputs) > 200:
            raise WeeklyMeetingReportError("A report cannot exceed 200 action items")
        for action_item in list(report.action_items):
            self.db.delete(action_item)
        report.action_items.clear()
        self.db.flush()
        for sequence, item in enumerate(inputs):
            action_text = item.action_text.strip()
            if not action_text:
                continue
            owner_name = item.owner_name.strip()
            if item.owner_employee_id is not None:
                owner = self._get_employee(organization_id, item.owner_employee_id)
                owner_name = owner.full_name
            if len(owner_name) > 160:
                raise WeeklyMeetingReportError(
                    "Action owner names cannot exceed 160 characters"
                )
            report.action_items.append(
                WeeklyMeetingActionItem(
                    organization_id=organization_id,
                    action_text=action_text,
                    owner_employee_id=item.owner_employee_id,
                    owner_name_snapshot=owner_name or None,
                    due_date=item.due_date,
                    status=item.status,
                    sequence=sequence,
                    created_by_id=actor_person_id,
                )
            )

    @staticmethod
    def _report_number(department: Department, week_ending: date) -> str:
        code = re.sub(r"[^A-Za-z0-9]+", "-", department.department_code).strip("-")
        return f"WMR-{week_ending:%Y%m%d}-{code.upper()}"

    @staticmethod
    def _require_draft(report: WeeklyMeetingReport) -> None:
        if report.status != WeeklyMeetingReportStatus.DRAFT:
            raise WeeklyMeetingReportLockedError(
                "Submitted reports are locked. Reopen the report before editing."
            )

    @staticmethod
    def _is_valid_notification_email(value: str) -> bool:
        local, separator, domain = value.partition("@")
        return bool(
            local
            and separator
            and domain
            and "." in domain
            and not any(character.isspace() for character in value)
            and "@" not in domain
        )

    @staticmethod
    def _validate_input(organization_id: UUID, data: WeeklyMeetingReportInput) -> None:
        if not organization_id:
            raise WeeklyMeetingReportError("Organization context is required")
        if data.meeting_date > data.week_ending:
            raise WeeklyMeetingReportError(
                "Meeting date cannot be after the week-ending date"
            )
        if not data.participants:
            raise WeeklyMeetingReportError("Add at least one participant")
