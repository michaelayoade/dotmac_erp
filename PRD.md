# HTMX Macro Implementation Plan for Dotmac ERP

## Overview
Implement a comprehensive HTMX macro system to enable partial page updates across the application, improving UX by eliminating full-page reloads for common operations like search, filter, pagination, and delete actions.

## Current State
- **HTMX v2.0.8** is loaded in `base.html` but underutilized
- **30+ Jinja2 macros** exist in `templates/components/macros.html`
- Admin pages (users.html, organizations.html) already use HTMX for search/filter
- No HX-Request detection exists in backend routes
- Alpine.js bulk actions already support HTMX via htmx:afterSwap listener

## Implementation Phases

---
### Phase 1: Backend Infrastructure

#### 1.1 Create HTMX Response Helpers
**File:** `app/services/htmx.py` (NEW)

```python
"""HTMX response helpers for partial template rendering."""
from typing import Any, Optional
from fastapi import Request
from fastapi.responses import HTMLResponse
import json


def is_htmx_request(request: Request) -> bool:
    """Check if the current request is an HTMX request."""
    return request.headers.get("HX-Request") == "true"


def htmx_response(
    content: str,
    trigger: Optional[dict] = None,
    push_url: Optional[str] = None,
    redirect: Optional[str] = None,
) -> HTMLResponse:
    """Create an HTMX-aware HTML response with optional HX-* headers."""
    headers = {}
    if trigger:
        headers["HX-Trigger"] = json.dumps(trigger)
    if push_url:
        headers["HX-Push-Url"] = push_url
    if redirect:
        headers["HX-Redirect"] = redirect
    return HTMLResponse(content=content, headers=headers)


def htmx_toast(message: str, type: str = "success") -> dict:
    """Helper to create a toast trigger dict."""
    return {"showToast": {"message": message, "type": type}}
```

#### 1.2 Add Context Helper
**File:** `app/web/deps.py` (MODIFY)

```python
def partial_context(request: Request, auth: WebAuthContext, **kwargs) -> dict:
    """Context for partial template renders (HTMX responses)."""
    return {
        "csrf_token": getattr(request.state, "csrf_token", ""),
        "user": {"name": auth.user_name, "initials": auth.user_initials},
        **kwargs,
    }
```

---
## Checklist

### Milestone 1: Foundation
- [ ] Create `app/services/htmx.py`
- [ ] Add `partial_context` to `app/web/deps.py`
- [ ] Create `templates/components/htmx_macros.html`
- [ ] Add HTMX CSS to `src/css/input.css`
- [ ] Add HTMX error handling to `templates/base.html`

### Milestone 2: First Module (AR Customers)
- [ ] Create `templates/finance/partials/_customer_table.html`
- [ ] Create `templates/finance/partials/_customer_row.html`
- [ ] Modify `app/services/finance/ar/web.py` for HTMX partials
- [ ] Update `templates/finance/ar/customers.html` to use HTMX macros
- [ ] Add HTMX delete route in `app/web/finance/ar.py`

### Milestone 3: Expand to Core Modules
- [ ] AP Suppliers: partials + HTMX list handling + delete route
- [ ] GL Accounts: partials + HTMX list handling + delete route
- [ ] AR Invoices: partials + HTMX list handling + delete route
- [ ] AP Invoices: partials + HTMX list handling + delete route

### Milestone 4: Secondary Modules
- [ ] Inventory Items: partials + HTMX list handling + delete route
- [ ] Fixed Assets: partials + HTMX list handling + delete route
- [ ] People/Employees: partials + HTMX list handling + delete route
- [ ] Banking Accounts: partials + HTMX list handling + delete route

---
### Phase 2: HTMX Macros

**File:** `templates/components/htmx_macros.html` (NEW)

Create the following macros:
- `htmx_search_input` — Search with debounce
- `htmx_filter_select` — Filter dropdown
- `htmx_table_container` — Wrapper for HTMX targeting
- `htmx_pagination` — Page navigation
- `htmx_delete_button` — Delete with confirmation
- `htmx_action_button` — Generic action button
- `htmx_form` — Form with HTMX submission
- `htmx_search_filter_bar` — Complete search/filter bar
- `htmx_loading_indicator` — Spinner for loading states

