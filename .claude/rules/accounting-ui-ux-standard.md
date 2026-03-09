# Accounting App UI/UX Standard

Definitive reference for all user interface and experience decisions in DotMac ERP. Every new page, component, or template MUST conform to these standards. This document supersedes `ui-ux.md` for architectural decisions; `design-system.md` remains the token/component implementation reference.

---

## 1. Information Architecture & Navigation

### Module Organization

Accounting features are organized into clearly separated modules with consistent color identity:

| Module | Purpose | Accent | Tailwind |
|--------|---------|--------|----------|
| General Ledger | Chart of accounts, journals, trial balance, fiscal periods | Teal | `teal-600` |
| Accounts Receivable | Customers, invoices, receipts, credit notes, aging | Emerald | `emerald-600` |
| Accounts Payable | Suppliers, bills, payments, debit notes, aging | Rose | `rose-600` |
| Banking | Bank accounts, reconciliation, transfers, statements | Blue | `blue-600` |
| Tax | Tax periods, returns, WHT, VAT, stamp duty | Red | `red-600` |
| Fixed Assets | Asset register, depreciation schedules, disposal | Violet | `violet-600` |
| Inventory | Items, warehouses, stock movements, valuation | Amber | `amber-600` |
| Expense | Claims, approvals, reimbursements, limits | Amber | `amber-500` |
| People/HR | Employees, leave, payroll, discipline, training | Violet | `violet-500` |
| Reports | Financial statements, analytics, scheduled reports | Indigo | `indigo-500` |

Color-as-module-identity is an accounting convention from physical ledger tabs. Users develop muscle memory associating color with module. Color MUST NEVER be the sole indicator (WCAG) but it accelerates orientation.

### Sidebar Requirements

- **Persistent left sidebar** — 16rem desktop, collapsible to 4rem (icon-only)
- **Module switching** — clear mechanism to move between major modules
- **Active state** — `aria-current="page"` + module accent highlight on current item
- **Section grouping** — collapsible sub-sections with group headers
- **Sticky positioning** — sidebar stays visible while content scrolls
- **Mobile overlay** — slides in from left with backdrop on small screens (<640px)
- Each module has its own base template: `base_finance.html` (teal), `base_people.html` (violet), `base_expense.html` (amber), `base_inventory.html` (emerald), `base_procurement.html` (blue), `base_public_sector.html` (cyan), `base_modules.html` (indigo)

### Breadcrumbs

Every page below the module root MUST have breadcrumbs:

```
Finance > Accounts Receivable > Invoices > INV-00421
```

- Wrapped in `<nav aria-label="Breadcrumb">` with `<ol>`
- Final item gets `aria-current="page"`
- Clicking any ancestor navigates there
- Use the `topbar` macro with `breadcrumbs` caller block

---

## 2. Core Page Types

Every entity in the system is served by four page types. Each has mandatory elements.

### 2a. Dashboard Page

Every module MUST have a dashboard as its landing page.

