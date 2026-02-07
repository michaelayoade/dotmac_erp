# UI/UX Design Standards

## Typography
- **Page titles (h1)**: `text-xl font-semibold font-display` (Fraunces) — via `topbar` macro
- **Section titles (h2/h3)**: `text-lg font-semibold` or `text-xl font-semibold` (DM Sans)
- **Body text**: `text-sm` (14px) — tables, forms, descriptions
- **Captions/labels**: `text-xs font-medium` (12px) — timestamps, secondary labels
- **Financial values**: ALWAYS `font-mono tabular-nums`
- **Minimum text size**: `text-xs` (12px) — NEVER use `text-[10px]` or `text-[11px]`

## Spacing
- `gap-4` between cards, `gap-6` between sections, `gap-8` between zones
- `p-4` card padding mobile, `p-6` desktop, `p-5` stat cards

## Color Standards
| Status | Color | Text | BG |
|--------|-------|------|----|
| Success/Paid/Active | Emerald | `text-emerald-700 dark:text-emerald-400` | `bg-emerald-50 dark:bg-emerald-900/20` |
| Warning/Pending/Draft | Amber | `text-amber-700 dark:text-amber-400` | `bg-amber-50 dark:bg-amber-900/20` |
| Error/Overdue/Rejected | Rose | `text-rose-700 dark:text-rose-400` | `bg-rose-50 dark:bg-rose-900/20` |
| Info/Processing | Blue | `text-blue-700 dark:text-blue-400` | `bg-blue-50 dark:bg-blue-900/20` |
| Neutral/Closed/Voided | Slate | `text-slate-600 dark:text-slate-400` | `bg-slate-100 dark:bg-slate-800` |

Module accents: Finance=teal, People=violet, Expense=amber, Inventory=emerald, Procurement=blue, Operations=indigo.

## Components
- **Status badges**: `{{ status_badge(status, 'sm') }}` — NEVER inline badge HTML
- **Empty states**: `{{ empty_state(title, description, icon) }}` — every `{% for %}` needs `{% else %}`
- **Search**: `{{ live_search(...) }}` macro — NEVER inline search forms
- **Tables**: `<div class="table-container">`, `scope="col"` on `<th>`, amounts `text-right font-mono`
- **Forms**: Labels above fields, `{{ request.state.csrf_form | safe }}`, no `alert()` (use toast)
- **Modals**: `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, `@keydown.escape`
- **Toasts**: Bottom-right, stacked upward. 5s success, 8s warning, persistent error

## Search Macro Usage
```jinja2
{# Simple #}
{{ live_search(search=search, base_url="/module/items", placeholder="Search...") }}

{# With filters #}
{{ live_search(search=search, filters=[{"name": "status", ...}], base_url="/module/items") }}

{# With dynamic filters #}
{% call(search_attrs) live_search(search=search, base_url="/module/items") %}
    <select name="category_id" class="form-select" {{ search_attrs }}>...</select>
{% endcall %}
```
- Results MUST be in `<div id="results-container">`
- Macro creates its own card — never double-wrap

## Dark Mode
- ALWAYS pair: `bg-white dark:bg-slate-800`, `text-slate-900 dark:text-white`
- Never pure black — darkest is `slate-900`

## Accessibility (WCAG 2.2 AA)
- Icon-only buttons: `aria-label="Delete invoice"`
- Sidebar `<nav>`: `aria-label="Main navigation"`, active links: `aria-current="page"`
- Breadcrumbs: `<nav aria-label="Breadcrumb">` with `<ol>`, final item `aria-current="page"`
- Color NEVER sole indicator — pair with text/icon
- Touch targets: minimum 44x44px on mobile

## Data Display
- **Dates**: `DD MMM YYYY` in tables, `YYYY-MM-DD` in form inputs
- **Currency**: `font-mono tabular-nums`, right-aligned
- **Enums**: `{{ status | replace('_', ' ') | title }}`
- **None/null**: `{{ var if var else '' }}` — NEVER render "None"
- **Negatives**: Parentheses + rose: `<span class="text-rose-600">(1,234.56)</span>`

## Transitions
- Color/opacity: `transition-colors duration-150`
- Layout: `transition-all duration-200 ease-out`
- Modal enter: `duration-200 ease-out`, leave: `duration-150 ease-in`
- NEVER exceed 400ms
