"""
ProbationService - Manages employee probation lifecycle.

Handles probation tracking, confirmation, and enforcement of probation-period
restrictions on certain HR operations.

Note: Single-tenant implementation. Organization scoping is enforced via
explicit organization_id parameters in mutation methods.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee, EmployeeStatus

logger = logging.getLogger(__name__)


class ProbationServiceError(Exception):
    """Base error for probation service."""

    pass


class EmployeeAlreadyConfirmedError(ProbationServiceError):
    """Employee has already been confirmed."""

    def __init__(self, employee_id: UUID, confirmation_date: date):
        self.employee_id = employee_id
        self.confirmation_date = confirmation_date
        super().__init__(
            f"Employee {employee_id} was already confirmed on {confirmation_date}"
        )


class ProbationNotCompleteError(ProbationServiceError):
    """Probation period has not ended yet."""

    def __init__(self, employee_id: UUID, probation_end_date: date):
        self.employee_id = employee_id
        self.probation_end_date = probation_end_date
        super().__init__(
            f"Employee {employee_id} is still on probation until {probation_end_date}"
        )


class EmployeeInProbationError(ProbationServiceError):
    """Operation not allowed during probation period."""

    def __init__(self, employee_id: UUID, operation: str, probation_end_date: date):
        self.employee_id = employee_id
        self.operation = operation
        self.probation_end_date = probation_end_date
        super().__init__(
            f"Cannot perform '{operation}' for employee {employee_id}: "
            f"employee is on probation until {probation_end_date}"
        )


@dataclass
class ProbationStatus:
    """Status information about an employee's probation."""

    employee_id: UUID
    employee_name: str
    is_on_probation: bool
    is_confirmed: bool
    date_of_joining: date | None
    probation_end_date: date | None
    confirmation_date: date | None
    days_remaining: int  # Negative if probation ended


@dataclass
class ProbationCheckResult:
    """Result of checking employees with ending probation."""

    employees_ending_soon: list[ProbationStatus]  # Ending within threshold
    employees_due_for_confirmation: list[
        ProbationStatus
    ]  # Probation ended, not confirmed


# Operations restricted during probation
PROBATION_RESTRICTED_OPERATIONS = {
    "loan_application",
    "salary_advance",
    "transfer",
    "leave_encashment",
}


