# ERP UI/UX Guide

**Scope:** Product UI standards for all ERP modules (Finance, People, Procurement, Admin)  
**Goal:** Ship interfaces that are clear, consistent, accessible, and fast.  
**Applies to:** Web app pages, reusable components, forms, tables, dashboards, and workflows.

---

## 1) Design Principles

### 1.1 Clarity
- One primary action per screen section.
- Put critical data above the fold (first 900px on desktop).
- Use plain labels: avoid internal jargon when user-facing language is possible.
- Show units everywhere numbers appear (`₦`, `%`, `days`, `hrs`).
- Keep visual hierarchy explicit:
  - Page title (H1)
  - Section title (H2/H3)
  - Supporting copy (body)

### 1.2 Consistency
- Same action labels for same outcomes everywhere:
  - Use **Save**, not mix of Save/Update/Submit for same behavior.
  - Use **Delete** for destructive actions (never Remove/Erase interchangeably).
- Reuse component variants and sizes; avoid page-specific custom controls unless approved.
- Keep list/detail/edit layouts structurally similar across modules.

### 1.3 Accessibility
- Target **WCAG 2.2 AA** minimum.
- Keyboard-operable flows for all core tasks.
- Visible focus on all interactive controls.
- Color cannot be the only way to convey status.

### 1.4 Performance
- Perceived performance targets:
  - Initial route interactive: **< 2.5s** on mid-tier laptop + normal office network.
  - Primary table/filter actions response feedback: **< 100ms visual feedback**, **< 1s data update** (with skeleton/spinner if longer).
- Prefer skeletons for data-heavy screens over blank loading states.
- Render only necessary rows on large tables (virtualization for > 200 rows).

---

## 2) Design Tokens & Specs

> Use tokens in code, not raw hex/px literals in components.

### 2.1 Color System

#### Semantic roles
- `color.bg.canvas` — app background
- `color.bg.surface` — cards/panels/modals
- `color.bg.subtle` — table stripe/hover backgrounds
- `color.text.primary`
- `color.text.secondary`
- `color.text.muted`
- `color.border.default`
- `color.border.strong`
- `color.brand.primary`
- `color.state.success`
- `color.state.warning`
- `color.state.error`
- `color.state.info`

#### Example baseline palette (can map to existing brand)
- Brand: `#2563EB`
- Success: `#15803D`
- Warning: `#B45309`
- Error: `#B91C1C`
- Info: `#0369A1`
- Light canvas: `#F8FAFC`, surface: `#FFFFFF`
- Dark canvas: `#0B1220`, surface: `#111827`

#### Contrast thresholds
- Body text (normal): **≥ 4.5:1**
- Large text (18px+ regular or 14px+ bold): **≥ 3:1**
- UI boundaries/icons/controls: **≥ 3:1** against adjacent color
- Focus ring vs surrounding color: **≥ 3:1**

#### Light/Dark guidance
- Do not invert colors mechanically; remap semantic tokens.
- Keep status meaning stable in both themes.
- In dark mode, reduce pure black; use deep gray surfaces for readability.

### 2.2 Typography Scale

#### Font families
- UI font: `Inter` (fallback: `system-ui, -apple-system, Segoe UI, Roboto, sans-serif`)
- Numeric/tabular data: enable tabular figures (`font-variant-numeric: tabular-nums`) for money and KPIs.

#### Size/line-height scale
- `text-xs`: 12 / 16
- `text-sm`: 14 / 20
- `text-base`: 16 / 24
- `text-lg`: 18 / 28
- `text-xl`: 20 / 30
- `text-2xl`: 24 / 32
- `text-3xl`: 30 / 38

#### Weight usage
- Regular 400: body/help text
- Medium 500: field labels, tabs
- Semibold 600: section titles, button text
- Bold 700: page headings, key KPIs

