# DotMac ERP Design System

Comprehensive reference for all UI decisions. Fonts are self-hosted. Stack: Tailwind CSS + Alpine.js + HTMX + Jinja2.

## Design Tokens

### Color Palette (CSS Variables)

```
--ink: #0f172a          --ink-light: #334155      --ink-muted: #64748b      --ink-faint: #94a3b8
--parchment: #faf9f7    --parchment-dark: #f5f3f0
--teal: #0d9488         --teal-light: #ccfbf1     --teal-dark: #115e59
--gold: #d97706         --gold-light: #fef3c7     --gold-dark: #92400e
--brand-primary: var(--teal)    --brand-accent: var(--gold)
```

### Module Accent Colors

| Module | Variable | Hex | Tailwind |
|--------|----------|-----|----------|
| Finance/GL | `--module-finance` | `#0d9488` | `teal-600` |
| AR | `--module-ar` | `#059669` | `emerald-600` |
| AP | `--module-ap` | `#e11d48` | `rose-600` |
| Banking | `--module-banking` | `#2563eb` | `blue-600` |
| Inventory | `--module-inv` | `#d97706` | `amber-600` |
| Fixed Assets | `--module-fa` | `#7c3aed` | `violet-600` |
| Tax | `--module-tax` | `#dc2626` | `red-600` |
| People/HR | `--module-people` | `#8b5cf6` | `violet-500` |
| Expense | `--module-expense` | `#f59e0b` | `amber-500` |
| Operations | `--module-operations` | `#3b82f6` | `blue-500` |
| Admin | `--module-admin` | `#6366f1` | `indigo-500` |
| Automation | `--module-automation` | `#a855f7` | `purple-500` |
| Reports | `--module-reports` | `#6366f1` | `indigo-500` |

### Status Colors

| Status | Text | Background | Border |
|--------|------|------------|--------|
| Success/Paid/Active | `text-emerald-700 dark:text-emerald-400` | `bg-emerald-50 dark:bg-emerald-900/20` | `border-emerald-200 dark:border-emerald-800` |
| Warning/Pending/Draft | `text-amber-700 dark:text-amber-400` | `bg-amber-50 dark:bg-amber-900/20` | `border-amber-200 dark:border-amber-800` |
| Error/Overdue/Rejected | `text-rose-700 dark:text-rose-400` | `bg-rose-50 dark:bg-rose-900/20` | `border-rose-200 dark:border-rose-800` |
| Info/Processing | `text-blue-700 dark:text-blue-400` | `bg-blue-50 dark:bg-blue-900/20` | `border-blue-200 dark:border-blue-800` |
| Neutral/Closed/Voided | `text-slate-600 dark:text-slate-400` | `bg-slate-100 dark:bg-slate-800` | `border-slate-200 dark:border-slate-700` |

### Typography

| Role | Font | Class | Size | Weight |
|------|------|-------|------|--------|
| Page titles (h1) | Fraunces (serif) | `font-display text-xl font-semibold` | 20px | 600 |
| Section titles (h2) | DM Sans | `text-lg font-semibold` | 18px | 600 |
| Body text | DM Sans | `text-sm` | 14px | 400 |
| Labels | DM Sans | `form-label` or `label-caps` | 13px/11px | 600, uppercase, 0.05em/0.1em tracking |
| Captions | DM Sans | `text-xs font-medium` | 12px | 500 |
| Financial values | JetBrains Mono | `font-mono tabular-nums` | inherit | inherit |
| Code snippets | JetBrains Mono | `font-mono` | inherit | inherit |

**Rules**: Minimum text size is `text-xs` (12px). NEVER use `text-[10px]` or `text-[11px]`. Financial values, amounts, IDs, and invoice numbers MUST use `font-mono tabular-nums`.

### Spacing Scale

