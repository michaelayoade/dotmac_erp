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

## Form Design Standards

### Context Query Parameters
Forms that create entities linked to a parent MUST accept the parent ID as a query parameter. Use consistent names across all modules:

| Parameter | Used By |
|-----------|---------|
| `customer_id` | Receipts, invoices, quotes, sales orders |
| `supplier_id` | AP payments, purchase orders |
| `invoice_id` | Receipts (AR), payments (AP) |
| `account_id` | Journal entries, transfers |
| `project_id` | Expenses, time entries, tasks |
| `po_id` | Goods received notes |
| `so_id` | Delivery notes, invoices |
| `quote_id` | Sales orders |
| `employee_id` | Leave requests, payslips, expense claims |

### Form Context Method Pattern
Every form MUST have a dedicated `*_form_context()` method on the web service that returns all dropdown data, defaults, and pre-selections:

```python
@staticmethod
def receipt_form_context(
    db: Session,
    organization_id: str,
    *,
    invoice_id: Optional[str] = None,   # Query param pre-selection
    customer_id: Optional[str] = None,  # Query param pre-selection
    receipt_id: Optional[str] = None,   # Edit mode
) -> dict:
    context = {"customers": [...], "accounts": [...], "payment_methods": [...]}

    # Pre-select from query param
    if invoice_id:
        invoice = service.get_by_id(UUID(invoice_id))
        context["selected_invoice"] = invoice
        context["selected_customer_id"] = str(invoice.customer_id)
        context["locked_customer"] = True  # Lock downstream field

    return context
```

### Data Precedence Rules
When populating form fields, apply values in this order (first wins):

1. **Edit mode** (existing record) — overrides everything
2. **Related entity from query param** — e.g., `?invoice_id=` sets customer and amount
3. **Organization defaults** — today's date, org currency, default accounts
4. **Empty/blank** — field left for user input

### Locked Field UI
When a field is auto-selected from a query param, display it read-only and hide the editable control:

```html
{# Locked display when customer comes from invoice context #}
<div x-show="lockedCustomer" class="form-input bg-slate-50 dark:bg-slate-700 cursor-not-allowed">
    <span x-text="selectedCustomerName"></span>
    <span class="text-xs text-slate-400 ml-2">(from invoice)</span>
</div>
<select x-show="!lockedCustomer" name="customer_id" class="form-select">
    {% for c in customers %}
    <option value="{{ c.customer_id }}" {{ 'selected' if selected_customer_id == c.customer_id|string }}>
        {{ c.customer_name }}
    </option>
    {% endfor %}
</select>
```

### Context Banner
When a form is prefilled from a parent entity, show a concise banner at the top:

```html
{% if selected_invoice %}
<div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-3 mb-4 flex items-center gap-2">
    <svg class="w-4 h-4 text-blue-500" ...><!-- info icon --></svg>
    <span class="text-sm text-blue-700 dark:text-blue-300">
        Creating receipt for Invoice <strong>{{ selected_invoice.invoice_number }}</strong>
        — {{ selected_invoice.total | format_currency }}
    </span>
</div>
{% endif %}
```

### Navigation Continuity
"New X" links from detail pages MUST include the parent entity ID:

```html
{# On invoice detail page — link to new receipt #}
<a href="/finance/ar/receipts/new?invoice_id={{ invoice.invoice_id }}" class="btn btn-primary">
    Record Payment
</a>

{# On customer detail page — link to new invoice #}
<a href="/finance/ar/invoices/new?customer_id={{ customer.customer_id }}" class="btn btn-primary">
    New Invoice
</a>
```

### Post-Submit Redirect
After successful form submission, redirect based on context:

```python
# If form was opened from a parent entity, redirect back to it
if invoice_id:
    return RedirectResponse(
        url=f"/finance/ar/invoices/{invoice_id}?success=Receipt+created",
        status_code=303,
    )
# Otherwise redirect to the list page
return RedirectResponse(
    url="/finance/ar/receipts?success=Receipt+created",
    status_code=303,
)
```

### Error Re-Population
On validation failure, re-render the form with the error message and original input preserved:

```python
except (ValueError, ValidationError) as e:
    context = base_context(request, auth, "New Receipt", "finance", db=db)
    context.update(self.receipt_form_context(db, str(auth.organization_id)))
    context["error"] = str(e)
    context["form_data"] = data  # Re-populate fields
    return templates.TemplateResponse(request, "finance/ar/receipt_form.html", context)
```

### Form Template Section Order
All form templates MUST follow this layout:

1. **Context banner** (if prefilled from parent entity)
2. **Error summary** (if validation failed)
3. **Header details** — dates, reference numbers, payment method
4. **Primary entity** — customer/supplier/employee selector
5. **Amounts and allocations** — line items, totals
6. **Notes and attachments** — optional fields
7. **Form actions** — Cancel (left), Save (right), optional Save & New