#### Rules
- Minimum body text: **14px**.
- Avoid line lengths > **80 characters** in prose.
- Use sentence case for labels/buttons; avoid all caps except tiny badges.

### 2.3 Spacing, Grid, and Widths

#### Spacing scale (4/8 system)
- Base unit: **4px**
- Token ladder: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64

#### Component spacing rules
- Form field vertical gap: **16px**
- Label to input gap: **6px**
- Card padding:
  - compact: 12px
  - default: 16px
  - spacious: 24px
- Section spacing: **24–32px**

#### Layout grid
- Desktop (≥1280): 12 columns, 24px gutters, 32px page margins
- Tablet (768–1279): 8 columns, 20px gutters, 24px margins
- Mobile (<768): 4 columns, 16px gutters, 16px margins

#### Max content widths
- Form pages: **max 960px**
- Read-heavy/settings pages: **max 840px**
- Dashboard canvas: full width with minimum side padding by breakpoint

### 2.4 Radius, Borders, Elevation, Shadows
- Border radius:
  - small controls: 6px
  - default controls/cards: 8px
  - modals/dropdowns: 12px
  - pills/chips: 999px
- Border widths:
  - default: 1px
  - emphasis/dividers: 2px only when needed
- Elevation levels:
  - Level 0: none
  - Level 1 (cards): `0 1px 2px rgba(16,24,40,.08)`
  - Level 2 (dropdown): `0 4px 12px rgba(16,24,40,.12)`
  - Level 3 (modal): `0 12px 32px rgba(16,24,40,.18)`

---

## 3) Component Standards

### 3.1 Buttons

#### Sizes
- Small: height 32px, horizontal padding 12px
- Medium (default): height 40px, padding 16px
- Large: height 48px, padding 20px
- Minimum target area: **44x44px**

#### Variants
- Primary: highest-emphasis action (1 per section)
- Secondary: supporting action
- Tertiary/Ghost: low-emphasis action
- Destructive: delete/archive irreversible actions

#### States
- Default, hover, focus, active, disabled, loading
- Disabled opacity should still keep text contrast ≥ 3:1
- Loading state keeps button width fixed (prevent layout shift)

### 3.2 Inputs (text/select/date/number)
- Input height: 40px default
- Horizontal padding: 12px
- Label required for all form controls (placeholder is not label)
- Helper text below control at 12–14px
- Error text below control with icon + actionable instruction
- Validation trigger:
  - Inline on blur for single field
  - Full validation on submit

### 3.3 Tables
- Row height:
  - Compact: 36px
  - Default: 44px
  - Comfortable: 52px
- Header height: 44px
- Cell padding: 12px horizontal, 8px vertical
- Numeric columns right-aligned; text left-aligned.
- Sticky header for tables with > 8 visible rows.
- Zebra striping optional; hover highlight required.

### 3.4 Cards
- Card title + optional subtitle + actions in top-right.
- Internal spacing: 16px default.
- Avoid nested cards deeper than one level.

### 3.5 Alerts
- Types: info, success, warning, error.
- Include icon + concise title + supporting text.
- Error alerts must include recovery path (e.g., “Retry”, “Contact Admin”).

### 3.6 Badges/Chips
- Status badges use semantic colors, never arbitrary colors.
- Text size: 12px, min height: 20px, horizontal padding: 8px.
- Keep status taxonomy stable across modules:
  - Draft, Pending, Approved, Rejected, Paid, Overdue, Archived

### 3.7 Modals
- Width:
  - small 480px
  - medium 640px
  - large 960px
- Max height: 90vh, body scroll inside modal.
- Must include explicit close button + Escape close.
- Primary action bottom-right, secondary left of primary.

### 3.8 Empty States
- Include: clear title, one-sentence reason, primary next action.
- Optional secondary action for docs/help.
- Example (Procurement): “No purchase orders yet. Create your first PO to start approval routing.”

