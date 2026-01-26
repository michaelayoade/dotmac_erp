"""HR-specific service errors.

These errors extend the base ServiceError classes with domain-specific
error types for HR operations.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.services.common import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceError,
    ValidationError,
)

__all__ = [
    # Re-export base errors for convenience
    "ServiceError",
    "NotFoundError",
    "ValidationError",
    "ConflictError",
    "ForbiddenError",
    # Employee errors
    "EmployeeNotFoundError",
    "EmployeeAlreadyExistsError",
    "EmployeeStatusError",
    "InvalidManagerError",
    # Organization errors
    "DepartmentNotFoundError",
    "DesignationNotFoundError",
    "EmploymentTypeNotFoundError",
    "EmployeeGradeNotFoundError",
    "CircularDepartmentError",
    # Leave errors
    "LeaveTypeNotFoundError",
    "LeavePolicyNotFoundError",
    "LeaveAllocationNotFoundError",
    "LeaveApplicationNotFoundError",
    "InsufficientLeaveBalanceError",
    "LeaveOverlapError",
    "LeaveStatusTransitionError",
    "LeavePolicyViolationError",
    "HolidayListNotFoundError",
    # Attendance errors
    "ShiftTypeNotFoundError",
    "ShiftAssignmentNotFoundError",
    "ShiftAssignmentError",
    "AttendanceNotFoundError",
    "DuplicateAttendanceError",
    "CheckInError",
    "CheckOutError",
    "AttendanceRequestNotFoundError",
    "AttendanceRequestStatusError",
    # Payroll errors
    "SalaryComponentNotFoundError",
    "SalarySlipNotFoundError",
    "PayrollEntryNotFoundError",
    "SalaryStructureNotFoundError",
    "SalaryStructureAssignmentNotFoundError",
    "NoSalaryAssignmentError",
    "PayrollAlreadyProcessedError",
    "SlipStatusTransitionError",
    # Recruitment errors
    "JobOpeningNotFoundError",
    "ApplicantNotFoundError",
    "JobOfferNotFoundError",
    "InterviewNotFoundError",
    "ApplicantPipelineError",
    "OfferExpiredError",
    # Training errors
    "TrainingProgramNotFoundError",
    "TrainingEventNotFoundError",
    "TrainingRegistrationError",
    # Appraisal errors
    "AppraisalNotFoundError",
    "AppraisalTemplateNotFoundError",
    "AppraisalStatusTransitionError",
    # Lifecycle errors
    "OnboardingNotFoundError",
    "SeparationNotFoundError",
    "PromotionNotFoundError",
    "TransferNotFoundError",
    "LifecycleStatusError",
]


# ==============================================================================
# Employee Errors
# ==============================================================================


class EmployeeNotFoundError(NotFoundError):
    """Raised when an employee is not found."""

    def __init__(
        self, employee_id: "uuid.UUID | None" = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Employee not found: {employee_id}"
                if employee_id
                else "Employee not found"
            )
        super().__init__(message)
        self.employee_id = employee_id


class EmployeeAlreadyExistsError(ConflictError):
    """Raised when attempting to create a duplicate employee."""

    def __init__(
        self, identifier: str | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Employee already exists: {identifier}"
                if identifier
                else "Employee already exists"
            )
        super().__init__(message)
        self.identifier = identifier


class EmployeeStatusError(ConflictError):
    """Raised when employee status prevents an operation."""

    def __init__(self, current_status: str, message: str | None = None) -> None:
        if message is None:
            message = f"Operation not allowed for employee with status: {current_status}"
        super().__init__(message)
        self.current_status = current_status


class InvalidManagerError(ValidationError):
    """Raised when manager assignment would create circular reference."""

    def __init__(
        self, message: str = "Invalid manager assignment - would create circular reference"
    ) -> None:
        super().__init__(message)


# ==============================================================================
# Organization Errors
# ==============================================================================


class DepartmentNotFoundError(NotFoundError):
    """Raised when a department is not found."""

    def __init__(
        self, department_id: "uuid.UUID | None" = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Department not found: {department_id}"
                if department_id
                else "Department not found"
            )
        super().__init__(message)
        self.department_id = department_id


class LocationNotFoundError(NotFoundError):
    """Raised when a location is not found."""

    def __init__(
        self, location_id: "uuid.UUID | None" = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Location not found: {location_id}"
                if location_id
                else "Location not found"
            )
        super().__init__(message)
        self.location_id = location_id


class DesignationNotFoundError(NotFoundError):
    """Raised when a designation is not found."""

    def __init__(
        self, designation_id: "uuid.UUID | None" = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Designation not found: {designation_id}"
                if designation_id
                else "Designation not found"
            )
        super().__init__(message)
        self.designation_id = designation_id


class EmploymentTypeNotFoundError(NotFoundError):
    """Raised when an employment type is not found."""

    def __init__(
        self, employment_type_id: "uuid.UUID | None" = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Employment type not found: {employment_type_id}"
                if employment_type_id
                else "Employment type not found"
            )
        super().__init__(message)
        self.employment_type_id = employment_type_id


class EmployeeGradeNotFoundError(NotFoundError):
    """Raised when an employee grade is not found."""

    def __init__(
        self, grade_id: "uuid.UUID | None" = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Employee grade not found: {grade_id}"
                if grade_id
                else "Employee grade not found"
            )
        super().__init__(message)
        self.grade_id = grade_id


class CircularDepartmentError(ValidationError):
    """Raised when department hierarchy would create a cycle."""

    def __init__(
        self, message: str = "Department hierarchy would create a circular reference"
    ) -> None:
        super().__init__(message)


# ==============================================================================
# Leave Errors
# ==============================================================================


class LeaveTypeNotFoundError(NotFoundError):
    """Raised when a leave type is not found."""

    def __init__(
        self, leave_type_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Leave type not found: {leave_type_id}"
                if leave_type_id
                else "Leave type not found"
            )
        super().__init__(message)
        self.leave_type_id = leave_type_id


class LeaveAllocationNotFoundError(NotFoundError):
    """Raised when a leave allocation is not found."""

    def __init__(
        self, allocation_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Leave allocation not found: {allocation_id}"
                if allocation_id
                else "Leave allocation not found"
            )
        super().__init__(message)
        self.allocation_id = allocation_id


class LeaveApplicationNotFoundError(NotFoundError):
    """Raised when a leave application is not found."""

    def __init__(
        self, application_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Leave application not found: {application_id}"
                if application_id
                else "Leave application not found"
            )
        super().__init__(message)
        self.application_id = application_id


class InsufficientLeaveBalanceError(ValidationError):
    """Raised when employee doesn't have enough leave balance."""

    def __init__(
        self, available: float, requested: float, leave_type: str | None = None
    ) -> None:
        leave_info = f" for {leave_type}" if leave_type else ""
        message = f"Insufficient leave balance{leave_info}. Available: {available}, Requested: {requested}"
        super().__init__(message)
        self.available = available
        self.requested = requested
        self.leave_type = leave_type


