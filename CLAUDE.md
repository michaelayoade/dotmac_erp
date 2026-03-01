# DotMac ERP — Claude Agent Guide

FastAPI + SQLAlchemy 2.0 + Jinja2/HTMX/Alpine.js + Celery + PostgreSQL.
Multi-tenant SaaS ERP covering Finance, People/HR, Inventory, Expense, Operations, Procurement, Public Sector.

## Hooks (active — run automatically)
- **PreToolUse/Bash**: `block-dangerous.sh` — blocks `git push --force`, `git reset --hard`, `git clean -f`, `DROP TABLE`, `TRUNCATE`
- **PostToolUse/Edit+Write**: `post-edit-lint.sh` (ruff auto-fix + format + route AST validation), `check-multitenant.py`, `check-python-style.py`, `check-template-style.py`
- **Stop**: `enforce-quality.sh` — ruff + mypy on all session-edited files (non-blocking, advisory)

## Plugins
`frontend-design`, `context7`, `code-review`, `commit-commands`, `pyright-lsp`, `explanatory-output-style`, `ralph-loop`, `playwright`

## Non-Negotiable Rules

### Multi-tenancy
Every query MUST filter by `organization_id`. The `check-multitenant.py` hook will flag violations.
```python
stmt = select(Invoice).where(
    Invoice.organization_id == org_id,
    Invoice.status == "OPEN",
)
```

### SQLAlchemy 2.0 only
- `select()` + `db.scalars()` — NEVER `db.query()`
- `db.flush()` in services (NOT `db.commit()` — routes/tasks commit)
- `db.get(Model, pk)` for single-PK lookups
- Eager loading: `joinedload()` / `selectinload()`

### Model PK naming — read the model first
Each model uses its own PK name, never `.id`:
```python
claim.claim_id       # ExpenseClaim
invoice.invoice_id   # SupplierInvoice / CustomerInvoice
payment.payment_id
journal.journal_id
```

### Routes are thin wrappers — no logic inside
Routes call web service methods only. No `select()`, no `db.add()`, no business logic.

### Circular imports — lazy import pattern (approved)
```python
def _handle_paid(self, rrr):
    from app.services.remita.source_handler import get_source_handler
    handler = get_source_handler(self.db)
    handler.handle_rrr_paid(rrr)
```

### Commands — always use `poetry run`
```bash
poetry run ruff check app/ --fix
poetry run mypy app/
poetry run pytest tests/ -x -q
poetry run alembic upgrade head
```

### Docker containers
- App: `dotmac_erp_app`
- Worker: `dotmac_erp_worker`
- DB: `dotmac_erp_db`

## Module Structure

```
app/
├── models/          # SQLAlchemy ORM models (read these before touching fields!)
├── services/
│   ├── finance/     # gl/ ar/ ap/ banking/ tax/ reminder/
│   ├── people/      # hr/ payroll/ leave/
│   ├── pm/          # Project management
│   ├── expense/
│   ├── notification.py
│   └── file_upload.py   # ALL file uploads go through here
├── web/             # Routes + web services (thin wrappers)
│   └── deps.py      # Auth dependencies (web services MAY import this; pure services MUST NOT)
├── tasks/           # Celery tasks — delegate ALL logic to services
└── templates/       # Jinja2 per module
    └── components/  # Shared macros — ALWAYS use these, never inline
```

## Sidebar base templates by module
| Module | Base template | Accent |
|--------|---------------|--------|
| Finance | `finance/base_finance.html` | Teal |
| People/HR | `people/base_people.html` | Violet |
| Expense | `expense/base_expense.html` | Amber |
| Inventory | `inventory/base_inventory.html` | Emerald |
| Procurement | `procurement/base_procurement.html` | Blue |
| Public Sector | `public_sector/base_public_sector.html` | Cyan |
| Operations | `modules/base_modules.html` | Indigo |
| Admin | `admin/base_admin.html` | Gray |

## Template Rules

### Alpine.js — single quotes on x-data with tojson (critical)
```html
<!-- CORRECT -->
<div x-data='{ items: {{ items | tojson }} }'>
<!-- WRONG — breaks Alpine -->
<div x-data="{{ items | tojson }}">
```

### None handling — not default()
```jinja2
{{ var if var else '' }}   {# correct — handles Python None #}
{{ var | default('') }}   {# WRONG — only handles Jinja2 undefined #}
```

### Tailwind dynamic classes — dict lookup not interpolation
```jinja2
{% set color_map = {'success': 'bg-emerald-50', 'error': 'bg-rose-50'} %}
<div class="{{ color_map.get(status, 'bg-slate-50') }}">
```

### Enums — always filter
```jinja2
{{ status | replace('_', ' ') | title }}
```

