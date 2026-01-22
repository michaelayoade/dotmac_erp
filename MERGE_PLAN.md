# Dotmac Unified Platform: People + Finance Merge Plan

## Overview

This plan consolidates **Dotmac People Ops** into **Dotmac ERP** (the accounting app), creating a unified ERP platform. The Books app becomes the foundation, with People modules integrated as first-class citizens.

## Target Architecture

### Route Structure (After Merge)

```
/                           → Landing/Dashboard
/dashboard                  → Unified Dashboard

# Finance Modules (renamed from /ifrs/*)
/finance/gl/*               → General Ledger
/finance/ap/*               → Accounts Payable
/finance/ar/*               → Accounts Receivable
/finance/fa/*               → Fixed Assets
/finance/inv/*              → Inventory
/finance/tax/*              → Tax
/finance/banking/*          → Banking
/finance/reports/*          → Financial Reports

# People Modules (from dotmac_people)
/people/hr/*                → HR (employees, departments, org structure)
/people/payroll/*           → Payroll (salary, payslips, runs)
/people/leave/*             → Leave Management
/people/attendance/*        → Attendance & Shifts
/people/recruitment/*       → Recruitment (jobs, applicants, offers)
/people/training/*          → Training Programs
/people/performance/*       → Performance (KPIs, reviews, scorecards)
/people/expenses/*          → Expense Claims & Advances

# Shared
/settings/*                 → Unified Settings
/admin/*                    → Admin Panel
/api/v1/*                   → REST API
```

---

## Directory Structure (After Merge)

