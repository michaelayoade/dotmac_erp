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
- Type hints on ALL function signatures (mypy must pass), **including private helpers**
- Use `from __future__ import annotations` if needed for forward refs
- Imports: stdlib, then third-party, then local (absolute imports preferred)
- Line length: 88 chars (black/ruff default)
- Use `Optional[X]` or `X | None` for nullable types
- Every service file MUST have a logger: `logger = logging.getLogger(__name__)`

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

### API Pagination Standard
All list endpoints MUST use consistent pagination parameters:
```python
from fastapi import Query

@router.get("/items")
def list_items(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),  # Default 25, max 100
):
    ...
```

## Testing

### Test Structure
- Tests use SQLite in-memory (conftest patches PostgreSQL UUID)
- Use fixtures from `tests/conftest.py` for db sessions and auth
- Mock external services (ERPNext, email, payment providers)

### Test Requirements for New Code
Every new service MUST have a corresponding test file covering:
1. **Happy path** for all public methods
2. **Error cases** (NotFoundError, ValidationError, permission denied)
3. **Multi-tenancy isolation** (org_id filtering prevents cross-tenant data access)
4. **Edge cases** (None values, empty lists, boundary conditions)

```python
# Minimum: 1 test file per service file
# tests/services/test_my_service.py

def test_create_success(db_session):
    """Happy path."""
    service = MyService(db_session)
    result = service.create(valid_data)
    assert result.id is not None

def test_create_missing_required_field(db_session):
    """Validation error case."""
    with pytest.raises(ValidationError):
        service.create(incomplete_data)

def test_list_isolates_by_org(db_session):
    """Multi-tenancy: org A cannot see org B's data."""
    results = service.list_for_org(org_a_id)
    assert all(r.organization_id == org_a_id for r in results)
```

### E2E Test Assertions
Every page test MUST assert something meaningful about the content:
```python
# WRONG - always passes, tests nothing
expect(page.locator("body")).to_be_visible()

# CORRECT - verifies actual content rendered
expect(page.locator("h1")).to_contain_text("Invoices")
expect(page.locator("table")).to_be_visible()
```

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

## Pre-Commit Checklist (MANDATORY)

Before declaring any code complete, run these commands:
```bash
poetry run mypy app/path/to/new/files.py --ignore-missing-imports  # Type check new files
poetry run ruff check app/path/to/new/files.py                      # Lint check
poetry run ruff format app/path/to/new/files.py                     # Format
```

For template changes, also verify:
- Every `<form method="POST">` includes `{{ request.state.csrf_form | safe }}`
- No `| safe` on user-submitted content (use `| sanitize_html` instead)
- New test file exists for any new service file

## Writing New Code - Process

### Step 1: Study Existing Patterns FIRST
Before writing ANY new module, find and read a similar existing module:
```bash
# For a new service, find similar service
ls app/services/finance/payments/  # Example: look at paystack_client.py for API client patterns

# For a new model, find similar model
cat app/models/finance/banking/bank_account.py  # Example: see existing model structure

# For web routes, check existing patterns
cat app/web/finance/payments.py  # Example: see route patterns
```

### Step 2: Match Existing Conventions
- **Imports**: Copy import style from existing files in same directory
- **Type hints**: Use same style (`Optional[X]` vs `X | None`, `List[X]` vs `list[X]`)
- **Docstrings**: Match existing docstring format
- **Error handling**: Use same exception patterns

### Step 3: Verify Before Completion
Run mypy on your new files BEFORE saying "done":
```bash
poetry run mypy app/services/new_module/ --ignore-missing-imports
```

## Clean Code Patterns

### Before Referencing Model Fields
ALWAYS read the model file to verify field names. Each model has its own primary key naming:
```python
# WRONG - assuming generic 'id'
claim.id  # ExpenseClaim uses claim_id
invoice.id  # SupplierInvoice uses invoice_id

# CORRECT - check the model first
claim.claim_id
invoice.invoice_id
payment.payment_id
```

### Cross-Module Integration Pattern
When one module needs to trigger actions in another, use a dispatcher/handler:
```python
# app/services/remita/source_handler.py
class RemitaSourceHandler:
    """Dispatches RRR events to source entity handlers."""

    def handle_rrr_paid(self, rrr: RemitaRRR) -> None:
        handler_map = {
            "ap_invoice": self._handle_ap_invoice_paid,
            "payroll_run": self._handle_payroll_run_paid,
        }
        handler = handler_map.get(rrr.source_type)
        if handler:
            handler(rrr)
```

