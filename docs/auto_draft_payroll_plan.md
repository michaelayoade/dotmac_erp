# Auto-Generate Draft Payroll - Implementation Plan

## Overview

Automatically generate draft payroll N days before month end, with:
- All active employees with salary assignments
- Pre-filled attendance from attendance module
- LWP deductions from approved leave
- Proration for mid-month joiners AND exits
- Data completeness validation
- Both in-app and email notifications
- Skip if draft already exists

---

## Current State

### Exists ✅
| Component | Location |
|-----------|----------|
| PayrollEntry model | `payroll_entry.py` |
| SalarySlip model | `salary_slip.py` |
| Slip generation | `salary_slip_service.py` |
| AttendancePayrollAdapter | `attendance_adapter.py` (not integrated) |
| WorkingDaysCalculator | `working_days_calculator.py` |
| PAYE Calculator | `paye_calculator.py` |
| HolidayList model | `app/models/people/leave/holiday.py` |

### Gaps ❌
| Gap | Fix |
|-----|-----|
| No scheduled task | New Celery task |
| LWP not auto-fetched | Integrate LeaveApplication |
| Attendance not auto-fetched | Integrate AttendancePayrollAdapter |
| Hardcoded holidays | Use HolidayList model |
| No duplicate check | Add validation |
| No exit proration | Enhance WorkingDaysCalculator |
| No data completeness check | New validation service |

---

## Implementation

### 1. Settings (DomainSetting)

```python
# Add to app/services/settings_spec.py

SettingSpec(
    key="payroll_auto_generate_enabled",
    domain=SettingDomain.PAYROLL,
    value_type=SettingValueType.BOOLEAN,
    default_value=False,
    description="Automatically generate draft payroll before month end",
),
SettingSpec(
    key="payroll_auto_generate_days_before",
    domain=SettingDomain.PAYROLL,
    value_type=SettingValueType.INTEGER,
    default_value=5,
    description="Days before month end to auto-generate draft",
),
SettingSpec(
    key="payroll_auto_generate_notify_emails",
    domain=SettingDomain.PAYROLL,
    value_type=SettingValueType.JSON,
    default_value=[],
    description="Email addresses to notify when draft is ready",
),
```

---

### 2. Data Completeness Service (New)