### 3.9 Success/Error States
- Success confirmations for save/create actions (toast or inline)
- Error states must specify:
  - What failed
  - Why (if known)
  - What user can do next

---

## 4) Data-Heavy ERP Patterns

### 4.1 Financial Dashboards (Finance)
- KPI cards: max 4 per row on desktop, 2 on tablet, 1 on mobile.
- KPI card structure:
  - Label (12–14px)
  - Primary value (24–30px bold)
  - Delta vs period (12–14px with sign and trend icon)
- Sign conventions:
  - Positive: `+₦12,400`
  - Negative: `−₦12,400` (use minus symbol, not hyphen)
- Currency formatting:
  - Always include symbol/code and locale separators.
  - Example: `₦1,250,000.00`
  - Show 2 decimals for financial reports unless explicitly configured.

### 4.2 Chart Labeling
- Axis labels required unless chart is purely decorative.
- Avoid more than 6 legend categories in one chart.
- Use direct labels where possible for line charts.
- Color + pattern/marker for status-critical lines (do not rely on color only).

### 4.3 Tables in ERP Modules

#### Density rules
- Finance ledger: default density (44px rows)
- People directory: compact allowed when scanning many records
- Procurement approvals: default with sticky headers/actions

#### Truncation rules
- Single-line table cells truncate with ellipsis after 1 line.
- Provide full value on hover tooltip.
- Critical identifiers (invoice number, employee ID, PO number) must never truncate below distinguishable length; allocate fixed min width.

#### Long text handling
- In-cell max lines:
  - Table: 1 line + tooltip
  - Card summaries: 2 lines
  - Detail view: unlimited wrap

---

## 5) Navigation & Information Architecture

### 5.1 Sidebar/Header Consistency
- Sidebar order should remain stable across sessions.
- Use module groups: Finance, People, Procurement, Admin.
- Highlight active module and active page simultaneously.
- Header always contains:
  - Page title
  - Global search (if enabled)
  - Context actions
  - User/org menu

### 5.2 Breadcrumbs
- Required for depth ≥ 2 beyond module landing.
- Format: `Module / Section / Entity`
- Last breadcrumb item is current page (not clickable).

### 5.3 Module Switching
- Module switch control location must be fixed (top-left area).
- Switching modules should preserve organization context and role permissions.
- Unsaved-change protection on navigation: prompt before leaving dirty forms.

---

## 6) Accessibility Baseline

### 6.1 WCAG AA targets
- Text contrast and non-text contrast as defined above.
- Resize text to 200% without loss of content/function.
- Reflow support down to **320px** wide viewport.

### 6.2 Keyboard Navigation
- All interactive elements reachable via Tab.
- Logical tab order follows visual flow.
- Enter/Space behavior:
  - Enter submits focused form (when valid)
  - Space activates focused button/checkbox
- Modals trap focus; focus returns to trigger on close.

### 6.3 Focus Visibility
- Focus ring: at least **2px** outline or equivalent with offset.
- Must be visible on all backgrounds; do not remove outline without replacement.

### 6.4 ARIA rules for icon buttons
- Every icon-only button must have `aria-label`.
- Toggle buttons must expose pressed state (`aria-pressed`).
- Decorative icons must be `aria-hidden="true"`.
- Errors should be announced via `aria-live="polite"` for inline validation where applicable.

---

## 7) Responsive Behavior

### 7.1 Breakpoints
- `sm`: 0–767px (mobile)
- `md`: 768–1023px (tablet)
- `lg`: 1024–1279px (small desktop)
- `xl`: 1280–1535px (desktop)
- `2xl`: 1536px+ (wide desktop)

### 7.2 Behavior by form factor
- Desktop:
  - Persistent sidebar
  - Multi-column forms/tables
- Tablet:
  - Collapsible sidebar
  - 2-column forms where readable
- Mobile:
  - Drawer navigation
  - Single-column forms
  - Table fallback to card/list layout for dense datasets