### Generic Source Linking Pattern
For features that can link to multiple entity types:
```python
# In model - generic source fields
source_type: Mapped[Optional[str]] = mapped_column(String(50))  # "ap_invoice", "payroll_run"
source_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))

# In service - validation helper
def _resolve_source(self, org_id: UUID, source_type: str, source_id: UUID):
    type_map = {
        "ap_invoice": SupplierInvoice,
        "payroll_run": PayrollEntry,
    }
    model = type_map.get(source_type)
    if not model:
        raise ValueError("Invalid source type")
    entity = self.db.get(model, source_id)
    if not entity or entity.organization_id != org_id:
        raise ValueError("Source not found or access denied")
    return entity
```

### Graceful Error Handling in Side Effects
When a side effect fails, don't fail the main operation:
```python
def mark_paid(self, rrr_id: UUID, reference: str) -> RemitaRRR:
    rrr.status = RRRStatus.paid
    rrr.payment_reference = reference
    self.db.flush()

    # Side effect - don't fail main operation if this fails
    try:
        self._notify_source_paid(rrr)
    except Exception as e:
        logger.exception(f"Failed to notify source: {e}")
        # Continue - RRR is still marked paid

    return rrr
```

### Import Inside Functions to Avoid Circular Imports
For cross-module dependencies, import inside the function:
```python
def _handle_paid(self, rrr: RemitaRRR) -> None:
    # Import here to avoid circular import at module load
    from app.services.remita.source_handler import get_source_handler

    handler = get_source_handler(self.db)
    handler.handle_rrr_paid(rrr)
```

### Web Service Context Pattern
For complex pages, build context in a dedicated web service method:
```python
# app/services/module/web/module_web.py
class ModuleWebService:
    def detail_context(self, org_id: UUID, entity_id: UUID) -> dict:
        entity = self._get_or_404(entity_id)
        related = self._get_related(entity)
        actions = self._available_actions(entity)

        return {
            "entity": entity,
            "related": related,
            "can_edit": actions.can_edit,
            "can_delete": actions.can_delete,
        }

# In route - just call web service
@router.get("/{id}")
def detail(id: UUID, auth: WebAuthContext, db: Session):
    context = base_context(request, auth, "Title", "section", db=db)
    context.update(ModuleWebService(db).detail_context(auth.organization_id, id))
    return templates.TemplateResponse(request, "template.html", context)
```

### Web Service Dependency Exception
Files in `app/services/*/web.py` or `app/services/*/web/*.py` are **web-layer helpers**, not pure business logic services. They MAY import from `app.web.deps` for `WebAuthContext` and `base_context()`. They must NOT contain database queries directly — delegate to the parent service.

**Pure business logic services** (`*_service.py`) must **NEVER** import from `app.web.*`:
```python
# WRONG - service importing web layer
# app/services/finance/ar/invoice_service.py
from app.web.deps import WebAuthContext  # Don't do this in a _service.py file

# CORRECT - only web service files import web deps
# app/services/finance/ar/web/invoice_web.py
from app.web.deps import WebAuthContext, base_context  # OK here
```

### Reusable Template Partials
For UI components used across pages, create Jinja2 macros:
```html
{# templates/finance/remita/_generate_modal.html #}
{% macro rrr_modal(amount, source_type=None, source_id=None) %}
<div class="modal">
    <form method="POST" action="/finance/remita/generate">
        <input type="hidden" name="amount" value="{{ amount }}">
        {% if source_type %}
        <input type="hidden" name="source_type" value="{{ source_type }}">
        <input type="hidden" name="source_id" value="{{ source_id }}">
        {% endif %}
        <!-- Form fields -->
    </form>
</div>
{% endmacro %}

{# Usage in another template #}
{% from "finance/remita/_generate_modal.html" import rrr_modal %}
{{ rrr_modal(invoice.amount, "ap_invoice", invoice.invoice_id) }}
```

### Payer/Org Defaults Pattern
For forms that need organization defaults:
```python
def generate_form_context_with_org(self, organization_id: UUID) -> dict:
    from app.models.finance.core_org.organization import Organization

    org = self.db.get(Organization, organization_id)
    context = self.generate_form_context()
    context["payer_defaults"] = {
        "payer_name": org.trading_name or org.legal_name or "",
        "payer_email": org.contact_email or "",
        "payer_phone": org.contact_phone or "",
    }
    return context
```

## Error Handling Rules

### Never Use Bare `except:`
Bare except clauses catch ALL exceptions including `KeyboardInterrupt` and `SystemExit`, making debugging impossible and hiding real errors.

```python
# WRONG - catches everything, silent failures
try:
    num = Decimal(value)
except:
    continue

# CORRECT - catch specific exceptions
try:
    num = Decimal(value)
except (ValueError, TypeError, ArithmeticError) as e:
    logger.warning("Invalid number in row %s: %s", row_num, e)
    continue
```