### Build Input Methods
Parse and validate raw form data in a dedicated static method — never in the route:

```python
@staticmethod
def build_receipt_input(data: dict) -> CustomerPaymentInput:
    return CustomerPaymentInput(
        customer_id=UUID(data["customer_id"]),
        payment_date=parse_date(data.get("payment_date")) or date.today(),
        payment_method=PaymentMethod(data.get("payment_method", "BANK_TRANSFER")),
        amount=Decimal(data.get("amount", "0")),
        allocations=[
            PaymentAllocationInput(**a)
            for a in json.loads(data.get("allocations", "[]"))
        ],
    )
```

### Cross-Entity Validation
When a form references a parent entity, validate ownership on submit:

```python
# Reject if invoice doesn't belong to selected customer
if invoice.customer_id != input_data.customer_id:
    raise ValueError("Invoice does not belong to selected customer")

# Reject if entity belongs to different org (multi-tenancy)
if invoice.organization_id != org_id:
    raise ValueError("Invoice not found")
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

## UI/UX Design Standards

### Typography Rules
- **Page titles (h1)**: `text-xl font-semibold font-display` (Fraunces) — via `topbar` macro
- **Section titles (h2/h3)**: `text-lg font-semibold` or `text-xl font-semibold` (DM Sans)
- **Body text**: `text-sm` (14px) — default for tables, forms, descriptions
- **Captions/labels**: `text-xs font-medium` (12px) — timestamps, secondary labels
- **Financial values**: ALWAYS `font-mono tabular-nums` — amounts, IDs, invoice numbers
- **Minimum text size**: `text-xs` (12px) — NEVER use `text-[10px]` or `text-[11px]`
- **Table column headers**: Use `label-caps` or uppercase styling consistently

### Spacing Conventions
- `gap-4` between cards in a grid
- `gap-6` between page sections (`space-y-6` for vertical flow)
- `gap-8` between major page zones
- `p-4` for card padding on mobile, `p-6` on desktop
- `p-5` for stat cards

### Color Standards
| Status | Color | Text Class | BG Class |
|--------|-------|------------|----------|
| Success/Paid/Active | Emerald | `text-emerald-700 dark:text-emerald-400` | `bg-emerald-50 dark:bg-emerald-900/20` |
| Warning/Pending/Draft | Amber | `text-amber-700 dark:text-amber-400` | `bg-amber-50 dark:bg-amber-900/20` |
| Error/Overdue/Rejected | Rose | `text-rose-700 dark:text-rose-400` | `bg-rose-50 dark:bg-rose-900/20` |
| Info/Processing | Blue | `text-blue-700 dark:text-blue-400` | `bg-blue-50 dark:bg-blue-900/20` |
| Neutral/Closed/Voided | Slate | `text-slate-600 dark:text-slate-400` | `bg-slate-100 dark:bg-slate-800` |

Module accents: Finance=teal, People=violet, Expense=amber, Inventory=emerald, Procurement=blue, Operations=indigo.

### Component Standards

**Status badges**: ALWAYS use `{{ status_badge(status, 'sm') }}` macro from `components/macros.html`. NEVER create inline badge HTML.

**Empty states**: ALWAYS use `{{ empty_state(title, description, icon) }}` macro. Every table `{% for %}` loop MUST have `{% else %}` with an empty state.

**Tables**:
- Wrap in `<div class="table-container">` for horizontal scroll
- Left-align text, right-align numbers (`text-right`), center status badges (`text-center`)
- Add `scope="col"` to all `<th>` elements
- Hide secondary columns on mobile: `hidden sm:table-cell`
- Amounts: `<td class="text-right font-mono">{{ amount | format_currency }}</td>`

**Forms**:
- Labels above fields: `<label class="form-label">Name</label>`
- Required indicator: `<span class="text-rose-500">*</span>` after label text
- Error display: Use `.form-error` class below field, PLUS error summary at form top
- NEVER use `alert()` for validation — use inline errors or toast notifications
- All POST forms MUST include `{{ request.state.csrf_form | safe }}`

**Modals**: MUST include `role="dialog"`, `aria-modal="true"`, `aria-labelledby="modal-title-id"`, `@keydown.escape` handler, and focus trap.

**Toasts**: Bottom-right, stacked upward. Auto-dismiss: 5s success, 8s warning, persistent error. Use `aria-live="polite"`.

**Search / Typeahead**: ALWAYS use the `live_search` macro from `components/macros.html`. NEVER write inline search forms.
```jinja2
{# Simple search (no filters) #}
{{ live_search(search=search, base_url="/module/items", placeholder="Search items...") }}

{# With static filter dropdowns #}
{{ live_search(
    search=search,
    filters=[
        {"name": "status", "label": "All Status", "value": status,
         "options": [{"value": "ACTIVE", "label": "Active"}, {"value": "INACTIVE", "label": "Inactive"}]}
    ],
    base_url="/module/items",
    placeholder="Search items..."
) }}

{# With dynamic filters (Jinja2 loops) — use {% call %} block #}
{% call(search_attrs) live_search(search=search, base_url="/module/items", placeholder="Search items...") %}
    <select name="category_id" class="form-select" {{ search_attrs }}>
        <option value="">All Categories</option>
        {% for cat in categories %}
        <option value="{{ cat.id }}" {{ 'selected' if selected == cat.id|string else '' }}>{{ cat.name }}</option>
        {% endfor %}
    </select>
{% endcall %}

{# With entity autosuggest (navigates to detail page on select) #}
{{ live_search(search=search, base_url="/finance/ar/customers",
               entity_type="customers", placeholder="Search customers...") }}
```
- Results table + pagination MUST be wrapped in `<div id="results-container">...</div>`
- The macro creates its own `<div class="card p-4">` — never double-wrap in another card
- `search_attrs` in `{% call %}` blocks provides HTMX trigger/target attrs for custom filter elements
- Companion JS: `static/js/live-search.js` (auto-loaded in `base.html`)
- Old macros (`search_filter_bar`, `search_autosuggest`) are DEPRECATED — do not use

### Dark Mode Rules
- ALWAYS pair light/dark variants: `bg-white dark:bg-slate-800`, `text-slate-900 dark:text-white`, `border-slate-200 dark:border-slate-700`
- Never use pure black (`#000000`) — darkest is `slate-900`
- Test both modes before merging template changes

### Accessibility (WCAG 2.2 AA)
- All icon-only buttons MUST have `aria-label`: `<button aria-label="Delete invoice">`
- Sidebar `<nav>` needs `aria-label="Main navigation"`
- Active sidebar links need `aria-current="page"`
- Breadcrumbs: `<nav aria-label="Breadcrumb">` with `<ol>` structure, final item gets `aria-current="page"`
- Focus visible on all interactive elements (existing `:focus-visible` CSS is correct)
- Color must NEVER be the sole indicator — always pair with text, icon, or pattern
- Touch targets: minimum 44x44px on mobile
- Decorative icons: `aria-hidden="true"`

### Data Display
- **Dates**: `DD MMM YYYY` in tables (e.g., "07 Feb 2026"), ISO `YYYY-MM-DD` in form inputs
- **Currency**: ISO code or symbol with `font-mono tabular-nums`, right-aligned
- **Enums**: `{{ status | replace('_', ' ') | title }}` — never show raw enum values
- **None/null**: `{{ var if var else '' }}` or `{{ var if var else '-' }}` — NEVER render "None"
- **Negative amounts**: Parentheses + rose text: `<span class="text-rose-600">(1,234.56)</span>`

### Transition Standards
- Color/opacity changes: `transition-colors duration-150`
- Layout changes: `transition-all duration-200 ease-out`
- Modal enter: `duration-200 ease-out`, leave: `duration-150 ease-in`
- NEVER exceed 400ms for any UI transition
- Respect `@media (prefers-reduced-motion: reduce)` — CSS handles this

### Template Pre-Commit Checklist
- [ ] `font-display` on page titles (via topbar macro)
- [ ] `font-mono tabular-nums` on all financial values
- [ ] Status badges use `status_badge()` macro (no inline badges)
- [ ] Tables have `scope="col"` on `<th>`, amounts `text-right`, in `table-container`
- [ ] Empty states on all tables/lists (via `empty_state()` macro)
- [ ] Dark mode variants on all bg, text, border classes
- [ ] All icon-only buttons have `aria-label`
- [ ] Modals have `role="dialog"` + `aria-modal="true"` + escape handler
- [ ] No text smaller than `text-xs` (12px)
- [ ] Dates as `DD MMM YYYY`, enums filtered with `replace('_', ' ') | title`

## Security Checklist (Pre-Commit)

Before committing ANY code that accepts user input, queries the database, renders HTML templates, or handles file uploads, verify:

- [ ] No raw SQL or string formatting in queries (use SQLAlchemy ORM)
- [ ] All queries filter by `organization_id` (multi-tenancy)
- [ ] All POST forms include `{{ request.state.csrf_form | safe }}`
- [ ] User-submitted content uses `| sanitize_html`, never `| safe`
- [ ] File uploads validate content type and size
- [ ] No secrets hardcoded in code (use environment variables)
- [ ] No bare `except:` clauses (catch specific exceptions)