Key macro excerpt:
```jinja
{% macro htmx_search_input(search="", target="", url="", placeholder="Search...",
                           name="search", include="", debounce_ms=300, push_url=true) %}
<div class="relative flex-1 min-w-[200px]">
    <svg class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400">...</svg>
    <input type="text" name="{{ name }}" value="{{ search }}" placeholder="{{ placeholder }}"
           class="form-input pl-10 w-full"
           hx-get="{{ url }}"
           hx-trigger="keyup changed delay:{{ debounce_ms }}ms, search"
           hx-target="{{ target }}"
           hx-select="{{ target }}"
           {% if push_url %}hx-push-url="true"{% endif %}
           {% if include %}hx-include="{{ include }}"{% endif %}
           autocomplete="off" />
</div>
{% endmacro %}
```

---
### Phase 3: Partial Templates

Create partials directory structure and extract table/row templates:
```
templates/finance/partials/
├── _customer_table.html
├── _customer_row.html
├── _supplier_table.html
├── _supplier_row.html
├── _invoice_table.html
├── _invoice_row.html
├── _account_table.html
└── _account_row.html

templates/people/partials/
├── _employee_table.html
├── _employee_row.html

templates/operations/partials/
├── _item_table.html
├── _item_row.html
```

Example `_customer_table.html`:
```jinja
{# Required context: customers, page, total_pages, total_count, limit, search, status #}
{% from "components/macros.html" import status_badge, bulk_select_header, bulk_select_cell, empty_state %}
{% from "components/htmx_macros.html" import htmx_pagination, htmx_delete_button %}

<div class="table-container">
    <table class="table">
        <thead>
            <tr>
                {{ bulk_select_header() }}
                <th>Code</th>
                <th>Name</th>
            </tr>
        </thead>
        <tbody>
            {% for customer in customers %}
            {% include "finance/partials/_customer_row.html" %}
            {% else %}
            <tr><td colspan="10">{{ empty_state('No customers found', ...) }}</td></tr>
            {% endfor %}
        </tbody>
    </table>
</div>

{{ htmx_pagination(page=page, total_pages=total_pages, total_count=total_count,
                   target="#customers-table-container", url="/finance/ar/customers",
                   params={"search": search, "status": status}, limit=limit) }}
```

---
### Phase 4: Backend Route Modifications

#### 4.1 Update Web Services for HTMX Support
Pattern for each list view:
```python
from app.services.htmx import is_htmx_request, htmx_response, htmx_toast

def list_customers_response(self, request, auth, db, search, status, page):
    # Fetch data (existing code)
    context = {"customers": customers, "search": search, "status": status,
               "page": page, "total_pages": total_pages, ...}

    if is_htmx_request(request):
        return templates.TemplateResponse(request,
            "finance/partials/_customer_table.html", context)

    full_context = base_context(request, auth, "Customers", "ar")
    full_context.update(context)
    return templates.TemplateResponse(request, "finance/ar/customers.html", full_context)
```

#### 4.2 Add HTMX Delete Endpoints
**File:** `app/web/finance/ar.py` (MODIFY)

```python
@router.delete("/customers/{customer_id}/htmx-delete")
def htmx_delete_customer(request: Request, customer_id: str,
                         auth: WebAuthContext = Depends(require_finance_access),
                         db: Session = Depends(get_db)):
    """HTMX delete - returns empty response with toast trigger."""
    # Delete logic...
    return htmx_response(content="", trigger=htmx_toast("Customer deleted", "success"))
```

---
### Phase 5: CSS for Loading States
**File:** `src/css/input.css` (MODIFY)