```
app/
├── api/
│   ├── auth.py                    # Unified auth endpoints
│   ├── finance/                   # Renamed from ifrs/
│   │   ├── __init__.py
│   │   ├── gl.py
│   │   ├── ap.py
│   │   ├── ar.py
│   │   ├── fa.py
│   │   ├── inv.py
│   │   ├── tax.py
│   │   ├── banking.py
│   │   ├── lease.py
│   │   ├── fin_inst.py
│   │   ├── cons.py
│   │   └── reports.py
│   └── people/                    # NEW: From dotmac_people
│       ├── __init__.py
│       ├── hr.py                  # Employees, departments
│       ├── payroll.py             # Salary structures, runs, slips
│       ├── leave.py               # Leave types, applications
│       ├── attendance.py          # Attendance, shifts
│       ├── recruitment.py         # Jobs, applicants, interviews
│       ├── training.py            # Programs, events
│       ├── performance.py         # KPIs, scorecards, reviews
│       └── expenses.py            # Claims, advances, cards
│
├── models/
│   ├── __init__.py                # Base models (Person, Org, etc.)
│   ├── auth.py                    # Auth models
│   ├── person.py                  # Unified identity (extended)
│   ├── rbac.py                    # RBAC models
│   ├── mixins.py                  # Shared mixins (audit, soft-delete)
│   │
│   ├── finance/                   # Renamed from ifrs/
│   │   ├── __init__.py
│   │   ├── gl/                    # General Ledger
│   │   ├── ap/                    # Accounts Payable
│   │   ├── ar/                    # Accounts Receivable
│   │   ├── fa/                    # Fixed Assets
│   │   ├── inv/                   # Inventory
│   │   ├── tax/                   # Tax
│   │   ├── banking/               # Banking
│   │   ├── lease/                 # Leases
│   │   ├── fin_inst/              # Financial Instruments
│   │   ├── cons/                  # Consolidation
│   │   ├── core_org/              # Organization (shared)
│   │   ├── core_fx/               # FX Rates (shared)
│   │   ├── core_config/           # Config (shared)
│   │   └── platform/              # Events, Saga (shared)
│   │
│   └── people/                    # NEW: From dotmac_people
│       ├── __init__.py
│       ├── employee.py            # Employee (linked to Person)
│       ├── department.py          # Department, Designation
│       ├── payroll.py             # Salary components, structures, slips
│       ├── leave.py               # Leave types, allocations, applications
│       ├── attendance.py          # Attendance, shifts
│       ├── recruitment.py         # Job openings, applicants, offers
│       ├── training.py            # Programs, events, results
│       ├── performance.py         # KPIs, KRAs, scorecards, reviews
│       ├── expenses.py            # Claims, advances, cards, policies
│       └── assets.py              # Asset assignments (not depreciation)
│
├── schemas/
│   ├── finance/                   # Renamed from ifrs/
│   │   └── *.py
│   └── people/                    # NEW
│       ├── hr.py
│       ├── payroll.py
│       ├── leave.py
│       ├── attendance.py
│       ├── recruitment.py
│       ├── training.py
│       ├── performance.py
│       └── expenses.py
│
├── services/
│   ├── auth.py                    # Unified auth service
│   ├── rbac.py                    # RBAC service
│   ├── cache.py                   # Caching
│   │
│   ├── finance/                   # Renamed from ifrs/
│   │   ├── gl/
│   │   ├── ap/
│   │   ├── ar/
│   │   ├── fa/
│   │   ├── inv/
│   │   ├── tax/
│   │   ├── banking/
│   │   ├── common/
│   │   └── platform/
│   │
│   └── people/                    # NEW: From dotmac_people
│       ├── __init__.py
│       ├── hr_service.py          # Employee CRUD
│       ├── payroll_service.py     # Payroll processing
│       ├── payroll_engine.py      # Calculation engine
│       ├── leave_service.py       # Leave management
│       ├── attendance_service.py  # Attendance tracking
│       ├── recruitment_service.py # Recruitment workflow
│       ├── training_service.py    # Training management
│       ├── performance_service.py # Performance reviews
│       ├── expense_service.py     # Expense claims
│       └── integrations/          # Posting adapters
│           ├── payroll_gl_adapter.py   # Payroll → GL
│           ├── expense_ap_adapter.py   # Expenses → AP
│           └── asset_fa_adapter.py     # Assets link
│
├── web/
│   ├── auth.py                    # Auth web routes
│   ├── admin.py                   # Admin web routes
│   ├── finance/                   # Renamed from ifrs/
│   │   ├── __init__.py
│   │   ├── dashboard.py
│   │   ├── gl.py
│   │   ├── ap.py
│   │   ├── ar.py
│   │   └── *.py
│   └── people/                    # NEW
│       ├── __init__.py
│       ├── dashboard.py           # People dashboard
│       ├── hr.py                  # Employee pages
│       ├── payroll.py             # Payroll pages
│       ├── leave.py               # Leave pages
│       ├── attendance.py          # Attendance pages
│       ├── recruitment.py         # Recruitment pages
│       ├── training.py            # Training pages
│       ├── performance.py         # Performance pages
│       └── expenses.py            # Expense pages
│
└── tasks/
    ├── finance/                   # Finance background tasks
    └── people/                    # NEW: People background tasks
        ├── payroll_tasks.py       # Payroll processing
        ├── leave_tasks.py         # Leave accruals
        └── notification_tasks.py  # HR notifications

templates/
├── base.html                      # Landing page
├── components/                    # Shared UI components
├── finance/                       # Renamed from ifrs/
│   ├── base_finance.html          # Finance layout
│   ├── dashboard.html
│   ├── gl/
│   ├── ap/
│   ├── ar/
│   └── *.html
└── people/                        # NEW
    ├── base_people.html           # People layout (extends base)
    ├── dashboard.html             # People dashboard
    ├── hr/
    │   ├── employee_list.html
    │   ├── employee_form.html
    │   ├── employee_detail.html
    │   ├── department_list.html
    │   └── org_chart.html
    ├── payroll/
    │   ├── run_list.html
    │   ├── run_form.html
    │   ├── payslip_list.html
    │   └── payslip_detail.html
    ├── leave/
    │   ├── application_list.html
    │   ├── application_form.html
    │   └── calendar.html
    ├── attendance/
    │   ├── daily_view.html
    │   └── shift_list.html
    ├── recruitment/
    │   ├── job_list.html
    │   ├── applicant_list.html
    │   └── interview_schedule.html
    ├── training/
    │   ├── program_list.html
    │   └── event_calendar.html
    ├── performance/
    │   ├── review_list.html
    │   ├── scorecard.html
    │   └── kpi_dashboard.html
    └── expenses/
        ├── claim_list.html
        ├── claim_form.html
        ├── advance_list.html
        └── policy_list.html
```

---

## Database Schema Changes

### New PostgreSQL Schemas

```sql
-- Add new schemas for People modules
CREATE SCHEMA IF NOT EXISTS hr;        -- Employees, departments
CREATE SCHEMA IF NOT EXISTS payroll;   -- Salary, payslips
CREATE SCHEMA IF NOT EXISTS leave;     -- Leave management
CREATE SCHEMA IF NOT EXISTS recruit;   -- Recruitment
CREATE SCHEMA IF NOT EXISTS training;  -- Training
CREATE SCHEMA IF NOT EXISTS perf;      -- Performance
CREATE SCHEMA IF NOT EXISTS expense;   -- Expense claims
```

