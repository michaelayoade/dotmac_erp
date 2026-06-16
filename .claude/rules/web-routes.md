# Web Route Patterns

## Route Structure
Web routes live in `app/web/` and are THIN WRAPPERS only.

```python
@router.get("/items")
def list_items(request: Request, auth=Depends(require_auth), db: Session = Depends(get_db)):
    context = base_context(request, auth, "Items", "inventory", db=db)
    ws = ItemWebService(db)
    context.update(ws.list_response(auth.organization_id))
    return templates.TemplateResponse(request, "inventory/items/list.html", context)
```

## NEVER do this in routes:
- `db.query()`, `select()`, `db.add()` — use service layer
- Business logic (if/else calculations) — use service layer
- Direct model imports for queries — use web service

## Web Service Pattern
Web services build template context:
```python
class ItemWebService:
    def __init__(self, db: Session):
        self.db = db

    def list_response(self, org_id: UUID) -> dict:
        items = self.db.scalars(select(Item).where(Item.organization_id == org_id)).all()
        return {"items": list(items)}
```

## Base Templates
| Module | Base template | Sidebar color |
|--------|---------------|---------------|
| Finance | `finance/base_finance.html` | Teal |
| People/HR | `people/base_people.html` | Blue |
| Operations | `modules/base_modules.html` | Indigo |
| Expense | `expense/base_expense.html` | Amber |
| Inventory | `inventory/base_inventory.html` | Emerald |
| Procurement | `procurement/base_procurement.html` | Violet |
| Public Sector | `public_sector/base_public_sector.html` | Cyan |
| Admin | `admin/base_admin.html` | Gray |

## Route URL Conventions
- Use kebab-case: `/finance/sales-orders` (not `/finance/sales_orders`)
- Module prefix: `/finance/...`, `/inventory/...`, `/people/...`, `/public-sector/...`
- Some routes break nesting: `/finance/quotes` (not `/finance/ar/quotes`)
- Standalone modules: `/public-sector/` (IPSAS fund accounting, own base template)
- Automation at `/automation` (not `/finance/automation`)

## API Mounting & Versioning
JSON API routers are mounted via `_include_api_router()` in `app/main.py`, which
mounts each router **twice**: at the bare path (`/gl/...`) and under `/api/v1/...`.

- **`/api/v1/...` is canonical** — use it for all new clients, docs, and integrations.
- The bare path is a **legacy compatibility alias** (older in-tree callers + UI fetches).
  Both mounts share identical dependencies and response models, so they never diverge.
- **Breaking changes** → add a *new* `/api/v2` router (`prefix="/api/v2"`); do not
  re-point `_include_api_router`. Deprecate `/api/v1` on a published timeline rather
  than mutating it in place.
- Web (HTML) routers are NOT dual-mounted — they mount once at their module path.
