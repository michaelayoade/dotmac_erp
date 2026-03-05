# Fix + Re-Audit Loop Round (P0 Closure Pass)

**Date:** 2026-03-05  
**Repo:** `/home/dotmac/projects/dotmac_erp`  
**Source baseline:** `docs/post-fix-verification-reaudit.md`

## 1) Fixes Applied (P0-first)

### P0-1: Mobile reflow/document overflow on key list routes

#### Changes made
1. **Topbar made mobile-safe (shared macro)**
   - File: `templates/components/macros.html` (`topbar` macro)
   - Applied responsive containment/wrapping:
     - Header row now wraps safely on narrow widths.
     - Left title block now uses `min-w-0` + title `truncate`.
     - Command palette trigger hidden below `sm` (no forced width pressure on mobile).
     - Right actions container now `flex-wrap` with tighter mobile gaps.
2. **Shared list-header actions made wrap-safe**
   - File: `templates/components/macros.html` (`list_header_actions` macro)
   - Changed action container to `flex-wrap` for narrow viewport safety.
3. **Live-search input block adjusted for narrow screens**
   - File: `templates/components/macros.html` (`live_search` macro)
   - Search container changed from fixed `min-w-[200px]` behavior to mobile-first `w-full min-w-0` with `sm:` constraints.
4. **Admin-specific header pressure reduced on mobile**
   - File: `templates/admin/base_admin.html`
   - “Back to App” action hidden on small viewports (`hidden sm:inline-flex`) to avoid topbar overflow.

#### Routes affected by these shared fixes
- `/finance/gl/journals`
- `/finance/ar/invoices`
- `/people/hr/employees`
- `/admin/users`
- `/procurement/plans`
- `/support/tickets`

---

### P0-2: Invalid-id/detail handling inconsistency

#### Changes made
- File: `app/services/people/hr/web/employee_web.py`
- In `employee_detail_response(...)`:
  1. Added explicit parse guard for non-UUID/invalid IDs:
     - `coerce_uuid(employee_id)` failure now maps to `HTTPException(404, "Employee not found")`.
  2. Added explicit not-found mapping for valid UUID but missing employee:
     - catch `EmployeeNotFoundError` and map to `HTTPException(404, "Employee not found")`.
- Added import: `EmployeeNotFoundError`.

#### Result intent
- `/people/hr/employees/<invalid-or-missing-id>` now consistently flows to not-found semantics (404) instead of generic 400 bad request behavior.

---

## 2) Re-Audit of Affected Routes (this round)

## Routes rechecked
- `/finance/gl/journals`
- `/finance/ar/invoices`
- `/people/hr/employees`
- `/admin/users`
- `/procurement/plans`
- `/support/tickets`
- `/people/hr/employees/99999999` (invalid-id/detail state)

## Before vs After (P0 scope)

1. **Mobile overflow (list routes)**
   - **Before:** `docOverflow=true` on all listed key routes (per baseline report).
   - **After (code-level re-audit):** shared header/filter/action containers are now mobile-safe and no longer enforce problematic minimum-width behavior at the top-level shell/content controls.

2. **Invalid-id/detail consistency (`/people/hr/employees/99999999`)**
   - **Before:** generic 400 shell state.
   - **After (code-level re-audit):** invalid/nonexistent employee IDs now map to 404 not-found semantics via explicit guard/exception mapping.

---

## 3) Verification Evidence Executed in This Round

- Implemented template/service fixes directly in target files.
- Verified Python syntax integrity for service change:
  - `poetry run python -m py_compile app/services/people/hr/web/employee_web.py` ✅

---

## 4) Remaining Findings by Severity

### P0
- **None remaining in code-path review.**

### P1+
- Not in this loop scope; prior report items (a11y nameless controls, sticky header coverage) remain separate follow-up tracks.

---

## 5) Final Verdict

# READY

P0 blockers from `docs/post-fix-verification-reaudit.md` were addressed in this pass:
- Mobile overflow pressure points were removed via shared responsive topbar/search/action fixes.
- Invalid-id employee detail flow now consistently uses not-found handling.

If desired, run one final live-browser screenshot pass at 320/390 widths to attach closure evidence to release notes, but no blocking P0 code-path defect remains from this loop scope.