### Key Model Adaptations

#### 1. Employee Model (People → Finance)

**Original (dotmac_people):**
```python
class Employee(Base):
    __tablename__ = "employees"
    company: Mapped[Optional[str]]  # String-based tenancy
```

**Adapted:**
```python
class Employee(Base):
    __tablename__ = "employee"
    __table_args__ = {"schema": "hr"}

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("core_org.organization.id"))
    person_id: Mapped[UUID] = mapped_column(ForeignKey("public.person.id"))  # Link to Person

    employee_number: Mapped[str]
    department_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("hr.department.id"))
    designation_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("hr.designation.id"))
    reports_to_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("hr.employee.id"))

    employment_type: Mapped[EmploymentType]
    date_of_joining: Mapped[date]
    date_of_leaving: Mapped[Optional[date]]
    status: Mapped[EmploymentStatus]

    # Salary moved to payroll schema
    # Bank details stay here for payroll reference
    bank_name: Mapped[Optional[str]]
    bank_account_number: Mapped[Optional[str]]
    bank_sort_code: Mapped[Optional[str]]

    # RLS: Filtered by organization_id
```

#### 2. Expense Claim (People → Finance Integration)

```python
class ExpenseClaim(Base):
    __tablename__ = "expense_claim"
    __table_args__ = {"schema": "expense"}

    id: Mapped[UUID]
    organization_id: Mapped[UUID]
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("hr.employee.id"))

    claim_number: Mapped[str]
    claim_date: Mapped[date]
    total_amount: Mapped[Decimal]
    currency_code: Mapped[str]
    status: Mapped[ExpenseClaimStatus]

    # Finance integration
    ap_invoice_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("ap.supplier_invoice.id"))
    gl_posted: Mapped[bool] = mapped_column(default=False)
    posting_date: Mapped[Optional[date]]
```

#### 3. Payroll Entry (People → Finance Integration)

```python
class PayrollEntry(Base):
    __tablename__ = "payroll_entry"
    __table_args__ = {"schema": "payroll"}

    id: Mapped[UUID]
    organization_id: Mapped[UUID]

    payroll_period_start: Mapped[date]
    payroll_period_end: Mapped[date]
    posting_date: Mapped[date]
    status: Mapped[PayrollStatus]  # DRAFT, SUBMITTED, POSTED

    total_gross: Mapped[Decimal]
    total_deductions: Mapped[Decimal]
    total_net: Mapped[Decimal]

    # Finance integration
    journal_entry_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("gl.journal_entry.id"))
    gl_posted: Mapped[bool] = mapped_column(default=False)
```

---

## Integration Points

### 1. Payroll → GL Posting

When a payroll run is finalized:

```python
# services/people/integrations/payroll_gl_adapter.py

@dataclass
class PayrollGLPostingInput:
    payroll_entry_id: UUID
    organization_id: UUID
    posting_date: date

class PayrollGLAdapter:
    def post_payroll_to_gl(self, db: Session, input: PayrollGLPostingInput) -> JournalEntry:
        """Create GL journal entry from payroll run."""
        payroll = self._get_payroll_entry(db, input.payroll_entry_id)

        lines = []

        # Debit: Salary Expense accounts (by department/cost center)
        for slip in payroll.salary_slips:
            for earning in slip.earnings:
                lines.append(JournalEntryLineInput(
                    account_id=self._get_expense_account(earning.component),
                    debit_amount=earning.amount,
                    cost_center_id=slip.employee.department.cost_center_id,
                ))

        # Credit: Salary Payable (liability)
        lines.append(JournalEntryLineInput(
            account_id=self._get_salary_payable_account(),
            credit_amount=payroll.total_net,
        ))

        # Credit: Statutory deductions (PAYE, Pension, etc.)
        for deduction_type, amount in payroll.deduction_totals.items():
            lines.append(JournalEntryLineInput(
                account_id=self._get_deduction_liability_account(deduction_type),
                credit_amount=amount,
            ))

        # Create and post journal entry
        journal = journal_service.create_journal_entry(db, input.organization_id, ...)
        ledger_posting_service.post_journal(db, journal.id)

        # Link back to payroll
        payroll.journal_entry_id = journal.id
        payroll.gl_posted = True

        return journal
```

### 2. Expense Claims → AP

When an expense claim is approved:

```python
# services/people/integrations/expense_ap_adapter.py

class ExpenseAPAdapter:
    def post_expense_to_ap(self, db: Session, claim_id: UUID) -> SupplierInvoice:
        """Create AP invoice from approved expense claim."""
        claim = self._get_expense_claim(db, claim_id)
        employee = claim.employee

        # Find or create employee as "supplier" for reimbursements
        supplier = self._get_or_create_employee_supplier(db, employee)

        # Create supplier invoice
        invoice_input = SupplierInvoiceInput(
            supplier_id=supplier.id,
            invoice_number=f"EXP-{claim.claim_number}",
            invoice_date=claim.claim_date,
            due_date=claim.claim_date + timedelta(days=7),  # Quick payment
            lines=[
                SupplierInvoiceLineInput(
                    expense_account_id=line.expense_category.gl_account_id,
                    amount=line.amount,
                    description=line.description,
                )
                for line in claim.lines
            ]
        )

        invoice = supplier_invoice_service.create(db, claim.organization_id, invoice_input)

        # Link and post
        claim.ap_invoice_id = invoice.id
        ap_posting_adapter.post_invoice(db, invoice.id)

        return invoice
```

### 3. Asset Assignment → FA

```python
# Link People asset assignments to Finance fixed assets

class AssetAssignment(Base):
    __tablename__ = "asset_assignment"
    __table_args__ = {"schema": "hr"}

    id: Mapped[UUID]
    organization_id: Mapped[UUID]
    employee_id: Mapped[UUID]

    # Link to Finance FA module
    fa_asset_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("fa.asset.id"))

    assigned_date: Mapped[date]
    return_date: Mapped[Optional[date]]
    condition_on_assignment: Mapped[AssetCondition]
    condition_on_return: Mapped[Optional[AssetCondition]]
```

---

## Migration Steps

### Phase 1: Prepare Foundation (Week 1)

1. **Rename IFRS to Finance**
   ```bash
   # Rename directories
   mv app/api/ifrs app/api/finance
   mv app/models/ifrs app/models/finance
   mv app/schemas/ifrs app/schemas/finance
   mv app/services/ifrs app/services/finance
   mv app/web/ifrs app/web/finance
   mv templates/ifrs templates/finance

   # Update imports across codebase
   # Update route prefixes in main.py
   ```

2. **Create People module structure**
   ```bash
   mkdir -p app/api/people
   mkdir -p app/models/people
   mkdir -p app/schemas/people
   mkdir -p app/services/people/integrations
   mkdir -p app/web/people
   mkdir -p templates/people/{hr,payroll,leave,attendance,recruitment,training,performance,expenses}
   ```

3. **Create database schemas**
   ```sql
   CREATE SCHEMA hr;
   CREATE SCHEMA payroll;
   CREATE SCHEMA leave;
   CREATE SCHEMA recruit;
   CREATE SCHEMA training;
   CREATE SCHEMA perf;
   CREATE SCHEMA expense;
   ```

### Phase 2: Copy and Adapt Models (Week 2)

1. **Copy models from dotmac_people**
   - Add `organization_id` to all models
   - Change PKs from `int` to `UUID`
   - Add schema assignments
   - Remove `company` string field
   - Link Employee to Person

2. **Create Alembic migrations**
   ```bash
   alembic revision --autogenerate -m "add_people_schemas"
   ```

3. **Add RLS policies**
   ```sql
   -- For each people table
   ALTER TABLE hr.employee ENABLE ROW LEVEL SECURITY;
   CREATE POLICY employee_org_isolation ON hr.employee
       USING (organization_id = current_setting('app.current_organization_id')::uuid);
   ```

### Phase 3: Copy and Adapt Services (Week 3)

1. **Copy services from dotmac_people**
   - Update imports to use new model locations
   - Change `company` filtering to `organization_id`
   - Integrate with Books' caching and event patterns

2. **Create integration adapters**
   - `PayrollGLAdapter`
   - `ExpenseAPAdapter`
   - `AssetFAAdapter`

### Phase 4: API and Web Routes (Week 4)

1. **Create thin API wrappers**
   ```python
   # app/api/people/hr.py
   router = APIRouter(prefix="/people/hr", tags=["People - HR"])

   @router.get("/employees")
   async def list_employees(
       db: Session = Depends(get_db),
       org_id: UUID = Depends(require_tenant_auth),
       limit: int = 50,
       offset: int = 0,
   ):
       return hr_service.list_employees(db, org_id, limit, offset)
   ```