```
┌─────────────────────────────────────────────────┐
│ [Module Name] Dashboard              [Date Range]│
├─────────────────────────────────────────────────┤
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐            │
│ │ Stat │ │ Stat │ │ Stat │ │ Stat │  ← 4 KPIs  │
│ │ Card │ │ Card │ │ Card │ │ Card │             │
│ └──────┘ └──────┘ └──────┘ └──────┘            │
│ ┌──────────────────┐ ┌──────────────────┐       │
│ │  Chart (trend)   │ │  Chart (breakdn) │       │
│ └──────────────────┘ └──────────────────┘       │
│ ┌──────────────────────────────────────────┐    │
│ │  Recent Items / Action Items Table       │    │
│ └──────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

**Required dashboard stat cards by module:**

| Module | Card 1 | Card 2 | Card 3 | Card 4 |
|--------|--------|--------|--------|--------|
| AR | Total Receivables | Overdue | Collections MTD | Credit Notes |
| AP | Total Payables | Overdue | Payments MTD | Pending Bills |
| GL | Total Assets | Total Liabilities | Revenue MTD | Expense MTD |
| Banking | Total Balance | Unreconciled | Inflows MTD | Outflows MTD |
| Tax | Tax Collected | Tax Paid | Net Payable | Next Filing |
| Inventory | Total Value | Low Stock Items | Receipts MTD | Issues MTD |
| Expense | Pending Claims | Approved MTD | Rejected MTD | Total Spent |

**Each stat card MUST show:**
- Label (what it measures)
- Value (formatted number, `font-mono tabular-nums`)
- Trend indicator (vs prior period: `+12%` in emerald, `-5%` in rose)
- Icon (visual anchor via `icon_svg()`)
- Click target (links to filtered list page)
- Use `stats_card()` macro — NEVER write stat card HTML inline

### 2b. List Page

The workhorse of any accounting app. Every entity type needs one.

```
┌─────────────────────────────────────────────────┐
│ Invoices                          [+ New Invoice]│
├─────────────────────────────────────────────────┤
│ [Search...           ] [Status ▾] [Date Range ▾]│
├─────────────────────────────────────────────────┤
│ ☐  Invoice#   Customer    Date     Amount  Status│
│ ☐  INV-421   Acme Corp   07 Feb   ₦45,000  Paid │
│ ☐  INV-420   Beta Ltd    05 Feb   ₦12,300  Open │
│ ☐  INV-419   Gamma Inc   03 Feb   ₦78,900  Over │
│                                                  │
│            ← 1  2  3  4  5 →                    │
└─────────────────────────────────────────────────┘
│▓▓▓▓▓▓▓▓▓ 3 selected: [Export] [Delete] ▓▓▓▓▓▓▓│
```

**Mandatory list page elements:**

1. **Page title** + primary action button (top-right) via `topbar` macro
2. **Live search** — `live_search()` macro, HTMX debounced (300ms), no page reload
3. **Filters** — status dropdown at minimum; date range for financial entities
4. **Data table** inside `<div class="table-container">` with:
   - Bulk select checkboxes (`bulk_select_header()` / `bulk_select_cell()`)
   - Sortable column headers (`sortable_th()` macro)
   - Right-aligned financial amounts (`text-right font-mono tabular-nums`)
   - Center-aligned status badges (`status_badge()` macro)
   - Row hover highlighting (`.group` class)
   - Clickable primary column (entity number links to detail)
   - Hidden columns on mobile (`hidden sm:table-cell`)
   - `scope="col"` on every `<th>`
5. **Pagination** — `pagination()` macro, offset-based
6. **Empty state** — `empty_state()` macro with CTA inside `{% else %}` of the `{% for %}` loop
7. **Bulk action bar** — `bulk_action_bar()` macro, fixed bottom (Export + Delete at minimum)
8. **Results container** — all table + pagination inside `<div id="results-container">`

### 2c. Detail Page

Shows a single entity with full context and available actions.

```
┌─────────────────────────────────────────────────┐
│ ← Invoices / INV-00421        [Edit] [More ▾]  │
├─────────────────────────────────────────────────┤
│ ┌─ Workflow Stepper ──────────────────────────┐ │
│ │ ● Draft → ● Submitted → ● Approved → ○ Paid│ │
│ └─────────────────────────────────────────────┘ │
│                                                  │
│ ┌─ Document Card ────────────────────────────┐  │
│ │ Invoice Date: 07 Feb 2026    Due: 07 Mar   │  │
│ │ Customer: Acme Corp          Terms: Net 30  │  │
│ │ Currency: <ORG_CCY>          PO#: PO-1234   │  │
│ ├────────────────────────────────────────────┤  │
│ │ # │ Description     │ Qty │ Rate  │ Amount │  │
│ │ 1 │ Consulting      │  10 │ 4,500 │ 45,000 │  │
│ ├────────────────────────────────────────────┤  │
│ │                         Subtotal:   45,000  │  │
│ │                         VAT (7.5%):  3,375  │  │
│ │                         TOTAL:      48,375  │  │
│ └────────────────────────────────────────────┘  │
│                                                  │
│ ┌─ Related Entities (Payments, Credit Notes) ─┐ │
│ │ (linked records table)                       │ │
│ └──────────────────────────────────────────────┘ │
│                                                  │
│ ┌─ Activity Timeline ────────────────────────┐  │
│ │ 08 Feb — Approved by Finance Manager       │  │
│ │ 08 Feb — Submitted for approval            │  │
│ │ 07 Feb — Created by John                   │  │
│ └────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**Mandatory detail page elements:**