```python
# app/services/people/payroll/data_completeness.py

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from uuid import UUID

class CompletenessIssue(str, Enum):
    """Types of data completeness issues."""
    MISSING_BANK_DETAILS = "missing_bank_details"
    MISSING_TAX_PROFILE = "missing_tax_profile"
    MISSING_TIN = "missing_tin"
    MISSING_PENSION_ID = "missing_pension_id"
    MISSING_NHF_NUMBER = "missing_nhf_number"
    MISSING_SALARY_ASSIGNMENT = "missing_salary_assignment"
    EXPIRED_SALARY_ASSIGNMENT = "expired_salary_assignment"
    ATTENDANCE_GAPS = "attendance_gaps"
    NO_ATTENDANCE_RECORDS = "no_attendance_records"


@dataclass
class EmployeeCompletenessResult:
    """Result of data completeness check for one employee."""
    employee_id: UUID
    employee_code: str
    employee_name: str
    is_complete: bool
    issues: list[CompletenessIssue]
    warnings: list[str]  # Human-readable warnings

    # Specific missing fields
    missing_bank_details: bool = False
    missing_tax_profile: bool = False
    attendance_gap_days: int = 0


@dataclass
class PayrollCompletenessReport:
    """Overall completeness report for a payroll period."""
    organization_id: UUID
    period_start: date
    period_end: date

    total_employees: int
    complete_employees: int
    incomplete_employees: int

    # Grouped by issue type
    employees_missing_bank: list[EmployeeCompletenessResult]
    employees_missing_tax: list[EmployeeCompletenessResult]
    employees_with_attendance_gaps: list[EmployeeCompletenessResult]

    # Can proceed with warnings?
    can_proceed: bool  # True if critical issues resolved
    critical_issues: list[str]
    warnings: list[str]


class PayrollDataCompletenessService:
    """
    Validates employee data completeness before payroll generation.

    Checks:
    - Bank details (if salary_mode = BANK)
    - Tax profile (TIN, tax state)
    - Pension details (if pension applicable)
    - NHF number (if NHF applicable)
    - Valid salary assignment for period
    - Attendance records (flags gaps)
    """

    def __init__(self, db: Session):
        self.db = db

    def check_employee(
        self,
        employee: Employee,
        period_start: date,
        period_end: date,
    ) -> EmployeeCompletenessResult:
        """Check single employee's data completeness."""
        issues = []
        warnings = []

        # Bank details (critical if salary_mode = BANK)
        if employee.salary_mode == SalaryMode.BANK:
            if not employee.bank_account_number or not employee.bank_name:
                issues.append(CompletenessIssue.MISSING_BANK_DETAILS)
                warnings.append("Missing bank account details for bank transfer")

        # Tax profile
        tax_profile = self._get_tax_profile(employee.employee_id, period_end)
        if not tax_profile:
            issues.append(CompletenessIssue.MISSING_TAX_PROFILE)
            warnings.append("No tax profile - PAYE cannot be calculated accurately")
        elif not tax_profile.tin:
            issues.append(CompletenessIssue.MISSING_TIN)
            warnings.append("Missing TIN (Tax Identification Number)")

        # Salary assignment
        assignment = self._get_salary_assignment(employee.employee_id, period_start, period_end)
        if not assignment:
            issues.append(CompletenessIssue.MISSING_SALARY_ASSIGNMENT)
            warnings.append("No active salary assignment for this period")
        elif assignment.to_date and assignment.to_date < period_end:
            issues.append(CompletenessIssue.EXPIRED_SALARY_ASSIGNMENT)
            warnings.append(f"Salary assignment expires on {assignment.to_date}")

        # Attendance gaps
        attendance_summary = self._get_attendance_summary(
            employee.employee_id, period_start, period_end
        )
        if attendance_summary is None:
            issues.append(CompletenessIssue.NO_ATTENDANCE_RECORDS)
            warnings.append("No attendance records - will be flagged for review")
        elif attendance_summary.gap_days > 0:
            issues.append(CompletenessIssue.ATTENDANCE_GAPS)
            warnings.append(f"{attendance_summary.gap_days} days without attendance records")

        return EmployeeCompletenessResult(
            employee_id=employee.employee_id,
            employee_code=employee.employee_code,
            employee_name=employee.full_name,
            is_complete=len(issues) == 0,
            issues=issues,
            warnings=warnings,
            missing_bank_details=CompletenessIssue.MISSING_BANK_DETAILS in issues,
            missing_tax_profile=CompletenessIssue.MISSING_TAX_PROFILE in issues,
            attendance_gap_days=attendance_summary.gap_days if attendance_summary else 0,
        )

    def check_all_employees(
        self,
        organization_id: UUID,
        period_start: date,
        period_end: date,
    ) -> PayrollCompletenessReport:
        """Check all active employees for payroll readiness."""

        # Get active employees with salary assignments
        employees = self._get_payroll_eligible_employees(organization_id, period_start, period_end)

        results = []
        for emp in employees:
            result = self.check_employee(emp, period_start, period_end)
            results.append(result)

        # Group by issue
        missing_bank = [r for r in results if r.missing_bank_details]
        missing_tax = [r for r in results if r.missing_tax_profile]
        attendance_gaps = [r for r in results if r.attendance_gap_days > 0]

        complete = [r for r in results if r.is_complete]
        incomplete = [r for r in results if not r.is_complete]

        # Critical issues that block payroll
        critical = []
        if missing_bank:
            critical.append(f"{len(missing_bank)} employees missing bank details")

        # Warnings (can proceed but needs attention)
        warns = []
        if missing_tax:
            warns.append(f"{len(missing_tax)} employees missing tax profile")
        if attendance_gaps:
            warns.append(f"{len(attendance_gaps)} employees with attendance gaps (flagged for review)")

        return PayrollCompletenessReport(
            organization_id=organization_id,
            period_start=period_start,
            period_end=period_end,
            total_employees=len(results),
            complete_employees=len(complete),
            incomplete_employees=len(incomplete),
            employees_missing_bank=missing_bank,
            employees_missing_tax=missing_tax,
            employees_with_attendance_gaps=attendance_gaps,
            can_proceed=len(critical) == 0,  # Can proceed if no critical issues
            critical_issues=critical,
            warnings=warns,
        )

    def _get_payroll_eligible_employees(
        self,
        organization_id: UUID,
        period_start: date,
        period_end: date,
    ) -> list[Employee]:
        """Get employees eligible for payroll in this period."""
        stmt = (
            select(Employee)
            .where(
                Employee.organization_id == organization_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
                Employee.is_deleted == False,
                # Either no leaving date, or leaving date is within/after period
                or_(
                    Employee.date_of_leaving.is_(None),
                    Employee.date_of_leaving >= period_start,
                ),
                # Joined before or during period
                Employee.date_of_joining <= period_end,
            )
            .options(joinedload(Employee.person))
        )
        return list(self.db.scalars(stmt).all())

    def _get_tax_profile(self, employee_id: UUID, as_of_date: date):
        """Get employee's active tax profile."""
        stmt = (
            select(EmployeeTaxProfile)
            .where(
                EmployeeTaxProfile.employee_id == employee_id,
                EmployeeTaxProfile.effective_from <= as_of_date,
                or_(
                    EmployeeTaxProfile.effective_to.is_(None),
                    EmployeeTaxProfile.effective_to >= as_of_date,
                ),
            )
            .order_by(EmployeeTaxProfile.effective_from.desc())
            .limit(1)
        )
        return self.db.scalar(stmt)

    def _get_salary_assignment(self, employee_id: UUID, period_start: date, period_end: date):
        """Get employee's active salary assignment for period."""
        stmt = (
            select(SalaryStructureAssignment)
            .where(
                SalaryStructureAssignment.employee_id == employee_id,
                SalaryStructureAssignment.from_date <= period_end,
                or_(
                    SalaryStructureAssignment.to_date.is_(None),
                    SalaryStructureAssignment.to_date >= period_start,
                ),
                SalaryStructureAssignment.is_active == True,
            )
            .order_by(SalaryStructureAssignment.from_date.desc())
            .limit(1)
        )
        return self.db.scalar(stmt)

    def _get_attendance_summary(self, employee_id: UUID, period_start: date, period_end: date):
        """Get attendance summary and identify gaps."""
        adapter = AttendancePayrollAdapter(self.db)
        return adapter.get_payroll_attendance_summary(
            employee_id=employee_id,
            start_date=period_start,
            end_date=period_end,
            flag_gaps=True,  # New parameter to identify missing days
        )
```

