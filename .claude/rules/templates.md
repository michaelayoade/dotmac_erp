# Jinja2 + Alpine.js Template Patterns

## Alpine.js Attribute Quoting (CRITICAL)
Always use SINGLE quotes for `x-data` attributes. Double quotes cause the `tojson` filter to break Alpine parsing:

```html
<!-- CORRECT -->
<div x-data='{ notifications: {{ notifications | tojson }} }'>

<!-- WRONG — breaks Alpine -->
<div x-data="{ notifications: {{ notifications | tojson }} }">
```

## Enum Display
Python `str, enum.Enum` renders as raw uppercase in Jinja2. Always apply filters:
```html
{{ status | replace('_', ' ') | title }}
```

## None Handling
`default('')` only works for UNDEFINED variables, not Python None:
```html
<!-- CORRECT for potentially None values -->
{{ var if var else '' }}

<!-- WRONG — None still renders as "None" -->
{{ var | default('') }}
```

## Dynamic Tailwind Classes
String interpolation like `bg-{{ color }}-50` gets purged by Tailwind. Use dict lookup:
```html
{% set color_map = {'success': 'bg-green-50', 'warning': 'bg-yellow-50', 'error': 'bg-red-50'} %}
<div class="{{ color_map.get(status, 'bg-gray-50') }}">
```
Or add to `safelist` in `tailwind.config.js`.

## Status Badge Macro
Use `{% from "components/_badges.html" import status_badge %}` for consistent status display.
Supported statuses include: ACTIVE, INACTIVE, DRAFT, PENDING, APPROVED, REJECTED, OPEN, CLOSED, SUBMITTED, PROCESSING, RECEIVED, QUARANTINED, EXPIRED, DEPLETED, AVAILABLE, IN_PROGRESS.

## HTMX target wrapper (CRITICAL)
Any template whose HTMX attribute targets an element ID — `hx-target="#foo"` or `hx-select="#foo"` — must include that element somewhere on the page, or the swap silently does nothing. The `live_search` macro defaults to `#results-container`; pages that use it must wrap their table + pagination in `<div id="results-container">`. Pages using a custom target (e.g. `#run-content`, `#form-result`) must include that ID themselves.

Backend routes that return a partial in response to an HTMX request should use `app.services.htmx.is_htmx_request(request)` and `htmx_response(...)` rather than reading the `HX-Request` header directly.

## File Upload Component
Use the reusable upload macro:
```html
{% from "components/_file_upload.html" import file_upload_zone %}
{{ file_upload_zone(
    name="logo_file",
    label="Upload Logo",
    accept="image/*",
    max_size_mb=5,
    preview="image",
    current_url=org.logo_url,
    remove_name="remove_logo"
) }}
```

## Dark Mode
All templates support dark mode via Tailwind's `dark:` prefix. Always provide dark variants for text, backgrounds, and borders:
```html
<div class="bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100">
```