1. **Breadcrumb navigation** back to list
2. **Status-aware action buttons** — only show valid actions for current state
3. **"More" dropdown** — secondary actions (Print, Void, Clone, Delete)
4. **Workflow stepper** — visual progress through entity lifecycle
5. **Status/alert banners** — when entity is voided, overdue, cancelled, etc.
6. **Document card** — structured header fields + line items table + totals summary
7. **Related entities section** — linked payments, credit notes, journals, receipts
8. **Activity/audit timeline** — who did what, when (chronological, most recent first)
9. **Print button** — triggers `@media print` optimized layout
10. **Navigation continuity** — "New X" links include parent ID as query param

### 2d. Form Page

Creates or edits an entity.

```
┌─────────────────────────────────────────────────┐
│ New Invoice                                      │
├─────────────────────────────────────────────────┤
│ ┌─ Context Banner (if prefilled) ─────────────┐ │
│ │ Creating invoice for customer: Acme Corp     │ │
│ └─────────────────────────────────────────────┘ │
│                                                  │
│ ┌─ Error Summary (if validation failed) ──────┐ │
│ │ Please fix 2 errors below                    │ │
│ └─────────────────────────────────────────────┘ │
│                                                  │
│ Customer*    [▾ Select customer          ]       │
│ Currency*    [▾ <Org currency>           ]       │
│ Invoice Date [2026-02-07]  Due Date [2026-03-09] │
│ PO Reference [___________]                       │
│                                                  │
│ ┌─ Line Items ───────────────────────────────┐  │
│ │ Item  │ Description │ Qty │ Rate  │ Amount  │  │
│ │ [▾  ] │ [________]  │ [_] │ [___] │ calc'd  │  │
│ │                               [+ Add Line]  │  │
│ ├─────────────────────────────────────────────┤  │
│ │                      Subtotal:  ₦45,000.00   │  │
│ │                      VAT:        ₦3,375.00   │  │
│ │                      TOTAL:     ₦48,375.00   │  │
│ └─────────────────────────────────────────────┘  │
│                                                  │
│ Notes [________________________________]         │
│                                                  │
│                        [Cancel]  [Save Invoice]  │
└─────────────────────────────────────────────────┘
```

**Mandatory form elements:**

1. **CSRF token** — `{{ request.state.csrf_form | safe }}` in every POST form
2. **Required field indicators** — `<span class="text-rose-500">*</span>` after label
3. **Context banner** — when prefilled from parent entity (e.g., invoice from quote)
4. **Error summary** — top of form, listing all validation errors with rose styling
5. **Inline field errors** — `<div class="form-error">` below individual fields
6. **Locked fields** — read-only display when auto-selected from context query param
7. **Line items table** — add/remove rows dynamically, real-time total calculation (Alpine.js)
8. **Form actions** — Cancel (left, `btn-secondary`) + Save (right, `btn-primary`)
9. **Data re-population** — on validation failure, all entered data preserved in `form_data`
10. **Post-submit redirect** — back to parent entity if opened from context, otherwise to list
11. **Dedicated `*_form_context()` method** on web service — returns all dropdowns, defaults, pre-selections

**Form section order:**
1. Context banner (if prefilled)
2. Error summary (if validation failed)
3. Header details (dates, reference numbers)
4. Primary entity selector (customer/supplier)
5. Amounts, line items, and allocations
6. Notes and attachments
7. Form actions

---

## 3. Financial Data Display Standards

### Number Formatting