---

### 3. Holiday Calendar Integration

Update `WorkingDaysCalculator` to use `HolidayList` model:

```python
# app/services/people/payroll/working_days_calculator.py

class WorkingDaysCalculator:
    """Calculate working days using organization's holiday calendar."""

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id
        self._holidays_cache: set[date] = None

    def _load_holidays(self, year: int) -> set[date]:
        """Load holidays from HolidayList for the organization."""
        if self._holidays_cache is not None:
            return self._holidays_cache

        # Get organization's holiday list
        stmt = (
            select(Holiday.holiday_date)
            .join(HolidayList, Holiday.holiday_list_id == HolidayList.holiday_list_id)
            .where(
                HolidayList.organization_id == self.organization_id,
                HolidayList.is_active == True,
                func.extract('year', Holiday.holiday_date) == year,
            )
        )

        self._holidays_cache = set(self.db.scalars(stmt).all())
        return self._holidays_cache

    def is_working_day(self, d: date) -> bool:
        """Check if date is a working day (not weekend, not holiday)."""
        # Weekend check (Saturday=5, Sunday=6)
        if d.weekday() >= 5:
            return False

        # Holiday check
        holidays = self._load_holidays(d.year)
        if d in holidays:
            return False

        return True

    def count_working_days(
        self,
        start_date: date,
        end_date: date,
    ) -> int:
        """Count working days between two dates (inclusive)."""
        count = 0
        current = start_date
        while current <= end_date:
            if self.is_working_day(current):
                count += 1
            current += timedelta(days=1)
        return count

    def calculate_proration(
        self,
        employee: Employee,
        period_start: date,
        period_end: date,
    ) -> ProrationResult:
        """
        Calculate proration for mid-period joiners AND leavers.

        Uses organization's holiday calendar for accurate working days.
        """
        effective_start = period_start
        effective_end = period_end
        reasons = []

        # Mid-period joiner
        if employee.date_of_joining and employee.date_of_joining > period_start:
            effective_start = employee.date_of_joining
            reasons.append(ProrationReason.JOINED_MID_PERIOD)

        # Mid-period leaver (RESIGNED, TERMINATED, RETIRED)
        if (
            employee.date_of_leaving
            and employee.date_of_leaving >= period_start
            and employee.date_of_leaving < period_end
        ):
            effective_end = employee.date_of_leaving
            reasons.append(ProrationReason.LEFT_MID_PERIOD)

        total_working_days = self.count_working_days(period_start, period_end)
        payment_days = self.count_working_days(effective_start, effective_end)

        # Determine proration reason
        if len(reasons) == 2:
            reason = ProrationReason.BOTH
        elif len(reasons) == 1:
            reason = reasons[0]
        else:
            reason = None

        return ProrationResult(
            total_working_days=Decimal(total_working_days),
            payment_days=Decimal(payment_days),
            effective_start=effective_start,
            effective_end=effective_end,
            is_prorated=reason is not None,
            proration_reason=reason,
            proration_factor=(
                Decimal(payment_days) / Decimal(total_working_days)
                if total_working_days > 0 else Decimal("0")
            ),
        )
```

