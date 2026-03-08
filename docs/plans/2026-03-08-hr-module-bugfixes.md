# HR Module Code Review Bugfixes

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all 20 issues found in the HR module code review — payroll calculation bugs, multi-tenancy violations, CLAUDE.md rule violations, logic bugs, and template pattern violations.

**Architecture:** Fixes are grouped by category. Each task is independent and can be parallelized. All fixes follow existing project patterns from CLAUDE.md.

**Tech Stack:** Python/SQLAlchemy 2.0, Jinja2 templates, FastAPI

---

## Group A: Critical Financial Calculation Bugs

### Task 1: Fix PAYE calculation on pro-rated gross

**Files:**
- Modify: `app/services/people/payroll/salary_slip_service.py:524-532`

**Problem:** For mid-month joiners/leavers, earnings are pro-rated first, then the pro-rated gross is passed to `PAYECalculator.calculate()` as `gross_monthly`. The calculator annualizes this, so a half-month employee's annual income is underestimated by 50%.

**Fix:** Pass the full monthly salary to the PAYE calculator, then apply the pro-ration factor to the resulting deduction amounts.

At line 524-532, change:
```python
# BEFORE
paye_breakdown = calculator.calculate(
    organization_id=org_id,
    gross_monthly=gross_pay,
    basic_monthly=basic_pay,
    employee_id=emp_id,
    as_of_date=input.start_date,
)

# AFTER — pass full monthly amounts, apply proration to results
proration_factor = Decimal("1")
if payment_days and total_working_days and total_working_days > 0:
    proration_factor = Decimal(str(payment_days)) / Decimal(str(total_working_days))

# Use full monthly salary for correct PAYE annualization
full_monthly_gross = gross_pay / proration_factor if proration_factor > 0 else gross_pay
full_monthly_basic = basic_pay / proration_factor if proration_factor > 0 else basic_pay

paye_breakdown = calculator.calculate(
    organization_id=org_id,
    gross_monthly=full_monthly_gross,
    basic_monthly=full_monthly_basic,
    employee_id=emp_id,
    as_of_date=input.start_date,
)

# Pro-rate the statutory deduction results
if proration_factor < Decimal("1"):
    paye_breakdown = paye_breakdown.prorate(proration_factor)
```

**Note:** Check if `PAYEBreakdown` has a `prorate()` method. If not, manually multiply each field (monthly_paye, monthly_pension, monthly_nhf) by `proration_factor` and re-quantize.

---

### Task 2: Fix GL journal imbalance — employer pension missing expense debit

**Files:**
- Modify: `app/services/people/payroll/payroll_gl_adapter.py:187-216`

**Problem:** In `post_salary_slip`, employer pension (marked `do_not_include_in_total=True`, `statistical_component=True`) passes through the skip check at line 199-203 and gets credited to liability, but no expense debit is created. The parallel `create_slip_journal` method (line 458-481) correctly handles both debit and credit.

**Fix:** Add an expense debit line for employer pension in `post_salary_slip`, matching the pattern from `create_slip_journal`:

After line 203 (`continue  # Skip excluded items except employer pension`), add employer pension handling:
```python
if component.component_code == EMPLOYER_PENSION_COMPONENT_CODE:
    # Employer pension: debit expense, credit liability (same as create_slip_journal)
    if component.expense_account_id:
        journal_lines.append(
            JournalLineInput(
                account_id=component.expense_account_id,
                debit_amount=deduction.amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=functional_amount,
                credit_amount_functional=Decimal("0"),
                description=f"Employer Pension - {slip.employee_name}",
                cost_center_id=slip.cost_center_id or employee.cost_center_id,
            )
        )
```

---

### Task 3: Fix reducing-balance loan interest split

**Files:**
- Modify: `app/services/people/payroll/loan_service.py:547-556`

**Problem:** Uses a constant `total_interest / total_repayable` ratio for every installment. For reducing-balance loans, interest should be calculated on the current outstanding balance.

**Fix:** Calculate interest based on outstanding balance for reducing-balance loans:
```python
# BEFORE
interest_portion = _round_currency(
    amount * (loan.total_interest / loan.total_repayable)
)
principal_portion = amount - interest_portion

# AFTER
if loan.interest_method == InterestMethod.REDUCING_BALANCE:
    # Interest for this period based on current outstanding balance
    monthly_rate = (loan.interest_rate / Decimal("100")) / Decimal("12")
    interest_portion = _round_currency(balance * monthly_rate)
    if interest_portion > amount:
        interest_portion = amount  # Cap interest at installment amount
    principal_portion = amount - interest_portion
else:
    # FLAT interest: constant ratio is correct
    interest_portion = _round_currency(
        amount * (loan.total_interest / loan.total_repayable)
    )
    principal_portion = amount - interest_portion
```