| Type | Format | CSS Class | Example |
|------|--------|-----------|---------|
| Currency | Symbol + comma-separated, 2 decimals | `font-mono tabular-nums text-right` | `₦1,234,567.89` |
| Negative currency | Parentheses + rose color | `font-mono tabular-nums text-right text-rose-600 dark:text-rose-400` | `(₦1,234.56)` |
| Percentages | 1-2 decimals, right-aligned | `font-mono text-right` | `7.50%` |
| Quantities | Integer or decimal as needed | `font-mono text-right` | `1,000` |
| IDs / References | Monospace, left-aligned | `font-mono tabular-nums` | `INV-00421` |

**Rules:**
- NEVER use minus sign for negative amounts — always accounting parentheses `(1,234.56)`
- NEVER render `None` or `null` — use em dash `—` or empty string
- Currency symbol placement follows locale (₦ prefix for Naira)
- Zero amounts display as `₦0.00`, not blank
- Right-align ALL numeric columns in tables for decimal alignment
- Use `tabular-nums` so digits have equal width for vertical alignment

### Date Formatting

| Context | Format | Example |
|---------|--------|---------|
| Table cells | `DD MMM YYYY` | `07 Feb 2026` |
| Form inputs | `YYYY-MM-DD` (HTML5 date input) | `2026-02-07` |
| Timestamps | `DD MMM YYYY, HH:MM` | `07 Feb 2026, 14:30` |
| Period labels | `MMM YYYY` | `Feb 2026` |
| Fiscal years | `FY YYYY` | `FY 2026` |
| Relative (recent) | `Today`, `Yesterday`, `3 days ago` | — |

### Enum / Status Display

Raw Python enums render as uppercase. ALWAYS apply filters:
```jinja2
{{ status | replace('_', ' ') | title }}
```

For status badges, ALWAYS use the macro — NEVER inline badge HTML:
```jinja2
{{ status_badge(entity.status, 'sm') }}
```

### Status Color Mapping

| Color | Statuses |
|-------|----------|
| **Amber** (warning) | DRAFT, PENDING, PENDING_APPROVAL, SUBMITTED, DUE_SOON |
| **Blue** (info) | PROCESSING, IN_PROGRESS, OPEN, PARTIAL, SCHEDULED |
| **Emerald** (success) | APPROVED, PAID, POSTED, ACTIVE, RECONCILED, COMPLETED, RECEIVED |
| **Rose** (danger) | REJECTED, OVERDUE, FAILED, EXPIRED, BLOCKED, SUSPENDED |
| **Slate** (neutral) | CLOSED, VOIDED, CANCELLED, INACTIVE, REVERSED, ARCHIVED |

### None/Null Handling

```jinja2
{# CORRECT — handles Python None #}
{{ var if var else '' }}
{{ var if var else '—' }}

{# WRONG — default() only works for Jinja2 undefined, not Python None #}
{{ var | default('') }}
```

---

## 4. Accounting-Specific UI Patterns

### 4a. Double-Entry Journal Display

Journal entries MUST always display balanced debits and credits:

```
┌──────────────────────────────────────────────────┐
│ Account              │     Debit  │    Credit     │
│ ─────────────────────┼────────────┼───────────────│
│ 4100 - Revenue       │            │   ₦45,000.00  │
│ 2210 - VAT Collected │            │    ₦3,375.00  │
│ 1200 - Trade Recv.   │ ₦48,375.00 │              │
│ ─────────────────────┼────────────┼───────────────│
│ TOTALS               │ ₦48,375.00 │  ₦48,375.00  │
│                      │    Difference: ₦0.00 ✓     │
└──────────────────────────────────────────────────┘
```

**Rules:**
- Debit column on left, credit column on right (universal convention)
- Column totals row MUST be visible — debits and credits must match
- Difference row with check (emerald) or warning (rose) indicator
- Prevent submission if debits ≠ credits (client-side Alpine.js + server-side validation)
- Account codes displayed in `font-mono`

### 4b. Aging Analysis Display

AR and AP modules MUST include aging visualization:

```
┌──────────────────────────────────────────┐
│ ████████████████░░░░░░▒▒▒▒▓▓            │
│ Current (65%)  30d(15%) 60d(12%) 90d(8%) │
└──────────────────────────────────────────┘
```

Use the `aging_bar()` macro. Color progression:
- **Current**: emerald (healthy)
- **1-30 days**: amber (attention)
- **31-60 days**: orange (warning)
- **61-90 days**: rose (action needed)
- **90+ days**: dark rose (critical)

