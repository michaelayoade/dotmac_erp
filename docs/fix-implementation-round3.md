# ERP UI/UX Remediation — Round 3 (P1/P2)

Date: 2026-03-05
Project: `dotmac_erp`
Scope: Post-P0 consistency + polish pass

## Summary
Round 3 implemented a mechanical, cross-module normalization pass focused on:
- action label vocabulary (`Create`-first verbing)
- status token consistency via shared `status_badge` macro
- empty-state consistency (title + reason + primary CTA)
- breadcrumb/path semantics alignment
- table readability refinements (density, numeric readability, truncation/tooltips)
- copy affordance for key IDs/codes on list pages

No staging or commits were performed.

---

## Implemented Items by Priority Scope

## 1) Global action vocabulary consistency
Normalized action CTA text from mixed variants (`New`, `Add`) to `Create` patterns on key list/index routes:

- `/admin/users` → **Create User**
- `/admin/roles` → **Create Role**
- `/people/hr/employees` → **Create Employee**
- `/inventory/items` → **Create Item**
- `/expense` list → **Create Expense**
- `/finance/ar/invoices` and `/finance/ap/invoices` → **Create Invoice**
- `/finance/gl/journals` → **Create Entry**
- `/procurement/plans` → **Create Plan**
- `/procurement/rfqs` → **Create RFQ**
- `/procurement/contracts` → **Create Contract**
- `/support/tickets` empty-state CTA → **Create Ticket**

## 2) Sticky-header parity + table readability normalization
Applied shared table readability improvements in global CSS to standardize behavior across table-container pages:

- normalized table width/collapse behavior
- minimum row height target (`min-height: 44px`)
- nowrap for table headers
- tabular numeric alignment for right-aligned numeric cells
- safer truncate behavior (`.truncate` block sizing)

Added UI guardrail behavior for truncation title fallback:
- auto-populates `title` for `.truncate` and opt-in truncation nodes missing tooltips.

## 3) Status token consistency (icon/text + semantics)
Replaced locally-crafted/route-specific status chips with shared `status_badge(...)` on multiple modules to enforce consistent semantics and non-color-only tokening:

- `templates/people/hr/employees.html`
- `templates/support/tickets.html`
- `templates/admin/roles.html`
- `templates/procurement/plans/list.html`
- `templates/procurement/rfqs/list.html`
- `templates/procurement/contracts/list.html`

## 4) Empty-state consistency
Normalized empty states to include clear title + reason + primary CTA (when no filter context):

- Admin Users
- Admin Roles
- Inventory Items
- Support Tickets
- Procurement Plans/RFQs/Contracts
- Finance AR/AP/GL list pages (CTA text harmonized)
- Expense list copy and CTA harmonized

## 5) Breadcrumb/path semantics consistency
Aligned breadcrumb style and naming where divergent:

- Added intermediate `Finance` crumb in AR/AP/GL routes
- Simplified route labels (`Support Tickets` → `Tickets`, `Inventory Items` → `Items`)
- Updated employee route breadcrumb separators to icon-based pattern used elsewhere

## 6) Minor responsive/edge-route polish
- Removed route-specific micro-CSS in expense list button and normalized to shared button system.
- Added copy affordance hooks for key identifiers (ticket numbers, invoice numbers, entry numbers, item/employee codes, user email, expense number).
- Added keyboard-accessible copy behavior (Enter/Space) with visual copied feedback token.

---

## Changed Files

- `static/css/app.css`
- `static/js/accessibility-guardrails.js` *(new in working tree)*
- `templates/admin/roles.html`
- `templates/admin/users.html`
- `templates/expense/list.html`
- `templates/finance/ap/invoices.html`
- `templates/finance/ar/invoices.html`
- `templates/finance/gl/journals.html`
- `templates/inventory/items.html`
- `templates/people/hr/employees.html`
- `templates/procurement/contracts/list.html`
- `templates/procurement/plans/list.html`
- `templates/procurement/rfqs/list.html`
- `templates/support/tickets.html`

---

## Validation Results (Lightweight)

Executed:
- `node --check static/js/accessibility-guardrails.js` ✅ pass
- `poetry run pytest tests/test_backup_erp_db_script.py -q` ✅ pass (`2 passed`)

Notes:
- Full test suite was not run in this pass (intentionally lightweight per scope/time).

---

## Remaining Gaps / Follow-ups

1. **Pre-existing repo churn**
   - Repository contains many unrelated modified/untracked files outside this round’s UI pass.
   - Recommend isolating/partitioning Round 3 changes before PR assembly.

2. **Status macro coverage audit (long-tail routes)**
   - Some niche pages may still use local status pill markup; a repo-wide grep can finalize 100% migration.

3. **Copy affordance breadth**
   - Current pass covers high-value list identifiers; long-tail detail pages can be harmonized in a follow-up pass.

4. **Visual QA sweep**
   - Suggested spot-check at breakpoints (`sm`, `md`, `lg`) for touched routes to verify no regressions in compact tables and mobile cards.

---

## Outcome
Round 3 delivered cross-module P1/P2 UX consistency improvements with safe/mechanical edits, aligned with the requested priority scope and without staging/committing changes.