### 7.3 Responsive rules
- Minimum horizontal page padding:
  - mobile 16px, tablet 24px, desktop 32px
- Avoid horizontal scroll on primary pages except data grids explicitly requiring it.
- Sticky action bars on mobile for key form actions.

---

## 8) Content & Microcopy

### 8.1 Labeling
- Labels should be noun-based and specific:
  - Good: “Approval threshold (₦)”
  - Bad: “Limit”
- Required fields marked consistently (`*` + accessible required semantics).

### 8.2 Action Language
- Use strong verbs:
  - Create invoice, Approve request, Export payroll, Assign role
- Avoid vague actions like “Process” unless domain-specific and defined.

### 8.3 Empty-State Messaging
- Template:
  1. What is empty
  2. Why it matters
  3. What to do next
- Example (People): “No employees added yet. Add your first employee to start attendance and payroll tracking.”

### 8.4 Error Copy
- Format: **Problem + Cause (if known) + Next step**
- Example (Finance): “Payment could not be posted. The journal period is closed. Reopen period in Finance Settings or choose an open date.”
- Never use blame language (“You entered invalid data”).

---

## 9) Implementation Checklist + Definition of Done

### 9.1 Implementation Checklist
- [ ] Uses approved design tokens (color/type/spacing/radius/shadow)
- [ ] Component variants and sizes conform to standards
- [ ] All interactive states implemented (hover/focus/active/disabled/loading)
- [ ] Empty/loading/error/success states implemented
- [ ] Keyboard access validated for critical flows
- [ ] Contrast checks pass WCAG AA thresholds
- [ ] Responsive behavior verified at `sm`, `md`, `lg`, `xl`
- [ ] Numeric/currency formatting follows module rules
- [ ] Tables support truncation + tooltip and sticky headers where required
- [ ] Microcopy reviewed for clarity and actionability

### 9.2 Definition of Done (DoD)
A UI feature is done only when:
1. It passes functional QA and meets acceptance criteria.
2. It passes accessibility baseline checks (keyboard, focus, contrast, labels).
3. It follows tokenized styles with no ad-hoc visual values.
4. It includes robust states (loading/empty/error/success).
5. It is verified on desktop, tablet, and mobile breakpoints.
6. Product/design review signs off with no unresolved P0/P1 issues.

---

## 10) Review & Audit Rubric (P0/P1/P2)

### P0 — Must Fix Before Release
- Accessibility blockers (keyboard trap, missing labels, failed contrast on core text)
- Critical workflow failure (cannot submit invoice/approve request/save employee)
- Data misrepresentation (incorrect currency/sign conventions, ambiguous totals)
- Responsive break causing unusable core actions

### P1 — Fix in Current Sprint
- Inconsistent component usage causing confusion
- Missing non-critical states (e.g., weak empty state, unclear error recovery)
- Readability issues in dense tables/charts
- Navigation inconsistencies across modules

### P2 — Polish / Improvement Queue
- Minor spacing/typography inconsistencies
- Visual refinement opportunities (alignment, hierarchy tuning)
- Enhanced helper text/tooltips for advanced workflows

### Audit scoring template (optional)
- Accessibility: /25
- Consistency: /20
- Clarity: /20
- Data UX (tables/charts/financial): /20
- Responsiveness: /15
- **Pass threshold:** ≥ 85 and no open P0

---

## Module-Specific Quick Examples

- **Finance:** Ledger tables use default density; currency always right-aligned and tabular numerals.
- **People:** Profile forms prioritize readability; progressive disclosure for advanced HR fields.
- **Procurement:** Approval status chips and timeline clarity are mandatory; empty states must prompt PO creation.
- **Admin:** Role/permission matrices require clear legends, sticky headers, and keyboard-friendly toggles.

---

## Governance
- Treat this guide as the baseline for new features and refactors.
- Exceptions require explicit design/engineering sign-off and documented rationale in PR notes.