### Side Effects Must Not Break Main Flow
When a side effect (notification, audit log, webhook) fails, log the error but don't fail the primary operation:

```python
try:
    send_notification(...)  # Side effect
except Exception as e:
    logger.exception("Notification failed: %s", e)
    # Continue - main operation succeeded
```

### Log at Appropriate Levels
- `logger.debug()` - Diagnostic details (query params, intermediate values)
- `logger.info()` - Business events (created, updated, deleted, processed)
- `logger.warning()` - Unexpected but recoverable (missing optional config, deprecated usage)
- `logger.error()` - Errors needing attention (failed external API call)
- `logger.exception()` - Exceptions with stack trace (inside `except` blocks)

**NEVER log sensitive data** (passwords, tokens, PII, full request bodies with credentials).

## Template Security

### Output Escaping
Jinja2 auto-escapes by default. The `| safe` filter disables this and **must be used carefully**.

**Allowed uses of `| safe`:**
```html
{{ request.state.csrf_form | safe }}        {# Framework-generated CSRF input #}
{{ data | tojson | safe }}                   {# Serialized JSON for JavaScript #}
{{ org_branding.css | safe }}               {# Admin-configured CSS only #}
```

**For user-submitted content, use `| sanitize_html`** (defined in `app/templates.py`):
```html
{# WRONG - stored XSS vulnerability #}
{{ ticket.description | safe }}

{# CORRECT - strips dangerous tags/attributes, keeps safe formatting #}
{{ ticket.description | sanitize_html }}
```

**For plain text with newlines, use `| nl2br`** (also in `app/templates.py`):
```html
{# Escapes HTML then converts \n to <br> #}
{{ comment.text | nl2br }}
```

### CSRF Protection
Every `<form method="POST">` MUST include the CSRF hidden input:
```html
<form method="POST" action="/some/endpoint">
    {{ request.state.csrf_form | safe }}
    <!-- form fields -->
</form>
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
  - Nullable fields: use `Optional[X]` for fields that can be None

### Migrations - Idempotent Pattern (REQUIRED)
All migrations MUST be idempotent - safe to run multiple times:
```python
def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Check table exists before creating
    if not inspector.has_table("my_table", schema="my_schema"):
        op.create_table(...)

    # Check column exists before adding
    if inspector.has_table("my_table", schema="my_schema"):
        columns = {col["name"] for col in inspector.get_columns("my_table", schema="my_schema")}
        if "new_column" not in columns:
            op.add_column(...)

    # Check enum exists before creating
    existing_enums = [e["name"] for e in inspector.get_enums(schema="my_schema")]
    if "my_enum" not in existing_enums:
        my_enum.create(bind)

    # Check index exists before creating
    indexes = {idx["name"] for idx in inspector.get_indexes("my_table", schema="my_schema")}
    if "ix_my_index" not in indexes:
        op.create_index(...)

def downgrade() -> None:
    # Same pattern - check before dropping
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("my_table", schema="my_schema"):
        op.drop_table(...)
```

### External Integrations
For any external API integration (Remita, Paystack, etc.):
1. Add configuration to `app/config.py` with empty string or `None` defaults
2. Add `is_configured()` method that checks if credentials are set
3. Raise clear error if trying to use unconfigured service
4. Show warning in UI when not configured
5. **NEVER** use production URLs as default values — use empty string

```python
# In service
def is_configured(self) -> bool:
    return bool(settings.api_key and settings.api_secret)

@property
def client(self):
    if not self.is_configured():
        raise ValueError("Service not configured. Set API_KEY and API_SECRET.")
    return self._create_client()
```

```python
# In config.py
# WRONG - dev environment accidentally hits production
crm_api_url: str = os.getenv("CRM_API_URL", "https://crm.dotmac.io")

# CORRECT - explicit empty default, checked at usage
crm_api_url: str = os.getenv("CRM_API_URL", "")
```

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

## Security Checklist (Pre-Commit)

Before committing ANY code that accepts user input, queries the database, renders HTML templates, or handles file uploads, verify:

- [ ] No raw SQL or string formatting in queries (use SQLAlchemy ORM)
- [ ] All queries filter by `organization_id` (multi-tenancy)
- [ ] All POST forms include `{{ request.state.csrf_form | safe }}`
- [ ] User-submitted content uses `| sanitize_html`, never `| safe`
- [ ] File uploads validate content type and size
- [ ] No secrets hardcoded in code (use environment variables)
- [ ] No bare `except:` clauses (catch specific exceptions)