Always pair with a legend showing actual amounts per bucket.

### 4c. Trial Balance & Financial Statement Hierarchy

Hierarchical account display with indent levels:

```
Account                          Debit         Credit
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ASSETS
  Current Assets
    1100 Cash & Bank         ₦2,450,000
    1200 Trade Receivables   ₦1,890,000
    1300 Prepayments           ₦340,000
  ─── Subtotal               ₦4,680,000

  Non-Current Assets
    1500 Property & Equip.   ₦8,200,000
    1510 Accum. Depreciation              (₦1,640,000)
  ─── Subtotal               ₦6,560,000

═══ TOTAL ASSETS            ₦11,240,000
```

**Rules:**
- Indent child accounts (16px per level, use `pl-4`, `pl-8`, `pl-12`)
- Subtotal rows with thin separator line above
- Grand total rows with bold separator and `font-semibold`
- Header rows styled differently (bold, no amounts, background tint)
- Collapsible account groups (Alpine.js `x-show` toggle)
- Print-optimized layout (page breaks between major sections)

### 4d. Bank Reconciliation UI

Side-by-side comparison of book vs bank:

```
┌─────────── Book Balance ──────────┬──────────── Bank Balance ───────────┐
│ Opening Balance:    ₦1,200,000    │ Statement Balance:    ₦1,350,000   │
│                                   │                                     │
│ ☐ Deposit 07 Feb      ₦150,000   │ ☑ Deposit 07 Feb      ₦150,000    │
│ ☑ Payment 08 Feb     (₦50,000)   │ ☐ Fee 08 Feb           (₦2,500)   │
│ ☐ Transfer 09 Feb    ₦200,000    │ ☑ Transfer 09 Feb     ₦200,000    │
│                                   │                                     │
│ Adjusted Balance:   ₦1,350,000   │ Adjusted Balance:    ₦1,347,500   │
│                                   │                                     │
│              Difference: ₦2,500 (unmatched bank fee)                   │
└───────────────────────────────────┴─────────────────────────────────────┘
```

- Checkbox matching for line-by-line reconciliation
- Running adjusted balance updates in real-time (Alpine.js computed)
- Difference highlighted: emerald when `₦0.00`, rose when non-zero
- Auto-match suggestions where dates and amounts align

### 4e. Fiscal Period Controls

Period open/close status MUST be visible and controllable:

```
┌────────────────────────────────────────────────┐
│ Fiscal Year 2026                               │
├────────────────────────────────────────────────┤
│ Jan 2026  ● Closed    [Reopen]                 │
│ Feb 2026  ● Open      [Close Period]           │
│ Mar 2026  ○ Not Yet   —                        │
└────────────────────────────────────────────────┘
  ● Closed = slate    ● Open = emerald    ○ Future = amber
```

- Visual indicator of which periods accept transactions
- Warning dialog before closing (consequences explained)
- Forms that allow date selection MUST disable dates in closed periods

### 4f. Document Numbering Display

All entity numbers (INV-00421, JE-2026-0042, REC-00103) MUST:
- Use `font-mono tabular-nums` for consistent character width
- Be sequential and gap-free (voided numbers are NOT reused)
- Be the primary clickable link in list tables
- Be prominently displayed on detail pages (in topbar)

---

## 5. Workflow & Approval UX

### Workflow Stepper

Every entity with a multi-step lifecycle MUST have a visual stepper on its detail page:

```
● ─── ● ─── ● ─── ○ ─── ○
Draft  Submit  Approve  Post  Paid
              ▲ current
```

- **Completed steps**: filled circle in module accent color
- **Current step**: highlighted with label emphasis
- **Future steps**: hollow circle in muted color
- **Rejected**: rose X indicator on the rejection step

### Status-Aware Action Buttons

Action buttons on detail pages MUST only show valid transitions:

| Current Status | Available Actions |
|---------------|-------------------|
| DRAFT | Edit, Submit, Delete |
| SUBMITTED | Approve, Reject, Return to Draft |
| APPROVED | Post, Return to Draft |
| POSTED | Record Payment, Void, Print |
| PAID | Print, View Payment |
| VOIDED | (no actions — display voided banner) |

