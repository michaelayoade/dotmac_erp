# DotMac ERP

IFRS-based multi-tenant ERP system. FastAPI + SQLAlchemy 2.0 + Celery + Jinja2/Alpine.js.

## Quick Commands

```bash
# Quality (or use: make check)
make lint                        # ruff check app/
make format                      # ruff format + fix
make type-check                  # mypy app/

# Testing (or use: make test)
pytest tests/path/test_file.py -v  # Specific test
pytest -x --tb=short               # Stop on first failure
make test-cov                      # With coverage

# Database
make migrate                     # alembic upgrade head
make migrate-new msg="desc"      # New migration

# Development
make dev                         # uvicorn with reload
make docker-up / docker-down     # Docker lifecycle
make docker-shell                # Shell into app container
```

## Architecture

```
app/
├── api/        # REST API routes (thin wrappers → services)
├── web/        # HTML routes (thin wrappers → web services)
├── models/     # SQLAlchemy ORM models (by domain)
├── schemas/    # Pydantic v2 request/response models
├── services/   # ALL business logic lives here
├── tasks/      # Celery tasks (orchestrate services)
templates/      # Jinja2 + Alpine.js + HTMX
tests/          # e2e/, ifrs/, integration/, services/
```

### Module Map
| Module | Service Path | Web Route Prefix |
|--------|-------------|-----------------|
| GL | `finance/gl/` | `/finance/gl/` |
| AR | `finance/ar/` | `/finance/ar/` |
| AP | `finance/ap/` | `/finance/ap/` |
| Banking | `finance/banking/` | `/finance/banking/` |
| Expense | `expense/` | `/finance/expenses/` |
| Inventory | `inventory/` | `/inventory/` |
| People/HR | `people/hr/` | `/people/` |
| Payroll | `people/payroll/` | `/people/payroll/` |

Special routes: `/finance/quotes`, `/finance/sales-orders`, `/automation` (not nested under `/finance/ar/`)

## Critical Rules

### 1. Service Layer — Routes are THIN WRAPPERS
IMPORTANT: Routes MUST NOT contain database queries, business logic, or conditionals.
```python
# CORRECT
@router.post("/invoices")
def create_invoice(data: InvoiceCreate, db: Session = Depends(get_db)):
    return InvoiceService(db).create(data)

# WRONG — logic in route
@router.post("/invoices")
def create_invoice(data: InvoiceCreate, db: Session = Depends(get_db)):
    invoice = Invoice(**data.dict())  # NO
    db.add(invoice)                   # NO
```

### 2. Multi-tenancy — ALWAYS Filter by org_id
Every query MUST include `organization_id`. Omitting this leaks data across tenants.

### 3. SQLAlchemy 2.0 — Use select(), Not db.query()
```python
stmt = select(Invoice).where(Invoice.organization_id == org_id)
invoices = db.scalars(stmt).all()
```

### 4. Pydantic v2 — Use ConfigDict, Not orm_mode
```python
class InvoiceCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)
```

### 5. Model PK Naming — Read the Model First
Each model has unique PK names: `claim.claim_id`, `invoice.invoice_id`, `payment.payment_id`.
ALWAYS read the model file to verify field names before referencing them.

### 6. Migrations — Must Be Idempotent
Check before creating: `inspector.has_table()`, column existence, enum existence.

### 7. Route Handlers Are Sync
SQLAlchemy sessions are sync. Use `def`, not `async def`. Background work goes to Celery.

## Code Style

- Type hints on ALL functions (mypy must pass), including private helpers
- Every service file: `logger = logging.getLogger(__name__)`
- Imports: stdlib → third-party → local (absolute imports)
- Line length: 88 chars (ruff)
- Use `flush()` not `commit()` in services — caller controls transaction

## Testing Requirements

- SQLite in-memory (conftest patches PostgreSQL UUID)
- Every new service needs: happy path + error cases + multi-tenancy isolation + edge cases
- E2E tests must assert meaningful content (NOT just `body.to_be_visible()`)

## Verification Workflow

IMPORTANT: Before declaring any task complete, run verification:

**For Python changes:**
```bash
make lint                                            # Must pass
poetry run mypy app/path/to/changed/files.py --ignore-missing-imports  # Must pass
pytest tests/path/to/relevant/tests.py -v            # Must pass
```

**For template changes, also verify:**
- Every `<form method="POST">` includes `{{ request.state.csrf_form | safe }}`
- No `| safe` on user-submitted content (use `| sanitize_html`)
- Dark mode variants on all color classes
- Status badges use `status_badge()` macro, not inline HTML
- Search uses `live_search` macro, not inline forms

**For migrations:**
- Idempotent (safe to run multiple times)
- Has both `upgrade()` and `downgrade()`

## Agent Workflow

### Explore Before Implementing
ALWAYS read existing code in the same directory before writing new code. Match the patterns you find — import style, type hints, error handling, docstrings.

### Use Plan Mode for Multi-File Changes
For changes touching 3+ files, use plan mode first. Explore the codebase, identify all files that need changes, then present a plan before implementing.

### Verify Your Own Work
After implementing, run the verification workflow above. If tests fail, fix them before reporting completion. If mypy fails, fix type errors. Never skip verification.

### Common Mistakes to Avoid
- Using `db.query()` instead of `select()` (SQLAlchemy 1.x vs 2.0)
- Forgetting `organization_id` filter (multi-tenancy leak)
- Using `| safe` on user content (XSS vulnerability)
- Using bare `except:` (catch specific exceptions)
- Putting business logic in routes (must be in services)
- Using `async def` for route handlers (sessions are sync)
- Assuming `model.id` exists (each model has unique PK naming)
- String interpolation in Tailwind classes (gets purged — use dict lookup)
- Double quotes on Alpine.js `x-data` with `tojson` (use single quotes)

## Domain Reference

### Discipline Module
```
app/services/people/discipline/
├── discipline_service.py      # Core case management
├── case_action_service.py     # Action recording
├── case_response_service.py   # Employee responses
└── web/discipline_web.py      # Web route helpers
```
Workflow: DRAFT → QUERY_ISSUED → RESPONSE_RECEIVED → HEARING_SCHEDULED → HEARING_COMPLETED → DECISION_MADE → APPEAL_FILED → APPEAL_DECIDED → CLOSED

### Environment Variables
Required: `DATABASE_URL`, `SECRET_KEY`, `REDIS_URL`
Optional: `ERPNEXT_API_KEY/SECRET`, `SMTP_*`, `PAYSTACK_SECRET_KEY`

## Extended Standards (in .claude/rules/)
- **`design-system.md`** — Complete UI reference: tokens, colors, components, layout, interactions, dark mode
- `forms.md` — Form design patterns, context methods, locked fields
- `ui-ux.md` — Typography, colors, components, accessibility, dark mode
- `security.md` — Error handling, template escaping, CSRF, multi-tenancy
- `patterns.md` — Cross-module integration, source linking, web services
- `services.md` — Service class structure, querying, error handling
- `web-routes.md` — Route structure, base templates, URL conventions
- `templates.md` — Alpine.js quoting, enum display, Tailwind purge, uploads
- `notifications.md` — NotificationService usage, entity types, channels
- `celery-tasks.md` — Task patterns, scheduling, batch processing
- `file-uploads.md` — FileUploadService, validation, frontend macro
