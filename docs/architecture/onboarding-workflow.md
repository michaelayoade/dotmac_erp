# Onboarding Workflow Enhancement

**Status:** ✅ Data Model + Service Layer + ATS Integration Implemented (2026-01-28)

## Overview

This document describes enhancements to the existing onboarding models to support:
- Self-service portal for new hires
- Automated reminders
- Task assignments and due dates
- Document collection
- Progress tracking

## Existing Models

The following models already exist in `app/models/people/hr/lifecycle.py`:

- `ChecklistTemplate` / `ChecklistTemplateItem` - Reusable checklist definitions
- `EmployeeOnboarding` / `EmployeeOnboardingActivity` - Per-employee onboarding
- `BoardingStatus` enum - PENDING, IN_PROGRESS, COMPLETED

## Enhancements

### 1. ChecklistTemplateItem Enhancements

Add fields to define richer task templates:

```python
# New fields for ChecklistTemplateItem
category: Mapped[Optional[str]]  # DAY_ONE, FIRST_WEEK, FIRST_MONTH, ONGOING
default_assignee_role: Mapped[Optional[str]]  # HR, MANAGER, IT, EMPLOYEE
days_from_start: Mapped[int]  # Days after start date when due (default 0)
requires_document: Mapped[bool]  # If document upload needed
document_type: Mapped[Optional[str]]  # e.g., ID_COPY, SIGNED_CONTRACT
instructions: Mapped[Optional[str]]  # Detailed instructions for assignee
```

### 2. EmployeeOnboarding Enhancements

Add fields for self-service and tracking:

```python
# New fields for EmployeeOnboarding
template_id: Mapped[Optional[uuid.UUID]]  # FK to checklist_template
self_service_token: Mapped[Optional[str]]  # Token for new hire portal access
self_service_token_expires: Mapped[Optional[datetime]]  # Token expiry
self_service_email_sent: Mapped[bool]  # Track if welcome email sent
expected_completion_date: Mapped[Optional[date]]  # Target completion
actual_completion_date: Mapped[Optional[date]]  # When marked complete
buddy_employee_id: Mapped[Optional[uuid.UUID]]  # Assigned buddy/mentor
manager_id: Mapped[Optional[uuid.UUID]]  # Direct manager for approvals
progress_percentage: Mapped[int]  # Calculated progress (0-100)
```

### 3. EmployeeOnboardingActivity Enhancements

Add fields for assignments and tracking:

```python
# New fields for EmployeeOnboardingActivity
template_item_id: Mapped[Optional[uuid.UUID]]  # FK to template item
category: Mapped[Optional[str]]  # Phase category
due_date: Mapped[Optional[date]]  # Task deadline
assignee_id: Mapped[Optional[uuid.UUID]]  # FK to person (specific assignee)
assigned_to_employee: Mapped[bool]  # True if employee self-service task
requires_document: Mapped[bool]  # If document upload required
document_id: Mapped[Optional[uuid.UUID]]  # FK to uploaded document
completed_by: Mapped[Optional[uuid.UUID]]  # Who completed the task
completion_notes: Mapped[Optional[str]]  # Notes on completion
reminder_sent_at: Mapped[Optional[datetime]]  # Last reminder timestamp
is_overdue: Mapped[bool]  # Computed or updated flag
```

### 4. New ActivityStatus Enum

Replace string status with proper enum:

```python
class ActivityStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_DOCUMENT = "AWAITING_DOCUMENT"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
```

### 5. New OnboardingCategory Enum

Standard categories for phased onboarding:

```python
class OnboardingCategory(str, enum.Enum):
    PRE_BOARDING = "PRE_BOARDING"  # Before start date
    DAY_ONE = "DAY_ONE"           # First day tasks
    FIRST_WEEK = "FIRST_WEEK"     # First week tasks
    FIRST_MONTH = "FIRST_MONTH"   # First month tasks
    ONGOING = "ONGOING"           # Continuous/recurring
```

---

## Migration Strategy

Since these are additive changes to existing tables, use ALTER TABLE:

```sql
-- checklist_template_item additions
ALTER TABLE hr.checklist_template_item
    ADD COLUMN category VARCHAR(30),
    ADD COLUMN default_assignee_role VARCHAR(50),
    ADD COLUMN days_from_start INTEGER DEFAULT 0,
    ADD COLUMN requires_document BOOLEAN DEFAULT FALSE,
    ADD COLUMN document_type VARCHAR(50),
    ADD COLUMN instructions TEXT;

-- employee_onboarding additions
ALTER TABLE hr.employee_onboarding
    ADD COLUMN template_id UUID REFERENCES hr.checklist_template(template_id),
    ADD COLUMN self_service_token VARCHAR(100),
    ADD COLUMN self_service_token_expires TIMESTAMPTZ,
    ADD COLUMN self_service_email_sent BOOLEAN DEFAULT FALSE,
    ADD COLUMN expected_completion_date DATE,
    ADD COLUMN actual_completion_date DATE,
    ADD COLUMN buddy_employee_id UUID REFERENCES hr.employee(employee_id),
    ADD COLUMN manager_id UUID REFERENCES hr.employee(employee_id),
    ADD COLUMN progress_percentage INTEGER DEFAULT 0;

-- employee_onboarding_activity additions
ALTER TABLE hr.employee_onboarding_activity
    ADD COLUMN template_item_id UUID REFERENCES hr.checklist_template_item(item_id),
    ADD COLUMN category VARCHAR(30),
    ADD COLUMN due_date DATE,
    ADD COLUMN assignee_id UUID REFERENCES public.person(person_id),
    ADD COLUMN assigned_to_employee BOOLEAN DEFAULT FALSE,
    ADD COLUMN requires_document BOOLEAN DEFAULT FALSE,
    ADD COLUMN document_id UUID,
    ADD COLUMN completed_by UUID REFERENCES public.person(person_id),
    ADD COLUMN completion_notes TEXT,
    ADD COLUMN reminder_sent_at TIMESTAMPTZ,
    ADD COLUMN is_overdue BOOLEAN DEFAULT FALSE;
```

---

## Self-Service Portal Flow

### 1. Token Generation
When onboarding is created, generate secure token:
```python
import secrets
token = secrets.token_urlsafe(32)
expires = datetime.now() + timedelta(days=30)
```

### 2. Welcome Email
Email sent to new hire with link:
```
https://app.company.com/onboarding/start/{token}
```

### 3. Self-Service Portal
New hire can:
- View their onboarding checklist
- Complete employee tasks
- Upload required documents
- View company information/handbook
- Update personal information

### 4. Authentication
- Token validates access (no login required initially)
- After completing initial tasks, prompt to set password
- Convert to full employee account

---

## Reminder System

### Automated Reminders
Background task checks daily:
1. Find activities where `due_date <= today + 2 days` and `status = PENDING`
2. Send reminder to `assignee_id` or `assignee_role`
3. Update `reminder_sent_at`

### Overdue Handling
1. Find activities where `due_date < today` and `status = PENDING`
2. Set `is_overdue = True`
3. Notify HR admin and manager

---

## Progress Calculation

```python
def calculate_progress(onboarding: EmployeeOnboarding) -> int:
    total = len(onboarding.activities)
    if total == 0:
        return 0
    completed = sum(
        1 for a in onboarding.activities
        if a.status in ('COMPLETED', 'SKIPPED')
    )
    return int((completed / total) * 100)
```

---

## ATS Integration

When a job offer is accepted and converted to an employee (`RecruitmentService.convert_to_employee`), the onboarding is automatically triggered:

```python
employee_id = recruitment_service.convert_to_employee(
    org_id,
    offer_id,
    date_of_joining=start_date,
    create_onboarding=True,           # Auto-create onboarding (default)
    onboarding_template_id=None,      # Use default template
    buddy_employee_id=buddy_id,       # Optional buddy assignment
    manager_id=manager_id,            # Optional manager override
    send_welcome_email=True,          # Send portal link (default)
)
```

The integration:
1. Creates employee from accepted offer
2. Creates onboarding record with checklist from template
3. Links onboarding to job_applicant_id and job_offer_id
4. Queues welcome email via Celery task

---

## Files to Modify

| File | Changes |
|------|---------|
| `app/models/people/hr/lifecycle.py` | Add enums, extend models |
| `app/models/people/hr/checklist_template.py` | Extend ChecklistTemplateItem |
| `alembic/versions/YYYYMMDD_enhance_onboarding.py` | Migration for new columns |

---

## Implementation Order

1. Create migration for new columns
2. Update model classes with new fields
3. Update __init__.py exports
4. Create OnboardingService (Task #5)
5. Create self-service portal (Task #6)
6. Create reminder task (Task #7)
