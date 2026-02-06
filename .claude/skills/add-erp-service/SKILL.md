---
name: add-erp-service
description: Scaffold a new ERP service module with model, service, web service, routes, and tests
arguments:
  - name: module_path
    description: "Dot-separated module path (e.g. finance/ar/credit_note, people/hr/training)"
---

# Add ERP Service

Scaffold a complete service module for the DotMac ERP system.

## Steps

### 1. Understand the request
Parse the `$ARGUMENTS` to determine:
- **Domain**: finance, people, operations, expense, etc.
- **Module path**: e.g. `finance/ar/credit_note`
- **Entity name**: e.g. `CreditNote`

### 2. Study the closest existing pattern
Read these reference files to match the codebase conventions:
- **Model**: `app/models/finance/ar/invoice.py` — Mapped[] annotations, UUID PKs, organization_id FK
- **Service**: `app/services/finance/ar/invoice_service.py` — `__init__(self, db)`, `list_for_org()`, `create()`, `get_or_404()`
- **Web service**: `app/services/finance/ar/web/invoice_web.py` — `*_response()` context methods
- **Web route**: `app/web/finance/ar.py` — thin wrappers calling web service
- **Test**: `tests/ifrs/ar/test_invoice_service.py`

### 3. Create the model
Create `app/models/{module_path}/{entity}.py`:
- Use `Mapped[]` type annotations (SQLAlchemy 2.0)
- Include `organization_id` FK for multi-tenancy
- Use UUID primary key: `{entity}_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)`
- Add `created_at`, `updated_at` timestamps
- Register in `app/models/__init__.py` if there's a central registry

### 4. Create the service
Create `app/services/{module_path}/{entity}_service.py`:
- Accept `db: Session` in `__init__`
- Always filter by `organization_id` (multi-tenancy)
- Use `db.flush()` after create (not `db.commit()` — let caller handle)
- Add type hints on ALL function signatures
- Use `select()` for queries, not `db.query()`

### 5. Create the web service (if web UI needed)
Create `app/services/{module_path}/web/{entity}_web.py`:
- `list_response()`, `detail_response()`, `form_response()` methods
- Return dicts ready for template context

### 6. Create web routes (if web UI needed)
Add routes in `app/web/{domain}/{module}.py`:
- Thin wrappers: parse request -> call service -> return TemplateResponse
- NO business logic in routes

### 7. Create database migration
```bash
poetry run alembic revision --autogenerate -m "Add {entity} table"
```
Then review the generated migration and make it idempotent (check table/column exists before creating).

### 8. Create tests
Create `tests/services/{module_path}/test_{entity}_service.py`:
- Test CRUD operations
- Test multi-tenancy isolation
- Test validation rules

### 9. Verify
```bash
poetry run mypy app/models/{module_path}/ app/services/{module_path}/ --ignore-missing-imports
poetry run ruff check app/models/{module_path}/ app/services/{module_path}/
poetry run pytest tests/services/{module_path}/ -v
```