```
--spacing-xs: 4px       --spacing-sm: 8px       --spacing-md: 12px
--spacing-lg: 16px      --spacing-xl: 20px      --spacing-2xl: 24px      --spacing-3xl: 32px
--spacing-card: 24px    --spacing-card-sm: 16px  --spacing-card-lg: 32px
--spacing-input-x: 16px --spacing-input-y: 12px  --content-gutter: var(--spacing-2xl)
```

| Context | Tailwind | CSS Var |
|---------|----------|---------|
| Between cards in a grid | `gap-4` | 16px |
| Between page sections | `gap-6` / `space-y-6` | 24px |
| Between major page zones | `gap-8` | 32px |
| Card padding (mobile) | `p-4` | 16px |
| Card padding (desktop) | `p-6` | 24px |
| Stat card padding | `p-5` | 20px |

### Border Radius

```
--radius-sm: 4px        --radius-md: 6px (badges)     --radius-lg: 10px (buttons, inputs)
--radius-xl: 12px (icons)   --radius-2xl: 16px         --radius-card: 16px
--radius-btn: 10px      --radius-input: 10px           --radius-badge: 6px    --radius-icon: 12px
```

### Shadows

```
--card-shadow:       0 1px 3px rgba(15,23,42,.04), 0 4px 12px rgba(15,23,42,.06)
--card-shadow-hover: 0 8px 24px rgba(15,23,42,.12)
--btn-shadow:        0 4px 12px rgba(26,31,54,.25)
--btn-shadow-hover:  0 8px 20px rgba(26,31,54,.35)
```

## Component Patterns

### Buttons

**Base**: All buttons have `min-height: 44px` (touch target), `border-radius: var(--radius-btn)`, `font-weight: 600`, `letter-spacing: 0.02em`, `transition: all 0.15s ease`.

| Variant | Class | When to Use |
|---------|-------|-------------|
| Primary | `btn btn-primary` | Main CTA (one per page section) |
| Secondary | `btn btn-secondary` | Alternative actions, Cancel |
| Ghost | `btn btn-ghost` | Tertiary actions, less emphasis |
| Text | `btn btn-text` | Inline actions, minimal chrome |
| Success | `btn btn-success` | Approve, Confirm, Accept |
| Danger outline | `btn btn-danger-outline` | Delete, Reject, Cancel (destructive) |
| Small | `btn btn-sm` | Table row actions, compact areas |
| Responsive | `btn btn-responsive` | Full-width mobile, auto desktop |

**Hover**: `translateY(-1px)` lift + `shadow-btn-hover`. **Active**: `scale(0.96)`.
**Disabled**: `opacity: 0.5; cursor: not-allowed`. **Loading**: `.btn-loading` shows spinner overlay.

### Cards

```html
<div class="card">                          <!-- Standard card -->
<div class="card card-hover">               <!-- Clickable card (lifts on hover) -->
<div class="card card-responsive">          <!-- Responsive padding (p-4 → p-5 → p-6) -->
<div class="card-header">Title</div>        <!-- Card header with bottom border -->
```

Card structure: `bg: var(--card-bg)`, `border: 1px solid var(--card-border)`, `border-radius: var(--radius-card)`, `box-shadow: var(--card-shadow)`, `padding: var(--spacing-card)`.

### Tables

```html
<div class="table-container">               <!-- Horizontal scroll wrapper -->
  <table class="table">
    <thead>
      <tr>
        <th scope="col">Name</th>           <!-- ALWAYS scope="col" -->
        <th scope="col" class="text-right">Amount</th>
        <th scope="col" class="text-center">Status</th>
        <th scope="col" class="hidden sm:table-cell">Date</th>  <!-- Hide on mobile -->
      </tr>
    </thead>
    <tbody>
      <tr class="group">                     <!-- "group" enables hover action buttons -->
        <td><a href="..." class="font-medium text-slate-900 dark:text-white hover:text-teal-600">Item Name</a></td>
        <td class="text-right font-mono tabular-nums">{{ amount | format_currency }}</td>
        <td class="text-center">{{ status_badge(status, 'sm') }}</td>
        <td class="hidden sm:table-cell text-slate-500">{{ date | format_date }}</td>
      </tr>
    </tbody>
  </table>
</div>
```

