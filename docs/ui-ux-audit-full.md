# Dotmac ERP Full UI/UX Audit

**Date:** 2026-03-05  
**Environment:** http://160.119.127.195:8003 (admin)  
**Baseline:** `/home/dotmac/projects/dotmac_erp/docs/ui-ux-guide.md`  
**Method:** End-to-end browser audit across reachable module routes (desktop + mobile viewport checks), with route-level accessibility/IA consistency checks.

---

## Executive Summary

Dotmac ERP has strong module coverage and generally consistent shell/navigation patterns, but it is currently held back by three release-blocking UX issues: (1) mobile/reflow failures in dense data tables, (2) major accessibility labeling gaps in high-volume forms/tables, and (3) inconsistent navigation semantics between Admin and core module layouts.

The application demonstrates good foundations (token-like spacing rhythm, reusable sidebar patterns, breadcrumb presence in many transactional pages, and meaningful empty states in Procurement). However, several core workflows violate the guide’s WCAG/reflow/table responsiveness standards and can create real user failure risk on mobile and keyboard/assistive-tech usage.

### Overall Risk
- **P0 (must-fix before release):** 5
- **P1 (current sprint):** 12
- **P2 (polish):** 10

### Score (Guide Rubric)
- Accessibility: **11/25**
- Consistency: **14/20**
- Clarity: **16/20**
- Data UX: **13/20**
- Responsiveness: **7/15**
- **Total: 61/100 (Fail, due to open P0s)**

---

## Audit Coverage (Routes reviewed)

### Finance
- `/finance/dashboard`
- `/finance/gl/journals`
- `/finance/ap/invoices`
- `/finance/ar/invoices`
- `/finance/reports`

### People
- `/people/hr/employees`
- `/people/hr/discipline`
- `/people/self/attendance`

### Procurement
- `/procurement`
- `/procurement/plans`
- `/procurement/rfqs`

### Admin
- `/admin`
- `/admin/users`
- `/admin/roles`

### Other exposed modules sampled
- `/inventory/items`
- `/fleet`
- `/support/tickets`
- `/projects`
- `/expense`
- `/public-sector/`

---

## Cross-Cutting Findings (System-wide)

### P0-1 — Mobile reflow breaks critical table workflows
- **Impacted routes:** `/finance/gl/journals`, `/finance/ap/invoices`, `/finance/ar/invoices`, `/people/hr/employees`, `/admin/users`, `/inventory/items`, `/support/tickets`
- **Evidence:** At mobile viewport (390px), table-first pages remain multi-column without card/list fallback; content becomes clipped/compressed and requires impractical horizontal scanning.
- **Guide references:** §6.1 (reflow to 320px), §7.2 (mobile table fallback), §7.3 (avoid horizontal scroll except explicit grids).
- **Exact fix recommendation:**
  1. Add responsive `DataTable` mode switch at `<md` to stacked row cards.
  2. Keep top 2–3 critical fields visible; collapse secondary fields into expandable “More details”.
  3. Add sticky mobile action bar for row actions (View/Edit/Approve) per §7.3.
- **Acceptance checks:**
  - At 320/360/390 widths, no clipped core data or hidden primary actions.
  - User can complete “find row + open details + take action” without horizontal page scroll.

### P0-2 — Form control labeling gaps (WCAG)
- **Impacted routes:** especially `/people/hr/employees`, `/support/tickets`, `/admin/users`, plus many transactional filters in Finance/Procurement.
- **Evidence:** High counts of controls without clear label/ARIA association in route-level checks (e.g., Employees ≈40 unlabeled controls; Support ≈64; Admin Users ≈23).
- **Guide references:** §1.3, §6.4, §3.2.
- **Exact fix recommendation:**
  1. Enforce `FormField` wrapper requiring `label + input id + helper/error slots`.
  2. Add lint/test rule failing unlabeled `input/select/textarea`.
  3. Add `aria-label` for icon-only or compact filter controls.
- **Acceptance checks:**
  - 0 unlabeled controls on audited routes.
  - Screen reader announces label + state + error text correctly.

### P0-3 — Admin layout diverges from shared IA pattern
- **Impacted routes:** `/admin`, `/admin/users`, `/admin/roles`
- **Evidence:** Admin uses different shell behavior than Finance/People/Procurement (missing breadcrumb consistency, different density and spacing conventions), increasing cognitive switching cost.
- **Guide references:** §1.2, §5.1, §5.2.
- **Exact fix recommendation:**
  1. Migrate Admin pages onto shared AppShell components (header/breadcrumb/action bar structure).
  2. Enforce same page heading stack (`H1 + breadcrumb + context actions`) as other modules.
- **Acceptance checks:**
  - Admin pages visually/structurally match core module shell patterns.
  - Breadcrumb behavior is consistent for depth ≥2.