---

### 4. LWP Leave Adapter (New)

```python
# app/services/people/payroll/leave_adapter.py

from decimal import Decimal
from datetime import date
from uuid import UUID

from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import Session

from app.models.people.leave import LeaveApplication, LeaveApplicationStatus, LeaveType


class LeavePayrollAdapter:
    """Bridge between Leave module and Payroll for LWP calculations."""

    def __init__(self, db: Session):
        self.db = db

    def get_lwp_days(
        self,
        employee_id: UUID,
        period_start: date,
        period_end: date,
    ) -> Decimal:
        """
        Get total Leave Without Pay days for employee in period.

        Only counts:
        - LeaveType.is_lwp = True
        - LeaveApplication.status = APPROVED
        - Leave dates overlapping with pay period

        Handles partial overlaps (leave spanning multiple pay periods).
        """
        # Get approved LWP applications overlapping with period
        stmt = (
            select(LeaveApplication)
            .join(LeaveType, LeaveApplication.leave_type_id == LeaveType.leave_type_id)
            .where(
                LeaveApplication.employee_id == employee_id,
                LeaveApplication.status == LeaveApplicationStatus.APPROVED,
                LeaveType.is_lwp == True,
                # Overlaps with period
                LeaveApplication.from_date <= period_end,
                LeaveApplication.to_date >= period_start,
            )
        )

        applications = self.db.scalars(stmt).all()

        total_lwp_days = Decimal("0")

        for app in applications:
            # Calculate overlap with pay period
            overlap_start = max(app.from_date, period_start)
            overlap_end = min(app.to_date, period_end)

            # Count days in overlap (inclusive)
            overlap_days = (overlap_end - overlap_start).days + 1

            # Adjust for half-day leaves if applicable
            if app.half_day and overlap_days == 1:
                overlap_days = Decimal("0.5")

            total_lwp_days += Decimal(str(overlap_days))

        return total_lwp_days

    def get_leave_summary(
        self,
        employee_id: UUID,
        period_start: date,
        period_end: date,
    ) -> dict:
        """
        Get detailed leave summary for payroll display.

        Returns breakdown by leave type.
        """
        stmt = (
            select(
                LeaveType.name,
                LeaveType.is_lwp,
                func.sum(LeaveApplication.total_leave_days).label("days"),
            )
            .join(LeaveType, LeaveApplication.leave_type_id == LeaveType.leave_type_id)
            .where(
                LeaveApplication.employee_id == employee_id,
                LeaveApplication.status == LeaveApplicationStatus.APPROVED,
                LeaveApplication.from_date <= period_end,
                LeaveApplication.to_date >= period_start,
            )
            .group_by(LeaveType.name, LeaveType.is_lwp)
        )

        results = self.db.execute(stmt).all()

        return {
            "total_leave_days": sum(r.days for r in results),
            "lwp_days": sum(r.days for r in results if r.is_lwp),
            "paid_leave_days": sum(r.days for r in results if not r.is_lwp),
            "by_type": [
                {"type": r.name, "days": r.days, "is_lwp": r.is_lwp}
                for r in results
            ],
        }
```

