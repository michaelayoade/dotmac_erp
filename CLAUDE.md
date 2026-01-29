# DotMac ERP

IFRS-based multi-tenant ERP system with Finance, HR, Operations, and People modules.

## Quick Commands

```bash
# Development
poetry install                              # Install dependencies
alembic upgrade head                        # Apply database migrations
python -m uvicorn app.main:app --reload     # Run dev server (port 8000)

# Testing
pytest                                      # Run full test suite
pytest tests/path/test_file.py -v           # Run specific test file
pytest -k "test_name" -v                    # Run tests by name pattern
pytest --cov=app tests/                     # Run with coverage

# Type checking & linting
mypy app/                                   # Type check (MUST pass before committing)
ruff check app/                             # Lint check
ruff format app/                            # Format code

# Database
alembic revision --autogenerate -m "desc"   # Create migration
alembic upgrade head                        # Apply migrations
alembic downgrade -1                        # Rollback last migration

# Celery (background tasks)
celery -A app.celery_app worker --loglevel=info   # Run worker
celery -A app.celery_app beat --loglevel=info     # Run scheduler
```

## Architecture

### Directory Structure
```
app/
├── api/           # FastAPI route handlers (thin layer, delegates to services)
├── models/        # SQLAlchemy ORM models organized by domain
├── schemas/       # Pydantic request/response models
├── services/      # Business logic (ALL complex logic lives here)
├── middleware/    # Rate limiting, caching, auth
├── tasks/         # Celery background tasks (call services, don't contain logic)
├── web/           # Server-rendered HTML routes (Jinja2 templates)
templates/         # Jinja2 HTML templates
tests/
├── e2e/           # End-to-end tests (Playwright)
├── ifrs/          # Domain-specific unit tests
├── integration/   # Integration tests
└── services/      # Service layer tests
```

### Module Domains
| Module | Path | Description |
|--------|------|-------------|
| Finance/GL | `app/services/finance/gl/` | Chart of accounts, journals, fiscal periods |
| Finance/AR | `app/services/finance/ar/` | Customers, invoices, receipts, aging |
| Finance/AP | `app/services/finance/ap/` | Suppliers, bills, payments, aging |
| Finance/Banking | `app/services/finance/banking/` | Bank accounts, reconciliation |
| Finance/Tax | `app/services/finance/tax/` | Tax periods, returns, calculations |
| Finance/FA | `app/services/finance/fa/` | Fixed assets, depreciation |
| Finance/Inv | `app/services/finance/inv/` | Inventory, warehouses, BOMs |
| People/HR | `app/services/people/hr/` | Employees, organization, lifecycle |
| People/Payroll | `app/services/people/payroll/` | Salary, payslips |
| People/Leave | `app/services/people/leave/` | Leave management |
| People/Recruit | `app/services/people/recruit/` | Job postings, applications |
| People/Discipline | `app/services/people/discipline/` | Policy violations, queries, hearings |
| Operations/PM | `app/services/pm/` | Projects, tasks, time entries |
| Expense | `app/services/expense/` | Expense claims and approvals |

### Key Patterns

**Service Layer Pattern**: ALL business logic in services, never in routes or tasks.

**CRITICAL RULE**: API routes (`app/api/`) and web routes (`app/web/`) are **thin wrappers only**.
- NO database queries in routes (no `select()`, `db.query()`, `db.add()`)
- NO business logic in routes (no conditionals, calculations, validations)
- Routes ONLY: parse request → call service → return response
- ALL logic lives in `app/services/`

```python
# CORRECT: Route delegates to service
@router.post("/invoices")
def create_invoice(data: InvoiceCreate, db: Session = Depends(get_db)):
    service = InvoiceService(db)
    return service.create(data)

# CORRECT: Web route delegates to service
@router.get("/invoices")
def list_invoices(request: Request, db: Session = Depends(get_db)):
    service = InvoiceService(db)
    invoices = service.list_for_org(get_org_id(request))
    return templates.TemplateResponse("invoices.html", {"invoices": invoices})

# WRONG: Logic in route handler
@router.post("/invoices")
def create_invoice(data: InvoiceCreate, db: Session = Depends(get_db)):
    invoice = Invoice(**data.dict())  # Don't do this
    db.add(invoice)                   # Don't do this
    if invoice.amount > 10000:        # Don't do this
        send_approval_email(...)      # Don't do this
```

**Background Tasks Pattern**: Tasks orchestrate, services do the work.
```python
# app/tasks/finance.py
@shared_task
def process_overdue_invoices():
    with SessionLocal() as db:
        service = ARAgingService(db)
        overdue = service.get_overdue_invoices()  # Service does the query
        notification_service.notify_overdue(db, overdue)  # Service sends
```

