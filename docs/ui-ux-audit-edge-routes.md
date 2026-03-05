# ERP UI/UX Audit — Edge Routes + Hidden States

Date: 2026-03-05  
Baseline: `docs/ui-ux-guide.md`  
App: `http://160.119.127.195:8003`

## Audit Method

- Route inventory from code + templates (see `route-inventory.md`).
- Live browser verification on authenticated admin session for representative module routes and edge URLs.
- Static UI quality scan across templates for accessibility and table/layout anti-patterns.

Supporting scan artifacts:
- `docs/.template_ui_issues.json`
- `docs/.route_defs.json`
- `docs/.href_paths.txt`

## Coverage Table

| Coverage Item | Count | Notes |
|---|---:|---|
| Total discovered paths | 1,565 | Union of web route defs + template links |
| Parameterized/edge-capable patterns | 728 | Detail/edit/new/report/action variants |
| Browser-audited route states (representative live pass) | 142 | Cross-module + edge variants |
| Static template UI checks | 100% templates | 626 issues flagged heuristically |
| Not directly reachable in URL form | 131 | Placeholder links with `{{...}}` need runtime IDs/tokens |

## Route-Cluster Findings

## P0 (Critical)

1. **Widespread unlabeled form controls** (accessibility blocker)  
   - Evidence: template scan found **431 unlabeled controls**.  
   - Live spot-check: `/people/hr/employees` showed very high unlabeled input count and overflow behavior.  
   - Violates guide: **3.2 Inputs (Label required for all form controls)**, **1.3 Accessibility**.

2. **Icon/action buttons without accessible name**  
   - Evidence: **25 icon/button controls** lacking visible text/`aria-label`/title.  
   - Violates guide: **1.3 Accessibility**, **3.1 Buttons (clear action state)**.

3. **Edge error states inconsistent across detail routes**  
   - Invalid-id/detail routes often rely on generic framework fallback; recovery CTA is inconsistent.  
   - Violates guide: **3.9 Success/Error States** (must include recovery path).

## P1 (High)

4. **Table-heavy pages missing sticky header / robust overflow handling**  
   - Evidence: **164 templates** with table patterns lacking sticky/overflow hints.  
   - Violates guide: **3.3 Tables** (sticky header for long tables), **1.4 Performance**.

5. **CTA naming inconsistency (`Save` vs `Submit`/`Update`)**  
   - Evidence: 6 templates flagged using mixed language.  
   - Violates guide: **1.2 Consistency**.

6. **Narrow viewport horizontal overflow in dense pages**  
   - Observed in employee/list-heavy pages with large filter/action bars and dense columns.  
   - Violates guide: **2.3 Grid/widths**, **3.3 Tables**.

7. **Long-content truncation without discoverable expansion**  
   - Truncated values in tables/details are not consistently expandable or tooltip-backed.  
   - Violates guide: **1.1 Clarity**.

8. **Empty states inconsistent across module families**  
   - Some pages have good CTA copy; others show generic blank/table-only state.  
   - Violates guide: **3.8 Empty States**.

## P2 (Medium)

9. **Status conveyed by color only in some badges/chips**  
   - Improve explicit text/icon semantics in status chips and legends.  
   - Violates guide: **1.3 Accessibility**, **3.6 Badges**.

10. **Inconsistent spacing rhythm in forms and section stacks**  
    - Mixed gaps not aligned to 4/8 system in a subset of forms.  
    - Violates guide: **2.3 Spacing**.

11. **Primary actions occasionally duplicated in same section**  
    - Multiple high-emphasis actions where one primary should dominate.  
    - Violates guide: **1.1 Clarity**, **3.1 Buttons**.

12. **Feedback latency affordances inconsistent for async actions**  
    - Not all data actions show immediate visual acknowledgement.  
    - Violates guide: **1.4 Performance**.

## Top 15 Fixes (Actionable)

1. Add explicit `<label for>` or `aria-label` to every `input/select/textarea` in all module forms.  
2. Add `aria-label` to all icon-only action buttons (`view/edit/delete/toggle/more`).  
3. Standardize create/edit CTAs to **Save** (not mixed Save/Submit/Update) unless semantic submit is domain-specific and documented.  
4. Implement shared error-state component for invalid-id/detail routes with: reason + Back + primary recovery CTA.  
5. Add sticky headers + horizontal scroll containers to all long tables (>8 visible rows).  
6. Add mobile table fallback pattern (stacked key-value cards or column-priority collapse) under 768px.  
7. Enforce visible keyboard focus ring on all interactive controls across themes (WCAG AA contrast).  
8. Add standardized empty-state component (title, one-line reason, primary CTA) for every list screen.  
9. Add immediate feedback for async actions (button loading, skeleton, optimistic pending badge).  
10. Ensure status badges include text + icon/shape (not color-only semantics).  
11. Normalize row/action density in high-volume modules (Finance/People/Support) to default 44px with compact opt-in.  
12. Ensure numeric columns are consistently right-aligned and use tabular numerals in financial tables.  
13. Add overflow-safe handling for long identifiers (journal numbers, references, names) with tooltip + copy affordance.  
14. Consolidate duplicate primary actions per section; keep one primary, move others to secondary/ghost.  
15. Add route-level edge-state QA checklist in CI for `new/edit/detail/invalid-id/empty/mobile/keyboard` before merge.

## Cluster-Specific Notes

- **Finance (AP/AR/GL/Tax/Banking/Reports):** strongest table-density and edge-detail coverage needs; prioritize sticky headers + invalid-id recovery + numeric alignment consistency.
- **People (HR/Leave/Payroll/Self/Training):** highest form volume; prioritize labeling, keyboard order, and mobile overflow fixes.
- **Support/Procurement/Inventory/Fleet:** generally structured well, but action naming + empty-state consistency + icon-button labels need standardization.
- **Admin/Settings:** ensure high-risk actions (delete/toggle/reset) have consistent confirmation and accessible button naming.

## Unreachable/Deferred During Live Navigation

| Route Type | Count | Reason |
|---|---:|---|
| Placeholder links with `{{...}}` | 131 | Need runtime entity IDs/tokens |
| Tokenized onboarding/reset paths | Included above | Requires valid signed tokens |
| Action endpoints (POST/process) | N/A | Not direct page routes |

## Exit Criteria for Remediation

- 0 unlabeled controls in templates.
- 0 nameless icon/action buttons.
- 100% table pages pass sticky/mobile overflow checks.
- Invalid-id on all detail routes shows recoverable error UX.
- CTA vocabulary aligned to guide (Save/Delete/etc.) across modules.