---

### 5. Enhanced Slip Generation

```python
# app/services/people/payroll/payroll_service.py

def generate_salary_slips_auto(
    self,
    entry_id: UUID,
    include_attendance: bool = True,
    include_lwp: bool = True,
    prorate_new_hires: bool = True,
    prorate_exits: bool = True,
) -> GenerationResult:
    """
    Generate salary slips with auto-fetched data.

    - Fetches attendance from AttendancePayrollAdapter
    - Fetches LWP from LeavePayrollAdapter
    - Calculates proration using WorkingDaysCalculator with holiday calendar
    - Flags employees with data gaps for review
    """
    entry = self.db.get(PayrollEntry, entry_id)
    if not entry:
        raise NotFoundError(f"PayrollEntry {entry_id} not found")

    if entry.salary_slips_created:
        raise ValidationError("Salary slips already created. Use regenerate instead.")

    # Get eligible employees
    employees = self._get_eligible_employees(
        organization_id=entry.organization_id,
        period_start=entry.start_date,
        period_end=entry.end_date,
        department_id=entry.department_id,
        designation_id=entry.designation_id,
    )

    # Initialize adapters
    working_days_calc = WorkingDaysCalculator(self.db, entry.organization_id)
    attendance_adapter = AttendancePayrollAdapter(self.db)
    leave_adapter = LeavePayrollAdapter(self.db)
    completeness_service = PayrollDataCompletenessService(self.db)

    results = GenerationResult(
        total=len(employees),
        created=0,
        skipped=0,
        flagged_for_review=[],
        errors=[],
    )

    for employee in employees:
        try:
            # Check data completeness
            completeness = completeness_service.check_employee(
                employee, entry.start_date, entry.end_date
            )

            # Skip if no salary assignment
            if CompletenessIssue.MISSING_SALARY_ASSIGNMENT in completeness.issues:
                results.skipped += 1
                continue

            # Calculate proration
            proration = working_days_calc.calculate_proration(
                employee=employee,
                period_start=entry.start_date,
                period_end=entry.end_date,
            )

            # Get attendance (if enabled)
            absent_days = Decimal("0")
            attendance_flags = []
            if include_attendance:
                attendance = attendance_adapter.get_payroll_attendance_summary(
                    employee_id=employee.employee_id,
                    start_date=entry.start_date,
                    end_date=entry.end_date,
                )
                if attendance:
                    absent_days = attendance.absent_days
                else:
                    # No attendance records - flag for review
                    attendance_flags.append("no_attendance_records")

            # Get LWP days (if enabled)
            lwp_days = Decimal("0")
            if include_lwp:
                lwp_days = leave_adapter.get_lwp_days(
                    employee_id=employee.employee_id,
                    period_start=entry.start_date,
                    period_end=entry.end_date,
                )

            # Build input
            slip_input = SalarySlipInput(
                employee_id=employee.employee_id,
                start_date=entry.start_date,
                end_date=entry.end_date,
                posting_date=entry.posting_date,
                total_working_days=proration.total_working_days,
                absent_days=absent_days,
                leave_without_pay=lwp_days,
            )

            # Create slip
            slip = self.salary_slip_service.create_salary_slip(
                db=self.db,
                organization_id=entry.organization_id,
                input=slip_input,
                payroll_entry_id=entry_id,
                created_by_user_id=None,  # System generated
            )

            # Flag for review if needed
            review_reasons = []
            if not completeness.is_complete:
                review_reasons.extend(completeness.warnings)
            if attendance_flags:
                review_reasons.append("No attendance records - days assumed present")

            if review_reasons:
                slip.needs_review = True
                slip.review_reasons = review_reasons
                results.flagged_for_review.append({
                    "employee_id": str(employee.employee_id),
                    "employee_name": employee.full_name,
                    "slip_id": str(slip.slip_id),
                    "reasons": review_reasons,
                })

            results.created += 1

        except Exception as e:
            logger.exception("Failed to create slip for %s", employee.employee_code)
            results.errors.append({
                "employee_id": str(employee.employee_id),
                "employee_code": employee.employee_code,
                "error": str(e),
            })

    # Update entry
    entry.salary_slips_created = True
    entry.status = PayrollEntryStatus.SLIPS_CREATED
    entry.employee_count = results.created
    self._update_entry_totals(entry)

    return results
```

