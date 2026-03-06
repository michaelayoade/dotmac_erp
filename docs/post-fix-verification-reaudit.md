# ERP Final Post-Fix Verification Re-Audit

**Date:** 2026-03-05  
**Project:** `/home/dotmac/projects/dotmac_erp`  
**App:** `http://160.119.127.195:8003`  
**Account used:** `admin`

## 1) Scope and Coverage Summary

This re-audit validates the final remediation pass against the required categories:
- mobile reflow
- a11y labels and icon names
- invalid-id/detail state consistency
- action vocabulary consistency
- sticky table headers
- status not color-only

### Routes checked (live browser)
- `/finance/gl/journals`
- `/finance/ar/invoices`
- `/finance/ar/invoices/new`
- `/finance/ar/invoices/99999999` (invalid-id state)
- `/people/hr/employees`
- `/people/hr/employees/new`
- `/people/hr/employees/99999999` (invalid-id state)
- `/admin/users`
- `/procurement/plans`
- `/support/tickets`

### Baseline documents referenced
- `docs/ui-ux-guide.md`
- `docs/ui-ux-audit-full.md`
- `docs/ui-ux-audit-edge-routes.md`
- `docs/fix-implementation-final.md`
- `docs/route-inventory.md`

---

## 2) Pass/Fail Matrix by Requirement Category

| Requirement Category | Result | Evidence Summary |
|---|---|---|
| Mobile reflow | **FAIL** | At mobile viewport (390w), `docOverflow=true` across sampled data-heavy routes (`/finance/gl/journals`, `/finance/ar/invoices`, `/people/hr/employees`, `/admin/users`, `/procurement/plans`, `/support/tickets`). Table/card fallback is not consistently present. |
| A11y labels + icon names | **PARTIAL** | Major improvement vs baseline: most sampled routes now show `unlabeled=0`. Remaining gaps observed: `/people/hr/employees` (`unlabeled=1`, `nameless=2`), `/support/tickets` (`nameless=1`), `/finance/*` sampled pages (`nameless=1`). |
| Invalid-id/detail consistency | **FAIL** | `/finance/ar/invoices/99999999` shows recoverable not-found UX with back/navigation actions (good). `/people/hr/employees/99999999` returns generic `400 Bad Request` shell state instead of standardized detail-error pattern; inconsistent behavior remains. |
| Action vocabulary consistency | **PASS** | Sampled create/edit/new routes show normalized labels (`Create Employee`, `Create Invoice`), and no sampled `Update/Submit/Save Changes` regressions detected in submit actions. |
| Sticky table headers | **PARTIAL** | Sticky header observed on finance sample (`/finance/gl/journals`, `/finance/ar/invoices`). Not observed on `/people/hr/employees` and `/admin/users` in sampled state. |
| Status not color-only | **PARTIAL** | Shared macro fix exists and no color-only-only regression was detected in sampled states; however, sampled routes had limited visible status chips in current data state, so full route-family verification is not yet complete. |

---

## 3) Remaining Findings (with Severity and Exact Routes)

## P0 (must fix before release)

1. **Mobile reflow still breaks usability on key list routes**  
   - Routes: `/finance/gl/journals`, `/finance/ar/invoices`, `/people/hr/employees`, `/admin/users`, `/procurement/plans`, `/support/tickets`  
   - Symptom: horizontal document overflow persists at mobile width; responsive card/list fallback not consistently active.

2. **Invalid-id/detail state handling is inconsistent across modules**  
   - Route: `/people/hr/employees/99999999`  
   - Symptom: generic `400 Bad Request` state instead of standardized recoverable detail-error component used elsewhere.

## P1 (high)

3. **Residual a11y naming gaps remain on selected pages**  
   - Routes: `/people/hr/employees`, `/support/tickets`, `/finance/gl/journals`, `/finance/ar/invoices`  
   - Symptom: remaining nameless icon controls; one remaining unlabeled control in employees page sample.

4. **Sticky header coverage not uniform in sampled table routes**  
   - Routes: `/people/hr/employees`, `/admin/users`  
   - Symptom: no sticky `<thead>` detected in sampled state.

## P2 (polish / confidence hardening)

5. **Status non-color verification incomplete due data-state limitations**  
   - Routes sampled: `/procurement/plans`, `/admin/users`, `/finance/ar/invoices`  
   - Symptom: limited visible status-chip variety in sampled data; add targeted seeded-data pass for full confidence.

---

## 4) Re-Score (Post-Fix)

Using the same rubric from prior audit:
- Accessibility: **20/25** (up from 11)
- Consistency: **17/20** (up from 14)
- Clarity: **17/20** (up from 16)
- Data UX: **15/20** (up from 13)
- Responsiveness: **8/15** (up from 7)

**Total: 77/100**  

> Improvement is significant, but release gate still fails due to open P0s.

---

## 5) Final Readiness Verdict

# **NOT READY**

### Why
Two release-blocking issues remain open:
1. Mobile reflow/horizontal overflow on key operational routes.
2. Inconsistent invalid-id/detail error-state behavior across module families.

### Recommended next actions (final closure checklist)
1. Implement/verify true mobile table fallback (or strict overflow-safe responsive pattern) on all key list routes listed above.
2. Route all invalid-id/detail failures to the same standardized `detail_error_state` experience (including People module detail endpoints).
3. Run one focused accessibility sweep to eliminate residual nameless icon controls and unlabeled input(s).
4. Re-run this exact route set at 320/390/768/1024 widths and attach screenshot evidence for closure.

When these are complete and no P0 remains, readiness can be moved to **READY**.