**Table Rules**:
- Left-align text, right-align numbers (`text-right`), center status badges (`text-center`)
- Header: 12px uppercase, 0.05em tracking, `bg: var(--parchment)`
- Row hover: `rgba(13, 148, 136, 0.04)` (teal 4%)
- Action buttons appear on row hover via `group` class
- Every `{% for %}` loop MUST have `{% else %}` with `{{ empty_state(...) }}`

### Forms

```html
<form method="POST" class="space-y-6">
  {{ request.state.csrf_form | safe }}        <!-- CSRF token — MANDATORY -->

  <div>
    <label class="form-label">Field Name <span class="text-rose-500">*</span></label>
    <input type="text" name="field" class="form-input" placeholder="Enter value" required>
    <span class="form-hint">Optional help text</span>
  </div>

  <div>
    <label class="form-label">Select</label>
    <select name="status" class="form-select">
      <option value="">Choose...</option>
    </select>
  </div>

  <!-- Currency input -->
  <input type="number" step="0.01" class="form-input form-input-currency">

  <!-- Error display -->
  <div class="form-error">Validation message here</div>

  <!-- Actions: Cancel left, Save right -->
  <div class="flex gap-3 justify-end">
    <a href="/back" class="btn btn-secondary">Cancel</a>
    <button type="submit" class="btn btn-primary">Save</button>
  </div>
</form>
```

**Input styling**: `min-height: 44px`, `padding: var(--spacing-input-y) var(--spacing-input-x)`, `border-radius: var(--radius-input)`. Hover: teal border + light bg. Focus: `inset 0 0 0 2px var(--input-focus)`.

### Status Badges

ALWAYS use the macro — NEVER write inline badge HTML:
```jinja2
{{ status_badge(entity.status, 'sm') }}
```

70+ mapped statuses including: DRAFT, PENDING, APPROVED, REJECTED, PAID, POSTED, OVERDUE, CANCELLED, ACTIVE, INACTIVE, OPEN, CLOSED, SUBMITTED, PROCESSING, RECEIVED, QUARANTINED, EXPIRED, DEPLETED, AVAILABLE, IN_PROGRESS, PARTIAL, VOIDED, REVERSED, RECONCILED, etc.

### Stat Cards

```jinja2
{{ stats_card(
    label="Revenue",
    value=revenue | format_currency,
    icon="trending-up",
    color="emerald",
    variant="revenue",        {# Adds colored top border gradient #}
    href="/finance/ar/invoices",
    trend="+12%",
    subtitle="vs last month"
) }}
```

**Color options**: `emerald`, `rose`, `blue`, `amber`, `teal`, `violet`, `slate`.
**Variants**: `revenue` (emerald→violet gradient), `expenses` (rose→pink), `receivables` (emerald→teal), `payables` (amber→orange). Variant adds a 3px top border gradient.

### Empty States

```jinja2
{{ empty_state(
    title="No invoices yet",
    description="Create your first invoice to get started.",
    icon="document",
    cta_text="Create Invoice",
    cta_href="/finance/ar/invoices/new"
) }}
```

Centered layout, min-height 300px, floating icon animation, gradient icon background (teal → gold).

### Search (Live Search Macro)

```jinja2
{# Simple search #}
{{ live_search(search=search, base_url="/module/items", placeholder="Search items...") }}

{# With static filter dropdowns #}
{{ live_search(search=search, base_url="/module/items", placeholder="Search...",
    filters=[
        {"name": "status", "label": "All Status", "value": status,
         "options": [{"value": "ACTIVE", "label": "Active"}, {"value": "INACTIVE", "label": "Inactive"}]}
    ]
) }}

{# With dynamic filters (Jinja2 loops) #}
{% call(search_attrs) live_search(search=search, base_url="/module/items") %}
    <select name="category" class="form-select" {{ search_attrs }}>
        <option value="">All Categories</option>
        {% for cat in categories %}
        <option value="{{ cat.id }}" {{ 'selected' if ... }}>{{ cat.name }}</option>
        {% endfor %}
    </select>
{% endcall %}

{# With entity autosuggest #}
{{ live_search(search=search, base_url="/finance/ar/customers", entity_type="customers") }}
```