---

### 6. SalarySlip Model Update

Add fields for review flagging:

```python
# app/models/people/payroll/salary_slip.py

# Add these fields:
needs_review: Mapped[bool] = mapped_column(
    Boolean,
    default=False,
    comment="Flag if slip needs manual review due to data gaps",
)
review_reasons: Mapped[Optional[list]] = mapped_column(
    JSONB,
    nullable=True,
    comment="Reasons why this slip is flagged for review",
)
```

---

### 7. Celery Task

```python
# app/tasks/payroll.py

from datetime import date, timedelta
from calendar import monthrange

from celery import shared_task

from app.db import SessionLocal
from app.services.settings_cache import get_setting
from app.services.people.payroll import PayrollService
from app.services.people.payroll.data_completeness import PayrollDataCompletenessService
from app.services.notification import NotificationService
from app.models.notification import EntityType, NotificationType, NotificationChannel


def last_day_of_month(d: date) -> date:
    """Get last day of the month for given date."""
    _, last_day = monthrange(d.year, d.month)
    return date(d.year, d.month, last_day)


def first_day_of_month(d: date) -> date:
    """Get first day of the month for given date."""
    return date(d.year, d.month, 1)


@shared_task
def auto_generate_draft_payroll() -> dict:
    """
    Daily task: Generate draft payroll N days before month end.

    Schedule: Daily at 8:00 AM

    For each organization with auto-generation enabled:
    1. Check if today is N days before month end
    2. Skip if payroll already exists for period
    3. Run data completeness check
    4. Generate draft payroll with auto-fetched data
    5. Notify HR/Finance (in-app + email)
    """
    import logging
    from sqlalchemy import select
    from app.models.finance.core_org import Organization

    logger = logging.getLogger(__name__)

    today = date.today()
    month_end = last_day_of_month(today)
    days_until_end = (month_end - today).days

    results = {
        "date": str(today),
        "days_until_month_end": days_until_end,
        "organizations_checked": 0,
        "payrolls_generated": [],
        "skipped": [],
        "errors": [],
    }

    with SessionLocal() as db:
        # Get organizations with auto-generate enabled
        orgs = db.scalars(
            select(Organization).where(Organization.is_active == True)
        ).all()

        for org in orgs:
            results["organizations_checked"] += 1

            try:
                # Check if auto-generation is enabled
                enabled = get_setting(
                    db, org.organization_id,
                    "payroll_auto_generate_enabled",
                    default=False,
                )
                if not enabled:
                    continue

                # Check if today is the right day
                days_before = get_setting(
                    db, org.organization_id,
                    "payroll_auto_generate_days_before",
                    default=5,
                )
                if days_until_end != days_before:
                    continue

                period_start = first_day_of_month(today)
                period_end = month_end

                # Skip if payroll already exists
                if _payroll_exists(db, org.organization_id, period_start, period_end):
                    results["skipped"].append({
                        "org_id": str(org.organization_id),
                        "org_name": org.name,
                        "reason": "Payroll already exists for period",
                    })
                    continue

                # Run data completeness check first
                completeness_service = PayrollDataCompletenessService(db)
                completeness_report = completeness_service.check_all_employees(
                    organization_id=org.organization_id,
                    period_start=period_start,
                    period_end=period_end,
                )

                # Generate draft payroll
                payroll_service = PayrollService(db)

                entry = payroll_service.create_payroll_entry(
                    organization_id=org.organization_id,
                    start_date=period_start,
                    end_date=period_end,
                    posting_date=period_end,
                    payroll_frequency=PayrollFrequency.MONTHLY,
                    created_by_id=None,  # System-generated
                )

                generation_result = payroll_service.generate_salary_slips_auto(
                    entry_id=entry.entry_id,
                    include_attendance=True,
                    include_lwp=True,
                    prorate_new_hires=True,
                    prorate_exits=True,
                )

                db.commit()

                # Send notifications
                _notify_draft_ready(
                    db=db,
                    org=org,
                    entry=entry,
                    completeness_report=completeness_report,
                    generation_result=generation_result,
                )

                results["payrolls_generated"].append({
                    "org_id": str(org.organization_id),
                    "org_name": org.name,
                    "entry_id": str(entry.entry_id),
                    "entry_number": entry.entry_number,
                    "employee_count": generation_result.created,
                    "flagged_for_review": len(generation_result.flagged_for_review),
                    "completeness_issues": completeness_report.incomplete_employees,
                })

                logger.info(
                    "Generated draft payroll %s for %s: %d employees, %d flagged",
                    entry.entry_number,
                    org.name,
                    generation_result.created,
                    len(generation_result.flagged_for_review),
                )

            except Exception as e:
                logger.exception("Failed to generate payroll for org %s", org.organization_id)
                results["errors"].append({
                    "org_id": str(org.organization_id),
                    "error": str(e),
                })
                db.rollback()

    return results


def _payroll_exists(db, organization_id: UUID, start_date: date, end_date: date) -> bool:
    """Check if non-cancelled payroll exists for period."""
    from app.models.people.payroll import PayrollEntry, PayrollEntryStatus

    stmt = select(func.count(PayrollEntry.entry_id)).where(
        PayrollEntry.organization_id == organization_id,
        PayrollEntry.start_date == start_date,
        PayrollEntry.end_date == end_date,
        PayrollEntry.status != PayrollEntryStatus.CANCELLED,
    )
    return db.scalar(stmt) > 0


def _notify_draft_ready(
    db,
    org,
    entry,
    completeness_report,
    generation_result,
):
    """Send in-app and email notifications."""
    from app.services.notification import NotificationService
    from app.services.rbac import get_users_with_permission

    notification_service = NotificationService()

    # Build message
    period_name = entry.start_date.strftime("%B %Y")
    message_parts = [
        f"Draft payroll for {period_name} is ready for review.",
        f"{generation_result.created} employees included.",
    ]

    if generation_result.flagged_for_review:
        message_parts.append(
            f"{len(generation_result.flagged_for_review)} slips flagged for review."
        )

    if completeness_report.incomplete_employees > 0:
        message_parts.append(
            f"{completeness_report.incomplete_employees} employees have incomplete data."
        )

    message = " ".join(message_parts)

    # In-app notifications to users with payroll permission
    users = get_users_with_permission(db, org.organization_id, "payroll.entry.approve")

    for user in users:
        notification_service.create(
            db,
            organization_id=org.organization_id,
            recipient_id=user.person_id,
            entity_type=EntityType.PAYROLL,
            entity_id=entry.entry_id,
            notification_type=NotificationType.INFO,
            title=f"Draft Payroll Ready: {entry.entry_number}",
            message=message,
            channel=NotificationChannel.BOTH,
            action_url=f"/people/payroll/runs/{entry.entry_id}",
        )

    # Email to configured recipients
    email_recipients = get_setting(
        db, org.organization_id,
        "payroll_auto_generate_notify_emails",
        default=[],
    )

    if email_recipients:
        from app.services.email import send_email

        send_email(
            to=email_recipients,
            subject=f"Draft Payroll Ready: {entry.entry_number} - {period_name}",
            template="emails/payroll/draft_ready.html",
            context={
                "org": org,
                "entry": entry,
                "period_name": period_name,
                "employee_count": generation_result.created,
                "flagged_count": len(generation_result.flagged_for_review),
                "incomplete_count": completeness_report.incomplete_employees,
                "completeness_report": completeness_report,
                "url": f"/people/payroll/runs/{entry.entry_id}",
            },
        )

    db.commit()
```