**Note:** Check if `InterestMethod` enum exists and what value names it uses. Also apply same fix at `record_manual_payment` (line ~731).

---

### Task 4: Fix bulk LWP calculation ignoring half-day flag

**Files:**
- Modify: `app/services/people/payroll/leave_adapter.py:210-237`

**Problem:** `get_bulk_lwp_days` doesn't select or check `half_day` flag, while `get_lwp_days` correctly adjusts for it.

**Fix:** Add `half_day` to the select and adjust the overlap calculation:
```python
# Add half_day to select (line 211-214)
stmt = (
    select(
        LeaveApplication.employee_id,
        LeaveApplication.from_date,
        LeaveApplication.to_date,
        LeaveApplication.half_day,
    )
    .join(LeaveType, LeaveApplication.leave_type_id == LeaveType.leave_type_id)
    ...
)

# Fix aggregation loop (lines 233-237)
for row in results:
    overlap_start = max(row.from_date, period_start)
    overlap_end = min(row.to_date, period_end)
    overlap_days = Decimal(str((overlap_end - overlap_start).days + 1))
    # Adjust for half-day leaves (matching get_lwp_days behavior)
    if row.half_day and overlap_days == 1:
        lwp_by_emp[row.employee_id] += Decimal("0.5")
    else:
        lwp_by_emp[row.employee_id] += overlap_days
```

---

## Group B: Multi-Tenancy Violations

### Task 5: Add org_id filter to DisciplineService.get_case

**Files:**
- Modify: `app/services/people/discipline/discipline_service.py:105-117`

**Fix:**
```python
def get_case(self, case_id: UUID, organization_id: UUID | None = None) -> DisciplinaryCase | None:
    """Get a single case by ID."""
    case = self.db.get(DisciplinaryCase, case_id)
    if case and case.is_deleted:
        return None
    if case and organization_id is not None and case.organization_id != organization_id:
        return None
    return case

def get_case_or_404(self, case_id: UUID, organization_id: UUID | None = None) -> DisciplinaryCase:
    """Get case or raise NotFoundError."""
    case = self.get_case(case_id, organization_id=organization_id)
    if not case:
        raise NotFoundError(f"Disciplinary case {case_id} not found")
    return case
```

**Callers to update:** Search all callers of `get_case` and `get_case_or_404` and pass `organization_id` where available.

---

### Task 6: Make org_id required in LoanService.get_active_loans_for_employee

**Files:**
- Modify: `app/services/people/payroll/loan_service.py:453-477`

**Fix:** Make `organization_id` a required parameter and always filter by it:
```python
def get_active_loans_for_employee(
    self,
    employee_id: UUID,
    as_of_date: date | None = None,
    organization_id: UUID | None = None,
) -> list[EmployeeLoan]:
    """Get all active (disbursed, not completed) loans for an employee."""
    emp_id = coerce_uuid(employee_id)

    stmt = (
        select(EmployeeLoan)
        .where(
            EmployeeLoan.employee_id == emp_id,
            EmployeeLoan.status == LoanStatus.DISBURSED,
            EmployeeLoan.outstanding_balance > 0,
        )
        .order_by(EmployeeLoan.disbursement_date)
    )

    if organization_id is not None:
        stmt = stmt.where(
            EmployeeLoan.organization_id == coerce_uuid(organization_id)
        )
    else:
        logger.warning(
            "get_active_loans_for_employee called without organization_id for employee %s",
            emp_id,
        )

    return list(self.db.scalars(stmt).all())
```

---

### Task 7: Add org_id check to record_payroll_deduction

**Files:**
- Modify: `app/services/people/payroll/loan_service.py:616-637`

**Fix:** Add organization_id parameter and validation:
```python
def record_payroll_deduction(
    self,
    loan_id: UUID,
    slip_id: UUID,
    amount: Decimal,
    principal_portion: Decimal,
    interest_portion: Decimal,
    repayment_date: date,
    created_by_id: UUID | None = None,
    skip_link_creation: bool = False,
    organization_id: UUID | None = None,
) -> LoanRepayment:
    ...
    loan = self.db.get(EmployeeLoan, l_id)
    if not loan:
        raise ValueError(f"Loan {loan_id} not found")

    if organization_id is not None and loan.organization_id != organization_id:
        raise ValueError(f"Loan {loan_id} does not belong to organization {organization_id}")
    ...
```