### P0-4 — Color-only status communication in dense tables/charts
- **Impacted routes:** `/admin/users`, `/finance/*` list pages, `/procurement/*` status-heavy pages.
- **Guide references:** §1.3, §6.1, §3.6.
- **Exact fix recommendation:**
  - Pair status colors with text + icon/shape patterns; ensure non-text contrast ≥3:1.
- **Acceptance checks:**
  - Status remains distinguishable in grayscale and color-blind simulation.

### P0-5 — Inconsistent workflow action language for equivalent outcomes
- **Impacted routes:** multi-module (Finance/People/Admin/Procurement action bars and row menus).
- **Guide references:** §1.2, §8.2.
- **Exact fix recommendation:**
  - Standardize action verb dictionary (Create, Save, Approve, Delete, Export) and remove ambiguous variants.
- **Acceptance checks:**
  - Equivalent outcomes use identical action labels across modules.

---

## Module-by-Module Detailed Audit

## 1) Finance (Dashboard, GL, AP, AR, Reports)

### P0
1. **Mobile table unusability in GL/AP/AR lists**
   - **Routes:** `/finance/gl/journals`, `/finance/ap/invoices`, `/finance/ar/invoices`
   - **Fix:** responsive list/card fallback + pinned primary identifiers and amounts.
   - **Acceptance:** task completion on 320–390px without horizontal scan fatigue.

2. **Potential ambiguity in financial sign presentation across widgets/tables**
   - **Routes:** `/finance/dashboard`, list/report pages
   - **Fix:** enforce §4.1 sign conventions globally (`+₦`, `−₦`, consistent parentheses policy).
   - **Acceptance:** all negative values use a single convention, validated by visual regression fixtures.

### P1
1. **Dashboard information density too high; primary action hierarchy blurred**
   - **Route:** `/finance/dashboard`
   - **Fix:** keep one primary CTA cluster per section; demote secondary links to contextual menus.
   - **Acceptance:** one dominant action per card/section.

2. **Some deep pages lack clear breadcrumb depth semantics**
   - **Routes:** sampled within finance submodules
   - **Fix:** strict `Module / Section / Entity` breadcrumb builder (§5.2).
   - **Acceptance:** all depth≥2 routes show non-clickable terminal breadcrumb item.

3. **Filter bars mix dense controls without clear grouping**
   - **Routes:** journals, AP, AR
   - **Fix:** standard segmented filter rows (date/status/search/advanced).
   - **Acceptance:** reduced misclick and faster filtering in usability pass.

### P2
- Standardize KPI card subtitle microcopy and trend descriptors.
- Add clearer “last updated” metadata on reporting cards.
- Improve chart legends with direct labels where feasible.

---

## 2) People (HR, Discipline, Self Service)

### P0
1. **Large unlabeled control footprint in employee management views**
   - **Route:** `/people/hr/employees`
   - **Fix:** mandatory labeled form/filter primitives; aria for compact controls.
   - **Acceptance:** no unlabeled form controls; keyboard-only filter flow works.

2. **Self-service profile dependency blocker lacks recovery affordance richness**
   - **Route:** `/people/self/attendance`
   - **Fix:** include explicit next-step CTA + ownership contact + contextual reason.
   - **Acceptance:** user can resolve blocker from page without external support.

### P1
1. **Table-first employee directory not mobile friendly**
   - **Route:** `/people/hr/employees`
   - **Fix:** card/list fallback with key identity fields and sticky row actions.
   - **Acceptance:** scanning + opening employee record works on mobile.

2. **Sidebar depth is strong but visually heavy in one panel**
   - **Route cluster:** `/people/hr/*`
   - **Fix:** introduce group separators + progressive disclosure + concise labels.
   - **Acceptance:** first-time user can locate core HR pages within 3 clicks.

### P2
- Improve badge taxonomy consistency for statuses (Terminated/Resigned/etc.) with semantic mapping.
- Tighten spacing rhythm in high-density table cells.

---

## 3) Procurement (Dashboard, Plans, RFQs + related nav)

### P0
- None observed as hard blockers in sampled flows.

### P1
1. **Dashboard empty states are good but action priority can be clearer**
   - **Route:** `/procurement`
   - **Fix:** use one dominant CTA per empty-state card; move secondary links to text links.
   - **Acceptance:** users identify first action in <3 seconds during testing.

2. **Filter control labeling and consistency gaps in list pages**
   - **Routes:** `/procurement/plans`, `/procurement/rfqs`
   - **Fix:** align filter control labeling and helper text with §3.2.
   - **Acceptance:** all filters have explicit labels and accessible helper/error messaging.

### P2
- Normalize status chip sizing and spacing across plans/rfqs/contracts/vendors.
- Add richer empty-state educational copy for advanced workflows (evaluation/prequalification).

---

## 4) Admin (Dashboard, Users, Roles)