---

### 8. Celery Beat Schedule

```python
# app/celery_config.py

from celery.schedules import crontab

beat_schedule = {
    'auto-generate-draft-payroll': {
        'task': 'app.tasks.payroll.auto_generate_draft_payroll',
        'schedule': crontab(hour=8, minute=0),  # Daily at 8 AM
    },
}
```

---

## Files Summary

| File | Action | Description |
|------|--------|-------------|
| `app/services/settings_spec.py` | Modify | Add 3 payroll auto-generation settings |
| `app/services/people/payroll/data_completeness.py` | **New** | Employee data validation service |
| `app/services/people/payroll/leave_adapter.py` | **New** | LWP days fetcher from Leave module |
| `app/services/people/payroll/working_days_calculator.py` | Modify | Use HolidayList, add exit proration |
| `app/services/people/payroll/payroll_service.py` | Modify | Add `generate_salary_slips_auto()` |
| `app/models/people/payroll/salary_slip.py` | Modify | Add `needs_review`, `review_reasons` fields |
| `app/tasks/payroll.py` | Modify | Add `auto_generate_draft_payroll` task |
| `app/celery_config.py` | Modify | Add daily schedule |
| `templates/emails/payroll/draft_ready.html` | **New** | Email template for notifications |
| `alembic/versions/xxx_add_salary_slip_review_fields.py` | **New** | Migration for new fields |

---

## UI Enhancements (Optional)

1. **Data Completeness Report Page**
   - `/people/payroll/data-completeness`
   - Shows all employees with missing data
   - Links to employee edit forms

2. **Payroll Run Detail**
   - Highlight slips flagged for review
   - Show review reasons
   - Quick actions to resolve issues

3. **Settings Page**
   - Toggle auto-generation on/off
   - Set days before month end
   - Configure notification emails

---

## Testing Checklist

- [ ] Holiday calendar loads correctly from HolidayList
- [ ] Proration works for mid-month joiners
- [ ] Proration works for mid-month exits (resigned/terminated)
- [ ] LWP days calculated from approved leave
- [ ] Attendance gaps flagged for review
- [ ] Missing bank details flagged
- [ ] Missing tax profile flagged
- [ ] Duplicate period check works
- [ ] Notifications sent (in-app + email)
- [ ] Skip if draft exists
- [ ] Settings control behavior correctly