**Callers to update:** Pass `organization_id` from callers.

---

### Task 8: Add org_id filter to ProbationService.get_employee

**Files:**
- Modify: `app/services/people/hr/probation_service.py:128-130`

**Fix:**
```python
def get_employee(self, employee_id: UUID, organization_id: UUID | None = None) -> Employee | None:
    """Get employee by ID with optional org isolation."""
    employee = self.db.get(Employee, employee_id)
    if employee and organization_id is not None and employee.organization_id != organization_id:
        return None
    return employee
```

**Callers to update:** Pass `organization_id` where available.

---

## Group C: CLAUDE.md Rule Violations

### Task 9: Replace db.commit() with db.flush() in PayrollLifecycle

**Files:**
- Modify: `app/services/people/payroll/lifecycle.py` — lines 279, 369, 454, 764, 852, 995

**Fix:** At each of the 6 locations, replace:
```python
self.db.commit()
```
with:
```python
self.db.flush()
```

The comment "Commit before emitting event so handlers see committed state" should be updated to note that the caller is responsible for committing. If the event dispatch pattern requires committed state, the event should be emitted after the route/task commits.

---

### Task 10: Replace db.commit() with db.flush() in expense_ap_adapter

**Files:**
- Modify: `app/services/people/integrations/expense_ap_adapter.py:203,226,228`

**Fix:** Replace `db.commit()` at line 203 with `db.flush()`. Remove the error-handling commit/rollback at lines 226-228 (let the caller handle transaction boundaries). Keep the logging.

---

### Task 11: Convert db.query() to select() in PayrollService

**Files:**
- Modify: `app/services/people/payroll/payroll_service.py:1556-1610`

**Fix:** Rewrite both queries using SQLAlchemy 2.0 pattern:
```python
# BEFORE (line 1556-1584)
base_results = (
    self.db.query(
        SalarySlip.employee_id,
        Employee.employee_code,
        Person.name_expr().label("employee_name"),
        ...
    )
    .select_from(SalarySlip)
    .join(...)
    .filter(...)
    .group_by(...)
    .all()
)

# AFTER
base_stmt = (
    select(
        SalarySlip.employee_id,
        Employee.employee_code,
        Person.name_expr().label("employee_name"),
        func.coalesce(Employee.department_id, None).label("department_name"),
        func.count(SalarySlip.slip_id).label("slip_count"),
        func.sum(SalarySlip.gross_pay).label("total_gross"),
        func.sum(SalarySlip.total_deduction).label("total_deductions"),
        func.sum(SalarySlip.net_pay).label("total_net"),
    )
    .select_from(SalarySlip)
    .join(Employee, SalarySlip.employee_id == Employee.employee_id)
    .join(Person, Employee.person_id == Person.id)
    .where(
        SalarySlip.organization_id == org_id,
        SalarySlip.start_date >= year_start,
        SalarySlip.end_date <= year_end,
    )
    .group_by(
        SalarySlip.employee_id,
        Employee.employee_code,
        Person.first_name,
        Person.last_name,
        Employee.department_id,
    )
    .order_by(Person.first_name, Person.last_name)
)
base_results = self.db.execute(base_stmt).all()
```

Same pattern for the deduction_results query at line 1587-1610.

---

## Group D: Logic Bugs

### Task 12: Fix schedule generator hardcoded % 2

**Files:**
- Modify: `app/services/people/scheduling/schedule_generator.py:497`

**Problem:** `% 2` hardcodes 2-week cycles. The `ShiftPattern` model has a `cycle_weeks` field.

**Fix:**
```python
# BEFORE
adjusted_week = (week_number + assignment.rotation_week_offset) % 2

# AFTER
cycle_length = pattern.cycle_weeks or 2
adjusted_week = (week_number + assignment.rotation_week_offset) % cycle_length
```

---

### Task 13: Fix lifecycle update methods that can't clear optional fields

**Files:**
- Modify: `app/services/people/hr/lifecycle.py` — lines 174-176, 383-385, 527-529, 647-649

**Problem:** `if value is not None` prevents clearing fields by passing `None`.