Results MUST be in `<div id="results-container">`. The macro creates its own card — never double-wrap. HTMX-powered with 300ms debounce.

### Toasts (Notification System)

```javascript
window.showToast('Invoice created successfully', 'success');   // Auto-dismiss 5s
window.showToast('Please check the amount', 'warning');        // Auto-dismiss 8s
window.showToast('Failed to save', 'error');                   // Persistent, manual dismiss
```

Position: bottom-right, stacked upward. Uses `aria-live="polite"`. NEVER use `alert()`.

### Modals

```html
<div x-show="showModal" x-cloak
     role="dialog" aria-modal="true" aria-labelledby="modal-title"
     @keydown.escape.window="showModal = false"
     class="fixed inset-0 z-50 flex items-center justify-center">
  <!-- Backdrop -->
  <div class="fixed inset-0 bg-black/50" @click="showModal = false"
       x-transition:enter="duration-200 ease-out" x-transition:enter-start="opacity-0"
       x-transition:leave="duration-150 ease-in" x-transition:leave-end="opacity-0"></div>
  <!-- Panel -->
  <div class="relative card max-w-lg w-full mx-4"
       x-transition:enter="duration-200 ease-out" x-transition:enter-start="opacity-0 scale-95"
       x-transition:leave="duration-150 ease-in" x-transition:leave-end="opacity-0 scale-95">
    <h3 id="modal-title" class="text-lg font-semibold">Title</h3>
    <!-- Content -->
  </div>
</div>
```

### Bulk Actions Bar

Fixed at bottom, slides up when items selected. Dark background (`var(--ink)`), white text.
```jinja2
{{ bulk_action_bar(
    actions=[
        {"name": "export", "label": "Export", "icon": "download"},
        {"name": "delete", "label": "Delete", "icon": "trash", "confirm": true, "danger": true}
    ],
    entity_name="invoices"
) }}
```

### Progress & Aging Bars

```jinja2
{# Aging bar (stacked: emerald → amber → orange → rose) #}
{{ aging_bar(current=5000, days30=2000, days60=800, days90=200, show_legend=true) }}

{# Progress bar (single color) #}
{{ progress_bar(label="Budget Used", value="₦450,000", percentage=75, color="teal") }}
```

### Sparklines

```jinja2
{{ sparkline(data=[10, 25, 15, 30, 22, 35, 28], color="emerald", width=80, height=30) }}
```

SVG-based mini line chart, no dependencies.

## Layout Patterns

### App Shell

```
┌─────────────────────────────────────────┐
│ app-shell (flex row)                    │
│ ┌──────────┬───────────────────────────┐│
│ │ sidebar  │ app-main                  ││
│ │ 16rem    │ ┌───────────────────────┐ ││
│ │ sticky   │ │ app-header (sticky)   │ ││
│ │ h-screen │ │ topbar + breadcrumbs  │ ││
│ │          │ ├───────────────────────┤ ││
│ │          │ │ app-page              │ ││
│ │          │ │ padding: content-gutter│ ││
│ │          │ │ (page content here)   │ ││
│ │          │ └───────────────────────┘ ││
│ └──────────┴───────────────────────────┘│
└─────────────────────────────────────────┘
```

### Page Types

**List Page**:
```
[Topbar: title + "New" button]
[Live Search + Filters]
[Results Container]
  [Table with bulk select]
  [Pagination]
[Bulk Action Bar (when selected)]
```

**Detail Page**:
```
[Topbar: title + action buttons (Edit, Delete, etc.)]
[Status banner (if applicable)]
[Info grid: 2-3 columns of key-value pairs]
[Tabs or sections for related data]
[Related items table]
[Activity/history timeline]
```