### Confirmation Dialogs

Destructive or irreversible actions require confirmation modals:

| Action Severity | Dialog Type | Button Style |
|----------------|-------------|-------------|
| **Reversible** (return to draft) | Quick `hx-confirm` tooltip | `btn-secondary` |
| **Significant** (post, approve) | Info modal with consequences | `btn-primary` or `btn-success` |
| **Destructive** (delete, void) | Danger modal, entity name in message | `btn-danger-outline` |
| **Irreversible** (close period) | Warning modal, full consequence list | `btn-danger-outline` |

**Confirmation message rules:**
- Entity name/number ALWAYS in the confirmation message
- Consequences stated explicitly ("A reversal journal will be created")
- Destructive button uses `btn-danger-outline`, NOT filled red
- Cancel is always available and is the visually safer default

---

## 6. Search, Filter & Export

### Search

- **Debounced live search** (300ms) via `live_search()` macro
- Searches across: entity number, name/description, reference fields
- Results update in-place via HTMX targeting `#results-container`
- URL updates with search params (`hx-push-url="true"`) so browser back works
- Never requires a submit button — triggers on input change

### Filters

Every list page needs at minimum:

| Filter | Required On | Implementation |
|--------|-------------|---------------|
| **Status** | All entity lists | Dropdown with "All Statuses" default |
| **Date range** | All financial entities | From/to date pickers |
| **Customer/Supplier** | AR/AP entities | Entity autosuggest dropdown |
| **Account** | GL journals, trial balance | Account picker |
| **Period** | Reports, aging | Fiscal period dropdown |

Advanced filters should be collapsible via `compact_filters()` macro to avoid overwhelming the page. Active filter count shown as chip badge.

### Export

- **Export All** button in topbar or bulk action bar
- Exports the full filtered dataset, not just current page
- Minimum formats: CSV (data analysis), PDF (print/share)
- Ideal formats: CSV, PDF, Excel (finance teams expect .xlsx)
- Export respects current filters and search query
- Use `BulkActionService.export_all()` + `window.exportAll(baseUrl)` pattern

---

## 7. Notifications & Feedback

### Toasts (Transient Feedback)

```
[✓ Invoice created successfully    ×]     ← success: 5s auto-dismiss
[⚠ Amount exceeds credit limit     ×]     ← warning: 8s auto-dismiss
[✕ Failed to post journal          ×]     ← error: persistent, manual dismiss
```

- Position: bottom-right, stacked upward
- `aria-live="polite"` container for screen readers
- Use `window.showToast(message, level)` — NEVER use `alert()`
- Success = auto-dismiss 5s, Warning = auto-dismiss 8s, Error = persistent

### Inline Alerts (Page-Level)

For contextual warnings that need attention before the user proceeds:

```html
<div class="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl p-4">
  <strong class="text-amber-700 dark:text-amber-400">Period Closing Soon</strong>
  <p class="text-amber-600 dark:text-amber-300 text-sm mt-1">Feb 2026 closes in 3 days.</p>
</div>
```

### Entity Status Banners

On detail pages when an entity has a noteworthy state:

- **Voided**: Rose banner — "This invoice has been VOIDED. Reversal journal: JE-2026-0089"
- **Overdue**: Amber banner — "This invoice is OVERDUE by 15 days. Last reminder sent 3 days ago."
- **Cancelled**: Slate banner — "This order was CANCELLED on 07 Feb 2026."

### Form Validation Feedback

- **Error summary** at top of form listing all issues
- **Inline errors** below individual fields with `form-error` class
- **Success redirect** with toast on successful submission
- **Preserve all entered data** on validation failure via `form_data` context

---

## 8. Accessibility (WCAG 2.2 AA)

Non-negotiable for accounting software (used in regulated environments with diverse users):