**Fix:** Use a sentinel to distinguish "not provided" from "explicitly None":
```python
_UNSET = object()

# In each update method, change:
for key, value in kwargs.items():
    if value is not None and hasattr(onboarding, key):
        setattr(onboarding, key, value)

# To:
for key, value in kwargs.items():
    if hasattr(onboarding, key):
        setattr(onboarding, key, value)
```

**Note:** This is the simpler fix. If callers pass kwargs they don't intend to set, the sentinel approach is needed. Check callers to verify.

---

### Task 14: Fix discarded await request.form() result

**Files:**
- Modify: `app/web/people/self_service.py:681-682`

**Fix:**
```python
# BEFORE
form = getattr(request.state, "csrf_form", None)
if form is None:
    await request.form()

# AFTER
form = getattr(request.state, "csrf_form", None)
if form is None:
    form = await request.form()
```

---

### Task 15: Fix attendance adapter using only last day's expected hours

**Files:**
- Modify: `app/services/people/payroll/attendance_adapter.py:262-270`

**Problem:** `expected_hours_per_day` is overwritten each iteration and only the last day's value is used.

**Fix:** Accumulate expected hours per working day:
```python
# Add accumulator before the while loop (after line 214)
total_expected_hours = Decimal("0")

# Inside the while loop, after getting expected_hours_per_day (line 220-222):
if not is_weekend:
    total_expected_hours += expected_hours_per_day

# Replace lines 262-270 with:
summary.expected_working_hours = total_expected_hours.quantize(
    Decimal("0.01"), rounding=ROUND_HALF_UP
)
```

---

## Group E: Template Pattern Violations

### Task 16: Replace inline badge HTML with status_badge() macro

**Files to modify:**
1. `templates/people/payroll/slips.html:151-161` — remove `status_colors` dict, use `{{ status_badge(slip.status.value, 'sm') }}`
2. `templates/people/perf/appraisals.html:93-103` — same
3. `templates/people/hr/discipline/cases.html:125-148` — replace both severity and status inline badges
4. `templates/people/recruit/applicant_detail.html:32-48` — replace inline status badge
5. `templates/people/hr/departments.html:55-62` — replace Active/Inactive ternary with `{{ status_badge('ACTIVE' if dept.is_active else 'INACTIVE', 'sm') }}`
6. `templates/people/hr/employee_detail.html:172-183,194-197,631-634` — replace all 3 inline badge blocks

Each template must import the macro if not already imported:
```jinja2
{% from "components/_badges.html" import status_badge %}
```

---

### Task 17: Fix results-container placement

**Files to modify:**
1. `templates/people/attendance/records.html:189` — move `<div id="results-container">` to wrap the records table + pagination, not the Quick Reports card
2. `templates/people/self/payslips.html:49-52` — remove malformed `<div id="results-container">` from inside `<a>` tag; place it wrapping the grid
3. `templates/people/leave/applications.html:75` — move `<div id="results-container">` outside the `{% if applications %}` block
4. `templates/people/perf/appraisals.html:63` — same as above

---

### Task 18: Standardize CSRF pattern to `{{ request.state.csrf_form | safe }}`

**Files to modify** — in each, find `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">` or `{{ request.state.csrf_token }}` and replace with `{{ request.state.csrf_form | safe }}`:
1. `templates/people/hr/employee_form.html:46`
2. `templates/people/hr/employee_detail.html:94,100,106,116,127,138,201,219,231,299,311,355`
3. `templates/people/hr/department_form.html:29`
4. `templates/people/leave/applications.html:79`
5. `templates/people/leave/application_form.html:39`
6. `templates/people/payroll/runs.html:124`
7. `templates/people/payroll/slips.html:177`
8. `templates/people/payroll/slip_detail.html:24,39`
9. `templates/people/perf/appraisal_form.html:29`

---

### Task 19: Add enum filter chain to raw enum displays

**Files to modify:**
1. `templates/people/self/leave.html:131` — change `{{ app.status.value }}` to `{{ app.status.value | replace('_', ' ') | title }}`
2. `templates/people/self/attendance.html:44,87` — same for `{{ today_record.status.value }}` and `{{ rec.status.value }}`
3. `templates/people/payroll/run_detail.html:354` — change `{{ s | title }}` to `{{ s | replace('_', ' ') | title }}`

---

### Task 20: Add missing {% else %} + empty_state() to for loops

**Files to modify:**
1. `templates/people/attendance/records.html:242` — add `{% else %}` with empty_state
2. `templates/people/self/leave.html:119` — replace separate `{% if %}` block with `{% else %}` on the `{% for %}` loop

---