**Notification Pattern**: Use NotificationService for all notifications.
```python
from app.services.notification import NotificationService
from app.models.notification import EntityType, NotificationType

notification_service = NotificationService()
notification_service.create(
    db, org_id, recipient_id,
    entity_type=EntityType.SYSTEM,  # or EXPENSE, LEAVE, etc.
    entity_id=entity_uuid,
    notification_type=NotificationType.REMINDER,  # or DUE_SOON, OVERDUE
    title="Title here",
    message="Full message",
    channel=NotificationChannel.BOTH,  # IN_APP, EMAIL, or BOTH
)
```

## Code Style

### Python
- Type hints on ALL function signatures (mypy must pass)
- Use `from __future__ import annotations` if needed for forward refs
- Imports: stdlib, then third-party, then local (absolute imports preferred)
- Line length: 88 chars (black/ruff default)
- Use `Optional[X]` or `X | None` for nullable types

### SQLAlchemy 2.0 Style
```python
# Use Mapped[] type annotations
class Invoice(Base):
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ar.customer.customer_id"))

# Use select() for queries
stmt = select(Invoice).where(Invoice.status == "OPEN")
invoices = db.scalars(stmt).all()
```

### Pydantic v2 Style
```python
from pydantic import BaseModel, ConfigDict

class InvoiceCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # Not orm_mode
    customer_id: uuid.UUID
    amount: Decimal
```

## Testing

### Test Structure
- Tests use SQLite in-memory (conftest patches PostgreSQL UUID)
- Use fixtures from `tests/conftest.py` for db sessions and auth
- Mock external services (ERPNext, email, payment providers)

### Running Tests
```bash
pytest tests/ifrs/ar/test_invoice_service.py -v   # Specific domain
pytest tests/e2e/ -v                               # End-to-end tests
pytest -x                                          # Stop on first failure
pytest --tb=short                                  # Shorter tracebacks
```

## Multi-tenancy

All queries MUST include organization_id filter:
```python
# CORRECT
invoices = db.scalars(
    select(Invoice)
    .where(Invoice.organization_id == org_id)
    .where(Invoice.status == "OPEN")
).all()

# WRONG - leaks data across tenants
invoices = db.scalars(select(Invoice).where(Invoice.status == "OPEN")).all()
```

## Common Gotchas

### Database
- Always use `db.flush()` after create/update if you need the generated ID
- Use `joinedload()` or `selectinload()` to avoid N+1 queries
- FK constraints are enforced - verify parent exists before creating child

### Async
- Route handlers are sync (not async def) - SQLAlchemy sessions are sync
- Background tasks run in Celery workers, not async

### Type Checking
- Run `mypy app/` before committing - it MUST pass
- Use `# type: ignore` sparingly and only with comment explaining why
- Common fixes:
  - Missing return type: add `-> ReturnType`
  - Optional access: use `if x is not None:` before accessing

### Migrations
- Always review auto-generated migrations before running
- Test migrations on a copy of prod data structure
- Naming: `YYYYMMDD_description.py`

## Discipline Module

The Discipline module handles employee policy violations, queries, and disciplinary actions.

### Core Entities
| Entity | Description |
|--------|-------------|
| `DisciplinaryCase` | Main case record (violation type, status, dates) |
| `CaseWitness` | Witnesses linked to a case |
| `CaseAction` | Actions taken (warning, suspension, termination) |
| `CaseDocument` | Evidence and response documents |
| `CaseResponse` | Employee responses to queries |

### Workflow States
```
DRAFT → QUERY_ISSUED → RESPONSE_RECEIVED → HEARING_SCHEDULED →
HEARING_COMPLETED → DECISION_MADE → APPEAL_FILED → APPEAL_DECIDED → CLOSED
```

### Module Integrations
| Integration | Purpose |
|-------------|---------|
| **Performance** | Link violations to appraisals, affect ratings |
| **Payroll** | Unpaid suspension deductions |
| **Leave** | Block leave during investigation |
| **Lifecycle** | Trigger termination workflow |
| **Training** | Mandatory training as corrective action |
| **Notifications** | Alert employee on queries, status changes |

### Service Structure
```
app/services/people/discipline/
├── __init__.py
├── discipline_service.py      # Core case management
├── case_action_service.py     # Action recording
├── case_response_service.py   # Employee responses
└── web/
    └── discipline_web.py      # Web route helpers (thin)
```

## Environment Variables

Required:
- `DATABASE_URL` - PostgreSQL connection string
- `SECRET_KEY` - JWT signing key
- `REDIS_URL` - Redis for Celery and caching

Optional:
- `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` - ERPNext integration
- `SMTP_*` - Email configuration
- `PAYSTACK_SECRET_KEY` - Payment processing