| Requirement | Implementation |
|-------------|---------------|
| Keyboard navigation | All interactive elements reachable via Tab key |
| Focus indicators | 2px teal outline on `:focus-visible` |
| Screen reader labels | `aria-label` on all icon-only buttons |
| Table headers | `scope="col"` on every `<th>` |
| Form labels | Every `<input>` has an associated `<label>` |
| Error identification | Errors linked to fields via `aria-describedby` |
| Color independence | Status ALWAYS has text + color, never color alone |
| Touch targets | Minimum 44x44px on all interactive elements |
| Reduced motion | Respect `prefers-reduced-motion: reduce` — disable animations |
| Contrast ratio | 4.5:1 minimum for normal text, 3:1 for large text |
| Landmark roles | `<nav aria-label="...">`, `<main>`, `role="dialog"` on modals |
| Modal behavior | `aria-modal="true"`, `aria-labelledby`, escape key closes |
| Live regions | Toast container uses `aria-live="polite"` |
| Breadcrumbs | `<nav aria-label="Breadcrumb">` with `<ol>` structure |
| Active page | Current sidebar link has `aria-current="page"` |
| Decorative icons | `aria-hidden="true"` on all decorative SVGs |

---

## 9. Responsive Design

| Breakpoint | Width | Key Adaptations |
|------------|-------|-----------------|
| **Mobile** | <640px | Sidebar overlays as drawer; 1-column grids; tables hide non-essential columns (`hidden sm:table-cell`); stat cards stack; forms go full-width; `btn-responsive` for full-width buttons |
| **Tablet (sm)** | 640px+ | Sidebar visible but collapsible; 2-column grids; forms side-by-side |
| **Desktop (md)** | 768px+ | 3-column grids; all table columns visible |
| **Large (lg)** | 1024px+ | 4-column stat card grids; comfortable spacing |
| **XL** | 1280px+ | Max content width; extra whitespace |

**Financial tables on mobile**: Show only priority columns (entity number, amount, status). Secondary data (dates, references, descriptions) hidden. Users tap a row to see the full detail page.

---

## 10. Performance UX

| Pattern | Standard |
|---------|----------|
| Page load | Content visible within 1 second |
| Search response | Results update within 500ms of typing stop |
| Form submission | Button shows `.btn-loading` spinner; prevent double-submit |
| Large lists | Paginate at 25 items default (options: 25, 50, 100) |
| Loading states | Skeleton screens (`animate-pulse`) for initial load; spinner for actions |
| Network errors | Toast with retry suggestion, never silent failure |
| Optimistic UI | Badge updates immediately on action; reverts on server error |

---

## 11. Audit Trail & Compliance UX

Accounting apps operate in regulated environments. The UI MUST support:

### Audit Trail

- **Every entity** displays created_by, created_at, updated_by, updated_at
- **Activity timeline** on detail pages — chronological, most recent first
- **Change history** — what field changed, old value → new value
- Audit data is **read-only** — cannot be modified or deleted via the UI
- Timeline entries show user name, action, and timestamp

### Document Numbering

- Sequential, gap-free numbering on all document entities
- Numbering format configurable per entity type via `SyncNumberingService`
- Numbers displayed in `font-mono tabular-nums`
- Voided numbers are NEVER reused — voided entity stays visible with VOIDED status
- Numbering gaps must be explainable (voided documents)

### Period Locking

- Closed periods reject new transactions at both UI and API level
- Forms disable date selection for closed periods
- Clear visual indicator of period status (emerald = open, slate = closed)
- Period close requires explicit confirmation with consequences listed

---

## 12. Print & Document Output

Accounting users print frequently. Every "document" entity (invoice, receipt, journal entry, statement, payment voucher) MUST support:

- **Print button** in the detail page action bar
- **Print-optimized CSS** via `@media print`:
  - No sidebar, no navigation, no action buttons
  - Company letterhead area at top
  - Structured header with entity details
  - Line items table with borders
  - Totals section
  - Footer with page number, print timestamp, "Computer Generated" notice
- **Document-style layout** that resembles a formal business document
- Amounts in `font-mono tabular-nums` in print output

---

## 13. Dark Mode

All UI MUST support dark mode via Tailwind `dark:` prefix (toggled by `class="dark"` on `<html>`).