class LeaveOverlapError(ConflictError):
    """Raised when leave application overlaps with existing application."""

    def __init__(self, overlap_id: int, from_date: str, to_date: str) -> None:
        message = f"Leave application overlaps with existing application (ID: {overlap_id}, {from_date} to {to_date})"
        super().__init__(message)
        self.overlap_id = overlap_id
        self.from_date = from_date
        self.to_date = to_date


class LeaveStatusTransitionError(ConflictError):
    """Raised when leave status transition is not allowed."""

    def __init__(self, current_status: str, target_status: str) -> None:
        message = f"Cannot transition leave application from {current_status} to {target_status}"
        super().__init__(message)
        self.current_status = current_status
        self.target_status = target_status


class LeavePolicyViolationError(ValidationError):
    """Raised when leave application violates policy constraints."""

    def __init__(self, message: str, policy_rule: str | None = None) -> None:
        super().__init__(message)
        self.policy_rule = policy_rule


class LeavePolicyNotFoundError(NotFoundError):
    """Raised when a leave policy is not found."""

    def __init__(
        self, policy_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Leave policy not found: {policy_id}"
                if policy_id
                else "Leave policy not found"
            )
        super().__init__(message)
        self.policy_id = policy_id


class HolidayListNotFoundError(NotFoundError):
    """Raised when a holiday list is not found."""

    def __init__(
        self, holiday_list_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Holiday list not found: {holiday_list_id}"
                if holiday_list_id
                else "Holiday list not found"
            )
        super().__init__(message)
        self.holiday_list_id = holiday_list_id