**Dashboard Page**:
```
[Topbar: module name]
[Stat cards grid: 2 cols mobile, 4 cols desktop]
[Charts row: 1-2 Chart.js canvases]
[Recent items table]
[Quick actions grid]
```

**Form Page**:
```
[Topbar: "New/Edit Entity"]
[Context banner (if prefilled from parent)]
[Error summary (if validation failed)]
[Form fields in space-y-6 layout]
[Form actions: Cancel (left), Save (right)]
```

### Topbar Macro

```jinja2
{% call(breadcrumbs, actions) topbar("Invoice #INV-001", accent="teal") %}
  {% call(bc) breadcrumbs %}
    {{ bc("Invoices", "/finance/ar/invoices") }}
    {{ bc("INV-001") }}
  {% endcall %}
  {% call() actions %}
    <a href="..." class="btn btn-primary">Edit</a>
  {% endcall %}
{% endcall %}
```

### Sidebar Structure

Each module has its own base template with colored sidebar:
- `base_finance.html` (teal)
- `base_people.html` (violet)
- `base_expense.html` (amber)
- `base_inventory.html` (emerald)
- `base_procurement.html` (blue)
- `base_modules.html` (indigo, Operations only)

Sidebar: 16rem width, sticky, collapsible to 4rem (icon-only). Active link: `aria-current="page"` + accent color highlight.

### Responsive Breakpoints

| Breakpoint | Width | Layout Changes |
|-----------|-------|----------------|
| Mobile | < 640px | Sidebar overlays, single column, stacked forms, `hidden sm:table-cell` |
| Tablet (sm) | 640px+ | 2-col grids, sidebar visible, forms side-by-side |
| Desktop (md) | 768px+ | 3-col grids, full table columns |
| Large (lg) | 1024px+ | 4-col stat grids, comfortable spacing |
| XL (xl) | 1280px+ | Max content width, extra whitespace |

## Interaction Patterns

### Transitions

| Context | Duration | Easing | Classes |
|---------|----------|--------|---------|
| Color/opacity changes | 150ms | ease | `transition-colors duration-150` |
| Layout changes | 200ms | ease-out | `transition-all duration-200 ease-out` |
| Modal enter | 200ms | ease-out | `duration-200 ease-out` (x-transition) |
| Modal leave | 150ms | ease-in | `duration-150 ease-in` (x-transition) |
| Sidebar slide | 300ms | ease | CSS transition on width |
| Button hover | 150ms | ease | Lift + shadow |
| Button active | instant | — | `scale(0.96)` |
| Page enter | 400ms | ease-out | `fadeUp` keyframe |

NEVER exceed 400ms for any UI transition. Respect `prefers-reduced-motion: reduce`.

### Keyframe Animations

```css
fadeUp:       opacity 0→1, translateY(16px→0)     /* Page/card enter */
slideInLeft:  opacity 0→1, translateX(-12px→0)    /* Sidebar items */
scaleIn:      opacity 0→1, scale(0.95→1)          /* Modal/dropdown */
shimmer:      background-position 200%→-200%      /* Skeleton loading */
float:        translateY(0→-8px→0), 3s infinite   /* Empty state icon */
```

### Loading States

- **Button loading**: `.btn-loading` — spinner overlay, text hidden
- **Table loading**: HTMX `htmx-request` class adds spinner to search bar
- **Skeleton loading**: `.animate-pulse` + gray backgrounds on placeholder elements
- **Page loading**: View transitions API (250ms crossfade)

### HTMX Patterns

- Search: `hx-get` with `hx-trigger="input changed delay:300ms"` and `hx-target="#results-container"`
- Filters: `hx-get` with `hx-trigger="change"`, `hx-include` for sibling inputs
- Pagination: Link-based, targets `#results-container`
- Inline edit: `hx-post` with `hx-swap="outerHTML"`
- Delete: `hx-delete` with `hx-confirm` dialog

