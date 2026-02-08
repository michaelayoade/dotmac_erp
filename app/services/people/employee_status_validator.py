"""
Employee Status Validator - Centralized validation for employee status checks.

Provides reusable validation methods to ensure operations are only performed
on employees with appropriate status.

Note: This is a single-tenant implementation. Employee lookups are by employee_id
only - organization scoping is handled at the API/route layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee, EmployeeStatus

logger = logging.getLogger(__name__)


class OperationType(str, Enum):
    """Types of operations that require status validation."""

    # Payroll operations
    SALARY_SLIP = "salary_slip"
    SALARY_ASSIGNMENT = "salary_assignment"
    BONUS = "bonus"

    # Leave operations
    LEAVE_APPLICATION = "leave_application"
    LEAVE_ENCASHMENT = "leave_encashment"

    # Loan operations
    LOAN_APPLICATION = "loan_application"
    SALARY_ADVANCE = "salary_advance"

    # HR operations
    PROMOTION = "promotion"
    TRANSFER = "transfer"
    DEMOTION = "demotion"
    TRAINING = "training"

    # Attendance
    ATTENDANCE_MARK = "attendance_mark"
    OVERTIME = "overtime"


@dataclass
class ValidationResult:
    """Result of status validation."""

    is_valid: bool
    message: str
    employee_status: EmployeeStatus | None = None
    employee_name: str | None = None


# Define allowed statuses for each operation type
OPERATION_ALLOWED_STATUSES: dict[OperationType, set[EmployeeStatus]] = {
    # Payroll - active and on-leave employees (on-leave still receive salary)
    OperationType.SALARY_SLIP: {EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE},
    OperationType.SALARY_ASSIGNMENT: {EmployeeStatus.ACTIVE, EmployeeStatus.DRAFT},
    OperationType.BONUS: {EmployeeStatus.ACTIVE},
    # Leave - active and on-leave can apply
    OperationType.LEAVE_APPLICATION: {EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE},
    OperationType.LEAVE_ENCASHMENT: {EmployeeStatus.ACTIVE},
    # Loans - only active employees
    OperationType.LOAN_APPLICATION: {EmployeeStatus.ACTIVE},
    OperationType.SALARY_ADVANCE: {EmployeeStatus.ACTIVE},
    # HR operations - active employees only
    OperationType.PROMOTION: {EmployeeStatus.ACTIVE},
    OperationType.TRANSFER: {EmployeeStatus.ACTIVE},
    OperationType.DEMOTION: {EmployeeStatus.ACTIVE, EmployeeStatus.SUSPENDED},
    OperationType.TRAINING: {EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE},
    # Attendance - active employees only
    OperationType.ATTENDANCE_MARK: {EmployeeStatus.ACTIVE},
    OperationType.OVERTIME: {EmployeeStatus.ACTIVE},
}

# Statuses that indicate employment has ended
TERMINATED_STATUSES: set[EmployeeStatus] = {
    EmployeeStatus.RESIGNED,
    EmployeeStatus.TERMINATED,
    EmployeeStatus.RETIRED,
}

# Statuses that indicate employee is currently employed (even if not active)
EMPLOYED_STATUSES: set[EmployeeStatus] = {
    EmployeeStatus.DRAFT,
    EmployeeStatus.ACTIVE,
    EmployeeStatus.ON_LEAVE,
    EmployeeStatus.SUSPENDED,
}


class EmployeeStatusValidator:
    """
    Centralized validator for employee status checks.

    Also validates probation restrictions for certain operations.

    Usage:
        validator = EmployeeStatusValidator(db)

        # Check if operation is allowed
        result = validator.validate_operation(employee_id, OperationType.LOAN_APPLICATION)
        if not result.is_valid:
            raise ValidationError(result.message)

        # Or use the quick check methods
        if not validator.can_apply_for_loan(employee_id):
            raise ValidationError("Employee cannot apply for loans")
    """

    # Operations restricted during probation period
    # NOTE: Keep in sync with PROBATION_RESTRICTED_OPERATIONS in probation_service.py
    PROBATION_RESTRICTED_OPERATIONS = {
        OperationType.LOAN_APPLICATION,
        OperationType.SALARY_ADVANCE,
        OperationType.LEAVE_ENCASHMENT,
        OperationType.TRANSFER,  # Cannot transfer during probation
    }

    def __init__(self, db: Session):
        self.db = db

    def get_employee(self, employee_id: UUID) -> Employee | None:
        """Get employee by ID."""
        return self.db.get(Employee, employee_id)

    def is_on_probation(self, employee_id: UUID) -> bool:
        """Check if employee is currently on probation."""
        from datetime import date

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
        return employee.probation_end_date >= date.today()

    def validate_operation(
        self,
        employee_id: UUID,
        operation: OperationType,
        *,
        check_probation: bool = True,
    ) -> ValidationResult:
        """
        Validate if an operation can be performed on an employee.

        Checks both employee status and probation restrictions.

        Args:
            employee_id: The employee's UUID
            operation: The type of operation to validate
            check_probation: Whether to check probation restrictions (default True)

        Returns:
            ValidationResult with is_valid flag and message
        """
        employee = self.get_employee(employee_id)

        if employee is None:
            return ValidationResult(
                is_valid=False,
                message=f"Employee {employee_id} not found",
            )

        allowed_statuses = OPERATION_ALLOWED_STATUSES.get(operation, set())
        employee_name = employee.full_name or str(employee_id)

        # Check status
        if employee.status not in allowed_statuses:
            allowed_str = ", ".join(s.value for s in allowed_statuses)
            return ValidationResult(
                is_valid=False,
                message=(
                    f"Cannot perform {operation.value} for {employee_name}: "
                    f"employee status is {employee.status.value}. "
                    f"Allowed statuses: {allowed_str}"
                ),
                employee_status=employee.status,
                employee_name=employee_name,
            )

        # Check probation restrictions
        if (
            check_probation
            and operation in self.PROBATION_RESTRICTED_OPERATIONS
            and self.is_on_probation(employee_id)
        ):
            return ValidationResult(
                is_valid=False,
                message=(
                    f"Cannot perform {operation.value} for {employee_name}: "
                    f"employee is on probation until {employee.probation_end_date}"
                ),
                employee_status=employee.status,
                employee_name=employee_name,
            )

        return ValidationResult(
            is_valid=True,
            message="OK",
            employee_status=employee.status,
            employee_name=employee_name,
        )

    def validate_operation_or_raise(
        self,
        employee_id: UUID,
        operation: OperationType,
    ) -> Employee:
        """
        Validate operation and raise ValueError if not allowed.

        Returns the employee if validation passes.
        """
        result = self.validate_operation(employee_id, operation)
        if not result.is_valid:
            raise ValueError(result.message)

        return self.get_employee(employee_id)  # type: ignore

    # Quick check methods for common operations

    def is_active(self, employee_id: UUID) -> bool:
        """Check if employee is active."""
        employee = self.get_employee(employee_id)
        return employee is not None and employee.status == EmployeeStatus.ACTIVE

    def is_employed(self, employee_id: UUID) -> bool:
        """Check if employee is still employed (not resigned/terminated/retired)."""
        employee = self.get_employee(employee_id)
        return employee is not None and employee.status in EMPLOYED_STATUSES

    def is_terminated(self, employee_id: UUID) -> bool:
        """Check if employee has left the organization."""
        employee = self.get_employee(employee_id)
        return employee is not None and employee.status in TERMINATED_STATUSES

    def can_receive_salary(self, employee_id: UUID) -> bool:
        """Check if employee can receive salary."""
        return self.validate_operation(employee_id, OperationType.SALARY_SLIP).is_valid

    def can_apply_for_leave(self, employee_id: UUID) -> bool:
        """Check if employee can apply for leave."""
        return self.validate_operation(
            employee_id, OperationType.LEAVE_APPLICATION
        ).is_valid

    def can_apply_for_loan(self, employee_id: UUID) -> bool:
        """Check if employee can apply for a loan."""
        return self.validate_operation(
            employee_id, OperationType.LOAN_APPLICATION
        ).is_valid

    def can_be_promoted(self, employee_id: UUID) -> bool:
        """Check if employee can be promoted."""
        return self.validate_operation(employee_id, OperationType.PROMOTION).is_valid

    def can_be_transferred(self, employee_id: UUID) -> bool:
        """Check if employee can be transferred."""
        return self.validate_operation(employee_id, OperationType.TRANSFER).is_valid

    def can_mark_attendance(self, employee_id: UUID) -> bool:
        """Check if attendance can be marked for employee."""
        return self.validate_operation(
            employee_id, OperationType.ATTENDANCE_MARK
        ).is_valid

    # Bulk validation methods

    def filter_by_status(
        self,
        employee_ids: list[UUID],
        allowed_statuses: set[EmployeeStatus],
    ) -> list[UUID]:
        """
        Filter a list of employee IDs to only those with allowed statuses.

        Args:
            employee_ids: List of employee UUIDs to filter
            allowed_statuses: Set of allowed statuses

        Returns:
            List of employee IDs that have an allowed status
        """
        if not employee_ids:
            return []

        stmt = (
            select(Employee.employee_id)
            .where(Employee.employee_id.in_(employee_ids))
            .where(Employee.status.in_(allowed_statuses))
        )
        result = self.db.execute(stmt).scalars().all()
        return list(result)

    def get_active_employee_ids(self, employee_ids: list[UUID]) -> list[UUID]:
        """Filter to only active employees."""
        return self.filter_by_status(employee_ids, {EmployeeStatus.ACTIVE})

    def get_payroll_eligible_ids(self, employee_ids: list[UUID]) -> list[UUID]:
        """Filter to only employees eligible for payroll."""
        return self.filter_by_status(
            employee_ids,
            OPERATION_ALLOWED_STATUSES[OperationType.SALARY_SLIP],
        )