```css
/* HTMX Loading States */
.htmx-indicator { display: none; }
.htmx-request .htmx-indicator { display: inline-flex; }
.htmx-request.htmx-indicator { display: inline-flex; }

/* Fade out deleted rows */
tr.htmx-swapping {
    opacity: 0;
    transition: opacity 0.3s ease-out;
}

/* Button loading state */
button[data-loading-disable].htmx-request {
    pointer-events: none;
    opacity: 0.7;
}
```

---
### Phase 6: Error Handling
**File:** `templates/base.html` (MODIFY)

```js
document.body.addEventListener('htmx:responseError', function(evt) {
    const status = evt.detail.xhr.status;
    let message = 'An error occurred';
    if (status === 401) { window.location.href = '/login'; return; }
    if (status === 403) message = 'Permission denied';
    if (status === 404) message = 'Not found';
    if (status >= 500) message = 'Server error';
    window.dispatchEvent(new CustomEvent('show-toast', {
        detail: { message, type: 'error' }
    }));
});
```

---
## Files to Create/Modify

### New Files
- `app/services/htmx.py`
- `templates/components/htmx_macros.html`
- `templates/finance/partials/_customer_table.html`
- `templates/finance/partials/_customer_row.html`
- `templates/finance/partials/_supplier_table.html`
- `templates/finance/partials/_supplier_row.html`
- `templates/finance/partials/_invoice_table.html`
- `templates/finance/partials/_invoice_row.html`
- `templates/finance/partials/_account_table.html`
- `templates/finance/partials/_account_row.html`

### Modified Files
- `app/web/deps.py` (add `partial_context`)
- `app/web/finance/ar.py` (HTMX delete endpoints)
- `app/web/finance/ap.py` (HTMX delete endpoints)
- `app/web/finance/gl.py` (HTMX delete endpoints)
- `app/services/finance/ar/web.py` (HTMX response handling)
- `app/services/finance/ap/web.py` (HTMX response handling)
- `app/services/finance/gl/web.py` (HTMX response handling)
- `templates/finance/ar/customers.html` (use HTMX macros)
- `templates/finance/ap/suppliers.html` (use HTMX macros)
- `templates/finance/gl/accounts.html` (use HTMX macros)
- `src/css/input.css` (HTMX loading styles)
- `templates/base.html` (HTMX error handling JS)

---
## Implementation Order

### Milestone 1: Foundation
1. Create `app/services/htmx.py`
2. Add `partial_context` to `app/web/deps.py`
3. Create `templates/components/htmx_macros.html`
4. Add HTMX CSS to `src/css/input.css`
5. Add error handling to `templates/base.html`

### Milestone 2: First Module (AR Customers)
1. Create `templates/finance/partials/_customer_table.html`
2. Create `templates/finance/partials/_customer_row.html`
3. Modify `app/services/finance/ar/web.py`
4. Update `templates/finance/ar/customers.html`
5. Add HTMX routes to `app/web/finance/ar.py`

### Milestone 3: Expand to Core Modules
1. AP Suppliers (same pattern)
2. GL Accounts (same pattern)
3. AR Invoices (same pattern)
4. AP Invoices (same pattern)

### Milestone 4: Secondary Modules
1. Inventory Items
2. Fixed Assets
3. People/Employees
4. Banking Accounts

---
## Verification

Manual Testing Checklist (per converted page):
- Full page load works (non-HTMX fallback)
- Search triggers partial update (no full reload)
- Filter changes trigger partial update
- Pagination updates table only
- Browser back/forward preserves state
- Delete removes row with animation
- Bulk selection persists after updates
- Toast notifications appear
- Loading indicators show during requests
- Dark mode styling correct

Commands:
- `npm run build:css`
- `uvicorn app.main:app --reload`
- `curl -H "HX-Request: true" http://localhost:8000/finance/ar/customers`

---
## Notes
- Alpine.js compatibility: Existing bulkActions component already handles htmx:afterSwap events
- CSRF tokens: Existing meta tag pattern continues to work with HTMX
- Progressive enhancement: All pages will continue to work without JavaScript
- No breaking changes: Existing functionality preserved; HTMX is additive