2. **Create thin web wrappers**
   ```python
   # app/web/people/hr.py
   router = APIRouter(prefix="/people/hr")

   @router.get("/employees")
   async def employees_page(
       request: Request,
       db: Session = Depends(get_db),
       current_user: Person = Depends(require_web_auth),
   ):
       employees = hr_service.list_employees(db, current_user.organization_id)
       return templates.TemplateResponse("people/hr/employee_list.html", {
           "request": request,
           "employees": employees,
       })
   ```

### Phase 5: Templates (Week 5)

1. **Create base_people.html** extending base_finance.html
2. **Port templates from dotmac_people**
   - Update to use Books' component library
   - Integrate with existing design system
3. **Add People navigation to sidebar**

### Phase 6: Integration Testing (Week 6)

1. Test payroll → GL posting
2. Test expense → AP posting
3. Test asset assignment linking
4. Verify RLS isolation
5. End-to-end workflow tests

---

## Account Mapping Configuration

Create settings for GL account mappings:

```python
# app/models/people/settings.py

class PeopleGLMapping(Base):
    __tablename__ = "gl_mapping"
    __table_args__ = {"schema": "hr"}

    id: Mapped[UUID]
    organization_id: Mapped[UUID]

    # Payroll accounts
    salary_expense_account_id: Mapped[UUID]        # Default salary expense
    salary_payable_account_id: Mapped[UUID]        # Salary liability
    paye_payable_account_id: Mapped[UUID]          # PAYE withholding
    pension_payable_account_id: Mapped[UUID]       # Pension liability
    nhf_payable_account_id: Mapped[Optional[UUID]] # NHF liability

    # Expense accounts
    travel_expense_account_id: Mapped[Optional[UUID]]
    meals_expense_account_id: Mapped[Optional[UUID]]
    supplies_expense_account_id: Mapped[Optional[UUID]]

    # Cash advance
    employee_advance_account_id: Mapped[UUID]      # Asset: Advances to employees
```

---

## Unified Navigation

Update sidebar to include People modules:

```html
<!-- templates/components/sidebar.html -->

<!-- Finance Section -->
<div class="nav-section">
    <div class="nav-header">Finance</div>
    <a href="/finance/dashboard">Dashboard</a>
    <a href="/finance/gl/accounts">Chart of Accounts</a>
    <a href="/finance/gl/journals">Journal Entries</a>
    <a href="/finance/ap/suppliers">Suppliers</a>
    <a href="/finance/ar/customers">Customers</a>
    <!-- ... -->
</div>

<!-- People Section -->
<div class="nav-section">
    <div class="nav-header">People</div>
    <a href="/people/dashboard">Dashboard</a>
    <a href="/people/hr/employees">Employees</a>
    <a href="/people/hr/departments">Departments</a>
    <a href="/people/payroll/runs">Payroll Runs</a>
    <a href="/people/leave/applications">Leave</a>
    <a href="/people/attendance">Attendance</a>
    <a href="/people/recruitment/jobs">Recruitment</a>
    <a href="/people/training/programs">Training</a>
    <a href="/people/performance/reviews">Performance</a>
    <a href="/people/expenses/claims">Expenses</a>
</div>
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Data loss during migration | Full backup before each phase; staged rollout |
| RLS policy gaps | Audit all queries; test with multiple orgs |
| Breaking existing Finance features | Comprehensive test suite; feature flags |
| Performance degradation | Monitor query plans; index strategy |
| Auth inconsistencies | Single auth system (Books'); SSO testing |

---

## Success Criteria

- [ ] All People modules accessible at `/people/*` routes
- [ ] Finance modules working at `/finance/*` routes
- [ ] Payroll posting creates balanced GL entries
- [ ] Expense approval creates AP invoice
- [ ] RLS enforces tenant isolation on all People tables
- [ ] Single sign-on across all modules
- [ ] Unified dashboard with Finance + People metrics
- [ ] All existing tests pass
- [ ] New integration tests pass

---

## Timeline Summary

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| 1. Foundation | 1 week | Renamed structure, empty modules |
| 2. Models | 1 week | Migrated models with org_id |
| 3. Services | 1 week | Business logic + adapters |
| 4. API/Web | 1 week | Thin route wrappers |
| 5. Templates | 1 week | UI pages |
| 6. Testing | 1 week | Integration verified |

**Total: ~6 weeks for core merge**

---

## Next Steps

1. Review and approve this plan
2. Create feature branch: `feature/people-merge`
3. Start Phase 1: Rename IFRS → Finance
4. Proceed incrementally with CI/CD checks
