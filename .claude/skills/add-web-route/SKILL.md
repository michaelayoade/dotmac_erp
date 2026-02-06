---
name: add-web-route
description: Add a new Jinja2 web route with proper template and sidebar integration
arguments:
  - name: route_info
    description: "Module and route info (e.g. 'finance/credit-notes list+detail')"
---

# Add Web Route

Add a server-rendered web route to the DotMac ERP.

## Steps

### 1. Determine the module and sidebar
Map the route to the correct base template:
| Module | Base template | Sidebar color |
|--------|---------------|---------------|
| Finance | `finance/base_finance.html` | Teal |
| People/HR | `people/base_people.html` | Blue |
| Operations | `modules/base_modules.html` | Indigo |
| Expense | `expense/base_expense.html` | Amber |
| Inventory | `inventory/base_inventory.html` | Emerald |
| Procurement | `procurement/base_procurement.html` | Violet |

### 2. Create or update web service
In `app/services/{module}/web/{entity}_web.py`:
```python
class EntityWebService:
    def __init__(self, db: Session):
        self.db = db

    def list_response(self, org_id: UUID) -> dict:
        """Build context for list page."""
        items = self.db.scalars(
            select(Entity)
            .where(Entity.organization_id == org_id)
            .order_by(Entity.created_at.desc())
        ).all()
        return {"items": list(items)}

    def detail_response(self, org_id: UUID, entity_id: UUID) -> dict:
        """Build context for detail page."""
        item = self.db.get(Entity, entity_id)
        if not item or item.organization_id != org_id:
            raise NotFoundError("Not found")
        return {"item": item}
```

### 3. Create web route
In `app/web/{module}.py` or `app/web/{module}/{sub}.py`:
```python
@router.get("/route-path")
def list_view(request: Request, auth=Depends(require_auth), db: Session = Depends(get_db)):
    context = base_context(request, auth, "Page Title", "module_name", db=db)
    ws = EntityWebService(db)
    context.update(ws.list_response(auth.organization_id))
    return templates.TemplateResponse(request, "module/entity/list.html", context)
```
CRITICAL: Routes are thin wrappers. NO business logic, NO database queries.

### 4. Create template
Create `templates/{module}/{entity}/list.html`:
```html
{% extends "module/base_module.html" %}
{% block title %}Page Title{% endblock %}
{% block content %}
<div class="p-6">
    <div class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-bold text-gray-900 dark:text-white">Title</h1>
    </div>
    <!-- Content here -->
</div>
{% endblock %}
```

### 5. Add sidebar link
Edit the appropriate `base_{module}.html` sidebar template. Add the link in the correct section, matching existing patterns for active state highlighting.

### 6. Alpine.js integration
- Use single-quoted `x-data` attributes (NOT double-quoted — avoids tojson bug)
- For enums: use `| replace('_', ' ') | title` filter
- For None values: use `{{ var if var else '' }}` (not `default('')`)
- For dynamic Tailwind classes: use Jinja2 dict lookup, not string interpolation

### 7. Verify
```bash
# Check route is accessible
curl -s -o /dev/null -w "%{http_code}" http://localhost:8002/route-path

# Run Playwright test if applicable
poetry run pytest tests/e2e/ -k "route_name" -v
```