### Macros — always use, never inline
Required imports from `components/`:
- `status_badge(status, size)` — 70+ statuses mapped
- `empty_state(title, desc, icon, cta_text, cta_href)` — every `{% for %}` needs `{% else %}`
- `live_search(search, base_url, placeholder, filters)` — 300ms debounced HTMX
- `stats_card(label, value, icon, color, href, trend)` — never write stat card HTML
- `bulk_action_bar(actions, entity_name)` — fixed bottom bar
- `aging_bar(current, days30, days60, days90)` — AR/AP aging
- `progress_bar(label, value, percentage, color)`
- `topbar(title, accent)` — with breadcrumbs + actions callers

### `| safe` — only these three uses
```jinja2
{{ request.state.csrf_form | safe }}   {# CSRF token #}
{{ data | tojson | safe }}              {# JSON for JS #}
{{ org_branding.css | safe }}           {# Admin-configured CSS only #}
```
Never `| safe` on user content — use `| sanitize_html`.

### CSRF — mandatory on every POST form
```html
{{ request.state.csrf_form | safe }}
```

### Results container — mandatory on list pages
```html
<div id="results-container">
  {# table + pagination here #}
</div>
```

## Service Layer

```python
class SomeService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, org_id: UUID, data: CreateSchema) -> SomeModel:
        record = SomeModel(organization_id=org_id, **data.model_dump())
        self.db.add(record)
        self.db.flush()   # NOT commit — caller commits
        logger.info("Created %s: %s", SomeModel.__name__, record.pk)
        return record
```

## Celery Tasks

```python
@shared_task
def process_something() -> dict:
    results = {"processed": 0, "errors": []}
    with SessionLocal() as db:
        from app.services.some_module import SomeService   # import inside task
        service = SomeService(db)
        for item in service.get_items():
            try:
                service.process_item(item)
                results["processed"] += 1
            except Exception as e:
                logger.exception("Failed: %s", item)
                results["errors"].append(str(e))
        db.commit()
    return results
```

## File Uploads

All file uploads through `app/services/file_upload.py`. Never custom disk I/O in domain services.
Always: validate size before write, use `resolve_safe_path()`, use UUID-based storage names.

## Notifications

```python
from app.services.notification import NotificationService
from app.models.notification import EntityType, NotificationType, NotificationChannel

NotificationService().create(
    db,
    organization_id=org_id,
    recipient_id=user_id,
    entity_type=EntityType.SYSTEM,
    entity_id=entity_uuid,
    notification_type=NotificationType.OVERDUE,
    title="...",
    message="...",
    channel=NotificationChannel.BOTH,
    action_url="/finance/ar/invoices",
)
```
Check for duplicates before sending recurring notifications.

## Financial Display Standards

| Type | Format | Class |
|------|--------|-------|
| Currency | `₦1,234,567.89` | `font-mono tabular-nums text-right` |
| Negative | `(₦1,234.56)` | `font-mono tabular-nums text-right text-rose-600 dark:text-rose-400` |
| Date (tables) | `07 Feb 2026` | `text-sm` |
| Date (form inputs) | `2026-02-07` | `form-input` |
| Entity numbers | `INV-00421` | `font-mono tabular-nums` |

Never minus sign for negatives — always parentheses. Never render `None` — use `—` or `''`.

## Status Colors

| Color | Statuses |
|-------|---------|
| Amber | DRAFT, PENDING, PENDING_APPROVAL, SUBMITTED, DUE_SOON |
| Blue | PROCESSING, IN_PROGRESS, OPEN, PARTIAL, SCHEDULED |
| Emerald | APPROVED, PAID, POSTED, ACTIVE, RECONCILED, COMPLETED |
| Rose | REJECTED, OVERDUE, FAILED, EXPIRED, BLOCKED |
| Slate | CLOSED, VOIDED, CANCELLED, INACTIVE, REVERSED |

## Dark Mode — always pair
```html
<div class="bg-white dark:bg-slate-800 text-slate-900 dark:text-white border-slate-200 dark:border-slate-700">
```
Never pure `#000` — darkest is `slate-900`.

## Common Mistakes to Avoid
- Using `db.query()` instead of `select()` + `scalars()`
- `db.commit()` in a service (should be `flush()`)
- Forgetting `organization_id` filter on queries
- Using `| default('')` for None values (use `var if var else ''`)
- Double quotes on `x-data` with `tojson`
- Inline badge HTML instead of `status_badge()` macro
- No `{% else %}` + `empty_state()` on `{% for %}` loops
- Missing CSRF token on POST forms
- `| safe` on user content
- String-interpolated Tailwind classes (`bg-{{ color }}-50`)
- Bare `except:` — always catch specific exceptions
- Missing `scope="col"` on `<th>` elements
- Importing `app.web.*` from pure business services