### Alpine.js Patterns

```html
<!-- CRITICAL: Use SINGLE quotes for x-data with tojson -->
<div x-data='{ items: {{ items | tojson }}, showModal: false }'>

<!-- Toggle -->
<button @click="showModal = !showModal">

<!-- Conditional rendering -->
<div x-show="showModal" x-cloak x-transition>

<!-- Loop -->
<template x-for="item in items" :key="item.id">
```

NEVER use double quotes for `x-data` when using `tojson` — it breaks Alpine parsing.

## Dark Mode Strategy

Implemented via `class="dark"` on `<html>` (toggled by Alpine.js, persisted in localStorage).

All CSS variables have dark mode overrides:
```css
.dark {
  --ink: #f8fafc;              /* Light text on dark bg */
  --parchment: #0f172a;        /* Dark page background */
  --card-bg: #1e293b;          /* Dark card background */
  --card-border: hsla(0,0%,100%,.08);  /* Subtle light border */
}
```

**Pairing Rule**: ALWAYS include dark variants:
```html
<div class="bg-white dark:bg-slate-800 text-slate-900 dark:text-white border-slate-200 dark:border-slate-700">
```

Never use pure black (`#000`). Darkest is `slate-900` (`#0f172a`). Shadows increase opacity in dark mode for visibility.

## Background Patterns

| Pattern | Class | Use Case |
|---------|-------|----------|
| Main gradient | `bg-main-gradient` | Default page background |
| Dashboard | `bg-dashboard` | Module dashboards (radial teal + gold) |
| Ledger lines | `bg-ledger` | GL/journal pages (horizontal rules at 40px) |
| Mesh | `bg-mesh` | Special pages (3 radial gradients) |
| Login | `login-bg` | Auth pages (glassmorphic card) |

## Accessibility Checklist

- [ ] All `<th>` have `scope="col"`
- [ ] All icon-only buttons have `aria-label`
- [ ] Sidebar `<nav>` has `aria-label="Main navigation"`
- [ ] Active links have `aria-current="page"`
- [ ] Breadcrumbs: `<nav aria-label="Breadcrumb">` with `<ol>`
- [ ] Modals: `role="dialog"` + `aria-modal="true"` + `aria-labelledby` + escape handler
- [ ] Toast container: `aria-live="polite"`
- [ ] Color is NEVER the sole indicator — always pair with text/icon
- [ ] Minimum touch target: 44x44px
- [ ] Decorative icons: `aria-hidden="true"`
- [ ] Focus visible: 2px teal outline on `:focus-visible`

## Data Display Rules

| Data Type | Format | CSS Class |
|-----------|--------|-----------|
| Date (table) | `DD MMM YYYY` (e.g., "07 Feb 2026") | `text-sm` |
| Date (form input) | `YYYY-MM-DD` | `form-input` |
| Currency | Symbol + amount, right-aligned | `text-right font-mono tabular-nums` |
| Negative amount | Parentheses + rose | `text-rose-600 dark:text-rose-400 font-mono` → `(1,234.56)` |
| Enum status | `{{ status \| replace('_', ' ') \| title }}` | via `status_badge()` macro |
| None/null | `{{ var if var else '' }}` or `—` | Never render "None" |
| Invoice/ID numbers | Monospace | `font-mono tabular-nums` |
| Percentages | Right-aligned, 1 decimal | `text-right font-mono` |

## Icon System

Built-in icons via `{{ icon_svg(name, size) }}` macro:
`trending-up`, `trending-down`, `document`, `chart`, `users`, `currency`, `receipt`,
`credit-card`, `folder`, `clock`, `check-circle`, `warning`, `box`, `download`,
`trash`, `edit`, `plus`, `search`, `filter`, `chevron-down`, `chevron-right`,
`external-link`, `mail`, `phone`, `building`, `calendar`, `tag`.

All icons are inline SVG (no external requests). Decorative icons get `aria-hidden="true"`.