# ==============================================================================
# Attendance Errors
# ==============================================================================


class ShiftTypeNotFoundError(NotFoundError):
    """Raised when shift type is not found."""

    def __init__(
        self, shift_type_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Shift type not found: {shift_type_id}"
                if shift_type_id
                else "Shift type not found"
            )
        super().__init__(message)
        self.shift_type_id = shift_type_id


class ShiftAssignmentNotFoundError(NotFoundError):
    """Raised when shift assignment is not found."""

    def __init__(
        self, assignment_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Shift assignment not found: {assignment_id}"
                if assignment_id
                else "Shift assignment not found"
            )
        super().__init__(message)
        self.assignment_id = assignment_id


class AttendanceNotFoundError(NotFoundError):
    """Raised when attendance record is not found."""

    def __init__(
        self, attendance_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Attendance record not found: {attendance_id}"
                if attendance_id
                else "Attendance record not found"
            )
        super().__init__(message)
        self.attendance_id = attendance_id


class DuplicateAttendanceError(ConflictError):
    """Raised when attendance record already exists for date."""

    def __init__(self, employee_id: int, attendance_date: str) -> None:
        message = f"Attendance record already exists for employee {employee_id} on {attendance_date}"
        super().__init__(message)
        self.employee_id = employee_id
        self.attendance_date = attendance_date


class ShiftAssignmentError(ValidationError):
    """Raised when shift assignment fails."""

    def __init__(self, message: str = "Shift assignment failed") -> None:
        super().__init__(message)


class CheckInError(ValidationError):
    """Raised when check-in operation fails."""

    def __init__(
        self, message: str = "Check-in failed", reason: str | None = None
    ) -> None:
        super().__init__(message)
        self.reason = reason


class CheckOutError(ValidationError):
    """Raised when check-out operation fails."""

    def __init__(
        self, message: str = "Check-out failed", reason: str | None = None
    ) -> None:
        super().__init__(message)
        self.reason = reason


class AttendanceRequestNotFoundError(NotFoundError):
    """Raised when attendance request is not found."""

    def __init__(
        self, request_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Attendance request not found: {request_id}"
                if request_id
                else "Attendance request not found"
            )
        super().__init__(message)
        self.request_id = request_id


class AttendanceRequestStatusError(ConflictError):
    """Raised when attendance request status transition is not allowed."""

    def __init__(self, request_id: int, current_status: str, target_status: str) -> None:
        message = f"Cannot transition attendance request {request_id} from {current_status} to {target_status}"
        super().__init__(message)
        self.request_id = request_id
        self.current_status = current_status
        self.target_status = target_status


# ==============================================================================
# Payroll Errors
# ==============================================================================


class SalaryComponentNotFoundError(NotFoundError):
    """Raised when salary component is not found."""

    def __init__(
        self, component_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Salary component not found: {component_id}"
                if component_id
                else "Salary component not found"
            )
        super().__init__(message)
        self.component_id = component_id


class SalarySlipNotFoundError(NotFoundError):
    """Raised when salary slip is not found."""

    def __init__(self, slip_id: int | None = None, message: str | None = None) -> None:
        if message is None:
            message = (
                f"Salary slip not found: {slip_id}"
                if slip_id
                else "Salary slip not found"
            )
        super().__init__(message)
        self.slip_id = slip_id


class PayrollEntryNotFoundError(NotFoundError):
    """Raised when payroll entry is not found."""

    def __init__(self, entry_id: int | None = None, message: str | None = None) -> None:
        if message is None:
            message = (
                f"Payroll entry not found: {entry_id}"
                if entry_id
                else "Payroll entry not found"
            )
        super().__init__(message)
        self.entry_id = entry_id