class ProbationService:
    """
    Service for managing employee probation periods.

    Handles:
    - Checking probation status
    - Confirming employees after probation
    - Enforcing probation-period restrictions
    - Finding employees due for confirmation

    Usage:
        service = ProbationService(db)

        # Check if employee is on probation
        status = service.get_probation_status(employee_id)
        if status.is_on_probation:
            print(f"Probation ends on {status.probation_end_date}")

        # Confirm an employee
        service.confirm_employee(org_id, employee_id, confirmed_by_id)

        # Check if operation is allowed
        service.validate_operation_allowed(employee_id, "loan_application")
    """

    def __init__(self, db: Session):
        self.db = db

    def get_employee(self, employee_id: UUID, organization_id: UUID | None = None) -> Employee | None:
        """Get employee by ID with optional org isolation."""
        employee = self.db.get(Employee, employee_id)
        if employee and organization_id is not None and employee.organization_id != organization_id:
            return None
        return employee

    def is_on_probation(
        self, employee_id: UUID, as_of_date: date | None = None
    ) -> bool:
        """
        Check if employee is currently on probation.

        An employee is on probation if:
        - They have a probation_end_date set
        - That date is in the future (or today)
        - They have not been confirmed (confirmation_date is None)

        Args:
            employee_id: Employee UUID
            as_of_date: Date to check (defaults to today)

        Returns:
            True if on probation, False otherwise
        """
        check_date = as_of_date or date.today()
        employee = self.get_employee(employee_id)

        if not employee:
            return False

        # If already confirmed, not on probation
        if employee.confirmation_date is not None:
            return False

        # If no probation end date set, not on probation
        if employee.probation_end_date is None:
            return False

        # On probation if end date hasn't passed
        return employee.probation_end_date >= check_date

    def is_confirmed(self, employee_id: UUID) -> bool:
        """Check if employee has been confirmed."""
        employee = self.get_employee(employee_id)
        return employee is not None and employee.confirmation_date is not None

    def get_probation_status(
        self,
        employee_id: UUID,
        as_of_date: date | None = None,
    ) -> ProbationStatus | None:
        """
        Get detailed probation status for an employee.

        Args:
            employee_id: Employee UUID
            as_of_date: Date to check (defaults to today)

        Returns:
            ProbationStatus with detailed information, or None if employee not found
        """
        check_date = as_of_date or date.today()
        employee = self.get_employee(employee_id)

        if not employee:
            return None

        is_confirmed = employee.confirmation_date is not None
        probation_end = employee.probation_end_date

        # Calculate days remaining
        if probation_end:
            days_remaining = (probation_end - check_date).days
        else:
            days_remaining = 0

        is_on_probation = (
            not is_confirmed
            and probation_end is not None
            and probation_end >= check_date
        )

        return ProbationStatus(
            employee_id=employee.employee_id,
            employee_name=employee.full_name or str(employee.employee_id),
            is_on_probation=is_on_probation,
            is_confirmed=is_confirmed,
            date_of_joining=employee.date_of_joining,
            probation_end_date=probation_end,
            confirmation_date=employee.confirmation_date,
            days_remaining=days_remaining,
        )

    def validate_operation_allowed(
        self,
        employee_id: UUID,
        operation: str,
    ) -> None:
        """
        Validate that an operation is allowed for an employee.

        Raises EmployeeInProbationError if the operation is restricted during probation.

        Args:
            employee_id: Employee UUID
            operation: Operation name (e.g., "loan_application", "transfer")

        Raises:
            EmployeeInProbationError: If operation is restricted and employee is on probation
        """
        if operation.lower() not in PROBATION_RESTRICTED_OPERATIONS:
            return  # Operation not restricted

        status = self.get_probation_status(employee_id)
        if status and status.is_on_probation:
            raise EmployeeInProbationError(
                employee_id,
                operation,
                status.probation_end_date,  # type: ignore
            )

    def confirm_employee(
        self,
        organization_id: UUID,
        employee_id: UUID,
        confirmed_by_id: UUID,
        confirmation_date: date | None = None,
        *,
        allow_early_confirmation: bool = False,
    ) -> Employee:
        """
        Confirm an employee after probation period.

        Args:
            organization_id: Organization scope
            employee_id: Employee to confirm
            confirmed_by_id: User performing confirmation
            confirmation_date: Date of confirmation (defaults to today)
            allow_early_confirmation: Allow confirmation before probation ends

        Returns:
            Updated Employee record

        Raises:
            ProbationServiceError: If employee not found
            EmployeeAlreadyConfirmedError: If already confirmed
            ProbationNotCompleteError: If probation not ended and early not allowed
        """
        confirm_date = confirmation_date or date.today()
        employee = self.get_employee(employee_id, organization_id=organization_id)

        if not employee:
            raise ProbationServiceError(f"Employee {employee_id} not found")

        if employee.organization_id != organization_id:
            raise ProbationServiceError(
                f"Employee {employee_id} not found in organization"
            )

        if employee.confirmation_date is not None:
            raise EmployeeAlreadyConfirmedError(employee_id, employee.confirmation_date)

        # Check if probation has ended (unless early confirmation allowed)
        if (
            not allow_early_confirmation
            and employee.probation_end_date is not None
            and employee.probation_end_date > confirm_date
        ):
            raise ProbationNotCompleteError(employee_id, employee.probation_end_date)

        # Set confirmation date
        employee.confirmation_date = confirm_date
        # Note: updated_at is handled by SQLAlchemy onupdate=func.now()

        self.db.flush()

        logger.info(
            "Employee %s confirmed on %s by user %s",
            employee_id,
            confirm_date,
            confirmed_by_id,
        )

        return employee

    def extend_probation(
        self,
        organization_id: UUID,
        employee_id: UUID,
        new_end_date: date,
        extended_by_id: UUID,
        reason: str | None = None,
    ) -> Employee:
        """
        Extend an employee's probation period.

        Args:
            organization_id: Organization scope
            employee_id: Employee to extend
            new_end_date: New probation end date
            extended_by_id: User performing extension
            reason: Reason for extension

        Returns:
            Updated Employee record

        Raises:
            ProbationServiceError: If employee not found or already confirmed
        """
        employee = self.get_employee(employee_id, organization_id=organization_id)

        if not employee:
            raise ProbationServiceError(f"Employee {employee_id} not found")

        if employee.organization_id != organization_id:
            raise ProbationServiceError(
                f"Employee {employee_id} not found in organization"
            )

        if employee.confirmation_date is not None:
            raise EmployeeAlreadyConfirmedError(employee_id, employee.confirmation_date)

        # Validate new_end_date is in the future
        today = date.today()
        if new_end_date <= today:
            raise ProbationServiceError(
                f"New probation end date {new_end_date} must be in the future"
            )

        # Validate new_end_date extends (not reduces) probation
        old_end_date = employee.probation_end_date
        if old_end_date is not None and new_end_date < old_end_date:
            raise ProbationServiceError(
                f"Cannot reduce probation period from {old_end_date} to {new_end_date}. "
                f"Use confirm_employee() to end probation early."
            )

        employee.probation_end_date = new_end_date

        self.db.flush()

        logger.info(
            "Extended probation for employee %s from %s to %s (reason: %s) by user %s",
            employee_id,
            old_end_date,
            new_end_date,
            reason or "Not specified",
            extended_by_id,
        )

        return employee

    def get_employees_ending_probation(
        self,
        organization_id: UUID,
        days_threshold: int = 7,
        as_of_date: date | None = None,
    ) -> list[ProbationStatus]:
        """
        Get employees whose probation is ending within the threshold.

        Used by Celery task to send reminders to HR.

        Args:
            organization_id: Organization scope
            days_threshold: Number of days to look ahead
            as_of_date: Reference date (defaults to today)

        Returns:
            List of ProbationStatus for employees ending soon
        """
        check_date = as_of_date or date.today()
        threshold_date = check_date + timedelta(days=days_threshold)

        stmt = (
            select(Employee)
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.confirmation_date.is_(None),  # Not yet confirmed
                Employee.probation_end_date.isnot(None),
                Employee.probation_end_date >= check_date,  # Hasn't ended yet
                Employee.probation_end_date <= threshold_date,  # Ending soon
            )
            .order_by(Employee.probation_end_date)
        )

        employees = list(self.db.scalars(stmt).all())

        results = []
        for emp in employees:
            days_remaining = (emp.probation_end_date - check_date).days  # type: ignore
            results.append(
                ProbationStatus(
                    employee_id=emp.employee_id,
                    employee_name=emp.full_name or str(emp.employee_id),
                    is_on_probation=True,
                    is_confirmed=False,
                    date_of_joining=emp.date_of_joining,
                    probation_end_date=emp.probation_end_date,
                    confirmation_date=None,
                    days_remaining=days_remaining,
                )
            )

        return results

    def get_employees_due_for_confirmation(
        self,
        organization_id: UUID,
        as_of_date: date | None = None,
    ) -> list[ProbationStatus]:
        """
        Get employees whose probation has ended but are not yet confirmed.

        These employees need HR review for confirmation or extension.

        Args:
            organization_id: Organization scope
            as_of_date: Reference date (defaults to today)

        Returns:
            List of ProbationStatus for employees due for confirmation
        """
        check_date = as_of_date or date.today()

        stmt = (
            select(Employee)
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.confirmation_date.is_(None),  # Not yet confirmed
                Employee.probation_end_date.isnot(None),
                Employee.probation_end_date < check_date,  # Probation ended
            )
            .order_by(Employee.probation_end_date)
        )

        employees = list(self.db.scalars(stmt).all())

        results = []
        for emp in employees:
            days_overdue = (check_date - emp.probation_end_date).days  # type: ignore
            results.append(
                ProbationStatus(
                    employee_id=emp.employee_id,
                    employee_name=emp.full_name or str(emp.employee_id),
                    is_on_probation=False,  # Probation period ended
                    is_confirmed=False,  # But not confirmed
                    date_of_joining=emp.date_of_joining,
                    probation_end_date=emp.probation_end_date,
                    confirmation_date=None,
                    days_remaining=-days_overdue,  # Negative = overdue
                )
            )

        return results

    def check_all_probations(
        self,
        organization_id: UUID,
        days_threshold: int = 7,
    ) -> ProbationCheckResult:
        """
        Check all employees' probation status for a Celery task.

        Returns employees ending soon and those due for confirmation.
        """
        ending_soon = self.get_employees_ending_probation(
            organization_id, days_threshold
        )
        due_for_confirmation = self.get_employees_due_for_confirmation(organization_id)

        return ProbationCheckResult(
            employees_ending_soon=ending_soon,
            employees_due_for_confirmation=due_for_confirmation,
        )

    def set_probation_end_date(
        self,
        organization_id: UUID,
        employee_id: UUID,
        probation_end_date: date,
        set_by_id: UUID,
    ) -> Employee:
        """
        Set the probation end date for an employee.

        Usually called when employee is hired with a defined probation period.

        Args:
            organization_id: Organization scope
            employee_id: Employee to update
            probation_end_date: End date of probation
            set_by_id: User setting the date

        Returns:
            Updated Employee record
        """
        employee = self.get_employee(employee_id, organization_id=organization_id)

        if not employee:
            raise ProbationServiceError(f"Employee {employee_id} not found")

        if employee.confirmation_date is not None:
            raise EmployeeAlreadyConfirmedError(employee_id, employee.confirmation_date)

        employee.probation_end_date = probation_end_date

        self.db.flush()

        logger.info(
            "Set probation end date for employee %s to %s by user %s",
            employee_id,
            probation_end_date,
            set_by_id,
        )

        return employee