**Pairing rule** — ALWAYS include both light and dark variants:
```html
<div class="bg-white dark:bg-slate-800 text-slate-900 dark:text-white border-slate-200 dark:border-slate-700">
```

- Never use pure black (`#000`) — darkest is `slate-900` (`#0f172a`)
- Shadows increase opacity in dark mode for visibility
- Status colors use `/20` opacity backgrounds in dark mode (e.g., `dark:bg-emerald-900/20`)
- Persisted in localStorage, toggled via Alpine.js

---

## 14. Interaction Standards

### Transitions

| Context | Duration | Classes |
|---------|----------|---------|
| Color/opacity | 150ms | `transition-colors duration-150` |
| Layout changes | 200ms | `transition-all duration-200 ease-out` |
| Modal enter | 200ms | `duration-200 ease-out` (x-transition) |
| Modal leave | 150ms | `duration-150 ease-in` (x-transition) |
| Sidebar collapse | 300ms | CSS transition on width |
| Button hover | 150ms | `translateY(-1px)` lift + shadow |
| Button active | instant | `scale(0.96)` |
| Page enter | 400ms | `fadeUp` keyframe |

**NEVER exceed 400ms for any UI transition.** Respect `prefers-reduced-motion`.

### HTMX Patterns

- Search: `hx-get` + `hx-trigger="input changed delay:300ms"` + `hx-target="#results-container"`
- Filters: `hx-get` + `hx-trigger="change"` + `hx-include` for sibling inputs
- Pagination: link-based targeting `#results-container`
- URL sync: `hx-push-url="true"` on search/filter requests

### Alpine.js Patterns

```html
<!-- CRITICAL: SINGLE quotes for x-data when using tojson -->
<div x-data='{ items: {{ items | tojson }}, showModal: false }'>
```

- `x-model` for two-way form binding
- `@click.away` for dropdown/modal dismissal
- `x-transition` for enter/leave animations
- `x-cloak` to hide until Alpine initializes

---

## Compliance Checklist

Use this checklist when building or reviewing any page:

### Every Page
- [ ] Breadcrumbs present (below module root)
- [ ] Topbar with page title via `topbar` macro
- [ ] Dark mode variants on all color classes
- [ ] Responsive at all breakpoints (mobile, tablet, desktop)
- [ ] No `text-[10px]` or `text-[11px]` — minimum is `text-xs` (12px)

### List Pages
- [ ] `live_search()` macro for search
- [ ] At least one filter (status)
- [ ] `<div id="results-container">` wrapping table + pagination
- [ ] `empty_state()` in `{% else %}` of `{% for %}` loop
- [ ] `bulk_action_bar()` with Export action
- [ ] `scope="col"` on every `<th>`
- [ ] Amounts right-aligned with `font-mono tabular-nums`
- [ ] Status badges via `status_badge()` macro

### Detail Pages
- [ ] Status-aware action buttons (only valid transitions)
- [ ] Workflow stepper for multi-step entities
- [ ] Related entities section
- [ ] Activity/audit timeline
- [ ] Print button for document entities

### Form Pages
- [ ] `{{ request.state.csrf_form | safe }}` in form
- [ ] Required field indicators (red asterisk)
- [ ] Error summary + inline field errors
- [ ] Form actions: Cancel (left, secondary) + Save (right, primary)
- [ ] `*_form_context()` method on web service
- [ ] Data re-population on validation failure
- [ ] Context banner when prefilled from parent
- [ ] No `| safe` on user-submitted content

### Financial Data
- [ ] Currency amounts in `font-mono tabular-nums text-right`
- [ ] Negative amounts in parentheses with `text-rose-600`
- [ ] Dates in `DD MMM YYYY` format (tables) or `YYYY-MM-DD` (forms)
- [ ] No `None` rendered — use `—` or empty string
- [ ] Entity numbers in `font-mono tabular-nums`

### Accessibility
- [ ] `aria-label` on icon-only buttons
- [ ] `aria-current="page"` on active sidebar link
- [ ] `scope="col"` on table headers
- [ ] Touch targets minimum 44x44px
- [ ] Color never sole indicator of status