class SalaryStructureNotFoundError(NotFoundError):
    """Raised when salary structure is not found."""

    def __init__(
        self, structure_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Salary structure not found: {structure_id}"
                if structure_id
                else "Salary structure not found"
            )
        super().__init__(message)
        self.structure_id = structure_id


class SalaryStructureAssignmentNotFoundError(NotFoundError):
    """Raised when salary structure assignment is not found."""

    def __init__(
        self, assignment_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Salary structure assignment not found: {assignment_id}"
                if assignment_id
                else "Salary structure assignment not found"
            )
        super().__init__(message)
        self.assignment_id = assignment_id


class NoSalaryAssignmentError(ValidationError):
    """Raised when employee has no salary structure assignment."""

    def __init__(self, employee_id: int, as_of: str | None = None) -> None:
        date_info = f" as of {as_of}" if as_of else ""
        message = f"No salary structure assignment found for employee {employee_id}{date_info}"
        super().__init__(message)
        self.employee_id = employee_id
        self.as_of = as_of


class PayrollAlreadyProcessedError(ConflictError):
    """Raised when payroll has already been processed."""

    def __init__(self, entry_id: int, message: str | None = None) -> None:
        if message is None:
            message = f"Payroll entry {entry_id} has already been processed"
        super().__init__(message)
        self.entry_id = entry_id


class SlipStatusTransitionError(ConflictError):
    """Raised when salary slip status transition is not allowed."""

    def __init__(self, slip_id: int, current_status: str, target_status: str) -> None:
        message = f"Cannot transition salary slip {slip_id} from {current_status} to {target_status}"
        super().__init__(message)
        self.slip_id = slip_id
        self.current_status = current_status
        self.target_status = target_status


# ==============================================================================
# Recruitment Errors
# ==============================================================================


class JobOpeningNotFoundError(NotFoundError):
    """Raised when job opening is not found."""

    def __init__(
        self, opening_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Job opening not found: {opening_id}"
                if opening_id
                else "Job opening not found"
            )
        super().__init__(message)
        self.opening_id = opening_id


class ApplicantNotFoundError(NotFoundError):
    """Raised when job applicant is not found."""

    def __init__(
        self, applicant_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Job applicant not found: {applicant_id}"
                if applicant_id
                else "Job applicant not found"
            )
        super().__init__(message)
        self.applicant_id = applicant_id


class JobOfferNotFoundError(NotFoundError):
    """Raised when job offer is not found."""

    def __init__(self, offer_id: int | None = None, message: str | None = None) -> None:
        if message is None:
            message = (
                f"Job offer not found: {offer_id}"
                if offer_id
                else "Job offer not found"
            )
        super().__init__(message)
        self.offer_id = offer_id


class InterviewNotFoundError(NotFoundError):
    """Raised when interview is not found."""

    def __init__(
        self, interview_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Interview not found: {interview_id}"
                if interview_id
                else "Interview not found"
            )
        super().__init__(message)
        self.interview_id = interview_id


class ApplicantPipelineError(ConflictError):
    """Raised when applicant pipeline transition fails."""

    def __init__(
        self, current_stage: str, target_stage: str, reason: str | None = None
    ) -> None:
        message = f"Cannot move applicant from {current_stage} to {target_stage}"
        if reason:
            message += f": {reason}"
        super().__init__(message)
        self.current_stage = current_stage
        self.target_stage = target_stage
        self.reason = reason


class OfferExpiredError(ConflictError):
    """Raised when job offer has expired."""

    def __init__(self, offer_id: int, expiry_date: str) -> None:
        message = f"Job offer {offer_id} expired on {expiry_date}"
        super().__init__(message)
        self.offer_id = offer_id
        self.expiry_date = expiry_date


# ==============================================================================
# Training Errors
# ==============================================================================


class TrainingProgramNotFoundError(NotFoundError):
    """Raised when training program is not found."""

    def __init__(
        self, program_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Training program not found: {program_id}"
                if program_id
                else "Training program not found"
            )
        super().__init__(message)
        self.program_id = program_id