### P0
1. **Admin shell inconsistency vs core ERP shell**
   - **Routes:** `/admin`, `/admin/users`, `/admin/roles`
   - **Fix:** migrate to shared app shell + breadcrumb conventions.
   - **Acceptance:** navigation, spacing, and top actions match core modules.

2. **Users table on mobile is severely compressed and scan-hostile**
   - **Route:** `/admin/users`
   - **Fix:** responsive row-card transformation + prioritized fields (`User`, `Role`, `Status`, `Last Active`).
   - **Acceptance:** manage user status/role from mobile without zoom/horizontal drag.

### P1
1. **Visual density too high for permission-heavy screens**
   - **Route:** `/admin/roles`
   - **Fix:** split matrix into tabs/sections, sticky headers, clearer legends (§module example in guide).
   - **Acceptance:** permission assignment accuracy improves in QA scripts.

2. **Icon-only controls need explicit accessible names/states**
   - **Routes:** `/admin*`
   - **Fix:** enforce `aria-label` and `aria-pressed` where relevant.
   - **Acceptance:** screen reader announces icon control intent and toggle state.

### P2
- Improve vertical rhythm in list/filter container card.
- Add contextual helper microcopy for destructive role changes.

---

## 5) Other Exposed Modules (Sampled)

### Inventory (`/inventory/items`)
- **P1:** Table responsiveness/accessibility parity with Finance/People patterns needed.
- **P2:** Better truncation + tooltip consistency for long item identifiers (§4.3).

### Fleet (`/fleet`)
- **P2:** Strengthen dashboard hierarchy and CTA prominence in low-data states.

### Support (`/support/tickets`)
- **P0:** very high unlabeled control density in ticket management filters/forms; must be fixed before release.
- **P1:** mobile dense-list readability improvements needed.

### Projects (`/projects`)
- **P1:** improve consistent action labeling and status chip patterns.

### Expense (`/expense`)
- **P1:** breadcrumb inconsistency vs module standard.

### Public Sector (`/public-sector/`)
- **P1:** align heading/crumb semantics and shared shell patterns with core modules.

---

## Prioritized Implementation Backlog

## Quick Wins (<1 day)
1. Add `aria-label` to all icon-only buttons and compact filter controls.
2. Standardize action labels (Create/Save/Delete/Approve/Export) across top 30 routes.
3. Normalize badge/status taxonomy and color-token mapping.
4. Add missing breadcrumb on pages with depth ≥2 (especially Admin/Expense).
5. Add helper/error text templates to all filter bars and inline form controls.
6. Enforce numeric alignment + tabular numerals for currency columns.

## Medium (1–3 days)
1. Build shared responsive table behavior (`desktop table` → `mobile cards`) and roll out to Finance, People, Admin.
2. Refactor Admin shell to shared AppShell layout conventions.
3. Introduce global FormField primitive with strict label/id association.
4. Rework dense filter rows into grouped, consistent control architecture.
5. Add accessibility CI checks (axe + custom lint for unlabeled controls).

## Larger Initiatives (>3 days)
1. Full design-token enforcement pass (remove ad-hoc spacing/color values).
2. ERP-wide table system overhaul with virtualization strategy for >200 rows and sticky behavior standards.
3. Cross-module UX harmonization program (Finance/People/Admin/Procurement + secondary modules) with visual regression suite.
4. Accessibility hardening program to reach WCAG 2.2 AA baseline across critical workflows.

---

## Acceptance Test Matrix (Release Gate)

1. **Accessibility gate**
   - 0 unlabeled interactive form controls on critical routes.
   - Keyboard-only completion for: create invoice, edit employee, approve requisition, update role.
2. **Responsive gate**
   - Core workflows pass at 320px, 768px, 1024px without content loss.
3. **Consistency gate**
   - Same action labels for same outcomes across modules.
4. **Data UX gate**
   - Financial values use consistent sign/currency formatting and right alignment.
5. **Navigation gate**
   - Breadcrumbs present/consistent for depth≥2 and active module/page state clearly indicated.

---

## Top 10 Highest-Impact Fixes

1. Implement mobile table/card fallback for Finance GL/AP/AR lists.
2. Implement mobile table/card fallback for Admin Users and People Employees.
3. Eliminate unlabeled controls on People Employees and Support Tickets pages.
4. Migrate Admin pages to shared shell + breadcrumb conventions.
5. Add universal `FormField` component with enforced label/id/aria contract.
6. Standardize status chips (text+icon+color) across Finance/Procurement/Admin.
7. Standardize action language dictionary across all modules.
8. Re-architect dense filter bars into grouped, accessible control clusters.
9. Add automated accessibility checks in CI (axe + unlabeled-control lint rule).
10. Enforce currency/sign formatting consistency in all Finance widgets/tables/reports.

---

## Notes
- Procurement module is comparatively strongest in empty-state communication and module-level IA.
- Highest near-term ROI is the **table responsiveness + accessibility labeling** bundle; this resolves the largest user-impact and compliance risks quickly.
