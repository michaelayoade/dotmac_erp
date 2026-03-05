# ERP Route Inventory (Code + Template Discovery)

Date: 2026-03-05  
Project: `dotmac_erp`  
Base URL: `http://160.119.127.195:8003`

## Method

1. Parsed all web route decorators from `app/web/**/*.py` (`@router.get/post/...`, `@app.get/post/...`).
2. Extracted route-like links from `templates/**/*.html` (`href="/..."`).
3. De-duplicated paths and classified into route types.
4. Added edge-state route variants (`/new`, `/edit`, detail/id, invalid-id) from parameterized patterns.

Artifacts generated during inventory:
- `docs/.route_defs.json` (raw extracted route definitions)
- `docs/.href_paths.txt` (raw extracted template href paths)

## Coverage Summary

| Metric | Count |
|---|---:|
| Web route definitions discovered (`app/web`) | 1,362 |
| Unique template href paths discovered | 578 |
| Total unique discovered paths (union) | 1,565 |
| Static/no-param paths | 837 |
| Parameterized paths (with `{...}`) | 728 |

## Route Classification (inventory-level)

| Class | Count | Notes |
|---|---:|---|
| Public | 11 | Includes `/`, `/login`, `/admin/login`, forgot/reset password, onboarding token entry |
| Authenticated | 1,246 | Majority of module routes (Finance, People, Inventory, Support, Procurement, Fleet, Projects, Expense, Coach) |
| Admin-only | 74 | `/admin*` surface and admin settings subpages |
| Hidden/Internal | 234 | callback/export/import/toggle/archive/delete/review/file helper routes; mostly action/process endpoints surfaced in UI flows |
| Error/Edge-capable patterns | 728 | Parameterized detail routes and create/edit/report variants requiring edge-state checks |

## Module Cluster Inventory (high-level)

| Cluster | Path Prefixes (examples) | Auth Scope |
|---|---|---|
| Core/Auth | `/`, `/login`, `/logout`, `/forgot-password`, `/reset-password` | Public + Auth transitions |
| Finance | `/finance/dashboard`, `/finance/gl/*`, `/finance/ap/*`, `/finance/ar/*`, `/finance/reports/*`, `/finance/tax/*`, `/finance/banking/*`, `/finance/settings/*`, `/automation/*` | Authenticated |
| People | `/people/hr/*`, `/people/payroll/*`, `/people/leave/*`, `/people/attendance/*`, `/people/recruit/*`, `/people/training/*`, `/people/self/*` | Authenticated |
| Public Sector | `/public-sector/*` | Authenticated |
| Inventory | `/inventory/*` | Authenticated |
| Fleet | `/fleet*` | Authenticated |
| Support | `/support/*` | Authenticated |
| Procurement | `/procurement/*` | Authenticated |
| Projects/Tasks | `/projects*`, `/tasks*` | Authenticated |
| Expense | `/expense*` | Authenticated |
| Coach | `/coach*` | Authenticated |
| Admin | `/admin*` | Admin-only |

## Unreachable / Not directly auditable route list (from inventory pass)

### A) Template placeholder links (requires runtime ID interpolation)
- Count: **131**
- Pattern: links containing `{{ ... }}` (e.g. `/finance/ar/invoices/{{`, `/people/hr/employees/{{`, `/onboarding/start/{{`)
- Reason: cannot be directly navigated as-is; requires existing entity IDs/tokens.

### B) Action/process routes not standalone pages
- Examples: `/toggle`, `/delete`, `/archive`, `/restore`, `/bulk/*`, `/recalculate`, `/review`, `/file`
- Reason: intended as POST/action handlers or workflow transitions, not direct page views.

### C) Token-protected/public magic-link routes
- Examples: `/onboarding/start/{token}`, reset flows
- Reason: require valid signed token/session context.

---

## Edge Route Set Defined for UI Audit

The audit targets each cluster with these edge variants:
- Empty states (no records)
- Create/new forms
- Edit/detail pages
- Invalid ID/detail not found states
- Loading and async action feedback
- Narrow viewport/mobile behavior
- Keyboard-only path and focus visibility
- Unlabeled interactive controls

(Findings and prioritized fixes are in `docs/ui-ux-audit-edge-routes.md`.)