class TrainingEventNotFoundError(NotFoundError):
    """Raised when training event is not found."""

    def __init__(self, event_id: int | None = None, message: str | None = None) -> None:
        if message is None:
            message = (
                f"Training event not found: {event_id}"
                if event_id
                else "Training event not found"
            )
        super().__init__(message)
        self.event_id = event_id


class TrainingRegistrationError(ConflictError):
    """Raised when training registration fails."""

    def __init__(
        self, message: str = "Training registration failed", reason: str | None = None
    ) -> None:
        super().__init__(message)
        self.reason = reason


class TrainingResultNotFoundError(NotFoundError):
    """Raised when training result is not found."""

    def __init__(self, result_id: int | None = None, message: str | None = None) -> None:
        if message is None:
            message = (
                f"Training result not found: {result_id}"
                if result_id
                else "Training result not found"
            )
        super().__init__(message)
        self.result_id = result_id


class TrainingEventStatusError(ValidationError):
    """Raised when training event status transition is not allowed."""

    def __init__(
        self, event_id: int, current_status: str, target_status: str
    ) -> None:
        message = f"Cannot transition event {event_id} from {current_status} to {target_status}"
        super().__init__(message)
        self.event_id = event_id
        self.current_status = current_status
        self.target_status = target_status


# ==============================================================================
# Appraisal Errors
# ==============================================================================


class AppraisalNotFoundError(NotFoundError):
    """Raised when appraisal is not found."""

    def __init__(
        self, appraisal_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Appraisal not found: {appraisal_id}"
                if appraisal_id
                else "Appraisal not found"
            )
        super().__init__(message)
        self.appraisal_id = appraisal_id


class AppraisalTemplateNotFoundError(NotFoundError):
    """Raised when appraisal template is not found."""

    def __init__(
        self, template_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Appraisal template not found: {template_id}"
                if template_id
                else "Appraisal template not found"
            )
        super().__init__(message)
        self.template_id = template_id


class AppraisalStatusTransitionError(ConflictError):
    """Raised when appraisal status transition is not allowed."""

    def __init__(self, current_status: str, target_status: str) -> None:
        message = f"Cannot transition appraisal from {current_status} to {target_status}"
        super().__init__(message)
        self.current_status = current_status
        self.target_status = target_status


class AppraisalStatusError(ValidationError):
    """Raised when appraisal status transition is invalid."""

    def __init__(
        self, appraisal_id: int, current_status: str, target_status: str
    ) -> None:
        message = f"Cannot transition appraisal {appraisal_id} from {current_status} to {target_status}"
        super().__init__(message)
        self.appraisal_id = appraisal_id
        self.current_status = current_status
        self.target_status = target_status


# ==============================================================================
# Lifecycle Errors
# ==============================================================================


class OnboardingNotFoundError(NotFoundError):
    """Raised when onboarding record is not found."""

    def __init__(
        self, onboarding_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Onboarding not found: {onboarding_id}"
                if onboarding_id
                else "Onboarding not found"
            )
        super().__init__(message)
        self.onboarding_id = onboarding_id


class SeparationNotFoundError(NotFoundError):
    """Raised when separation record is not found."""

    def __init__(
        self, separation_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Separation not found: {separation_id}"
                if separation_id
                else "Separation not found"
            )
        super().__init__(message)
        self.separation_id = separation_id


class PromotionNotFoundError(NotFoundError):
    """Raised when promotion record is not found."""

    def __init__(
        self, promotion_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Promotion not found: {promotion_id}"
                if promotion_id
                else "Promotion not found"
            )
        super().__init__(message)
        self.promotion_id = promotion_id


class TransferNotFoundError(NotFoundError):
    """Raised when transfer record is not found."""

    def __init__(
        self, transfer_id: int | None = None, message: str | None = None
    ) -> None:
        if message is None:
            message = (
                f"Transfer not found: {transfer_id}"
                if transfer_id
                else "Transfer not found"
            )
        super().__init__(message)
        self.transfer_id = transfer_id


class LifecycleStatusError(ConflictError):
    """Raised when lifecycle status prevents an operation."""

    def __init__(self, current_status: str, operation: str) -> None:
        message = f"Cannot perform {operation} - current status is {current_status}"
        super().__init__(message)
        self.current_status = current_status
        self.operation = operation
